from datetime import timedelta

from fastapi import APIRouter
from sqlmodel import col, select

from back_end.api.deps import ActorDep, SvcDep
from back_end.auth.permissions import can
from back_end.core.timeutil import days_since, utcnow
from back_end.db import models as m
from back_end.schemas import DashboardAdmin, DashboardKpis, DashboardOut, StaleRow, TriageTeaser
from back_end.services import analytics as an
from back_end.services import repo

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardOut, summary="Role-aware home page data")
async def dashboard(actor: ActorDep, svc: SvcDep) -> DashboardOut:
    station = actor.station if actor.role in ("sous_chef", "line_cook") else None
    async with svc.db.session() as s:
        tickets = await repo.all_rows(s, m.Ticket)
        docs = await repo.all_rows(s, m.Document)
        lk = await repo.lookup(s)
        ctx = await repo.ticket_context(s, tickets)
        cites = await repo.citations_7d(s)
        who = await repo.names(s)
        pending = len((await s.exec(select(m.Approval).where(m.Approval.status == "pending"))).all())
        events = (await s.exec(select(m.ActivityEvent).order_by(col(m.ActivityEvent.at).desc()).limit(60))).all()
        runs = await repo.all_rows(s, m.AgentRun) if actor.role == "admin" else []

    scoped = [t for t in tickets if not station or t.station_id == station]
    open_ = [t for t in scoped if an.is_open(t)]
    today = utcnow()
    open_trend = []
    for i in range(13, -1, -1):
        day_end = today - timedelta(days=i)
        open_trend.append(sum(1 for t in scoped if t.created_at <= day_end and (an.is_open(t) or t.updated_at > day_end)))
    closed_trend = an.weekly_trend(scoped, weeks=12, closed=True)
    stale = an.stale_rows(docs, tickets, cites, an.STALE_DEFAULT, actor.station if actor.role == "sous_chef" else None)
    resolved30 = sum(1 for t in scoped if not an.is_open(t) and days_since(t.updated_at) <= 30)
    created30 = sum(1 for t in scoped if days_since(t.created_at) <= 30)
    mine = [t for t in tickets if actor.crew_member_id and t.assignee_id == actor.crew_member_id and an.is_open(t)]
    by_id = {t.id: t for t in tickets}
    teaser = [TriageTeaser(**r, ticket=repo.ticket_base(by_id[r["ticket_id"]])) for r in an.rank_tickets(tickets, lk, station)[:3]]
    see_inc = can.see_incident_reports(actor)
    feed = [e for e in events if see_inc or not e.target.startswith("DOC-INC")][:10]

    admin = None
    if actor.role == "admin":
        loaded = []
        try:
            loaded = await svc.ollama.ps()
        except Exception:  # noqa: BLE001 - Ollama down: show zero rather than fail the page
            loaded = []
        vectors = sum(v["vectors"] for v in svc.vectors.stats().values())
        admin = DashboardAdmin(
            runs_today=sum(1 for r in runs if days_since(r.started_at) == 0),
            models_loaded=len(loaded),
            vectors=vectors,
            errors=sum(1 for r in runs if r.status == "error"),
        )

    return DashboardOut(
        scope=lk.station_name(station) if station else "All stations",
        kpis=DashboardKpis(
            open=len(open_),
            critical=sum(1 for t in open_ if t.priority == "critical"),
            high=sum(1 for t in open_ if t.priority == "high"),
            stale_docs=len(stale),
            pending_approvals=pending,
            my_open=len(mine),
            resolution_rate=min(1.0, resolved30 / created30) if created30 else 0.0,
        ),
        open_trend=open_trend,
        closed_trend=closed_trend,
        my_tickets=[ctx.item(t) for t in mine],
        stale_cited=[StaleRow(**r) for r in stale if r["cited_last7d"] > 0 and r["category"] == "sop"],
        triage_teaser=teaser,
        activity=[repo.activity_out(e, who) for e in feed],
        admin=admin,
    )
