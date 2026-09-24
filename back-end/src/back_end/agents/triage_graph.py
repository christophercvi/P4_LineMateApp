"""The Triage graph (LangGraph).

    load_tickets -> rank -> enrich_with_docs -> check_supplies -> detect_risks -> propose_actions
                 -> summarize -+-> human_approval (loops until every proposal is decided) -> execute_decision
                               +-> END (nothing to approve)

* `rank` is a deterministic numpy score (services/analytics.rank_tickets).
* `enrich_with_docs` and `check_supplies` call the external MCP servers (shift scheduler, supplier).
* `propose_actions` asks a Pydantic AI agent to choose from rule-built candidates with a validated
  structured output; if the model fails or times out, the top candidates are used as-is.
* `human_approval` pauses with `interrupt()`; the Kitchen Manager's decisions resume the thread.
"""

import asyncio
import operator
import re
import time
from datetime import timedelta
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import BaseModel, Field
from sqlmodel import col, select

from back_end.agents import actions
from back_end.agents.common import actor_of, emit, finish_run, node_event, resolve_think, same_target, trace
from back_end.auth.permissions import can
from back_end.core.logging import get_logger
from back_end.core.timeutil import days_since, utcnow
from back_end.db import models as m
from back_end.services import analytics as an
from back_end.services import repo
from back_end.services.container import Services
from back_end.services.mcp_clients import McpCallError

log = get_logger(__name__)

TOP_N = 8
DAY_RE = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b", re.I)
SHIFT_RE = re.compile(r"\b(am|pm|lunch|dinner|brunch)\b", re.I)

TITLES = {
    "load_tickets": "Load open tickets",
    "rank": "Score priority",
    "enrich_with_docs": "Check linked SOPs & rosters",
    "check_supplies": "Check supplier stock",
    "detect_risks": "Detect risks",
    "propose_actions": "Propose actions",
    "summarize": "Write the summary",
    "human_approval": "Wait for approval",
}

SUMMARY_PROMPT = """You are LineMate, writing a shift triage note for the {scope} backlog.
Using ONLY the facts below, write 3 to 5 short Markdown bullet points: what to fix first and why,
naming ticket IDs (like TKT-017). Mention stale SOPs, ownership mismatches, stock shortfalls and
staffing gaps when they appear. End with one line on the actions waiting for the Kitchen Manager.
No preamble and no headings.

Facts:
{facts}"""

PLAN_INSTRUCTIONS = """You help a restaurant Kitchen Manager decide which actions to put forward for approval.
Choose at most 3 of the numbered candidate actions that matter most for food safety, service and cost.
Give each a one-sentence reason (under 20 words). Only use candidate numbers that exist."""


class Pick(BaseModel):
    candidate: int = Field(description="Number of the candidate action")
    reason: str = Field(description="One short sentence, under 20 words")


class ActionPlan(BaseModel):
    picks: list[Pick] = Field(default_factory=list, max_length=3)


class TriageState(TypedDict, total=False):
    run_id: str
    started: float
    actor: dict
    model: str
    thinking: str
    station: str | None
    open_ids: list[str]
    ranked: list[dict]
    docs: dict[str, dict]
    rosters: list[dict]
    stock: dict[str, dict]
    risks: list[dict]
    candidates: list[dict]
    proposal_ids: list[str]
    reused_ids: list[str]
    planner: str
    summary: str
    usage: dict
    decisions: Annotated[list[dict], operator.add]
    trace: Annotated[list[dict], operator.add]


def _done(state: TriageState, node: str, t0: float, description: str) -> None:
    node_event(node, TITLES[node], "success", description, int((time.perf_counter() - t0) * 1000))


