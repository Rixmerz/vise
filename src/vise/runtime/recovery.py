"""What to do when a task fails — see docs/scheduler.md § Retry, escalation, replan.

Three responses, and conflating them is how an orchestrator burns a budget going
in circles:

  retry     same task, same rung. Only for failures outside the work.
  escalate  same task, a bigger model. For work attempted and wrong.
  replan    throw the task graph away. For when the *plan* was wrong.

Calling an escalation a "retry" is the specific mistake that hides cost: three
retries read like a stubborn task, three escalations read like $6. This module
exists so the distinction is made once, in one place, from the failure's
classification rather than from whoever is writing the log line.

Everything here is a pure function of a result and its history. No model call,
no I/O, no clock — the decision has to be reproducible from the record, or
``vise explain`` is reconstructing a guess.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Sequence

from vise.runtime.contracts import (
    REPLAN_KINDS,
    RETRY_KINDS,
    Attempt,
    FailureKind,
    TaskResult,
    TaskState,
    Verdict,
)

#: How many times a task may be attempted before the runtime stops asking the
#: same question. Four is the ladder's height: haiku/low → sonnet/medium →
#: sonnet/high → opus/high. A fifth attempt would repeat the top rung, which is
#: the definition of a loop.
DEFAULT_MAX_ATTEMPTS = 4

#: How many times a run may rebuild its task graph. Replanning twice is a plan
#: problem; replanning five times is a goal problem, and that is a person's.
DEFAULT_MAX_REPLANS = 2

#: How many times an environment failure is retried at the same rung. One. If
#: the database is still down on the second try, waiting is not the fix.
DEFAULT_MAX_ENV_RETRIES = 1


class Recovery(StrEnum):
    """What the scheduler should do next with this task."""

    ACCEPT = "accept"
    RETRY = "retry"
    ESCALATE = "escalate"
    REPLAN = "replan"
    HUMAN = "human"


@dataclass(frozen=True)
class RecoveryDecision:
    """The move, the state it implies, and why."""

    action: Recovery
    state: TaskState
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action.value, "state": self.state.value, "reason": self.reason}


def _env_retries(attempts: Sequence[Attempt]) -> int:
    return sum(1 for a in attempts if a.classification in RETRY_KINDS)


def decide(
    result: TaskResult,
    attempts: Sequence[Attempt] = (),
    *,
    gates_accepted: bool = True,
    at_top_rung: bool = False,
    replans_used: int = 0,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    max_replans: int = DEFAULT_MAX_REPLANS,
    max_env_retries: int = DEFAULT_MAX_ENV_RETRIES,
) -> RecoveryDecision:
    """Decide the next move for one finished attempt.

    ``attempts`` includes the attempt ``result`` came from — the caller records
    it before deciding, so that "how many tries has this had" and "what did they
    say" are read from one list rather than from a count plus an off-by-one.
    """
    used = len(attempts)

    if result.verdict is Verdict.PASS and gates_accepted:
        return RecoveryDecision(
            Recovery.ACCEPT, TaskState.SUCCEEDED, "worker passed and the gates accepted it"
        )

    # An honesty refusal is a failure of the work, not of the environment, and
    # it escalates like one. The worker claimed something it could not show.
    if result.verdict is Verdict.PASS and not gates_accepted:
        classification: FailureKind | None = result.classification or FailureKind.CODE_BUG
    else:
        classification = result.classification

    if result.verdict is Verdict.INCONCLUSIVE:
        # Nothing was learned about the code. Trying again at a bigger model
        # would be paying more to learn nothing again.
        #
        # `<=`, not `<`, and for the same reason as the RETRY_KINDS branch
        # below: `attempts` includes the attempt being judged, so on the first
        # one `_env_retries` is already 1. With `<` this gave a task *zero*
        # retries and then reported "inconclusive twice" after one — which is
        # what a docs task that ran out of turns hit, parking a run that had
        # one cheap retry left.
        if _env_retries(attempts) <= max_env_retries and used < max_attempts:
            return RecoveryDecision(
                Recovery.RETRY, TaskState.PENDING,
                "inconclusive — the work was never evaluated, so the same rung tries again",
            )
        return RecoveryDecision(
            Recovery.HUMAN, TaskState.BLOCKED,
            "inconclusive twice — the task cannot be evaluated here and no model fixes that",
        )

    if classification in REPLAN_KINDS:
        if replans_used < max_replans:
            return RecoveryDecision(
                Recovery.REPLAN, TaskState.PENDING,
                f"classified {classification.value} — the plan is wrong, so trying the "
                f"same task harder cannot fix it",
            )
        return RecoveryDecision(
            Recovery.HUMAN, TaskState.BLOCKED,
            f"classified {classification.value} after {replans_used} replan(s) — the goal "
            f"itself needs a person",
        )

    if classification in RETRY_KINDS:
        if _env_retries(attempts) <= max_env_retries and used < max_attempts:
            return RecoveryDecision(
                Recovery.RETRY, TaskState.PENDING,
                "environment failure — same rung, because no model fixes a missing binary",
            )
        return RecoveryDecision(
            Recovery.HUMAN, TaskState.BLOCKED,
            "environment failure persisted through its retry — waiting is not the fix",
        )

    if used >= max_attempts:
        if replans_used < max_replans:
            return RecoveryDecision(
                Recovery.REPLAN, TaskState.PENDING,
                f"{used} attempts spent — asking the same question harder has stopped "
                f"being the question",
            )
        return RecoveryDecision(
            Recovery.HUMAN, TaskState.FAILED,
            f"{used} attempts and {replans_used} replan(s) spent",
        )

    if at_top_rung:
        if replans_used < max_replans:
            return RecoveryDecision(
                Recovery.REPLAN, TaskState.PENDING,
                "already at the top rung — there is nothing left to escalate to",
            )
        return RecoveryDecision(
            Recovery.HUMAN, TaskState.FAILED,
            "top rung reached and the replan budget is spent",
        )

    return RecoveryDecision(
        Recovery.ESCALATE, TaskState.PENDING,
        f"attempt {used} failed with the work attempted and wrong — one rung up",
    )


#: Substrings that identify an environment failure when nothing classified one.
#: A fallback, not a classifier: the debugger agent's verdict always wins, and
#: this only runs when there is none. Deliberately narrow — every phrase here
#: names a machine that was not there, never a test that disagreed. A pattern
#: that could match a real assertion failure would silently convert wrong code
#: into "the environment did it" and retry it forever at the cheapest rung.
_ENVIRONMENT_MARKERS: tuple[str, ...] = (
    "command not found",
    "no such file or directory",
    "connection refused",
    "could not connect",
    "permission denied",
    "modulenotfounderror",
    "importerror",
    "network is unreachable",
    "temporary failure in name resolution",
    "timed out waiting for",
)


def classify_from_text(text: str) -> FailureKind | None:
    """Guess a classification from a failure's own output. Fallback only.

    Returns None rather than a default. An undiagnosed failure escalates — see
    ``routing.escalation_steps`` — and that is the safe direction: guessing
    ``ENVIRONMENT_BUG`` would park a genuinely broken task at the cheapest rung
    and retry it until the attempt budget ran out.
    """
    if not text:
        return None
    lowered = text.lower()
    if any(marker in lowered for marker in _ENVIRONMENT_MARKERS):
        return FailureKind.ENVIRONMENT_BUG
    return None
