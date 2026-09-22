"""Knowledge-base documents (upload and ingestion, review dates, retrieval sources).

Uploads are multipart: the file is stored under storage/documents/<docId>/v<version>/, recorded in the
file_assets table, and indexed in the background (parse -> chunk -> embed). The response returns at once
with an ingest job that the UI polls.
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, File, Form, Query, UploadFile, status
from sqlmodel import col, select

from back_end.api.deps import ActorDep, SvcDep
from back_end.auth.permissions import Actor, can, deny_reason
from back_end.core.errors import BadRequest, Forbidden, NotFound
from back_end.core.timeutil import utcnow
from back_end.db import models as m
from back_end.schemas import (
    BulkReviewIn,
    BulkReviewOut,
    ChunkOut,
    DocCategory,
    DocListItem,
    DocListOut,
    DocumentDetailOut,
    DocUploadOut,
    IngestJobOut,
    ReviewIn,
    StationId,
)
from back_end.services import analytics as an
from back_end.services import indexing, repo
from back_end.services.container import Services
from back_end.services.storage import DOCUMENT_KINDS

router = APIRouter(prefix="/api", tags=["documents"])

CATEGORY_CODE = {"recipe": "REC", "sop": "SOP", "incident": "INC", "onboarding": "ONB"}


def _tags(raw: str | None) -> list[str]:
    return [t.strip().lower() for t in (raw or "").split(",") if t.strip()][:12]


async def _doc(s, actor: Actor, doc_id: str) -> m.Document:
    d = await s.get(m.Document, doc_id)
    if d is None:
        raise NotFound("Document")
    if d.category == "incident" and not can.see_incident_reports(actor):
        raise Forbidden("Incident Reports are restricted for Line Cooks.")
    return d


async def _list_item(s, d: m.Document) -> DocListItem:
    reviews = (await repo.reviews_by_doc(s)).get(d.id, [])
    tickets = (await s.exec(select(m.Ticket).where(m.Ticket.related_doc_id == d.id))).all()
    return repo.doc_list_item(d, sorted(reviews, key=lambda r: r.at, reverse=True), tickets)


async def _start_job(svc: Services, s, d: m.Document, actor: Actor) -> m.IngestJob:
    job = m.IngestJob(
        id=await repo.next_id(s, "ingest_job", "JOB-", 4),
        doc_id=d.id,
        file_name=d.file_name,
        collection=svc.vectors.docs_name,
        stage="queued",
        progress=5,
        requested_by=actor.user_id,
    )
    s.add(job)
    return job


@router.get("/documents", response_model=DocListOut, summary="Documents visible to the caller")
async def list_documents(
    actor: ActorDep,
    svc: SvcDep,
    q: str = "",
    category: DocCategory | None = None,
    station: StationId | None = None,
    source: str | None = None,
    stale: bool = False,
    include_archived: Annotated[bool, Query(alias="includeArchived")] = False,
) -> DocListOut:
    async with svc.db.session() as s:
        docs = await repo.all_rows(s, m.Document)
        reviews = await repo.reviews_by_doc(s)
        tickets = await repo.all_rows(s, m.Ticket)
    hidden = 0
    items: list[DocListItem] = []
    needle = q.strip().lower()
    for d in sorted(docs, key=lambda x: x.created_at, reverse=True):
        if d.category == "incident" and not can.see_incident_reports(actor):
            hidden += 1
            continue
        if d.status == "archived" and not include_archived:
            continue
        if (category and d.category != category) or (station and d.station_id != station):
            continue
        if (source and d.source != source) or (stale and not an.is_stale(d)):
            continue
        if needle and needle not in f"{d.id} {d.title} {' '.join(d.tags or [])} {d.summary}".lower():
            continue
        items.append(repo.doc_list_item(d, sorted(reviews.get(d.id, []), key=lambda r: r.at, reverse=True), tickets))
    return DocListOut(items=items, hidden_incidents=hidden)


@router.get("/documents/{doc_id}", response_model=DocumentDetailOut, summary="Document with chunks and tickets")
async def get_document(doc_id: str, actor: ActorDep, svc: SvcDep) -> DocumentDetailOut:
    async with svc.db.session() as s:
        d = await _doc(s, actor, doc_id)
        reviews = sorted((await repo.reviews_by_doc(s)).get(d.id, []), key=lambda r: r.at, reverse=True)
        cites = (await repo.citations_7d(s)).get(d.id, 0)
        chunks = (
            await s.exec(select(m.DocumentChunk).where(m.DocumentChunk.document_id == d.id).order_by(col(m.DocumentChunk.index)))
        ).all()
        related = (await s.exec(select(m.Ticket).where(m.Ticket.related_doc_id == d.id))).all()
        ctx = await repo.ticket_context(s)
        jobs = (await s.exec(select(m.IngestJob).where(m.IngestJob.doc_id == d.id).order_by(col(m.IngestJob.started_at).desc()))).all()
        who = await repo.names(s)
    return DocumentDetailOut(
        document=repo.doc_full(d, reviews, cites),
        chunks=[ChunkOut(index=c.index, tokens=c.tokens, heading=c.heading, preview=c.text[:280], sha=c.sha) for c in chunks],
        related_tickets=[ctx.item(t) for t in related],
        jobs=[repo.job_out(j, who) for j in jobs],
        embedding_model=f"{svc.settings.embedding_model} (v1.5)",
    )


@router.post(
    "/documents", response_model=DocUploadOut, status_code=status.HTTP_201_CREATED, summary="Upload a document into the knowledge base"
)
async def upload_document(
    actor: ActorDep,
    svc: SvcDep,
    file: Annotated[UploadFile, File(description="PDF, Word, PowerPoint, Excel, Markdown, text or image")],
    title: Annotated[str, Form(min_length=3, max_length=160)],
    category: Annotated[DocCategory, Form()],
    station: Annotated[StationId, Form()],
    summary: Annotated[str, Form(max_length=600)] = "",
    tags: Annotated[str, Form()] = "",
    owner_id: Annotated[str | None, Form(alias="ownerId")] = None,
) -> DocUploadOut:
    if not can.upload_category(actor, category, station):
        raise Forbidden(deny_reason("upload", actor))
    data = await file.read()
    name = file.filename or "upload"
    async with svc.db.session() as s:
        n = await repo.next_id(s, f"doc_{CATEGORY_CODE[category].lower()}", "", 3)
        doc_id = f"DOC-{CATEGORY_CODE[category]}-{n}"
        stored = svc.files.save(
            data=data,
            original_name=name,
            purpose="document",
            owner_type="document",
            owner_id=doc_id,
            subdir=f"documents/{doc_id}/v1",
            uploaded_by=actor.user_id,
            allowed=DOCUMENT_KINDS,
        )
        s.add(stored.asset)
        await s.flush()
        owner = owner_id or actor.crew_member_id or (await repo.lookup(s)).station_lead(station) or actor.user_id
        now = utcnow()
        d = m.Document(
            id=doc_id,
            title=title.strip(),
            category=category,
            station_id=station,
            owner_id=owner,
            created_at=now,
            last_reviewed=now,
            source="upload",
            uploaded_by=actor.user_id,
            file_name=stored.asset.original_name,
            file_kind=stored.kind,
            file_bytes=stored.asset.bytes,
            summary=summary.strip(),
            body="",
            tags=_tags(tags),
            status="processing",
            version=1,
            asset_id=stored.asset.id,
        )
        s.add(d)
        await s.flush()
        s.add(m.ReviewEvent(document_id=doc_id, at=now, by=actor.who, note="Initial version uploaded"))
        job = await _start_job(svc, s, d, actor)
        await repo.log_activity(s, actor.who, "uploaded", doc_id, "document", d.title)
        await s.flush()
        out = DocUploadOut(document=await _list_item(s, d), job=repo.job_out(job, await repo.names(s)))
    svc.spawn(indexing.index_document(svc, doc_id, job_id=job.id), name=f"index:{doc_id}")
    return out


@router.post("/documents/review-bulk", response_model=BulkReviewOut, summary="Mark several documents reviewed")
async def review_bulk(body: BulkReviewIn, actor: ActorDep, svc: SvcDep) -> BulkReviewOut:
    updated: list[str] = []
    async with svc.db.session() as s:
        now = utcnow()
        for doc_id in body.ids:
            d = await s.get(m.Document, doc_id)
            if d and d.status != "archived" and can.mark_reviewed(actor, d.station_id):
                d.last_reviewed = now
                s.add(d)
                s.add(m.ReviewEvent(document_id=d.id, at=now, by=actor.who, note="Reviewed via stale audit (bulk)"))
                await repo.log_activity(s, actor.who, "reviewed", d.id, "document", d.title)
                updated.append(doc_id)
    if body.ids and not updated:
        raise Forbidden(deny_reason("review", actor))
    return BulkReviewOut(updated=updated, skipped=[i for i in body.ids if i not in updated])


@router.post("/documents/{doc_id}/replace", response_model=DocUploadOut, summary="Upload a new version of a file")
async def replace_document(
    doc_id: str,
    actor: ActorDep,
    svc: SvcDep,
    file: Annotated[UploadFile, File()],
    note: Annotated[str, Form(max_length=300)] = "",
) -> DocUploadOut:
    data = await file.read()
    async with svc.db.session() as s:
        d = await _doc(s, actor, doc_id)
        if not can.upload_category(actor, d.category, d.station_id):
            raise Forbidden(deny_reason("upload", actor))
        if d.status == "archived":
            raise BadRequest("Archived documents cannot be replaced.")
        version = d.version + 1
        stored = svc.files.save(
            data=data,
            original_name=file.filename or d.file_name,
            purpose="document",
            owner_type="document",
            owner_id=doc_id,
            subdir=f"documents/{doc_id}/v{version}",
            uploaded_by=actor.user_id,
            allowed=DOCUMENT_KINDS,
        )
        s.add(stored.asset)
        await s.flush()
        now = utcnow()
        d.version, d.asset_id, d.status, d.last_reviewed = version, stored.asset.id, "processing", now
        d.file_name, d.file_kind, d.file_bytes = stored.asset.original_name, stored.kind, stored.asset.bytes
        if d.source == "upload":
            d.body = ""
        s.add(d)
        s.add(
            m.ReviewEvent(
                document_id=d.id,
                at=now,
                by=actor.who,
                note=note.strip() or f"Replaced file (v{version}); old vectors removed after re-index",
            )
        )
        job = await _start_job(svc, s, d, actor)
        await repo.log_activity(s, actor.who, "replaced the file of", d.id, "document", d.title)
        await s.flush()
        out = DocUploadOut(document=await _list_item(s, d), job=repo.job_out(job, await repo.names(s)))
    svc.spawn(indexing.index_document(svc, doc_id, job_id=job.id, keep_body=False), name=f"index:{doc_id}")
    return out


@router.post("/documents/{doc_id}/review", response_model=DocListItem, summary="Mark a document reviewed")
async def review_document(doc_id: str, body: ReviewIn, actor: ActorDep, svc: SvcDep) -> DocListItem:
    async with svc.db.session() as s:
        d = await _doc(s, actor, doc_id)
        if not can.mark_reviewed(actor, d.station_id):
            raise Forbidden(deny_reason("review", actor))
        now = utcnow()
        d.last_reviewed = now
        s.add(d)
        s.add(m.ReviewEvent(document_id=d.id, at=now, by=actor.who, note=body.note.strip() or "Reviewed, no changes needed"))
        await repo.log_activity(s, actor.who, "reviewed", d.id, "document", d.title)
        await s.flush()
        return await _list_item(s, d)


@router.post("/documents/{doc_id}/archive", response_model=DocListItem, summary="Archive (exclude from retrieval)")
async def archive_document(doc_id: str, actor: ActorDep, svc: SvcDep) -> DocListItem:
    async with svc.db.session() as s:
        d = await _doc(s, actor, doc_id)
        if not can.archive_document(actor):
            raise Forbidden(deny_reason("archive", actor))
        d.status = "archived"
        s.add(d)
        s.add(m.ReviewEvent(document_id=d.id, at=utcnow(), by=actor.who, note="Archived; excluded from retrieval"))
        await repo.log_activity(s, actor.who, "archived", d.id, "document", d.title)
        await s.flush()
        out = await _list_item(s, d)
    await asyncio.to_thread(svc.vectors.delete_document, doc_id)
    return out


@router.get("/ingest/jobs", response_model=list[IngestJobOut], summary="Ingestion jobs (newest first)")
async def ingest_jobs(
    _: ActorDep, svc: SvcDep, doc_id: Annotated[str | None, Query(alias="docId")] = None, limit: int = 50
) -> list[IngestJobOut]:
    async with svc.db.session() as s:
        stmt = select(m.IngestJob).order_by(col(m.IngestJob.started_at).desc()).limit(min(limit, 200))
        if doc_id:
            stmt = stmt.where(m.IngestJob.doc_id == doc_id)
        jobs = (await s.exec(stmt)).all()
        who = await repo.names(s)
    return [repo.job_out(j, who) for j in jobs]


@router.get("/ingest/jobs/{job_id}", response_model=IngestJobOut, summary="One ingestion job")
async def ingest_job(job_id: str, _: ActorDep, svc: SvcDep) -> IngestJobOut:
    async with svc.db.session() as s:
        j = await s.get(m.IngestJob, job_id)
        if j is None:
            raise NotFound("Ingest job")
        return repo.job_out(j, await repo.names(s))
