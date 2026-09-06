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
from typing import Any, Mapping, Sequence

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

#: The lenses a panel is briefed from, in order, cycling when more verifiers
#: are asked for than there are lenses.
#:
#: Distinct questions rather than the same question asked louder. Three agents
#: given one prompt return one opinion three times — the disagreement that
#: makes a panel worth its price has to be built into what they are each asked,
#: which is the same reason `research-graph.yaml` pays one task to look for the
#: counter-case.
#:
#: The first is today's question, unchanged, so a panel of one is exactly the
#: single verifier that shipped before panels existed.
LENSES: tuple[tuple[str, str], ...] = (
    ("criteria", ""),
    ("evidence", (
        "Your lens is the evidence. Do not reason about whether the change looks "
        "correct — ask whether the quoted output actually demonstrates the "
        "criterion, and whether someone running that command would see it. "
        "Output that does not bear on a criterion leaves it unmet, however "
        "convincing the diff looks."
    )),
    ("adversary", (
        "Your lens is the case against. Assume the criteria are met in the happy "
        "path and look for the input that breaks them: an empty or maximum "
        "value, non-ASCII text, the second call, the caller who is authenticated "
        "but not authorised. A criterion that holds only for the example in the "
        "evidence is not met."
    )),
    ("regression", (
        "Your lens is what this changed that nobody asked it to. Read the diff "
        "for behaviour an existing caller depends on and no criterion mentions. "
        "A criterion met by breaking something else is not met."
    )),
)


def lens_at(index: int) -> tuple[str, str]:
    """The lens for the ``index``-th verifier of a panel, cycling."""
    return LENSES[index % len(LENSES)]


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

    Two sources, in order: a ``verification`` artifact carrying a verdict, then
    the result's own verdict. A verdict-carrying artifact wins because it is
    structured — a verifier that produced one has answered the question in a
    shape nobody has to interpret.

    An artifact carrying a ``verdict`` field that cannot be read stays
    ``inconclusive`` and never falls through. That is a verifier that tried to
    answer and produced something unreadable, and reading past it to the bare
    verdict would make a broken verifier indistinguishable from a working one.

    An artifact with no ``verdict`` field at all is a different thing: not a
    broken answer but notes. ``verification`` is one of the artifact kinds the
    worker contract offers, so a verifier that files a criteria table or a test
    list under it has done nothing wrong — and letting those notes shadow the
    verdict it *did* give on the result turned a live ``pass`` into
    ``inconclusive``, blocked the task, and stalled every task behind it.
    """
    for artifact in result.artifacts:
        if artifact.kind != "verification":
            continue
        parsed = _from_payload(artifact.payload)
        if parsed is not None:
            return parsed
        if "verdict" in artifact.payload:
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
    index: int = 0,
    panel: int = 1,
) -> TaskBrief:
    """Build the brief the ``index``-th verifier of a panel gets for one task.

    Note what is absent: ``work_brief.prompt`` and ``result.summary``. The
    prompt is what the implementer was told to do, and the summary is its
    account of what it did — both are the implementer's framing, and handing
    them over is how a verifier ends up agreeing with a story rather than
    checking a diff.

    A panel member is also not told what the others think, or that there are
    others. Both would defeat the point: a verifier who knows two colleagues
    already passed is deciding whether to disagree with them, not whether the
    criteria are met.
    """
    name, instruction = lens_at(index)
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
        task_id=verifier_id(work_brief.task_id, index, panel),
        name=f"verify {work_brief.name}" + (f" ({name})" if panel > 1 else ""),
        role="verify",
        prompt=f"{VERIFY_INSTRUCTIONS}\n\n{instruction}" if instruction else VERIFY_INSTRUCTIONS,
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


def verifier_id(task_id: str, index: int, panel: int) -> str:
    """``task::verify`` for a single verifier, ``task::verify[2]`` in a panel.

    Unchanged for a panel of one, because that is the id every recorded run and
    every reader of a state file already knows.
    """
    return f"{task_id}::verify" if panel <= 1 else f"{task_id}::verify[{index + 1}]"


@dataclass(frozen=True)
class PanelVerdict:
    """What a panel of verifiers concluded, and on what split."""

    verdict: Verdict
    verifications: tuple[Verification, ...] = ()
    reasons: tuple[str, ...] = ()
    unmet: tuple[str, ...] = ()

    @property
    def tally(self) -> dict[str, int]:
        counts = {v.value: 0 for v in Verdict}
        for verification in self.verifications:
            counts[verification.verdict.value] += 1
        return counts

    def render(self) -> str:
        return ", ".join(f"{n} {name}" for name, n in self.tally.items() if n)


def decide_panel(verifications: Sequence[Verification]) -> PanelVerdict:
    """Fold a panel's answers into one verdict. A majority, decided in code.

    A majority rather than unanimity, because the lenses differ on purpose: a
    verifier looking at whether the evidence reproduces may have nothing useful
    to say about a criterion that is about wording, and unanimity would let the
    lens least able to evaluate veto the ones that could.

    No majority either way is ``INCONCLUSIVE`` and not a quiet pass. Verifiers
    who could not decide have not decided, and the task is blocked rather than
    accepted — the same direction every gate in this runtime fails.

    For one verifier this is exactly the single-verifier behaviour that shipped
    before panels: its verdict, unchanged.
    """
    verifications = tuple(verifications)
    if not verifications:
        return PanelVerdict(
            Verdict.INCONCLUSIVE,
            reasons=("no verifier reported",),
        )
    counts = {v: 0 for v in Verdict}
    for verification in verifications:
        counts[verification.verdict] += 1
    needed = len(verifications) / 2

    if counts[Verdict.PASS] > needed:
        return PanelVerdict(Verdict.PASS, verifications)
    if counts[Verdict.FAIL] > needed:
        # The reasons of the verifiers who said fail, and only those: a passing
        # verifier's notes in a failing panel would send the next attempt to
        # fix what somebody thought was already right.
        failing = [v for v in verifications if v.verdict is Verdict.FAIL]
        return PanelVerdict(
            Verdict.FAIL,
            verifications,
            reasons=tuple(dict.fromkeys(r for v in failing for r in v.reasons)),
            unmet=tuple(dict.fromkeys(u for v in failing for u in v.unmet)),
        )
    return PanelVerdict(
        Verdict.INCONCLUSIVE,
        verifications,
        reasons=tuple(dict.fromkeys(r for v in verifications for r in v.reasons)),
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
