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
    repeated_answer,
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


# --- the ladder answering itself -----------------------------------------
#
# Escalating is only worth paying for while a bigger model might say something
# new. Two rungs reaching the same answer is evidence that it will not, and the
# ladder is four rungs of which the top two are most of the cost.

SAID = "the auth test still fails on an expired token"
REWORDED = "the auth test still fails for an expired token"
OTHER = "the migration could not find the orders table"


def _at(number, model, effort, summary=SAID, classification=FailureKind.CODE_BUG,
        verdict=Verdict.FAIL) -> Attempt:
    return Attempt(number, model, effort, verdict, summary, classification, Usage())


def test_two_rungs_reaching_the_same_answer_replan_instead_of_climbing():
    attempts = [_at(1, "haiku", ""), _at(2, "sonnet", "medium")]
    move = decide(_result(classification=FailureKind.CODE_BUG), attempts)
    assert move.action is Recovery.REPLAN
    assert "haiku" in move.reason and "sonnet/medium" in move.reason


def test_a_reworded_repeat_still_counts():
    """The next attempt's brief carries the previous summary into it, so a worker
    with nothing new to report still reports it in new words. An exact-match
    check would be a guard that never fires."""
    attempts = [_at(1, "haiku", ""), _at(2, "sonnet", "medium", REWORDED)]
    assert decide(_result(), attempts).action is Recovery.REPLAN


def test_whitespace_and_case_do_not_make_an_answer_new():
    shouted = "  The AUTH test\n still  fails on an expired token "
    attempts = [_at(1, "haiku", ""), _at(2, "sonnet", "medium", shouted)]
    assert decide(_result(), attempts).action is Recovery.REPLAN


def test_the_same_rung_agreeing_with_itself_is_not_a_repeat():
    """Determinism is not a discovery, and an environment failure retries at the
    same rung by design — reading that as a loop would replan a task whose only
    problem was a missing binary."""
    attempts = [_at(1, "sonnet", "medium"), _at(2, "sonnet", "medium")]
    assert decide(_result(), attempts).action is Recovery.ESCALATE


def test_two_rungs_reaching_different_answers_still_climb():
    attempts = [_at(1, "haiku", ""), _at(2, "sonnet", "medium", OTHER)]
    assert decide(_result(), attempts).action is Recovery.ESCALATE


def test_the_same_words_about_a_different_failure_still_climb():
    """Same prose, different classification. The classification is the typed
    half of the answer and it moved, so something was learned."""
    attempts = [_at(1, "haiku", ""), _at(2, "sonnet", "medium",
                                         classification=FailureKind.TEST_BUG)]
    assert decide(_result(), attempts).action is Recovery.ESCALATE


def test_saying_nothing_twice_is_an_absence_not_a_repeat():
    """Absent and identical are different. A worker that reported nothing has not
    repeated an answer, and replanning on it would spend the plan budget on
    vise's own missing evidence."""
    attempts = [_at(1, "haiku", "", summary=""), _at(2, "sonnet", "medium", summary="")]
    assert decide(_result(), attempts).action is Recovery.ESCALATE


def test_a_repeat_past_the_replan_budget_stops_for_a_person():
    attempts = [_at(1, "haiku", ""), _at(2, "sonnet", "medium")]
    move = decide(_result(), attempts, replans_used=2, max_replans=2)
    assert move.action is Recovery.HUMAN
    assert move.state is TaskState.BLOCKED


def test_any_earlier_rung_counts_not_only_the_last_one():
    """Once a rung has produced this answer the ladder has been tried against
    it, whatever the task did in between."""
    attempts = [_at(1, "haiku", ""), _at(2, "sonnet", "medium", OTHER),
                _at(3, "sonnet", "high")]
    move = decide(_result(), attempts)
    assert move.action is Recovery.REPLAN
    assert "attempt 1" in move.reason


def test_a_repeat_is_caught_before_the_attempt_budget_runs_out():
    """The whole saving is arriving early. Caught at attempt 2 the task has spent
    the two cheap rungs; running to `max_attempts` spends the two expensive ones
    to be told the same thing twice more, and replans anyway."""
    attempts = [_at(1, "haiku", ""), _at(2, "sonnet", "medium")]
    assert len(attempts) < DEFAULT_MAX_ATTEMPTS
    move = decide(_result(), attempts)
    assert move.action is Recovery.REPLAN
    assert "attempts spent" not in move.reason


def test_an_environment_repeat_is_still_an_environment_retry():
    """RETRY_KINDS is answered before this check. A missing binary reported twice
    is the same missing binary, and no plan fixes it either."""
    attempts = [_at(1, "haiku", "", classification=FailureKind.ENVIRONMENT_BUG),
                _at(2, "sonnet", "medium", classification=FailureKind.ENVIRONMENT_BUG)]
    move = decide(_result(classification=FailureKind.ENVIRONMENT_BUG), attempts)
    assert move.action is Recovery.HUMAN
    assert "environment" in move.reason


def test_a_claim_the_gates_refused_twice_is_a_repeat():
    """The most expensive loop there is: two rungs claiming the same thing, both
    refused for not showing it. `decide` accepts a pass the gates accepted long
    before this check, so the only passes that reach it are refused ones."""
    attempts = [_at(1, "haiku", "", verdict=Verdict.PASS),
                _at(2, "sonnet", "medium", verdict=Verdict.PASS)]
    move = decide(_result(Verdict.PASS), attempts, gates_accepted=False)
    assert move.action is Recovery.REPLAN


def test_a_pass_does_not_repeat_a_failure_that_read_the_same():
    """The verdict moved, so the answer did. It went from wrong to unproven."""
    attempts = [_at(1, "haiku", ""), _at(2, "sonnet", "medium", verdict=Verdict.PASS)]
    assert repeated_answer(attempts) is None


def test_one_attempt_cannot_repeat_anything():
    assert repeated_answer([_at(1, "haiku", "")]) is None
    assert repeated_answer([]) is None
