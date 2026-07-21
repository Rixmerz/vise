"""Tests for vise.engines.goal_state."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from vise.engines.goal_state import (
    GoalStatus,
    ValidatorRecord,
    append_history,
    clear_goal,
    default_target_confidence,
    get_goal,
    mark_abandoned,
    mark_complete,
    set_goal,
    update_goal,
)


@pytest.fixture(autouse=True)
def isolated_goal_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "goal"
    monkeypatch.setenv("VISE_GOAL_DIR", str(d))
    return d


PROJECT = "/tmp/myproject"


# ---------------------------------------------------------------------------
# set_goal
# ---------------------------------------------------------------------------

def test_set_goal_creates_file(isolated_goal_dir: Path) -> None:
    g = set_goal(PROJECT, "implement feature X")
    assert g.goal == "implement feature X"
    assert g.status == GoalStatus.ACTIVE.value
    assert g.id
    files = list(isolated_goal_dir.glob("*.json"))
    assert len(files) == 1


def test_set_goal_fields_correct() -> None:
    g = set_goal(
        PROJECT,
        "do something",
        acceptance_criteria=["tests pass", "lint clean"],
        target_confidence=0.95,
        complexity="medium",
    )
    assert g.acceptance_criteria == ["tests pass", "lint clean"]
    assert g.target_confidence == 0.95
    assert g.complexity == "medium"
    assert g.confidence == 0.0
    assert g.attempts == 0


def test_set_goal_history_has_started_event() -> None:
    g = set_goal(PROJECT, "do something")
    assert len(g.history) == 1
    assert g.history[0].event == "started"
    assert "do something" in g.history[0].detail


# ---------------------------------------------------------------------------
# get_goal round-trip
# ---------------------------------------------------------------------------

def test_get_goal_round_trip_preserves_history() -> None:
    orig = set_goal(PROJECT, "round-trip test", acceptance_criteria=["a", "b"])
    loaded = get_goal(PROJECT)
    assert loaded is not None
    assert loaded.id == orig.id
    assert loaded.goal == orig.goal
    assert loaded.acceptance_criteria == ["a", "b"]
    assert len(loaded.history) == 1
    assert loaded.history[0].event == "started"


def test_get_goal_returns_none_when_missing() -> None:
    result = get_goal("/nonexistent/project/path")
    assert result is None


# ---------------------------------------------------------------------------
# update_goal
# ---------------------------------------------------------------------------

def test_update_goal_updates_field_and_updated_at() -> None:
    g = set_goal(PROJECT, "update test")
    old_ts = g.updated_at
    updated = update_goal(PROJECT, status=GoalStatus.PAUSED.value)
    assert updated is not None
    assert updated.status == GoalStatus.PAUSED.value
    assert updated.updated_at >= old_ts


def test_update_goal_returns_none_when_no_goal() -> None:
    result = update_goal("/no/such/project", status="complete")
    assert result is None


# ---------------------------------------------------------------------------
# append_history
# ---------------------------------------------------------------------------

def test_append_history_adds_event() -> None:
    set_goal(PROJECT, "hist test")
    g = append_history(PROJECT, event="restarted", detail="attempt 2")
    assert g is not None
    assert any(e.event == "restarted" and e.detail == "attempt 2" for e in g.history)


def test_append_history_bumps_updated_at() -> None:
    g0 = set_goal(PROJECT, "ts test")
    old_ts = g0.updated_at
    g1 = append_history(PROJECT, event="validator_run")
    assert g1 is not None
    assert g1.updated_at >= old_ts


def test_append_history_returns_none_when_no_goal() -> None:
    result = append_history("/no/such/project", event="started")
    assert result is None


# ---------------------------------------------------------------------------
# default_target_confidence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("complexity,expected", [
    ("simple", 1.0),
    ("medium", 0.95),
    ("complex", 0.90),
    ("unknown", 0.90),
    ("bogus_string", 0.90),
])
def test_default_target_confidence(complexity: str, expected: float) -> None:
    assert default_target_confidence(complexity) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# clear_goal
# ---------------------------------------------------------------------------

def test_clear_goal_returns_true_on_existing() -> None:
    set_goal(PROJECT, "to clear")
    assert clear_goal(PROJECT) is True


def test_clear_goal_returns_false_on_missing() -> None:
    assert clear_goal("/nonexistent/path") is False


def test_clear_goal_removes_file(isolated_goal_dir: Path) -> None:
    set_goal(PROJECT, "to clear")
    clear_goal(PROJECT)
    assert list(isolated_goal_dir.glob("*.json")) == []


# ---------------------------------------------------------------------------
# mark_complete / mark_abandoned
# ---------------------------------------------------------------------------

def test_mark_complete_sets_status() -> None:
    set_goal(PROJECT, "complete me")
    g = mark_complete(PROJECT)
    assert g is not None
    assert g.status == GoalStatus.COMPLETE.value


def test_mark_abandoned_sets_status() -> None:
    set_goal(PROJECT, "abandon me")
    g = mark_abandoned(PROJECT)
    assert g is not None
    assert g.status == GoalStatus.ABANDONED.value


# ---------------------------------------------------------------------------
# Back-compat: legacy JSON without bootstrapped/bootstrap_completed_at
# ---------------------------------------------------------------------------

def test_get_goal_loads_legacy_json_without_bootstrap_fields(
    isolated_goal_dir: Path,
) -> None:
    """Pre-existing on-disk goal files don't carry the new bootstrap
    fields. _from_dict must default them to False/"" so users
    upgrading mid-task don't see ValueError on first read."""
    legacy = {
        "id": "legacy-uuid",
        "project_dir": PROJECT,
        "goal": "legacy goal",
        "acceptance_criteria": [],
        "target_confidence": 0.9,
        "complexity": "unknown",
        "status": "active",
        "started_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:00+00:00",
        "attempts": 0,
        "confidence": 0.0,
        "validator_configs": [],
        "last_results": [],
        "history": [],
        "preferred_model": "",
    }
    isolated_goal_dir.mkdir(parents=True, exist_ok=True)
    path = isolated_goal_dir / f"{Path(PROJECT).name}.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    g = get_goal(PROJECT)
    assert g is not None
    assert g.id == "legacy-uuid"
    assert g.bootstrapped is False
    assert g.bootstrap_completed_at == ""


