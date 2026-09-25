"""LineMate's own MCP server.

Exposed to MCP clients (Claude Desktop, IDE agents, evaluation jobs) over Streamable HTTP at `/mcp`
and over stdio (`uv run linemate-mcp`). Read tools answer directly; write tools never change kitchen
data: they create an Approval that the Kitchen Manager decides in the LineMate UI.

Authentication
* HTTP: `Authorization: Bearer <token>` where the token is a LineMate service token (`lm_svc_...`,
  argon2-hashed in the database, created by an Admin) or a user's access JWT.
* stdio: the service token is read from the `LINEMATE_MCP_TOKEN` environment variable.
* The console's "Try it" button calls the same tools in-process as the signed-in user.
"""

import contextvars
import os
import time
from dataclasses import dataclass
from typing import Any, Literal

import jwt
from mcp.server.mcpserver import Context, MCPServer
from sqlmodel import col, select
from starlette.responses import JSONResponse

from back_end.agents import actions
from back_end.auth.permissions import Actor, can
from back_end.core.logging import get_logger
from back_end.core.security import decode_access_token, token_lookup_prefix, verify_password
from back_end.core.timeutil import days_since, utcnow
from back_end.db import models as m
from back_end.services import analytics as an
from back_end.services import repo
from back_end.services.container import Services, get_services
from back_end.services.mcp_clients import McpCallError
from back_end.services.vectorstore import role_filter

log = get_logger(__name__)

PRIORITY_ORDER = ["low", "medium", "high", "critical"]


@dataclass(frozen=True, slots=True)
class ToolMeta:
    access: Literal["read", "write"]
    requires_approval: bool
    roles: tuple[str, ...]
    scope: str


TOOL_META: dict[str, ToolMeta] = {
    "search_documents": ToolMeta("read", False, ("line_cook", "sous_chef", "kitchen_manager", "admin", "service"), "mcp:read"),
    "list_open_tickets": ToolMeta("read", False, ("sous_chef", "kitchen_manager", "admin", "service"), "mcp:read"),
    "workload_analytics": ToolMeta("read", False, ("sous_chef", "kitchen_manager", "admin", "service"), "mcp:read"),
    "create_ticket": ToolMeta("write", True, ("line_cook", "sous_chef", "kitchen_manager", "service"), "mcp:write"),
    "draft_supply_order": ToolMeta("write", True, ("sous_chef", "kitchen_manager", "service"), "mcp:write"),
    "escalate_incident": ToolMeta("write", True, ("sous_chef", "kitchen_manager", "service"), "mcp:write"),
}


@dataclass(frozen=True, slots=True)
class Caller:
    actor: Actor
    scopes: tuple[str, ...]
    label: str  # shown in the call log ("claude-desktop-elena", "Elena Rossi (try it)")


class McpAuthError(PermissionError):
    pass


# Set by the in-process "Try it" path; tasks spawned by the in-memory client inherit it.
inprocess_caller: contextvars.ContextVar[Caller | None] = contextvars.ContextVar("inprocess_caller", default=None)


async def caller_from_token(svc: Services, token: str) -> Caller:
    """Resolve a bearer token to the acting identity (service token first, then user JWT)."""
    async with svc.db.session() as s:
        if token.startswith("lm_svc_"):
            rows = (await s.exec(select(m.ServiceToken).where(m.ServiceToken.prefix == token_lookup_prefix(token)))).all()
            for row in rows:
                if verify_password(token, row.token_hash):
                    if row.revoked:
                        raise McpAuthError("This service token was revoked")
                    if row.expires_at and row.expires_at < utcnow():
                        raise McpAuthError("This service token has expired")
                    row.last_used = utcnow()
                    s.add(row)
                    owner = await s.get(m.User, row.user_id)
                    actor = Actor(
                        user_id=row.user_id,
                        role="service",
                        station=None,
                        crew_member_id=None,
                        display_name=f"{row.name} (service token{f' of {owner.display_name}' if owner else ''})",
                    )
                    return Caller(actor, tuple(row.scopes or []), row.name)
            raise McpAuthError("Unknown service token")
        try:
            claims = decode_access_token(token)
        except jwt.PyJWTError as exc:
            raise McpAuthError("Invalid or expired access token") from exc
        user = await s.get(m.User, claims["sub"])
        if user is None or not user.active:
            raise McpAuthError("User not found or disabled")
        actor = repo.actor_for(user)
        return Caller(actor, ("mcp:read", "mcp:write"), user.display_name)


