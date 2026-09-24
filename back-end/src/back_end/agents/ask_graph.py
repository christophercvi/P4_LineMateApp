"""The Ask graph (LangGraph).

    classify_intent -> retrieve -> grade_documents -+-> load_model -> generate -+-> check_citations -> propose_action
                                                    +-> no_answer --------------+
    propose_action -+-> human_approval -> execute_action
                    +-> END

Every node streams UI events through LangGraph's custom stream writer as (event, data) tuples;
`agents/runner.py` turns them into Server-Sent Events. Consequential actions (supply orders,
escalations, reassignments) pause the graph with `interrupt()` until the Kitchen Manager decides.
"""

import asyncio
import base64
import io
import operator
import re
import time
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableLambda
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlmodel import col, select

from back_end.agents import actions
from back_end.agents.common import actor_of, emit, finish_run, same_target, step, trace
from back_end.auth.permissions import can
from back_end.core.logging import get_logger
from back_end.core.timeutil import days_since
from back_end.db import models as m
from back_end.services import analytics as an
from back_end.services import ingestion as ing
from back_end.services import repo
from back_end.services.container import Services
from back_end.services.mcp_clients import McpCallError
from back_end.services.vectorstore import role_filter

log = get_logger(__name__)

TICKET_RE = re.compile(r"\bTKT-\d{3,}\b", re.I)
CITE_RE = re.compile(r"\[(\d{1,2})\]")
SUPPLY_WORDS = (
    "running low",
    "run out",
    "running out",
    "out of",
    "low on",
    "down to",
    "reorder",
    "re-order",
    "order more",
    "need more",
    "short on",
    "below par",
    "stock",
)
ESCALATE_WORDS = ("escalate", "escalation", "report this", "hold the product", "notify the manager")
REASSIGN_WORDS = ("reassign", "re-assign", "wrong station", "who should", "assign it", "owner", "mismatch")
OPEN = an.OPEN_STATUSES

SYSTEM_PROMPT = """You are LineMate, the assistant for the Hearthline restaurant kitchen crew.
Answer using ONLY the numbered kitchen documents and records in the context.
- Cite every fact with its source number in square brackets, like [1] or [2], at the end of the sentence.
  Example: "Sanitize boards every 4 hours [1]." Never cite a number that is not listed as a valid citation.
- Lead with the direct answer, then the steps or numbers as a short list. Keep it practical for a busy cook.
- Quote temperatures, times, quantities and part numbers exactly as written.
- If a document is marked STALE, say it may be out of date and should be confirmed.
- If the context does not answer the question, say so plainly and suggest raising a ticket. Never invent procedures.
- Answer in Markdown. No preamble."""


class AskState(TypedDict, total=False):
    run_id: str
    started: float
    actor: dict
    question: str
    model: str
    reasoning: bool
    think: bool | None
    vision: bool
    strategy: str
    conversation_id: str
    history: list[dict]
    summary: str
    attachments: list[dict]
    ticket_id: str | None
    intent: str
    flags: list[str]
    query: str
    hits: list[dict]
    hidden: int
    extra_context: list[str]
    images: list[str]
    stock: dict | None
    sources: list[dict]
    context: str
    answer: str
    reasoning_ms: int
    usage: dict
    approval_id: str | None
    decision: dict | None
    trace: Annotated[list[dict], operator.add]


# ---------------------------------------------------------------- helpers


def _has(text: str, words: tuple[str, ...]) -> bool:
    t = text.lower()
    return any(w in t for w in words)


def _image_b64(path, max_side: int = 1024) -> str:
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------- graph


