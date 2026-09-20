"""Chroma persistence (back-end/chroma_db) for document chunks and photos.

Two collections, both cosine:
  documents__nomic-embed-text-v1.5   text chunks of every document (seed + uploaded)
  photos__nomic-embed-vision-v1.5    one vector per photo (document images and ticket photos)

Writes go through langchain-chroma; reads use the raw collection so the API can return real
similarity scores for every strategy (similarity, MMR, score threshold).
"""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import chromadb
import numpy as np
from chromadb.config import Settings as ChromaSettings
from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores.utils import maximal_marginal_relevance

from back_end.core.logging import get_logger

log = get_logger(__name__)

Strategy = Literal["similarity", "mmr", "threshold"]


@dataclass(slots=True)
class ChunkRecord:
    id: str
    text: str
    metadata: dict[str, Any]


@dataclass(slots=True)
class Hit:
    id: str
    text: str
    metadata: dict[str, Any]
    score: float


class VectorStore:
    def __init__(
        self,
        persist_dir: Path,
        embeddings: Embeddings,
        *,
        docs_collection: str,
        photos_collection: str,
        embedding_model_name: str,
    ):
        persist_dir.mkdir(parents=True, exist_ok=True)
        self.persist_dir = persist_dir
        self.embeddings = embeddings
        self.embedding_model_name = embedding_model_name
        self.client = chromadb.PersistentClient(
            path=str(persist_dir), settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True)
        )
        self.docs_name = docs_collection
        self.photos_name = photos_collection
        self._docs = self._collection(docs_collection, {"embedding_model": embedding_model_name})
        self._photos = self._collection(photos_collection, {"embedding_model": "nomic-embed-vision-v1.5"})
        self.lc = Chroma(client=self.client, collection_name=docs_collection, embedding_function=embeddings)
        self.last_indexed: dict[str, datetime | None] = {docs_collection: None, photos_collection: None}

    def _collection(self, name: str, metadata: dict):
        return self.client.get_or_create_collection(
            name=name, configuration={"hnsw": {"space": "cosine"}}, metadata=metadata, embedding_function=None
        )

    # ------------------------------------------------------------------ writes

    def existing_shas(self, ids: Sequence[str], *, photos: bool = False) -> dict[str, str]:
        if not ids:
            return {}
        col = self._photos if photos else self._docs
        got = col.get(ids=list(ids), include=["metadatas"])
        return {i: (m or {}).get("sha", "") for i, m in zip(got["ids"], got["metadatas"] or [], strict=False)}

    async def upsert_chunks(self, records: Sequence[ChunkRecord]) -> int:
        """Embeds and stores only chunks whose content hash changed. Returns how many were embedded."""
        if not records:
            return 0
        have = await asyncio.to_thread(self.existing_shas, [r.id for r in records])
        todo = [r for r in records if have.get(r.id) != r.metadata.get("sha")]
        if todo:
            texts = [r.text for r in todo]
            vectors = await self.embeddings.aembed_documents(texts)
            await asyncio.to_thread(
                self._docs.upsert,
                ids=[r.id for r in todo],
                embeddings=vectors,
                documents=texts,
                metadatas=[_clean(r.metadata) for r in todo],
            )
            self.last_indexed[self.docs_name] = datetime.now(UTC)
        return len(todo)

    async def upsert_photo(self, photo_id: str, vector: list[float], caption: str, metadata: dict) -> None:
        await asyncio.to_thread(self._photos.upsert, ids=[photo_id], embeddings=[vector], documents=[caption], metadatas=[_clean(metadata)])
        self.last_indexed[self.photos_name] = datetime.now(UTC)

    def delete_document(self, doc_id: str, *, keep_version: int | None = None) -> int:
        where: dict[str, Any] = {"doc_id": doc_id}
        if keep_version is not None:
            where = {"$and": [{"doc_id": doc_id}, {"version": {"$ne": keep_version}}]}
        removed = 0
        for col in (self._docs, self._photos):
            ids = col.get(where=where, include=[])["ids"]
            if ids:
                col.delete(ids=ids)
                removed += len(ids)
        return removed

    def delete_photo_owner(self, owner_type: str, owner_id: str) -> None:
        ids = self._photos.get(where={"$and": [{"owner_type": owner_type}, {"owner_id": owner_id}]}, include=[])["ids"]
        if ids:
            self._photos.delete(ids=ids)

    # ------------------------------------------------------------------ reads

    def stats(self) -> dict[str, dict]:
        out = {}
        for name, col in ((self.docs_name, self._docs), (self.photos_name, self._photos)):
            got = col.get(include=["metadatas"])
            metas = got["metadatas"] or []
            out[name] = {
                "vectors": len(got["ids"]),
                "doc_ids": {m.get("doc_id") or m.get("owner_id") for m in metas if m},
                "metadatas": metas,
                "ids": got["ids"],
            }
        return out

    def dimensions(self, photos: bool = False) -> int:
        col = self._photos if photos else self._docs
        got = col.peek(limit=1)
        emb = got.get("embeddings")
        if emb is not None and len(emb):
            return len(emb[0])
        return 768

    def remove_ids(self, ids: Sequence[str], photos: bool = False) -> None:
        if ids:
            (self._photos if photos else self._docs).delete(ids=list(ids))

    async def search(
        self,
        query: str,
        *,
        k: int = 4,
        strategy: Strategy = "mmr",
        where: dict | None = None,
        threshold: float = 0.42,
        fetch_k: int = 16,
        lambda_mult: float = 0.6,
    ) -> list[Hit]:
        qvec = await self.embeddings.aembed_query(query)
        n = max(k, fetch_k if strategy == "mmr" else k * 3)
        res = await asyncio.to_thread(
            self._docs.query,
            query_embeddings=[qvec],
            n_results=n,
            where=where,
            include=["documents", "metadatas", "distances", "embeddings"],
        )
        ids = res["ids"][0]
        if not ids:
            return []
        docs = res["documents"][0]
        metas = res["metadatas"][0]
        sims = [1.0 - float(d) for d in res["distances"][0]]
        hits = [Hit(i, t, m or {}, s) for i, t, m, s in zip(ids, docs, metas, sims, strict=False)]
        if strategy == "mmr":
            embs = np.array(res["embeddings"][0])
            picked = maximal_marginal_relevance(np.array(qvec), embs, lambda_mult=lambda_mult, k=min(k, len(hits)))
            return [hits[i] for i in picked]
        if strategy == "threshold":
            return [h for h in hits if h.score >= threshold][:k]
        return hits[:k]

    async def search_photos(self, query: str, *, k: int = 3, where: dict | None = None) -> list[Hit]:
        """Text -> photo search (nomic text and vision models share one embedding space)."""
        if self._photos.count() == 0:
            return []
        return await self.search_photos_by_vector(await self.embeddings.aembed_query(query), k=k, where=where)

    async def search_photos_by_vector(self, qvec: list[float], *, k: int = 3, where: dict | None = None) -> list[Hit]:
        """Photo -> photo search (an uploaded picture against photos already on file)."""
        if self._photos.count() == 0 or len(qvec) != self.dimensions(photos=True):
            return []
        res = await asyncio.to_thread(
            self._photos.query,
            query_embeddings=[qvec],
            n_results=min(k, self._photos.count()),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        return [
            Hit(i, t or "", m or {}, 1.0 - float(d))
            for i, t, m, d in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0], strict=False)
        ]

    def reset(self) -> None:
        for name in (self.docs_name, self.photos_name):
            try:
                self.client.delete_collection(name)
            except Exception:  # noqa: BLE001
                pass
        self._docs = self._collection(self.docs_name, {"embedding_model": self.embedding_model_name})
        self._photos = self._collection(self.photos_name, {"embedding_model": "nomic-embed-vision-v1.5"})
        self.lc = Chroma(client=self.client, collection_name=self.docs_name, embedding_function=self.embeddings)


def role_filter(can_see_incidents: bool, extra: dict | None = None) -> dict | None:
    clauses = []
    if not can_see_incidents:
        clauses.append({"category": {"$ne": "incident"}})
    if extra:
        clauses.append(extra)
    if not clauses:
        return None
    return clauses[0] if len(clauses) == 1 else {"$and": clauses}


def _clean(meta: dict) -> dict:
    """Chroma metadata values must be str/int/float/bool."""
    out = {}
    for k, v in meta.items():
        if v is None:
            continue
        out[k] = v if isinstance(v, (str, int, float, bool)) else str(v)
    return out
