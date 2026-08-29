"""How a task is actually executed — see docs/worker-contract.md.

A worker is anything that turns a brief into a result: a Claude Code subagent, a
shell script, a mock. The protocol is three lines because everything that makes
the runtime trustworthy lives *around* the worker rather than inside it — the
brief it is handed, the gates its claim has to survive, the verifier that has to
agree.

Nothing here calls a model. ``execute`` is the seam the Claude adapter will fill
in M3, and it is written so that filling it in changes one class and no rule:
the honesty gates run against the adapter's results exactly as they run against
the mock's, because they never see which produced them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from vise.runtime.contracts import TaskBrief, TaskResult, Usage, Verdict
from vise.runtime.honesty import GateOutcome, check_result, tree_hash


@runtime_checkable
class Worker(Protocol):
    """Executes one brief and reports one result. Nothing else."""

    def run(self, brief: TaskBrief) -> TaskResult:  # pragma: no cover - protocol
        ...


@dataclass
class MockWorker:
    """A deterministic worker for tests and dry runs.

    Records every brief it is handed, which is what makes the brief itself
    testable: "does an escalated attempt actually carry the previous attempt's
    classification into the prompt" is a question about a string, and the answer
    should not require a model call to find out.
    """

    scripted: dict[str, list[TaskResult]] = field(default_factory=dict)
    default_verdict: Verdict = Verdict.PASS
    briefs: list[TaskBrief] = field(default_factory=list)

    def run(self, brief: TaskBrief) -> TaskResult:
        self.briefs.append(brief)
        queue = self.scripted.get(brief.task_id)
        if queue:
            return queue.pop(0)
        return TaskResult(
            task_id=brief.task_id,
            verdict=self.default_verdict,
            summary=f"mock worker ran {brief.task_id}",
            evidence="$ mock\nok",
            checks="$ mock\nok",
            usage=Usage(tokens_in=100, tokens_out=100, cost_usd=0.0),
            model=brief.model,
            effort=brief.effort,
        )


def execute(
    brief: TaskBrief,
    worker: Worker,
    *,
    project_dir: str | Path | None = None,
) -> tuple[TaskResult, GateOutcome]:
    """Run one brief and put the result through the honesty gates.

    The baseline tree hash is taken *here*, immediately before the worker starts,
    rather than by the caller. A baseline captured earlier would include whatever
    happened between then and now — another task's writes, a rebase, the user
    editing a file — and rule 3 would be comparing the wrong two states.

    Returns both the (possibly downgraded) result and the gate outcome. Callers
    that only want the verdict should read the result; callers reporting to a
    human want the refusals, which say what was actually wrong.
    """
    baseline = tree_hash(project_dir) if brief.writes else None
    result = worker.run(brief)
    current = tree_hash(project_dir) if brief.writes else None
    outcome = check_result(brief, result, baseline_tree=baseline, current_tree=current)
    return (outcome.result or result), outcome
