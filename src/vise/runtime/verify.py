"""The second opinion — see docs/worker-contract.md § The worker does not grade itself.

``TaskResult.verdict`` is what the worker says about its own work. ``SUCCEEDED``
is what the runtime concludes, and getting from one to the other takes an agent
that did not do the work, given a different input.

The input is the load-bearing part. The verifier receives the acceptance
criteria, the diff and the evidence — **never the worker's reasoning**. A
reviewer who reads the argument for why the code is right is reviewing the
argument, and the argument is exactly the artefact a wrong-but-confident worker
produces most convincingly.

Above the verifier sits the adversarial reviewer, which is not asked "is this
correct". It is asked to find reasons this should not ship, and its probes are
named rather than left to judgement — the measured gap behind
docs/model-routing.md was the largest available model missing non-ASCII input
twice, because nothing told it to look.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from vise.runtime.contracts import (
    Artifact,
    Criticality,
    FailureKind,
    TaskBrief,
    TaskBudget,
    TaskResult,
    Verdict,
)

#: What the adversarial reviewer is told to probe. Named, not left to
#: judgement. Every entry earned its place by being missed at least once.
REVIEW_PROBES: tuple[str, ...] = (
    "boundary conditions — empty, one, maximum, off-by-one at every limit",
    "malformed input, and non-ASCII input specifically: a parser or validator "
    "that silently accepts '٥' (Arabic-Indic five) is the failure this list exists for",
    "concurrency — two callers at once, and the same caller twice",
    "timeouts and retries — what happens on the second delivery of the first request",
    "partial failure — the call that half-succeeded and returned an error",
    "data corruption — what a bad write leaves behind for the next read",
    "permissions — the caller who is authenticated but not authorised",
    "API compatibility — what an existing caller sees after this change",
    "failure recovery — whether the system can be restarted into a good state",
)

#: What a verifier is asked. Kept separate from the reviewer's list because
#: they are different questions: "does this meet the criteria" versus "what is
#: wrong with it".
VERIFY_INSTRUCTIONS = (
    "Judge each acceptance criterion separately against the diff and the quoted "
    "evidence, and nothing else. You have not been given the implementer's "
    "reasoning and must not ask for it.\n"
    "Return pass only when every criterion is demonstrably met. Return fail when "
    "one is demonstrably not met, naming which. Return inconclusive when you "
    "could not evaluate — an absent suite, evidence that does not bear on the "
    "criteria, a diff that does not touch the behaviour. Inconclusive is a real "
    "answer: reporting fail there sends the next attempt to fix code that may "
    "be fine."
)


@dataclass(frozen=True)
class Verification:
    """A verifier's answer about one task."""

    verdict: Verdict
    reasons: tuple[str, ...] = ()
    evidence: str = ""
    unmet: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return self.verdict is Verdict.PASS

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "reasons": list(self.reasons),
            "evidence": self.evidence,
            "unmet": list(self.unmet),
        }


def parse_verification(result: TaskResult) -> Verification:
    """Read a verifier's result into a verdict the scheduler can act on.

    Two sources, in order: a ``verification`` artifact, then the result's own
    verdict. The artifact wins because it is structured — a verifier that
    produced one has answered the question in a shape nobody has to interpret.

    An unparseable artifact becomes ``inconclusive``, never ``pass``. A verifier
    whose answer we cannot read has not verified anything, and defaulting to
    pass would make a broken verifier indistinguishable from a working one that
    always agrees.
    """
    artifact = next((a for a in result.artifacts if a.kind == "verification"), None)
    if artifact is not None:
        parsed = _from_payload(artifact.payload)
        if parsed is not None:
            return parsed
        return Verification(
            Verdict.INCONCLUSIVE,
            ("the verification artifact could not be read as a verdict",),
        )
    return Verification(
        result.verdict,
        (result.summary,) if result.summary else (),
        evidence=result.evidence,
    )


def _from_payload(payload: Mapping[str, Any]) -> Verification | None:
    raw = payload.get("verdict")
    if not isinstance(raw, str):
        return None
    try:
        verdict = Verdict(raw.strip().lower())
    except ValueError:
        return None
    reasons = payload.get("reasons")
    unmet = payload.get("unmet")
    return Verification(
        verdict=verdict,
        reasons=tuple(str(r) for r in reasons) if isinstance(reasons, list) else (),
        evidence=str(payload.get("evidence") or ""),
        unmet=tuple(str(u) for u in unmet) if isinstance(unmet, list) else (),
    )


