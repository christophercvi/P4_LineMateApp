"""Stale-document audit, ownership mismatches and workload analytics."""

from datetime import UTC, date, datetime, time
from typing import Annotated

from fastapi import APIRouter, Query

from back_end.api.deps import ActorDep, SvcDep
from back_end.auth.permissions import can
from back_end.core.errors import Forbidden
from back_end.core.timeutil import days_since
from back_end.db import models as m
from back_end.schemas import FlowOut, MismatchRow, OwnershipOut, PerDayOut, StaleAuditOut, StaleRow, WorkloadOut, WorkloadRow
from back_end.services import analytics as an
from back_end.services import repo

router = APIRouter(prefix="/api", tags=["audits & analytics"])


@router.get("/audits/stale", response_model=StaleAuditOut, summary="Documents past their review threshold")
async def stale_audit(
    actor: ActorDep,
    svc: SvcDep,
    threshold: Annotated[int, Query(ge=30, le=730)] = an.STALE_DEFAULT,
) -> StaleAuditOut:
    if not can.view_audits(actor):
        raise Forbidden("The stale-document audit is for Sous Chefs, the Kitchen Manager and Admins.")
    station = actor.station if actor.role == "sous_chef" else None
    async with svc.db.session() as s:
        docs = await repo.all_rows(s, m.Document)
        tickets = await repo.all_rows(s, m.Ticket)
        cites = await repo.citations_7d(s)
        lk = await repo.lookup(s)
    in_scope = [d for d in docs if not station or d.station_id == station]
    live = [d for d in in_scope if d.category != "incident" and d.status != "archived"]
    return StaleAuditOut(
        threshold=threshold,
        scope=lk.station_name(station) if station else "All stations",
        rows=[StaleRow(**r) for r in an.stale_rows(docs, tickets, cites, threshold, station)],
        excluded_incidents=[
            {"docId": d.id, "title": d.title, "days": days_since(d.last_reviewed)}
            for d in in_scope
            if d.category == "incident" and days_since(d.last_reviewed) > threshold
        ],
        boundary=[{"docId": d.id, "title": d.title} for d in live if days_since(d.last_reviewed) == threshold],
        all=[{"docId": d.id, "title": d.title, "station": d.station_id, "days": days_since(d.last_reviewed)} for d in live],
    )


@router.get("/audits/ownership", response_model=OwnershipOut, summary="Tickets assigned outside the document's station")
async def ownership_audit(actor: ActorDep, svc: SvcDep) -> OwnershipOut:
    if not can.view_ownership_audit(actor):
        raise Forbidden("The ownership audit is for Sous Chefs and the Kitchen Manager, who reassign tickets.")
    station = actor.station if actor.role == "sous_chef" else None
    async with svc.db.session() as s:
        tickets = await repo.all_rows(s, m.Ticket)
        lk = await repo.lookup(s)
    rows = an.mismatches(tickets, lk, station)
    return OwnershipOut(
        scope=lk.station_name(station) if station else "All stations",
        rows=[MismatchRow(**r) for r in rows],
        flows=[FlowOut(**f) for f in an.mismatch_flows(rows, lk)],
    )


def _day(d: date | None, end: bool = False) -> datetime | None:
    if d is None:
        return None
    return datetime.combine(d, time.max if end else time.min, tzinfo=UTC)


@router.get("/analytics/workload", response_model=WorkloadOut, summary="Open tickets per station with a load index")
async def workload(
    actor: ActorDep,
    svc: SvcDep,
    statuses: str = "",
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> WorkloadOut:
    """`load index` = a station's open tickets divided by the mean across stations; above 1.5 is flagged."""
    if not can.view_analytics(actor):
        raise Forbidden("Workload analytics are for Sous Chefs, the Kitchen Manager and Admins.")
    async with svc.db.session() as s:
        tickets = await repo.all_rows(s, m.Ticket)
        stations = await repo.all_rows(s, m.Station)
        lk = await repo.lookup(s)
    rows, mean = an.workload(tickets, stations, [x for x in statuses.split(",") if x], _day(date_from), _day(date_to, True))
    station = None if can.analytics_all_stations(actor) else actor.station
    per_day = an.opened_per_day(tickets, stations, 30)
    if station:
        per_day = [p for p in per_day if p["station"] == lk.station_name(station)]
        rows = [r for r in rows if r["station"] == station]
    return WorkloadOut(
        scope=lk.station_name(station) if station else "All stations",
        station=station,
        mean=round(mean, 2),
        rows=[WorkloadRow(**r) for r in rows],
        per_day=[PerDayOut(**p) for p in per_day],
        read_only=actor.role == "admin",
    )
