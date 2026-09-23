"""Per-conversation memory stored in SQLite.

The last `memory_window` exchanges are passed to the model verbatim; anything older is folded
into a running summary (written by the same chat model, in the background after an answer) so
long conversations keep their context without overflowing the model's window.
"""

from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage
from sqlmodel import col, select

from back_end.core.logging import get_logger
from back_end.core.timeutil import utcnow
from back_end.db import models as m
from back_end.services.container import Services

log = get_logger(__name__)

SUMMARY_PROMPT = (
    "You maintain the memory of a kitchen assistant conversation. Merge the existing summary and the "
    "new exchanges into one short summary (max 120 words). Keep ticket ids, document titles, "
    "temperatures, quantities and decisions. Plain sentences, no preamble."
)


@dataclass(slots=True)
class Memory:
    summary: str
    turns: list[dict]  # [{"role": "user"|"assistant", "content": str}]
    total_messages: int


async def load(svc: Services, conversation_id: str, user_id: str) -> Memory:
    async with svc.db.session() as s:
        conv = await s.get(m.Conversation, conversation_id)
        if conv is None or conv.user_id != user_id:
            return Memory("", [], 0)
        rows = list(
            (
                await s.exec(select(m.ChatMessage).where(m.ChatMessage.conversation_id == conversation_id).order_by(col(m.ChatMessage.id)))
            ).all()
        )
    keep = svc.settings.memory_window * 2
    recent = rows[-keep:] if keep else []
    turns = [{"role": r.role, "content": r.content} for r in recent if r.content and not (r.extra or {}).get("aborted")]
    return Memory(conv.memory_summary, turns, len(rows))


async def save_exchange(
    svc: Services,
    *,
    conversation_id: str,
    user_id: str,
    model: str,
    question: str,
    answer: str,
    user_extra: dict,
    assistant_extra: dict,
) -> None:
    async with svc.db.session() as s:
        conv = await s.get(m.Conversation, conversation_id)
        if conv is None:
            title = question.strip().splitlines()[0][:60] or "New conversation"
            conv = m.Conversation(id=conversation_id, user_id=user_id, title=title, model=model)
            s.add(conv)
            await s.flush()
        elif conv.user_id != user_id:
            return
        conv.model = model
        conv.updated_at = utcnow()
        s.add(conv)
        s.add(m.ChatMessage(conversation_id=conversation_id, role="user", content=question, extra=user_extra))
        s.add(m.ChatMessage(conversation_id=conversation_id, role="assistant", content=answer, extra=assistant_extra))


async def maybe_summarize(svc: Services, conversation_id: str, model: str) -> None:
    """Fold messages that fell out of the window into the summary."""
    keep = svc.settings.memory_window * 2
    async with svc.db.session() as s:
        conv = await s.get(m.Conversation, conversation_id)
        if conv is None:
            return
        rows = list(
            (
                await s.exec(select(m.ChatMessage).where(m.ChatMessage.conversation_id == conversation_id).order_by(col(m.ChatMessage.id)))
            ).all()
        )
        folded, summary = conv.memory_folded, conv.memory_summary
    overflow = rows[: max(0, len(rows) - keep)]
    new = overflow[folded:]
    if len(new) < 2:
        return
    transcript = "\n".join(f"{r.role}: {r.content[:800]}" for r in new)
    try:
        chat = svc.chat(model, reasoning=False if "thinking" in await _caps(svc, model) else None, temperature=0.1, num_predict=220)
        msg = await chat.ainvoke(
            [
                SystemMessage(SUMMARY_PROMPT),
                HumanMessage(f"Existing summary:\n{summary or '(none)'}\n\nNew exchanges:\n{transcript}"),
            ]
        )
        text = str(msg.content).strip()
    except Exception as exc:  # noqa: BLE001 - memory is best effort
        log.warning("memory_summary_failed", conversation=conversation_id, error=str(exc))
        return
    async with svc.db.session() as s:
        conv = await s.get(m.Conversation, conversation_id)
        if conv:
            conv.memory_summary = text
            conv.memory_folded = folded + len(new)
            s.add(conv)
    log.info("memory_summarized", conversation=conversation_id, folded=folded + len(new))


async def _caps(svc: Services, model: str) -> list[str]:
    info = await svc.ollama.get(model)
    return info.capabilities if info else []
