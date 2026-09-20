"""Turns a stored file into searchable vectors and keeps the database in step.

Stages reported on the ingest job (polled by the UI): queued -> parsing -> embedding -> ready | failed.
Each database update uses its own short session, so a long parse or embedding call never blocks
other requests on the shared in-memory SQLite connection.
"""

import asyncio
import hashlib
import time
from pathlib import Path

import httpx
import httpx2
from sqlalchemy import delete
from sqlmodel import select

from back_end.core.logging import get_logger
from back_end.core.timeutil import utcnow
from back_end.db import models as m
from back_end.services import ingestion as ing
from back_end.services.container import Services
from back_end.services.storage import IMAGE_KINDS
from back_end.services.vectorstore import ChunkRecord

log = get_logger(__name__)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


async def _job(svc: Services, job_id: str | None, **fields) -> None:
    if not job_id:
        return
    async with svc.db.session() as s:
        job = await s.get(m.IngestJob, job_id)
        if job:
            for k, v in fields.items():
                setattr(job, k, v)
            s.add(job)


def _friendly(exc: BaseException, svc: Services) -> str:
    if isinstance(exc, (httpx.ConnectError, httpx2.ConnectError, ConnectionError)) or "Connection refused" in str(exc):
        return f"Ollama is not reachable at {svc.settings.ollama_base_url}. Start Ollama and re-index."
    msg = str(exc) or type(exc).__name__
    return msg[:300]


def _parse_cache(svc: Services) -> Path:
    return svc.settings.parse_cache_dir or (svc.settings.storage_dir / ".cache" / "parsed")


async def index_document(
    svc: Services,
    doc_id: str,
    *,
    job_id: str | None = None,
    keep_body: bool = False,
) -> int:
    """Parse + chunk + embed the current version of a document. Returns the number of chunks."""
    started = time.perf_counter()
    async with svc.db.session() as s:
        doc = await s.get(m.Document, doc_id)
        if doc is None or doc.asset_id is None:
            return 0
        asset = await s.get(m.FileAsset, doc.asset_id)
        path = svc.files.path_of(asset)
        title, kind, version = doc.title, doc.file_kind, doc.version
        meta = {
            "doc_id": doc.id,
            "version": doc.version,
            "title": doc.title,
            "category": doc.category,
            "station": doc.station_id,
            "source": doc.source,
            "file_kind": doc.file_kind,
        }
    try:
        await _job(svc, job_id, stage="parsing", progress=15)
        parsed = await asyncio.to_thread(ing.parse_cached, path, kind, title, _parse_cache(svc))
        chunks = ing.chunk(parsed.sections, title, chunk_tokens=svc.settings.chunk_tokens, overlap=svc.settings.chunk_overlap)
        await _job(svc, job_id, stage="embedding", progress=55)
        records = [
            ChunkRecord(
                id=f"{doc_id}:v{version}:{c.index}",
                text=c.text,
                metadata={**meta, "chunk_index": c.index, "heading": c.heading, "tokens": c.tokens, "sha": _sha(c.text)},
            )
            for c in chunks
        ]
        embedded = await svc.vectors.upsert_chunks(records)
        if kind in IMAGE_KINDS:
            await index_photo(
                svc,
                path,
                photo_id=f"{doc_id}:v{version}:photo",
                caption=parsed.markdown,
                metadata={**meta, "owner_type": "document", "owner_id": doc_id},
            )
        removed = await asyncio.to_thread(svc.vectors.delete_document, doc_id, keep_version=version)
        async with svc.db.session() as s:
            await s.execute(delete(m.DocumentChunk).where(m.DocumentChunk.document_id == doc_id))
            for r, c in zip(records, chunks, strict=True):
                s.add(
                    m.DocumentChunk(
                        id=r.id,
                        document_id=doc_id,
                        version=version,
                        index=c.index,
                        tokens=c.tokens,
                        heading=c.heading,
                        text=c.text,
                        sha=r.metadata["sha"],
                        collection=svc.vectors.docs_name,
                    )
                )
            doc = await s.get(m.Document, doc_id)
            doc.chunk_count = len(chunks)
            doc.status = "ready"
            if not keep_body:
                doc.body = parsed.markdown
            if not doc.summary:
                doc.summary = ing.summary_from(parsed.markdown)
            s.add(doc)
        await _job(
            svc, job_id, stage="ready", progress=100, chunks=len(chunks), finished_at=utcnow(), error="; ".join(parsed.warnings) or None
        )
        log.info(
            "document_indexed",
            doc_id=doc_id,
            version=version,
            chunks=len(chunks),
            embedded=embedded,
            removed_old=removed,
            ms=int((time.perf_counter() - started) * 1000),
        )
        return len(chunks)
    except Exception as exc:  # noqa: BLE001 - reported on the job; the request already returned
        message = _friendly(exc, svc)
        log.warning("document_index_failed", doc_id=doc_id, error=message)
        await _job(svc, job_id, stage="failed", progress=100, error=message, finished_at=utcnow())
        async with svc.db.session() as s:
            doc = await s.get(m.Document, doc_id)
            if doc and doc.source == "upload" and doc.chunk_count == 0:
                doc.status = "failed"
                s.add(doc)
        return 0


