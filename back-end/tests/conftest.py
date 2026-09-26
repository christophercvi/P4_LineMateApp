"""Shared fixtures.

The tests never need a running Ollama server, GPU or network:

* Ollama's HTTP API is replaced by an httpx2.MockTransport (`FakeOllama`) that knows four chat
  models and the embedding model, and tracks which model is "loaded".
* Chat models are `FakeChatModel` instances that stream reasoning and cited answer tokens.
* Text embeddings are deterministic hashed bag-of-words vectors; photo embeddings hash file bytes.
* The external MCP servers run in-process; the triage planner uses Pydantic AI's TestModel.

Seed documents are parsed with unstructured on the first run and cached by file hash under
back-end/storage/.cache/parsed, so later runs are fast.
"""

import json
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic_ai.models.test import TestModel

from back_end.core.config import BACKEND_ROOT, Settings
from back_end.main import build_services, create_app
from back_end.services.container import Services
from back_end.services.embeddings import FakeVisionEmbedder, HashEmbeddings

PASSWORD = "Hearthline#2026"

# --------------------------------------------------------------------------- fake Ollama

MODELS: dict[str, dict[str, Any]] = {
    "llama3.2:3b": {
        "family": "llama",
        "size": 2_019_393_189,
        "params": "3.2B",
        "quant": "Q4_K_M",
        "caps": ["completion", "tools"],
        "ctx": 131072,
    },
    "qwen3:4b-q4_K_M": {
        "family": "qwen3",
        "size": 2_620_788_019,
        "params": "4.0B",
        "quant": "Q4_K_M",
        "caps": ["completion", "tools", "thinking"],
        "ctx": 40960,
    },
    "gemma4:e2b": {
        "family": "gemma4",
        "size": 4_600_000_000,
        "params": "5.1B",
        "quant": "Q4_K_M",
        "caps": ["completion", "vision", "tools", "thinking"],
        "ctx": 131072,
    },
    "phi4-mini:latest": {
        "family": "phi3",
        "size": 2_500_000_000,
        "params": "3.8B",
        "quant": "Q4_K_M",
        "caps": ["completion"],
        "ctx": 131072,
    },
    "nomic-embed-text:latest": {
        "family": "nomic-bert",
        "size": 274_302_450,
        "params": "137M",
        "quant": "F16",
        "caps": ["embedding"],
        "ctx": 2048,
    },
}


class FakeOllama:
    """Minimal Ollama HTTP API: /api/version, /api/tags, /api/show, /api/ps, /api/generate."""

    def __init__(self) -> None:
        self.models = dict(MODELS)
        self.loaded: set[str] = set()
        self.requests: list[tuple[str, str, dict]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content or b"{}") if request.content else {}
        path = request.url.path
        self.requests.append((request.method, path, body))
        if path == "/api/version":
            return httpx2.Response(200, json={"version": "0.35.0"})
        if path == "/api/tags":
            return httpx2.Response(
                200,
                json={
                    "models": [
                        {
                            "name": n,
                            "model": n,
                            "size": m["size"],
                            "modified_at": "2026-09-30T12:00:00Z",
                            "details": {"family": m["family"], "parameter_size": m["params"], "quantization_level": m["quant"]},
                        }
                        for n, m in self.models.items()
                    ]
                },
            )
        if path == "/api/show":
            m = self.models.get(body.get("model", ""))
            if m is None:
                return httpx2.Response(404, json={"error": "model not found"})
            return httpx2.Response(
                200,
                json={
                    "capabilities": m["caps"],
                    "details": {"family": m["family"], "parameter_size": m["params"], "quantization_level": m["quant"]},
                    "model_info": {f"{m['family']}.context_length": m["ctx"]},
                },
            )
        if path == "/api/ps":
            return httpx2.Response(
                200, json={"models": [{"name": n, "model": n, "expires_at": "2099-01-01T00:00:00Z"} for n in sorted(self.loaded)]}
            )
        if path == "/api/generate":
            name = body.get("model", "")
            if body.get("keep_alive") == 0:
                self.loaded.discard(name)
            elif name in self.models:
                self.loaded.add(name)
            else:
                return httpx2.Response(404, json={"error": "model not found"})
            return httpx2.Response(200, json={"model": name, "done": True, "response": ""})
        return httpx2.Response(404, json={"error": f"unknown path {path}"})


# --------------------------------------------------------------------------- fake chat model

DEFAULT_ANSWER = "Follow the documented procedure and log it [1]. Tell the Sous Chef if anything is off [1]."


class FakeChatModel(BaseChatModel):
    """Streams optional reasoning (as Ollama's reasoning_content) followed by a cited answer."""

    model: str = "llama3.2:3b"
    reasoning: bool | str | None = None
    answer: str = DEFAULT_ANSWER
    thought: str = "The context covers this. I should cite source 1."
    fail: bool = False

    @property
    def _llm_type(self) -> str:
        return "fake-ollama"

    def _tokens(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        if self.reasoning:
            out += [("reasoning", w + " ") for w in self.thought.split()]
        out += [("content", w + " ") for w in self.answer.split()]
        return out

    def _generate(
        self, messages: list[BaseMessage], stop: list[str] | None = None, run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any
    ) -> ChatResult:
        if self.fail:
            raise ConnectionError("Ollama is not reachable")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.answer))])

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        if self.fail:
            raise ConnectionError("Ollama is not reachable")
        tokens = self._tokens()
        for kind, text in tokens:
            if kind == "reasoning":
                msg = AIMessageChunk(content="", additional_kwargs={"reasoning_content": text})
            else:
                msg = AIMessageChunk(content=text)
            yield ChatGenerationChunk(message=msg)
        n = len(tokens)
        yield ChatGenerationChunk(
            message=AIMessageChunk(content="", usage_metadata={"input_tokens": 120, "output_tokens": n, "total_tokens": 120 + n})
        )


