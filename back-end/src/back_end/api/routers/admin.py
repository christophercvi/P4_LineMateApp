"""Admin: user accounts and the vector store (collections, re-index, reconcile, service health)."""

import asyncio
import time

import httpx2
from fastapi import APIRouter, status
from sqlalchemy import text
from sqlmodel import col, select

from back_end.api.deps import ActorDep, SvcDep
from back_end.api.routers.auth import user_out
from back_end.auth.permissions import Actor, can
from back_end.core.errors import BadRequest, Conflict, Forbidden, NotFound
from back_end.db import models as m
from back_end.schemas import (
    AdminUserOut,
    AdminUserPatch,
    CollectionOut,
    ReindexIn,
    ReindexStatus,
    ServiceHealthOut,
    VectorStoreOut,
)
from back_end.services import indexing, repo
from back_end.services.container import Services
from back_end.services.mcp_clients import SERVERS

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _admin(actor: Actor) -> None:
    if not can.admin(actor):
        raise Forbidden("This page is for Admins.")


async def _users(svc: Services) -> list[AdminUserOut]:
    async with svc.db.session() as s:
        users = (await s.exec(select(m.User).where(m.User.role != "service").order_by(col(m.User.display_name)))).all()
        crew = {c.id: c for c in await repo.all_rows(s, m.CrewMember)}
    out = []
    for u in users:
        base = user_out(u).model_dump()
        c = crew.get(u.crew_member_id or "")
        out.append(AdminUserOut(**base, crew_title=c.title if c else None))
    return out


@router.get("/users", response_model=list[AdminUserOut], summary="All user accounts")
async def list_users(actor: ActorDep, svc: SvcDep) -> list[AdminUserOut]:
    _admin(actor)
    return await _users(svc)


@router.patch("/users/{user_id}", response_model=AdminUserOut, summary="Change a user's role, station or status")
async def patch_user(user_id: str, body: AdminUserPatch, actor: ActorDep, svc: SvcDep) -> AdminUserOut:
    _admin(actor)
    async with svc.db.session() as s:
        u = await s.get(m.User, user_id)
        if u is None or u.role == "service":
            raise NotFound(f"User {user_id}")
        if u.id == actor.user_id and (body.active is False or (body.role and body.role != "admin")):
            raise Conflict("You cannot disable or demote your own account.")
        role = body.role or u.role
        station = u.station_id if body.station is None else (body.station or None)
        if role in ("line_cook", "sous_chef") and not station:
            raise BadRequest("Line Cooks and Sous Chefs need a station.")
        if role in ("kitchen_manager", "admin"):
            station = None
        changes = []
        if role != u.role:
            changes.append(f"role {u.role} -> {role}")
        if station != u.station_id:
            changes.append(f"station {u.station_id or '-'} -> {station or '-'}")
        if body.active is not None and body.active != u.active:
            changes.append("enabled" if body.active else "disabled")
        u.role, u.station_id = role, station
        if body.active is not None:
            u.active = body.active
        if u.crew_member_id and (c := await s.get(m.CrewMember, u.crew_member_id)):
            c.station_id = station
            s.add(c)
        s.add(u)
        if changes:
            await repo.log_activity(s, actor.who, "updated user", u.id, "user", ", ".join(changes))
    return next(x for x in await _users(svc) if x.id == user_id)


# ------------------------------------------------------------------------------------ vector store


async def _live(svc: Services) -> tuple[dict[str, int], set[str], dict[str, int]]:
    async with svc.db.session() as s:
        docs = (await s.exec(select(m.Document).where(m.Document.status != "archived"))).all()
        atts = (await s.exec(select(m.Attachment))).all()
    live = {d.id: d.version for d in docs}
    photo_docs = {d.id: d.version for d in docs if d.file_kind in ("jpg", "png")}
    return live, {a.id for a in atts}, photo_docs


