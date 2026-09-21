"""Read-side analytics: stale-document audit, ownership mismatches, workload
balance and the triage ranking used by the dashboard and the triage graph.

Functions take plain lists of table rows so they are easy to unit-test without a database.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from back_end.core.timeutil import as_utc, days_since, utcnow
from back_end.db.models import CrewMember, Document, Station, Ticket

OPEN_STATUSES = ("open", "in_progress", "blocked")
PRIORITIES = ("critical", "high", "medium", "low")
PRIORITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}
RANK_BASE = {"critical": 100.0, "high": 60.0, "medium": 30.0, "low": 10.0}
STALE_DEFAULT = 180


def is_open(t: Ticket) -> bool:
    return t.status in OPEN_STATUSES


def is_stale(d: Document, threshold: int = STALE_DEFAULT, at: datetime | None = None) -> bool:
    """Incident reports are historical records, so they never go stale."""
    return d.category != "incident" and d.status != "archived" and days_since(d.last_reviewed, at) > threshold


@dataclass(slots=True)
class Lookup:
    crew: dict[str, CrewMember]
    docs: dict[str, Document]
    stations: list[Station]

    @classmethod
    def build(cls, crew: Iterable[CrewMember], docs: Iterable[Document], stations: Iterable[Station]):
        return cls({c.id: c for c in crew}, {d.id: d for d in docs}, list(stations))

    def station_name(self, sid: str | None) -> str:
        return next((s.name for s in self.stations if s.id == sid), sid or "—")

    def station_lead(self, station: str) -> str | None:
        crew = list(self.crew.values())
        sous = next((c for c in crew if c.station_id == station and c.title.startswith("Sous Chef")), None)
        lead = next((c for c in crew if c.station_id == station and "Lead" in c.title), None)
        pick = sous or lead
        return pick.id if pick else None


def stale_rows(
    docs: Sequence[Document],
    tickets: Sequence[Ticket],
    citations: dict[str, int],
    threshold: int = STALE_DEFAULT,
    station: str | None = None,
) -> list[dict]:
    rows = []
    for d in docs:
        if not is_stale(d, threshold) or (station and d.station_id != station):
            continue
        rows.append(
            {
                "doc_id": d.id,
                "title": d.title,
                "category": d.category,
                "station": d.station_id,
                "owner_id": d.owner_id,
                "last_reviewed": d.last_reviewed,
                "days_since_review": days_since(d.last_reviewed),
                "open_tickets": sum(1 for t in tickets if t.related_doc_id == d.id and is_open(t)),
                "cited_last7d": citations.get(d.id, 0),
            }
        )
    return sorted(rows, key=lambda r: r["days_since_review"], reverse=True)


def mismatches(tickets: Sequence[Ticket], lk: Lookup, station: str | None = None) -> list[dict]:
    rows = []
    for t in tickets:
        if not is_open(t) or not t.related_doc_id:
            continue
        doc = lk.docs.get(t.related_doc_id)
        assignee = lk.crew.get(t.assignee_id) if t.assignee_id else None
        if not doc or not assignee or assignee.station_id is None:
            continue
        if assignee.station_id == doc.station_id:
            continue
        if station and doc.station_id != station and assignee.station_id != station:
            continue
        rows.append(
            {
                "ticket_id": t.id,
                "title": t.title,
                "priority": t.priority,
                "status": t.status,
                "assignee_id": t.assignee_id,
                "assignee_station": assignee.station_id,
                "doc_id": doc.id,
                "doc_title": doc.title,
                "doc_station": doc.station_id,
                "doc_stale": is_stale(doc),
                "suggested_assignee_id": lk.station_lead(doc.station_id),
            }
        )
    return sorted(rows, key=lambda r: PRIORITY_WEIGHT[r["priority"]], reverse=True)


def mismatch_flows(rows: Sequence[dict], lk: Lookup) -> list[dict]:
    """Sankey links: owning station -> assigned station (names, so the chart labels read well)."""
    counts: dict[tuple[str, str], int] = {}
    for r in rows:
        key = (f"{lk.station_name(r['doc_station'])} (owner)", f"{lk.station_name(r['assignee_station'])} (assigned)")
        counts[key] = counts.get(key, 0) + 1
    return [{"source": s, "target": t, "value": v} for (s, t), v in counts.items()]


def workload(
    tickets: Sequence[Ticket],
    stations: Sequence[Station],
    statuses: Sequence[str] | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> tuple[list[dict], float]:
    statuses = list(statuses) if statuses else list(OPEN_STATUSES)
    frame = pd.DataFrame(
        [{"station": t.station_id, "priority": t.priority, "status": t.status, "created": as_utc(t.created_at)} for t in tickets],
        columns=["station", "priority", "status", "created"],
    )
    if not frame.empty:
        mask = frame["status"].isin(statuses)
        if date_from is not None:
            mask &= frame["created"] >= as_utc(date_from)
        if date_to is not None:
            mask &= frame["created"] <= as_utc(date_to)
        frame = frame[mask]
    total = len(frame) or 1
    mean = (len(frame) / len(stations)) if stations and len(frame) else 1.0
    counts = (
        frame.pivot_table(index="station", columns="priority", values="status", aggfunc="count", fill_value=0)
        if not frame.empty
        else pd.DataFrame()
    )
    rows = []
    for s in stations:
        per = {p: int(counts.at[s.id, p]) if (s.id in counts.index and p in counts.columns) else 0 for p in PRIORITIES}
        open_count = sum(per.values())
        load_index = round(open_count / mean, 2)
        rows.append(
            {
                "station": s.id,
                "open": open_count,
                **per,
                "weighted": sum(PRIORITY_WEIGHT[p] * n for p, n in per.items()),
                "share": round(open_count / total * 100, 1),
                "load_index": load_index,
                "flagged": load_index > 1.5,
            }
        )
    return rows, round(len(frame) / len(stations), 2) if stations else 0.0


def opened_per_day(tickets: Sequence[Ticket], stations: Sequence[Station], days: int = 30) -> list[dict]:
    today = utcnow().astimezone().date()
    window = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    local_dates = pd.Series([as_utc(t.created_at).astimezone().date() for t in tickets], dtype="object")
    station_ids = pd.Series([t.station_id for t in tickets], dtype="object")
    frame = pd.DataFrame({"date": local_dates, "station": station_ids})
    grouped = frame.groupby(["date", "station"]).size() if not frame.empty else pd.Series(dtype=int)
    out = []
    for day in window:
        for s in stations:
            out.append({"date": day, "station": s.name, "count": int(grouped.get((day, s.id), 0))})
    return out


def rank_tickets(tickets: Sequence[Ticket], lk: Lookup, station: str | None = None) -> list[dict]:
    """Deterministic urgency score (numpy, vectorised). The triage graph adds an LLM summary on top."""
    scope = [t for t in tickets if is_open(t) and (not station or t.station_id == station)]
    if not scope:
        return []
    mm = {m["ticket_id"] for m in mismatches(tickets, lk)}
    base = np.array([RANK_BASE[t.priority] for t in scope])
    stale = np.array([bool(t.related_doc_id and t.related_doc_id in lk.docs and is_stale(lk.docs[t.related_doc_id])) for t in scope])
    outside = np.array([t.id in mm for t in scope])
    blocked = np.array([t.status == "blocked" for t in scope])
    food = np.array(["food-safety" in (t.tags or []) for t in scope])
    supply = np.array(["supply" in (t.tags or []) for t in scope])
    age = np.array([days_since(t.created_at) for t in scope], dtype=float)
    score = base + 8 * stale + 6 * outside + 3 * blocked + 5 * food + 2 * supply + np.minimum(age, 14) * 0.5
    ranked = []
    for i, t in enumerate(scope):
        reasons = [f"{t.priority.capitalize()} priority"]
        if stale[i]:
            reasons.append(f"linked SOP stale ({t.related_doc_id})")
        if outside[i]:
            reasons.append("assigned outside the owning station")
        if blocked[i]:
            reasons.append("blocked")
        if food[i]:
            reasons.append("food safety")
        if supply[i]:
            reasons.append("stock below par")
        if age[i] >= 7:
            reasons.append(f"open {int(age[i])} days")
        ranked.append({"ticket_id": t.id, "score": round(float(score[i]), 1), "reasons": reasons})
    return sorted(ranked, key=lambda r: r["score"], reverse=True)


def weekly_trend(tickets: Sequence[Ticket], *, weeks: int = 12, closed: bool = False) -> list[int]:
    """Tickets opened (or resolved/closed) per week, oldest first."""
    now = utcnow()
    out = []
    for w in range(weeks - 1, -1, -1):
        start, end = now - timedelta(days=7 * (w + 1)), now - timedelta(days=7 * w)
        if closed:
            n = sum(1 for t in tickets if t.status in ("resolved", "closed") and start < as_utc(t.updated_at) <= end)
        else:
            n = sum(1 for t in tickets if start < as_utc(t.created_at) <= end)
        out.append(n)
    return out
