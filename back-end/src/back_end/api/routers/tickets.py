"""Tickets (ownership, workload, triage input) with threaded comments and attachments.

Attachments are multipart uploads stored under storage/attachments/<ticketId>/ and recorded in the
file_assets table. Photos are also embedded with nomic-embed-vision so they can be found from a text
question in Ask LineMate.
"""

from typing import Annotated

from fastapi import APIRouter, File, UploadFile, status
from sqlmodel import col, select

from back_end.api.deps import ActorDep, SvcDep
from back_end.auth.permissions import can
from back_end.core.errors import Forbidden
from back_end.core.timeutil import utcnow
from back_end.db import models as m
from back_end.schemas import (
    AttachmentOut,
    CommentIn,
    CommentOut,
    MismatchRow,
    TicketCreateIn,
    TicketDetailOut,
    TicketListItem,
    TicketPatchIn,
    TicketPermissions,
)
from back_end.services import analytics as an
from back_end.services import indexing, repo
from back_end.services import tickets as svc_tickets
from back_end.services.storage import IMAGE_KINDS

router = APIRouter(prefix="/api", tags=["tickets"])

ATTACHMENT_KINDS = {"png", "jpg", "pdf", "docx", "xlsx", "txt", "md"}


def _csv(raw: str | None) -> list[str]:
    return [x for x in (raw or "").split(",") if x]


@router.get("/tickets", response_model=list[TicketListItem], summary="Tickets with filters")
async def list_tickets(
    actor: ActorDep,
    svc: SvcDep,
    priority: str | None = None,
    station: str | None = None,
    assignee: str | None = None,
    q: str = "",
    mine: bool = False,
    status: str | None = None,
) -> list[TicketListItem]:
    """`status` and `priority` accept comma-separated values (e.g. `status=open,blocked`)."""
    statuses, priorities = _csv(status), _csv(priority)
    needle = q.strip().lower()
    async with svc.db.session() as s:
        tickets = list((await s.exec(select(m.Ticket).order_by(col(m.Ticket.updated_at).desc()))).all())
        ctx = await repo.ticket_context(s, tickets)
    out = []
    for t in tickets:
        if (statuses and t.status not in statuses) or (priorities and t.priority not in priorities):
            continue
        if (station and t.station_id != station) or (assignee and t.assignee_id != assignee):
            continue
        if mine and (not actor.crew_member_id or t.assignee_id != actor.crew_member_id):
            continue
        if needle and needle not in f"{t.id} {t.title} {t.description} {' '.join(t.tags or [])}".lower():
            continue
        out.append(ctx.item(t))
    return out


@router.get("/tickets/{ticket_id}", response_model=TicketDetailOut, summary="Ticket with thread and history")
async def get_ticket(ticket_id: str, actor: ActorDep, svc: SvcDep) -> TicketDetailOut:
    async with svc.db.session() as s:
        t = await svc_tickets.get_ticket(s, ticket_id)
        tickets = await repo.all_rows(s, m.Ticket)
        ctx = await repo.ticket_context(s, tickets)
        comments = (
            await s.exec(select(m.TicketComment).where(m.TicketComment.ticket_id == t.id).order_by(col(m.TicketComment.created_at)))
        ).all()
        atts = (await s.exec(select(m.Attachment).where(m.Attachment.ticket_id == t.id).order_by(col(m.Attachment.uploaded_at)))).all()
        doc = await s.get(m.Document, t.related_doc_id) if t.related_doc_id else None
        related = None
        if doc and (doc.category != "incident" or can.see_incident_reports(actor)):
            reviews = sorted((await repo.reviews_by_doc(s)).get(doc.id, []), key=lambda r: r.at, reverse=True)
            related = repo.doc_list_item(doc, reviews, tickets)
        events = (
            await s.exec(select(m.ActivityEvent).where(m.ActivityEvent.target == t.id).order_by(col(m.ActivityEvent.at).desc()).limit(20))
        ).all()
        who = await repo.names(s)
        mismatch = next((r for r in an.mismatches(tickets, ctx.lk) if r["ticket_id"] == t.id), None)
    return TicketDetailOut(
        ticket=ctx.item(t),
        comments=[repo.comment_out(c) for c in comments],
        attachments=[repo.attachment_out(a) for a in atts],
        related_doc=related,
        mismatch=MismatchRow(**mismatch) if mismatch else None,
        activity=[repo.activity_out(e, who) for e in events],
        permissions=TicketPermissions(
            change_status=can.change_status(actor, t.station_id, t.assignee_id),
            lower_or_close=can.lower_priority_or_close(actor, t.station_id),
            raise_=can.raise_priority(actor),
            assign=can.assign(actor, t.station_id),
            comment=can.comment(actor),
            attach=can.attach_to_ticket(actor),
        ),
    )


