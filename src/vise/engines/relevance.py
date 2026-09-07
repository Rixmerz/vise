"""The one relevance formula. Two implementations of it is two rankings.

``experience_memory.compute_relevance`` ranks for the ``experience_*`` tools and
the CLI. ``hooks/experience_injector`` ranks for the ``PreToolUse`` hook, off a
sidecar index, inlining what it needs because it runs as its own interpreter on
every edit and has about eight milliseconds of headroom.

They scored the same entries against the same target and disagreed:

===================  ===============  ===============
component            engine           hook
===================  ===============  ===============
path                 0.25             0.30
semantic (keywords)  0.30             0.25
domain               0.20             0.20
confidence           0.15, decayed    0.15, undecayed
recency              0.10             absent
same-parent tier     0.4              absent
sum of weights       1.00             0.90
===================  ===============  ===============

So an entry the tool ranked first could come third in the hook, and the
FSRS decay the README promises was applied only on the surface nobody watches —
the hook is the one that speaks into an agent's context unasked.

Everything here is standard library and takes plain values rather than entries,
so the hook can import it without importing the store. The component scores stay
with their callers: the hook matches globs with string operations against a
pre-baked parent and the engine compiles a regex, and making those one function
would cost the hook more than the divergence did. What is shared is what drifted
— the weights, the tiers, and the two curves.
"""
from __future__ import annotations

from datetime import datetime

#: Weights, summing to 1.0. A set that does not sum to one still ranks, which is
#: why nobody noticed the hook's 0.90: the order it produces is simply a
#: different order, and no threshold expressed in these units means what it says.
W_PATH = 0.25
W_SEMANTIC = 0.30
W_DOMAIN = 0.20
W_CONFIDENCE = 0.15
W_RECENCY = 0.10

#: Default FSRS stability in days, and the floor under the decay factor. Mirrors
#: ``engines.fsrs``; duplicated as a number rather than imported so this module
#: stays a leaf the hook can import for about a millisecond.
DEFAULT_STABILITY_DAYS = 10.0
DECAY_FLOOR = 0.05

#: How many days of staleness take recency to zero.
RECENCY_WINDOW_DAYS = 30.0


def days_since(iso_timestamp: str, *, default: float) -> float:
    """Elapsed days since *iso_timestamp*, or *default* when it cannot be read.

    The default is the caller's because the two curves want opposite answers for
    a missing timestamp: an entry with no recall anchor should decay as if it
    were old, and an entry with no ``last_seen`` has no recency to claim rather
    than a perfect one.
    """
    if not iso_timestamp:
        return default
    try:
        parsed = datetime.fromisoformat(iso_timestamp)
    except (ValueError, TypeError):
        return default
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    return max(0.0, (datetime.now() - parsed).total_seconds() / 86400.0)


def decay_factor(stability: float, last_reviewed: str, last_seen: str) -> float:
    """FSRS retrievability for an entry, floored so nothing reaches zero.

    Floored because a decayed entry is demoted, never deleted — that is the
    garbage collector's decision and it makes it with its own threshold.
    """
    s = stability if stability > 0 else DEFAULT_STABILITY_DAYS
    anchor = last_reviewed or last_seen
    elapsed = days_since(anchor, default=DEFAULT_STABILITY_DAYS * 9.0)
    return max(DECAY_FLOOR, (1.0 + elapsed / (9.0 * s)) ** -1)


def recency(last_seen: str) -> float:
    """1.0 for something seen today, falling to 0.0 over the window."""
    if not last_seen:
        return 0.0
    elapsed = days_since(last_seen, default=RECENCY_WINDOW_DAYS)
    return max(0.0, 1.0 - elapsed / RECENCY_WINDOW_DAYS)


def path_score(*, exact: bool, same_dir: bool, same_parent: bool) -> float:
    """How well a pattern's location matches the target's, in three tiers.

    The callers decide the booleans their own way; the tiers live here because
    the hook was missing the third one entirely, so a lesson filed one directory
    up scored zero for it and 0.4 for the tools.
    """
    if exact:
        return 1.0
    if same_dir:
        return 0.7
    return 0.4 if same_parent else 0.0


def relevance(
    *,
    path: float,
    semantic: float,
    domain: float,
    confidence: float,
    stability: float = 0.0,
    last_reviewed: str = "",
    last_seen: str = "",
) -> float:
    """Combine the component scores into one ranking number."""
    return (
        path * W_PATH
        + semantic * W_SEMANTIC
        + domain * W_DOMAIN
        + confidence * decay_factor(stability, last_reviewed, last_seen) * W_CONFIDENCE
        + recency(last_seen) * W_RECENCY
    )


__all__ = [
    "DECAY_FLOOR",
    "DEFAULT_STABILITY_DAYS",
    "RECENCY_WINDOW_DAYS",
    "W_CONFIDENCE",
    "W_DOMAIN",
    "W_PATH",
    "W_RECENCY",
    "W_SEMANTIC",
    "days_since",
    "decay_factor",
    "path_score",
    "recency",
    "relevance",
]
