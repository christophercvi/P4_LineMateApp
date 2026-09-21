"""Talks to the local Ollama server.

* The model list is dynamic: every model returned by `/api/tags` is shown, with its real
  capabilities from `/api/show`. The three supported models are flagged and listed first.
* Which models stay in memory is Ollama's decision (OLLAMA_MAX_LOADED_MODELS, keep_alive on
  the Ollama server); the app only warms the selected model before streaming.
"""

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Literal

import httpx2

from back_end.core.logging import get_logger

log = get_logger(__name__)

ThinkingMode = Literal["none", "toggle", "always"]

PURPOSE = {
    "llama3.2:3b": "Fast everyday answers and tool calling",
    "qwen3:4b-q4_K_M": "Step-by-step reasoning (thinking on or off)",
    "gemma4:e2b": "Reads photos and documents; thinking on or off",
    "nomic-embed-text": "Text embeddings for document search",
    "nomic-embed-text:latest": "Text embeddings for document search",
}
ALWAYS_THINK_PREFIXES = ("deepseek-r1", "gpt-oss", "qwq", "magistral")


@dataclass(slots=True)
class ModelInfo:
    name: str
    family: str
    parameters: str
    quantization: str
    size_gb: float
    context_length: int
    capabilities: list[str]
    thinking: ThinkingMode
    loaded: bool
    keep_alive: str
    purpose: str
    supported: bool
    embedding: bool = False
    modified_at: str | None = None
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        d.pop("extra", None)
        return d


def thinking_mode(name: str, capabilities: list[str]) -> ThinkingMode:
    if "thinking" not in capabilities:
        return "none"
    base = name.split(":")[0]
    # qwen3:4b (no suffix) now points at Qwen3-4B-Thinking-2507, which always reasons.
    if base.startswith(ALWAYS_THINK_PREFIXES) or "thinking" in name or name in ("qwen3:4b", "qwen3:4b-thinking"):
        return "always"
    return "toggle"


def _same(a: str, b: str) -> bool:
    norm = lambda n: n if ":" in n else f"{n}:latest"  # noqa: E731
    return norm(a) == norm(b)


class OllamaService:
    def __init__(
        self,
        base_url: str,
        *,
        supported: list[str],
        keep_alive: str = "15m",
        timeout: float = 600.0,
        transport: httpx2.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.supported = supported
        self.keep_alive = keep_alive
        self._client = httpx2.AsyncClient(base_url=self.base_url, timeout=httpx2.Timeout(timeout, connect=5.0), transport=transport)
        self._cache: tuple[float, list[ModelInfo]] | None = None
        self._show_cache: dict[str, dict] = {}

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ raw endpoints

    async def version(self) -> str | None:
        try:
            r = await self._client.get("/api/version", timeout=3.0)
            r.raise_for_status()
            return r.json().get("version")
        except httpx2.HTTPError:
            return None

    async def tags(self) -> list[dict]:
        r = await self._client.get("/api/tags", timeout=10.0)
        r.raise_for_status()
        return r.json().get("models", [])

    async def show(self, name: str) -> dict:
        if name not in self._show_cache:
            r = await self._client.post("/api/show", json={"model": name}, timeout=15.0)
            r.raise_for_status()
            self._show_cache[name] = r.json()
        return self._show_cache[name]

    async def ps(self) -> list[dict]:
        r = await self._client.get("/api/ps", timeout=5.0)
        r.raise_for_status()
        return r.json().get("models", [])

    # ------------------------------------------------------------------ registry

    async def list_models(self, *, force: bool = False) -> list[ModelInfo]:
        if not force and self._cache and time.monotonic() - self._cache[0] < 20:
            return await self._with_loaded(self._cache[1])
        tags = await self.tags()
        infos: list[ModelInfo] = []
        for tag in tags:
            name = tag["name"]
            try:
                show = await self.show(name)
            except httpx2.HTTPError:
                show = {}
            details = show.get("details") or tag.get("details") or {}
            caps = list(show.get("capabilities") or ["completion"])
            model_info = show.get("model_info") or {}
            ctx = next((int(v) for k, v in model_info.items() if k.endswith(".context_length")), 0)
            embedding = "embedding" in caps or "embed" in name
            infos.append(
                ModelInfo(
                    name=name,
                    family=str(details.get("family", "")),
                    parameters=str(details.get("parameter_size", "")),
                    quantization=str(details.get("quantization_level", "")),
                    size_gb=round(tag.get("size", 0) / 1e9, 2),
                    context_length=ctx,
                    capabilities=caps,
                    thinking=thinking_mode(name, caps),
                    loaded=False,
                    keep_alive=self.keep_alive,
                    purpose=PURPOSE.get(name, self._purpose(caps)),
                    supported=any(_same(name, s) for s in self.supported),
                    embedding=embedding,
                    modified_at=tag.get("modified_at"),
                )
            )
        order = {s: i for i, s in enumerate(self.supported)}
        infos.sort(key=lambda m: (m.embedding, order.get(m.name, 99), m.name))
        self._cache = (time.monotonic(), infos)
        return await self._with_loaded(infos)

    async def chat_models(self) -> list[ModelInfo]:
        return [m for m in await self.list_models() if not m.embedding and "completion" in m.capabilities]

    async def get(self, name: str) -> ModelInfo | None:
        return next((m for m in await self.list_models() if _same(m.name, name)), None)

    async def _with_loaded(self, infos: list[ModelInfo]) -> list[ModelInfo]:
        try:
            running = await self.ps()
        except httpx2.HTTPError:
            running = []
        by_name = {r["name"]: r for r in running}
        out = []
        for m in infos:
            r = next((v for k, v in by_name.items() if _same(k, m.name)), None)
            keep = self.keep_alive
            if r and r.get("expires_at"):
                keep = _remaining(r["expires_at"]) or keep
            out.append(ModelInfo(**{**asdict(m), "loaded": r is not None, "keep_alive": keep}))
        return out

    @staticmethod
    def _purpose(caps: list[str]) -> str:
        if "embedding" in caps:
            return "Embeddings"
        bits = ["chat"]
        if "vision" in caps:
            bits.append("photos")
        if "thinking" in caps:
            bits.append("reasoning")
        if "tools" in caps:
            bits.append("tools")
        return "Available locally: " + ", ".join(bits)

    # ------------------------------------------------------------------ load control

    async def unload(self, name: str) -> None:
        await self._client.post("/api/generate", json={"model": name, "keep_alive": 0}, timeout=60.0)

    async def ensure_loaded(self, name: str) -> dict:
        """Warm the model before streaming so the UI can show a separate "Loading model" step.
        Ollama decides what else stays in memory (OLLAMA_MAX_LOADED_MODELS on the Ollama server)."""
        started = time.perf_counter()
        running = await self.ps()
        already = any(_same(r["name"], name) for r in running)
        if not already:
            r = await self._client.post("/api/generate", json={"model": name, "keep_alive": self.keep_alive}, timeout=600.0)
            r.raise_for_status()
            log.info("ollama_loaded", model=name, ms=int((time.perf_counter() - started) * 1000))
            self._cache = None
        return {"model": name, "was_loaded": already, "load_ms": int((time.perf_counter() - started) * 1000)}


def _remaining(expires_at: str) -> str | None:
    try:
        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    secs = int((exp - datetime.now(exp.tzinfo)).total_seconds())
    if secs <= 0:
        return "unloading"
    if secs > 10**8:
        return "forever"
    return f"{secs // 60}m" if secs >= 60 else f"{secs}s"
