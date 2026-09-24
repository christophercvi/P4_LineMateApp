"""Ask LineMate, shift triage, agent runs and graph diagrams.

Both agents stream Server-Sent Events. Event names: meta, step/node, sources, reasoning, content,
result, interrupt, done, error. Each `data:` line is JSON.
"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import delete
from sqlmodel import col, select

from back_end.agents import runner
from back_end.api.deps import ActorDep, SvcDep
from back_end.api.routers.models import models_for
from back_end.auth.permissions import Actor, can
from back_end.core.errors import BadRequest, Forbidden, NotFound, UpstreamUnavailable
from back_end.db import models as m
from back_end.schemas import (
    AskIn,
    ChatMessageOut,
    ChatUploadOut,
    ConversationDetailOut,
    ConversationOut,
    ConversationRenameIn,
    RunOut,
    TriageIn,
)
from back_end.services import repo
from back_end.services.container import Services
from back_end.services.ollama import ModelInfo
from back_end.services.storage import FileStore

router = APIRouter(prefix="/api", tags=["agent"])

CHAT_UPLOAD_KINDS = {"png", "jpg", "pdf", "docx", "txt", "md"}
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


async def _model(svc: Services, actor: Actor, name: str | None) -> ModelInfo:
    mine, up = await models_for(svc, actor)
    if not up:
        raise UpstreamUnavailable(f"Ollama is not reachable at {svc.settings.ollama_base_url}. Start it with `ollama serve` and try again.")
    if not mine:
        raise BadRequest("No chat models are available. Pull one with `ollama pull llama3.2:3b`.")
    if not name:
        return next((i for i in mine if i.name == svc.settings.chat_model), mine[0])
    info = next((i for i in mine if i.name == name), None)
    if info is None:
        raise Forbidden(f"The model {name} is not available for your role.")
    return info


@router.post("/ask/stream", summary="Ask LineMate (streams SSE)", response_class=StreamingResponse)
async def ask_stream(body: AskIn, actor: ActorDep, svc: SvcDep) -> StreamingResponse:
    if not can.ask(actor):
        raise Forbidden("Your role cannot use Ask LineMate.")
    info = await _model(svc, actor, body.model)
    attachments: list[dict] = []
    if body.attachments:
        async with svc.db.session() as s:
            for a in body.attachments:
                if not a.asset_id:
                    continue
                asset = await s.get(m.FileAsset, a.asset_id)
                if asset is None or asset.uploaded_by != actor.user_id:
                    raise NotFound(f"Attachment {a.name}")
                path: Path = svc.files.path_of(asset)
                attachments.append(
                    {
                        "assetId": asset.id,
                        "name": asset.original_name,
                        "kind": path.suffix.lstrip(".").lower(),
                        "path": str(path),
                        "url": FileStore.url(asset.id),
                        "bytes": asset.bytes,
                    }
                )
    stream = runner.stream_ask(svc, actor, body, info, attachments)
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/agent/triage/stream", summary="Run the triage agent (streams SSE)", response_class=StreamingResponse)
async def triage_stream(body: TriageIn, actor: ActorDep, svc: SvcDep) -> StreamingResponse:
    if not can.run_triage(actor):
        raise Forbidden("Triage is for Sous Chefs and the Kitchen Manager.")
    info = await _model(svc, actor, body.model)
    station = actor.station if actor.role == "sous_chef" else body.station
    return StreamingResponse(runner.stream_triage(svc, actor, info, station), media_type="text/event-stream", headers=SSE_HEADERS)


@router.post(
    "/chat/uploads", response_model=ChatUploadOut, status_code=status.HTTP_201_CREATED, summary="Attach a photo or file to a chat question"
)
async def chat_upload(
    actor: ActorDep,
    svc: SvcDep,
    file: Annotated[UploadFile, File()],
    conversation_id: Annotated[str, Form(alias="conversationId", min_length=1, max_length=40)],
) -> ChatUploadOut:
    if not can.ask(actor):
        raise Forbidden("Your role cannot use Ask LineMate.")
    data = await file.read()
    safe_conv = "".join(ch for ch in conversation_id if ch.isalnum() or ch in "-_")[:40] or "conversation"
    stored = svc.files.save(
        data=data,
        original_name=file.filename or "upload",
        purpose="chat",
        owner_type="conversation",
        owner_id=safe_conv,
        subdir=f"chat/{safe_conv}",
        uploaded_by=actor.user_id,
        allowed=CHAT_UPLOAD_KINDS,
    )
    async with svc.db.session() as s:
        s.add(stored.asset)
    return ChatUploadOut(
        asset_id=stored.asset.id,
        name=stored.asset.original_name,
        url=FileStore.url(stored.asset.id),
        bytes=stored.asset.bytes,
        kind=stored.kind,
    )


# ------------------------------------------------------------------------------------ conversations


async def _conversation(s, actor: Actor, conversation_id: str) -> m.Conversation:
    c = await s.get(m.Conversation, conversation_id)
    if c is None or c.user_id != actor.user_id:
        raise NotFound("Conversation")
    return c


@router.get("/conversations", response_model=list[ConversationOut], summary="The caller's conversations")
async def list_conversations(actor: ActorDep, svc: SvcDep) -> list[ConversationOut]:
    async with svc.db.session() as s:
        convs = (
            await s.exec(
                select(m.Conversation).where(m.Conversation.user_id == actor.user_id).order_by(col(m.Conversation.updated_at).desc())
            )
        ).all()
        msgs = await repo.all_rows(s, m.ChatMessage)
    counts: dict[str, int] = {}
    for msg in msgs:
        counts[msg.conversation_id] = counts.get(msg.conversation_id, 0) + 1
    return [
        ConversationOut(
            id=c.id, title=c.title, model=c.model, created_at=c.created_at, updated_at=c.updated_at, messages=counts.get(c.id, 0)
        )
        for c in convs
    ]


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut, summary="Messages and memory")
async def get_conversation(conversation_id: str, actor: ActorDep, svc: SvcDep) -> ConversationDetailOut:
    async with svc.db.session() as s:
        c = await _conversation(s, actor, conversation_id)
        msgs = (await s.exec(select(m.ChatMessage).where(m.ChatMessage.conversation_id == c.id).order_by(col(m.ChatMessage.id)))).all()
        count = len(msgs)
    return ConversationDetailOut(
        id=c.id,
        title=c.title,
        model=c.model,
        created_at=c.created_at,
        updated_at=c.updated_at,
        messages=count,
        memory_summary=c.memory_summary,
        history=[ChatMessageOut(role=x.role, content=x.content, extra=x.extra or {}, created_at=x.created_at) for x in msgs],
    )


@router.patch("/conversations/{conversation_id}", response_model=ConversationOut, summary="Rename a conversation")
async def rename_conversation(conversation_id: str, body: ConversationRenameIn, actor: ActorDep, svc: SvcDep) -> ConversationOut:
    async with svc.db.session() as s:
        c = await _conversation(s, actor, conversation_id)
        c.title = body.title.strip()
        s.add(c)
        count = len((await s.exec(select(m.ChatMessage).where(m.ChatMessage.conversation_id == c.id))).all())
        return ConversationOut(id=c.id, title=c.title, model=c.model, created_at=c.created_at, updated_at=c.updated_at, messages=count)


@router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a conversation and its memory")
async def delete_conversation(conversation_id: str, actor: ActorDep, svc: SvcDep) -> None:
    async with svc.db.session() as s:
        c = await _conversation(s, actor, conversation_id)
        await s.execute(delete(m.ChatMessage).where(m.ChatMessage.conversation_id == c.id))
        await s.delete(c)


# ------------------------------------------------------------------------------------ runs & graphs


@router.get("/agent/runs", response_model=list[RunOut], summary="Agent runs with node traces")
async def list_runs(actor: ActorDep, svc: SvcDep, limit: int = 100) -> list[RunOut]:
    if not can.view_agent_runs(actor):
        raise Forbidden("Agent runs are visible to Sous Chefs, the Kitchen Manager and Admins.")
    async with svc.db.session() as s:
        stmt = select(m.AgentRun).order_by(col(m.AgentRun.started_at).desc()).limit(min(limit, 500))
        if not can.view_all_runs(actor):
            stmt = stmt.where(m.AgentRun.user_id == actor.user_id)
        runs = (await s.exec(stmt)).all()
        who = await repo.names(s)
    return [repo.run_out(r, who) for r in runs]


@router.get("/agent/runs/{run_id}", response_model=RunOut, summary="One agent run")
async def get_run(run_id: str, actor: ActorDep, svc: SvcDep) -> RunOut:
    async with svc.db.session() as s:
        r = await s.get(m.AgentRun, run_id)
        if r is None:
            raise NotFound(f"Run {run_id}")
        if not (can.view_all_runs(actor) or (can.view_agent_runs(actor) and r.user_id == actor.user_id)):
            raise Forbidden("You can only open your own runs.")
        return repo.run_out(r, await repo.names(s))


@router.get("/agent/graphs", summary="Mermaid diagrams of the compiled LangGraph state graphs")
async def graphs(actor: ActorDep, svc: SvcDep) -> dict[str, str]:
    if not can.view_agent_runs(actor):
        raise Forbidden("Agent graphs are visible to Sous Chefs, the Kitchen Manager and Admins.")
    return {name: g.get_graph().draw_mermaid() for name, g in svc.graphs.items()}
