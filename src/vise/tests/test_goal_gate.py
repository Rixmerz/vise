"""Tests for engines.goal_gate.evaluate() — the Stop-hook autonomy lock.

Focus: the mechanical-pass requirement on the GOAL_COMPLETE release branch
(Hole C) and that existing release paths still behave (override env, cancel
file, max attempts, plateau).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vise.engines import goal_state
from vise.engines.goal_gate import Action, Decision, evaluate
from vise.engines.goal_state import ValidatorRecord


@pytest.fixture(autouse=True)
def gate_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    monkeypatch.setenv("VISE_GOAL_GATE", "1")
    # Clear opt-out envs that other tests/sessions may have left set.
    monkeypatch.delenv("VISE_GOAL_GATE_OVERRIDE", raising=False)
    monkeypatch.delenv("VISE_GOAL_GATE_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("VISE_GOAL_GATE_PLATEAU_WINDOW", raising=False)


@pytest.fixture()
def project_dir(tmp_path: Path) -> str:
    p = tmp_path / "proj"
    (p / ".claude" / "state").mkdir(parents=True)
    return str(p)


def _decision(action: Action, confidence: float = 0.95, target: float = 0.9) -> Decision:
    return Decision(action=action, confidence=confidence,
                    target_confidence=target, advisory="")


def _mechanical_pass() -> ValidatorRecord:
    return ValidatorRecord(
        name="tests_pass", passed=True, confidence_contribution=0.9,
        weight=0.9, evidence="ok", at="2025-01-01T00:00:00+00:00",
        source="mechanical", exit_code=0,
    )


# ---------------------------------------------------------------------------
# Hole C: GOAL_COMPLETE requires a mechanical pass
# ---------------------------------------------------------------------------

def test_goal_complete_blocks_without_mechanical_pass(project_dir: str) -> None:
    goal_state.set_goal(project_dir, "ship it")
    # confidence target reached but last_results is empty → fabricable
    gate = evaluate(project_dir, _decision(Action.GOAL_COMPLETE))
    assert gate.block is True
    assert gate.cause == "no_mechanical_pass"
    assert "no mechanical validator passed" in gate.reason


def test_goal_complete_blocks_when_only_asserted_pass(project_dir: str) -> None:
    goal_state.set_goal(project_dir, "ship it")
    asserted = ValidatorRecord(
        name="self_claim", passed=True, confidence_contribution=1.0,
        weight=1.0, evidence="trust me", at="2025-01-01T00:00:00+00:00",
        source="asserted",
    )
    goal_state.update_goal(project_dir, last_results=[asserted], confidence=1.0)
    gate = evaluate(project_dir, _decision(Action.GOAL_COMPLETE))
    assert gate.block is True
    assert gate.cause == "no_mechanical_pass"


def test_goal_complete_releases_with_real_mechanical_pass(project_dir: str) -> None:
    goal_state.set_goal(project_dir, "ship it")
    goal_state.update_goal(project_dir, last_results=[_mechanical_pass()], confidence=0.95)
    gate = evaluate(project_dir, _decision(Action.GOAL_COMPLETE))
    assert gate.block is False
    assert gate.cause == "complete"


# ---------------------------------------------------------------------------
# Existing release paths still behave
# ---------------------------------------------------------------------------

def test_gate_disabled_releases(project_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISE_GOAL_GATE", "0")
    goal_state.set_goal(project_dir, "ship it")
    gate = evaluate(project_dir, _decision(Action.CONTINUE, confidence=0.1))
    assert gate.block is False
    assert gate.cause == "gate_disabled"


def test_override_env_releases(project_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISE_GOAL_GATE_OVERRIDE", "1")
    goal_state.set_goal(project_dir, "ship it")
    gate = evaluate(project_dir, _decision(Action.CONTINUE, confidence=0.1))
    assert gate.block is False
    assert gate.cause == "override"


def test_cancel_file_releases(project_dir: str) -> None:
    goal_state.set_goal(project_dir, "ship it")
    cancel = Path(project_dir) / ".claude" / "state" / "goal-cancel"
    cancel.touch()
    gate = evaluate(project_dir, _decision(Action.CONTINUE, confidence=0.1))
    assert gate.block is False
    assert gate.cause == "cancelled"


def test_no_active_goal_releases(project_dir: str) -> None:
    # No goal set at all.
    gate = evaluate(project_dir, _decision(Action.CONTINUE, confidence=0.1))
    assert gate.block is False
    assert gate.cause == "no_goal"


def test_max_attempts_releases(project_dir: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VISE_GOAL_GATE_MAX_ATTEMPTS", "3")
    goal_state.set_goal(project_dir, "ship it")
    goal_state.update_goal(project_dir, attempts=3)
    gate = evaluate(project_dir, _decision(Action.CONTINUE, confidence=0.1))
    assert gate.block is False
    assert gate.cause == "max_attempts"


def test_plateau_releases_via_structured_history(
    project_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VISE_GOAL_GATE_PLATEAU_WINDOW", "3")
    goal_state.set_goal(project_dir, "ship it")
    # Three structured confidence_update events all within tolerance.
    for _ in range(3):
        goal_state.append_history(project_dir, event="confidence_update", detail="0.500")
    gate = evaluate(project_dir, _decision(Action.CONTINUE, confidence=0.5))
    assert gate.block is False
    assert gate.cause == "plateau"


def test_no_plateau_when_confidence_moving_blocks(
    project_dir: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VISE_GOAL_GATE_PLATEAU_WINDOW", "3")
    goal_state.set_goal(project_dir, "ship it")
    for v in ("0.300", "0.500", "0.700"):
        goal_state.append_history(project_dir, event="confidence_update", detail=v)
    gate = evaluate(project_dir, _decision(Action.CONTINUE, confidence=0.7))
    # Confidence still climbing and below target → keep working (block).
    assert gate.block is True
    assert gate.cause == "continue"


def test_active_goal_in_progress_blocks(project_dir: str) -> None:
    goal_state.set_goal(project_dir, "ship it")
    gate = evaluate(project_dir, _decision(Action.CONTINUE, confidence=0.2))
    assert gate.block is True
    assert gate.cause == "continue"