async def _health(svc: Services) -> list[ServiceHealthOut]:
    out = [ServiceHealthOut(name="FastAPI", status="up", detail="REST, SSE and MCP endpoints", version=svc.settings.app_name)]
    started = time.perf_counter()
    try:
        async with svc.db.session() as s:
            ver = (await s.execute(text("select sqlite_version()"))).scalar_one()
        out.append(
            ServiceHealthOut(
                name="SQLite (in-memory)", status="up", detail=f"{int((time.perf_counter() - started) * 1000)} ms", version=str(ver)
            )
        )
    except Exception as exc:  # noqa: BLE001
        out.append(ServiceHealthOut(name="SQLite (in-memory)", status="down", detail=str(exc), version=""))
    try:
        import chromadb

        await asyncio.to_thread(svc.vectors.client.heartbeat)
        out.append(ServiceHealthOut(name="Chroma", status="up", detail=str(svc.settings.chroma_dir.name), version=chromadb.__version__))
    except Exception as exc:  # noqa: BLE001
        out.append(ServiceHealthOut(name="Chroma", status="down", detail=str(exc), version=""))
    try:
        version = await svc.ollama.version()
        loaded = [r["name"] for r in await svc.ollama.ps()]
        out.append(
            ServiceHealthOut(
                name="Ollama",
                status="up" if version else "down",
                detail=("loaded: " + ", ".join(loaded)) if loaded else "no model loaded",
                version=version or "",
            )
        )
    except (httpx2.HTTPError, OSError) as exc:
        out.append(ServiceHealthOut(name="Ollama", status="down", detail=f"{svc.settings.ollama_base_url}: {exc}", version=""))
    v = svc.vision
    out.append(
        ServiceHealthOut(
            name="nomic-embed-vision-v1.5",
            status="up" if v.enabled and v.loaded else ("degraded" if v.enabled else "down"),
            detail="in-process (Transformers)" + ("" if v.loaded else "; loads on first photo") if v.enabled else "disabled",
            version="v1.5",
        )
    )
    for sv in SERVERS.values():
        st = {"connected": "up", "degraded": "degraded"}.get(sv.status, "down")
        out.append(ServiceHealthOut(name=f"MCP: {sv.name}", status=st, detail=f"stdio, {sv.latency_ms} ms", version=""))
    return out


@router.get("/vector-store", response_model=VectorStoreOut, summary="Collections, ingest jobs and service health")
async def vector_store(actor: ActorDep, svc: SvcDep) -> VectorStoreOut:
    _admin(actor)
    live, attachment_ids, _ = await _live(svc)
    stats = await asyncio.to_thread(svc.vectors.stats)
    orphans = await asyncio.to_thread(indexing.orphan_count, svc, live, attachment_ids)
    collections = []
    for name, info in stats.items():
        photos = name == svc.vectors.photos_name
        collections.append(
            CollectionOut(
                name=name,
                embedding_model="nomic-embed-vision-v1.5" if photos else svc.settings.embedding_model,
                runtime="in-process" if photos else "ollama",
                dimensions=await asyncio.to_thread(svc.vectors.dimensions, photos),
                vectors=info["vectors"],
                documents=len({x for x in info["doc_ids"] if x}),
                distance="cosine",
                last_indexed=svc.vectors.last_indexed.get(name),
                orphaned=orphans.get(name, 0),
            )
        )
    async with svc.db.session() as s:
        jobs = (await s.exec(select(m.IngestJob).order_by(col(m.IngestJob.started_at).desc()).limit(30))).all()
        who = await repo.names(s)
    return VectorStoreOut(
        collections=collections,
        jobs=[repo.job_out(j, who) for j in jobs],
        services=await _health(svc),
        reindex=ReindexStatus(**svc.reindex) if svc.reindex else None,
    )


@router.post(
    "/vector-store/reindex",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ReindexStatus,
    summary="Re-embed every live document and photo (runs in the background)",
)
async def reindex(body: ReindexIn, actor: ActorDep, svc: SvcDep) -> ReindexStatus:
    _admin(actor)
    if svc.reindex:
        raise Conflict("A re-index is already running.")
    svc.reindex = {"collection": body.collection or svc.vectors.docs_name, "progress": 0}
    svc.spawn(indexing.reindex_all(svc, collection=body.collection), name="reindex")
    return ReindexStatus(**svc.reindex)


@router.post("/vector-store/reconcile", summary="Delete vectors for archived, replaced or deleted items")
async def reconcile(actor: ActorDep, svc: SvcDep) -> dict[str, int]:
    _admin(actor)
    live, attachment_ids, _ = await _live(svc)
    removed = await asyncio.to_thread(indexing.reconcile, svc, live, attachment_ids)
    async with svc.db.session() as s:
        await repo.log_activity(
            s, actor.who, "reconciled vector store", svc.vectors.docs_name, "agent", f"{removed} orphaned vectors removed"
        )
    return {"removed": removed}