# ---------------------------------------------------------------------------
# ValidatorRecord new fields (source / exit_code / full_output_path)
# ---------------------------------------------------------------------------

def test_validator_record_new_fields_default() -> None:
    """New self-grading/evidence fields default for back-compat construction."""
    r = ValidatorRecord(
        name="tests_pass",
        passed=True,
        confidence_contribution=0.4,
        weight=0.4,
        evidence="ok",
        at="2025-01-01T00:00:00+00:00",
    )
    assert r.source == "mechanical"
    assert r.exit_code is None
    assert r.full_output_path == ""


def test_get_goal_round_trips_validator_record_new_fields(
    isolated_goal_dir: Path,
) -> None:
    """asdict/_from_dict must preserve source/exit_code/full_output_path."""
    set_goal(PROJECT, "round-trip records")
    rec = ValidatorRecord(
        name="tests_pass",
        passed=True,
        confidence_contribution=0.4,
        weight=0.4,
        evidence="3 passed",
        at="2025-01-01T00:00:00+00:00",
        source="mechanical",
        exit_code=0,
        full_output_path="/tmp/evidence/tests_pass-x.log",
    )
    update_goal(PROJECT, last_results=[rec], confidence=0.4)
    loaded = get_goal(PROJECT)
    assert loaded is not None
    assert len(loaded.last_results) == 1
    lr = loaded.last_results[0]
    assert lr.source == "mechanical"
    assert lr.exit_code == 0
    assert lr.full_output_path == "/tmp/evidence/tests_pass-x.log"


def test_get_goal_loads_legacy_validator_record_without_new_fields(
    isolated_goal_dir: Path,
) -> None:
    """A legacy last_results entry lacking source/exit_code/full_output_path
    must default cleanly via _from_dict (ValidatorRecord(**r))."""
    legacy = {
        "id": "legacy-rec",
        "project_dir": PROJECT,
        "goal": "legacy record goal",
        "acceptance_criteria": [],
        "target_confidence": 0.9,
        "complexity": "unknown",
        "status": "active",
        "started_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:00+00:00",
        "attempts": 0,
        "confidence": 0.4,
        "validator_configs": [],
        "last_results": [
            {
                "name": "tests_pass",
                "passed": True,
                "confidence_contribution": 0.4,
                "weight": 0.4,
                "evidence": "3 passed",
                "at": "2025-01-01T00:00:00+00:00",
            }
        ],
        "history": [],
        "preferred_model": "",
    }
    isolated_goal_dir.mkdir(parents=True, exist_ok=True)
    path = isolated_goal_dir / f"{Path(PROJECT).name}.json"
    path.write_text(json.dumps(legacy), encoding="utf-8")

    g = get_goal(PROJECT)
    assert g is not None
    assert len(g.last_results) == 1
    lr = g.last_results[0]
    assert lr.source == "mechanical"
    assert lr.exit_code is None
    assert lr.full_output_path == ""
