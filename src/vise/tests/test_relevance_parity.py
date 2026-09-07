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


def test_the_weights_sum_to_one():
    """A set that does not still ranks, which is why the hook's 0.90 went
    unnoticed: it produces a different order and no threshold in these units
    means what it says."""
    total = rel.W_PATH + rel.W_SEMANTIC + rel.W_DOMAIN + rel.W_CONFIDENCE + rel.W_RECENCY
    assert total == pytest.approx(1.0)
    assert rel.relevance(path=1, semantic=1, domain=1, confidence=1,
                         last_seen=_iso(0), last_reviewed=_iso(0),
                         stability=10.0) == pytest.approx(1.0, abs=1e-3)


@pytest.mark.parametrize("exact,same_dir,same_parent,expected", [
    (True, False, False, 1.0),
    (False, True, False, 0.7),
    (False, False, True, 0.4),
    (False, False, False, 0.0),
    (True, True, True, 1.0),
])
def test_the_path_tiers(exact, same_dir, same_parent, expected):
    assert rel.path_score(exact=exact, same_dir=same_dir, same_parent=same_parent) == expected


def test_confidence_is_weighted_by_decay_not_taken_at_face_value():
    """Parity cannot catch this one. Both callers share the function, so deleting
    the decay term keeps them in perfect agreement — and the README's promise of
    FSRS retrievability would quietly stop being true on both surfaces.

    Everything but the recall anchor is held equal, so nothing else can separate
    the two scores.
    """
    seen = _iso(10)
    recalled = rel.relevance(path=0, semantic=0, domain=0, confidence=1.0,
                             stability=10.0, last_reviewed=_iso(0), last_seen=seen)
    forgotten = rel.relevance(path=0, semantic=0, domain=0, confidence=1.0,
                              stability=10.0, last_reviewed=_iso(3000), last_seen=seen)
    undecayed = rel.W_CONFIDENCE + rel.recency(seen) * rel.W_RECENCY

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