def build_triage_graph(svc: Services):
    settings = svc.settings

    async def load_tickets(state: TriageState) -> dict:
        t0 = time.perf_counter()
        node_event("load_tickets", TITLES["load_tickets"])
        station = state.get("station")
        async with svc.db.session() as s:
            q = select(m.Ticket).where(col(m.Ticket.status).in_(an.OPEN_STATUSES))
            if station:
                q = q.where(m.Ticket.station_id == station)
            tickets = list((await s.exec(q)).all())
            lk = await repo.lookup(s)
        scope = lk.station_name(station) if station else "all stations"
        crit = sum(1 for t in tickets if t.priority == "critical")
        _done(state, "load_tickets", t0, f"{len(tickets)} open · {scope}" + (f" · {crit} critical" if crit else ""))
        return {
            "open_ids": [t.id for t in tickets],
            "trace": trace(state, "load_tickets", t0, {"station": station}, {"open": len(tickets), "critical": crit}),
        }

    async def rank(state: TriageState) -> dict:
        t0 = time.perf_counter()
        node_event("rank", TITLES["rank"])
        async with svc.db.session() as s:
            tickets = list((await s.exec(select(m.Ticket))).all())
            lk = await repo.lookup(s)
        ranked = an.rank_tickets(tickets, lk, state.get("station"))
        top = ranked[0] if ranked else None
        _done(state, "rank", t0, f"Top: {top['ticket_id']} (score {top['score']})" if top else "Nothing open")
        return {
            "ranked": ranked,
            "trace": trace(
                state,
                "rank",
                t0,
                {"tickets": len(state.get("open_ids") or [])},
                {"top": [(r["ticket_id"], r["score"]) for r in ranked[:5]]},
            ),
        }

    async def enrich_with_docs(state: TriageState) -> dict:
        t0 = time.perf_counter()
        node_event("enrich_with_docs", TITLES["enrich_with_docs"])
        top_ids = [r["ticket_id"] for r in (state.get("ranked") or [])[:TOP_N]]
        async with svc.db.session() as s:
            tickets = {t.id: t for t in (await s.exec(select(m.Ticket).where(col(m.Ticket.id).in_(top_ids)))).all()} if top_ids else {}
            lk = await repo.lookup(s)
        docs: dict[str, dict] = {}
        staffing: list[tuple[str, str, str, str]] = []
        for tid in top_ids:
            t = tickets[tid]
            d = lk.docs.get(t.related_doc_id) if t.related_doc_id else None
            if d is not None:
                docs[tid] = {
                    "docId": d.id,
                    "title": d.title,
                    "station": d.station_id,
                    "daysSinceReview": days_since(d.last_reviewed),
                    "stale": an.is_stale(d, settings.stale_days),
                }
            if "staffing" in (t.tags or []):
                text = f"{t.title} {t.description}"
                day = (DAY_RE.search(text).group(1) if DAY_RE.search(text) else "fri").lower()
                sh = SHIFT_RE.search(text)
                shift = "AM" if sh and sh.group(1).lower() in ("am", "lunch", "brunch") else "PM"
                staffing.append((tid, t.station_id, day, shift))

        async def roster(tid: str, station: str, day: str, shift: str) -> dict | None:
            try:
                r = await svc.mcp.call("shift-scheduler", "get_roster", {"station": station, "day": day, "shift": shift})
                cover = (
                    await svc.mcp.call("shift-scheduler", "find_cover", {"station": station, "day": day, "shift": shift})
                    if r.get("short_by", 0) > 0
                    else {"options": []}
                )
                return {"ticketId": tid, **r, "cover": cover.get("options", [])}
            except McpCallError as exc:
                log.warning("roster_unavailable", error=str(exc))
                return None

        rosters = [r for r in await asyncio.gather(*(roster(*x) for x in staffing[:2])) if r]
        stale = sum(1 for d in docs.values() if d["stale"])
        desc = f"{len(docs)} linked SOP{'s' if len(docs) != 1 else ''} · {stale} stale"
        if rosters:
            desc += " · roster via MCP"
        _done(state, "enrich_with_docs", t0, desc)
        return {
            "docs": docs,
            "rosters": rosters,
            "trace": trace(
                state,
                "enrich_with_docs",
                t0,
                {"tickets": top_ids},
                {
                    "linked_docs": {k: v["docId"] for k, v in docs.items()},
                    "stale": stale,
                    "rosters": [(r["ticketId"], r["short_by"]) for r in rosters],
                },
            ),
        }

    async def check_supplies(state: TriageState) -> dict:
        t0 = time.perf_counter()
        node_event("check_supplies", TITLES["check_supplies"])
        open_ids = state.get("open_ids") or []
        async with svc.db.session() as s:
            tickets = list(await s.exec(select(m.Ticket).where(col(m.Ticket.id).in_(open_ids)))) if open_ids else []
        supply = [t for t in tickets if "supply" in (t.tags or [])][:4]

        async def check(t: m.Ticket) -> tuple[str, dict | None]:
            try:
                r = await svc.mcp.call("supplier-inventory", "check_stock", {"query": f"{t.title} {t.description}"})
                return t.id, (r if r.get("found") else None)
            except McpCallError as exc:
                log.warning("stock_unavailable", error=str(exc))
                return t.id, None

        stock = {tid: r for tid, r in await asyncio.gather(*(check(t) for t in supply)) if r}
        low = [r for r in stock.values() if r.get("below_par")]
        desc = (
            f"{len(low)} of {len(stock)} item{'s' if len(stock) != 1 else ''} below par · {low[0]['supplier']} (MCP)"
            if stock
            else "No supply tickets"
        )
        _done(state, "check_supplies", t0, desc)
        return {
            "stock": stock,
            "trace": trace(
                state,
                "check_supplies",
                t0,
                {"supply_tickets": [t.id for t in supply]},
                {"stock": {k: f"{v['on_hand']}/{v['par']} {v['unit']}" for k, v in stock.items()}},
            ),
        }

    async def detect_risks(state: TriageState) -> dict:
        t0 = time.perf_counter()
        node_event("detect_risks", TITLES["detect_risks"])
        async with svc.db.session() as s:
            tickets = list((await s.exec(select(m.Ticket))).all())
            lk = await repo.lookup(s)
        risks: list[dict] = []
        flagged: set[str] = set()
        for r in an.mismatches(tickets, lk, state.get("station")):
            kind = "combined" if r["doc_stale"] else "ownership_mismatch"
            msg = (
                f"Assigned to {lk.station_name(r['assignee_station'])} but {r['doc_id']} is owned by "
                f"{lk.station_name(r['doc_station'])}" + (" and is overdue for review" if r["doc_stale"] else "")
            )
            risks.append({"ticketId": r["ticket_id"], "kind": kind, "message": msg})
            flagged.add(r["ticket_id"])
        for tid, d in (state.get("docs") or {}).items():
            if d["stale"] and tid not in flagged:
                risks.append(
                    {
                        "ticketId": tid,
                        "kind": "stale_sop",
                        "message": f"{d['docId']} {d['title']} last reviewed {d['daysSinceReview']} days ago",
                    }
                )
        for tid, st in (state.get("stock") or {}).items():
            if st.get("below_par"):
                risks.append(
                    {
                        "ticketId": tid,
                        "kind": "shortage",
                        "message": f"{st['item']}: {st['on_hand']} of {st['par']} {st['unit']} on hand, {st['lead_days']}-day lead time",
                    }
                )
        for ro in state.get("rosters") or []:
            if ro.get("short_by", 0) > 0:
                names = ", ".join(c["name"] for c in ro.get("cover", [])[:2])
                risks.append(
                    {
                        "ticketId": ro["ticketId"],
                        "kind": "staffing",
                        "message": f"{ro['day'].capitalize()} {ro['shift']} short by {ro['short_by']}"
                        + (f"; cover options: {names}" if names else ""),
                    }
                )
        counts: dict[str, int] = {}
        for r in risks:
            counts[r["kind"]] = counts.get(r["kind"], 0) + 1
        desc = " · ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items()) or "No risks found"
        _done(state, "detect_risks", t0, desc)
        return {"risks": risks, "trace": trace(state, "detect_risks", t0, {}, {"risks": counts})}

    async def propose_actions(state: TriageState) -> dict:
        t0 = time.perf_counter()
        node_event("propose_actions", TITLES["propose_actions"])
        actor = actor_of(state)
        candidates = await _candidates(svc, state)
        planner = "rules"
        chosen = candidates[:3]
        if len(candidates) > 1:
            plan = await _plan_with_agent(svc, state["model"], state.get("thinking", "none"), candidates)
            if plan is not None:
                picked = []
                for p in plan.picks:
                    if 1 <= p.candidate <= len(candidates) and candidates[p.candidate - 1] not in picked:
                        c = dict(candidates[p.candidate - 1])
                        c["reason"] = p.reason.strip() or c["reason"]
                        picked.append(c)
                if picked:
                    chosen, planner = picked, "pydantic-ai"
        new_ids: list[str] = []
        reused: list[str] = []
        if can.request_approval(actor):
            async with svc.db.session() as s:
                pending = list((await s.exec(select(m.Approval).where(m.Approval.status == "pending"))).all())
                for c in chosen:
                    same = next((a for a in pending if a.kind == c["kind"] and same_target(a.payload or {}, c["payload"])), None)
                    if same is not None:
                        reused.append(same.id)
                        continue
                    a = await actions.create_approval(
                        s,
                        kind=c["kind"],
                        title=c["title"],
                        summary=f"{c['summary']} {c['reason']}".strip(),
                        payload=c["payload"],
                        requested_by=actor.who,
                        run_id=state["run_id"],
                        thread_id=state["run_id"],
                        source="agent",
                    )
                    await s.flush()
                    new_ids.append(a.id)
                    pending.append(a)
        desc = f"{len(new_ids)} new · {len(reused)} already waiting" if (new_ids or reused) else "Nothing to approve"
        desc += f" · {'Pydantic AI' if planner == 'pydantic-ai' else 'rules'}"
        _done(state, "propose_actions", t0, desc)
        return {
            "candidates": chosen,
            "proposal_ids": new_ids,
            "reused_ids": reused,
            "planner": planner,
            "trace": trace(
                state, "propose_actions", t0, {"candidates": len(candidates)}, {"planner": planner, "approvals": new_ids, "reused": reused}
            ),
        }

    async def summarize(state: TriageState) -> dict:
        t0 = time.perf_counter()
        node_event("summarize", TITLES["summarize"])
        facts = await _facts(svc, state)
        scope = "whole kitchen"
        if state.get("station"):
            async with svc.db.session() as s:
                scope = (await repo.lookup(s)).station_name(state["station"]) + " station"
        usage: dict = {}
        text: list[str] = []
        try:
            chat = svc.chat(state["model"], reasoning=resolve_think(state.get("thinking", "none"), False), temperature=0.2, num_predict=450)
            await svc.ollama.ensure_loaded(state["model"])
            async for chunk in chat.astream(SUMMARY_PROMPT.format(scope=scope, facts=facts)):
                r = (chunk.additional_kwargs or {}).get("reasoning_content")
                if r:
                    emit("reasoning", r)
                if chunk.content:
                    piece = chunk.content if isinstance(chunk.content, str) else ""
                    text.append(piece)
                    emit("content", piece)
                if chunk.usage_metadata:
                    usage = dict(chunk.usage_metadata)
            summary = "".join(text).strip()
            desc = f"{usage.get('output_tokens', 0)} tokens"
        except Exception as exc:  # noqa: BLE001 - the ranked list is still useful without the model
            log.warning("triage_summary_failed", error=str(exc))
            summary = _fallback_summary(state)
            emit("content", summary)
            desc = "Model unavailable · rule-based summary"
        _done(state, "summarize", t0, desc)
        result = {
            "runId": state["run_id"],
            "summary": summary,
            "ranked": [
                {"ticketId": r["ticket_id"], "score": r["score"], "reasons": r["reasons"]} for r in (state.get("ranked") or [])[:TOP_N]
            ],
            "risks": state.get("risks") or [],
            "proposedApprovalIds": [*(state.get("proposal_ids") or []), *(state.get("reused_ids") or [])],
        }
        emit("result", result)
        if state.get("proposal_ids") or state.get("reused_ids"):
            async with svc.db.session() as s:
                for aid in [*(state.get("proposal_ids") or []), *(state.get("reused_ids") or [])]:
                    a = await s.get(m.Approval, aid)
                    if a is not None:
                        emit("interrupt", actions.interrupt_payload(a, actor_of(state)))
        return {
            "summary": summary,
            "usage": usage,
            "trace": trace(
                state, "summarize", t0, {"model": state["model"]}, {"tokens_out": usage.get("output_tokens", 0), "chars": len(summary)}
            ),
        }

    def after_summary(state: TriageState) -> str:
        return "human_approval" if state.get("proposal_ids") else END

    async def human_approval(state: TriageState) -> dict:
        decided = {d.get("approvalId") for d in state.get("decisions") or []}
        waiting = [a for a in state.get("proposal_ids") or [] if a not in decided]
        decision = interrupt({"pending": waiting})
        return {"decisions": [decision]}

    def after_human(state: TriageState) -> str:
        decided = {d.get("approvalId") for d in state.get("decisions") or []}
        waiting = [a for a in state.get("proposal_ids") or [] if a not in decided]
        return "human_approval" if waiting else "execute_decision"

    async def execute_decision(state: TriageState) -> dict:
        t0 = time.perf_counter()
        decisions = state.get("decisions") or []
        entries = [
            {
                "node": "human_approval",
                "started_ms": int((time.time() - state["started"]) * 1000),
                "duration_ms": 0,
                "status": "success",
                "input": {"approvals": state.get("proposal_ids")},
                "output": {d.get("approvalId"): d.get("status") for d in decisions},
            }
        ]
        entries += trace(state, "execute_decision", t0, {}, {"results": [d.get("result") for d in decisions]})
        await finish_run(svc, state["run_id"], entries)
        return {"trace": entries}

    g = StateGraph(TriageState)
    for name, fn in [
        ("load_tickets", load_tickets),
        ("rank", rank),
        ("enrich_with_docs", enrich_with_docs),
        ("check_supplies", check_supplies),
        ("detect_risks", detect_risks),
        ("propose_actions", propose_actions),
        ("summarize", summarize),
        ("human_approval", human_approval),
        ("execute_decision", execute_decision),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "load_tickets")
    g.add_edge("load_tickets", "rank")
    g.add_edge("rank", "enrich_with_docs")
    g.add_edge("enrich_with_docs", "check_supplies")
    g.add_edge("check_supplies", "detect_risks")
    g.add_edge("detect_risks", "propose_actions")
    g.add_edge("propose_actions", "summarize")
    g.add_conditional_edges("summarize", after_summary, ["human_approval", END])
    g.add_conditional_edges("human_approval", after_human, ["human_approval", "execute_decision"])
    g.add_edge("execute_decision", END)
    return g.compile(checkpointer=svc.checkpointer)