class ChatRecorder:
    """Chat factory used by the app under test. Records every model request."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.answer = DEFAULT_ANSWER
        self.fail = False

    def __call__(
        self,
        model: str,
        *,
        reasoning: bool | str | None = None,
        temperature: float = 0.2,
        num_predict: int | None = None,
        json_mode: bool = False,
    ) -> BaseChatModel:
        self.calls.append({"model": model, "reasoning": reasoning, "num_predict": num_predict, "json_mode": json_mode})
        return FakeChatModel(model=model, reasoning=reasoning, answer=self.answer, fail=self.fail)


# --------------------------------------------------------------------------- settings & services


def make_settings(tmp: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "environment": "test",
        "log_level": "warning",
        "database_url": "sqlite+aiosqlite:///:memory:",
        "jwt_secret": "test-secret-0123456789-abcdefghijklmnop",
        "storage_dir": tmp / "storage",
        "chroma_dir": tmp / "chroma",
        "parse_cache_dir": BACKEND_ROOT / "storage" / ".cache" / "parsed",
        "ollama_base_url": "http://ollama.test",
        "seed_on_startup": True,
        "vectorize_on_startup": True,
        "mcp_external_enabled": True,
        "vision_enabled": True,
        "max_upload_mb": 5,
        # hashed test embeddings score lower than nomic, so relax the relevance floors
        "min_relevance": 0.05,
        "score_threshold": 0.1,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def make_factory(ollama: FakeOllama, chat: ChatRecorder):
    def factory(settings: Settings) -> Services:
        svc = build_services(
            settings,
            text_embeddings=HashEmbeddings(256),
            vision=FakeVisionEmbedder(),
            chat=chat,
            mcp_mode="inprocess",
            ollama_transport=httpx2.MockTransport(ollama),
        )
        svc.planner_model = TestModel(call_tools=[])
        return svc

    return factory


@pytest.fixture(scope="session")
def fake_ollama() -> FakeOllama:
    return FakeOllama()


@pytest.fixture(scope="session")
def chat_recorder() -> ChatRecorder:
    return ChatRecorder()


@pytest.fixture(scope="session")
def app_client(tmp_path_factory, fake_ollama, chat_recorder) -> Iterator[TestClient]:
    """One seeded application for the integration tests (seeding + vectorizing runs once)."""
    tmp = tmp_path_factory.mktemp("linemate")
    settings = make_settings(tmp)
    app = create_app(settings, factory=make_factory(fake_ollama, chat_recorder))
    with TestClient(app) as client:
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            state = client.get("/api/health").json()["seed"]["state"]
            if state in ("ready", "failed"):
                break
            time.sleep(0.25)
        assert state == "ready", client.get("/api/health").json()
        yield client


@pytest.fixture(scope="session")
def services(app_client) -> Services:
    return app_client.app.state.services


@pytest.fixture(autouse=True)
def _reset_chat(chat_recorder) -> Iterator[None]:
    chat_recorder.answer = DEFAULT_ANSWER
    chat_recorder.fail = False
    yield


# --------------------------------------------------------------------------- auth helpers

_TOKENS: dict[str, str] = {}


def login(client: TestClient, username: str, password: str = PASSWORD) -> str:
    if username not in _TOKENS:
        r = client.post("/api/auth/login", json={"username": username, "password": password})
        assert r.status_code == 200, r.text
        _TOKENS[username] = r.json()["token"]
    return _TOKENS[username]


def auth(client: TestClient, username: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {login(client, username)}"}


@pytest.fixture(scope="session")
def as_cook(app_client) -> dict[str, str]:
    return auth(app_client, "marco")


@pytest.fixture(scope="session")
def as_sous(app_client) -> dict[str, str]:
    return auth(app_client, "priya")


@pytest.fixture(scope="session")
def as_prep_sous(app_client) -> dict[str, str]:
    return auth(app_client, "samuel")


@pytest.fixture(scope="session")
def as_manager(app_client) -> dict[str, str]:
    return auth(app_client, "elena")


@pytest.fixture(scope="session")
def as_admin(app_client) -> dict[str, str]:
    return auth(app_client, "alex.admin")


# --------------------------------------------------------------------------- SSE helper


def read_sse(client: TestClient, url: str, body: dict, headers: dict[str, str]) -> list[tuple[str, Any]]:
    """POST and collect (event, data) pairs from a text/event-stream response."""
    events: list[tuple[str, Any]] = []
    with client.stream("POST", url, json=body, headers=headers) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("text/event-stream")
        event, data = "message", []
        for line in r.iter_lines():
            if line == "":
                if data:
                    raw = "\n".join(data)
                    try:
                        value: Any = json.loads(raw)
                    except json.JSONDecodeError:
                        value = raw
                    events.append((event, value))
                event, data = "message", []
            elif line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
    return events


def of(events: list[tuple[str, Any]], name: str) -> list[Any]:
    return [d for e, d in events if e == name]
