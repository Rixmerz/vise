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

from dataclasses import dataclass, replace
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

    #: The most tasks that can ever run at once, given the dependency edges and
    #: the ownership claims. Not read off ``waves`` — those are already narrowed
    #: by ``max_parallel``, so reading them would report the budget back to
    #: itself and a graph capped at 3 would always "reach" 3.
    concurrency_ceiling: int = 0

    #: The longest dependency chain, which is the run's floor in wall-clock
    #: terms however wide the budget is.
    critical_path: int = 0

    #: The lanes the caller asked for. Kept because the ceiling above is
    #: deliberately structural: without this, a plan capped at 2 still reported
    #: that 4 tasks "can run at once", which is the same class of claim the
    #: ceiling was added to stop the plan from making.
    max_parallel: int = 0

    #: Observations that do not stop the run. Kept apart from ``problems``
    #: because that field is load-bearing — ``vise runtime plan`` exits 1 on it
    #: and ``run`` refuses to dispatch — and "you asked for three lanes and can
    #: use two" describes a plan that is correct and will run fine.
    notes: tuple[str, ...] = ()

    @property
    def estimated_cost_usd(self) -> float:
        return sum(w.estimated_cost_usd for w in self.waves)

    @property
    def effective_concurrency(self) -> int:
        """What this run actually reaches — the structure and the budget, both.

        The narrower of the two binds. Reporting the ceiling alone overstates a
        capped run; reporting the cap alone overstates a chain.
        """
        if not self.concurrency_ceiling:
            return 0
        if not self.max_parallel:
            return self.concurrency_ceiling
        return min(self.concurrency_ceiling, self.max_parallel)

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
        if self.concurrency_ceiling:
            out.append(
                f"shape: at most {self.effective_concurrency} task(s) run at once; "
                f"longest chain is {self.critical_path}"
            )
        if self.notes:
            out += [f"  note: {n}" for n in self.notes]
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
            "concurrency_ceiling": self.concurrency_ceiling,
            "effective_concurrency": self.effective_concurrency,
            "max_parallel": self.max_parallel,
            "critical_path": self.critical_path,
            "notes": list(self.notes),
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


#: A placeholder for the stand-in tasks the ceiling is measured on. Nothing
#: reads it — `_split_on_ownership` only looks at `ownership` and `writes` —
#: but `PlannedTask` requires a decision, and inventing a model here would put
#: a routing claim into a number that is purely about shape.
_NO_DECISION = RoutingDecision(model="", effort="", tier=0)


def _concurrency_ceiling(raw_waves: Sequence[Sequence[Any]]) -> int:
    """The most tasks that can ever be in flight at once.

    Computed from the RAW dependency waves with the parallelism cap lifted.
    Reading it off the rendered waves would be circular: those are already
    split by ``max_parallel``, so a graph capped at 3 would always report 3 and
    the number would confirm the budget instead of testing it.

    Ownership counts because it is a real constraint, not a preference: two
    tasks claiming the same path are never dispatched together, so a wave of
    six that all write one file has a ceiling of one.
    """
    widest = 0
    for raw in raw_waves:
        stand_ins = [
            PlannedTask(
                task_id=t.id, name=getattr(t, "name", t.id), role="", agent_id=None,
                decision=_NO_DECISION,
                ownership=tuple(getattr(t, "ownership", ()) or ()),
                writes=bool(getattr(t, "writes", True)),
            )
            for t in raw
        ]
        # `len(raw)` as the cap is the cap lifted: no group can exceed the wave.
        groups = _split_on_ownership(stand_ins, len(raw) or 1)
        widest = max(widest, max((len(g) for g in groups), default=0))
    return widest


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
    spent_usd: float = 0.0,
) -> RunPlan:
    """Turn a DAG node's tasks into a readable, costed, checked plan.

    ``project_dir`` opts the plan into the same spec gate the scheduler
    enforces. Reporting it here is what makes the block cost nothing to
    discover: the alternative is learning you are gated from the command that
    spends money.
    """
    registry = (
        registry if registry is not None else AgentRegistry.for_project(project_dir)
    )
    router = router or ModelRouter()
    ledger = BudgetLedger(budget or RunBudget())
    # Money already spent against this goal, when the plan continues a prior
    # run. Without it a continuation's preview measures its own cost against
    # the whole ceiling and reports "fits" for a chain that is already over —
    # the ceiling would bound the last link instead of the run of runs.
    if spent_usd:
        ledger.spent = replace(ledger.spent, cost_usd=ledger.spent.cost_usd + spent_usd)
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

    ceiling = _concurrency_ceiling(raw_waves)
    declared = (budget or RunBudget()).max_parallel
    notes: list[str] = []
    if ceiling and declared > ceiling:
        notes.append(
            f"max_parallel is {declared}, but dependencies and ownership allow at "
            f"most {ceiling} task(s) at once — the extra lane(s) buy nothing here"
        )
    elif ceiling and declared < ceiling:
        # The other direction, and the one that was silent: the structure is
        # wider than the budget, so the cap is what makes the run narrow. That
        # is actionable — raising it makes the same plan finish sooner — and a
        # plan that does not say it leaves the caller to guess.
        notes.append(
            f"the structure allows {ceiling} task(s) at once but max_parallel is "
            f"{declared} — raising it would widen this run"
        )

    return RunPlan(
        waves=tuple(waves),
        problems=tuple(problems),
        unschedulable=tuple(unschedulable),
        concurrency_ceiling=ceiling,
        critical_path=len(raw_waves),
        max_parallel=declared,
        notes=tuple(notes),
    )
