"""Populate the in-memory SQLite database and vectorize the knowledge base into Chroma.

Sources (all inside back-end/):
    seed_data/*.json                 stations, crew, users, documents, tickets, comments, attachments,
                                     approvals, citations, MCP tool usage, service tokens
    seed_data/document_bodies/*.md   the Markdown body shown on each document's detail page
    seed_documents/                  the original files (DOCX, PDF, PPTX, XLSX, MD, JPG) that are parsed,
                                     chunked and embedded
    seed_attachments/                photos and PDFs attached to seed tickets

Times in the JSON files are relative ("@ago:<hours>") so the stale-document audit, ticket ages and
charts always look current. Vectorizing is idempotent: Chroma persists in back-end/chroma_db and a chunk
is only re-embedded when its text hash changes, so restarts are fast.

Runs automatically at start-up (settings.seed_on_startup). It can also be run by hand:

    uv run linemate-seed              # populate + vectorize, print a summary
    uv run linemate-seed --reset      # wipe the Chroma collections first, then rebuild them
    uv run linemate-seed --no-vectors # only check that the seed data loads
"""

import argparse
import asyncio
import json
import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlmodel import select

from back_end.core.logging import get_logger
from back_end.core.security import hash_password, new_service_token
from back_end.core.timeutil import resolve_relative, utcnow
from back_end.db import models as m
from back_end.services import indexing, repo
from back_end.services.container import Services

log = get_logger(__name__)


@dataclass
class SeedReport:
    counts: dict[str, int] = field(default_factory=dict)
    jobs: list[str] = field(default_factory=list)

    def line(self) -> str:
        return ", ".join(f"{k}={v}" for k, v in self.counts.items())


def _when(value: Any, anchor: datetime) -> datetime:
    out = resolve_relative(value, anchor) if isinstance(value, str) else value
    if not isinstance(out, datetime):
        raise ValueError(f"Expected a relative timestamp, got {value!r}")
    return out


def _resolve_tokens(value: Any, anchor: datetime) -> Any:
    """Resolve '@ago:'/'@agod:' tokens nested in JSON payloads into ISO date/time strings."""
    if isinstance(value, dict):
        return {k: _resolve_tokens(v, anchor) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_tokens(v, anchor) for v in value]
    if isinstance(value, str) and value.startswith("@ago"):
        out = resolve_relative(value, anchor)
        return out.isoformat() if isinstance(out, (datetime, date)) else out
    return value


def _load(svc: Services, name: str) -> list[dict[str, Any]]:
    return json.loads((svc.settings.seed_data_dir / f"{name}.json").read_text(encoding="utf-8"))


