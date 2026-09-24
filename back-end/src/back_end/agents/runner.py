"""Runs the agent graphs and turns their events into Server-Sent Events.

Event names match what the React chat provider and Triage screen parse:
    ask:    meta, step, sources, reasoning, content, interrupt, done, error
    triage: meta, node, reasoning, content, result, interrupt, done, error
Every `data:` line is JSON (string deltas are JSON-encoded strings).
"""

import asyncio
import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx
import httpx2
from langgraph.types import Command

from back_end.agents import memory
from back_end.agents.common import actor_dict, resolve_think
from back_end.auth.permissions import Actor
from back_end.core.logging import get_logger
from back_end.db import models as m
from back_end.schemas import AskIn
from back_end.services import repo
from back_end.services.container import Services
from back_end.services.ollama import ModelInfo

log = get_logger(__name__)


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n"


def friendly_error(exc: BaseException, base_url: str, model: str) -> str:
    text = str(exc)
    low = text.lower()
    unreachable = isinstance(exc, (httpx.ConnectError, httpx2.ConnectError, ConnectionError))
    if unreachable or "failed to connect" in low or "connection refused" in low:
        return f"Can't reach Ollama at {base_url}. Start it with `ollama serve`, then try again."
    if "not found" in low and ("model" in low or "pull" in low):
        return f"The model {model} isn't installed in Ollama. Pull it with `ollama pull {model}`, or pick another model."
    if "does not support thinking" in low:
        return f"{model} can't show its reasoning. Turn Reasoning off and ask again."
    if "does not support" in low and "image" in low:
        return f"{model} can't read images. Pick a vision model (for example gemma4:e2b) or remove the photo."
    if isinstance(exc, (TimeoutError, httpx.TimeoutException, httpx2.TimeoutException)):
        return f"{model} took too long to answer. Try a smaller model or a shorter question."
    return f"Something went wrong while answering: {text[:240] or type(exc).__name__}"


async def _new_run_id(svc: Services) -> str:
    async with svc.db.session() as s:
        return await repo.next_id(s, "agent_run", "RUN-", 4)


async def _save_run(
    svc: Services, *, run_id: str, graph: str, actor: Actor, model: str, started: float, status: str, question: str | None, values: dict
) -> None:
    usage = values.get("usage") or {}
    async with svc.db.session() as s:
        run = await s.get(m.AgentRun, run_id) or m.AgentRun(id=run_id, graph=graph, user_id=actor.user_id, model=model)
        run.duration_ms = int((time.time() - started) * 1000)
        run.tokens_in = int(usage.get("input_tokens", 0))
        run.tokens_out = int(usage.get("output_tokens", 0))
        run.status = status
        run.question = question
        run.nodes = values.get("trace") or []
        s.add(run)


# ---------------------------------------------------------------- ask


async def stream_ask(svc: Services, actor: Actor, body: AskIn, info: ModelInfo, attachments: list[dict]) -> AsyncIterator[str]:
    started = time.time()
    run_id = await _new_run_id(svc)
    mem = await memory.load(svc, body.conversation_id, actor.user_id)
    history = mem.turns or [h.model_dump() for h in body.history][-8:]
    think = resolve_think(info.thinking, body.reasoning)
    turns = sum(1 for h in history if h["role"] == "user")
    yield sse(
        "meta",
        {
            "model": info.name,
            "reasoning": bool(think),
            "thinking": info.thinking,
            "memoryTurns": turns,
            "hiddenSources": 0,
            "runId": run_id,
            "conversationId": body.conversation_id,
        },
    )
    state = {
        "run_id": run_id,
        "started": started,
        "actor": actor_dict(actor),
        "question": body.question.strip(),
        "model": info.name,
        "reasoning": body.reasoning,
        "think": think,
        "vision": "vision" in info.capabilities,
        "strategy": body.strategy or svc.settings.retrieval_strategy,
        "conversation_id": body.conversation_id,
        "history": history,
        "summary": mem.summary,
        "attachments": attachments,
        "ticket_id": body.ticket_id,
    }
    config = {"configurable": {"thread_id": run_id}, "recursion_limit": 30}
    graph = svc.graphs["ask"]
    answer: list[str] = []
    reasoning: list[str] = []
    steps: dict[str, dict] = {}
    sources: list = []
    interrupt_data: dict | None = None
    hidden = 0
    status = "success"
    error: str | None = None

    def track(event: str, data: Any) -> None:
        nonlocal sources, interrupt_data, hidden
        if event == "content":
            answer.append(data)
        elif event == "reasoning":
            reasoning.append(data)
        elif event == "step":
            steps[data["key"]] = data
        elif event == "sources":
            sources = data
        elif event == "interrupt":
            interrupt_data = data
        elif event == "meta":
            hidden = data.get("hiddenSources", hidden)

    async def persist(final_status: str) -> None:
        values: dict = {}
        try:
            values = (await graph.aget_state(config)).values
        except Exception:  # noqa: BLE001
            pass
        await _save_run(
            svc,
            run_id=run_id,
            graph="ask",
            actor=actor,
            model=info.name,
            started=started,
            status="error" if final_status == "aborted" else final_status,
            question=body.question,
            values=values,
        )
        step_list = [
            {
                **v,
                "status": "abort"
                if final_status == "aborted" and v["status"] == "loading"
                else ("error" if final_status == "error" and v["status"] == "loading" else v["status"]),
            }
            for v in steps.values()
        ]
        await memory.save_exchange(
            svc,
            conversation_id=body.conversation_id,
            user_id=actor.user_id,
            model=info.name,
            question=body.question.strip(),
            answer="".join(answer).strip(),
            user_extra={
                "attachments": [
                    {"assetId": a["assetId"], "name": a["name"], "url": a.get("url"), "bytes": a.get("bytes")} for a in attachments
                ]
            },
            assistant_extra={
                "model": info.name,
                "runId": run_id,
                "reasoning": "".join(reasoning),
                "reasoningOn": bool(think),
                "reasoningMs": values.get("reasoning_ms", 0),
                "sources": sources,
                "steps": step_list,
                "interrupt": interrupt_data,
                "hiddenSources": hidden,
                "error": error,
                "aborted": final_status == "aborted",
            },
        )
        if final_status != "aborted":
            svc.spawn(memory.maybe_summarize(svc, body.conversation_id, info.name), name="memory-summary")

    try:
        async for mode, chunk in graph.astream(state, config, stream_mode=["custom", "updates"]):
            if mode == "custom":
                event, data = chunk
                track(event, data)
                yield sse(event, data)
            elif isinstance(chunk, dict) and "__interrupt__" in chunk:
                status = "interrupted"
    except (asyncio.CancelledError, GeneratorExit):
        svc.spawn(persist("aborted"), name=f"persist-{run_id}")
        raise
    except Exception as exc:  # noqa: BLE001 - reported to the user as an SSE error event
        log.warning("ask_failed", run=run_id, error=repr(exc))
        status, error = "error", friendly_error(exc, svc.settings.ollama_base_url, info.name)
        for k, v in steps.items():
            if v["status"] == "loading":
                v = {**v, "status": "error"}
                steps[k] = v
                yield sse("step", v)
        yield sse("error", {"message": error})
    await persist(status)
    values = {}
    try:
        values = (await graph.aget_state(config)).values
    except Exception:  # noqa: BLE001
        pass
    yield sse("done", {"elapsed_ms": int((time.time() - started) * 1000), "reasoning_ms": values.get("reasoning_ms", 0), "run_id": run_id})


