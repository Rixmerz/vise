"""One relevance formula, two callers, and the test that holds them to it.

The `experience_*` tools rank with `experience_memory.compute_relevance`. The
`PreToolUse` hook ranks with `experience_injector._score_entry`, off a sidecar
index, because it runs as its own interpreter on every edit. They scored the
same entries against the same target and disagreed: the hook weighted the path
0.30 against the engine's 0.25, had no same-parent tier, applied no FSRS decay
and no recency, and its weights summed to 0.90.

Nothing failed. An entry the tool ranked first came third in the hook, and the
decay the README promises was applied on the surface a person asks and not on
the one that speaks into an agent's context unasked.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from vise.engines import relevance as rel
from vise.engines.experience_memory import ExperienceEntry, compute_relevance
from vise.hooks import experience_injector as inj


def _iso(days_ago: float) -> str:
    return (datetime.now() - timedelta(days=days_ago)).isoformat()


def _pair(**kw):
    """The same entry as the store sees it and as the index writes it."""
    base = dict(
        id="e1", type="smell_fixed", file_pattern="src/services/*Service.ts",
        keywords=["service", "services"], domain="api", description="d",
        confidence=0.62, last_seen=_iso(3), last_reviewed=_iso(1), stability=12.0,
    )
    base.update(kw)
    entry = ExperienceEntry(**base)
    indexed = {
        "file_pattern": entry.file_pattern, "keywords": list(entry.keywords),
        "domain": entry.domain, "confidence": entry.confidence,
        "last_seen": entry.last_seen, "last_reviewed": entry.last_reviewed,
        "stability": entry.stability,
        "_parent": str(Path(entry.file_pattern).parent) if entry.file_pattern else "_nopattern",
    }
    return entry, indexed


def _hook_score(indexed: dict, target: str) -> float:
    return inj._score_entry(
        indexed, target,
        set(inj._extract_keywords(target)),
        inj._guess_domain(target),
        str(Path(target).parent),
    )


@pytest.mark.parametrize("target", [
    "src/services/paymentService.ts",   # exact pattern match
    "src/services/helper.py",           # same directory, different extension
    "src/models/order.ts",              # same parent directory
    "lib/unrelated/thing.go",           # no locality at all
])
def test_the_hook_and_the_tools_score_a_file_identically(target):
    entry, indexed = _pair()
    assert _hook_score(indexed, target) == pytest.approx(compute_relevance(entry, target))


def test_they_agree_on_an_entry_with_no_pattern():
    entry, indexed = _pair(file_pattern="")
    indexed["file_pattern"] = ""
    target = "src/services/paymentService.ts"
    assert _hook_score(indexed, target) == pytest.approx(compute_relevance(entry, target))


def test_they_agree_when_the_timestamps_are_missing():
    """Pre-FSRS records carry no `last_reviewed` and old ones no `stability`.
    The default for a missing anchor is the one place the two curves want
    opposite answers, so it is the one most likely to drift."""
    entry, indexed = _pair(last_reviewed="", stability=0.0)
    indexed.update(last_reviewed="", stability=0.0)
    target = "src/services/paymentService.ts"
    assert _hook_score(indexed, target) == pytest.approx(compute_relevance(entry, target))


def test_a_stale_entry_scores_below_a_fresh_one_in_the_hook_too():
    """The decay was in the engine and not in the hook, so on the surface that
    speaks unasked a two-year-old lesson ranked like yesterday's."""
    target = "src/services/paymentService.ts"
    _, fresh = _pair()
    _, stale = _pair(last_seen=_iso(900), last_reviewed=_iso(900))
    assert _hook_score(stale, target) < _hook_score(fresh, target)


# --- the formula itself ---------------------------------------------------


def test_the_match_weights_sum_to_one_and_a_perfect_entry_scores_one():
    """A weight set that does not sum to one still ranks, which is why the
    hook's 0.90 went unnoticed: it produces a different order and no threshold
    in those units means what it says. The composite is a product now, so the
    invariant moved to the match half — the three signals it combines."""
    assert rel.match_score(path=1, semantic=1, domain=1) == pytest.approx(1.0)
    assert rel.match_score(path=0, semantic=0, domain=0) == 0.0
    assert rel.relevance(path=1, semantic=1, domain=1, confidence=1,
                         last_seen=_iso(0), last_reviewed=_iso(0),
                         stability=10.0) == pytest.approx(1.0, abs=1e-3)


def test_confidence_cannot_carry_an_entry_that_matches_nothing():
    """The defect the product exists to close. Under the sum, an entry with no
    path, keyword or domain signal still scored `confidence * 0.15 + recency *
    0.10`, so a much-repeated lesson about an unrelated file outranked lessons
    that matched. On a target whose layout differs from the one the entry was
    recorded against — the cross-project case — the right lesson came sixteenth
    of eighteen."""
    assert rel.relevance(path=0, semantic=0, domain=0, confidence=0.95,
                         stability=10.0, last_reviewed=_iso(0),
                         last_seen=_iso(0)) == 0.0


def test_a_weak_match_is_dropped_and_the_floor_is_on_the_match_not_the_product():
    """A correct lesson nobody has confirmed yet must still surface; a floor on
    the product would hide it for being new."""
    weak = dict(path=0.0, semantic=0.05, domain=0.0)
    assert rel.match_score(**weak) < rel.MIN_MATCH
    assert rel.relevance(**weak, confidence=0.95) == 0.0

    fresh = dict(path=1.0, semantic=0.0, domain=0.0)
    assert rel.match_score(**fresh) >= rel.MIN_MATCH
    assert rel.relevance(**fresh, confidence=0.30) > 0.0