def build_ask_graph(svc: Services):
    settings = svc.settings

    async def classify_intent(state: AskState) -> dict:
        t0 = time.perf_counter()
        step("classify", "Understand the question")
        q = state["question"]
        attachments = state.get("attachments") or []
        ticket_ids = {x.upper() for x in TICKET_RE.findall(q)}
        if state.get("ticket_id"):
            ticket_ids.add(state["ticket_id"].upper())
        if any(a.get("kind") in ("png", "jpg") for a in attachments):
            intent = "photo"
        elif ticket_ids:
            intent = "ticket"
        elif _has(q, SUPPLY_WORDS):
            intent = "supply"
        else:
            intent = "procedure"
        flags = []
        if _has(q, ESCALATE_WORDS):
            flags.append("escalate")
        if _has(q, REASSIGN_WORDS):
            flags.append("reassign")
        if intent != "supply" and _has(q, SUPPLY_WORDS):
            flags.append("supply")
        history = state.get("history") or []
        prior_user = [h["content"] for h in history if h["role"] == "user"]
        query = q
        if prior_user and len(q.split()) <= 10:
            query = f"{prior_user[-1]} {q}"  # short follow-ups lean on the previous question for retrieval
        turns = len(prior_user)
        desc = f"Follow-up · using {turns} earlier turn{'s' if turns != 1 else ''}" if turns else "New question"
        desc += f" · {intent}"
        ticket_id = sorted(ticket_ids)[0] if ticket_ids else None
        step("classify", "Understand the question", "success", desc)
        return {
            "intent": intent,
            "flags": flags,
            "query": query,
            "ticket_id": ticket_id,
            "trace": trace(
                state,
                "classify_intent",
                t0,
                {"question": q[:200]},
                {"intent": intent, "flags": flags, "memory_turns": turns, "ticket": ticket_id},
            ),
        }

    async def retrieve(state: AskState) -> dict:
        t0 = time.perf_counter()
        actor = actor_of(state)
        intent = state["intent"]
        title = "Look at the photo & search" if intent == "photo" else "Search the knowledge base"
        step("retrieve", title)
        query = state["query"]
        extra: list[str] = []
        images: list[str] = []
        photo_hits: list[dict] = []
        stock: dict | None = None
        cache_dir = settings.parse_cache_dir or (settings.storage_dir / ".cache" / "parsed")

        # Attachments: photos are read (OCR), compared with photos on file, and shown to vision models.
        for att in state.get("attachments") or []:
            path = att.get("path")
            if not path:
                continue
            if att.get("kind") in ("png", "jpg"):
                try:
                    parsed = await asyncio.to_thread(ing.parse_cached, Path(path), att["kind"], att["name"], cache_dir)
                    ocr = re.sub(r"\s+", " ", parsed.markdown).strip()
                except Exception as exc:  # noqa: BLE001
                    log.warning("ocr_failed", name=att["name"], error=str(exc))
                    ocr = ""
                if ocr:
                    extra.append(f"Text read from the attached photo {att['name']}: {ocr[:600]}")
                    query = f"{query} {ocr[:300]}"
                if svc.vision.enabled:
                    try:
                        vec = (await svc.vision.aembed_images([Path(path)]))[0]
                        for h in await svc.vectors.search_photos_by_vector(vec, k=3):
                            if h.metadata.get("asset_id") == att.get("assetId"):
                                continue
                            photo_hits.append({"caption": h.text, "score": round(h.score, 3), **h.metadata})
                    except Exception as exc:  # noqa: BLE001
                        log.warning("photo_search_failed", error=str(exc))
                if state.get("vision"):
                    images.append(await asyncio.to_thread(_image_b64, Path(path)))
            else:
                try:
                    parsed = await asyncio.to_thread(ing.parse_cached, Path(path), att["kind"], att["name"], cache_dir)
                    extra.append(f"Attached file {att['name']}:\n{parsed.markdown[:5000]}")
                except Exception as exc:  # noqa: BLE001
                    extra.append(f"Attached file {att['name']} could not be read ({exc}).")
        if photo_hits:
            best = photo_hits[0]
            if best["score"] >= 0.7:
                query = f"{query} {best['caption']}"
            extra.append(
                "Similar photos already on file: " + "; ".join(f"{p['caption']} (similarity {p['score']:.2f})" for p in photo_hits[:2])
            )

        # Ticket questions: bring in the ticket, its thread, and the ownership check.
        if state.get("ticket_id"):
            async with svc.db.session() as s:
                t = await s.get(m.Ticket, state["ticket_id"])
                if t is not None:
                    lk = await repo.lookup(s)
                    who = await repo.names(s)
                    comments = list(
                        (
                            await s.exec(
                                select(m.TicketComment).where(m.TicketComment.ticket_id == t.id).order_by(col(m.TicketComment.created_at))
                            )
                        ).all()
                    )[-6:]
                    mm = an.mismatches([t], lk)
                    lines = [
                        f"Ticket {t.id}: {t.title}",
                        f"Status {t.status.replace('_', ' ')}, priority {t.priority}, station {lk.station_name(t.station_id)}, "
                        f"assigned to {who.get(t.assignee_id or '', 'nobody')}, opened {days_since(t.created_at)} days ago.",
                        f"Description: {t.description}",
                    ]
                    if t.related_doc_id:
                        lines.append(f"Linked document: {t.related_doc_id}.")
                    if mm:
                        r = mm[0]
                        lines.append(
                            f"Ownership check: assigned to {lk.station_name(r['assignee_station'])} but the linked document "
                            f"is owned by {lk.station_name(r['doc_station'])}. Suggested assignee: "
                            f"{who.get(r.get('suggested_assignee_id') or '', 'the station lead')}."
                        )
                    for c in comments:
                        lines.append(f"Comment by {who.get(c.author_id, c.author_id)}: {c.body}")
                    extra.insert(0, "\n".join(lines))
                    query = f"{t.title} {state['question']}"

        # Supply questions: ask the supplier's MCP server for live stock.
        if state["intent"] == "supply" or "supply" in (state.get("flags") or []):
            try:
                stock = await svc.mcp.call("supplier-inventory", "check_stock", {"query": state["question"]})
                if stock.get("found"):
                    extra.append(
                        f"Supplier stock ({stock['supplier']}, via MCP): {stock['item']} [{stock['sku']}]: "
                        f"{stock['on_hand']} {stock['unit']} on hand, par {stock['par']}, "
                        f"${stock['unit_price']:.2f} per {stock['unit']}, {stock['lead_days']}-day lead time."
                    )
                else:
                    stock = None
            except McpCallError as exc:
                extra.append(f"Supplier stock check unavailable: {exc}")

        where = role_filter(can.see_incident_reports(actor))
        strategy = state.get("strategy") or settings.retrieval_strategy
        hits = await svc.vectors.search(
            query, k=settings.retrieval_k + 2, strategy=strategy, where=where, threshold=settings.score_threshold
        )
        hidden = 0
        if not can.see_incident_reports(actor):
            inc = await svc.vectors.search(query, k=3, strategy="similarity", where={"category": "incident"})
            hidden = len({h.metadata.get("doc_id") for h in inc if h.score >= max(settings.score_threshold, 0.5)})
        desc = f"{len(hits)} chunk{'s' if len(hits) != 1 else ''} · {strategy}"
        if photo_hits:
            desc += f" · {len(photo_hits)} similar photo{'s' if len(photo_hits) != 1 else ''}"
        if stock:
            desc += " · supplier stock via MCP"
        step("retrieve", title, "success", desc)
        return {
            "query": query,
            "hits": [{"id": h.id, "text": h.text, "metadata": h.metadata, "score": h.score} for h in hits],
            "hidden": hidden,
            "extra_context": extra,
            "images": images,
            "stock": stock,
            "trace": trace(
                state,
                "retrieve",
                t0,
                {"query": query[:300], "strategy": strategy, "k": settings.retrieval_k},
                {
                    "hits": [h.metadata.get("doc_id") for h in hits],
                    "scores": [round(h.score, 3) for h in hits],
                    "photo_matches": [p.get("owner_id") for p in photo_hits],
                    "stock": bool(stock),
                },
            ),
        }

    async def grade_documents(state: AskState) -> dict:
        t0 = time.perf_counter()
        step("grade", "Grade relevance")
        actor = actor_of(state)
        strategy = state.get("strategy") or settings.retrieval_strategy
        floor = settings.score_threshold if strategy == "threshold" else settings.min_relevance
        relevant = [h for h in state.get("hits") or [] if h["score"] >= floor]
        by_doc: dict[str, list[dict]] = {}
        for h in sorted(relevant, key=lambda x: -x["score"]):
            by_doc.setdefault(h["metadata"].get("doc_id", ""), []).append(h)
        linked: str | None = None
        async with svc.db.session() as s:
            if state.get("ticket_id"):
                t = await s.get(m.Ticket, state["ticket_id"])
                linked = t.related_doc_id if t else None
            ids = list(by_doc) + ([linked] if linked and linked not in by_doc else [])
            docs = {d.id: d for d in (await s.exec(select(m.Document).where(col(m.Document.id).in_(ids)))).all()} if ids else {}
        order = list(by_doc)
        if linked and linked in docs:
            order = [linked] + [d for d in order if d != linked]
        sources: list[dict] = []
        blocks: list[str] = []
        for doc_id in order:
            d = docs.get(doc_id)
            if d is None or d.status == "archived" or not repo.visible_doc(actor, d):
                continue
            chunks = by_doc.get(doc_id, [])
            n = len(sources) + 1
            age = days_since(d.last_reviewed)
            stale = an.is_stale(d, settings.stale_days)
            score = round(chunks[0]["score"], 3) if chunks else 1.0
            snippet_src = chunks[0]["text"] if chunks else (d.summary or d.body[:400])
            snippet = re.sub(r"\s+", " ", snippet_src.split("\n", 1)[-1]).strip()[:240]
            sources.append(
                {
                    "docId": d.id,
                    "title": d.title,
                    "category": d.category,
                    "station": d.station_id,
                    "lastReviewed": d.last_reviewed.isoformat(),
                    "daysSinceReview": age,
                    "stale": stale,
                    "score": score,
                    "snippet": snippet,
                }
            )
            header = f"[{n}] {d.title} ({d.category}, last reviewed {age} days ago{'; STALE' if stale else ''})"
            body = "\n...\n".join(c["text"] for c in chunks[:2]) if chunks else (d.body[:1800] or d.summary)
            body = re.sub(r"\[(\d+)\]", r"(\1)", body)  # keep [n] reserved for source numbers
            blocks.append(f"{header}\n{body}")
            if len(sources) >= settings.retrieval_k:
                break
        extra = state.get("extra_context") or []
        context = "\n\n".join(blocks)
        if extra:
            context = (context + "\n\n" if context else "") + "Other records:\n" + "\n\n".join(extra)
        emit("sources", sources)
        hidden = state.get("hidden", 0)
        if hidden:
            emit("meta", {"hiddenSources": hidden})
        desc = f"{len(sources)} relevant" + (f" · {hidden} hidden by your role" if hidden else "")
        step("grade", "Grade relevance", "success", desc)
        return {
            "sources": sources,
            "context": context,
            "trace": trace(
                state,
                "grade_documents",
                t0,
                {"floor": floor},
                {"relevant": len(sources), "hidden_by_role": hidden, "docs": [x["docId"] for x in sources]},
            ),
        }

    def after_grade(state: AskState) -> str:
        if state.get("sources") or state.get("extra_context") or state.get("images"):
            return "load_model"
        return "no_answer"

    async def no_answer(state: AskState) -> dict:
        t0 = time.perf_counter()
        step("generate", "Write the answer")
        hidden = state.get("hidden", 0)
        text = (
            "I couldn't find this in the kitchen documents. Try rephrasing, or raise a ticket so the station's "
            "Sous Chef can add the missing guidance."
        )
        if hidden:
            text = (
                "I couldn't find guidance on this in the documents you can see. Ask your Sous Chef; "
                "some related records are only available to Sous Chefs and the Kitchen Manager."
            )
        emit("content", text)
        step("generate", "Write the answer", "success", "No matching documents · model not called")
        return {"answer": text, "usage": {}, "reasoning_ms": 0, "trace": trace(state, "generate", t0, {"model": None}, {"fallback": True})}

    async def load_model(state: AskState) -> dict:
        t0 = time.perf_counter()
        step("model", "Load model")
        info = await svc.ollama.ensure_loaded(state["model"])
        desc = "Already in memory" if info["was_loaded"] else f"Loaded {state['model']} in {info['load_ms'] / 1000:.1f} s"
        step("model", "Load model", "success", desc)
        return {"trace": trace(state, "load_model", t0, {"model": state["model"]}, info)}

    def _messages(state: AskState) -> list[BaseMessage]:
        system = SYSTEM_PROMPT
        if state.get("summary"):
            system += f"\n\nEarlier in this conversation: {state['summary']}"
        msgs: list[BaseMessage] = [SystemMessage(system)]
        for h in state.get("history") or []:
            content = h["content"][:1500]
            msgs.append(HumanMessage(content) if h["role"] == "user" else AIMessage(content))
        n = len(state.get("sources") or [])
        valid = ", ".join(f"[{i}]" for i in range(1, n + 1)) or "none"
        text = (
            f"Context:\n{state.get('context') or '(no documents matched)'}\n\n"
            f"Valid citations: {valid}. Copy thresholds and limits exactly (for example 'change when ...').\n\n"
            f"Question: {state['question']}"
        )
        if state.get("images"):
            blocks: list[Any] = [{"type": "text", "text": text}]
            blocks += [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b}"}} for b in state["images"]]
            msgs.append(HumanMessage(content=blocks))
        else:
            msgs.append(HumanMessage(text))
        return msgs

    async def generate(state: AskState) -> dict:
        t0 = time.perf_counter()
        step("generate", "Write the answer")
        think = state.get("think")
        budget = settings.max_answer_tokens + (1536 if think else 0)
        chat = svc.chat(state["model"], reasoning=think, temperature=0.2, num_predict=budget)
        chain = RunnableLambda(_messages) | chat  # LCEL: prompt builder piped into the chat model
        answer: list[str] = []
        think_started: float | None = None
        reasoning_ms = 0
        usage: dict = {}
        async for chunk in chain.astream(state):
            r = (chunk.additional_kwargs or {}).get("reasoning_content")
            if r:
                if think_started is None:
                    think_started = time.perf_counter()
                emit("reasoning", r)
            if chunk.content:
                if think_started is not None and not reasoning_ms:
                    reasoning_ms = int((time.perf_counter() - think_started) * 1000)
                text = (
                    chunk.content
                    if isinstance(chunk.content, str)
                    else "".join(p.get("text", "") for p in chunk.content if isinstance(p, dict))
                )
                answer.append(text)
                emit("content", text)
            if chunk.usage_metadata:
                usage = dict(chunk.usage_metadata)
        if think_started is not None and not reasoning_ms:
            reasoning_ms = int((time.perf_counter() - think_started) * 1000)
        full = "".join(answer).strip()
        out_tokens = usage.get("output_tokens", 0)
        secs = time.perf_counter() - t0
        desc = f"{out_tokens} tokens · {out_tokens / secs:.1f} tok/s" if out_tokens and secs else None
        step("generate", "Write the answer", "success", desc)
        return {
            "answer": full,
            "usage": usage,
            "reasoning_ms": reasoning_ms,
            "trace": trace(
                state,
                "generate",
                t0,
                {"model": state["model"], "reasoning": bool(think), "images": len(state.get("images") or [])},
                {"tokens_in": usage.get("input_tokens", 0), "tokens_out": out_tokens, "reasoning_ms": reasoning_ms},
            ),
        }

    async def check_citations(state: AskState) -> dict:
        t0 = time.perf_counter()
        step("cite", "Check citations")
        sources = state.get("sources") or []
        answer = state.get("answer") or ""
        nums = {int(n) for n in CITE_RE.findall(answer)}
        valid = sorted(n for n in nums if 1 <= n <= len(sources))
        invalid = sorted(n for n in nums if n not in valid)
        cited = [sources[n - 1] for n in valid]
        stale = [s for s in cited if s["stale"]]
        if stale:
            names = ", ".join(f"*{s['title']}* [{sources.index(s) + 1}] ({s['daysSinceReview']} days)" for s in stale)
            note = (
                f"\n\n> **Check before you act:** {names} {'is' if len(stale) == 1 else 'are'} overdue for review, "
                "so confirm the steps with your Sous Chef."
            )
            emit("content", note)
            answer += note
        if cited:
            async with svc.db.session() as s:
                for src in cited:
                    s.add(m.DocumentCitation(document_id=src["docId"], run_id=state["run_id"]))
        if not sources:
            desc = "No documents to cite"
        elif not valid:
            desc = "No citations in the answer"
        else:
            desc = f"{len(valid)} cited" + (f" · {len(stale)} stale source flagged" if stale else " · all sources current")
        if invalid:
            desc += f" · ignored {len(invalid)} unknown"
        step("cite", "Check citations", "success", desc)
        return {
            "answer": answer,
            "trace": trace(
                state,
                "check_citations",
                t0,
                {},
                {"cited": [s["docId"] for s in cited], "stale": [s["docId"] for s in stale], "invalid": invalid},
            ),
        }

    async def propose_action(state: AskState) -> dict:
        t0 = time.perf_counter()
        actor = actor_of(state)
        flags = state.get("flags") or []
        proposal: dict | None = None
        stock = state.get("stock")
        if stock and stock.get("below_par"):
            proposal = await _supply_proposal(svc, stock)
        elif state.get("ticket_id") and ("escalate" in flags or "reassign" in flags):
            proposal = await _ticket_proposal(svc, state["ticket_id"], flags, state["question"])
        if proposal is None:
            return {"trace": trace(state, "propose_action", t0, {}, {"proposal": None}, "skipped")}
        step("propose", "Propose an action")
        if not can.request_approval(actor):
            note = (
                "\n\nTell your Sous Chef: they can raise this as a request, and the Kitchen Manager approves it "
                "before anything is ordered or changed."
            )
            emit("content", note)
            step("propose", "Propose an action", "success", "Your role can't request approvals · told you who can")
            return {
                "answer": (state.get("answer") or "") + note,
                "trace": trace(state, "propose_action", t0, {"kind": proposal["kind"]}, {"blocked_by_role": True}),
            }
        async with svc.db.session() as s:
            existing = (await s.exec(select(m.Approval).where(m.Approval.status == "pending", m.Approval.kind == proposal["kind"]))).all()
            same = next((a for a in existing if same_target(a.payload or {}, proposal["payload"])), None)
            if same is None:
                same = await actions.create_approval(
                    s,
                    kind=proposal["kind"],
                    title=proposal["title"],
                    summary=proposal["summary"],
                    payload=proposal["payload"],
                    requested_by=actor.who,
                    run_id=state["run_id"],
                    thread_id=state["run_id"],
                    source="agent",
                )
                reused = False
            else:
                reused = True
            await s.flush()
            payload = actions.interrupt_payload(same, actor)
        desc = f"{payload['approvalId']} {'already waiting' if reused else 'sent'} to the Kitchen Manager"
        step("propose", "Propose an action", "success", desc)
        emit("interrupt", payload)
        return {
            "approval_id": None if reused else payload["approvalId"],
            "trace": trace(state, "propose_action", t0, {"kind": proposal["kind"]}, {"approval": payload["approvalId"], "reused": reused}),
        }

    def after_propose(state: AskState) -> str:
        return "human_approval" if state.get("approval_id") else END

    async def human_approval(state: AskState) -> dict:
        decision = interrupt({"approvalId": state["approval_id"]})
        return {"decision": decision}

    async def execute_action(state: AskState) -> dict:
        t0 = time.perf_counter()
        decision = state.get("decision") or {}
        entries = [
            {
                "node": "human_approval",
                "started_ms": int((time.time() - state["started"]) * 1000),
                "duration_ms": 0,
                "status": "success",
                "input": {"approval": state.get("approval_id")},
                "output": {"decision": decision.get("status"), "by": decision.get("decidedBy")},
            }
        ]
        entries += trace(
            state,
            "execute_action",
            t0,
            {"approval": state.get("approval_id")},
            {"result": decision.get("result"), "reason": decision.get("reason")},
        )
        await finish_run(svc, state["run_id"], entries)
        return {"trace": entries}

    g = StateGraph(AskState)
    g.add_node("classify_intent", classify_intent)
    g.add_node("retrieve", retrieve)
    g.add_node("grade_documents", grade_documents)
    g.add_node("no_answer", no_answer)
    g.add_node("load_model", load_model)
    g.add_node("generate", generate)
    g.add_node("check_citations", check_citations)
    g.add_node("propose_action", propose_action)
    g.add_node("human_approval", human_approval)
    g.add_node("execute_action", execute_action)
    g.add_edge(START, "classify_intent")
    g.add_edge("classify_intent", "retrieve")
    g.add_edge("retrieve", "grade_documents")
    g.add_conditional_edges("grade_documents", after_grade, ["load_model", "no_answer"])
    g.add_edge("load_model", "generate")
    g.add_edge("generate", "check_citations")
    g.add_edge("no_answer", "check_citations")
    g.add_edge("check_citations", "propose_action")
    g.add_conditional_edges("propose_action", after_propose, ["human_approval", END])
    g.add_edge("human_approval", "execute_action")
    g.add_edge("execute_action", END)
    return g.compile(checkpointer=svc.checkpointer)


