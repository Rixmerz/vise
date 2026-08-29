"""The data the agent runtime is made of — see docs/worker-contract.md.

Everything here is inert: dataclasses, enums, and the serialisation they need to
survive a process boundary. No I/O, no subprocess, no model call. The behaviour
that reads these lives in ``routing``, ``ownership``, ``budget``, ``honesty`` and
``planner``, and keeping the split sharp is what lets a contract change be
reviewed without reading a scheduler.

One rule runs through all of it: **a claim and a conclusion are different
fields.** ``TaskResult.verdict`` is what the worker says about its own work;
``TaskState.SUCCEEDED`` is what the runtime concludes after a separate verifier
agrees. Collapsing the two is the failure mode this whole design exists to
prevent, so the type system keeps them apart.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class TaskState(StrEnum):
    """Where a task is in its lifecycle.

    ``SUCCEEDED`` is the runtime's conclusion, never a worker's claim: it means
    a worker reported a pass *and* the honesty gates accepted the result *and* a
    verifier agreed. ``INCONCLUSIVE`` results land in ``BLOCKED``, not
    ``FAILED`` — a suite that could not run has said nothing about the code.
    """

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    WAITING_HUMAN = "waiting_human"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset(
    {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED}
)


class Verdict(StrEnum):
    """What a worker claims about its own output.

    ``INCONCLUSIVE`` is not a polite ``FAIL``. A worker that could not reach the
    database has not shown the code is wrong, and recording that as a failure
    sends the next attempt to fix code that may be fine — while also spending a
    rung of the escalation ladder on an environment problem.
    """

    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


class Criticality(StrEnum):
    """How expensive being wrong is. Routing input, not a priority."""

    ROUTINE = "routine"
    ELEVATED = "elevated"
    CRITICAL = "critical"


class Complexity(StrEnum):
    """How hard the work is expected to be. Raises the router's floor."""

    TRIVIAL = "trivial"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FailureKind(StrEnum):
    """Where a failure actually lives.

    This is the input that decides retry vs escalate vs replan. ``SPEC_BUG`` and
    ``ARCHITECTURE_BUG`` cannot be fixed by trying the same task harder, so they
    route to a replan; ``ENVIRONMENT_BUG`` is a retry at the same rung, because
    escalating it spends the top tier on a missing binary.
    """

    CODE_BUG = "code_bug"
    TEST_BUG = "test_bug"
    SPEC_BUG = "spec_bug"
    ARCHITECTURE_BUG = "architecture_bug"
    ENVIRONMENT_BUG = "environment_bug"


#: Failure kinds where trying the same task again is pointless — the plan, not
#: the work, is what was wrong.
REPLAN_KINDS = frozenset({FailureKind.SPEC_BUG, FailureKind.ARCHITECTURE_BUG})

#: Failure kinds that earn a retry at the same model and effort rather than an
#: escalation. The work was never attempted, so nothing about the attempt is
#: evidence that a bigger model would do better.
RETRY_KINDS = frozenset({FailureKind.ENVIRONMENT_BUG})


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Usage:
    """What one attempt actually consumed. Immutable and additive."""

    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    wall_time_s: float = 0.0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            tokens_in=self.tokens_in + other.tokens_in,
            tokens_out=self.tokens_out + other.tokens_out,
            cost_usd=self.cost_usd + other.cost_usd,
            wall_time_s=self.wall_time_s + other.wall_time_s,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": round(self.cost_usd, 6),
            "wall_time_s": round(self.wall_time_s, 3),
        }


@dataclass(frozen=True)
class RunBudget:
    """Ceilings for a whole run. ``0`` means unset, never unlimited.

    An unset ceiling is a ceiling the operator did not state, and the budget
    ledger reports it as such rather than pretending the run is unbounded. The
    distinction matters at the point where someone asks why a run cost $12.
    """

    max_cost_usd: float = 0.0
    max_workers: int = 0
    max_parallel: int = 4
    max_wall_time_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_cost_usd": self.max_cost_usd,
            "max_workers": self.max_workers,
            "max_parallel": self.max_parallel,
            "max_wall_time_s": self.max_wall_time_s,
        }


@dataclass(frozen=True)
class TaskBudget:
    """Ceilings for one task. ``0`` inherits the run's, never "unlimited"."""

    max_cost_usd: float = 0.0
    max_turns: int = 0
    timeout_s: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_cost_usd": self.max_cost_usd,
            "max_turns": self.max_turns,
            "timeout_s": self.timeout_s,
        }


@dataclass(frozen=True)
class Artifact:
    """Structured output one worker hands the next.

    Workers communicate through these, not through transcripts. That is a cost
    decision and a correctness one at the same time: a worker's transcript is
    both expensive and a worse input than its conclusions, because it carries
    every hypothesis the worker abandoned with the same weight as the one it
    kept.
    """

    run_id: str
    task_id: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task_id": self.task_id,
            "kind": self.kind,
            "payload": self.payload,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Artifact:
        return cls(
            run_id=str(data.get("run_id", "")),
            task_id=str(data.get("task_id", "")),
            kind=str(data.get("kind", "")),
            payload=data.get("payload") if isinstance(data.get("payload"), dict) else {},
            created_at=str(data.get("created_at") or _utcnow()),
        )


