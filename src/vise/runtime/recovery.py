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
``vise runtime explain`` is reconstructing a guess.
"""
from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
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
    tag,
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

#: How alike two summaries have to read before they count as the same answer.
#: The same number `experience_gc.CONSOLIDATE_SIMILARITY` uses, because it is the
#: same judgement — when do two prose blobs describe one thing. A second, freshly
#: invented number for it would mean one of the two is unexamined.
SAME_ANSWER_RATIO: float = 0.85


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


def _reads_alike(left: str, right: str) -> bool:
    """Whether two summaries say the same thing, allowing for rewording.

    Loosely, on purpose. The next attempt's brief carries the previous summary
    into it, so a worker with nothing new to report still reports it in new
    words — an exact-match check would be a guard that never fires, which is
    worse than no guard because it reads like coverage.

    Two empty summaries are not alike. Saying nothing twice is an absence, and
    an absence is not evidence that the same thing was said: a task whose worker
    reported nothing would otherwise be replanned for having no answer rather
    than for repeating one.
    """
    a = " ".join(left.split()).casefold()
    b = " ".join(right.split()).casefold()
    if not a or not b:
        return False
    return a == b or SequenceMatcher(None, a, b).ratio() >= SAME_ANSWER_RATIO


def repeated_answer(attempts: Sequence[Attempt]) -> Attempt | None:
    """The earlier attempt the latest one merely repeats, or None.

    Not "it failed twice" — that is what the ladder is for, and every escalation
    starts there. This is *two different rungs reaching the same answer*: same
    verdict, same classification, and summaries that read alike. A bigger model
    already had its turn on this task and said what the smaller one said, so the
    next rung is unlikely to say anything new, and there are only ever four.

    Requiring the rungs to differ is what keeps it off determinism. The same
    model at the same effort agreeing with itself is not a discovery, and an
    environment failure retries at the same rung by design — reading that as a
    loop would replan a task whose only problem was a missing binary.

    Every earlier attempt is compared, not just the previous one: once a rung
    has produced this answer, the ladder has been tried against it, whatever the
    task did in between.

    Verdicts are compared rather than filtered, so a ``PASS`` can only match
    another ``PASS`` — which is deliberate. ``decide`` accepts a pass the gates
    accepted long before it gets here, so the only passes that reach this are
    ones the honesty gates refused, and two rungs claiming the same thing they
    both cannot show is the most expensive loop of the lot.
    """
    if len(attempts) < 2:
        return None
    latest = attempts[-1]
    for prior in reversed(attempts[:-1]):
        if (prior.model, prior.effort) == (latest.model, latest.effort):
            continue
        if (prior.verdict, prior.classification) != (latest.verdict, latest.classification):
            continue
        if _reads_alike(prior.summary, latest.summary):
            return prior
    return None


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

    # Before the attempt count, because it fires earlier and explains more. A
    # task caught here at attempt 2 has spent the two cheap rungs; letting it
    # run to `max_attempts` spends the two expensive ones to be told the same
    # thing a third and fourth time, and then replans anyway.
    echo = repeated_answer(attempts)
    if echo is not None:
        rungs = f"{tag(echo.model, echo.effort)} and {tag(attempts[-1].model, attempts[-1].effort)}"
        if replans_used < max_replans:
            return RecoveryDecision(
                Recovery.REPLAN, TaskState.PENDING,
                f"attempt {echo.number} already reached this answer — {rungs} agree, "
                f"which is evidence about the plan rather than about the model",
            )
        return RecoveryDecision(
            Recovery.HUMAN, TaskState.BLOCKED,
            f"{rungs} reached the same answer and the replan budget is spent",
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
