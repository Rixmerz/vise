"""fastembed-based embedding service.

Singleton client with lazy model loading + idle unload. The model runs
in-process via ONNX Runtime; no daemon, no container, no HTTP.

Default model: BAAI/bge-small-en-v1.5 (384D, ~150 MB RAM). Override via
`VISE_EMBED_MODEL` (preferred) or `JIG_EMBED_MODEL` (legacy fallback).

Idle unload: after `VISE_EMBED_IDLE_TIMEOUT` (or `JIG_EMBED_IDLE_TIMEOUT`)
seconds (default 600) without a call to `embed_one`/`embed_many`, the
loaded model is released so its memory is reclaimable. Set to `0` to
disable unloading.

Usage:
    from vise.core.embeddings import get_embedder
    emb = get_embedder()
    vec = emb.embed_one("some text")              # list[float], length = emb.dim
    vecs = emb.embed_many(["a", "b"])             # list[list[float]]

Availability:
    emb.available  → False if fastembed is not installed; embed_* returns None.
    Callers must handle None gracefully (fall back to BM25 or skip semantic scoring).
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

log = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_IDLE_TIMEOUT = 600.0
MODEL_DIMS: dict[str, int] = {
    "BAAI/bge-large-en-v1.5": 1024,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-small-en-v1.5": 384,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
}


def _env(suffix: str) -> str | None:
    """Read VISE_<suffix>, falling back to legacy JIG_<suffix>."""
    for prefix in ("VISE_", "JIG_"):
        val = os.environ.get(prefix + suffix)
        if val is not None and val.strip():
            return val
    return None


def resolve_model() -> str:
    return (_env("EMBED_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _default_embed_cache_dir() -> Path:
    base = _env("EMBED_CACHE_DIR")
    if base:
        return Path(base)
    xdg = os.environ.get("XDG_CACHE_HOME")
    root = Path(xdg) if xdg else Path.home() / ".cache"
    return root / "vise" / "fastembed"


def resolve_idle_timeout() -> float:
    raw = (_env("EMBED_IDLE_TIMEOUT") or "").strip()
    if not raw:
        return DEFAULT_IDLE_TIMEOUT
    try:
        return max(0.0, float(raw))
    except ValueError:
        return DEFAULT_IDLE_TIMEOUT


def model_slug(name: str | None = None) -> str:
    """Stable, filesystem-safe slug used as cache key."""
    n = name or resolve_model()
    return hashlib.sha1(n.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


class FastembedClient:
    """Thin wrapper around fastembed.TextEmbedding with lazy model load."""

    def __init__(self, model_name: str | None = None, idle_timeout: float | None = None) -> None:
        self.model_name = model_name or resolve_model()
        self.idle_timeout = resolve_idle_timeout() if idle_timeout is None else idle_timeout
        self._model: Any = None
        self._lock = threading.Lock()
        self._load_error: Exception | None = None
        self._idle_timer: threading.Timer | None = None

    @property
    def available(self) -> bool:
        """True if the model is usable (or can be loaded)."""
        if self._load_error is not None:
            return False
        try:
            import fastembed  # noqa: F401
        except ImportError:
            return False
        return True

    @property
    def dim(self) -> int:
        return MODEL_DIMS.get(self.model_name, 768)

    def _ensure_model(self) -> bool:
        if self._model is not None:
            return True
        if self._load_error is not None:
            return False
        with self._lock:
            if self._model is not None:
                return True
            try:
                from fastembed import TextEmbedding

                log.info("[vise.embeddings] loading model %s (first run may download)", self.model_name)
                cache_dir = _default_embed_cache_dir()
                cache_dir.mkdir(parents=True, exist_ok=True)
                _embed_threads = int(_env("EMBED_THREADS") or "2")
                self._model = TextEmbedding(
                    model_name=self.model_name,
                    cache_dir=str(cache_dir),
                    threads=_embed_threads,
                )
                return True
            except Exception as e:  # pragma: no cover
                log.warning("[vise.embeddings] failed to load model %s: %s", self.model_name, e)
                self._load_error = e
                return False

    def _bump_idle_timer(self) -> None:
        if self.idle_timeout <= 0:
            return
        with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
            t = threading.Timer(self.idle_timeout, self.unload)
            t.daemon = True
            t.start()
            self._idle_timer = t

    def unload(self) -> None:
        """Release the in-memory model. Next embed call will reload it."""
        with self._lock:
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
            if self._model is not None:
                log.info("[vise.embeddings] unloading idle model %s", self.model_name)
                self._model = None
                import gc
                gc.collect()

    def embed_one(self, text: str) -> list[float] | None:
        if not self._ensure_model():
            return None
        assert self._model is not None
        try:
            for vec in self._model.embed([text]):
                return [float(x) for x in vec]
            return None
        finally:
            self._bump_idle_timer()

    def embed_many(self, texts: "Iterable[str]") -> list[list[float]] | None:
        if not self._ensure_model():
            return None
        assert self._model is not None
        try:
            out: list[list[float]] = []
            for vec in self._model.embed(list(texts)):
                out.append([float(x) for x in vec])
            return out
        finally:
            self._bump_idle_timer()


@lru_cache(maxsize=1)
def get_embedder() -> FastembedClient:
    return FastembedClient()


__all__ = [
    "DEFAULT_IDLE_TIMEOUT",
    "DEFAULT_MODEL",
    "FastembedClient",
    "MODEL_DIMS",
    "get_embedder",
    "model_slug",
    "resolve_idle_timeout",
    "resolve_model",
]
