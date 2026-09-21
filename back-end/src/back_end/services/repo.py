"""Small data-access helpers and row -> API schema serializers shared by routers, agents and MCP.

The dataset is a single kitchen (tens of documents, a few hundred tickets), so list endpoints
load whole tables and filter in Python; this keeps the permission logic in one readable place.
"""

from collections import Counter as Tally
from collections.abc import Iterable, Sequence
from datetime import timedelta

from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from back_end.auth.permissions import Actor, can
from back_end.core.timeutil import days_since, utcnow
from back_end.db import models as m
from back_end.schemas import (
    ActivityOut,
    ApprovalOut,
    AttachmentOut,
    CommentOut,
    DocListItem,
    DocumentFull,
    IngestJobOut,
    MismatchRow,
    ReviewEventOut,
    RunOut,
    TicketBase,
    TicketListItem,
)
from back_end.services import analytics as an
from back_end.services.storage import FileStore


async def next_id(s: AsyncSession, name: str, prefix: str, width: int = 3) -> str:
    row = await s.get(m.Counter, name)
    if row is None:
        row = m.Counter(name=name, value=0)
        s.add(row)
    row.value += 1
    await s.flush()
    return f"{prefix}{row.value:0{width}d}"


async def set_counter(s: AsyncSession, name: str, value: int) -> None:
    row = await s.get(m.Counter, name) or m.Counter(name=name)
    row.value = max(row.value or 0, value)
    s.add(row)


async def all_rows(s: AsyncSession, model):
    return list((await s.exec(select(model))).all())


async def lookup(s: AsyncSession) -> an.Lookup:
    return an.Lookup.build(await all_rows(s, m.CrewMember), await all_rows(s, m.Document), await all_rows(s, m.Station))


async def names(s: AsyncSession) -> dict[str, str]:
    """Display names for crew ids and user ids (activity, approvals and comments use either)."""
    out = {c.id: c.name for c in await all_rows(s, m.CrewMember)}
    for u in await all_rows(s, m.User):
        out.setdefault(u.id, u.display_name)
    out.setdefault("system", "LineMate")
    return out


async def reviews_by_doc(s: AsyncSession) -> dict[str, list[m.ReviewEvent]]:
    out: dict[str, list[m.ReviewEvent]] = {}
    for r in (await s.exec(select(m.ReviewEvent).order_by(col(m.ReviewEvent.at)))).all():
        out.setdefault(r.document_id, []).append(r)
    return out


async def citations_7d(s: AsyncSession) -> dict[str, int]:
    since = utcnow() - timedelta(days=7)
    rows = (
        await s.exec(
            select(m.DocumentCitation.document_id, func.count())
            .where(m.DocumentCitation.at >= since)
            .group_by(m.DocumentCitation.document_id)
        )
    ).all()
    return {doc_id: int(n) for doc_id, n in rows}


async def log_activity(s: AsyncSession, actor_id: str, action: str, target: str, target_type: str, detail: str | None = None) -> None:
    s.add(m.ActivityEvent(actor_id=actor_id, action=action, target=target, target_type=target_type, detail=detail))


def visible_doc(a: Actor, d: m.Document) -> bool:
    return d.status != "archived" and (d.category != "incident" or can.see_incident_reports(a))


# ---------------------------------------------------------------------------- serializers


def doc_base_fields(d: m.Document, reviews: Sequence[m.ReviewEvent], threshold: int = an.STALE_DEFAULT) -> dict:
    return {
        "id": d.id,
        "title": d.title,
        "category": d.category,
        "station": d.station_id,
        "owner_id": d.owner_id,
        "created_at": d.created_at,
        "last_reviewed": d.last_reviewed,
        "source": d.source,
        "uploaded_by": d.uploaded_by,
        "file_name": d.file_name,
        "file_kind": d.file_kind,
        "file_bytes": d.file_bytes,
        "file_url": FileStore.url(d.asset_id),
        "summary": d.summary,
        "tags": d.tags or [],
        "status": d.status,
        "version": d.version,
        "chunk_count": d.chunk_count,
        "review_history": [ReviewEventOut(at=r.at, by=r.by, note=r.note) for r in reviews],
        "stale": an.is_stale(d, threshold),
        "days_since_review": days_since(d.last_reviewed),
    }


def doc_list_item(d: m.Document, reviews: Sequence[m.ReviewEvent], tickets: Iterable[m.Ticket]) -> DocListItem:
    open_n = sum(1 for t in tickets if t.related_doc_id == d.id and an.is_open(t))
    return DocListItem(**doc_base_fields(d, reviews), open_tickets=open_n)


def doc_full(d: m.Document, reviews: Sequence[m.ReviewEvent], cited: int) -> DocumentFull:
    return DocumentFull(**doc_base_fields(d, reviews), body=d.body, cited_last7d=cited)


