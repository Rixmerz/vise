"""What a run knows about itself — see docs/scheduler.md § Task states.

The scheduler is a loop over this structure. Keeping it separate and inert is
what makes the loop testable: every interesting question about a run — is it
done, what is in flight, which task has burned three attempts, what did the
second attempt say — is a question about state, and none of them should need a
worker to answer.

State is persisted as one JSON file per run, written after every transition. A
run whose process dies mid-flight leaves a file that says exactly which tasks
had finished, which were in flight (and are therefore of unknown outcome), and
what everything cost up to that point. Losing that is losing the only record of
what a half-finished run actually did.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from vise.runtime.budget import BudgetLedger
from vise.runtime.contracts import (
    TERMINAL_STATES,
    Attempt,
    FailureKind,
    RunBudget,
    RunSpec,
    TaskResult,
    TaskState,
    Usage,
    Verdict,
)


def utcnow() -> str:
    """One clock for the whole runtime, so a state file and its events agree."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TaskRecord:
    """Everything the runtime knows about one task."""

    task_id: str
    state: TaskState = TaskState.PENDING
    attempts: list[Attempt] = field(default_factory=list)
    result: TaskResult | None = None
    agent_id: str | None = None
    model: str = ""
    effort: str = ""
    note: str = ""

    @property
    def attempt_count(self) -> int:
        return len(self.attempts)

    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "state": self.state.value,
            "attempts": [a.to_dict() for a in self.attempts],
            "result": self.result.to_dict() if self.result else None,
            "agent_id": self.agent_id,
            "model": self.model,
            "effort": self.effort,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaskRecord:
        return cls(
            task_id=str(data.get("task_id", "")),
            state=TaskState(data.get("state", TaskState.PENDING.value)),
            attempts=[_attempt_from_dict(a) for a in data.get("attempts") or []],
            result=None,  # results are rebuilt from artifacts, not from state
            agent_id=data.get("agent_id"),
            model=str(data.get("model") or ""),
            effort=str(data.get("effort") or ""),
            note=str(data.get("note") or ""),
        )


def _attempt_from_dict(data: dict[str, Any]) -> Attempt:
    usage = data.get("usage") or {}
    classification = data.get("classification")
    return Attempt(
        number=int(data.get("number", 0)),
        model=str(data.get("model") or ""),
        effort=str(data.get("effort") or ""),
        verdict=Verdict(data.get("verdict", Verdict.FAIL.value)),
        summary=str(data.get("summary") or ""),
        classification=FailureKind(classification) if classification else None,
        usage=Usage(
            tokens_in=int(usage.get("tokens_in", 0)),
            tokens_out=int(usage.get("tokens_out", 0)),
            cost_usd=float(usage.get("cost_usd", 0.0)),
            wall_time_s=float(usage.get("wall_time_s", 0.0)),
        ),
    )


