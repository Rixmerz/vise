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


def _inconclusive_attempts(n: int) -> list[Attempt]:
    """Attempts as the scheduler actually presents them.

    `state.finish()` appends the attempt and *then* calls `decide`, so the list
    always includes the one being judged. Driving `decide` with an empty list is
    a state production never reaches — and it is why this test passed for a
    version that gave inconclusive zero retries.
    """
    return [
        Attempt(i, "haiku", "medium", Verdict.INCONCLUSIVE, "error_max_turns",
                FailureKind.ENVIRONMENT_BUG, Usage())
        for i in range(1, n + 1)
    ]


def test_inconclusive_retries_once_then_stops():
    """Found by a real run: a docs task that ran out of turns was parked for a
    person after its *first* attempt, told "inconclusive twice"."""
    first = decide(_result(Verdict.INCONCLUSIVE), _inconclusive_attempts(1))
    assert first.action is Recovery.RETRY, first.reason

    second = decide(_result(Verdict.INCONCLUSIVE), _inconclusive_attempts(2))
    assert second.action is Recovery.HUMAN
    assert "twice" in second.reason


def test_inconclusive_and_an_environment_failure_get_the_same_budget():
    """The two branches share a constant and used to disagree by one character,
    so identical histories took opposite paths."""
    env_result = _result(classification=FailureKind.ENVIRONMENT_BUG)
    for n, expected in ((1, Recovery.RETRY), (2, Recovery.HUMAN)):
        inconclusive = decide(_result(Verdict.INCONCLUSIVE), _inconclusive_attempts(n))
        environment = decide(env_result, _inconclusive_attempts(n))
        assert inconclusive.action is expected, (n, inconclusive.reason)
        assert environment.action is expected, (n, environment.reason)


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