def ticket_base(t: m.Ticket) -> TicketBase:
    return TicketBase(
        id=t.id,
        title=t.title,
        description=t.description,
        priority=t.priority,
        status=t.status,
        station=t.station_id,
        assignee_id=t.assignee_id,
        reporter_id=t.reporter_id,
        related_doc_id=t.related_doc_id,
        created_at=t.created_at,
        updated_at=t.updated_at,
        tags=t.tags or [],
    )


class TicketContext:
    """Pre-computed counts so serialising a list of tickets is O(n)."""

    def __init__(
        self, tickets: Sequence[m.Ticket], lk: an.Lookup, comments: Iterable[m.TicketComment], attachments: Iterable[m.Attachment]
    ):
        self.lk = lk
        self.comment_counts = Tally(c.ticket_id for c in comments)
        self.attachment_counts = Tally(a.ticket_id for a in attachments)
        self.mismatch_ids = {r["ticket_id"] for r in an.mismatches(tickets, lk)}

    def item(self, t: m.Ticket) -> TicketListItem:
        doc = self.lk.docs.get(t.related_doc_id) if t.related_doc_id else None
        return TicketListItem(
            **ticket_base(t).model_dump(),
            comment_count=self.comment_counts.get(t.id, 0),
            attachment_count=self.attachment_counts.get(t.id, 0),
            doc_stale=bool(doc and an.is_stale(doc)),
            mismatch=t.id in self.mismatch_ids,
        )


async def ticket_context(s: AsyncSession, tickets: Sequence[m.Ticket] | None = None) -> TicketContext:
    tickets = tickets if tickets is not None else await all_rows(s, m.Ticket)
    return TicketContext(tickets, await lookup(s), await all_rows(s, m.TicketComment), await all_rows(s, m.Attachment))


def comment_out(c: m.TicketComment) -> CommentOut:
    return CommentOut(id=c.id, ticket_id=c.ticket_id, author_id=c.author_id, body=c.body, created_at=c.created_at, parent_id=c.parent_id)


def attachment_out(a: m.Attachment) -> AttachmentOut:
    return AttachmentOut(
        id=a.id,
        ticket_id=a.ticket_id,
        name=a.name,
        kind=a.kind,
        bytes=a.bytes,
        url=FileStore.url(a.asset_id),
        uploaded_by=a.uploaded_by,
        uploaded_at=a.uploaded_at,
    )


def activity_out(e: m.ActivityEvent, who: dict[str, str]) -> ActivityOut:
    return ActivityOut(
        id=str(e.id),
        at=e.at,
        actor_id=e.actor_id,
        actor_name=who.get(e.actor_id, e.actor_id),
        action=e.action,
        target=e.target,
        target_type=e.target_type,
        detail=e.detail,
    )


def approval_out(a: m.Approval, who: dict[str, str]) -> ApprovalOut:
    return ApprovalOut(
        id=a.id,
        kind=a.kind,
        status=a.status,
        title=a.title,
        summary=a.summary,
        requested_by=a.requested_by,
        requested_by_name=who.get(a.requested_by, a.requested_by),
        requested_at=a.requested_at,
        run_id=a.run_id,
        source=a.source,
        payload=a.payload or {},
        decided_by=a.decided_by,
        decided_by_name=who.get(a.decided_by, a.decided_by) if a.decided_by else None,
        decided_at=a.decided_at,
        reason=a.reason,
        result=a.result,
    )


def run_out(r: m.AgentRun, who: dict[str, str]) -> RunOut:
    return RunOut(
        id=r.id,
        graph=r.graph,
        user_id=r.user_id,
        user_name=who.get(r.user_id, r.user_id),
        model=r.model,
        started_at=r.started_at,
        duration_ms=r.duration_ms,
        tokens_in=r.tokens_in,
        tokens_out=r.tokens_out,
        status=r.status,
        question=r.question,
        nodes=r.nodes or [],
    )


def job_out(j: m.IngestJob, who: dict[str, str]) -> IngestJobOut:
    return IngestJobOut(
        id=j.id,
        doc_id=j.doc_id,
        file_name=j.file_name,
        collection=j.collection,
        stage=j.stage,
        progress=j.progress,
        started_at=j.started_at,
        finished_at=j.finished_at,
        chunks=j.chunks,
        error=j.error,
        requested_by=j.requested_by,
        requested_by_name=who.get(j.requested_by, j.requested_by),
    )


def mismatch_out(row: dict) -> MismatchRow:
    return MismatchRow(**row)


def actor_for(user: m.User) -> Actor:
    return Actor(
        user_id=user.id, role=user.role, station=user.station_id, crew_member_id=user.crew_member_id, display_name=user.display_name
    )