@dataclass
class RunState:
    """The live state of one run, and the only thing the scheduler mutates."""

    spec: RunSpec
    tasks: dict[str, TaskRecord] = field(default_factory=dict)
    ledger: BudgetLedger = field(default_factory=lambda: BudgetLedger(RunBudget()))
    started_at: str = field(default_factory=utcnow)
    finished_at: str = ""
    cancelled: bool = False
    cancel_reason: str = ""
    replans: int = 0
    human_gate: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)

    #: The narrative `vise explain` reads back. Bounded, because a run that
    #: escalates and replans can emit hundreds and the state file is read on
    #: every resume — an unbounded log turns a resume into a large parse.
    MAX_EVENTS: int = 2000

    def emit(self, kind: str, **fields: Any) -> dict[str, Any]:
        """Append one decision to the run's own record.

        Every scheduler decision goes through here, including the ones that do
        nothing. "Task deferred because another task holds src/auth/**" is the
        line that explains a run that took twice as long as its plan said.
        """
        event = {"ts": utcnow(), "kind": kind, **fields}
        self.events.append(event)
        if len(self.events) > self.MAX_EVENTS:
            del self.events[: len(self.events) - self.MAX_EVENTS]
        return event

    @classmethod
    def for_tasks(cls, spec: RunSpec, task_ids: Iterable[str]) -> RunState:
        state = cls(spec=spec, ledger=BudgetLedger(spec.budget))
        for task_id in task_ids:
            state.tasks[task_id] = TaskRecord(task_id=task_id)
        return state

    # --- queries ---------------------------------------------------------

    def record(self, task_id: str) -> TaskRecord:
        return self.tasks.setdefault(task_id, TaskRecord(task_id=task_id))

    def completed_ids(self) -> set[str]:
        """Tasks a dependent may now start after.

        Only ``SUCCEEDED`` counts. A failed dependency is not "done" — letting a
        downstream task start on it would build on work the runtime has already
        judged wrong, and the failure would surface later wearing the downstream
        task's name.
        """
        return {t.task_id for t in self.tasks.values() if t.state is TaskState.SUCCEEDED}

    def in_flight(self) -> set[str]:
        return {t.task_id for t in self.tasks.values() if t.state is TaskState.RUNNING}

    def by_state(self, state: TaskState) -> list[TaskRecord]:
        return [t for t in self.tasks.values() if t.state is state]

    def unfinished(self) -> list[TaskRecord]:
        return [t for t in self.tasks.values() if not t.is_terminal()]

    def is_done(self) -> bool:
        """True when nothing can move without a person or a new plan."""
        if self.cancelled or self.human_gate:
            return True
        return all(
            t.is_terminal() or t.state is TaskState.WAITING_HUMAN
            for t in self.tasks.values()
        )

    def succeeded(self) -> bool:
        """A run succeeds only when every task did."""
        return bool(self.tasks) and all(
            t.state is TaskState.SUCCEEDED for t in self.tasks.values()
        )

    # --- transitions -----------------------------------------------------

    def set_state(self, task_id: str, state: TaskState, note: str = "") -> TaskRecord:
        record = self.record(task_id)
        record.state = state
        if note:
            record.note = note
        return record

    def start(self, task_id: str, *, agent_id: str | None, model: str, effort: str) -> TaskRecord:
        record = self.record(task_id)
        record.state = TaskState.RUNNING
        record.agent_id = agent_id
        record.model = model
        record.effort = effort
        self.ledger.start_worker()
        return record

    def finish(self, task_id: str, result: TaskResult) -> TaskRecord:
        """Fold one completed attempt into the record. Does not decide a state.

        Deciding is ``recovery.decide``'s job, and keeping them apart is what
        stops "the worker said pass" from becoming "the task succeeded" through
        a convenient default.
        """
        record = self.record(task_id)
        record.result = result
        record.attempts.append(result.to_attempt(record.attempt_count + 1))
        self.ledger.spend(task_id, result.usage)
        return record

    def cancel(self, reason: str) -> None:
        self.cancelled = True
        self.cancel_reason = reason
        self.finished_at = utcnow()
        for record in self.tasks.values():
            if not record.is_terminal():
                record.state = TaskState.CANCELLED
                record.note = record.note or reason

    def stop_for_human(self, reason: str) -> None:
        """Park the run. Unfinished tasks wait; finished ones keep their verdict."""
        self.human_gate = reason
        self.finished_at = utcnow()
        for record in self.tasks.values():
            if not record.is_terminal():
                record.state = TaskState.WAITING_HUMAN
                record.note = record.note or reason

    # --- serialisation ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cancelled": self.cancelled,
            "cancel_reason": self.cancel_reason,
            "human_gate": self.human_gate,
            "replans": self.replans,
            "budget": self.ledger.report(),
            "tasks": {k: v.to_dict() for k, v in sorted(self.tasks.items())},
            "events": list(self.events),
        }

    def save(self, root: Path | str) -> Path:
        """Write the run's state file. Best-effort directory creation, real write.

        Unlike telemetry, this is not a side channel: a resume reads it back, so
        a silently dropped write would resume a run into a state that never
        happened.
        """
        path = Path(root) / "runs" / self.spec.run_id / "state.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return path

    @classmethod
    def load(cls, root: Path | str, run_id: str) -> RunState | None:
        path = Path(root) / "runs" / run_id / "state.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        spec_data = data.get("spec") or {}
        budget_data = (data.get("budget") or {}).get("budget") or {}
        spec = RunSpec(
            run_id=str(spec_data.get("run_id") or run_id),
            goal=str(spec_data.get("goal") or ""),
            project_dir=str(spec_data.get("project_dir") or ""),
            graph_name=str(spec_data.get("graph_name") or ""),
            node_id=str(spec_data.get("node_id") or ""),
            budget=RunBudget(
                max_cost_usd=float(budget_data.get("max_cost_usd", 0.0)),
                max_workers=int(budget_data.get("max_workers", 0)),
                max_parallel=int(budget_data.get("max_parallel", 4)),
                max_wall_time_s=float(budget_data.get("max_wall_time_s", 0.0)),
            ),
            created_at=str(spec_data.get("created_at") or utcnow()),
        )
        state = cls(spec=spec, ledger=BudgetLedger(spec.budget))
        state.started_at = str(data.get("started_at") or utcnow())
        state.finished_at = str(data.get("finished_at") or "")
        state.cancelled = bool(data.get("cancelled"))
        state.cancel_reason = str(data.get("cancel_reason") or "")
        state.human_gate = str(data.get("human_gate") or "")
        state.replans = int(data.get("replans", 0))
        for task_id, record in (data.get("tasks") or {}).items():
            state.tasks[task_id] = TaskRecord.from_dict(record)
        state.events = [e for e in (data.get("events") or []) if isinstance(e, dict)]
        spent = (data.get("budget") or {}).get("spent") or {}
        state.ledger.spent = Usage(
            tokens_in=int(spent.get("tokens_in", 0)),
            tokens_out=int(spent.get("tokens_out", 0)),
            cost_usd=float(spent.get("cost_usd", 0.0)),
            wall_time_s=float(spent.get("wall_time_s", 0.0)),
        )
        state.ledger.workers_started = int((data.get("budget") or {}).get("workers_started", 0))
        return state
