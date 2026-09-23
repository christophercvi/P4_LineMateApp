"""Helpers shared by the Ask and Triage graphs."""

import time
from dataclasses import asdict
from typing import Any

from langgraph.config import get_stream_writer

from back_end.auth.permissions import Actor
from back_end.db import models as m
from back_end.services.container import Services


def actor_of(state: dict) -> Actor:
    return Actor(**state["actor"])


def actor_dict(a: Actor) -> dict:
    return asdict(a)


def emit(event: str, data: Any) -> None:
    """Send one UI event through LangGraph's custom stream; the runner turns it into SSE."""
    get_stream_writer()((event, data))


def step(key: str, title: str, status: str = "loading", description: str | None = None) -> None:
    payload: dict[str, Any] = {"key": key, "title": title, "status": status}
    if description:
        payload["description"] = description
    emit("step", payload)


def node_event(node: str, title: str, status: str = "loading", description: str | None = None, duration_ms: int | None = None) -> None:
    payload: dict[str, Any] = {"node": node, "title": title, "status": status}
    if description:
        payload["description"] = description
    if duration_ms is not None:
        payload["durationMs"] = duration_ms
    emit("node", payload)


def trace(state: dict, node: str, t0: float, inp: dict, out: dict, status: str = "success") -> list[dict]:
    """One Agent Runs trace row; `t0` is time.perf_counter() at node start."""
    dur = int((time.perf_counter() - t0) * 1000)
    return [
        {
            "node": node,
            "started_ms": max(0, int((time.time() - state["started"]) * 1000) - dur),
            "duration_ms": dur,
            "status": status,
            "input": inp,
            "output": out,
        }
    ]


def same_target(a: dict, b: dict) -> bool:
    if a.get("sku") or b.get("sku"):
        return a.get("sku") == b.get("sku")
    return a.get("ticketId") == b.get("ticketId")


def resolve_think(thinking: str, requested: bool) -> bool | None:
    """Map the UI's Reasoning switch onto Ollama's `think` flag for this model."""
    if thinking == "always":
        return True
    if thinking == "toggle":
        return requested
    return None


async def finish_run(svc: Services, run_id: str, entries: list[dict]) -> None:
    """Append trace rows written after the run resumed from an approval interrupt."""
    async with svc.db.session() as s:
        run = await s.get(m.AgentRun, run_id)
        if run is not None:
            run.nodes = [*(run.nodes or []), *entries]
            run.status = "success"
            s.add(run)
