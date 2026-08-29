"""What a run may spend, and when it stops — see docs/scheduler.md § Admission.

The budget is the first admission question and the only one whose answer stops
the run rather than deferring a task. That ordering is deliberate: it is the
cheapest check and the most absolute.

**A run out of budget stops. It does not degrade to a cheaper model and keep
going.** Silent downgrade is the failure that makes a budget useless — the run
finishes, under budget, having produced work nobody would have authorised at
that quality, and the ceiling gets read as evidence the system is economical.

Ceilings of ``0`` are *unset*, not unlimited. The ledger reports them as unset,
which is the honest answer to "why did this cost twelve dollars": nobody said it
could not.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from vise.runtime.contracts import RunBudget, Usage


@dataclass(frozen=True)
class Admission:
    """Whether one more task may start, and what to do when it may not.

    ``stop`` distinguishes the two refusals that look alike and are not: a run
    over its cost ceiling is finished, while a run at ``max_parallel`` just has
    to wait. A scheduler that cannot tell them apart either wedges or overspends.
    """

    ok: bool
    reason: str = ""
    stop: bool = False

    def __bool__(self) -> bool:
        return self.ok


@dataclass
class BudgetLedger:
    """Tracks what a run has spent and answers whether it may spend more."""

    budget: RunBudget = field(default_factory=RunBudget)
    spent: Usage = field(default_factory=Usage)
    workers_started: int = 0
    by_task: dict[str, Usage] = field(default_factory=dict)

    def spend(self, task_id: str, usage: Usage) -> None:
        """Record one attempt's consumption. Attempts accumulate per task."""
        self.spent = self.spent + usage
        self.by_task[task_id] = self.by_task.get(task_id, Usage()) + usage

    def start_worker(self) -> None:
        self.workers_started += 1

    def remaining_usd(self) -> float | None:
        """Budget left, or None when no cost ceiling was set."""
        if not self.budget.max_cost_usd:
            return None
        return max(0.0, self.budget.max_cost_usd - self.spent.cost_usd)

    def exhausted(self) -> bool:
        remaining = self.remaining_usd()
        return remaining is not None and remaining <= 0

    def admit(
        self,
        estimated_cost_usd: float = 0.0,
        *,
        in_flight: int = 0,
        elapsed_s: float | None = None,
    ) -> Admission:
        """May one more task start now?

        Order matters: the ceilings that end the run are checked before the ones
        that merely defer, so a saturated scheduler on an exhausted budget
        reports "out of budget" rather than "try again later".
        """
        remaining = self.remaining_usd()
        if remaining is not None and estimated_cost_usd > remaining:
            return Admission(
                False,
                f"estimated ${estimated_cost_usd:.2f} exceeds ${remaining:.2f} remaining "
                f"of the ${self.budget.max_cost_usd:.2f} run ceiling",
                stop=True,
            )
        if self.budget.max_workers and self.workers_started >= self.budget.max_workers:
            return Admission(
                False,
                f"{self.workers_started} workers started, ceiling is "
                f"{self.budget.max_workers}",
                stop=True,
            )
        # `elapsed_s` is real wall clock and the ledger cannot know it: with
        # parallelism, summed worker time is larger than the time that passed.
        # A scheduler enforcing a deadline passes the true elapsed value; the
        # aggregate is the fallback, and it is conservative in the right
        # direction — it trips early, never late.
        wall = self.spent.wall_time_s if elapsed_s is None else elapsed_s
        if self.budget.max_wall_time_s and wall >= self.budget.max_wall_time_s:
            measure = "aggregate worker time" if elapsed_s is None else "elapsed"
            return Admission(
                False,
                f"{wall:.0f}s {measure}, ceiling is {self.budget.max_wall_time_s:.0f}s",
                stop=True,
            )
        if self.budget.max_parallel and in_flight >= self.budget.max_parallel:
            return Admission(
                False,
                f"{in_flight} tasks in flight, max_parallel is {self.budget.max_parallel}",
                stop=False,
            )
        return Admission(True)

    def report(self) -> dict[str, Any]:
        """The numbers behind "why did this run cost what it cost"."""
        return {
            "budget": self.budget.to_dict(),
            "spent": self.spent.to_dict(),
            "remaining_usd": self.remaining_usd(),
            "workers_started": self.workers_started,
            "by_task": {k: v.to_dict() for k, v in sorted(self.by_task.items())},
            "unset_ceilings": [
                name
                for name, value in (
                    ("max_cost_usd", self.budget.max_cost_usd),
                    ("max_workers", self.budget.max_workers),
                    ("max_wall_time_s", self.budget.max_wall_time_s),
                )
                if not value
            ],
        }