async def seed_database(svc: Services, *, anchor: datetime | None = None) -> SeedReport:
    """Insert every seed row. Safe to call once per process (the database is in memory)."""
    now = anchor or utcnow()
    rnd = random.Random(2026)
    report = SeedReport()
    cfg = svc.settings
    async with svc.db.session() as s:
        if await s.get(m.Station, "grill") is not None:
            log.info("seed_skipped", reason="already populated")
            return report

        for st in _load(svc, "stations"):
            s.add(m.Station(id=st["id"], name=st["name"], short=st["short"], color=st["color"]))
        await s.flush()  # no ORM relationships, so parent tables are flushed before their children
        crew = _load(svc, "crew")
        for c in crew:
            s.add(m.CrewMember(id=c["id"], name=c["name"], station_id=c.get("station"), title=c["title"], initials=c["initials"]))
        await s.flush()

        pw_hash = hash_password(cfg.seed_password)
        users = _load(svc, "users")
        for u in users:
            s.add(
                m.User(
                    id=u["id"],
                    username=u["username"],
                    display_name=u["displayName"],
                    email=u["email"],
                    role=u["role"],
                    station_id=u.get("station"),
                    crew_member_id=u.get("crewMemberId"),
                    password_hash=pw_hash,
                    active=bool(u.get("active", True)),
                    last_login=_when(u["lastLogin"], now) if u.get("lastLogin") else None,
                    created_at=now - timedelta(days=200),
                )
            )
        await s.flush()

        # ---- documents: metadata row + file asset (registered in place) + body + review history
        docs = _load(svc, "documents")
        per_category: dict[str, int] = {}
        for d in docs:
            path = cfg.seed_documents_dir / d["fileName"]
            asset = svc.files.register(
                path, purpose="document", owner_type="document", owner_id=d["id"], asset_id=f"FA-{d['id']}", uploaded_by=d.get("ownerId")
            )
            asset.created_at = _when(d["createdAt"], now)
            s.add(asset)
            await s.flush()
            body_path = cfg.seed_data_dir / "document_bodies" / f"{d['id']}.md"
            s.add(
                m.Document(
                    id=d["id"],
                    title=d["title"],
                    category=d["category"],
                    station_id=d["station"],
                    owner_id=d["ownerId"],
                    created_at=_when(d["createdAt"], now),
                    last_reviewed=_when(d["lastReviewed"], now),
                    source="seed",
                    uploaded_by=d.get("ownerId"),
                    file_name=d["fileName"],
                    file_kind=d["fileKind"],
                    file_bytes=asset.bytes,
                    summary=d.get("summary", ""),
                    body=body_path.read_text(encoding="utf-8") if body_path.exists() else "",
                    tags=list(d.get("tags") or []),
                    status="processing",
                    version=int(d.get("version", 1)),
                    asset_id=asset.id,
                )
            )
            await s.flush()
            for r in d.get("reviewHistory") or []:
                s.add(m.ReviewEvent(document_id=d["id"], at=_when(r["at"], now), by=r["by"], note=r["note"]))
                s.add(
                    m.ActivityEvent(
                        at=_when(r["at"], now),
                        actor_id=r["by"],
                        action="reviewed",
                        target=d["id"],
                        target_type="document",
                        detail=r["note"],
                    )
                )
            await s.flush()
            prefix = d["id"].rsplit("-", 1)[0]  # DOC-SOP
            per_category[prefix] = max(per_category.get(prefix, 0), int(d["id"].rsplit("-", 1)[1]))

        # ---- tickets, threaded comments, attachments
        tickets = _load(svc, "tickets")
        for t in tickets:
            s.add(
                m.Ticket(
                    id=t["id"],
                    title=t["title"],
                    description=t.get("description", ""),
                    priority=t["priority"],
                    status=t["status"],
                    station_id=t["station"],
                    assignee_id=t.get("assigneeId"),
                    reporter_id=t["reporterId"],
                    related_doc_id=t.get("relatedDocId"),
                    created_at=_when(t["createdAt"], now),
                    updated_at=_when(t["updatedAt"], now),
                    tags=list(t.get("tags") or []),
                )
            )
            s.add(
                m.ActivityEvent(
                    at=_when(t["createdAt"], now),
                    actor_id=t["reporterId"],
                    action="opened",
                    target=t["id"],
                    target_type="ticket",
                    detail=t["title"],
                )
            )
        await s.flush()
        comments = _load(svc, "comments")
        for c in sorted(comments, key=lambda c: c.get("parentId") is not None):  # parents before replies
            s.add(
                m.TicketComment(
                    id=c["id"],
                    ticket_id=c["ticketId"],
                    author_id=c["authorId"],
                    body=c["body"],
                    created_at=_when(c["createdAt"], now),
                    parent_id=c.get("parentId"),
                )
            )
            s.add(
                m.ActivityEvent(
                    at=_when(c["createdAt"], now),
                    actor_id=c["authorId"],
                    action="commented",
                    target=c["ticketId"],
                    target_type="ticket",
                    detail=c["body"][:140],
                )
            )
        attachments = _load(svc, "attachments")
        att_dir = cfg.seed_data_dir.parent / "seed_attachments"
        for a in attachments:
            asset = svc.files.register(
                att_dir / a["name"],
                purpose="attachment",
                owner_type="ticket",
                owner_id=a["ticketId"],
                asset_id=f"FA-{a['id']}",
                uploaded_by=a["uploadedBy"],
            )
            asset.created_at = _when(a["uploadedAt"], now)
            s.add(asset)
            await s.flush()
            s.add(
                m.Attachment(
                    id=a["id"],
                    ticket_id=a["ticketId"],
                    asset_id=asset.id,
                    name=a["name"],
                    kind=a["kind"],
                    bytes=asset.bytes,
                    uploaded_by=a["uploadedBy"],
                    uploaded_at=_when(a["uploadedAt"], now),
                )
            )
            s.add(
                m.ActivityEvent(
                    at=_when(a["uploadedAt"], now),
                    actor_id=a["uploadedBy"],
                    action="attached",
                    target=a["ticketId"],
                    target_type="ticket",
                    detail=a["name"],
                )
            )

        # ---- agent run history (traces shown on Agent Runs; approvals link to their run)
        runs = _load(svc, "agent_runs")
        for r in runs:
            s.add(
                m.AgentRun(
                    id=r["id"],
                    graph=r["graph"],
                    user_id=r["userId"],
                    model=r["model"],
                    started_at=_when(r["startedAt"], now),
                    duration_ms=int(r["durationMs"]),
                    tokens_in=int(r["tokensIn"]),
                    tokens_out=int(r["tokensOut"]),
                    status=r["status"],
                    question=r.get("question"),
                    nodes=list(r["nodes"]),
                )
            )

        # ---- approvals (two pending, three decided)
        approvals = _load(svc, "approvals")
        for a in approvals:
            s.add(
                m.Approval(
                    id=a["id"],
                    kind=a["kind"],
                    status=a["status"],
                    title=a["title"],
                    summary=a["summary"],
                    requested_by=a["requestedBy"],
                    requested_at=_when(a["requestedAt"], now),
                    run_id=a["runId"],
                    source="agent",
                    payload=_resolve_tokens(a["payload"], now),
                    decided_by=a.get("decidedBy"),
                    decided_at=_when(a["decidedAt"], now) if a.get("decidedAt") else None,
                    reason=a.get("reason"),
                )
            )
            if a.get("decidedBy"):
                s.add(
                    m.ActivityEvent(
                        at=_when(a["decidedAt"], now),
                        actor_id=a["decidedBy"],
                        action=a["status"],
                        target=a["id"],
                        target_type="approval",
                        detail=a["title"],
                    )
                )

        # ---- answer citations over the last 7 days (drives "most cited" on the dashboard)
        citations = json.loads((cfg.seed_data_dir / "citations.json").read_text(encoding="utf-8"))
        for doc_id, n in citations.items():
            for _ in range(n):
                s.add(m.DocumentCitation(document_id=doc_id, at=now - timedelta(hours=rnd.uniform(1, 160))))

        # ---- MCP: service tokens (only argon2 hashes are stored) and 7 days of tool-call history
        tokens = _load(svc, "service_tokens")
        for t in tokens:
            plain, prefix = new_service_token()  # never shown: seed tokens exist only as history
            owner = "u-elena" if "elena" in t["name"] else "u-svc-mcp"
            s.add(
                m.ServiceToken(
                    id=t["id"],
                    name=t["name"],
                    client=t["client"],
                    scopes=list(t["scopes"]),
                    prefix=prefix,
                    token_hash=hash_password(plain),
                    user_id=owner,
                    created_at=_when(t["createdAt"], now),
                    last_used=_when(t["lastUsed"], now) if t.get("lastUsed") else None,
                    expires_at=_when(t["expiresAt"], now),
                    revoked=bool(t.get("revoked")),
                )
            )
        callers = [t["name"] for t in tokens if not t.get("revoked")] or ["claude-desktop-elena"]
        for tool in _load(svc, "mcp_tools"):
            for _ in range(int(tool.get("callsLast7d", 0))):
                s.add(
                    m.McpToolCall(
                        tool=tool["name"],
                        caller=rnd.choice(callers),
                        at=now - timedelta(hours=rnd.uniform(0.2, 167)),
                        status="ok" if rnd.random() > 0.03 else "error",
                        latency_ms=round(rnd.uniform(40, 420) if tool["access"] == "read" else rnd.uniform(90, 650), 1),
                    )
                )

        # ---- counters so new ids continue after the seed ids
        def top(rows: list[dict], prefix: str) -> int:
            return max((int(r["id"].removeprefix(prefix)) for r in rows if r["id"].startswith(prefix)), default=0)

        await repo.set_counter(s, "ticket", top(tickets, "TKT-"))
        await repo.set_counter(s, "crew", top(crew, "CM-"))
        await repo.set_counter(s, "service_token", top(_load(svc, "service_tokens"), "tok-"))
        await repo.set_counter(s, "comment", top(comments, "C-"))
        await repo.set_counter(s, "attachment", top(attachments, "ATT-"))
        await repo.set_counter(s, "approval", top(approvals, "APR-"))
        for prefix, n in per_category.items():
            await repo.set_counter(s, prefix.lower().replace("-", "_"), n)  # doc_sop, doc_rec, ...

        report.counts = {
            "stations": 4,
            "crew": len(crew),
            "users": len(users),
            "documents": len(docs),
            "tickets": len(tickets),
            "comments": len(comments),
            "attachments": len(attachments),
            "approvals": len(approvals),
            "agent_runs": len(runs),
            "service_tokens": len(tokens),
        }
    log.info("seed_loaded", **report.counts)
    return report