# ---------------------------------------------------------------- proposals


async def _supply_proposal(svc: Services, stock: dict) -> dict | None:
    qty = max(1, stock["par"] - stock["on_hand"] + max(1, stock["par"] // 2))
    try:
        quote = await svc.mcp.call("supplier-inventory", "price_quote", {"sku": stock["sku"], "quantity": qty})
    except McpCallError:
        quote = {**stock, "quantity": qty, "total": round(stock["unit_price"] * qty, 2), "delivery_date": None}
    keyword = (stock.get("matched") or stock["item"].split(",")[0]).lower()
    async with svc.db.session() as s:
        tickets = (await s.exec(select(m.Ticket).where(col(m.Ticket.status).in_(OPEN)))).all()
    linked = next((t.id for t in tickets if keyword in t.title.lower() or keyword in (t.description or "").lower()), None)
    payload = {
        "supplier": quote["supplier"],
        "item": quote["item"],
        "sku": quote["sku"],
        "quantity": quote["quantity"],
        "unit": quote["unit"],
        "unitPrice": quote["unit_price"],
        "neededBy": quote.get("delivery_date"),
        "ticketId": linked,
    }
    return {
        "kind": "draft_supply_order",
        "title": f"Order {quote['quantity']} × {quote['item']}",
        "summary": (
            f"{stock['on_hand']} {stock['unit']} on hand against a par of {stock['par']}. "
            f"{quote['quantity']} at ${quote['unit_price']:.2f} = ${quote['total']:,.2f}"
            + (f", delivered {quote['delivery_date']}." if quote.get("delivery_date") else ".")
        ),
        "payload": payload,
    }


async def _ticket_proposal(svc: Services, ticket_id: str, flags: list[str], question: str) -> dict | None:
    async with svc.db.session() as s:
        t = await s.get(m.Ticket, ticket_id)
        if t is None or t.status not in OPEN:
            return None
        lk = await repo.lookup(s)
        who = await repo.names(s)
        km = (await s.exec(select(m.User).where(m.User.role == "kitchen_manager"))).first()
        if "reassign" in flags:
            mm = an.mismatches([t], lk)
            if mm and mm[0].get("suggested_assignee_id"):
                to = mm[0]["suggested_assignee_id"]
                return {
                    "kind": "reassign_ticket",
                    "title": f"Reassign {t.id} to {who.get(to, to)}",
                    "summary": (
                        f"{t.id} is assigned to {lk.station_name(mm[0]['assignee_station'])} but its document is "
                        f"owned by {lk.station_name(mm[0]['doc_station'])}."
                    ),
                    "payload": {
                        "ticketId": t.id,
                        "fromAssigneeId": t.assignee_id,
                        "toAssigneeId": to,
                        "reason": "Linked document is owned by another station",
                    },
                }
        if "escalate" in flags:
            tags = set(t.tags or [])
            severity = (
                "food_safety"
                if tags & {"food-safety", "allergen", "temperature", "cooling"}
                else ("staffing" if "staffing" in tags else "equipment")
            )
            notify = [x for x in [km.crew_member_id if km else None, lk.station_lead(t.station_id)] if x]
            return {
                "kind": "escalate_incident",
                "title": f"Escalate {t.id}: {t.title[:60]}",
                "summary": f"Raise {t.id} to critical and notify {', '.join(who.get(n, n) for n in notify) or 'the manager'}.",
                "payload": {
                    "ticketId": t.id,
                    "severity": severity,
                    "notify": notify,
                    "holdProduct": severity == "food_safety",
                    "note": question[:240],
                },
            }
    return None
