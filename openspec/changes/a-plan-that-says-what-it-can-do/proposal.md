# A plan that says what it can do, and a run that can be picked up

## Why

Two findings from measuring the nine runs vise has actually performed.

### The plan lets a number imply something false

Every recorded run declared `max_parallel: 3`. Measured from the event
timelines, peak concurrency was **2** in the five runs that got that far, and
**1** in the rest. That looked like a scheduler bug and is not one — the
workload's dependencies are a chain:

```
money → parser → report → cli → { tests, docs }
```

Only the last two can ever overlap. The scheduler achieved exactly the maximum
the graph allows. The defect is that **nobody was told**: `vise runtime plan`
reports waves, cost and problems, and never that the third lane it was asked
for can never be used. A person reads `max_parallel: 3`, sees six tasks, and
concludes the run will be wide. It cannot be.

That is the same shape as a gate reporting a pass it never checked: a number
that implies a capability the system does not have. vise's own convention is
to say what is actually true, and the plan is where a person looks before
spending money.

### A parked run is a dead run

`RunState.MAX_EVENTS` carries the comment *"the state file is read on every
resume"*. There is no resume. `RunState.load` exists and its only two callers
are the read-only `status` and `explain` commands. A run that stops — and the
loop stops for a person at nine distinct construction points, starting with
the mandatory `--yes` — cannot be continued. The work it already paid for is
readable and unusable.

## What Changes

- `RunPlan` reports two derived facts and a non-blocking `notes` channel:
  **`concurrency_ceiling`** (the most tasks that can ever run at once, given
  dependencies *and* ownership conflicts) and **`critical_path`** (the longest
  chain, which is the run's floor in wall-clock terms). When the declared
  `max_parallel` exceeds the ceiling, the plan says so in `notes`.
- `notes` is separate from `problems` on purpose: a problem blocks the run and
  makes `vise runtime plan` exit non-zero. Over-declared parallelism is not an
  error — the plan is still correct and still runnable. Conflating the two
  would make an honest observation refuse to run.
- `Scheduler.run` accepts an optional pre-existing `RunState`, and
  `Scheduler.resume` prepares one: succeeded tasks keep their results,
  everything non-terminal returns to pending, the human gate and the
  cancellation are cleared, and stale reservations are released.
- `vise runtime resume <run_id>` re-resolves the run's graph node and continues.

## The money must not reset

The one correctness question resume raises. A resumed run keeps
`ledger.spent` — the cost of work already done counts against the same
ceiling. Resetting it would turn `--max-cost` into a per-attempt limit that a
loop could spend without bound by resuming.

Stale *reservations* are the opposite case and are released: a reservation is
an estimate held for a task that had started and not yet reported, and after a
crash it is holding budget for work that will now be attempted again.

## What Does Not Change

- No new task ever appears from resuming. Resume re-runs the plan it was given;
  composing a new one is a different capability and belongs to a different
  change.
- The scheduler's dispatch loop is untouched. `run` gains one optional
  parameter; `resume` prepares state and delegates. A loop refactor to support
  a second entry point would risk the part of the runtime that is hardest to
  test for the part that is easiest.
