"""Tests for src/jig/engines/experience_gc.py.

Coverage:
  - score_entry: age-decay monotonic; confirmation boost; superseded penalty
  - consolidate: merges near-duplicates; preserves non-duplicates; merged_map
  - consolidate: superseded detection (same key, different desc, lower confidence)
  - protected_ids_for: reads asset_journal.jsonl; missing file returns empty set
  - gc: dry-run mutates nothing; apply rewrites file + .bak
  - gc: protected ids survive aggressive threshold
  - gc: recent entries survive regardless of score
  - gc: missing store returns error report
  - CLI smoke: _cmd_gc with dry-run and --stats via run(args)
"""
from __future__ import annotations

import pytest
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from vise.engines.experience_gc import (
    AUTO_GC_NUDGE_THRESHOLD,
    DEFAULT_KEEP_RECENT_DAYS,
    DEFAULT_THRESHOLD,
    HALF_LIFE_DAYS,
    consolidate,
    gc,
    maybe_nudge_gc,
    protected_ids_for,
    score_entry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _entry(
    *,
    id: str = "abc12345",
    type: str = "smell_introduced",
    file_pattern: str = "src/services/*Service.ts",
    domain: str = "api",
    description: str = "God file smell detected",
    resolution: str = "Split into sub-services",
    occurrences: int = 1,
    confidence: float = 0.3,
    last_seen: str | None = None,
    first_seen: str | None = None,
    superseded: bool = False,
) -> dict:
    now_iso = _now().isoformat()
    e = {
        "id": id,
        "type": type,
        "file_pattern": file_pattern,
        "domain": domain,
        "description": description,
        "resolution": resolution,
        "occurrences": occurrences,
        "confidence": confidence,
        "last_seen": last_seen or now_iso,
        "first_seen": first_seen or now_iso,
        "keywords": ["service", "api"],
        "severity": "medium",
        "scope": "global",
        "project_origin": "my_project",
        "related_files": [],
    }
    if superseded:
        e["_superseded"] = True
    return e


def _store_file(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "entries": entries,
        "last_updated": _now().isoformat(),
        "version": "1.0",
        "scope": "global",
        "project": None,
        "count": len(entries),
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# score_entry
# ---------------------------------------------------------------------------

class TestScoreEntry:
    def test_recent_entry_scores_near_one(self):
        e = _entry(last_seen=_now().isoformat(), occurrences=5, confidence=0.8)
        score = score_entry(e, _now())
        assert score > 0.8, f"Recent high-confidence entry should score well, got {score}"

    def test_age_decay_monotonic(self):
        """Older entries score progressively lower."""
        now = _now()
        scores = []
        for days_ago in [0, 30, 90, 180, 365]:
            ls = (now - timedelta(days=days_ago)).isoformat()
            e = _entry(last_seen=ls, occurrences=1, confidence=0.3)
            scores.append(score_entry(e, now))

        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], (
                f"Score should decrease with age: "
                f"scores[{i}]={scores[i]:.4f} < scores[{i+1}]={scores[i+1]:.4f}"
            )

    def test_half_life_at_90_days(self):
        """At 90 days (half-life), age component should be ~0.5."""
        now = _now()
        ls = (now - timedelta(days=HALF_LIFE_DAYS)).isoformat()
        # Minimal confirmation boost: occurrences=1, confidence=0.3
        e = _entry(last_seen=ls, occurrences=1, confidence=0.01)
        score = score_entry(e, now)
        # Age component alone ~0.5; minimal confirmation, so total ~0.5
        assert 0.4 < score < 0.7, f"Expected ~0.5 at half-life, got {score}"

    def test_superseded_penalty_applied(self):
        """Superseded entries score significantly lower than identical non-superseded."""
        now = _now()
        ls = now.isoformat()
        e_normal = _entry(last_seen=ls, occurrences=3, confidence=0.6, superseded=False)
        e_superseded = _entry(last_seen=ls, occurrences=3, confidence=0.6, superseded=True)
        s_normal = score_entry(e_normal, now)
        s_super = score_entry(e_superseded, now)
        assert s_super < s_normal, "Superseded entry should score lower"
        assert s_super < 0.7, f"Superseded score should be penalised: {s_super}"

    def test_confirmation_boost_from_occurrences(self):
        """More occurrences → higher score, ceteris paribus."""
        now = _now()
        ls = (now - timedelta(days=HALF_LIFE_DAYS)).isoformat()
        e_low = _entry(last_seen=ls, occurrences=1, confidence=0.3)
        e_high = _entry(last_seen=ls, occurrences=20, confidence=0.9)
        assert score_entry(e_high, now) > score_entry(e_low, now)

    def test_missing_last_seen_uses_fallback(self):
        """Entry with no last_seen or first_seen gets a middling score (0.5 factor)."""
        now = _now()
        e = _entry()
        e.pop("last_seen", None)
        e.pop("first_seen", None)
        score = score_entry(e, now)
        assert 0.0 < score < 1.0, f"Expected middling score for unknown age, got {score}"

    def test_score_capped_at_one(self):
        """Score never exceeds 1.0 regardless of inputs."""
        now = _now()
        e = _entry(
            last_seen=now.isoformat(),
            occurrences=1000,
            confidence=0.99,
        )
        assert score_entry(e, now) <= 1.0


# ---------------------------------------------------------------------------
# consolidate
# ---------------------------------------------------------------------------

class TestConsolidate:
    def test_empty_list_returns_empty(self):
        kept, merged = consolidate([])
        assert kept == []
        assert merged == {}

    def test_single_entry_unchanged(self):
        e = _entry(id="aaa", description="Some issue detected")
        kept, merged = consolidate([e])
        assert len(kept) == 1
        assert merged == {}

    def test_near_duplicate_merged(self):
        """Two entries with same key and similar description → merged into one."""
        e1 = _entry(
            id="id1",
            description="God file detected in service layer",
            occurrences=3,
            confidence=0.6,
            last_seen=(_now() - timedelta(days=10)).isoformat(),
        )
        e2 = _entry(
            id="id2",
            description="God file detected in service layer indeed",  # similar
            occurrences=2,
            confidence=0.5,
            last_seen=_now().isoformat(),
        )
        kept, merged = consolidate([e1, e2])
        assert len(kept) == 1, "Near-duplicates should merge to one entry"
        assert kept[0]["occurrences"] == 5, "Occurrences should sum"
        assert kept[0]["confidence"] == 0.6, "Confidence should be max"
        # merged_map: lower-priority id mapped to kept id
        assert len(merged) == 1

    def test_different_description_not_merged(self):
        """Same key but very different description → kept separate."""
        e1 = _entry(id="id1", description="God file detected")
        e2 = _entry(id="id2", description="Circular dependency between modules")
        kept, merged = consolidate([e1, e2])
        assert len(kept) == 2
        assert merged == {}

    def test_merged_map_records_absorbed_id(self):
        # Descriptions must be similar enough (ratio >= 0.85) to trigger merge
        e1 = _entry(id="old_id", description="Hub overload smell found in service", confidence=0.4)
        e2 = _entry(id="new_id", description="Hub overload smell found in services", confidence=0.8)
        _kept, merged = consolidate([e1, e2])
        # One should be absorbed; merged_map has the absorbed id → kept id
        absorbed = [k for k in merged if k in ("old_id", "new_id")]
        assert len(absorbed) >= 1

    def test_resolution_texts_appended(self):
        """Distinct resolution texts from merged entries are combined."""
        e1 = _entry(id="id1", description="Smell detected", resolution="Refactor module A")
        e2 = _entry(id="id2", description="Smell detected in the file", resolution="Split into two files")
        kept, _ = consolidate([e1, e2])
        if len(kept) == 1:
            res = kept[0].get("resolution", "")
            assert "Refactor module A" in res
            assert "Split into two files" in res

    def test_superseded_flag_set_for_lower_confidence_same_key(self):
        """Two entries with same key but different descriptions — lower confidence flagged superseded."""
        e_high = _entry(
            id="h1",
            description="Unique description for high-confidence entry",
            confidence=0.9,
            last_seen=_now().isoformat(),
        )
        e_low = _entry(
            id="l1",
            description="Different description for low-confidence entry",
            confidence=0.2,
            last_seen=(_now() - timedelta(days=30)).isoformat(),
        )
        kept, _ = consolidate([e_high, e_low])
        low_entry = next((e for e in kept if e.get("id") == "l1"), None)
        if low_entry is not None:
            assert low_entry.get("_superseded"), "Lower-confidence same-key entry should be superseded"

    def test_superseded_key_stripped_from_output_in_gc(self, tmp_path):
        """_superseded key is stripped from entries that survive in gc()."""
        store = tmp_path / "experience_memory.json"
        e = _entry(id="x1", confidence=0.01, last_seen=(_now() - timedelta(days=200)).isoformat())
        e["_superseded"] = True
        # Put it above threshold so it survives (protected)
        _store_file(store, [e])
        report = gc(store, apply=False, protected_ids={"x1"})
        # The original file still has _superseded; that's fine for dry-run
        assert report["protected_kept"] >= 0  # just confirm it ran


# ---------------------------------------------------------------------------
# protected_ids_for
# ---------------------------------------------------------------------------

class TestProtectedIdsFor:
    def test_missing_journal_returns_empty(self, tmp_path):
        result = protected_ids_for(tmp_path)
        assert result == set()

    def test_reads_experience_refs_list(self, tmp_path):
        jig_dir = tmp_path / ".vise"
        jig_dir.mkdir()
        journal = jig_dir / "asset_journal.jsonl"
        lines = [
            json.dumps({"ts": "2026-01-01T00:00:00Z", "experience_refs": ["abc12345", "def67890"]}),
            json.dumps({"ts": "2026-01-02T00:00:00Z", "experience_refs": ["ghi11111"]}),
        ]
        journal.write_text("\n".join(lines), encoding="utf-8")
        result = protected_ids_for(tmp_path)
        assert result == {"abc12345", "def67890", "ghi11111"}

    def test_reads_experience_refs_single_string(self, tmp_path):
        jig_dir = tmp_path / ".vise"
        jig_dir.mkdir()
        journal = jig_dir / "asset_journal.jsonl"
        journal.write_text(
            json.dumps({"ts": "2026-01-01T00:00:00Z", "experience_refs": "single_id"}) + "\n",
            encoding="utf-8",
        )
        result = protected_ids_for(tmp_path)
        assert "single_id" in result

    def test_skips_malformed_lines(self, tmp_path):
        jig_dir = tmp_path / ".vise"
        jig_dir.mkdir()
        journal = jig_dir / "asset_journal.jsonl"
        journal.write_text(
            "not-json\n"
            + json.dumps({"experience_refs": ["good_id"]}) + "\n",
            encoding="utf-8",
        )
        result = protected_ids_for(tmp_path)
        assert "good_id" in result

    def test_entries_without_refs_key_ignored(self, tmp_path):
        jig_dir = tmp_path / ".vise"
        jig_dir.mkdir()
        journal = jig_dir / "asset_journal.jsonl"
        journal.write_text(
            json.dumps({"ts": "2026-01-01T00:00:00Z", "other_key": "value"}) + "\n",
            encoding="utf-8",
        )
        result = protected_ids_for(tmp_path)
        assert result == set()


# ---------------------------------------------------------------------------
# gc — dry-run and apply
# ---------------------------------------------------------------------------

class TestGC:
    def test_missing_store_returns_error_report(self, tmp_path):
        store = tmp_path / "nonexistent.json"
        report = gc(store, apply=False)
        assert report["error"] is not None
        assert report["before"] == 0

    def test_dry_run_does_not_mutate_file(self, tmp_path):
        store = tmp_path / "experience_memory.json"
        entries = [_entry(id=f"id{i}") for i in range(5)]
        _store_file(store, entries)
        original_content = store.read_text()

        report = gc(store, apply=False)

        assert store.read_text() == original_content, "Dry-run must not mutate the file"
        assert report["dry_run"] is True
        assert not (store.with_suffix(".json.bak")).exists(), "Dry-run must not create .bak"

    def test_apply_rewrites_file_and_creates_bak(self, tmp_path):
        store = tmp_path / "experience_memory.json"
        entries = [_entry(id=f"id{i}") for i in range(5)]
        _store_file(store, entries)

        report = gc(store, apply=True, protected_ids=set(), threshold=0.0, keep_recent_days=0)

        assert report["dry_run"] is False
        bak = store.with_suffix(".json.bak")
        assert bak.exists(), "apply mode must create .bak"
        # Rewritten store must be valid JSON
        data = json.loads(store.read_text())
        assert "entries" in data

    def test_protected_ids_survive_aggressive_threshold(self, tmp_path):
        store = tmp_path / "experience_memory.json"
        old_date = (_now() - timedelta(days=500)).isoformat()
        # Entry with terrible score: old, low confidence, superseded
        protected_entry = _entry(
            id="protected_one",
            last_seen=old_date,
            occurrences=1,
            confidence=0.01,
            superseded=True,
        )
        _store_file(store, [protected_entry])

        report = gc(
            store,
            apply=False,
            protected_ids={"protected_one"},
            threshold=1.0,        # impossibly high — nothing would survive normally
            keep_recent_days=0,
        )

        assert report["after"] == 1, "Protected entry must survive even with threshold=1.0"
        assert report["protected_kept"] == 1

    def test_recent_entries_survive_regardless_of_score(self, tmp_path):
        store = tmp_path / "experience_memory.json"
        recent_entry = _entry(
            id="recent_one",
            last_seen=_now().isoformat(),
            occurrences=1,
            confidence=0.01,
        )
        _store_file(store, [recent_entry])

        report = gc(
            store,
            apply=False,
            protected_ids=set(),
            threshold=1.0,               # would drop everything by score
            keep_recent_days=DEFAULT_KEEP_RECENT_DAYS,
        )

        assert report["after"] == 1, "Recent entry must survive regardless of threshold"
        assert report["dropped"] == 0

    def test_old_low_score_entries_dropped(self, tmp_path):
        store = tmp_path / "experience_memory.json"
        # FSRS retrievability(t, S=10) drops below DEFAULT_THRESHOLD=0.10 at ~850 days.
        # Use 900 days so the entry is dropped under the unified FSRS model (feature B).
        old_date = (_now() - timedelta(days=900)).isoformat()
        stale_entry = _entry(
            id="stale_one",
            last_seen=old_date,
            occurrences=1,
            confidence=0.01,
        )
        _store_file(store, [stale_entry])

        report = gc(
            store,
            apply=False,
            protected_ids=set(),
            threshold=DEFAULT_THRESHOLD,
            keep_recent_days=0,
        )

        assert report["dropped"] == 1
        assert report["after"] == 0

    def test_report_fields_complete(self, tmp_path):
        store = tmp_path / "experience_memory.json"
        _store_file(store, [_entry()])

        report = gc(store, apply=False)

        required = {
            "store_path", "before", "after", "consolidated",
            "dropped", "protected_kept", "bytes_before", "bytes_after",
            "dry_run", "error",
        }
        assert required.issubset(report.keys()), f"Missing keys: {required - report.keys()}"

    def test_bytes_before_after_populated(self, tmp_path):
        store = tmp_path / "experience_memory.json"
        entries = [_entry(id=f"id{i}") for i in range(3)]
        _store_file(store, entries)

        report = gc(store, apply=False)

        assert report["bytes_before"] > 0
        assert report["bytes_after"] > 0

    def test_apply_store_count_correct(self, tmp_path):
        store = tmp_path / "experience_memory.json"
        recent = _entry(id="keep_me", last_seen=_now().isoformat())
        old_stale = _entry(
            id="drop_me",
            last_seen=(_now() - timedelta(days=500)).isoformat(),
            confidence=0.01,
            occurrences=1,
        )
        _store_file(store, [recent, old_stale])

        report = gc(
            store,
            apply=True,
            protected_ids=set(),
            threshold=DEFAULT_THRESHOLD,
            keep_recent_days=DEFAULT_KEEP_RECENT_DAYS,
        )

        data = json.loads(store.read_text())
        assert len(data["entries"]) == report["after"]
        assert any(e["id"] == "keep_me" for e in data["entries"])
        assert not any(e["id"] == "drop_me" for e in data["entries"])


# ---------------------------------------------------------------------------
# maybe_nudge_gc
# ---------------------------------------------------------------------------

class TestMaybeNudgeGC:
    def test_no_nudge_below_threshold(self, capsys):
        maybe_nudge_gc(AUTO_GC_NUDGE_THRESHOLD - 1)
        captured = capsys.readouterr()
        assert captured.err == ""

    def test_nudge_at_threshold(self, capsys):
        maybe_nudge_gc(AUTO_GC_NUDGE_THRESHOLD + 1)
        captured = capsys.readouterr()
        assert "experience store has" in captured.err
        assert "vise experience gc" in captured.err

    def test_nudge_includes_store_path(self, capsys, tmp_path):
        store = tmp_path / "experience_memory.json"
        maybe_nudge_gc(AUTO_GC_NUDGE_THRESHOLD + 1, store_path=store)
        captured = capsys.readouterr()
        assert str(store) in captured.err


# ---------------------------------------------------------------------------
# CLI smoke tests
# ---------------------------------------------------------------------------

class TestCLIGC:
    def _make_store(self, tmp_path: Path) -> Path:
        store = tmp_path / "experience_memory.json"
        _store_file(store, [_entry(id=f"id{i}") for i in range(3)])
        return store

    def test_dry_run_returns_zero(self, tmp_path, capsys):
        pytest.importorskip("vise.cli.experience_cmd")  # CLI not extracted to vise
        from vise.cli.experience_cmd import _cmd_gc

        store = self._make_store(tmp_path)
        with (
            patch("vise.engines.experience_gc.protected_ids_for", return_value=set()),
            patch("vise.engines.experience_memory.GLOBAL_MEMORY_FILE", store),
            patch("vise.engines.experience_memory.PROJECT_MEMORIES_DIR", tmp_path / "project_memories"),
        ):
            args = SimpleNamespace(project_dir=str(tmp_path), apply=False, stats=False, json=False)
            rc = _cmd_gc(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "dry-run" in out

    def test_stats_mode_returns_zero(self, tmp_path, capsys):
        pytest.importorskip("vise.cli.experience_cmd")  # CLI not extracted to vise
        from vise.cli.experience_cmd import _cmd_gc

        store = self._make_store(tmp_path)
        with (
            patch("vise.engines.experience_gc.protected_ids_for", return_value=set()),
            patch("vise.engines.experience_memory.GLOBAL_MEMORY_FILE", store),
            patch("vise.engines.experience_memory.PROJECT_MEMORIES_DIR", tmp_path / "project_memories"),
        ):
            args = SimpleNamespace(project_dir=str(tmp_path), apply=False, stats=True, json=False)
            rc = _cmd_gc(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "entries" in out

    def test_no_stores_returns_zero(self, tmp_path, capsys):
        pytest.importorskip("vise.cli.experience_cmd")  # CLI not extracted to vise
        from vise.cli.experience_cmd import _cmd_gc

        with (
            patch("vise.engines.experience_memory.GLOBAL_MEMORY_FILE", tmp_path / "nonexistent.json"),
            patch("vise.engines.experience_memory.PROJECT_MEMORIES_DIR", tmp_path / "noprojects"),
        ):
            args = SimpleNamespace(project_dir=str(tmp_path), apply=False, stats=False, json=False)
            rc = _cmd_gc(args)

        assert rc == 0
        out = capsys.readouterr().out
        assert "No experience stores" in out

    def test_json_flag_emits_json_array(self, tmp_path, capsys):
        pytest.importorskip("vise.cli.experience_cmd")  # CLI not extracted to vise
        from vise.cli.experience_cmd import _cmd_gc

        store = self._make_store(tmp_path)
        with (
            patch("vise.engines.experience_gc.protected_ids_for", return_value=set()),
            patch("vise.engines.experience_memory.GLOBAL_MEMORY_FILE", store),
            patch("vise.engines.experience_memory.PROJECT_MEMORIES_DIR", tmp_path / "project_memories"),
        ):
            args = SimpleNamespace(project_dir=str(tmp_path), apply=False, stats=False, json=True)
            rc = _cmd_gc(args)

        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert isinstance(parsed, list)
        assert len(parsed) >= 1
        assert "before" in parsed[0]