# ---------------------------------------------------------------- triage


async def stream_triage(svc: Services, actor: Actor, info: ModelInfo, station: str | None) -> AsyncIterator[str]:
    started = time.time()
    run_id = await _new_run_id(svc)
    yield sse("meta", {"model": info.name, "station": station, "runId": run_id})
    state = {
        "run_id": run_id,
        "started": started,
        "actor": actor_dict(actor),
        "model": info.name,
        "thinking": info.thinking,
        "station": station,
    }
    config = {"configurable": {"thread_id": run_id}, "recursion_limit": 40}
    graph = svc.graphs["triage"]
    status = "success"
    question = f"Triage · {station or 'all stations'}"
    try:
        async for mode, chunk in graph.astream(state, config, stream_mode=["custom", "updates"]):
            if mode == "custom":
                event, data = chunk
                yield sse(event, data)
            elif isinstance(chunk, dict) and "__interrupt__" in chunk:
                status = "interrupted"
    except (asyncio.CancelledError, GeneratorExit):
        svc.spawn(_save_triage(svc, graph, config, run_id, actor, info.name, started, "error", question))
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("triage_failed", run=run_id, error=repr(exc))
        status = "error"
        yield sse("error", {"message": friendly_error(exc, svc.settings.ollama_base_url, info.name)})
    await _save_triage(svc, graph, config, run_id, actor, info.name, started, status, question)
    yield sse("done", {"elapsed_ms": int((time.time() - started) * 1000), "run_id": run_id})


async def _save_triage(svc, graph, config, run_id, actor, model, started, status, question) -> None:
    values: dict = {}
    try:
        values = (await graph.aget_state(config)).values
    except Exception:  # noqa: BLE001
        pass
    await _save_run(
        svc, run_id=run_id, graph="triage", actor=actor, model=model, started=started, status=status, question=question, values=values
    )


# ---------------------------------------------------------------- resume


async def resume_after_decision(svc: Services, approval: m.Approval) -> bool:
    """Resume the paused graph thread that raised this approval, if it is still in memory."""
    if not approval.thread_id:
        return False
    async with svc.db.session() as s:
        run = await s.get(m.AgentRun, approval.run_id) if approval.run_id else None
    graph = svc.graphs.get(run.graph if run else "ask")
    if graph is None:
        return False
    config = {"configurable": {"thread_id": approval.thread_id}}
    try:
        snap = await graph.aget_state(config)
    except Exception:  # noqa: BLE001
        return False
    if not snap.next or "human_approval" not in snap.next:
        return False
    decision = {
        "approvalId": approval.id,
        "status": approval.status,
        "result": approval.result,
        "decidedBy": approval.decided_by,
        "reason": approval.reason,
    }
    try:
        await graph.ainvoke(Command(resume=decision), config)
        return True
    except Exception as exc:  # noqa: BLE001 - the decision itself is already saved
        log.warning("resume_failed", approval=approval.id, error=repr(exc))
        return False