@router.post(
    "/tickets", response_model=TicketListItem, status_code=status.HTTP_201_CREATED, summary="Open a ticket (attach files afterwards)"
)
async def create_ticket(body: TicketCreateIn, actor: ActorDep, svc: SvcDep) -> TicketListItem:
    if not can.create_ticket(actor):
        raise Forbidden("Admins run the system and do not create kitchen tickets.")
    async with svc.db.session() as s:
        t = await svc_tickets.create_ticket(
            s,
            actor=actor,
            title=body.title,
            description=body.description,
            priority=body.priority,
            station=body.station,
            assignee_id=body.assignee_id,
            related_doc_id=body.related_doc_id,
            tags=body.tags,
        )
        await s.flush()
        ctx = await repo.ticket_context(s)
        return ctx.item(t)


@router.patch("/tickets/{ticket_id}", response_model=TicketListItem, summary="Change status, priority or assignee")
async def patch_ticket(ticket_id: str, body: TicketPatchIn, actor: ActorDep, svc: SvcDep) -> TicketListItem:
    async with svc.db.session() as s:
        t = await svc_tickets.get_ticket(s, ticket_id)
        await svc_tickets.patch_ticket(
            s,
            t,
            actor,
            status=body.status,
            priority=body.priority,
            assignee_id=body.assignee_id,
            assignee_set=body.assignee_set or "assignee_id" in body.model_fields_set,
        )
        await s.flush()
        ctx = await repo.ticket_context(s)
        return ctx.item(t)


@router.post(
    "/tickets/{ticket_id}/comments", response_model=CommentOut, status_code=status.HTTP_201_CREATED, summary="Reply on the ticket thread"
)
async def add_comment(ticket_id: str, body: CommentIn, actor: ActorDep, svc: SvcDep) -> CommentOut:
    if not can.comment(actor):
        raise Forbidden("Admins cannot comment on kitchen tickets.")
    async with svc.db.session() as s:
        t = await svc_tickets.get_ticket(s, ticket_id)
        c = await svc_tickets.add_comment(s, t, actor.who, body.body, body.parent_id)
        return repo.comment_out(c)


@router.post(
    "/tickets/{ticket_id}/attachments",
    response_model=list[AttachmentOut],
    status_code=status.HTTP_201_CREATED,
    summary="Attach photos or files to a ticket",
)
async def add_attachments(
    ticket_id: str,
    actor: ActorDep,
    svc: SvcDep,
    files: Annotated[list[UploadFile], File(description="Photos (PNG/JPG), PDF, Word, Excel or text")],
) -> list[AttachmentOut]:
    if not can.attach_to_ticket(actor):
        raise Forbidden("Admins cannot attach files to kitchen tickets.")
    payloads = [(f.filename or "attachment", await f.read()) for f in files]
    created: list[m.Attachment] = []
    async with svc.db.session() as s:
        t = await svc_tickets.get_ticket(s, ticket_id)
        for name, data in payloads:
            att_id = await repo.next_id(s, "attachment", "ATT-", 1)
            stored = svc.files.save(
                data=data,
                original_name=name,
                purpose="attachment",
                owner_type="ticket",
                owner_id=t.id,
                subdir=f"attachments/{t.id}",
                uploaded_by=actor.user_id,
                allowed=ATTACHMENT_KINDS,
                asset_id=f"FA-{att_id}",
            )
            s.add(stored.asset)
            await s.flush()
            a = m.Attachment(
                id=att_id,
                ticket_id=t.id,
                asset_id=stored.asset.id,
                name=stored.asset.original_name,
                kind=stored.kind,
                bytes=stored.asset.bytes,
                uploaded_by=actor.who,
                uploaded_at=utcnow(),
            )
            s.add(a)
            created.append(a)
            await repo.log_activity(s, actor.who, "attached a file to", t.id, "ticket", a.name)
        t.updated_at = utcnow()
        s.add(t)
        await s.flush()
        out = [repo.attachment_out(a) for a in created]
    for a in created:
        if a.kind in IMAGE_KINDS:
            svc.spawn(indexing.index_ticket_photo(svc, a.id), name=f"photo:{a.id}")
    return out
