"""Embeddings.

* Text: `nomic-embed-text` (v1.5) served by Ollama, wrapped so documents get the
  `search_document: ` prefix and queries get `search_query: `, as the model card requires.
* Photos: `nomic-ai/nomic-embed-vision-v1.5` runs in-process (PyTorch + Transformers, CPU),
  not in Ollama. It shares an embedding space with nomic-embed-text-v1.5, so a typed question
  can find a photo. Text-to-image cosine scores sit far below text-to-text scores, so photos
  live in their own Chroma collection and are ranked separately.
"""

import asyncio
import hashlib
import threading
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from langchain_core.embeddings import Embeddings

from back_end.core.logging import get_logger

log = get_logger(__name__)

DOC_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


def _seconds(value: str | int) -> int:
    """'30m' / '2h' / '45s' / 900 -> seconds (OllamaEmbeddings takes an int)."""
    if isinstance(value, int):
        return value
    v = value.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600}
    if v and v[-1] in units:
        return int(float(v[:-1]) * units[v[-1]])
    return int(v)


class NomicTextEmbeddings(Embeddings):
    def __init__(self, base_url: str, model: str = "nomic-embed-text", keep_alive: str = "30m"):
        from langchain_ollama import OllamaEmbeddings

        self.model = model
        self._inner = OllamaEmbeddings(model=model, base_url=base_url, keep_alive=_seconds(keep_alive))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._inner.embed_documents([DOC_PREFIX + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._inner.embed_query(QUERY_PREFIX + text)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._inner.aembed_documents([DOC_PREFIX + t for t in texts])

    async def aembed_query(self, text: str) -> list[float]:
        return await self._inner.aembed_query(QUERY_PREFIX + text)


class HashEmbeddings(Embeddings):
    """Deterministic bag-of-words embeddings for tests and offline development (no Ollama)."""

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.model = f"hash-{dim}"

    def _vec(self, text: str) -> list[float]:
        v = np.zeros(self.dim, dtype=np.float32)
        for word in text.lower().split():
            token = "".join(ch for ch in word if ch.isalnum())
            if not token or token in {"search_document", "search_query"}:
                continue
            h = int(hashlib.md5(token.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0
        n = np.linalg.norm(v)
        return (v / n if n else v).tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class VisionEmbedder:
    """Lazy, thread-safe wrapper around nomic-embed-vision-v1.5 (768-d, L2-normalised CLS token)."""

    dimensions = 768

    def __init__(self, model_name: str = "nomic-ai/nomic-embed-vision-v1.5", enabled: bool = True):
        self.model_name = model_name
        self.enabled = enabled
        self._model = None
        self._processor = None
        self._lock = threading.Lock()
        self.error: str | None = None

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self) -> None:
        if self._model is not None or not self.enabled:
            return
        with self._lock:
            if self._model is not None:
                return
            try:
                import torch
                from transformers import AutoImageProcessor, AutoModel

                torch.set_num_threads(max(1, min(4, torch.get_num_threads())))
                self._processor = AutoImageProcessor.from_pretrained(self.model_name)
                self._model = AutoModel.from_pretrained(self.model_name, trust_remote_code=True).eval()
                log.info("vision_model_loaded", model=self.model_name)
            except Exception as exc:  # noqa: BLE001 - model download/loading is optional at runtime
                self.error = f"{type(exc).__name__}: {exc}"
                self.enabled = False
                log.warning("vision_model_unavailable", error=self.error)

    def embed_images(self, paths: Sequence[Path]) -> list[list[float]]:
        self._load()
        if self._model is None:
            raise RuntimeError(self.error or "vision embedding disabled")
        import torch
        import torch.nn.functional as F
        from PIL import Image

        images = [Image.open(p).convert("RGB") for p in paths]
        with torch.inference_mode():
            inputs = self._processor(images, return_tensors="pt")
            out = self._model(**inputs).last_hidden_state
            vecs = F.normalize(out[:, 0], p=2, dim=1)
        return vecs.cpu().numpy().astype(float).tolist()

    async def aembed_images(self, paths: Sequence[Path]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed_images, paths)


class FakeVisionEmbedder(VisionEmbedder):
    """Used by tests: hashes file bytes into a stable unit vector."""

    def __init__(self):
        super().__init__(enabled=True)
        self._model = object()

    def embed_images(self, paths: Sequence[Path]) -> list[list[float]]:
        out = []
        for p in paths:
            seed = int(hashlib.sha256(Path(p).read_bytes()).hexdigest()[:8], 16)
            v = np.random.default_rng(seed).normal(size=self.dimensions)
            out.append((v / np.linalg.norm(v)).tolist())
        return out
