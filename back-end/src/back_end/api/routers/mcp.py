"""MCP console: the tools LineMate exposes at /mcp, the external MCP servers it consumes, a
"Try it" runner, and service tokens for MCP clients (argon2-hashed; the plain token is shown once)."""

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Request, status
from mcp.client.client import Client
from sqlmodel import col, func, select

from back_end.api.deps import ActorDep, SvcDep
from back_end.auth.permissions import can
from back_end.core.errors import Forbidden, NotFound
from back_end.core.security import hash_password, new_service_token
from back_end.core.timeutil import utcnow
from back_end.db import models as m
from back_end.mcp_server import TOOL_META, Caller, inprocess_caller
from back_end.schemas import (
    McpOut,
    McpRemoteTool,
    McpServerOut,
    McpToolOut,
    McpTryIn,
    McpTryOut,
    ServiceTokenCreatedOut,
    ServiceTokenCreateIn,
    ServiceTokenOut,
)
from back_end.services import repo
from back_end.services.container import Services
from back_end.services.mcp_clients import SERVERS

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@lru_cache(maxsize=1)
def _samples() -> dict[str, dict]:
    from back_end.core.config import get_settings

    path = get_settings().seed_data_dir / "mcp_tools.json"
    try:
        return {t["name"]: t.get("sampleInput") or {} for t in json.loads(path.read_text())}
    except (OSError, ValueError):
        return {}


def token_out(t: m.ServiceToken) -> ServiceTokenOut:
    return ServiceTokenOut(
        id=t.id,
        name=t.name,
        client=t.client,
        scopes=t.scopes or [],
        prefix=t.prefix,
        created_at=t.created_at,
        last_used=t.last_used,
        expires_at=t.expires_at,
        revoked=t.revoked,
    )


async def _refresh_servers(svc: Services) -> None:
    stale = [sid for sid, sv in SERVERS.items() if not sv.last_seen or time.time() - sv.last_seen > 120]
    if stale and svc.settings.mcp_external_enabled:
        await asyncio.gather(*(svc.mcp.refresh(sid) for sid in stale), return_exceptions=True)


@router.get("", response_model=McpOut, summary="Exposed tools, consumed servers and tokens")
async def console(request: Request, actor: ActorDep, svc: SvcDep) -> McpOut:
    if not can.view_mcp(actor):
        raise Forbidden("The MCP console is for Sous Chefs, the Kitchen Manager and Admins.")
    server = request.app.state.mcp_server
    listed = {t.name: t for t in await server.list_tools()}
    since = utcnow() - timedelta(days=7)
    async with svc.db.session() as s:
        counts = dict(
            (await s.exec(select(m.McpToolCall.tool, func.count()).where(m.McpToolCall.at >= since).group_by(m.McpToolCall.tool))).all()
        )
        tokens = (await s.exec(select(m.ServiceToken).order_by(col(m.ServiceToken.created_at).desc()))).all()
    await _refresh_servers(svc)
    tools = []
    for name, meta in TOOL_META.items():
        t = listed.get(name)
        tools.append(
            McpToolOut(
                name=name,
                description=(t.description if t else "") or "",
                access=meta.access,
                requires_approval=meta.requires_approval,
                roles=list(meta.roles),
                input_schema=(t.input_schema if t else {}) or {},
                sample_input=_samples().get(name, {}),
                calls_last7d=int(counts.get(name, 0)),
            )
        )
    servers = [
        McpServerOut(
            id=sv.id,
            name=sv.name,
            url=f"stdio: python -m {sv.module}",
            transport="stdio",
            status=sv.status,
            last_seen=datetime.fromtimestamp(sv.last_seen, UTC) if sv.last_seen else None,
            latency_ms=sv.latency_ms,
            tools=[McpRemoteTool(**t) for t in sv.tools],
        )
        for sv in SERVERS.values()
    ]
    return McpOut(
        tools=tools,
        servers=servers,
        tokens=[token_out(t) for t in tokens] if can.manage_tokens(actor) else None,
        endpoint=str(request.base_url).rstrip("/") + "/mcp",
    )


@router.post("/tools/{name}/try", response_model=McpTryOut, summary="Call a tool as the signed-in user")
async def try_tool(name: str, body: McpTryIn, request: Request, actor: ActorDep, svc: SvcDep) -> McpTryOut:
    meta = TOOL_META.get(name)
    if meta is None:
        raise NotFound(f"Tool {name}")
    if not can.view_mcp(actor):
        raise Forbidden("The MCP console is for Sous Chefs, the Kitchen Manager and Admins.")
    if not can.call_mcp_tool(actor, meta.roles):
        raise Forbidden(f"{name} is not available to your role.")
    args: dict[str, Any] = body.input if body.input is not None else _samples().get(name, {})
    started = time.perf_counter()
    token = inprocess_caller.set(Caller(actor, ("mcp:read", "mcp:write"), f"{actor.display_name} (try it)"))
    try:
        async with Client(request.app.state.mcp_server) as client:
            res = await client.call_tool(name, args)
    finally:
        inprocess_caller.reset(token)
    latency = int((time.perf_counter() - started) * 1000)
    text = "\n".join(getattr(c, "text", "") for c in res.content or [])
    output: Any = res.structured_content
    if isinstance(output, dict) and set(output) == {"result"}:
        output = output["result"]
    if output is None:
        try:
            output = json.loads(text)
        except ValueError:
            output = text
    if res.is_error:
        return McpTryOut(
            status="error",
            requires_approval=meta.requires_approval,
            input=args,
            output=None,
            message=text or "Tool call failed",
            latency_ms=latency,
        )
    interrupted = isinstance(output, dict) and output.get("status") == "interrupted"
    return McpTryOut(
        status="interrupted" if interrupted else "ok",
        requires_approval=meta.requires_approval,
        input=args,
        output=output,
        message=output.get("message") if interrupted else None,
        latency_ms=latency,
    )


def _tokens_admin(actor) -> None:
    if not can.manage_tokens(actor):
        raise Forbidden("Service tokens are managed by Admins.")


@router.post(
    "/tokens", response_model=ServiceTokenCreatedOut, status_code=status.HTTP_201_CREATED, summary="Create a service token (shown once)"
)
async def create_token(body: ServiceTokenCreateIn, actor: ActorDep, svc: SvcDep) -> ServiceTokenCreatedOut:
    _tokens_admin(actor)
    plain, prefix = new_service_token()
    async with svc.db.session() as s:
        row = m.ServiceToken(
            id=await repo.next_id(s, "service_token", "tok-", 1),
            name=body.name.strip(),
            client=body.client.strip(),
            scopes=sorted(set(body.scopes)),
            prefix=prefix,
            token_hash=hash_password(plain),
            user_id="u-svc-mcp",
            expires_at=utcnow() + timedelta(days=body.days),
        )
        s.add(row)
        await s.flush()
        return ServiceTokenCreatedOut(**token_out(row).model_dump(), token=plain)


@router.post("/tokens/{token_id}/revoke", response_model=ServiceTokenOut, summary="Revoke a service token")
async def revoke_token(token_id: str, actor: ActorDep, svc: SvcDep) -> ServiceTokenOut:
    _tokens_admin(actor)
    async with svc.db.session() as s:
        row = await s.get(m.ServiceToken, token_id)
        if row is None:
            raise NotFound(f"Token {token_id}")
        row.revoked = True
        s.add(row)
        return token_out(row)
