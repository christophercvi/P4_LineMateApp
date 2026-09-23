"""Human-in-the-loop actions.

Agents and MCP clients never change kitchen data directly for consequential actions; they create an
Approval. When the Kitchen Manager approves, `execute()` applies the change and records the result.
Both graphs resume through this module, and so does the decision endpoint when the graph thread
is no longer in memory (for example after a server restart).
"""

from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from back_end.auth.permissions import Actor, can, deny_reason
from back_end.core.errors import BadRequest, Conflict, Forbidden, NotFound
from back_end.core.timeutil import utcnow
from back_end.db import models as m
from back_end.services import repo
from back_end.services import tickets as ticket_ops

TITLES = {
    "draft_supply_order": "Supply order",
    "escalate_incident": "Escalation",
    "reassign_ticket": "Reassignment",
    "create_ticket": "New ticket",
}


async def create_approval(
    s: AsyncSession,
    *,
    kind: str,
    title: str,
    summary: str,
    payload: dict[str, Any],
    requested_by: str,
    run_id: str,
    thread_id: str | None = None,
    source: str = "agent",
) -> m.Approval:
    approval = m.Approval(
        id=await repo.next_id(s, "approval", "APR-"),
        kind=kind,
        title=title,
        summary=summary,
        payload=payload,
        requested_by=requested_by,
        run_id=run_id,
        thread_id=thread_id,
        source=source,
    )
    s.add(approval)
    await repo.log_activity(s, requested_by, "requested approval", approval.id, "approval", title)
    return approval


async def decide(
    s: AsyncSession, approval_id: str, actor: Actor, decision: str, reason: str = "", quantity: int | None = None
) -> m.Approval:
    approval = await s.get(m.Approval, approval_id)
    if approval is None:
        raise NotFound(f"Approval {approval_id}")
    if not can.approve(actor):
        raise Forbidden(deny_reason("approve", actor))
    if approval.status != "pending":
        raise Conflict(f"{approval.id} was already {approval.status}")
    if decision == "rejected" and not reason.strip():
        raise BadRequest("Give a reason when rejecting so the requester knows what to change")
    approval.decided_by = actor.who
    approval.decided_at = utcnow()
    approval.reason = reason.strip() or None
    if decision == "approved":
        if quantity and approval.kind == "draft_supply_order":
            approval.payload = {**approval.payload, "quantity": quantity}
        approval.result = await execute(s, approval, actor)
        approval.status = "approved"
    else:
        approval.status = "rejected"
        ticket_id = (approval.payload or {}).get("ticketId")
        if ticket_id and (t := await s.get(m.Ticket, ticket_id)):
            await ticket_ops.add_comment(s, t, actor.who, f"{TITLES[approval.kind]} request {approval.id} declined: {reason.strip()}")
    s.add(approval)
    await repo.log_activity(s, actor.who, decision, approval.id, "approval", approval.title)
    return approval


async def execute(s: AsyncSession, approval: m.Approval, actor: Actor) -> dict[str, Any]:
    p = approval.payload or {}
    match approval.kind:
        case "draft_supply_order":
            qty = int(p.get("quantity") or 1)
            total = round(float(p.get("unitPrice") or 0) * qty, 2)
            ref = await repo.next_id(s, "purchase_order", "PO-", 4)
            result = {"orderRef": ref, "quantity": qty, "total": total, "supplier": p.get("supplier"), "status": "draft sent to supplier"}
            if (tid := p.get("ticketId")) and (t := await s.get(m.Ticket, tid)):
                await ticket_ops.add_comment(
                    s,
                    t,
                    actor.who,
                    f"Supply order {ref} approved: {qty} × {p.get('item')} ({p.get('sku')}) from {p.get('supplier')}, "
                    f"${total:,.2f}. Needed by {p.get('neededBy', 'n/a')}.",
                )
                if t.status == "open":
                    t.status = "in_progress"
                    s.add(t)
            return result
        case "escalate_incident":
            t = await ticket_ops.get_ticket(s, p["ticketId"])
            before = t.priority
            t.priority = "critical"
            t.tags = sorted({*(t.tags or []), "escalated"})
            if t.status in ("open", "blocked"):
                t.status = "in_progress"
            t.updated_at = utcnow()
            s.add(t)
            hold = " Product is on hold until cleared." if p.get("holdProduct") else ""
            notify = ", ".join(p.get("notify") or [])
            await ticket_ops.add_comment(
                s,
                t,
                actor.who,
                f"Escalated ({str(p.get('severity', 'incident')).replace('_', ' ')}). Notified: {notify or 'n/a'}.{hold} "
                f"{p.get('note') or ''}".strip(),
            )
            return {
                "ticketId": t.id,
                "priority": f"{before} → critical",
                "notified": p.get("notify") or [],
                "holdProduct": bool(p.get("holdProduct")),
            }
        case "reassign_ticket":
            t = await ticket_ops.get_ticket(s, p["ticketId"])
            to = p.get("toAssigneeId")
            if to and await s.get(m.CrewMember, to) is None:
                raise BadRequest(f"Unknown crew member {to}")
            before = t.assignee_id
            t.assignee_id = to
            t.updated_at = utcnow()
            s.add(t)
            await ticket_ops.add_comment(s, t, actor.who, f"Reassigned {before or 'unassigned'} → {to}. {p.get('reason') or ''}".strip())
            return {"ticketId": t.id, "from": before, "to": to}
        case "create_ticket":
            requester = Actor(
                user_id=approval.requested_by, role="kitchen_manager", station=p.get("station"), crew_member_id=None, display_name=""
            )
            t = await ticket_ops.create_ticket(
                s,
                actor=requester,
                title=p["title"],
                description=p.get("description", ""),
                priority=p.get("priority", "medium"),
                station=p.get("station"),
                tags=p.get("tags") or [],
                related_doc_id=p.get("relatedDocId"),
                via=f"via {approval.source.upper()} · approved by {actor.display_name or actor.who}",
            )
            t.reporter_id = approval.requested_by
            return {"ticketId": t.id}
    raise BadRequest(f"Unknown approval kind {approval.kind}")


def interrupt_payload(a: m.Approval, actor: Actor) -> dict[str, Any]:
    """Shape of the SSE `interrupt` event the chat and triage screens render as an approval card."""
    return {
        "approvalId": a.id,
        "kind": a.kind,
        "title": a.title,
        "summary": a.summary,
        "payload": a.payload,
        "canApprove": can.approve(actor),
    }