async def queue_ingest_jobs(svc: Services, requested_by: str = "system") -> list[str]:
    """One ingest job per seed document so the Vector Store page shows progress while indexing runs."""
    ids: list[str] = []
    async with svc.db.session() as s:
        docs = list((await s.exec(select(m.Document).where(m.Document.source == "seed").order_by(m.Document.id))).all())
        for d in docs:
            jid = await repo.next_id(s, "ingest_job", "JOB-", 4)
            s.add(
                m.IngestJob(
                    id=jid,
                    doc_id=d.id,
                    file_name=d.file_name,
                    collection=svc.vectors.docs_name,
                    stage="queued",
                    progress=0,
                    requested_by=requested_by,
                )
            )
            ids.append(jid)
    return ids


async def vectorize_seed(svc: Services, job_ids: list[str] | None = None) -> dict[str, int]:
    """Parse, chunk and embed every seed document and ticket photo (skips unchanged chunks)."""
    job_ids = job_ids if job_ids is not None else await queue_ingest_jobs(svc)
    async with svc.db.session() as s:
        jobs = {j.doc_id: j.id for j in (await s.exec(select(m.IngestJob).where(m.IngestJob.id.in_(job_ids)))).all()}  # type: ignore[attr-defined]
        attachments = [a.id for a in (await s.exec(select(m.Attachment))).all()]
    chunks = photos = 0
    for doc_id, job_id in sorted(jobs.items()):
        chunks += await indexing.index_document(svc, doc_id, job_id=job_id, keep_body=True)
    for att_id in attachments:
        photos += int(await indexing.index_ticket_photo(svc, att_id))
    async with svc.db.session() as s:
        live = {d.id: d.version for d in (await s.exec(select(m.Document))).all()}
    removed = await asyncio.to_thread(indexing.reconcile, svc, live, set(attachments))
    out = {"documents": len(jobs), "chunks": chunks, "photos": photos, "stale_vectors_removed": removed}
    log.info("seed_vectorized", **out)
    return out


