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
from vise.runtime.recovery import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_REPLANS,
    Recovery,
    classify_from_text,
    decide,
)
from vise.runtime.registry import AgentRegistry
from vise.runtime.routing import TOP, ModelRouter, tier_of
from vise.runtime.state import RunState, cancel_requested, utcnow
from vise.runtime.verify import (
    Verification,
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
    #: Returns a new task list, or None to stop for a person. Absent by
    #: default: inventing a new plan is a model's job, and a scheduler that
    #: silently reshuffles the graph is worse than one that stops and says so.
    replanner: Callable[[RunState, Sequence[Any]], Sequence[Any] | None] | None = None
    #: Consulted before each dispatch. True stops the run. The hook a caller
    #: uses to implement Ctrl-C, a timeout, or a UI cancel button.
    should_cancel: Callable[[], bool] | None = None
    #: Whether a passing task is checked by a second agent before it counts as
    #: SUCCEEDED. On by default: a worker grading its own homework is the
    #: failure the whole design exists to prevent. It only engages for tasks
    #: that declare acceptance criteria — there is nothing to verify against
    #: otherwise, and the brief already says so.
    verify: bool = True


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
        self.registry = registry if registry is not None else AgentRegistry.bundled()
        self.router = router or ModelRouter()
        self.artifacts = artifacts
        self.context = context
        self.state_root = Path(state_root) if state_root else None
        self.config = config or SchedulerConfig()

    # --- the loop --------------------------------------------------------

    def run(self, spec: RunSpec, tasks: Sequence[Any]) -> RunState:
        """Dispatch until every task is terminal, parked, or the run stops."""
        by_id = {t.id: t for t in tasks}
        state = RunState.for_tasks(spec, by_id)
        state.emit("run_started", goal=spec.goal, tasks=len(by_id))
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
            while not state.is_done():
                if self._cancelled(state):
                    break
                if self._over_wall_clock(state, started):
                    break

                dispatched = self._dispatch_ready(
                    state, by_id, pool, pending, briefs, baselines, started
                )

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
                            state, by_id, task_id, future, verifying
                        )
                    else:
                        replan = self._collect(
                            state, by_id, task_id, future, pool, pending, briefs, verifying
                        )
                    if replan:
                        by_id = self._replan(state, by_id, tasks)

        self._finalise(state)
        self._persist(state)
        return state

    # --- dispatch --------------------------------------------------------

    def _dispatch_ready(
        self,
        state: RunState,
        by_id: dict[str, Any],
        pool: ThreadPoolExecutor,
        pending: dict[Future[tuple[TaskResult, GateOutcome]], tuple[str, str]],
        briefs: dict[str, TaskBrief],
        baselines: dict[str, str | None],
        started: float,
    ) -> bool:
        """Start every task that is ready and admissible. True if any started."""
        started_any = False
        completed = state.completed_ids()
        for task_id in self._ready(state, by_id, completed):
            if len(pending) >= max(1, state.spec.budget.max_parallel or 1):
                break
            task = by_id[task_id]
            claim_conflict = self._ownership_conflict(state, by_id, task)
            if claim_conflict:
                state.emit("deferred", task=task_id, reason=f"ownership held by {claim_conflict}")
                continue

            built = self._brief(state, task)
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
            if task_id not in baselines:
                baselines[task_id] = (
                    tree_hash(state.spec.project_dir) if brief.writes else None
                )
            future = pool.submit(
                execute, brief, self.worker,
                project_dir=state.spec.project_dir,
                foreign_ownership=self._foreign_ownership(by_id, task_id),
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
            capability=_capability_of(task),
        ) if role else None
        agent = resolution.agent if resolution else None
        return agent, self.router.route(
            task,
            agent=agent,
            attempts=state.record(task.id).attempts,
            budget_remaining_usd=state.ledger.remaining_usd(),
        )

    def _brief(self, state: RunState, task: Any) -> tuple[TaskBrief, str, float] | None:
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
        inputs = ()
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

        result = self._classify(result)
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
                brief = verifier_brief(
                    work_brief, result,
                    model=self._verify_model()[0], effort=self._verify_model()[1],
                )
                state.emit("verifying", task=task_id, model=brief.model, effort=brief.effort)
                pending[pool.submit(
                    execute, brief, self.worker, project_dir=state.spec.project_dir
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
            verify_result, _ = future.result()
        except Exception as exc:  # noqa: BLE001 - a verifier crash is not a task failure
            verification = Verification(
                Verdict.INCONCLUSIVE,
                (f"the verifier raised {type(exc).__name__}: {exc}",),
            )
            verify_result = None
        else:
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
            self._apply(state, task_id, TaskState.SUCCEEDED, "verified against its criteria")
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
        if move.action is Recovery.HUMAN:
            state.stop_for_human(f"{task_id}: {move.reason}")
        return move.action is Recovery.REPLAN

    def _classify(self, result: TaskResult) -> TaskResult:
        """Fill in a missing classification from the failure's own output."""
        if result.verdict is Verdict.PASS or result.classification is not None:
            return result
        guessed = classify_from_text(f"{result.summary}\n{result.evidence}\n{result.checks}")
        if guessed is None:
            return result
        return replace(result, classification=guessed)

    # --- replanning ------------------------------------------------------

    def _replan(
        self, state: RunState, by_id: dict[str, Any], original: Sequence[Any]
    ) -> dict[str, Any]:
        if self.config.replanner is None:
            state.stop_for_human(
                "a failure says the plan is wrong and no replanner is configured"
            )
            state.emit("replan_unavailable")
            return by_id
        replacement = self.config.replanner(state, original)
        if not replacement:
            state.stop_for_human("the replanner declined to produce a new plan")
            state.emit("replan_declined")
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

    def _persist(self, state: RunState) -> None:
        if self.state_root is not None:
            state.save(self.state_root)


def _criticality(task: Any) -> Criticality:
    raw = str(getattr(task, "criticality", Criticality.ROUTINE.value))
    try:
        return Criticality(raw)
    except ValueError:
        return Criticality.ROUTINE


_CAPABILITY_WORDS = frozenset({
    "python", "typescript", "go", "rust", "java", "kotlin", "swift", "ruby",
    "php", "csharp", "cpp", "lua",
})


def _capability_of(task: Any) -> str | None:
    haystack = f"{getattr(task, 'id', '')} {getattr(task, 'name', '')}".lower()
    for word in haystack.replace("/", " ").replace("-", " ").replace("_", " ").split():
        if word in _CAPABILITY_WORDS:
            return word
    return None


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
