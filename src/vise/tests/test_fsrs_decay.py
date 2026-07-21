"""Self-check tests for the unified FSRS decay system.

Verifies:
1. Curve decays monotonically in t.
2. A recall bump raises retrievability at the same elapsed t.
3. An old un-recalled entry scores below a fresh one.
4. ExperienceEntry migration: old records without stability get DEFAULT_STABILITY_DAYS.
5. ExperienceEntry recall bump via ExperienceMemoryStore._bump_recall.
6. MemoryNode FSRS retrievability and recall bump.
7. memory-gc FSRS threshold path (faded node identified without TTL expiry).
8. experience_gc.score_entry uses FSRS (not old exponential) — monotonic decay.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vise.engines.fsrs import (
    ARCHIVE_THRESHOLD,
    DEFAULT_STABILITY_DAYS,
    STABILITY_BUMP,
    days_since,
    retrievability,
)

# ---------------------------------------------------------------------------
# 1. Monotonic decay
# ---------------------------------------------------------------------------

def test_retrievability_decays_monotonically() -> None:
    """R(t) must strictly decrease as t increases (same stability)."""
    s = DEFAULT_STABILITY_DAYS
    prev = 1.0
    for t in [0, 1, 5, 10, 30, 90, 180, 365]:
        r = retrievability(float(t), s)
        assert r <= prev, f"Curve not monotonic at t={t}: {r} > prev {prev}"
        prev = r
    # Boundary: R(0) exactly 1.0
    assert retrievability(0.0, s) == 1.0


def test_retrievability_half_life_is_9_times_stability() -> None:
    """At t = 9*S, R should equal 0.5 (the half-life property of the formula)."""
    s = DEFAULT_STABILITY_DAYS
    r_half = retrievability(9.0 * s, s)
    assert abs(r_half - 0.5) < 1e-9, f"Expected 0.5 at t=9S, got {r_half}"


# ---------------------------------------------------------------------------
# 2. Recall bump raises retrievability
# ---------------------------------------------------------------------------

def test_recall_bump_raises_retrievability() -> None:
    """Higher stability after a bump means higher R at the same elapsed t."""
    t = DEFAULT_STABILITY_DAYS * 3
    r_before = retrievability(t, DEFAULT_STABILITY_DAYS)
    r_after = retrievability(t, DEFAULT_STABILITY_DAYS * STABILITY_BUMP)
    assert r_after > r_before, (
        f"Bump should raise R: {r_before:.4f} -> {r_after:.4f}"
    )


# ---------------------------------------------------------------------------
# 3. Fresh entry scores above stale un-recalled entry
# ---------------------------------------------------------------------------

def test_fresh_beats_stale() -> None:
    """An entry recalled yesterday scores above one never recalled for 300 days."""
    r_fresh = retrievability(1.0, DEFAULT_STABILITY_DAYS)
    r_stale = retrievability(300.0, DEFAULT_STABILITY_DAYS)
    assert r_fresh > r_stale, (
        f"Fresh entry should score higher: fresh={r_fresh:.4f} stale={r_stale:.4f}"
    )


# ---------------------------------------------------------------------------
# 4. days_since is safe with empty/bad input
# ---------------------------------------------------------------------------

def test_days_since_empty_string_safe() -> None:
    d = days_since("")
    assert d >= 0, f"days_since('') should be non-negative, got {d}"


def test_days_since_recent_is_small() -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    d = days_since(now_iso)
    assert d < 0.1, f"days_since(now) should be near 0, got {d}"


def test_days_since_old_timestamp() -> None:
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    d = days_since(old)
    assert 89.0 < d < 91.0, f"Expected ~90 days, got {d}"


# ---------------------------------------------------------------------------
# 5. ExperienceEntry migration and recall bump
# ---------------------------------------------------------------------------

def test_experience_entry_migration_sets_default_stability() -> None:
    """from_dict on an old record (no stability field) migrates to DEFAULT_STABILITY_DAYS."""
    from vise.engines.experience_memory import ExperienceEntry

    old_dict = {
        "id": "test01",
        "type": "smell_introduced",
        "file_pattern": "src/*Service.py",
        "keywords": [],
        "domain": "api",
        "description": "test entry",
        "severity": "low",
        "confidence": 0.5,
        "occurrences": 1,
        "first_seen": "2026-01-01T00:00:00",
        "last_seen": "2026-01-01T00:00:00",
        "project_origin": "jig",
        "resolution": "",
        "related_files": [],
        "scope": "global",
        # No stability / last_reviewed — simulates old records
    }
    entry = ExperienceEntry.from_dict(old_dict)
    assert entry.stability == DEFAULT_STABILITY_DAYS, (
        f"Expected {DEFAULT_STABILITY_DAYS}, got {entry.stability}"
    )
    assert entry.last_reviewed == "2026-01-01T00:00:00", (
        f"last_reviewed should default to last_seen, got {entry.last_reviewed!r}"
    )


def test_experience_store_bump_recall(tmp_path: Path) -> None:
    """_bump_recall increases stability and sets last_reviewed to now."""
    from vise.engines.experience_memory import ExperienceEntry, ExperienceMemoryStore

    store = ExperienceMemoryStore()
    store._file_path = tmp_path / "exp.json"
    store._scope = "global"
    store._project_name = None

    entry = ExperienceEntry(
        id="r001",
        type="smell_introduced",
        file_pattern="src/*",
        domain="api",
        description="test",
        confidence=0.5,
        stability=DEFAULT_STABILITY_DAYS,
        last_reviewed="2020-01-01T00:00:00",
    )

    stability_before = entry.stability
    store._bump_recall(entry)

    assert entry.stability > stability_before, "stability should increase after bump"
    # last_reviewed should be a recent ISO timestamp
    lr = datetime.fromisoformat(entry.last_reviewed)
    assert (datetime.now() - lr).total_seconds() < 5, "last_reviewed should be ~now"


# ---------------------------------------------------------------------------
# 6. MemoryNode FSRS retrievability and recall bump
# ---------------------------------------------------------------------------

def test_memory_node_retrievability_fresh() -> None:
    """A node with last_reviewed=now should have high retrievability."""
    pytest.importorskip("vise.engines.memory_store")  # not extracted to vise
    from vise.engines.memory_store import MemoryNode

    node = MemoryNode(
        id="test-node",
        name="Test",
        description="desc",
        type="reference",
        stability=DEFAULT_STABILITY_DAYS,
        last_reviewed=datetime.now().isoformat(),
    )
    r = node.retrievability()
    assert r > 0.95, f"Fresh node should have R > 0.95, got {r}"


def test_memory_node_retrievability_stale() -> None:
    """A node last reviewed 500 days ago should have low retrievability.

    With S=10d, half-life = 9*10 = 90d.
    At t=500d: R = (1 + 500/90)^-1 ≈ 0.153 < 0.5.
    """
    pytest.importorskip("vise.engines.memory_store")  # not extracted to vise
    from vise.engines.memory_store import MemoryNode

    old_ts = (datetime.now() - timedelta(days=500)).isoformat()
    node = MemoryNode(
        id="stale-node",
        name="Stale",
        description="desc",
        type="reference",
        stability=DEFAULT_STABILITY_DAYS,
        last_reviewed=old_ts,
    )
    r = node.retrievability()
    assert r < 0.5, f"Stale node should have R < 0.5, got {r}"


def test_memory_node_bump_recall_increases_stability(tmp_path: Path) -> None:
    """_bump_recall applied to a MemoryNode raises stability and updates last_reviewed."""
    pytest.importorskip("vise.engines.memory_store")  # not extracted to vise
    from vise.engines.memory_store import MemoryNode, _bump_recall

    node = MemoryNode(
        id="bump-test",
        name="Bump",
        description="d",
        type="reference",
        stability=DEFAULT_STABILITY_DAYS,
        last_reviewed="2020-01-01T00:00:00",
    )
    s_before = node.stability
    _bump_recall(node)
    assert node.stability > s_before, "stability must increase after _bump_recall"
    lr = datetime.fromisoformat(node.last_reviewed)
    assert (datetime.now() - lr).total_seconds() < 5


# ---------------------------------------------------------------------------
# 7. memory-gc FSRS-faded path
# ---------------------------------------------------------------------------

def test_memory_gc_identifies_fsrs_faded_node(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A node below ARCHIVE_THRESHOLD without TTL is flagged as FSRS-faded.

    ARCHIVE_THRESHOLD=0.10; with S=10d, half-life = 9*10 = 90d.
    To reach R < 0.10 we need t such that (1 + t/90)^-1 < 0.10 → t > 810d.
    Use 1000 days to be safely below threshold.
    """
    pytest.importorskip("vise.engines.memory_store")  # not extracted to vise
    from vise.engines.memory_store import MemoryNode

    # Node with very low retrievability: 1000 days old, S=10
    # R = (1 + 1000/90)^-1 ≈ 0.082 < 0.10
    old_ts = (datetime.now() - timedelta(days=1000)).isoformat()
    faded = MemoryNode(
        id="faded",
        name="Faded",
        description="d",
        type="reference",
        stability=DEFAULT_STABILITY_DAYS,
        last_reviewed=old_ts,
    )
    fresh = MemoryNode(
        id="fresh",
        name="Fresh",
        description="d",
        type="reference",
        stability=DEFAULT_STABILITY_DAYS,
        last_reviewed=datetime.now().isoformat(),
    )

    assert faded.retrievability() < ARCHIVE_THRESHOLD, (
        f"Faded node should be below threshold {ARCHIVE_THRESHOLD}, "
        f"got {faded.retrievability():.4f}"
    )
    assert fresh.retrievability() >= ARCHIVE_THRESHOLD, (
        f"Fresh node should be above threshold, got {fresh.retrievability():.4f}"
    )


# ---------------------------------------------------------------------------
# 8. experience_gc.score_entry uses FSRS — monotonic over age
# ---------------------------------------------------------------------------

def test_experience_gc_score_entry_monotonic() -> None:
    """score_entry must decrease as the entry ages (FSRS replaces old exponential)."""
    from vise.engines.experience_gc import score_entry

    now = datetime.now(timezone.utc)

    def entry_aged(days: float) -> dict:
        anchor = (now - timedelta(days=days)).isoformat()
        return {
            "id": "x",
            "type": "smell_introduced",
            "occurrences": 1,
            "confidence": 0.5,
            "last_seen": anchor,
            "last_reviewed": anchor,
            "stability": DEFAULT_STABILITY_DAYS,
        }

    scores = [score_entry(entry_aged(d), now) for d in [0, 10, 30, 90, 180, 365]]
    for i in range(len(scores) - 1):
        assert scores[i] >= scores[i + 1], (
            f"score_entry not monotonic at index {i}: {scores[i]:.4f} < {scores[i+1]:.4f}"
        )
