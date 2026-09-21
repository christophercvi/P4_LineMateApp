"""Application-wide services, created once in the FastAPI lifespan (and replaced with fakes in tests)."""

import asyncio
import contextvars
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.memory import InMemorySaver

from back_end.core.config import Settings
from back_end.core.logging import get_logger
from back_end.db.engine import Database
from back_end.services.embeddings import VisionEmbedder
from back_end.services.mcp_clients import ExternalMcp
from back_end.services.ollama import OllamaService
from back_end.services.storage import FileStore
from back_end.services.vectorstore import VectorStore

log = get_logger(__name__)

ChatFactory = Callable[..., BaseChatModel]


def ollama_chat_factory(settings: Settings) -> ChatFactory:
    def make(
        model: str, *, reasoning: bool | None = None, temperature: float = 0.2, num_predict: int | None = None, json_mode: bool = False
    ) -> BaseChatModel:
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=model,
            base_url=settings.ollama_base_url,
            reasoning=reasoning,
            temperature=temperature,
            num_ctx=settings.num_ctx,
            num_predict=num_predict or settings.max_answer_tokens,
            keep_alive=settings.ollama_keep_alive,
            format="json" if json_mode else None,
            client_kwargs={"timeout": settings.ollama_timeout_seconds},
        )

    return make


@dataclass
class Services:
    settings: Settings
    db: Database
    files: FileStore
    vectors: VectorStore
    ollama: OllamaService
    text_embeddings: Embeddings
    vision: VisionEmbedder
    chat: ChatFactory
    mcp: ExternalMcp = field(default_factory=ExternalMcp)
    graphs: dict[str, Any] = field(default_factory=dict)
    planner_model: Any | None = None  # Pydantic AI model override for the triage planner (tests)
    checkpointer: InMemorySaver = field(default_factory=InMemorySaver)
    reindex: dict[str, Any] | None = None
    seed_status: dict[str, Any] = field(default_factory=lambda: {"state": "idle"})
    _tasks: set[asyncio.Task] = field(default_factory=set)

    def spawn(self, coro: Coroutine[Any, Any, Any], name: str | None = None) -> asyncio.Task:
        """Run work in the background with a fresh context (no inherited database session)."""
        task = asyncio.get_running_loop().create_task(coro, name=name, context=contextvars.Context())
        self._tasks.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("background_task_failed", task=task.get_name(), error=repr(task.exception()))

    async def drain(self, timeout: float = 120.0) -> None:
        """Wait for background work (tests use this after uploads)."""
        while self._tasks:
            await asyncio.wait(list(self._tasks), timeout=timeout)

    async def aclose(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await self.ollama.aclose()


_services: Services | None = None


def set_services(s: Services | None) -> None:
    global _services
    _services = s


def get_services() -> Services:
    if _services is None:
        raise RuntimeError("Services are not initialised")
    return _services