def cli() -> None:
    parser = argparse.ArgumentParser(prog="linemate-seed", description=__doc__.split("\n\n")[0])
    parser.add_argument("--no-vectors", action="store_true", help="load the database only; skip Chroma")
    parser.add_argument("--reset", action="store_true", help="delete the Chroma collections before vectorizing")
    args = parser.parse_args()

    from back_end.main import startup_services

    async def run() -> None:
        svc = await startup_services(seed_vectors=False)
        try:
            async with svc.db.session() as s:
                counts = {
                    name: len((await s.exec(select(model))).all())
                    for name, model in [
                        ("users", m.User),
                        ("documents", m.Document),
                        ("tickets", m.Ticket),
                        ("comments", m.TicketComment),
                        ("approvals", m.Approval),
                    ]
                }
            print("database:", ", ".join(f"{k}={v}" for k, v in counts.items()))
            if args.no_vectors:
                return
            if args.reset:
                await asyncio.to_thread(svc.vectors.reset)
            result = await vectorize_seed(svc)
            async with svc.db.session() as s:
                failed = [(j.doc_id, j.error) for j in (await s.exec(select(m.IngestJob))).all() if j.stage == "failed"]
            print("chroma:", ", ".join(f"{k}={v}" for k, v in result.items()), f"(persisted in {svc.settings.chroma_dir})")
            for doc_id, err in failed:
                print(f"  failed {doc_id}: {err}")
            if failed:
                raise SystemExit(1)
        finally:
            await svc.aclose()

    asyncio.run(run())


if __name__ == "__main__":
    cli()


__all__ = ["SeedReport", "cli", "queue_ingest_jobs", "seed_database", "vectorize_seed"]
