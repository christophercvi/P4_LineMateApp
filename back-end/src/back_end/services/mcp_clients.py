"""Client for the external MCP servers LineMate consumes (supplier inventory, shift scheduler).

Default transport is stdio: each call starts the server as a subprocess
(`python -m back_end.mcp_external.<module>`), exactly how a third-party MCP server is consumed.
Tests use the in-process transport (the MCPServer object itself) to stay fast and hermetic.
Results are cached briefly; status/latency feed the MCP console.
"""

import json
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from back_end.core.logging import get_logger

log = get_logger(__name__)


@dataclass(slots=True)
class ExternalServer:
    id: str
    name: str
    module: str
    tools_used_by: dict[str, str]
    status: Literal["connected", "degraded", "offline"] = "offline"
    last_seen: float | None = None
    latency_ms: int = 0
    tools: list[dict[str, str]] = field(default_factory=list)


SERVERS: dict[str, ExternalServer] = {
    "supplier-inventory": ExternalServer(
        id="supplier-inventory",
        name="Supplier Inventory",
        module="back_end.mcp_external.supplier_server",
        tools_used_by={
            "check_stock": "triage → check_supplies",
            "price_quote": "triage → propose_actions",
            "below_par": "triage → check_supplies",
        },
    ),
    "shift-scheduler": ExternalServer(
        id="shift-scheduler",
        name="Shift Scheduler",
        module="back_end.mcp_external.shift_server",
        tools_used_by={"get_roster": "triage → enrich_with_docs", "find_cover": "triage → propose_actions"},
    ),
}


class McpCallError(RuntimeError):
    pass


class ExternalMcp:
    def __init__(self, *, mode: Literal["stdio", "inprocess", "off"] = "stdio", timeout: float = 20.0, cache_seconds: float = 60.0):
        self.mode = mode
        self.timeout = timeout
        self.cache_seconds = cache_seconds
        self._cache: dict[str, tuple[float, Any]] = {}

    def _target(self, server: ExternalServer):
        if self.mode == "inprocess":
            module = __import__(server.module, fromlist=["server"])
            return module.server
        return StdioServerParameters(command=sys.executable, args=["-m", server.module])

    async def call(self, server_id: str, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.mode == "off":
            raise McpCallError("External MCP servers are disabled")
        server = SERVERS[server_id]
        key = f"{server_id}:{tool}:{json.dumps(args or {}, sort_keys=True)}"
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.cache_seconds:
            return hit[1]
        started = time.perf_counter()
        try:
            async with Client(self._target(server), read_timeout_seconds=self.timeout) as client:
                result = await client.call_tool(tool, args or {})
        except Exception as exc:
            server.status = "offline"
            log.warning("mcp_external_failed", server=server_id, tool=tool, error=repr(exc))
            raise McpCallError(f"{server.name} is unavailable: {exc}") from exc
        server.latency_ms = int((time.perf_counter() - started) * 1000)
        server.last_seen = time.time()
        server.status = "connected" if server.latency_ms < 5000 else "degraded"
        if result.is_error:
            text = " ".join(getattr(c, "text", "") for c in result.content)
            raise McpCallError(text or f"{tool} failed")
        data = result.structured_content
        if data is None:
            text = "".join(getattr(c, "text", "") for c in result.content)
            data = json.loads(text) if text else {}
        if isinstance(data, dict) and set(data) == {"result"}:
            data = data["result"]
        self._cache[key] = (time.monotonic(), data)
        log.info("mcp_external_call", server=server_id, tool=tool, ms=server.latency_ms)
        return data

    async def refresh(self, server_id: str) -> ExternalServer:
        server = SERVERS[server_id]
        if self.mode == "off":
            server.status = "offline"
            return server
        started = time.perf_counter()
        try:
            async with Client(self._target(server), read_timeout_seconds=self.timeout) as client:
                listed = await client.list_tools()
            server.tools = [
                {"name": t.name, "description": t.description or "", "used_by": server.tools_used_by.get(t.name, "-")} for t in listed.tools
            ]
            server.latency_ms = int((time.perf_counter() - started) * 1000)
            server.last_seen = time.time()
            server.status = "connected" if server.latency_ms < 5000 else "degraded"
        except Exception as exc:  # noqa: BLE001
            server.status = "offline"
            log.warning("mcp_external_unreachable", server=server_id, error=repr(exc))
        return server