async def _resolve_caller(ctx: Context | None) -> Caller:
    svc = get_services()
    if (c := inprocess_caller.get()) is not None:
        return c
    auth = ""
    try:
        headers = ctx.headers if ctx is not None else None
        auth = (headers or {}).get("authorization", "") if headers else ""
    except Exception:  # noqa: BLE001 - stdio has no request headers
        auth = ""
    if auth.lower().startswith("bearer "):
        return await caller_from_token(svc, auth.split(" ", 1)[1].strip())
    if env := os.environ.get("LINEMATE_MCP_TOKEN"):
        return await caller_from_token(svc, env)
    raise McpAuthError("Missing bearer token")


async def _guard(tool: str, ctx: Context | None) -> Caller:
    meta = TOOL_META[tool]
    caller = await _resolve_caller(ctx)
    if not can.call_mcp_tool(caller.actor, meta.roles):
        raise McpAuthError(f"{tool} is not available to the {caller.actor.role.replace('_', ' ')} role")
    if meta.scope not in caller.scopes:
        raise McpAuthError(f"{tool} needs the {meta.scope} scope")
    return caller


async def _log_call(tool: str, caller: str, started: float, status: str) -> None:
    svc = get_services()
    async with svc.db.session() as s:
        s.add(m.McpToolCall(tool=tool, caller=caller, status=status, latency_ms=round((time.perf_counter() - started) * 1000, 1)))


async def _run(tool: str, ctx: Context | None, fn) -> Any:
    started = time.perf_counter()
    label = "unknown"
    try:
        caller = await _guard(tool, ctx)
        label = caller.label
        out = await fn(caller)
        await _log_call(tool, label, started, "ok")
        return out
    except Exception:
        await _log_call(tool, label, started, "error")
        raise


def _approval_out(a: m.Approval) -> dict[str, Any]:
    return {
        "status": "interrupted",
        "approval_id": a.id,
        "waiting_for": "kitchen_manager",
        "message": f"{a.title} is waiting for the Kitchen Manager's approval in LineMate.",
    }


