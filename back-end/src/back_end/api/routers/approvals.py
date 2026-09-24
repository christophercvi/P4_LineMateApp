"""Human-in-the-loop approvals. Agents and MCP clients only *request*; a Kitchen Manager decides.

When an approval is decided, the action runs at once (agents/actions.py). If the agent run that raised
it is still paused in memory, that LangGraph thread is resumed so its trace records the outcome.
"""

from fastapi import APIRouter
from sqlmodel import col, select

from back_end.agents import actions
from back_end.agents.runner import resume_after_decision
from back_end.api.deps import ActorDep, SvcDep
from back_end.auth.permissions import can
from back_end.core.errors import Forbidden, NotFound
from back_end.db import models as m
from back_end.schemas import ApprovalOut, DecisionIn
from back_end.services import repo

router = APIRouter(prefix="/api", tags=["approvals"])

VIEW_DENIED = "Approvals are visible to Sous Chefs, the Kitchen Manager and Admins."


@router.get("/approvals", response_model=list[ApprovalOut], summary="Pending and decided approvals")
async def list_approvals(actor: ActorDep, svc: SvcDep) -> list[ApprovalOut]:
    if not can.view_approvals(actor):
        raise Forbidden(VIEW_DENIED)
    async with svc.db.session() as s:
        rows = (await s.exec(select(m.Approval).order_by(col(m.Approval.requested_at).desc()))).all()
        who = await repo.names(s)
    return [repo.approval_out(a, who) for a in rows]


@router.get("/approvals/{approval_id}", response_model=ApprovalOut, summary="One approval")
async def get_approval(approval_id: str, actor: ActorDep, svc: SvcDep) -> ApprovalOut:
    async with svc.db.session() as s:
        a = await s.get(m.Approval, approval_id)
        if a is None:
            raise NotFound(f"Approval {approval_id}")
        # The requester can always see their own request (e.g. a Line Cook in Ask LineMate).
        if not can.view_approvals(actor) and a.requested_by not in (actor.who, actor.user_id):
            raise Forbidden(VIEW_DENIED)
        return repo.approval_out(a, await repo.names(s))


@router.post("/approvals/{approval_id}/decision", response_model=ApprovalOut, summary="Approve or reject")
async def decide(approval_id: str, body: DecisionIn, actor: ActorDep, svc: SvcDep) -> ApprovalOut:
    async with svc.db.session() as s:
        a = await actions.decide(s, approval_id, actor, body.decision, body.reason, body.quantity)
        await s.flush()
        out = repo.approval_out(a, await repo.names(s))
    if a.thread_id:
        svc.spawn(resume_after_decision(svc, a), name=f"resume:{a.id}")
    return out