async def index_photo(svc: Services, path: Path, *, photo_id: str, caption: str, metadata: dict) -> bool:
    if not svc.vision.enabled:
        return False
    sha = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    have = await asyncio.to_thread(svc.vectors.existing_shas, [photo_id], photos=True)
    if have.get(photo_id) == sha:
        return True  # same file already embedded (Chroma persists across restarts)
    try:
        vec = (await svc.vision.aembed_images([path]))[0]
    except Exception as exc:  # noqa: BLE001
        log.warning("photo_embed_failed", photo=photo_id, error=str(exc))
        return False
    await svc.vectors.upsert_photo(photo_id, vec, caption[:2000], {**metadata, "sha": sha})
    return True


async def index_ticket_photo(svc: Services, attachment_id: str) -> bool:
    async with svc.db.session() as s:
        att = await s.get(m.Attachment, attachment_id)
        if att is None or att.kind not in IMAGE_KINDS or att.asset_id is None:
            return False
        ticket = await s.get(m.Ticket, att.ticket_id)
        asset = await s.get(m.FileAsset, att.asset_id)
        path = svc.files.path_of(asset)
        meta = {
            "owner_type": "ticket",
            "owner_id": att.ticket_id,
            "attachment_id": att.id,
            "name": att.name,
            "station": ticket.station_id if ticket else "",
            "category": "ticket",
        }
        caption = f"Photo attached to {att.ticket_id}: {ticket.title if ticket else ''} ({att.name})"
    return await index_photo(svc, path, photo_id=f"ATT:{attachment_id}", caption=caption, metadata=meta)


async def reindex_all(svc: Services, *, collection: str | None = None, force: bool = False) -> None:
    async with svc.db.session() as s:
        docs = list((await s.exec(select(m.Document).where(m.Document.status != "archived"))).all())
        photos = list((await s.exec(select(m.Attachment))).all())
    if force:
        await asyncio.to_thread(svc.vectors.reset)
    total = max(1, len(docs) + len(photos))
    svc.reindex = {"collection": collection or svc.vectors.docs_name, "progress": 0}
    try:
        for i, d in enumerate(docs, 1):
            await index_document(svc, d.id, keep_body=d.source == "seed")
            svc.reindex["progress"] = int(i / total * 100)
        for j, a in enumerate(photos, len(docs) + 1):
            await index_ticket_photo(svc, a.id)
            svc.reindex["progress"] = int(j / total * 100)
    finally:
        svc.reindex = None


def reconcile(svc: Services, live: dict[str, int], attachment_ids: set[str]) -> int:
    """Delete vectors whose document no longer exists or whose version is outdated."""
    stats = svc.vectors.stats()
    removed = 0
    for name, info in stats.items():
        photos = name == svc.vectors.photos_name
        stale_ids = []
        for vid, meta in zip(info["ids"], info["metadatas"], strict=False):
            meta = meta or {}
            if meta.get("owner_type") == "ticket":
                if meta.get("attachment_id") not in attachment_ids:
                    stale_ids.append(vid)
                continue
            doc_id = meta.get("doc_id")
            if doc_id not in live or int(meta.get("version", 0)) != live[doc_id]:
                stale_ids.append(vid)
        svc.vectors.remove_ids(stale_ids, photos=photos)
        removed += len(stale_ids)
    return removed


def orphan_count(svc: Services, live: dict[str, int], attachment_ids: set[str]) -> dict[str, int]:
    out = {}
    for name, info in svc.vectors.stats().items():
        n = 0
        for meta in info["metadatas"]:
            meta = meta or {}
            if meta.get("owner_type") == "ticket":
                n += meta.get("attachment_id") not in attachment_ids
            elif meta.get("doc_id") not in live or int(meta.get("version", 0)) != live[meta.get("doc_id")]:
                n += 1
        out[name] = n
    return out
