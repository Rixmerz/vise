"""The dispatch loop — see docs/scheduler.md.

The planner said what would happen. This makes it happen: it walks a DAG node's
tasks, dispatches the ones that are ready and admissible, collects results, puts
each through the honesty gates, and decides retry / escalate / replan / stop.

Three boundaries it does not cross, all of them from docs/agent-runtime.md:

  - It never traverses a graph edge. When every task reaches a terminal state,
    ``is_dag_complete`` goes true and the node's own gate decides the phase. The
    scheduler reports; the gate decides.
  - It never grades a worker's claim. ``honesty`` does, and ``recovery`` decides
    what the outcome means.
  - It never widens a budget, an ownership claim, or a tool set.

Concurrency is threads, not processes or asyncio, because a worker is I/O-bound
by construction: the Claude adapter shells out and waits. Threads keep the loop
readable and let a worker use ordinary blocking subprocess calls.
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, wait
from concurrent.futures import FIRST_COMPLETED
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from vise.runtime import ownership as _own
from vise.runtime.artifacts import ArtifactStore
from vise.runtime.context import ContextResolver
from vise.runtime.contracts import (
    TERMINAL_STATES,
    Artifact,
    Criticality,
    FailureKind,
    RunSpec,
    TaskBrief,
    TaskBudget,
    TaskResult,
    TaskState,
    Verdict,
)
from vise.runtime.honesty import GateOutcome, tree_hash
from vise.runtime.replan import default_replanner
from vise.runtime.recovery import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_REPLANS,
    Recovery,
    classify_from_text,
    decide,
)
from vise.runtime.registry import AgentRegistry, capability_hint
from vise.runtime.routing import TOP, ModelRouter, tier_of
from vise.runtime.spec_gate import SpecGateVerdict
from vise.runtime.spec_gate import check as spec_gate_check
from vise.runtime.state import RunState, cancel_requested, utcnow
from vise.runtime.verify import (
    Verification,
    debugger_brief,
    parse_classification,
    reviewer_brief,
    parse_verification,
    verification_artifact,
    verifier_brief,
)
from vise.runtime.worker import Worker, execute

#: How long a collection wait blocks before the loop re-checks the run's
#: ceilings. Not a poll interval for work — completions wake the wait
#: immediately — but the bound on how long a wall-clock ceiling can be overshot
#: while every worker happens to be busy.
WAIT_SLICE_S = 0.5


def new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


@dataclass
class SchedulerConfig:
    """Policy the loop reads. Every field is a decision someone should be able
    to argue with, which is why none of them are literals in the loop body."""

    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    max_replans: int = DEFAULT_MAX_REPLANS
    #: Called with (state, tasks) when a failure says the plan was wrong.
    #: Returns a new task list, or None to stop for a person.
    #:
    #: Defaults to `replan.default_replanner`, which puts a `design` task in
    #: front of every task whose failure said the *plan* was wrong and lets the
    #: same ladder judge the result. It was `None` for a long time, which meant
    #: `recovery` could return REPLAN, the swap logic below was written and
    #: tested, and in production every spec or architecture failure still ended
    #: at `stop_for_human` because nothing was on the other end of the hook.
    #:
    #: What a replanner may do is bounded on purpose: it composes *tasks* from
    #: roles the registry staffs. It never authors a validator or an acceptance
    #: criterion, because a planner that writes the condition it is judged by is
    #: grading its own homework. Pass `replanner=None` explicitly to go back to
    #: stopping for a person.
    replanner: Callable[[RunState, Sequence[Any]], Sequence[Any] | None] | None = (
        default_replanner
    )
    #: Consulted before each dispatch. True stops the run. The hook a caller
    #: uses to implement Ctrl-C, a timeout, or a UI cancel button.
    should_cancel: Callable[[], bool] | None = None
    #: When a failure carries no classification, ask a separate agent where it
    #: lives instead of guessing. The classification decides retry vs escalate
    #: vs replan, so a wrong one costs a whole strategy rather than one attempt
    #: — and letting the failing worker classify its own failure is the same
    #: mistake as letting it grade its own pass.
    diagnose: bool = True
    #: Run one adversarial review over the whole node once every task has
    #: succeeded. Off by default: it is a second opinion on work that already
    #: has one, and it costs the top rung once per run.
    review: bool = False
    #: Under isolation, throw away a failed attempt's worktree so the next one
    #: starts from HEAD rather than from its own failed output. Only available
    #: with `isolate`: in a shared tree the same operation would revert files
    #: the runtime cannot prove belong to this task alone.
    rollback: bool = True
    #: Give each writing task its own git worktree, and integrate its changes
    #: back only after it has passed and been verified. Off by default because
    #: it needs a git repository with a commit and costs a checkout per task;
    #: on, it removes the attribution problem the ownership gate otherwise has
    #: to bound — see runtime/isolation.py.
    isolate: bool = False
    #: Whether a passing task is checked by a second agent before it counts as
    #: SUCCEEDED. On by default: a worker grading its own homework is the
    #: failure the whole design exists to prevent. It only engages for tasks
    #: that declare acceptance criteria — there is nothing to verify against
    #: otherwise, and the brief already says so.
    verify: bool = True
    #: Asked once, before the first dispatch, when the run contains a task that
    #: writes: may this project have code written into it at all? vise's node
    #: gate makes the spec phase impossible to talk past, and the execution
    #: plane is the one path to a worker that never traverses a node — so
    #: without this the gate has a side door, and it is the door vise built.
    #: Left as None to mean the real filesystem check, so the binding stays
    #: late — the same shape as `replanner` and `should_cancel`, and the reason
    #: a test can substitute one without reaching into a frozen default.
    spec_gate: Callable[..., SpecGateVerdict] | None = None
    #: Pin the change this run implements. Empty accepts any well-formed active
    #: change, which is the common single-change-in-flight case.
    spec_change: str = ""


class Scheduler:
    """Runs one DAG node's tasks to a terminal state."""

    def __init__(
        self,
        *,
        worker: Worker,
        registry: AgentRegistry | None = None,
        router: ModelRouter | None = None,
        artifacts: ArtifactStore | None = None,
        context: ContextResolver | None = None,
        state_root: Path | str | None = None,
        config: SchedulerConfig | None = None,
    ) -> None:
        self.worker = worker
        # Held so `run` can tell "the caller chose this registry" from "nobody
        # did" — only the second is rebuilt from the run's project dir.
        self._registry_given = registry is not None
        self.registry = registry if registry is not None else AgentRegistry.bundled()
        self.router = router or ModelRouter()
        self.artifacts = artifacts
        self.context = context
        self._pool = None
        self.state_root = Path(state_root) if state_root else None
        self.config = config or SchedulerConfig()

    # --- the loop --------------------------------------------------------

    def resume(self, state: RunState, tasks: Sequence[Any]) -> RunState:
        """Continue a stopped run, keeping what it already paid for.

        A run stops for a person at nine construction points and cannot be
        picked back up — ``RunState.MAX_EVENTS`` even says "the state file is
        read on every resume", which was true of the design and never of the
        code. This is that resume.

        What carries over is the part that would be dishonest to reset.
        ``ledger.spent`` above all: a resumed run that forgot its spend would
        turn ``--max-cost`` into a per-attempt limit, and a loop could spend
        without bound by resuming. ``replans`` likewise — a resume is not a
        fresh replan budget.

        Stale *reservations* are the opposite case. A reservation holds an
        estimate for a task that started and never reported; after a stop it is
        holding budget for work about to be attempted again, so it is released.

        Succeeded work survives, for the same reason it survives a replan:
        paying twice for the same answer is the thing this runtime exists to
        avoid.
        """
        for task in tasks:
            state.record(task.id)
        resumable = [
            record for record in state.tasks.values()
            if record.state is not TaskState.SUCCEEDED
        ]
        for record in resumable:
            record.state = TaskState.PENDING
            record.note = ""
            state.ledger.release(record.task_id)

        state.human_gate = ""
        state.cancelled = False
        state.cancel_reason = ""
        state.finished_at = ""
        state.emit(
            "resumed",
            retrying=len(resumable),
            kept=len(state.tasks) - len(resumable),
            spent_usd=round(state.ledger.spent.cost_usd, 4),
        )
        return self.run(state.spec, tasks, resume_from=state)

    def continue_from(
        self, prior: RunState, spec: RunSpec, tasks: Sequence[Any]
    ) -> RunState:
        """Run a new plan as the continuation of a recorded one.

        ``resume`` keeps the plan and retries what did not finish. This keeps
        the *spend* and takes a different plan — the one a person composed from
        the stopped run. They share one argument and nothing else, which is why
        they are two methods: resetting records to ``PENDING`` is right for a
        graph that has not changed and meaningless for one that has.

        What carries is the ledger, for the reason ``resume`` gives about a
        single run and which is no less true of a chain: a ceiling that resets
        between links is not a ceiling, and a goal pursued across four runs
        would be under budget in each and unbounded overall.

        What does not carry is the succeeded *records*. Identity is exactly
        what a new plan may have changed — a composed ``cli-python`` with new
        ownership and new acceptance is not the ``cli-python`` that failed —
        and nothing here can tell. So a task the composer put in the plan runs.
        The prior run's successes reach the new plan the other way, as
        ``completed`` ids that satisfy dependencies, which is the caller's job
        and not this one's.
        """
        state = RunState.for_tasks(spec, [t.id for t in tasks])
        state.ledger.spent = prior.ledger.spent
        state.ledger.by_task = dict(prior.ledger.by_task)
        state.emit(
            "continued",
            parent=prior.spec.run_id,
            inherited_usd=round(prior.ledger.spent.cost_usd, 4),
            tasks=len(tasks),
        )
        return self.run(spec, tasks, resume_from=state)

    def run(
        self,
        spec: RunSpec,
        tasks: Sequence[Any],
        *,
        resume_from: RunState | None = None,
    ) -> RunState:
        """Dispatch until every task is terminal, parked, or the run stops.

        ``resume_from`` supplies the state instead of one being built here —
        ``resume`` passes a recorded run, ``continue_from`` passes a fresh run
        carrying a prior one's ledger. Nothing after this line distinguishes
        the three callers on purpose: the spec gate
        is re-checked (the repository may have moved since), the worktree pool
        is re-opened, and the dispatch loop below is the part of this runtime
        that is hardest to test, so it gets one entry point rather than two.
        """
        by_id = {t.id: t for t in tasks}
        state = resume_from if resume_from is not None else RunState.for_tasks(spec, by_id)
        if not self._registry_given:
            # `.vise/agents/` belongs to the tree the run is against, not to
            # wherever the scheduler was constructed.
            self.registry = AgentRegistry.for_project(spec.project_dir)
            for path, reason in self.registry.refused:
                state.emit("agent_refused", path=path, reason=reason)
            if self.registry.shadowed:
                state.emit("agents_shadowed", ids=list(self.registry.shadowed))

        state.emit("run_started", goal=spec.goal, tasks=len(by_id))

        # Before the pool, before the worktrees, before any money: a run that
        # cannot start should not have created anything to clean up.
        gate = self._spec_gate(state, by_id)
        if not gate.ok:
            for task_id in by_id:
                self._apply(state, task_id, TaskState.BLOCKED,
                            f"spec gate: {gate.reason}")
            self._finalise(state)
            self._persist(state)
            return state

        self._pool = self._open_pool(state)
        max_parallel = max(1, spec.budget.max_parallel or 1)
        started = time.monotonic()

        with ThreadPoolExecutor(max_workers=max_parallel) as pool:
            pending: dict[Future[tuple[TaskResult, GateOutcome]], tuple[str, str]] = {}
            # The brief each in-flight task was dispatched with, and — while a
            # verifier is running — the work result it is judging. Held here
            # rather than on the record because both are inputs to a decision
            # that has not been made yet, and a record is what a decision left
            # behind.
            briefs: dict[str, TaskBrief] = {}
            verifying: dict[str, TaskResult] = {}
            # The tree as it stood before this task's *first* attempt. Reused by
            # every retry: a second attempt that legitimately reproduces the
            # first one's file changes nothing since the first, and comparing
            # against the attempt's own start would refuse it for doing what it
            # was asked to do again.
            baselines: dict[str, str | None] = {}
            # Where each task ran. The verifier has to read the same tree: under
            # isolation the work is not in the main tree until it has been
            # verified, so a verifier pointed at the main tree would be judging
            # a diff that is not there yet.
            trees: dict[str, str] = {}
            # Write the file before the first dispatch, not after the first
            # collection. Until this exists `vise runtime status` answers "no
            # such run" — for the whole of the first task, which is exactly when
            # someone watching a run they just started looks — and a process
            # killed during that task leaves nothing behind at all.
            self._persist(state)
            while not state.is_done():
                if self._cancelled(state):
                    break
                if self._over_wall_clock(state, started):
                    break

                dispatched = self._dispatch_ready(
                    state, by_id, pool, pending, briefs, baselines, trees, started
                )
                if dispatched:
                    # A dispatch is a transition like any other: it is what makes
                    # the difference between "of unknown outcome" and "never
                    # started" readable after a crash.
                    self._persist(state)

                if not pending:
                    if not dispatched:
                        # Nothing running and nothing startable. Either the
                        # remaining tasks depend on something that failed, or a
                        # cycle survived planning. Either way, waiting cannot help.
                        self._block_stalled(state, by_id)
                    if state.is_done() or not dispatched:
                        break
                    continue

                done, _ = wait(list(pending), timeout=WAIT_SLICE_S, return_when=FIRST_COMPLETED)
                for future in done:
                    task_id, kind = pending.pop(future)
                    if kind == "verify":
                        replan = self._collect_verification(
                            state, by_id, task_id, future, verifying, baselines
                        )
                    else:
                        replan = self._collect(
                            state, by_id, task_id, future, pool, pending, briefs,
                            verifying, trees, baselines,
                        )
                    if replan:
                        by_id = self._replan(state, by_id, tasks, task_id)

        # The executor has joined every thread by here, so anything still in
        # `pending` has in fact finished — the loop just broke before collecting
        # it. Its cost is real whether or not anyone read the result.
        self._drain(state, pending)

        if state.succeeded():
            self._review(state)
        self._finalise(state)
        self._persist(state)
        if self._pool is not None:
            self._pool.cleanup()
            self._pool = None
        return state

    # --- isolation -------------------------------------------------------

    def _open_pool(self, state: RunState):
        """A worktree pool, or None with the reason on the record.

        Degrades rather than raises: a repository that cannot host worktrees
        should run in the shared tree and say so, not refuse to run at all.
        """
        if not self.config.isolate:
            return None
        from vise.runtime.isolation import IsolationUnavailable, WorktreePool

        root = self.state_root or Path(state.spec.project_dir) / ".vise" / "runtime"
        try:
            pool = WorktreePool.create(
                state.spec.project_dir, root / "runs" / state.spec.run_id, state.spec.run_id
            )
        except IsolationUnavailable as exc:
            state.emit("isolation_unavailable", reason=str(exc))
            return None
        state.emit("isolation_enabled", root=str(pool.root))
        return pool

    def _tree_for(self, state: RunState, task: Any, brief: TaskBrief) -> str:
        """Where this task's worker runs: its own worktree, or the shared tree."""
        if self._pool is None or not brief.writes:
            return state.spec.project_dir
        from vise.runtime.isolation import IsolationUnavailable

        try:
            return str(self._pool.acquire(task.id))
        except IsolationUnavailable as exc:
            state.emit("isolation_unavailable", task=task.id, reason=str(exc))
            return state.spec.project_dir

    def _integrate(self, state: RunState, task_id: str) -> bool:
        """Bring one verified task's worktree back. True when it landed."""
        if self._pool is None or task_id not in self._pool.worktrees:
            return True
        result = self._pool.integrate(task_id)
        state.emit(
            "integrated", task=task_id, applied=result.applied,
            paths=list(result.changed_paths), conflicts=list(result.conflicts),
            reason=result.reason,
        )
        if result.applied:
            self._pool.release(task_id)
            return True
        self._apply(
            state, task_id, TaskState.BLOCKED,
            f"its changes do not integrate: {result.reason}",
        )
        return False

    # --- dispatch --------------------------------------------------------

    def _dispatch_ready(
        self,
        state: RunState,
        by_id: dict[str, Any],
        pool: ThreadPoolExecutor,
        pending: dict[Future[tuple[TaskResult, GateOutcome]], tuple[str, str]],
        briefs: dict[str, TaskBrief],
        baselines: dict[str, str | None],
        trees: dict[str, str],
        started: float,
    ) -> bool:
        """Start every task that is ready and admissible. True if any started."""
        started_any = False
        completed = state.completed_ids()
        for task_id in self._ready(state, by_id, completed):
            if len(pending) >= max(1, state.spec.budget.max_parallel or 1):
                break
            task = by_id[task_id]
            if getattr(task, "requires_human", False):
                # Checked before anything else, including budget: the point of
                # this flag is that the work should not start, and finding out
                # only because the money ran out would be an accident.
                reason = (
                    f"task '{task_id}' is declared requires_human — it will not "
                    f"start without a person"
                )
                state.emit("human_gate", task=task_id, reason=reason)
                state.stop_for_human(reason)
                return started_any
            claim_conflict = self._ownership_conflict(state, by_id, task)
            if claim_conflict:
                state.emit("deferred", task=task_id, reason=f"ownership held by {claim_conflict}")
                continue

            built = self._brief(state, task)
            if built is None:
                continue
            brief, agent_id, estimate = built
            # Re-brief once the tree is known: workdir is part of the brief, and
            # `_tree_for` needs the brief to know whether the task writes.
            tree = self._tree_for(state, task, brief)
            if tree != brief.workdir:
                built = self._brief(state, task, workdir=tree)
                if built is None:
                    continue
                brief, agent_id, estimate = built

            admission = state.ledger.admit(
                estimate,
                in_flight=len(pending),
                elapsed_s=time.monotonic() - started,
            )
            if not admission:
                state.emit("not_admitted", task=task_id, reason=admission.reason,
                           stop=admission.stop)
                if admission.stop:
                    state.stop_for_human(admission.reason)
                    return started_any
                break

            attempt_number = state.record(task_id).attempt_count + 1
            state.ledger.reserve(task_id, estimate)
            state.start(task_id, agent_id=agent_id, model=brief.model, effort=brief.effort)
            state.emit("dispatched", task=task_id, model=brief.model, effort=brief.effort,
                       agent=agent_id, attempt=attempt_number)
            trees[task_id] = tree
            if task_id not in baselines:
                baselines[task_id] = tree_hash(tree) if brief.writes else None
            # Persisted *before* the future is submitted, not after the batch.
            # The worker starts on another thread the moment submit returns, so
            # a `vise runtime status` racing it — or the worker reading the file
            # itself — could see the task still PENDING inside a run that was
            # already spending money on it. Writing here costs one file write
            # per dispatch and removes the window rather than narrowing it.
            self._persist(state)
            future = pool.submit(
                execute, brief, self.worker,
                project_dir=tree,
                # Under isolation a task's tree holds only its own writes, so
                # nothing needs excusing and the gate goes back to being strict.
                foreign_ownership=(
                    () if self._pool is not None
                    else self._foreign_ownership(by_id, task_id)
                ),
                baseline_tree=baselines[task_id],
            )
            pending[future] = (task_id, "work")
            briefs[task_id] = brief
            started_any = True
        return started_any

    def _ready(
        self, state: RunState, by_id: dict[str, Any], completed: set[str]
    ) -> list[str]:
        """Dependency-satisfied, not terminal, not running. Order is the node's."""
        out = []
        for task_id, task in by_id.items():
            record = state.record(task_id)
            if record.state not in (TaskState.PENDING, TaskState.READY):
                continue
            if all(dep in completed for dep in getattr(task, "dependencies", ())):
                out.append(task_id)
        return out

    def _foreign_ownership(self, by_id: dict[str, Any], task_id: str) -> tuple[str, ...]:
        """What a peer could legitimately write while this task runs.

        Every other writing task whose claim does not conflict with this one's —
        not the ones marked RUNNING right now. An in-flight snapshot is taken at
        dispatch, and two peers dispatched in the same pass each miss the other:
        the first is started before the second exists to be seen. That is
        exactly the case an end-to-end run hit, and it refused both writers for
        each other's work.

        Conflicting peers are deliberately excluded. Admission serialises them,
        so their files appearing mid-run is not a peer being busy — it is
        something genuinely wrong, and the gate should still say so.
        """
        task = by_id.get(task_id)
        mine = getattr(task, "ownership", ()) or () if task is not None else ()
        claims: list[str] = []
        for other_id, other in by_id.items():
            if other_id == task_id or not getattr(other, "writes", True):
                continue
            theirs = getattr(other, "ownership", ()) or ()
            if theirs and not _own.conflicts(mine, theirs):
                claims.extend(theirs)
        return tuple(claims)

    def _ownership_conflict(
        self, state: RunState, by_id: dict[str, Any], task: Any
    ) -> str | None:
        """The id of an in-flight task whose claims intersect this one's."""
        if not getattr(task, "writes", True):
            return None
        for other_id in state.in_flight():
            other = by_id.get(other_id)
            if other is None or not getattr(other, "writes", True):
                continue
            if _own.conflicts(
                getattr(task, "ownership", ()), getattr(other, "ownership", ())
            ):
                return other_id
        return None

    # --- briefing --------------------------------------------------------

    def _route(self, state: RunState, task: Any):
        role = getattr(task, "role", None) or ""
        resolution = self.registry.resolve(
            role,
            writes=True if getattr(task, "writes", True) else None,
            capability=capability_hint(task),
        ) if role else None
        agent = resolution.agent if resolution else None
        return agent, self.router.route(
            task,
            agent=agent,
            attempts=state.record(task.id).attempts,
            budget_remaining_usd=state.ledger.remaining_usd(),
        )

    def _brief(
        self, state: RunState, task: Any, workdir: str | None = None
    ) -> tuple[TaskBrief, str, float] | None:
        """Assemble the brief, or park the task when nobody can run it.

        Returns the brief, the agent id it was built for, and the routed cost
        estimate. The agent id travels beside the brief rather than inside it:
        the worker does not need to know which charter selected it, and a field
        it does not need is a field it can contradict.
        """
        role = getattr(task, "role", None) or ""
        if not role:
            state.set_state(task.id, TaskState.BLOCKED,
                            "task declares no role — nothing can be routed to it")
            state.emit("unroutable", task=task.id, reason="no role")
            return None
        agent, decision = self._route(state, task)
        if agent is None:
            state.set_state(task.id, TaskState.BLOCKED,
                            f"no agent resolves role '{role}'")
            state.emit("unroutable", task=task.id, reason=f"role {role}")
            return None
        if not decision.affordable:
            state.emit("not_admitted", task=task.id,
                       reason=decision.reasons[-1] if decision.reasons else "budget", stop=True)
            state.stop_for_human(f"task '{task.id}' does not fit the remaining budget")
            return None

        record = state.record(task.id)
        inputs: tuple[Artifact, ...] = ()
        if self.artifacts is not None:
            inputs = self.artifacts.inputs_for(getattr(task, "dependencies", ()) or ())
        return TaskBrief(
            run_id=state.spec.run_id,
            task_id=task.id,
            name=getattr(task, "name", task.id),
            role=role,
            prompt=getattr(task, "prompt", "") or "",
            criticality=_criticality(task),
            ownership=tuple(getattr(task, "ownership", ()) or ()),
            acceptance=tuple(getattr(task, "acceptance", ()) or ()),
            inputs=inputs,
            attempts=tuple(record.attempts),
            tools_blocked=tuple(getattr(task, "tools_blocked", ()) or ()),
            mcps_enabled=tuple(getattr(task, "mcps_enabled", ("*",)) or ("*",)),
            model=decision.model,
            effort=decision.effort,
            budget=TaskBudget(
                max_cost_usd=float(getattr(task, "max_cost", 0.0) or 0.0),
                max_turns=int(getattr(task, "max_turns", 0) or 0),
                timeout_s=int(getattr(task, "timeout_s", 0) or 0),
            ),
            writes=bool(getattr(task, "writes", True)),
            context=self._context_for(state, task),
            workdir=workdir or state.spec.project_dir,
        ), agent.id, decision.estimated_cost_usd

    def _context_for(self, state: RunState, task: Any) -> tuple[str, ...]:
        """Resolved context, or nothing when no resolver was supplied.

        Nothing is the honest default. A scheduler that silently built its own
        resolver would walk the caller's repository without being asked, and a
        brief is the one place where "helpfully included extra" is a cost.
        """
        if self.context is None:
            return ()
        try:
            return self.context.resolve(task)
        except Exception as exc:  # noqa: BLE001 - context is an aid, never a gate
            state.emit("context_failed", task=task.id, error=f"{type(exc).__name__}: {exc}")
            return ()

    # --- collection ------------------------------------------------------

    def _collect(
        self,
        state: RunState,
        by_id: dict[str, Any],
        task_id: str,
        future: Future[tuple[TaskResult, GateOutcome]],
        pool: ThreadPoolExecutor,
        pending: dict[Future[tuple[TaskResult, GateOutcome]], tuple[str, str]],
        briefs: dict[str, TaskBrief],
        verifying: dict[str, TaskResult],
        trees: dict[str, str],
        baselines: dict[str, str | None],
    ) -> bool:
        """Fold one finished attempt in and act on it. True when a replan is due."""
        try:
            result, outcome = future.result()
        except Exception as exc:  # noqa: BLE001 - a worker crash is a task failure
            # A worker that raises has failed the task, not the run. Recording it
            # as an environment failure is the honest reading: the work was never
            # evaluated, so it retries at the same rung rather than paying more
            # to re-crash.
            result = TaskResult(
                task_id=task_id,
                verdict=Verdict.INCONCLUSIVE,
                summary=f"worker raised {type(exc).__name__}: {exc}",
                model=state.record(task_id).model,
                effort=state.record(task_id).effort,
            )
            outcome = GateOutcome(True, (), result)

        result = self._classify(state, result, briefs.get(task_id), trees.get(task_id))
        record = state.finish(task_id, result)
        if self.artifacts is not None and result.artifacts:
            self.artifacts.put_all(result.artifacts)

        # The rung that just failed, read off the record — not the rung the
        # router would pick next. Routing after the failure is recorded returns
        # the *escalated* tier, so asking it "are we at the top" answers one
        # attempt early and replans a task that still had opus to try.
        if outcome.accepted and result.verdict is Verdict.PASS:
            work_brief = briefs.get(task_id)
            if work_brief is not None and self._verification_applies(work_brief):
                verifying[task_id] = result
                verify_model, verify_effort = self._verify_model()
                brief = replace(
                    verifier_brief(work_brief, result,
                                   model=verify_model, effort=verify_effort),
                    workdir=trees.get(task_id, state.spec.project_dir),
                )
                state.emit("verifying", task=task_id, model=brief.model, effort=brief.effort)
                pending[pool.submit(
                    execute, brief, self.worker,
                    project_dir=trees.get(task_id, state.spec.project_dir),
                )] = (task_id, "verify")
                return False

        used_tier = tier_of(record.model, record.effort)
        move = decide(
            result,
            record.attempts,
            gates_accepted=outcome.accepted,
            at_top_rung=used_tier is not None and used_tier >= TOP,
            replans_used=state.replans,
            max_attempts=self.config.max_attempts,
            max_replans=self.config.max_replans,
        )
        # A task that passed without a verifier still has to land. Under
        # isolation its work is in its own worktree until it does, so this is
        # the point where "the task succeeded" becomes true of the repository
        # and not just of the worker.
        if move.state is TaskState.SUCCEEDED and not self._integrate(state, task_id):
            state.emit("collected", task=task_id, verdict=result.verdict.value,
                       gates_accepted=outcome.accepted, refusals=list(outcome.refusals),
                       action="blocked", reason="integration conflict",
                       cost_usd=round(result.usage.cost_usd, 4))
            self._persist(state)
            return False
        self._apply(state, task_id, move.state, move.reason)
        state.emit(
            "collected",
            task=task_id,
            verdict=result.verdict.value,
            gates_accepted=outcome.accepted,
            refusals=list(outcome.refusals),
            action=move.action.value,
            reason=move.reason,
            cost_usd=round(result.usage.cost_usd, 4),
        )
        if move.action in (Recovery.RETRY, Recovery.ESCALATE):
            self._rollback(state, task_id, baselines)
        self._persist(state)

        if move.action is Recovery.HUMAN:
            state.stop_for_human(f"{task_id}: {move.reason}")
        return move.action is Recovery.REPLAN

    # --- verification ----------------------------------------------------

    def _verification_applies(self, brief: TaskBrief) -> bool:
        """Whether this task's pass gets a second opinion.

        Only when it declares acceptance criteria. A verifier with nothing to
        check against would be asked "is this good", which is the open-ended
        question `reviewer` answers once per node — asking it once per task
        buys an opinion nobody costed.
        """
        if not self.config.verify or not brief.acceptance:
            return False
        return self.registry.resolve("verify", writes=None).agent is not None

    def _verify_model(self) -> tuple[str, str]:
        agent = self.registry.resolve("verify", writes=None).agent
        return (agent.model or "sonnet", agent.effort or "medium") if agent else \
            ("sonnet", "medium")

    def _collect_verification(
        self,
        state: RunState,
        by_id: dict[str, Any],
        task_id: str,
        future: Future[tuple[TaskResult, GateOutcome]],
        verifying: dict[str, TaskResult],
        baselines: dict[str, str | None],
    ) -> bool:
        """Act on a verifier's answer about one task.

        Three outcomes, three different meanings:

        pass          the task succeeded — the only path to SUCCEEDED.
        fail          the work was wrong; the verifier's reasons replace the
                      worker's summary and the task escalates like any wrong
                      answer.
        inconclusive  the *verifier* could not evaluate. The task is blocked,
                      not retried: re-running the implementer cannot fix a
                      verifier that would not run, and paying for it to find
                      that out again is the wrong lesson to learn twice.
        """
        work_result = verifying.pop(task_id, None)
        record = state.record(task_id)
        try:
            collected = future.result()
        except Exception as exc:  # noqa: BLE001 - a verifier crash is not a task failure
            verification = Verification(
                Verdict.INCONCLUSIVE,
                (f"the verifier raised {type(exc).__name__}: {exc}",),
            )
            verify_result = None
        else:
            # Unpacked here rather than in the `try`, so the name is bound once
            # on a path where it cannot be None. The two-branch version read the
            # same and left every later use nullable.
            verify_result, _ = collected
            state.ledger.spend(f"{task_id}::verify", verify_result.usage)
            verification = parse_verification(verify_result)

        if self.artifacts is not None:
            self.artifacts.put(
                verification_artifact(state.spec.run_id, task_id, verification)
            )
        state.emit(
            "verified",
            task=task_id,
            verdict=verification.verdict.value,
            unmet=list(verification.unmet),
            reasons=list(verification.reasons)[:3],
            cost_usd=round(verify_result.usage.cost_usd, 4) if verify_result else 0.0,
        )

        if verification.verdict is Verdict.PASS:
            if self._integrate(state, task_id):
                self._apply(state, task_id, TaskState.SUCCEEDED,
                            "verified against its criteria")
            self._persist(state)
            return False

        if verification.verdict is Verdict.INCONCLUSIVE:
            self._apply(
                state, task_id, TaskState.BLOCKED,
                "the verifier could not evaluate this — "
                + (verification.reasons[0] if verification.reasons else "no reason given"),
            )
            self._persist(state)
            return False

        # The verifier says the work is wrong. Its reasons, not the worker's
        # summary, are what the next attempt is told.
        reasons = "; ".join(verification.unmet or verification.reasons) or "criteria not met"
        rejected = replace(
            work_result or TaskResult(task_id=task_id, verdict=Verdict.FAIL),
            verdict=Verdict.FAIL,
            summary=f"verifier rejected it: {reasons}",
            classification=FailureKind.CODE_BUG,
        )
        if record.attempts:
            record.attempts[-1] = rejected.to_attempt(len(record.attempts))
        used_tier = tier_of(record.model, record.effort)
        move = decide(
            rejected,
            record.attempts,
            gates_accepted=True,
            at_top_rung=used_tier is not None and used_tier >= TOP,
            replans_used=state.replans,
            max_attempts=self.config.max_attempts,
            max_replans=self.config.max_replans,
        )
        self._apply(state, task_id, move.state, move.reason)
        state.emit("collected", task=task_id, verdict="fail", gates_accepted=True,
                   refusals=[], action=move.action.value, reason=move.reason,
                   cost_usd=0.0)
        self._persist(state)
        if move.action in (Recovery.RETRY, Recovery.ESCALATE):
            self._rollback(state, task_id, baselines)
        if move.action is Recovery.HUMAN:
            state.stop_for_human(f"{task_id}: {move.reason}")
        return move.action is Recovery.REPLAN

    def _rollback(
        self, state: RunState, task_id: str, baselines: dict[str, str | None]
    ) -> None:
        """Discard a failed attempt's worktree so the next one starts from HEAD.

        Isolation only. In a shared tree the same operation would revert files
        the runtime cannot prove belong to this task alone — which is the whole
        reason isolation exists — so there it does nothing rather than
        something dangerous.
        """
        if not (self.config.rollback and self._pool is not None):
            return
        if task_id not in self._pool.worktrees:
            return
        self._pool.release(task_id)
        baselines.pop(task_id, None)
        state.emit("rolled_back", task=task_id,
                   reason="the failed attempt's worktree was discarded")

    def _classify(
        self,
        state: RunState,
        result: TaskResult,
        brief: TaskBrief | None = None,
        workdir: str | None = None,
    ) -> TaskResult:
        """Decide where a failure lives, cheapest source first.

        The worker's own classification wins when it gave one — it was there.
        Otherwise the text heuristic, which only recognises a machine that was
        not present. Only when neither answers does a debugger run, because it
        costs a model call and most failures name themselves.
        """
        if result.verdict is Verdict.PASS or result.classification is not None:
            return result
        guessed = classify_from_text(f"{result.summary}\n{result.evidence}\n{result.checks}")
        if guessed is not None:
            return replace(result, classification=guessed)
        diagnosed = self._diagnose(state, result, brief, workdir)
        return replace(result, classification=diagnosed) if diagnosed else result

    def _diagnose(
        self,
        state: RunState,
        result: TaskResult,
        brief: TaskBrief | None,
        workdir: str | None,
    ) -> FailureKind | None:
        """Ask a debugger where an undiagnosed failure lives.

        Synchronous, and deliberately: the answer decides what happens to this
        task next, so there is nothing to overlap it with. Any failure of the
        debugger itself leaves the classification unset, which escalates — the
        safe direction, since a missing diagnosis is not evidence the work was
        fine.
        """
        if not self.config.diagnose or brief is None:
            return None
        resolution = self.registry.resolve("debug", writes=None)
        if resolution.agent is None:
            return None
        agent = resolution.agent
        probe = replace(
            debugger_brief(brief, result,
                           model=agent.model or "sonnet", effort=agent.effort or "high"),
            workdir=workdir or state.spec.project_dir,
        )
        state.emit("diagnosing", task=brief.task_id, agent=agent.id)
        try:
            answer = self.worker.run(probe)
        except Exception as exc:  # noqa: BLE001 - a debugger crash is not a task failure
            state.emit("diagnose_failed", task=brief.task_id,
                       error=f"{type(exc).__name__}: {exc}")
            return None
        state.ledger.spend(f"{brief.task_id}::debug", answer.usage)
        kind = parse_classification(answer)
        state.emit("diagnosed", task=brief.task_id,
                   classification=kind.value if kind else None,
                   cost_usd=round(answer.usage.cost_usd, 4))
        return kind

    # --- replanning ------------------------------------------------------

    def _replan(
        self,
        state: RunState,
        by_id: dict[str, Any],
        original: Sequence[Any],
        trigger: str = "",
    ) -> dict[str, Any]:
        """Recompose around a failure the plan caused.

        ``trigger`` is the task whose failure asked for this. Both refusals
        below record it and the reason: an event that says only that a replan
        did not happen is a report a composer cannot act on, and these two
        events are the strongest plan-level signal the runtime emits — they
        are what `vise runtime compose` reads to say the plan was wrong.
        """
        if self.config.replanner is None:
            reason = "a failure says the plan is wrong and no replanner is configured"
            state.stop_for_human(reason)
            state.emit("replan_unavailable", task=trigger or None, reason=reason)
            return by_id
        replacement = self.config.replanner(state, original)
        if not replacement:
            reason = "the replanner declined to produce a new plan"
            state.stop_for_human(reason)
            state.emit("replan_declined", task=trigger or None, reason=reason)
            return by_id
        state.replans += 1
        state.emit("replanned", tasks=len(replacement), replans=state.replans)
        new_by_id = {t.id: t for t in replacement}
        # Succeeded work survives a replan. Redoing a task that passed and was
        # verified is paying twice for the same answer.
        for task_id in new_by_id:
            if task_id not in state.tasks:
                state.record(task_id)
        for task_id, record in list(state.tasks.items()):
            if task_id not in new_by_id and record.state is not TaskState.SUCCEEDED:
                record.state = TaskState.CANCELLED
                record.note = "dropped by a replan"
            elif record.state in (TaskState.BLOCKED, TaskState.FAILED):
                record.state = TaskState.PENDING
        return new_by_id

    # --- the adversarial pass --------------------------------------------

    def _review(self, state: RunState) -> None:
        """One adversarial review over the whole node, after everything passed.

        Not per task: the questions it asks — what two of these changes do to
        each other, what an existing caller sees now — are about the node, and
        asking them once per task is both more expensive and worse at answering
        them. A blocking verdict parks the run; nothing is reverted, because
        deciding what to do about a shipping objection is a person's call.
        """
        if not self.config.review:
            return
        resolution = self.registry.resolve("review", writes=None)
        if resolution.agent is None:
            state.emit("review_unavailable", reason="no agent takes role 'review'")
            return
        agent = resolution.agent
        changed: list[str] = []
        for record in state.tasks.values():
            if record.result is not None:
                changed.extend(record.result.changed_paths)
        brief = replace(
            reviewer_brief(
                state.spec.run_id, goal=state.spec.goal,
                changed_paths=tuple(sorted(set(changed))),
                model=agent.model or "opus", effort=agent.effort or "high",
            ),
            workdir=state.spec.project_dir,
        )
        state.emit("reviewing", agent=agent.id, model=brief.model, effort=brief.effort)
        try:
            result = self.worker.run(brief)
        except Exception as exc:  # noqa: BLE001 - a reviewer crash is not a run failure
            state.emit("review_failed", error=f"{type(exc).__name__}: {exc}")
            return
        state.ledger.spend(f"{state.spec.run_id}::review", result.usage)
        if self.artifacts is not None and result.artifacts:
            self.artifacts.put_all(result.artifacts)
        state.emit("reviewed", verdict=result.verdict.value,
                   summary=result.summary[:400],
                   cost_usd=round(result.usage.cost_usd, 4))
        if result.verdict is Verdict.FAIL:
            state.stop_for_human(f"the adversarial review objects: {result.summary[:400]}")

    # --- stopping --------------------------------------------------------

    def _cancelled(self, state: RunState) -> bool:
        """Two ways to stop: an in-process hook, and a sentinel file.

        The file exists because the person cancelling is usually not in this
        process — they are at another terminal running `vise runtime cancel`.
        """
        if self.config.should_cancel is not None and self.config.should_cancel():
            state.cancel("cancelled by the caller")
            state.emit("cancelled", reason="caller")
            return True
        if self.state_root is not None and cancel_requested(self.state_root, state.spec.run_id):
            state.cancel("cancelled by `vise runtime cancel`")
            state.emit("cancelled", reason="sentinel")
            return True
        return False

    def _over_wall_clock(self, state: RunState, started: float) -> bool:
        ceiling = state.spec.budget.max_wall_time_s
        if not ceiling:
            return False
        elapsed = time.monotonic() - started
        if elapsed < ceiling:
            return False
        state.stop_for_human(f"{elapsed:.0f}s elapsed, ceiling is {ceiling:.0f}s")
        state.emit("wall_clock_exhausted", elapsed_s=round(elapsed, 1))
        return True

    def _block_stalled(self, state: RunState, by_id: dict[str, Any]) -> None:
        """Nothing running, nothing startable — say which dependency did it."""
        if state.human_gate or state.cancelled:
            # The run stopped for a reason that is already on the record. A
            # generic "nothing satisfies it" would overwrite that reason with a
            # description of its consequence.
            return
        completed = state.completed_ids()
        for record in state.unfinished():
            task = by_id.get(record.task_id)
            if task is None:
                continue
            if record.state is TaskState.BLOCKED and record.note:
                # Already blocked for a stated reason — "unroutable", say. A
                # generic stall message would overwrite the specific one, which
                # is the only useful half.
                continue
            missing = [d for d in getattr(task, "dependencies", ()) if d not in completed]
            reason = (
                f"blocked on {', '.join(missing)}" if missing
                else "no dependency satisfies it and nothing is running"
            )
            state.set_state(record.task_id, TaskState.BLOCKED, reason)
            state.emit("stalled", task=record.task_id, reason=reason)

    def _apply(
        self, state: RunState, task_id: str, new_state: TaskState, reason: str
    ) -> None:
        """Set a task's state, unless the run has already stopped.

        A task collected after the run stopped must not walk back out of the
        state the stop put it in. Recovery's answer is still "retry", but there
        is nothing left to retry into: writing PENDING there leaves a run that
        reports itself done with a task waiting to start, which is the one
        inconsistency every reader of the state file would trust.
        """
        if state.cancelled or state.human_gate:
            parked = TaskState.CANCELLED if state.cancelled else TaskState.WAITING_HUMAN
            if new_state not in TERMINAL_STATES:
                state.set_state(task_id, parked, f"{reason} (the run had already stopped)")
                return
        state.set_state(task_id, new_state, reason)

    def _drain(
        self,
        state: RunState,
        pending: dict[Future[tuple[TaskResult, GateOutcome]], tuple[str, str]],
    ) -> None:
        """Settle every future the loop stopped before collecting.

        A cancel, a wall-clock ceiling or a budget stop breaks the loop with
        work in flight. Leaving those futures alone did not save the money —
        the API was already billed — it only stopped the run from *saying* so,
        and a ledger reporting $0.00 for a run that spent $7.77 is the one
        number nobody can catch by reading the output.

        Deliberately not a second collection pass: nothing here retries,
        verifies, escalates or replans. The run is over. What is owed is the
        accounting and an honest note on the record; deciding is
        ``recovery.decide``'s job and there is nothing left to decide.
        """
        for future, (task_id, kind) in list(pending.items()):
            try:
                result, _ = future.result(timeout=0)
            except Exception as exc:  # noqa: BLE001 - a stopped run reports, never raises
                # No usage to settle, so the estimate has to come off the books
                # by hand or the run ends holding a reservation forever.
                state.ledger.release(task_id)
                record = state.record(task_id)
                record.note = record.note or (
                    f"the run stopped while this was in flight and it "
                    f"{type(exc).__name__}: {exc}"
                )
                # Emitted, not only noted: a task that already carries a note
                # from the stop itself would otherwise swallow this entirely,
                # and a worker that crashed on the way out is exactly what a
                # stopped run must not hide.
                state.emit(
                    "drain_failed", task=task_id, future_kind=kind,
                    error=f"{type(exc).__name__}: {exc}",
                )
                continue
            if kind == "verify":
                state.ledger.spend(f"{task_id}::verify", result.usage)
                state.record(task_id).note = state.record(task_id).note or (
                    "the run stopped while this task was being verified; the "
                    "verification cost is on the record, its verdict is not"
                )
                continue
            record = state.finish(task_id, result)
            record.note = record.note or (
                f"the run stopped before this was collected; the worker had "
                f"finished and reported {result.verdict.value}"
            )
            state.emit(
                "drained",
                task=task_id,
                verdict=result.verdict.value,
                cost_usd=round(result.usage.cost_usd, 4),
            )
        pending.clear()

    def _sweep(self, state: RunState) -> None:
        """Nothing may still be pending or running once the loop is over.

        The loop can exit with futures in flight — a cancel, a wall-clock
        ceiling, a budget stop. Their threads are joined by the pool, but their
        results are never collected, so without this the state file would claim
        tasks were running inside a run that had finished.
        """
        parked = TaskState.CANCELLED if state.cancelled else TaskState.WAITING_HUMAN
        for record in state.tasks.values():
            if record.state in (TaskState.PENDING, TaskState.READY, TaskState.RUNNING):
                was = record.state
                record.state = parked
                record.note = record.note or (
                    f"the run ended while this task was {was.value}"
                )

    def _finalise(self, state: RunState) -> None:
        self._sweep(state)
        if not state.finished_at:
            state.finished_at = utcnow()
        state.emit(
            "run_finished",
            succeeded=state.succeeded(),
            cost_usd=round(state.ledger.spent.cost_usd, 4),
        )

    def _spec_gate(self, state: RunState, by_id: dict[str, Any]) -> SpecGateVerdict:
        """Ask once whether this project may have work written into it.

        Asked here rather than per task on purpose. A per-task check would run
        the same repository-level query N times, and would let the first three
        tasks spend money before the fourth discovered the project has no
        specs — a run that ends half-applied, which is worse than one that
        never starts.
        """
        writes = any(bool(getattr(t, "writes", True)) for t in by_id.values())
        gate = self.config.spec_gate or spec_gate_check
        verdict = gate(
            state.spec.project_dir,
            change=self.config.spec_change,
            writes=writes,
        )
        if verdict.overridden:
            state.emit("spec_gate_overridden", reason=verdict.reason)
        elif not verdict.ok:
            state.emit("spec_gate_blocked", reason=verdict.reason)
        elif verdict.change:
            state.emit("spec_gate_passed", change=verdict.change)
        return verdict

    def _persist(self, state: RunState) -> None:
        if self.state_root is not None:
            state.save(self.state_root)


def _criticality(task: Any) -> Criticality:
    raw = str(getattr(task, "criticality", Criticality.ROUTINE.value))
    try:
        return Criticality(raw)
    except ValueError:
        return Criticality.ROUTINE


def run_tasks(
    tasks: Iterable[Any],
    *,
    worker: Worker,
    goal: str = "",
    project_dir: str = ".",
    spec: RunSpec | None = None,
    **kwargs: Any,
) -> RunState:
    """Convenience entry point: build a spec, run the tasks, return the state."""
    tasks = list(tasks)
    spec = spec or RunSpec(run_id=new_run_id(), goal=goal, project_dir=project_dir)
    return Scheduler(worker=worker, **kwargs).run(spec, tasks)
