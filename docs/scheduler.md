# The scheduler

The scheduler is the part of the runtime that turns "this phase contains five
tasks" into "run these three now, those two after, and stop if the third one
fails twice". It does not exist yet — this document specifies it, and
[`planner.py`](../src/vise/runtime/planner.py) implements the half of it that
needs no execution: wave computation and admission.

Writing the spec before the code is the point. A scheduler is the easiest place
in a system like this to accumulate policy that nobody can state out loud.

## What it schedules

Not phases. A phase transition is the graph's business, gated by validators, and
the scheduler has no vote in it. The scheduler schedules **tasks inside one DAG
node**, and reports a verdict the node's gate can read.

```
graph:   implement ──────────────────► test
              │  (gate: validators_green)
              │
scheduler:    ├─ wave 1 ─┬─ backend-auth
              │          ├─ frontend-login
              │          └─ migration
              │
              └─ wave 2 ─── integration
```

When every task in the node reaches a terminal state, `is_dag_complete` goes
true and the ordinary gate decides whether the edge is traversable. Nothing about
the transition changes.

## Waves

A wave is the set of tasks that are ready at the same time and may run
concurrently. It is derived, never declared: declaring waves in YAML duplicates
the dependency edges, and the two copies drift.

```
ready(W₀) = { t : deps(t) = ∅ }
ready(Wₙ) = { t : deps(t) ⊆ done(W₀ … Wₙ₋₁) }
```

`compute_ready_tasks` already computes `ready(W₀)` against live state. The
planner computes the whole sequence ahead of time by simulating completion,
which is what makes a plan showable before a single model call.

A wave is a *planning* unit, not a barrier. A scheduler that waits for every task
in a wave before starting any task in the next one wastes the wall-clock of its
slowest member. The waves exist so a person can read the plan; dispatch follows
dependencies, not wave boundaries.

## Admission

A task that is dependency-ready is not yet runnable. Four questions decide, in
this order — cheapest and most absolute first:

1. **Budget.** Would starting this task exceed the run's cost, worker, or
   wall-clock ceiling? A run out of budget stops; it does not degrade quietly to
   a cheaper model and keep going.
2. **Ownership.** Does any in-flight task claim a path this one also claims? Two
   agents editing the same file concurrently produce a diff neither of them
   wrote. See below.
3. **Concurrency.** Is `max_parallel` already saturated?
4. **Capability.** Does a registered agent exist that can do this task's role,
   and is it allowed to write if the task writes?

Failing 1 stops the run. Failing 2 or 3 defers the task. Failing 4 is a planning
bug and fails the task immediately — a task nobody can execute should never have
been planned.

## Ownership

Every task that writes declares the paths it owns, as globs:

```yaml
tasks:
  - id: backend-auth
    ownership: ["src/auth/**"]
  - id: frontend-login
    ownership: ["web/src/login/**"]
```

Two tasks conflict when their ownership sets intersect. Intersection is decided
structurally — `src/**` conflicts with `src/auth/x.py` even though neither string
contains the other — because the alternative is deciding it by literal prefix and
being wrong exactly where it matters.

This generalises a rule mini-vise already enforces at a coarser grain: two open
flows may not share a working directory, because a diff the reviewer reads has to
trace to one flow. Same reasoning, finer unit. A task with no declared ownership
is treated as owning everything and therefore runs alone — the safe default, and
a visible one, since the plan shows it as a wave of one.

Ownership is not a lock. It is an admission rule evaluated against tasks that are
*in flight*, and it is enforced by not dispatching, never by rejecting a write
after the fact.

## Task states

```
    PENDING ──► READY ──► RUNNING ──┬──► SUCCEEDED
        ▲                           │
        │                           ├──► FAILED ──► (retry / escalate)
        │                           │
        └──────── replan ───────────┴──► BLOCKED ──► WAITING_HUMAN
                                         CANCELLED
```

`SUCCEEDED` means the worker reported a pass **and** a verifier agreed. A worker
grading its own homework is the failure mode the whole design exists to prevent;
`worker-contract.md` says why the verifier is a separate agent with a separate
input.

## Retry, escalation, replan

Three different responses to failure, and conflating them is how an orchestrator
burns a budget going in circles.

- **Retry** — same task, same agent, same model. Only for failures whose cause is
  outside the work: a timeout, a transport error, a tool that was not installed.
  Bounded at 1 by default.
- **Escalate** — same task, more capable model or higher effort. For failures
  where the work was attempted and was wrong. The ladder is in
  [`model-routing.md`](model-routing.md).
- **Replan** — throw the task graph away and build a new one. For when the same
  task has failed `max_attempts` times, or when a failure's classification says
  the *plan* was wrong rather than the work: a `SPEC_BUG` or an
  `ARCHITECTURE_BUG` cannot be fixed by trying harder at the same task.

Every attempt is recorded, and every subsequent attempt's brief carries the
previous ones:

```
previous attempts on this task — already tried, do not repeat:
  attempt 1 [sonnet/medium, code_bug] parser accepted '٥' as a digit
  attempt 2 [sonnet/high,   test_bug] the added test asserted the wrong branch
```

This is mini-vise's lap history, which is the single cheapest anti-loop device
either codebase has: an agent that cannot see what the last agent tried will try
it again, and confidently. It costs a few hundred tokens and it is the difference
between three attempts and three identical attempts.

## Human gates

The scheduler stops and reports `WAITING_HUMAN` — it does not choose — when:

- the run's budget is exhausted,
- a task's blast radius crosses into a destructive migration or a breaking public
  API change,
- a replan would change the scope the user actually approved,
- two agents disagree on an architectural decision and neither is wrong on the
  evidence,
- a security finding's fix has a materially different design than the code under
  review.

Each of these is a case where continuing is cheap and being wrong is expensive.
That asymmetry, not a confidence threshold, is the test for adding one.

## What the scheduler must never do

- Traverse a graph edge. It reports; the gate decides.
- Lower a budget ceiling, widen an ownership claim, or downgrade a verdict.
- Retry a task whose failure was a wrong answer. That is escalation, and calling
  it a retry hides the cost.
- Treat "flaky" as a diagnosis. A test that fails twice on the same input failed.
