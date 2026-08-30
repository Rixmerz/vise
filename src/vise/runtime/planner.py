"""Which tasks form which wave, and what the whole thing will cost.

This is the half of the scheduler that needs no execution: it reads a DAG node,
derives the waves, resolves each task to an agent and a model, checks the plan
against ownership and budget, and hands back something a person can read
*before* anything runs. Dispatch, retry and escalation are M2 — see
docs/scheduler.md.

Planning ahead of execution is not just a convenience. A plan nobody can read is
a plan nobody can refuse, and the moment to refuse a four-dollar run over a
two-line change is before it starts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from vise.runtime import ownership as _own
from vise.runtime.budget import BudgetLedger
from vise.runtime.contracts import RunBudget
from vise.runtime.registry import AgentRegistry, AgentSpec, capability_hint
from vise.runtime.routing import ModelRouter, RoutingDecision
from vise.runtime.spec_gate import check as spec_gate_check


@dataclass(frozen=True)
class PlannedTask:
    """One task, resolved to who runs it and on what."""

    task_id: str
    name: str
    role: str
    agent_id: str | None
    decision: RoutingDecision
    ownership: tuple[str, ...]
    writes: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "name": self.name,
            "role": self.role,
            "agent_id": self.agent_id,
            "ownership": list(self.ownership),
            "writes": self.writes,
            "routing": self.decision.to_dict(),
        }


@dataclass(frozen=True)
class PlannedWave:
    """Tasks that may run concurrently.

    A wave is a *planning* unit, not a barrier. A scheduler that waits for every
    task in a wave before starting any task in the next one throws away the
    wall-clock of its slowest member. Waves exist so a person can read the plan;
    dispatch follows dependencies.
    """

    index: int
    tasks: tuple[PlannedTask, ...]

    @property
    def estimated_cost_usd(self) -> float:
        return sum(t.decision.estimated_cost_usd for t in self.tasks)


@dataclass(frozen=True)
class RunPlan:
    """The whole plan, including the parts that do not work."""

    waves: tuple[PlannedWave, ...] = ()
    problems: tuple[str, ...] = ()
    unschedulable: tuple[str, ...] = ()

    @property
    def estimated_cost_usd(self) -> float:
        return sum(w.estimated_cost_usd for w in self.waves)

    @property
    def task_count(self) -> int:
        return sum(len(w.tasks) for w in self.waves)

    def render(self) -> str:
        out: list[str] = []
        for wave in self.waves:
            out.append(f"wave {wave.index + 1}  ({len(wave.tasks)} task(s), "
                       f"~${wave.estimated_cost_usd:.2f})")
            for t in wave.tasks:
                agent = t.agent_id or "UNROUTABLE"
                out.append(
                    f"  {t.task_id:<24} {agent:<20} "
                    f"{t.decision.model}/{t.decision.effort}"
                )
                for reason in t.decision.reasons:
                    out.append(f"      · {reason}")
        out.append(f"\ntotal: {self.task_count} task(s), ~${self.estimated_cost_usd:.2f}")
        if self.problems:
            out.append("\nproblems — these must be fixed before the run can start:")
            out += [f"  ! {p}" for p in self.problems]
        return "\n".join(out)

    def to_dict(self) -> dict[str, Any]:
        return {
            "waves": [
                {"index": w.index, "tasks": [t.to_dict() for t in w.tasks]}
                for w in self.waves
            ],
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
            "task_count": self.task_count,
            "problems": list(self.problems),
            "unschedulable": list(self.unschedulable),
        }


def dependency_waves(
    tasks: Sequence[Any],
    completed: Iterable[str] = (),
) -> tuple[list[list[Any]], list[str]]:
    """Group tasks into dependency waves, plus the ids that can never run.

    Derived, never declared. Declaring waves in YAML duplicates the dependency
    edges, and the two copies drift — at which point the graph says one thing and
    the schedule does another, with no way to tell which was intended.

    The second return value is the tasks left over when a pass adds nothing:
    a cycle, or a dependency on an id that does not exist. They are returned
    rather than raised, so a plan can show every runnable task *and* name what is
    broken, instead of failing on the first problem and hiding the rest.
    """
    done = set(completed)
    remaining = [t for t in tasks if t.id not in done]
    waves: list[list[Any]] = []
    while remaining:
        ready = [t for t in remaining if all(d in done for d in t.dependencies)]
        if not ready:
            return waves, [t.id for t in remaining]
        waves.append(ready)
        done.update(t.id for t in ready)
        ready_ids = {t.id for t in ready}
        remaining = [t for t in remaining if t.id not in ready_ids]
    return waves, []


def _split_on_ownership(planned: Sequence[PlannedTask], max_parallel: int) -> list[list[PlannedTask]]:
    """Split one dependency wave into groups that may actually run together.

    Two constraints, applied in that order: tasks whose ownership intersects
    cannot share a group, and no group exceeds ``max_parallel``. Read-only tasks
    never conflict — they hold no claim on the tree — so they pack freely.
    """
    by_id = {t.task_id: t for t in planned}
    claims: dict[str, object] = {}
    readonly: list[PlannedTask] = []
    for t in planned:
        if not t.writes:
            readonly.append(t)
        else:
            claims[t.task_id] = t.ownership
    groups = [[by_id[i] for i in g] for g in _own.partition(claims)]
    if readonly:
        if groups:
            groups[0].extend(readonly)
        else:
            groups.append(readonly)
    if not max_parallel:
        return groups
    chunked: list[list[PlannedTask]] = []
    for group in groups:
        for i in range(0, len(group), max_parallel):
            chunked.append(group[i: i + max_parallel])
    return chunked


def plan(
    tasks: Sequence[Any],
    *,
    registry: AgentRegistry | None = None,
    router: ModelRouter | None = None,
    budget: RunBudget | None = None,
    completed: Iterable[str] = (),
    project_dir: str | None = None,
    change: str = "",
) -> RunPlan:
    """Turn a DAG node's tasks into a readable, costed, checked plan.

    ``project_dir`` opts the plan into the same spec gate the scheduler
    enforces. Reporting it here is what makes the block cost nothing to
    discover: the alternative is learning you are gated from the command that
    spends money.
    """
    registry = registry if registry is not None else AgentRegistry.bundled()
    router = router or ModelRouter()
    ledger = BudgetLedger(budget or RunBudget())
    remaining = ledger.remaining_usd()

    raw_waves, unschedulable = dependency_waves(tasks, completed)
    problems: list[str] = []
    if project_dir is not None:
        verdict = spec_gate_check(
            project_dir,
            change=change,
            writes=any(bool(getattr(t, "writes", True)) for t in tasks),
        )
        if not verdict.ok:
            problems.append(f"spec gate: {verdict.reason}")
    if unschedulable:
        problems.append(
            "unschedulable (dependency cycle or unknown dependency): "
            + ", ".join(sorted(unschedulable))
        )

    waves: list[PlannedWave] = []
    running_cost = 0.0
    index = 0
    for raw in raw_waves:
        planned: list[PlannedTask] = []
        for task in raw:
            role = getattr(task, "role", None) or ""
            agent: AgentSpec | None = None
            if not role:
                problems.append(
                    f"task '{task.id}' declares no role — the runtime cannot pick an "
                    f"agent for it, so it must not be planned"
                )
            else:
                # A read-only task passes no `writes` filter: an agent that can
                # write is perfectly able to do read-only work, and filtering to
                # writes=False would exclude every implementer from reviewing
                # its own area.
                resolution = registry.resolve(
                    role,
                    writes=True if getattr(task, "writes", True) else None,
                    capability=capability_hint(task),
                )
                agent = resolution.agent
                if agent is None:
                    detail = resolution.reason or "no agent takes it"
                    if resolution.ambiguous_among:
                        detail += (
                            f" ({', '.join(resolution.ambiguous_among)}) — name the "
                            f"capability in the task id, or pin an agent"
                        )
                    problems.append(f"task '{task.id}' is unroutable: {detail}")
            budget_left = None if remaining is None else max(0.0, remaining - running_cost)
            decision = router.route(task, agent=agent, budget_remaining_usd=budget_left)
            running_cost += decision.estimated_cost_usd
            if not decision.affordable:
                problems.append(
                    f"task '{task.id}' does not fit the remaining run budget "
                    f"(~${decision.estimated_cost_usd:.2f})"
                )
            pt = PlannedTask(
                task_id=task.id,
                name=getattr(task, "name", task.id),
                role=role,
                agent_id=agent.id if agent else None,
                decision=decision,
                ownership=tuple(getattr(task, "ownership", ()) or ()),
                writes=bool(getattr(task, "writes", True)),
            )
            planned.append(pt)
        for group in _split_on_ownership(planned, (budget or RunBudget()).max_parallel):
            waves.append(PlannedWave(index=index, tasks=tuple(group)))
            index += 1

    return RunPlan(
        waves=tuple(waves),
        problems=tuple(problems),
        unschedulable=tuple(unschedulable),
    )