@pytest.mark.parametrize("entry_kw,target_kw", [
    (["caches", "tokens"], ["token", "cache"]),     # plural on the entry side
    (["cache"], ["caches"]),                        # plural on the target side
    (["caching"], ["cache"]),                       # gerund
    (["retries"], ["retry"]),                       # y/ies
    (["index"], ["indexes"]),                       # es
    (["auth"], ["authentication"]),                 # abbreviation covers expansion
    (["cancellation"], ["cancel"]),                 # and the other way round
])
def test_morphological_variants_are_one_keyword(entry_kw, target_kw):
    """Exact set intersection scored `caches`/`tokens` against `token_cache.py`
    a flat zero, and that is the shape a cross-project lesson arrives in: the
    recorder names the file it happened on and the next repository spells it
    differently."""
    assert rel.keyword_score(entry_kw, target_kw) == pytest.approx(1.0)


def test_an_entry_is_not_punished_for_having_learned_more():
    """Jaccard divided by the union, so vocabulary counted against the entry:
    two keywords covering two of the target's three scored 0.667, and those
    same two plus eighteen others scored 0.095. Vocabulary grows with
    occurrences and so does confidence, so the term meant to find the
    most-learned lesson ranked it last."""
    target = ["token", "cache", "auth"]
    sparse = ["token", "cache"]
    rich = sparse + [f"unrelated{i}" for i in range(18)]
    assert rel.keyword_score(rich, target) == rel.keyword_score(sparse, target)


def test_unknown_is_not_a_domain():
    """`general` is the guesser's fallback. Comparing it with `==` scored two
    files it could not classify as a 0.20 domain match, which put a billing
    lesson above a migration lesson on a migration file."""
    assert rel.domain_score("general", "general") == 0.0
    assert rel.domain_score("", "") == 0.0
    assert rel.domain_score("auth", "auth") == 1.0
    assert rel.domain_score("auth", "api") == 0.0


@pytest.mark.parametrize("same_file,exact,same_dir,same_parent,expected", [
    (True, True, True, True, 1.0),    # the pattern names the file
    (False, True, False, False, 0.85),  # a glob covers it
    (False, False, True, False, 0.7),
    (False, False, False, True, 0.4),
    (False, False, False, False, 0.0),
    (False, True, True, True, 0.85),
])
def test_the_path_tiers(same_file, exact, same_dir, same_parent, expected):
    assert rel.path_score(exact=exact, same_dir=same_dir,
                          same_parent=same_parent, same_file=same_file) == expected


def test_same_file_defaults_off_so_an_untaught_caller_keeps_its_behaviour():
    assert rel.path_score(exact=True, same_dir=False, same_parent=False) == 0.85


def test_confidence_is_weighted_by_decay_not_taken_at_face_value():
    """Parity cannot catch this one. Both callers share the function, so deleting
    the decay term keeps them in perfect agreement — and the README's promise of
    FSRS retrievability would quietly stop being true on both surfaces.

    Everything but the recall anchor is held equal, so nothing else can separate
    the two scores.
    """
    seen = _iso(10)
    match = dict(path=1.0, semantic=1.0, domain=1.0)   # held equal and non-zero:
    # the product scores an unmatched entry zero, which would satisfy every
    # assertion below for a reason that has nothing to do with decay.
    recalled = rel.relevance(**match, confidence=1.0,
                             stability=10.0, last_reviewed=_iso(0), last_seen=seen)
    forgotten = rel.relevance(**match, confidence=1.0,
                              stability=10.0, last_reviewed=_iso(3000), last_seen=seen)
    undecayed = rel.match_score(**match) * 1.0

    assert forgotten < recalled
    assert forgotten < undecayed * 0.9, "confidence reached the score undecayed"
    assert recalled == pytest.approx(undecayed, rel=0.01)


def test_decay_is_floored_rather_than_zeroed():
    """A decayed entry is demoted, never deleted — deleting is the collector's
    decision and it makes it with its own threshold."""
    assert rel.decay_factor(10.0, "", _iso(100_000)) == pytest.approx(rel.DECAY_FLOOR)


def test_a_missing_recall_anchor_lands_on_the_half_life():
    """`days_since` answers 9 x stability for a missing anchor, which is the
    half-life by construction — so an entry with no timestamps is treated as
    exactly as recallable as one at the horizon, rather than as fresh."""
    assert rel.decay_factor(10.0, "", "") == pytest.approx(0.5)
    assert rel.decay_factor(10.0, "", _iso(90)) == pytest.approx(0.5, abs=0.01)


def test_a_missing_last_seen_claims_no_recency():
    """Not perfect recency. An entry with no timestamp has nothing to claim."""
    assert rel.recency("") == 0.0


def test_an_unparseable_timestamp_is_not_treated_as_now():
    assert rel.recency("yesterday-ish") == 0.0
    assert rel.days_since("not a date", default=7.0) == 7.0


def test_recency_falls_to_zero_over_the_window():
    assert rel.recency(_iso(0)) == pytest.approx(1.0, abs=0.01)
    assert rel.recency(_iso(rel.RECENCY_WINDOW_DAYS)) == pytest.approx(0.0, abs=0.01)
    assert rel.recency(_iso(rel.RECENCY_WINDOW_DAYS * 3)) == 0.0


def test_the_index_carries_every_field_the_formula_reads():
    """The hook cannot decay what the index did not write, and a missing field
    reads as a fresh entry rather than as an error."""
    from vise.hooks import experience_index_builder as builder

    assert {"last_seen", "last_reviewed", "stability"} <= builder._SCORE_FIELDS
    assert builder.SCHEMA_VERSION == inj.SCHEMA_VERSION, \
        "a reader and a writer disagreeing about the layout read stale buckets as fresh"