def verifier_brief(
    work_brief: TaskBrief,
    result: TaskResult,
    *,
    model: str = "sonnet",
    effort: str = "medium",
    diff: str = "",
) -> TaskBrief:
    """Build the brief a verifier gets for one finished task.

    Note what is absent: ``work_brief.prompt`` and ``result.summary``. The
    prompt is what the implementer was told to do, and the summary is its
    account of what it did — both are the implementer's framing, and handing
    them over is how a verifier ends up agreeing with a story rather than
    checking a diff.
    """
    context: list[str] = []
    if result.changed_paths:
        context.append("files the implementer reports changing:")
        context += [f"  {p}" for p in result.changed_paths]
    if diff:
        context.append("diff:")
        context.append(diff)
    if result.evidence.strip():
        context.append("evidence the implementer quoted:")
        context.append(result.evidence.strip())
    if result.checks.strip():
        context.append("checks the implementer quoted:")
        context.append(result.checks.strip())

    return TaskBrief(
        run_id=work_brief.run_id,
        task_id=f"{work_brief.task_id}::verify",
        name=f"verify {work_brief.name}",
        role="verify",
        prompt=VERIFY_INSTRUCTIONS,
        criticality=work_brief.criticality,
        ownership=(),
        acceptance=work_brief.acceptance,
        context=tuple(context),
        inputs=(),
        attempts=(),
        model=model,
        effort=effort,
        budget=TaskBudget(),
        writes=False,
    )


def reviewer_brief(
    run_id: str,
    *,
    goal: str,
    diff: str = "",
    changed_paths: tuple[str, ...] = (),
    model: str = "opus",
    effort: str = "high",
    probes: tuple[str, ...] = REVIEW_PROBES,
) -> TaskBrief:
    """Build the adversarial pass that runs once per node, not once per task."""
    context: list[str] = ["probe at least these, and say what you found for each:"]
    context += [f"  - {p}" for p in probes]
    if changed_paths:
        context.append("changed in this run:")
        context += [f"  {p}" for p in changed_paths]
    if diff:
        context.append("diff:")
        context.append(diff)
    return TaskBrief(
        run_id=run_id,
        task_id=f"{run_id}::review",
        name="adversarial review",
        role="review",
        prompt=(
            "Find reasons this should not ship. You are not being asked whether "
            "it is correct — assume someone competent already thought so. Rank "
            "what you find by the preconditions an attacker or a caller needs, "
            "never by an invented severity score."
        ),
        criticality=Criticality.ELEVATED,
        ownership=(),
        acceptance=(f"the change achieves: {goal}",) if goal else (),
        context=tuple(context),
        model=model,
        effort=effort,
        writes=False,
    )


def debugger_brief(
    work_brief: TaskBrief,
    result: TaskResult,
    *,
    model: str = "sonnet",
    effort: str = "high",
) -> TaskBrief:
    """Build the brief that turns a failure into a classification.

    The classification decides retry vs escalate vs replan, so this is the one
    place where a wrong answer costs a whole strategy rather than one attempt.
    The charter is therefore narrow: name where the failure lives, do not fix it.
    """
    kinds = ", ".join(k.value for k in FailureKind)
    context = []
    if result.summary:
        context.append(f"what failed: {result.summary}")
    if result.evidence.strip():
        context.append("output:")
        context.append(result.evidence.strip())
    if work_brief.attempts:
        context.append("previous attempts:")
        context += [a.render() for a in work_brief.attempts]
    return TaskBrief(
        run_id=work_brief.run_id,
        task_id=f"{work_brief.task_id}::debug",
        name=f"classify the failure in {work_brief.name}",
        role="debug",
        prompt=(
            f"Say where this failure lives. Answer with exactly one of: {kinds}.\n"
            f"code_bug — the implementation is wrong.\n"
            f"test_bug — the test is wrong; the implementation may be fine.\n"
            f"spec_bug — the criteria are wrong, contradictory, or unachievable.\n"
            f"architecture_bug — the design cannot support what is being asked.\n"
            f"environment_bug — a machine, binary, service or credential was missing.\n"
            f"Do not fix anything. A wrong answer here costs a whole strategy: "
            f"spec_bug and architecture_bug trigger a replan, environment_bug "
            f"retries at the same model, everything else escalates."
        ),
        acceptance=("the failure is assigned exactly one kind, with the line of "
                    "output that shows it",),
        context=tuple(context),
        model=model,
        effort=effort,
        writes=False,
    )


def parse_classification(result: TaskResult) -> FailureKind | None:
    """Read a debugger's answer, or None when it did not give one.

    None rather than a default: an unreadable classification means the debugger
    failed, and inventing ``CODE_BUG`` would silently convert "we do not know"
    into a decision with a cost.
    """
    artifact = next((a for a in result.artifacts if a.kind == "finding"), None)
    raw = None
    if artifact is not None:
        raw = artifact.payload.get("classification") or artifact.payload.get("kind")
    if raw is None:
        raw = result.summary.strip().lower()
    for kind in FailureKind:
        if isinstance(raw, str) and kind.value in raw.lower():
            return kind
    return None


def verification_artifact(run_id: str, task_id: str, v: Verification) -> Artifact:
    """The verifier's answer, as the artifact a later reader will look for."""
    return Artifact(run_id=run_id, task_id=task_id, kind="verification", payload=v.to_dict())


def render_verification(v: Verification) -> str:
    """One-line summary for an event log or a terminal."""
    head = f"verifier: {v.verdict.value}"
    if v.unmet:
        head += f" — unmet: {json.dumps(list(v.unmet))}"
    elif v.reasons:
        head += f" — {v.reasons[0]}"
    return head