# ---------------------------------------------------------------- candidates and planning


async def _candidates(svc: Services, state: TriageState) -> list[dict]:
    """Rule-built actions, ordered by the ticket's triage score."""
    scores = {r["ticket_id"]: r["score"] for r in state.get("ranked") or []}
    out: list[dict] = []
    async with svc.db.session() as s:
        tickets = {t.id: t for t in (await s.exec(select(m.Ticket))).all()}
        lk = await repo.lookup(s)
        who = await repo.names(s)
        km = (await s.exec(select(m.User).where(m.User.role == "kitchen_manager"))).first()
    for tid, st in (state.get("stock") or {}).items():
        if not st.get("below_par"):
            continue
        qty = max(1, st["par"] - st["on_hand"] + max(1, st["par"] // 2))
        try:
            q = await svc.mcp.call("supplier-inventory", "price_quote", {"sku": st["sku"], "quantity": qty})
        except McpCallError:
            q = {
                **st,
                "quantity": qty,
                "total": round(st["unit_price"] * qty, 2),
                "delivery_date": (utcnow() + timedelta(days=st["lead_days"])).date().isoformat(),
            }
        out.append(
            {
                "kind": "draft_supply_order",
                "ticketId": tid,
                "score": scores.get(tid, 0),
                "title": f"Order {q['quantity']} × {q['item']}",
                "summary": (
                    f"{st['on_hand']} {st['unit']} on hand against a par of {st['par']}. "
                    f"{q['quantity']} at ${q['unit_price']:.2f} = ${q['total']:,.2f}, delivered {q.get('delivery_date')}."
                ),
                "reason": "Stock is below par.",
                "payload": {
                    "supplier": q["supplier"],
                    "item": q["item"],
                    "sku": q["sku"],
                    "quantity": q["quantity"],
                    "unit": q["unit"],
                    "unitPrice": q["unit_price"],
                    "neededBy": q.get("delivery_date"),
                    "ticketId": tid,
                },
            }
        )
    for r in state.get("risks") or []:
        t = tickets.get(r["ticketId"])
        if t is None:
            continue
        if r["kind"] in ("ownership_mismatch", "combined"):
            mm = an.mismatches([t], lk)
            to = mm[0]["suggested_assignee_id"] if mm else None
            if to and to != t.assignee_id:
                out.append(
                    {
                        "kind": "reassign_ticket",
                        "ticketId": t.id,
                        "score": scores.get(t.id, 0),
                        "title": f"Reassign {t.id} to {who.get(to, to)}",
                        "summary": r["message"] + ".",
                        "reason": "The owning station should handle it.",
                        "payload": {
                            "ticketId": t.id,
                            "fromAssigneeId": t.assignee_id,
                            "toAssigneeId": to,
                            "reason": "Linked document is owned by another station",
                        },
                    }
                )
        if r["kind"] in ("combined", "stale_sop") and t.priority in ("critical", "high") and "food-safety" in (t.tags or []):
            notify = [x for x in [km.crew_member_id if km else None, lk.station_lead(t.station_id)] if x]
            out.append(
                {
                    "kind": "escalate_incident",
                    "ticketId": t.id,
                    "score": scores.get(t.id, 0) + 1,
                    "title": f"Escalate {t.id}: {t.title[:60]}",
                    "summary": f"Food-safety ticket relies on an out-of-date SOP ({r['message']}).",
                    "reason": "Food safety with a stale SOP.",
                    "payload": {
                        "ticketId": t.id,
                        "severity": "food_safety",
                        "notify": notify,
                        "holdProduct": True,
                        "note": "Raised by triage: linked SOP overdue for review.",
                    },
                }
            )
    out.sort(key=lambda c: -c["score"])
    return out


async def _plan_with_agent(svc: Services, model: str, thinking: str, candidates: list[dict]) -> ActionPlan | None:
    try:
        from pydantic_ai import Agent
        from pydantic_ai.models.ollama import OllamaModel
        from pydantic_ai.providers.ollama import OllamaProvider

        settings: dict[str, Any] = {"temperature": 0.0, "max_tokens": 400, "timeout": 120}
        if svc.planner_model is not None:  # tests inject a Pydantic AI TestModel / FunctionModel
            llm: Any = svc.planner_model
        else:
            info = await svc.ollama.get(model)
            if info is None or "tools" not in info.capabilities:
                return None
            if thinking == "toggle":
                settings["extra_body"] = {"reasoning_effort": "none"}
            llm = OllamaModel(model, provider=OllamaProvider(base_url=f"{svc.settings.ollama_base_url.rstrip('/')}/v1"))
        agent = Agent(
            llm,
            output_type=ActionPlan,
            instructions=PLAN_INSTRUCTIONS,
            retries=1,
            model_settings=settings,
        )
        listing = "\n".join(f"{i}. {c['title']} (ticket {c['ticketId']}): {c['summary']}" for i, c in enumerate(candidates, 1))
        res = await asyncio.wait_for(agent.run(f"Candidate actions:\n{listing}"), timeout=150)
        return res.output
    except Exception as exc:  # noqa: BLE001 - planning is best-effort; rules are the fallback
        log.warning("triage_planner_fallback", model=model, error=str(exc)[:300])
        return None


async def _facts(svc: Services, state: TriageState) -> str:
    async with svc.db.session() as s:
        ids = [r["ticket_id"] for r in (state.get("ranked") or [])[:6]]
        tickets = {t.id: t for t in (await s.exec(select(m.Ticket).where(col(m.Ticket.id).in_(ids)))).all()} if ids else {}
    lines = []
    for r in (state.get("ranked") or [])[:6]:
        t = tickets.get(r["ticket_id"])
        if t:
            lines.append(f"- {t.id} [{t.priority}, {t.status}] {t.title} (score {r['score']}: {', '.join(r['reasons'])})")
    for r in state.get("risks") or []:
        lines.append(f"- Risk {r['kind'].replace('_', ' ')} on {r['ticketId']}: {r['message']}")
    for c in state.get("candidates") or []:
        lines.append(f"- Awaiting approval: {c['title']}")
    return "\n".join(lines) or "- No open tickets."


def _fallback_summary(state: TriageState) -> str:
    ranked = (state.get("ranked") or [])[:3]
    parts = [f"- **{r['ticket_id']}** first: {', '.join(r['reasons'])}." for r in ranked]
    for r in (state.get("risks") or [])[:3]:
        parts.append(f"- {r['ticketId']}: {r['message']}.")
    if state.get("proposal_ids"):
        parts.append(f"- {len(state['proposal_ids'])} action(s) are waiting for the Kitchen Manager.")
    return "\n".join(parts) or "- No open tickets in scope."
