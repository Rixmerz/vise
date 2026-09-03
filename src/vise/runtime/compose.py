"""What a finished run says about the plan that should follow it.

The two planes have been complete and disconnected. `graph_builder_*` accepts
a whole plan — tasks, ownership, model, effort, validators. `RunState` records
everything a next plan would want to know: which tasks succeeded, which did
not and why, what the verifier said, why a replan was declined, what the run
learned. Between the two there was a person reading `state.json`.

This is the brief that closes that gap, and it deliberately stops one step
short of closing the loop. It reads the run and states, in the terms a
composer needs, what is already done, what is not, and under what constraint
the next graph may be written. It dispatches nothing and activates nothing.

Two decisions shape it, and both were made deliberately rather than defaulted:

**The composer may not author its own gate.** A composed node may declare only
the validators in ``BUILDER_VALIDATORS`` — every one of which runs vise's own
reviewed logic — and never ``command_exit`` or ``quality_check``, which run a
command the repository chose. The builder enforces this mechanically; the
brief states it so the composer is not left guessing.

**A person still starts the run.** There is no ``run_start`` MCP tool, by a
documented decision this module does not reopen. The composed graph is
something a person reads and runs; the plan it produces already reports its
own concurrency ceiling and cost, which is what makes that reading cheap.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vise.runtime.contracts import REPLAN_KINDS, TaskState
from vise.runtime.lessons import lessons_from
from vise.runtime.state import RunState
from vise.tools._graph_builder import BUILDER_VALIDATORS

#: Events that say something about the plan rather than about a task's work.
#: A composer that does not know the run was blocked before its first dispatch
#: will happily propose the same plan again.
_PLAN_LEVEL = ("human_gate", "replan_declined", "replan_unavailable", "unroutable",
               "not_admitted", "agent_refused", "spec_gate_blocked", "cancelled")


@dataclass
class Outcome:
    """One task, as the next plan needs to know it."""

    task_id: str
    state: str
    reason: str = ""
    classification: str = ""


@dataclass
class ComposeBrief:
    """Everything a composer needs, and nothing it has to infer."""

    run_id: str
    goal: str
    project_dir: str
    graph_name: str = ""
    node_id: str = ""
    succeeded: tuple[str, ...] = ()
    unfinished: tuple[Outcome, ...] = ()
    plan_level: tuple[str, ...] = ()
    lessons: tuple[str, ...] = ()
    spent_usd: float = 0.0
    replans: int = 0
    allowed_validators: tuple[str, ...] = field(
        default_factory=lambda: tuple(sorted(BUILDER_VALIDATORS))
    )

    @property
    def needs_a_new_plan(self) -> bool:
        """False when the run finished its work — there is nothing to compose."""
        return bool(self.unfinished)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "goal": self.goal,
            "project_dir": self.project_dir,
            "graph": self.graph_name,
            "node": self.node_id,
            "succeeded": list(self.succeeded),
            "unfinished": [
                {"task": o.task_id, "state": o.state, "reason": o.reason,
                 "classification": o.classification}
                for o in self.unfinished
            ],
            "plan_level": list(self.plan_level),
            "lessons": list(self.lessons),
            "spent_usd": round(self.spent_usd, 4),
            "replans": self.replans,
            "allowed_validators": list(self.allowed_validators),
            "needs_a_new_plan": self.needs_a_new_plan,
        }

    def render(self) -> str:
        out = [
            f"run {self.run_id} — {self.goal}",
            f"  from {self.graph_name or '?'}/{self.node_id or '?'} "
            f"in {self.project_dir}",
            f"  spent ${self.spent_usd:.2f}, replanned {self.replans}x",
            "",
        ]
        if self.succeeded:
            out.append("already done — do not plan these again:")
            out += [f"  {t}" for t in self.succeeded]
            out.append("")
        if not self.unfinished:
            out.append("every task succeeded. There is nothing to compose.")
            return "\n".join(out)

        out.append("not done:")
        for o in self.unfinished:
            kind = f" [{o.classification}]" if o.classification else ""
            out.append(f"  {o.task_id:<24} {o.state}{kind}")
            if o.reason:
                out.append(f"      {o.reason}")
        if self.plan_level:
            out += ["", "about the plan itself, not the work:"]
            out += [f"  {line}" for line in self.plan_level]
        if self.lessons:
            out += ["", "what this project's memory now carries:"]
            out += [f"  {line}" for line in self.lessons]
        out += [
            "",
            "Composing the next graph:",
            f"  validators a composed node may declare: "
            f"{', '.join(self.allowed_validators)}",
            "  command_exit and quality_check are refused — they run a command the",
            "  repository chose, and a planner does not get to write the condition",
            "  it is judged by.",
            "",
            "  Nothing here dispatches. Compose with graph_builder_*, then a person",
            "  reads the plan and runs it.",
        ]
        return "\n".join(out)


def _reason(record) -> tuple[str, str]:
    """(what it said, how it was classified) for a task that did not succeed."""
    said = record.note or ""
    classification = ""
    if record.result is not None:
        said = record.result.summary or said
        if record.result.classification is not None:
            classification = str(record.result.classification)
    if not classification:
        for attempt in reversed(record.attempts):
            if getattr(attempt, "classification", None):
                classification = str(attempt.classification)
                said = said or attempt.summary
                break
    return said, classification


def classification_of(record) -> str:
    """How a task that did not succeed was classified, or "" if it was not.

    Public because `vise runtime resume` needs the same answer, and deriving it
    a second time is how the two commands would come to disagree about whether
    a failure was the plan's fault. The subtlety worth not duplicating: a
    ``TaskRecord`` reloaded from disk has no ``result`` at all — results are
    rebuilt from artifacts, by ``TaskRecord.from_dict``'s explicit choice — and
    a later attempt overwrites it in memory. Either way the durable answer is
    on the attempt that carried it.
    """
    return _reason(record)[1]


def brief_from(state: RunState) -> ComposeBrief:
    """Read a run into the terms the next plan is written in.

    Deterministic on purpose. What to build next is a judgement, and this makes
    no attempt at it; what a composer should not have to re-derive is which
    work is already paid for and why the rest stopped, and getting that wrong
    is how a follow-up plan repeats a task that succeeded.
    """
    spec = state.spec
    succeeded, unfinished = [], []
    for task_id, record in sorted(state.tasks.items()):
        if record.state is TaskState.SUCCEEDED:
            succeeded.append(task_id)
            continue
        said, classification = _reason(record)
        unfinished.append(Outcome(
            task_id=task_id, state=record.state.value,
            reason=said, classification=classification,
        ))

    plan_level = []
    if state.human_gate:
        plan_level.append(f"parked for a person: {state.human_gate}")
    if state.cancelled:
        plan_level.append(f"cancelled: {state.cancel_reason or 'no reason recorded'}")
    seen = set()
    for event in state.events:
        kind = str(event.get("kind", ""))
        if kind not in _PLAN_LEVEL or kind in ("human_gate", "cancelled"):
            continue
        # A label with an empty detail — `replan_unavailable:` — claims
        # something happened and refuses to say what, which is worse for a
        # composer than the bare kind. Build the detail first, and only then
        # decide whether there is a separator to write.
        detail = " ".join(
            part for part in (str(event.get("task") or ""),
                              str(event.get("reason") or "")) if part
        ).strip()
        line = f"{kind}: {detail}" if detail else kind
        if line not in seen:
            seen.add(line)
            plan_level.append(line)

    # A replan that declined is the strongest plan-level signal there is: the
    # runtime looked at the failure, judged the plan wrong, and could not fix
    # it from inside the node.
    kinds = {k.value for k in REPLAN_KINDS}
    for outcome in unfinished:
        if outcome.classification in kinds:
            plan_level.append(
                f"{outcome.task_id} failed as {outcome.classification} — the plan "
                f"was wrong, not the work"
            )

    return ComposeBrief(
        run_id=spec.run_id,
        goal=spec.goal,
        project_dir=spec.project_dir,
        graph_name=spec.graph_name or "",
        node_id=spec.node_id or "",
        succeeded=tuple(succeeded),
        unfinished=tuple(unfinished),
        plan_level=tuple(plan_level),
        lessons=tuple(e.description for e in lessons_from(state)),
        spent_usd=state.ledger.spent.cost_usd,
        replans=state.replans,
    )