def create_mcp_server() -> MCPServer:
    server = MCPServer(
        "LineMate",
        instructions=(
            "Kitchen knowledge base and ticketing for a restaurant line. Read tools answer directly. "
            "Write tools create an approval request for the Kitchen Manager; nothing changes until it is approved."
        ),
    )

    @server.tool(
        description="Search the kitchen knowledge base (SOPs, recipes, onboarding, incident reports) "
        "and return the best matching documents with review age."
    )
    async def search_documents(
        query: str, category: str | None = None, station: str | None = None, k: int = 4, ctx: Context | None = None
    ) -> list[dict[str, Any]]:
        async def go(c: Caller) -> list[dict[str, Any]]:
            svc = get_services()
            extra: dict[str, Any] = {}
            if category:
                extra["category"] = category
            if station:
                extra["station"] = station
            where = role_filter(can.see_incident_reports(c.actor), extra or None)
            hits = await svc.vectors.search(query, k=max(1, min(k, 8)) * 3, strategy="similarity", where=where)
            seen: dict[str, dict] = {}
            async with svc.db.session() as s:
                for h in hits:
                    did = h.metadata.get("doc_id")
                    if not did or did in seen:
                        continue
                    d = await s.get(m.Document, did)
                    if d is None or d.status == "archived":
                        continue
                    seen[did] = {
                        "doc_id": d.id,
                        "title": d.title,
                        "category": d.category,
                        "station": d.station_id,
                        "score": round(h.score, 3),
                        "last_reviewed_days": days_since(d.last_reviewed),
                        "stale": an.is_stale(d, svc.settings.stale_days),
                        "snippet": h.text[:280],
                    }
                    if len(seen) >= max(1, min(k, 8)):
                        break
            return list(seen.values())

        return await _run("search_documents", ctx, go)

    @server.tool(description="List open tickets (open, in progress, blocked), highest priority first.")
    async def list_open_tickets(
        station: str | None = None, min_priority: str | None = None, limit: int = 20, ctx: Context | None = None
    ) -> list[dict[str, Any]]:
        async def go(c: Caller) -> list[dict[str, Any]]:
            svc = get_services()
            async with svc.db.session() as s:
                q = select(m.Ticket).where(col(m.Ticket.status).in_(an.OPEN_STATUSES))
                if station:
                    q = q.where(m.Ticket.station_id == station)
                rows = list((await s.exec(q)).all())
            floor = PRIORITY_ORDER.index(min_priority) if min_priority in PRIORITY_ORDER else 0
            rows = [t for t in rows if PRIORITY_ORDER.index(t.priority) >= floor]
            rows.sort(key=lambda t: (-PRIORITY_ORDER.index(t.priority), t.created_at))
            return [
                {
                    "id": t.id,
                    "title": t.title,
                    "priority": t.priority,
                    "status": t.status,
                    "station": t.station_id,
                    "assignee": t.assignee_id,
                    "related_doc_id": t.related_doc_id,
                    "age_days": days_since(t.created_at),
                }
                for t in rows[: max(1, min(limit, 100))]
            ]

        return await _run("list_open_tickets", ctx, go)

    @server.tool(description="Open-ticket load per station, normalised by crew size; flags overloaded stations.")
    async def workload_analytics(include_resolved: bool = False, ctx: Context | None = None) -> dict[str, Any]:
        async def go(c: Caller) -> dict[str, Any]:
            svc = get_services()
            async with svc.db.session() as s:
                tickets = list((await s.exec(select(m.Ticket))).all())
                stations = list((await s.exec(select(m.Station))).all())
            statuses = [*an.OPEN_STATUSES, "resolved"] if include_resolved else None
            rows, mean = an.workload(tickets, stations, statuses)
            return {
                "mean_load_index": round(mean, 2),
                "stations": {
                    r["station"]: {
                        "open": r["open"],
                        "critical": r["critical"],
                        "high": r["high"],
                        "load_index": r["load_index"],
                        "flagged": r["flagged"],
                    }
                    for r in rows
                },
            }

        return await _run("workload_analytics", ctx, go)

    @server.tool(description="Ask for a new ticket. Creates an approval request; the ticket is created once the Kitchen Manager approves.")
    async def create_ticket(
        title: str,
        station: str,
        description: str = "",
        priority: str = "medium",
        related_doc_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async def go(c: Caller) -> dict[str, Any]:
            if priority not in PRIORITY_ORDER:
                raise ValueError(f"priority must be one of {', '.join(PRIORITY_ORDER)}")
            svc = get_services()
            async with svc.db.session() as s:
                if await s.get(m.Station, station) is None:
                    raise ValueError(f"Unknown station {station}")
                if related_doc_id and await s.get(m.Document, related_doc_id) is None:
                    raise ValueError(f"Unknown document {related_doc_id}")
                a = await actions.create_approval(
                    s,
                    kind="create_ticket",
                    title=f"New ticket: {title[:80]}",
                    summary=f"{priority.capitalize()} ticket for {station} requested over MCP by {c.label}. {description[:200]}".strip(),
                    payload={
                        "title": title,
                        "description": description,
                        "priority": priority,
                        "station": station,
                        "relatedDocId": related_doc_id,
                        "tags": ["mcp"],
                    },
                    requested_by=c.actor.display_name or c.actor.who,
                    run_id=f"mcp:{c.label}",
                    source="mcp",
                )
                return _approval_out(a)

        return await _run("create_ticket", ctx, go)

    @server.tool(
        description="Draft a supply order from the supplier catalogue. Creates an approval request; "
        "nothing is ordered until the Kitchen Manager approves."
    )
    async def draft_supply_order(
        sku: str,
        quantity: int,
        supplier: str = "Coastal Restaurant Supply",
        needed_by: str | None = None,
        ticket_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        async def go(c: Caller) -> dict[str, Any]:
            if quantity < 1:
                raise ValueError("quantity must be at least 1")
            svc = get_services()
            try:
                q = await svc.mcp.call("supplier-inventory", "price_quote", {"sku": sku, "quantity": quantity})
            except McpCallError as exc:
                raise ValueError(f"Supplier catalogue unavailable: {exc}") from exc
            if not q.get("found"):
                raise ValueError(f"SKU {sku} is not in the {supplier} catalogue")
            async with svc.db.session() as s:
                if ticket_id and await s.get(m.Ticket, ticket_id) is None:
                    raise ValueError(f"Unknown ticket {ticket_id}")
                a = await actions.create_approval(
                    s,
                    kind="draft_supply_order",
                    title=f"Order {quantity} × {q['item']}",
                    summary=f"{quantity} {q['unit']} at ${q['unit_price']:.2f} = ${q['total']:,.2f} from {q['supplier']} "
                    f"(requested over MCP by {c.label}).",
                    payload={
                        "supplier": q["supplier"],
                        "item": q["item"],
                        "sku": q["sku"],
                        "quantity": quantity,
                        "unit": q["unit"],
                        "unitPrice": q["unit_price"],
                        "neededBy": needed_by or q.get("delivery_date"),
                        "ticketId": ticket_id,
                    },
                    requested_by=c.actor.display_name or c.actor.who,
                    run_id=f"mcp:{c.label}",
                    source="mcp",
                )
                return _approval_out(a)

        return await _run("draft_supply_order", ctx, go)

    @server.tool(
        description="Escalate an incident ticket to critical and notify the Kitchen Manager and station lead. Creates an approval request."
    )
    async def escalate_incident(
        ticket_id: str, severity: str = "food_safety", hold_product: bool = False, note: str = "", ctx: Context | None = None
    ) -> dict[str, Any]:
        async def go(c: Caller) -> dict[str, Any]:
            if severity not in ("food_safety", "equipment", "staffing"):
                raise ValueError("severity must be food_safety, equipment or staffing")
            svc = get_services()
            async with svc.db.session() as s:
                t = await s.get(m.Ticket, ticket_id)
                if t is None:
                    raise ValueError(f"Unknown ticket {ticket_id}")
                lk = await repo.lookup(s)
                km = (await s.exec(select(m.User).where(m.User.role == "kitchen_manager"))).first()
                notify = [x for x in [km.crew_member_id if km else None, lk.station_lead(t.station_id)] if x]
                a = await actions.create_approval(
                    s,
                    kind="escalate_incident",
                    title=f"Escalate {t.id}: {t.title[:60]}",
                    summary=f"{severity.replace('_', ' ').capitalize()} escalation requested over MCP by {c.label}. {note}".strip(),
                    payload={"ticketId": t.id, "severity": severity, "notify": notify, "holdProduct": hold_product, "note": note},
                    requested_by=c.actor.display_name or c.actor.who,
                    run_id=f"mcp:{c.label}",
                    source="mcp",
                )
                return _approval_out(a)

        return await _run("escalate_incident", ctx, go)

    return server


class McpBearerAuth:
    """ASGI middleware: reject /mcp requests without a valid bearer token before they reach the server."""

    def __init__(self, app, svc_getter=get_services) -> None:
        self.app = app
        self.svc_getter = svc_getter

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/mcp"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            await self._deny(scope, receive, send, "Missing bearer token")
            return
        try:
            await caller_from_token(self.svc_getter(), auth.split(" ", 1)[1].strip())
        except McpAuthError as exc:
            await self._deny(scope, receive, send, str(exc))
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _deny(scope, receive, send, message: str) -> None:
        resp = JSONResponse(
            {"error": "unauthorized", "detail": message}, status_code=401, headers={"WWW-Authenticate": 'Bearer realm="linemate-mcp"'}
        )
        await resp(scope, receive, send)


def main() -> None:
    """stdio entry point: `LINEMATE_MCP_TOKEN=lm_svc_... uv run linemate-mcp`.

    Starts the LineMate services (database seed, vector store) in this process, then serves MCP on stdio.
    """
    import anyio

    from back_end.main import startup_services

    async def run() -> None:
        svc = await startup_services(seed_vectors=False)
        try:
            server = create_mcp_server()
            await server.run_stdio_async()
        finally:
            await svc.aclose()

    anyio.run(run)
