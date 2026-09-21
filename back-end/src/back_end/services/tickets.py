"""Ticket writes shared by the REST API, approval execution and the MCP server.
Permission checks happen in the callers (routers / MCP tools); these functions apply the change
and write the activity trail.
"""

from sqlmodel.ext.asyncio.session import AsyncSession

from back_end.auth.permissions import PRIORITY_RANK, Actor, can, deny_reason
from back_end.core.errors import BadRequest, Forbidden, NotFound
from back_end.core.timeutil import utcnow
from back_end.db import models as m
from back_end.services import repo


async def get_ticket(s: AsyncSession, ticket_id: str) -> m.Ticket:
    t = await s.get(m.Ticket, ticket_id.upper())
    if t is None:
        raise NotFound(f"Ticket {ticket_id}")
    return t


async def create_ticket(
    s: AsyncSession,
    *,
    actor: Actor,
    title: str,
    description: str = "",
    priority: str = "medium",
    station: str | None = None,
    assignee_id: str | None = None,
    related_doc_id: str | None = None,
    tags: list[str] | None = None,
    via: str | None = None,
) -> m.Ticket:
    station = station or actor.station
    if station is None:
        raise BadRequest("Choose a station for this ticket")
    if await s.get(m.Station, station) is None:
        raise BadRequest(f"Unknown station {station}")
    if assignee_id:
        if not can.assign(actor, station) and assignee_id != actor.crew_member_id:
            raise Forbidden(deny_reason("assign", actor))
        if await s.get(m.CrewMember, assignee_id) is None:
            raise BadRequest(f"Unknown crew member {assignee_id}")
    if related_doc_id and await s.get(m.Document, related_doc_id) is None:
        raise BadRequest(f"Unknown document {related_doc_id}")
    ticket = m.Ticket(
        id=await repo.next_id(s, "ticket", "TKT-"),
        title=title.strip(),
        description=description.strip(),
        priority=priority,
        status="open",
        station_id=station,
        assignee_id=assignee_id,
        reporter_id=actor.who,
        related_doc_id=related_doc_id,
        tags=sorted({t.strip().lower() for t in tags or [] if t.strip()}),
    )
    s.add(ticket)
    await repo.log_activity(s, actor.who, "created", ticket.id, "ticket", via or f"{priority} · {station}")
    return ticket


async def add_comment(s: AsyncSession, ticket: m.Ticket, author_id: str, body: str, parent_id: str | None = None) -> m.TicketComment:
    if parent_id:
        parent = await s.get(m.TicketComment, parent_id)
        if parent is None or parent.ticket_id != ticket.id:
            raise BadRequest("Reply target is not on this ticket")
    c = m.TicketComment(
        id=await repo.next_id(s, "comment", "C-"), ticket_id=ticket.id, author_id=author_id, body=body.strip(), parent_id=parent_id
    )
    s.add(c)
    ticket.updated_at = utcnow()
    s.add(ticket)
    await repo.log_activity(s, author_id, "commented on", ticket.id, "ticket", body.strip()[:90])
    return c


async def patch_ticket(
    s: AsyncSession,
    ticket: m.Ticket,
    actor: Actor,
    *,
    status: str | None = None,
    priority: str | None = None,
    assignee_id: str | None = None,
    assignee_set: bool = False,
) -> m.Ticket:
    changes: list[str] = []
    if priority and priority != ticket.priority:
        lowering = PRIORITY_RANK[priority] < PRIORITY_RANK[ticket.priority]
        if lowering and not can.lower_priority_or_close(actor, ticket.station_id):
            raise Forbidden(deny_reason("lower", actor))
        if not lowering and not can.raise_priority(actor):
            raise Forbidden(deny_reason("lower", actor))
        changes.append(f"priority {ticket.priority} → {priority}")
        ticket.priority = priority
    if status and status != ticket.status:
        if status == "closed" and not can.lower_priority_or_close(actor, ticket.station_id):
            raise Forbidden(deny_reason("lower", actor))
        if not can.change_status(actor, ticket.station_id, ticket.assignee_id):
            raise Forbidden(deny_reason("status", actor))
        changes.append(f"status {ticket.status.replace('_', ' ')} → {status.replace('_', ' ')}")
        ticket.status = status
    if assignee_set and assignee_id != ticket.assignee_id:
        if not can.assign(actor, ticket.station_id):
            raise Forbidden(deny_reason("assign", actor))
        if assignee_id and await s.get(m.CrewMember, assignee_id) is None:
            raise BadRequest(f"Unknown crew member {assignee_id}")
        changes.append(f"assignee → {assignee_id or 'unassigned'}")
        ticket.assignee_id = assignee_id
    if changes:
        ticket.updated_at = utcnow()
        s.add(ticket)
        await repo.log_activity(s, actor.who, "updated", ticket.id, "ticket", "; ".join(changes))
    return ticket
