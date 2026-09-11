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
from functools import lru_cache

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


#: Suffixes folded to a common stem, longest first so ``ies`` wins over ``s``.
#: Not Porter: the vocabulary here is identifiers and path segments, where the
#: whole problem is plural-vs-singular and noun-vs-gerund. A trailing ``e`` goes
#: last so ``cache``/``caches``/``caching`` land on one stem.
_STEM_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", ""),
)

#: A stem never shrinks below this, so ``is`` does not become ``i``.
_MIN_STEM = 3

#: An abbreviation counts as its expansion only from this length up. ``auth``
#: covers ``authentication``; three characters would let ``api`` cover ``apify``.
#: It is the cheapest form of the substring match a trigram index gives you, and
#: it does let ``auth`` cover ``author`` — partial credit, not a whole match.
_MIN_PREFIX = 4

#: ``_guess_domain`` answers this when nothing matched. It is "could not
#: classify", not a domain, and two unclassifiable files are not neighbours.
UNKNOWN_DOMAINS = frozenset({"", "general", "unknown"})


@lru_cache(maxsize=4096)
def stem(word: str) -> str:
    """Fold one word to the stem its morphological variants share.

    Cached because the hook stems the same vocabulary once per candidate and
    scores about 150 of them per edit: the words repeat, the answer does not
    change, and the alternative was pre-baking stems into the sidecar index,
    which would have made the index format a second place to be wrong.
    """
    w = word.lower()
    for suffix, replacement in _STEM_SUFFIXES:
        if w.endswith(suffix) and len(w) - len(suffix) >= _MIN_STEM:
            w = w[: -len(suffix)] + replacement
            break
    return w[:-1] if w.endswith("e") and len(w) > _MIN_STEM else w


def _covers(target_stem: str, entry_stems: set[str]) -> bool:
    """Does any of *entry_stems* account for *target_stem*?"""
    if target_stem in entry_stems:
        return True
    for e in entry_stems:
        short, long = (target_stem, e) if len(target_stem) <= len(e) else (e, target_stem)
        if len(short) >= _MIN_PREFIX and long.startswith(short):
            return True
    return False


def keyword_score(entry_keywords, target_keywords) -> float:
    """How much of the TARGET's vocabulary this entry covers, on stemmed forms.

    Coverage, not Jaccard. Jaccard divides by the union, so an entry punished
    itself for having learned more: against ``token_cache.py`` an entry holding
    ``token`` and ``cache`` scored 0.667, and one holding those same two plus
    eighteen others scored 0.095 — seven times worse for covering exactly the
    same ground. Vocabulary grows with occurrences and so does confidence, so
    the term meant to find the most-learned lesson was ranking it last.

    The question a caller actually asks is "how much of the file I am editing
    does this lesson speak to", and the size of the lesson's own vocabulary is
    no part of it. So the denominator is the target's keyword count.

    Stemming is the other half. ``caches``/``tokens`` against ``token_cache.py``
    scored a flat zero on exact sets, and that is the shape a cross-project
    lesson almost always arrives in: the recorder names the file it happened on,
    and the next repository spells it differently.
    """
    if not entry_keywords or not target_keywords:
        return 0.0
    target_stems = {stem(k) for k in target_keywords}
    if not target_stems:
        return 0.0
    entry_stems = {stem(k) for k in entry_keywords}
    return sum(1 for t in target_stems if _covers(t, entry_stems)) / len(target_stems)


def domain_score(entry_domain: str, target_domain: str) -> float:
    """1.0 only when both sides named the SAME KNOWN domain.

    ``general`` is the guesser's fallback. Comparing it with ``==`` scored two
    files it could not classify as a domain match worth 0.20 — "I could not
    tell" counted as "these belong together", which is the absent-versus-
    unreadable collapse this repository refuses everywhere else. It put a
    billing lesson above a migration lesson on a migration file.
    """
    if entry_domain in UNKNOWN_DOMAINS or target_domain in UNKNOWN_DOMAINS:
        return 0.0
    return 1.0 if entry_domain == target_domain else 0.0