@dataclass(frozen=True)
class Attempt:
    """One past try at a task, as the next try needs to see it.

    Carried into every subsequent brief. An agent that cannot see what the last
    agent tried will try it again — confidently, and at full price. This is the
    cheapest anti-loop device in the system, and it is a few hundred tokens.
    """

    number: int
    model: str
    effort: str
    verdict: Verdict
    summary: str = ""
    classification: FailureKind | None = None
    usage: Usage = field(default_factory=Usage)

    def render(self) -> str:
        tag = f"{self.model}/{self.effort}"
        if self.classification:
            tag = f"{tag}, {self.classification.value}"
        return f"  attempt {self.number} [{tag}] {self.summary or self.verdict.value}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "model": self.model,
            "effort": self.effort,
            "verdict": self.verdict.value,
            "summary": self.summary,
            "classification": self.classification.value if self.classification else None,
            "usage": self.usage.to_dict(),
        }


@dataclass(frozen=True)
class TaskBrief:
    """Everything a worker is given, and nothing else.

    ``context`` is resolved files, symbols, decisions and relevant experience —
    not the session transcript. Handing each worker the whole conversation is
    the default that makes multi-agent orchestration cost more than doing the
    work serially, and it degrades quality on top: the three relevant files
    compete with forty that are not.
    """

    run_id: str
    task_id: str
    name: str
    role: str
    prompt: str = ""
    criticality: Criticality = Criticality.ROUTINE
    ownership: tuple[str, ...] = ()
    acceptance: tuple[str, ...] = ()
    context: tuple[str, ...] = ()
    inputs: tuple[Artifact, ...] = ()
    attempts: tuple[Attempt, ...] = ()
    tools_blocked: tuple[str, ...] = ()
    mcps_enabled: tuple[str, ...] = ("*",)
    model: str = "sonnet"
    effort: str = "medium"
    budget: TaskBudget = field(default_factory=TaskBudget)
    writes: bool = True

    def render(self) -> str:
        """The brief as text, in the order a worker should read it.

        Acceptance first — a worker that reads the prompt before the criteria
        optimises for the prompt. Prior attempts last, right before it starts,
        because that is the part it must not skim.
        """
        out = [f"task: {self.task_id} — {self.name}", f"role: {self.role}"]
        if self.criticality is not Criticality.ROUTINE:
            out.append(f"criticality: {self.criticality.value}")
        out.append(f"model: {self.model}/{self.effort}")
        if self.acceptance:
            out.append("acceptance criteria — you are judged against these and nothing else:")
            out += [f"  - {a}" for a in self.acceptance]
        else:
            out.append(
                "acceptance criteria: none declared — this task can be marked done "
                "but never verified. Say so rather than inventing criteria."
            )
        if self.ownership and self.writes:
            out.append("you may write only these paths:")
            out += [f"  - {o}" for o in self.ownership]
        elif self.writes:
            out.append(
                "ownership: undeclared — you are running alone because nothing "
                "bounds what you may touch. Keep the diff minimal."
            )
        if self.prompt:
            out.append(f"\n{self.prompt}")
        if self.context:
            out.append("context:")
            out += [f"  {c}" for c in self.context]
        if self.inputs:
            out.append("inputs from upstream tasks:")
            out += [
                f"  {a.task_id}/{a.kind}: {json.dumps(a.payload, sort_keys=True)[:400]}"
                for a in self.inputs
            ]
        if self.attempts:
            out.append("previous attempts on this task — already tried, do not repeat:")
            out += [a.render() for a in self.attempts]
        return "\n".join(out)


@dataclass(frozen=True)
class TaskResult:
    """What a worker owes back.

    ``verdict`` is a claim. It becomes a conclusion only after ``honesty``
    accepts it and a verifier agrees — see docs/worker-contract.md.
    """

    task_id: str
    verdict: Verdict
    summary: str = ""
    evidence: str = ""
    checks: str = ""
    changed_paths: tuple[str, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    usage: Usage = field(default_factory=Usage)
    classification: FailureKind | None = None
    model: str = ""
    effort: str = ""

    def to_attempt(self, number: int) -> Attempt:
        """Fold this result into the history the next attempt's brief carries."""
        return Attempt(
            number=number,
            model=self.model,
            effort=self.effort,
            verdict=self.verdict,
            summary=self.summary,
            classification=self.classification,
            usage=self.usage,
        )

    def failed(self) -> TaskResult:
        """This result, downgraded to a failure, preserving everything else."""
        return replace(self, verdict=Verdict.FAIL)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "verdict": self.verdict.value,
            "summary": self.summary,
            "evidence": self.evidence,
            "checks": self.checks,
            "changed_paths": list(self.changed_paths),
            "artifacts": [a.to_dict() for a in self.artifacts],
            "usage": self.usage.to_dict(),
            "classification": self.classification.value if self.classification else None,
            "model": self.model,
            "effort": self.effort,
        }


@dataclass(frozen=True)
class RunSpec:
    """One invocation of the runtime against one DAG node of one workflow.

    A run never spans workflow nodes. The node's gate is what decides whether
    the phase advances, and a run that outlived the node it was planned for
    would be reporting into a gate that had already fired.
    """

    run_id: str
    goal: str
    project_dir: str
    graph_name: str = ""
    node_id: str = ""
    budget: RunBudget = field(default_factory=RunBudget)
    created_at: str = field(default_factory=_utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "project_dir": self.project_dir,
            "graph_name": self.graph_name,
            "node_id": self.node_id,
            "budget": self.budget.to_dict(),
            "created_at": self.created_at,
        }
