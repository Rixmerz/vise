"""Retry, escalate and replan are three different moves, and this is where the
difference is decided.

Calling an escalation a retry is the mistake that hides cost: three retries read
like a stubborn task, three escalations read like six dollars. Every test here
pins which of the three a given failure earns.
"""
from __future__ import annotations

import pytest

from vise.runtime.contracts import (
    Attempt,
    FailureKind,
    TaskResult,
    TaskState,
    Usage,
    Verdict,
)
from vise.runtime.recovery import (
    DEFAULT_MAX_ATTEMPTS,
    Recovery,
    classify_from_text,
    decide,
)


def _result(verdict=Verdict.FAIL, classification=None, **kw) -> TaskResult:
    return TaskResult(task_id="t", verdict=verdict, classification=classification, **kw)


def _attempts(n: int, classification=FailureKind.CODE_BUG) -> list[Attempt]:
    return [
        Attempt(i, "sonnet", "medium", Verdict.FAIL, "wrong", classification, Usage())
        for i in range(1, n + 1)
    ]


def test_a_pass_the_gates_accepted_succeeds():
    move = decide(_result(Verdict.PASS), _attempts(0), gates_accepted=True)
    assert move.action is Recovery.ACCEPT
    assert move.state is TaskState.SUCCEEDED


def test_a_pass_the_gates_refused_escalates_like_a_wrong_answer():
    """The worker claimed something it could not show. That is the work failing."""
    move = decide(_result(Verdict.PASS), _attempts(1), gates_accepted=False)
    assert move.action is Recovery.ESCALATE


def test_a_wrong_answer_escalates():
    move = decide(_result(classification=FailureKind.CODE_BUG), _attempts(1))
    assert move.action is Recovery.ESCALATE
    assert move.state is TaskState.PENDING


def test_an_environment_failure_retries_at_the_same_rung():
    move = decide(
        _result(classification=FailureKind.ENVIRONMENT_BUG),
        _attempts(1, FailureKind.ENVIRONMENT_BUG),
    )
    assert move.action is Recovery.RETRY
    assert "no model fixes a missing binary" in move.reason


def test_a_second_environment_failure_stops_for_a_person():
    move = decide(
        _result(classification=FailureKind.ENVIRONMENT_BUG),
        _attempts(2, FailureKind.ENVIRONMENT_BUG),
    )
    assert move.action is Recovery.HUMAN


@pytest.mark.parametrize("kind", [FailureKind.SPEC_BUG, FailureKind.ARCHITECTURE_BUG])
def test_a_plan_level_failure_replans_rather_than_trying_harder(kind):
    move = decide(_result(classification=kind), _attempts(1, kind))
    assert move.action is Recovery.REPLAN


def test_a_plan_level_failure_past_the_replan_budget_stops_for_a_person():
    move = decide(
        _result(classification=FailureKind.SPEC_BUG),
        _attempts(1, FailureKind.SPEC_BUG),
        replans_used=2,
        max_replans=2,
    )
    assert move.action is Recovery.HUMAN


def test_inconclusive_retries_once_then_stops():
    first = decide(_result(Verdict.INCONCLUSIVE), _attempts(0))
    assert first.action is Recovery.RETRY
    second = decide(
        _result(Verdict.INCONCLUSIVE),
        _attempts(2, FailureKind.ENVIRONMENT_BUG),
    )
    assert second.action is Recovery.HUMAN


def test_inconclusive_is_never_treated_as_a_wrong_answer():
    """A suite that could not run has said nothing about the code."""
    assert decide(_result(Verdict.INCONCLUSIVE), _attempts(0)).action is not Recovery.ESCALATE


def test_the_attempt_budget_ends_in_a_replan_not_an_endless_climb():
    move = decide(
        _result(classification=FailureKind.CODE_BUG),
        _attempts(DEFAULT_MAX_ATTEMPTS),
    )
    assert move.action is Recovery.REPLAN


def test_the_attempt_budget_plus_the_replan_budget_ends_with_a_person():
    move = decide(
        _result(classification=FailureKind.CODE_BUG),
        _attempts(DEFAULT_MAX_ATTEMPTS),
        replans_used=2,
        max_replans=2,
    )
    assert move.action is Recovery.HUMAN
    assert move.state is TaskState.FAILED


def test_the_top_rung_replans_because_there_is_nothing_above_it():
    move = decide(
        _result(classification=FailureKind.CODE_BUG), _attempts(1), at_top_rung=True
    )
    assert move.action is Recovery.REPLAN
    assert "nothing left to escalate" in move.reason


def test_the_top_rung_with_no_replans_left_stops_for_a_person():
    move = decide(
        _result(classification=FailureKind.CODE_BUG),
        _attempts(1),
        at_top_rung=True,
        replans_used=2,
        max_replans=2,
    )
    assert move.action is Recovery.HUMAN


def test_every_decision_carries_a_reason():
    assert decide(_result(), _attempts(1)).reason


def test_decision_serialises():
    assert decide(_result(Verdict.PASS)).to_dict()["action"] == "accept"


# --- the fallback classifier ---------------------------------------------


@pytest.mark.parametrize("text", [
    "bash: psql: command not found",
    "ModuleNotFoundError: No module named 'requests'",
    "could not connect to server: Connection refused",
])
def test_the_fallback_classifier_recognises_a_missing_machine(text):
    assert classify_from_text(text) is FailureKind.ENVIRONMENT_BUG


@pytest.mark.parametrize("text", [
    "AssertionError: expected 3, got 4",
    "1 failed, 12 passed",
    "TypeError: unsupported operand type(s)",
    "",
])
def test_the_fallback_classifier_returns_none_rather_than_guessing(text):
    """Guessing ENVIRONMENT_BUG would park a broken task at the cheapest rung
    and retry it until the attempt budget ran out."""
    assert classify_from_text(text) is None