def path_score(
    *, exact: bool, same_dir: bool, same_parent: bool, same_file: bool = False,
) -> float:
    """How well a pattern's location matches the target's, in four tiers.

    The callers decide the booleans their own way; the tiers live here because
    the hook was missing the same-parent one entirely, so a lesson filed one
    directory up scored zero for it and 0.4 for the tools.

    ``same_file`` is the fourth, and it is newer. A pattern that NAMES the file
    and a glob that merely covers it both answered 1.0, so on a file with two
    candidate lessons the tie fell to whichever had been seen more often — a
    directory-wide lesson at confidence 0.64 outranked the lesson recorded
    against that exact file at 0.50. Naming the file is the stronger evidence
    and now says so. It defaults false so a caller that has not been taught the
    distinction keeps the behaviour it had.
    """
    if same_file:
        return 1.0
    if exact:
        return 0.85
    if same_dir:
        return 0.7
    return 0.4 if same_parent else 0.0


#: The three match weights, renormalised from the five above now that the other
#: two have left the sum. The ratio between them is unchanged.
_MATCH_TOTAL = W_PATH + W_SEMANTIC + W_DOMAIN

#: A candidate below this matched too little to be worth an agent's attention,
#: and `relevance` scores it zero. There used to be four thresholds instead —
#: 0.10 in the hook, 0.05 in the store's query, 0.5 in the tool and the CLI —
#: all in composite-score units, which mean a different thing after this change
#: and meant nothing comparable to each other before it. One threshold, on the
#: one component whose units are stable.
#: A floor on the MATCH, not on the final score: a correct lesson nobody has
#: confirmed yet should still surface, and a threshold on the product would hide
#: it for being new.
MIN_MATCH = 0.10


def match_score(*, path: float, semantic: float, domain: float) -> float:
    """The three match signals as one number in [0, 1]."""
    return (path * W_PATH + semantic * W_SEMANTIC + domain * W_DOMAIN) / _MATCH_TOTAL


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
    """How well this entry matches, scaled by how much it is worth believing.

    Match and belief are not addends. The five-term weighted sum let confidence
    alone carry an entry: with no path, keyword or domain signal at all, a
    lesson at confidence 0.93 still scored 0.139 and beat lessons that actually
    matched the file. On a target whose layout differs from the one the entry's
    pattern was recorded against — the cross-project case this store exists for
    — that put the right lesson sixteenth of eighteen.

    So the three match signals combine into one number in [0, 1] and the
    calibrated priors multiply it. Confidence is ``0.95 * (1 - 0.7^n)``, a
    probability that the lesson is right; ``decay_factor`` is FSRS
    retrievability, a probability that it is still worth recalling. A
    probability scales evidence, it does not substitute for it. An entry that
    matches nothing scores nothing, however sure of itself it is.

    ``recency`` no longer appears. ``decay_factor`` reads the same timestamps
    and is the recency model; summing a second linear one beside it was two
    models of one thing, which is the shape every other drift in this file had.
    The function stays exported — it answers a real question — but the composite
    asks it once.
    """
    match = match_score(path=path, semantic=semantic, domain=domain)
    if match < MIN_MATCH:
        return 0.0
    return match * confidence * decay_factor(stability, last_reviewed, last_seen)


__all__ = [
    "DECAY_FLOOR",
    "MIN_MATCH",
    "UNKNOWN_DOMAINS",
    "DEFAULT_STABILITY_DAYS",
    "RECENCY_WINDOW_DAYS",
    "W_CONFIDENCE",
    "W_DOMAIN",
    "W_PATH",
    "W_RECENCY",
    "W_SEMANTIC",
    "days_since",
    "decay_factor",
    "domain_score",
    "keyword_score",
    "match_score",
    "path_score",
    "stem",
    "recency",
    "relevance",
]
