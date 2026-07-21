"""Embedding-similarity capability auto-tagger.

Given a tool description string, suggests the closest capability from the
closed taxonomy in ``vise.recipes.capabilities.CAPABILITIES`` by computing
cosine similarity against curated exemplar prompts per capability.

Usage::

    from vise.recipes.autotag import suggest_capability
    result = suggest_capability("scrape this URL and return article text")
    if result and result.confident:
        print(result.capability)  # "web.scrape"
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

from vise.recipes.capabilities import CAPABILITIES

log = logging.getLogger(__name__)

_EXEMPLARS_DIR = Path(__file__).parent / "exemplars"

# Module-level cache: model_name -> {capability: centroid vector}
_centroid_cache: dict[str, dict[str, list[float]]] = {}


@dataclass(frozen=True, slots=True)
class AutoTagResult:
    """Result of a capability suggestion."""

    capability: str
    score: float
    runner_up: str | None
    runner_up_score: float
    confident: bool


def _load_exemplars() -> dict[str, list[str]]:
    """Load exemplar prompt lines from *.txt files in the exemplars directory."""
    result: dict[str, list[str]] = {}
    for path in _EXEMPLARS_DIR.glob("*.txt"):
        stem = path.stem
        if stem not in CAPABILITIES:
            log.warning("[autotag] exemplar file %r has stem not in CAPABILITIES — skipping", path.name)
            continue
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            result[stem] = lines
    return result


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _get_centroids(model_name: str) -> dict[str, list[float]] | None:
    """Return cached centroid embeddings, computing them on first call for this model."""
    if model_name in _centroid_cache:
        return _centroid_cache[model_name]

    from vise.core.embeddings import get_embedder

    emb = get_embedder()
    if not emb.available:
        return None

    exemplars = _load_exemplars()
    if not exemplars:
        log.warning("[autotag] no exemplar files found under %s", _EXEMPLARS_DIR)
        return None

    centroids: dict[str, list[float]] = {}
    for cap, lines in exemplars.items():
        vecs = emb.embed_many(lines)
        if vecs is None or not vecs:
            log.warning("[autotag] failed to embed exemplars for capability %r", cap)
            continue
        dim = len(vecs[0])
        centroid = [sum(v[i] for v in vecs) / len(vecs) for i in range(dim)]
        centroids[cap] = centroid

    _centroid_cache[model_name] = centroids
    return centroids


def score_all(tool_description: str) -> list[tuple[str, float]]:
    """Return all capabilities sorted by cosine similarity descending.

    Returns an empty list if the embedder is unavailable.
    """
    from vise.core.embeddings import get_embedder, resolve_model

    emb = get_embedder()
    if not emb.available:
        return []

    model_name = resolve_model()
    centroids = _get_centroids(model_name)
    if not centroids:
        return []

    query_vec = emb.embed_one(tool_description)
    if query_vec is None:
        return []

    scores = [(cap, _cosine(query_vec, centroid)) for cap, centroid in centroids.items()]
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores


def suggest_capability(
    tool_description: str,
    *,
    threshold: float = 0.78,
    gap: float = 0.05,
) -> AutoTagResult | None:
    """Suggest the best matching capability for a tool description.

    Returns ``None`` if the embedder is unavailable or no exemplars are loaded.
    Returns an ``AutoTagResult`` with ``confident=False`` when the best score
    is below *threshold* or the margin over the runner-up is less than *gap*.
    """
    try:
        scores = score_all(tool_description)
    except Exception:
        log.exception("[autotag] unexpected error in score_all")
        return None

    if not scores:
        return None

    best_cap, best_score = scores[0]
    runner_up: str | None = None
    runner_up_score = 0.0
    if len(scores) >= 2:
        runner_up, runner_up_score = scores[1]

    confident = best_score >= threshold and (best_score - runner_up_score) >= gap

    return AutoTagResult(
        capability=best_cap,
        score=best_score,
        runner_up=runner_up,
        runner_up_score=runner_up_score,
        confident=confident,
    )


__all__ = ["AutoTagResult", "score_all", "suggest_capability"]
