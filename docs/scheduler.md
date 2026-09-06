# The scheduler

The scheduler is the part of the runtime that turns "this phase contains five
tasks" into "run these three now, those two after, and stop if the third one
fails twice". It lives in [`scheduler.py`](../src/vise/runtime/scheduler.py);
[`planner.py`](../src/vise/runtime/planner.py) is the half that needs no
execution — wave computation and admission — and is what `vise runtime plan`
prints.

This document was written before either of them, and that ordering is the point.
A scheduler is the easiest place in a system like this to accumulate policy
nobody can state out loud.

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

## Expansion

A `dag` node's tasks are a list written before anyone has seen the data. A
task may instead declare `for_each` and become one task per item of a list an
upstream task produced:

```yaml
- id: "split"
  role: "research"
  writes: false
- id: "per-question"
  role: "research"
  writes: false
  for_each:
    from: "split"            # the task whose result it expands
    items: "sub_questions"   # the key in that task's artifact payload
    max_items: 8             # the width this may reach; 0 means the default
  prompt: |
    Answer this one sub-question and no other: {item}
```

When `split` succeeds, the scheduler reads `sub_questions` from its artifact
and creates `per-question[1]` … `per-question[n]`. They are ordinary tasks —
routed, admitted, gated, verified, escalated and integrated like any other —
each with `{item}` (or `{item.key}`, for an object) substituted into its
prompt, acceptance criteria and ownership patterns, and its item on the last
line of its prompt whether or not the template used the placeholder.

`per-question` itself never reaches a worker. It is ready twice: once when its
source succeeds, and it *expands*; once when every child has succeeded, and it
*joins*, at no cost, writing a `collection` artifact under its own id that
says how wide the fan-out was, what each item was, and how each child ended. A
downstream task that depends on it receives the children's artifacts beside
that collection.

What the source said decides three different things, and they are kept apart —
the same distinction `runtime/decouple.py` draws between `found: 0` and "could
not look":

| The source's artifacts…                         | The template                                                |
|-------------------------------------------------|-------------------------------------------------------------|
| carry the key with items                        | expands; the `expanded` event carries the count and the cap |
| carry the key, empty                            | joins with zero children and says so; downstream still runs |
| do not carry the key, or there is no store      | is `BLOCKED`, naming the source, the key, and what it did carry |

**The cap is reported, never silent.** `max_items` defaults to
`DEFAULT_MAX_ITEMS` in `runtime/expand.py` — 25, not the 250 a research
product runs on its own fleet, because a child here is a `claude -p` session on
this machine and a default that could spend 250× one estimate on one list is
one nobody would defend after the bill. A longer list is cut to the cap, and
`expansion_truncated` records how many items were dropped; the join's note and
the collection carry the same number.

A child fails the way any task does — the ladder, then a person — and the join
waits. The run stops naming the child; siblings that passed keep their pass;
`resume` derives the same children from the items the state recorded and
retries only what did not finish. The replanner is handed the live task list,
so a replan after an expansion keeps the children rather than cancelling them
as dropped.

`vise runtime plan` has no items. It shows the template once, marked
`×1..8 — one per item of split's 'sub_questions'`, prices it as a range from
one child to the cap, and notes when the widest case would not fit the budget —
a note, not a problem, because admission stops the run for a person when the
money runs out, and that is the design.

The width is decided by the data and applied by code. The worker's whole say is
the list; the count, the cap and the join are the scheduler's. A `for_each` run
without an artifact store blocks rather than guesses, and the CLI always
supplies one.

## Convergence

The other shape the task list could not express. "Find the duplicated
helpers", "find the untested branches", "find the case against" are not one
attempt at a known job — the right number of passes is a property of the
repository. Run once and the tail is missed, because the last round is where
the hard one is; run a fixed five and four are paid for to report nothing.

```yaml
- id: "hunt"
  role: "research"
  writes: false
  until:
    key: "findings"     # the payload key a round reports under
    stable_for: 2       # quiet rounds required to stop
    max_rounds: 5       # and the bound if it never goes quiet
```

**A round is a passing attempt, and an attempt is not a round.** A failed
round is a failed attempt: it takes the escalation ladder, exactly as it would
without `until`, and does not count as quiet. Folding "found nothing" into
"was wrong" is the conflation of `INCONCLUSIVE` with `FAIL` that `Verdict`
exists to prevent — and it would send the next round to a bigger model to fix
code that may be fine.

**The deduplication is code.** After each passing round the scheduler keys
what it reported, counts the keys no earlier round had, and decides. Nothing
is asked whether a finding is new: an agent asked that says yes, having no
memory of the other rounds and every incentive to have found something. Keys
are the item's text, matched exactly — a near-match rule would decide two
genuinely different findings are one and the second would never be reported,
which is a silent loss. Exact matching errs toward one more round, which
costs money instead of a finding.

Each later round's brief carries what the earlier ones found, marked as not to
be reported again. That is not politeness: a round that cannot see them
re-reports them, the runtime correctly counts that as nothing new, and the
round is paid for and buys a duplicate. The list is capped, and the cap is
stated in the line the worker reads rather than applied behind it.

Where a task also declares acceptance criteria, every round is verified — the
verifier judges that round's pass, and whether the sweep is finished is a
different question, counted rather than asked. `seen`, `rounds` and `stable`
live on the task's record and are persisted, so a resumed sweep continues
rather than starting over; one that forgot what it found would re-report all
of it and call that a round that found something.

Two ways to stop, and the record says which: `stable_for` consecutive quiet
rounds is convergence, and `max_rounds` is a bound — the note then says the
sweep may not be finished, because it may not be.

`vise runtime plan` prices a sweep as a range, and its floor is not one round:
the earliest a sweep can stop is when every round from the first is quiet, so
`stable_for` rounds is the cheapest it can be. Declared on a task that also
expands, the sweep is inherited by every child and the two multiply — four
items each sweeping four times is sixteen rounds, and the plan says so before
anyone runs it.

## The panel

`SUCCEEDED` needs a verifier. A task may declare that it needs more than one:

```yaml
- id: "auth"
  role: "backend"
  acceptance: ["an expired token is rejected with 401"]
  verifiers: 3
```

Each member is briefed from a different lens — the criteria as written,
whether the quoted evidence reproduces, what an adversary would try, what the
change broke that nobody asked it to — cycling when more are asked for than
there are lenses. Distinct questions rather than one question asked louder:
three agents given one prompt return one opinion three times, and the
disagreement that makes a panel worth its price has to be built into what they
are each asked. No member is told what the others think, or that there are
others; a verifier who knows two colleagues already passed is deciding whether
to disagree with them.

The decision is code. A majority of passes succeeds; a majority of fails
escalates with the union of the failing members' reasons — and only theirs,
because a passing member's notes would send the next attempt to fix what
somebody thought was already right. Anything else is `BLOCKED`: verifiers who
could not decide have not decided, and this gate fails closed like every
other. A majority rather than unanimity because the lenses differ on purpose —
the member checking whether evidence reproduces may have nothing to say about
a criterion that is about wording, and unanimity would let the lens least able
to evaluate veto the ones that could.

Each member's verdict is filed under its own id (`auth::verify[2]`) and
charged to the ledger under it, so a panel leaves three readable answers and
`vise runtime budget` can say what three opinions cost. The panel's decision
is filed under the task, which is what a downstream task reads. A panel of one
is exactly the single verifier that shipped before panels existed: same id,
same brief, same artifact, and no `panel` event, because a majority of one is
not a split.

`vise runtime plan` prices the verification a task will actually get. It used
to price none, understating every verified run by a model call — invisibly,
and by three once a task declares a panel.

## The spec gate

Asked once, before the first dispatch, whenever the run contains a task that
writes: **does this project have a plan?**

vise's node gate already makes the spec phase impossible to talk past, and
`skills/orchestration/SKILL.md` says why delegation is not an escape hatch — a
subagent hits the same gate, because it is a node gate rather than a prompt.
The execution plane broke that. A `dag` node's tasks never traverse the graph;
the scheduler turns them straight into subprocesses, so they reach no node at
all. Without this gate the runtime is a side door around the strongest
guarantee vise ships, and it is a door vise built itself.

The bar is an active OpenSpec change with a `proposal.md` and well-formed spec
deltas — every `### Requirement:` carrying at least one `#### Scenario:`.
Deliberately **not** `tasks_complete`, which is the bar the node gate uses on
the edge into the irreversible phase: there the work is done, here the run is
what does it. Requiring a ticked checklist to start would gate work on its own
output.

Three properties, each load-bearing:

- **It is asked once, before anything opens.** Not per task. A per-task check
  would let the first three tasks spend money before the fourth discovered the
  project has no specs — a run that ends half-applied, which is worse than one
  that never starts. A blocked run creates no worktrees and its recorded cost
  is zero.
- **A read-only run is exempt.** A run where every task declares
  `writes: false` cannot change the system's contract, so there is nothing for
  a specification to describe. The gate's red should always mean "you are about
  to build something nobody wrote down".
- **It never shells out and never raises.** The `openspec` CLI is a Node
  package; a gate that goes red because a teammate has not run `npm i -g`
  teaches people to bypass it. Everything here reads files vise already owns,
  and an unreadable planning tree is a refusal with an accurate reason rather
  than a crash.

`vise runtime plan` reports the same verdict among its problems, so the block
costs nothing to discover — you never first learn you are gated from the
command that spends money.

The only bypass is `VISE_NODE_GATE_OVERRIDE=1`, the same variable the node gate
honours, and an overridden run records `spec_gate_overridden` rather than
reporting itself as having passed. There is deliberately no `--no-spec-gate`
flag: a bypass that costs one keystroke and leaves no trace is not a gate, it
is a default.

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

### One tree or many

In a single shared working tree, a git diff cannot say whose file is whose, so
the ownership gate has to excuse paths a concurrent peer was entitled to write.
That is correct and bounded and still an excuse: a task writing into a peer's
territory goes unnoticed for as long as that peer could be running.

`--isolate` removes the question instead of bounding it. Each writing task gets
its own git worktree branched from HEAD, runs there, is verified there, and is
integrated into the main tree only once it has passed. The only writes in a
task's tree are that task's, so the gate goes back to being strict.

Integration is a three-way apply. A conflict means two tasks changed the same
lines, which is either an ownership declaration that was wrong or a plan that
was — both decisions, so the runtime reports the conflict and blocks the task
rather than picking a side. A refused apply is backed out to exactly the paths
it touched, so the main tree is never left half-patched.

It is off by default: it needs a git repository with a commit and costs a
checkout per task. Where it cannot run, the scheduler says so on the record and
falls back to the shared tree.

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

A `for_each` template never enters `RUNNING`: it is expanded at its first
readiness and joined at its second, both in-process, and its `SUCCEEDED` means
every child reached theirs. See [Expansion](#expansion).

## Concurrency

Threads, not processes or asyncio, because a worker is I/O-bound by
construction: the adapter shells out and waits. Threads keep the loop readable
and let a worker use ordinary blocking subprocess calls.

The pool is sized to `max_parallel`. Admission is re-evaluated on every pass, so
a task deferred for ownership is retried as soon as the task holding the claim
finishes — there is no queue to fall to the back of.

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

## Three passes above the worker

Each answers a question the worker cannot answer about itself.

**Diagnose.** A failure's classification decides retry vs escalate vs replan, so
a wrong one costs a whole strategy rather than one attempt — and letting the
failing worker classify its own failure is the same mistake as letting it grade
its own pass. Sources in order: the worker's own classification when it gave one
(it was there), then a text heuristic that only recognises a machine that was not
present, and only then a debugger agent. Most failures name themselves; a model
call to confirm that is waste. A debugger that cannot answer leaves the
classification unset, which escalates — the safe direction, since "nobody said"
is not evidence the work was fine.

**Review.** One adversarial pass over the whole node once every task has
succeeded, off by default. Not per task: the questions worth asking — what two
of these changes do to each other, what an existing caller sees now — are about
the node, and asking them once per task is both more expensive and worse at
answering them. A blocking verdict parks the run; nothing is reverted, because
deciding what to do about a shipping objection is a person's call.

**Roll back.** Under isolation, a failed attempt's worktree is discarded so the
next attempt starts from HEAD rather than from its own failed output. Offered
only with `--isolate`: in a shared tree the same operation would revert files the
runtime cannot prove belong to this task alone, which is the whole reason
isolation exists.

**Reassign is deliberately absent.** The registry resolves a role plus a
capability to exactly one agent and reports ambiguity rather than breaking it
alphabetically. "Try a different agent" would mean picking the one it already
refused to pick by coincidence — and if a second agent genuinely fits, the fix is
to say so in the task rather than to have the scheduler discover it after a
failure.

## Human gates

A task may also declare `requires_human: true`, and the scheduler parks before
dispatching it. That check runs before the budget check, because the point of
the flag is that the work should not start, and finding out only because the
money ran out would be an accident.

Otherwise the scheduler stops and reports `WAITING_HUMAN` — it does not choose —
when:

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

## Cancellation

Two ways, because the person cancelling is usually not in the process doing the
work. `SchedulerConfig.should_cancel` is the in-process hook — a UI button, a
signal handler. `vise runtime cancel <run>` writes a sentinel file under the
run's state directory, which the loop checks before each dispatch. A file rather
than a signal or a socket: the scheduler may be in another process, on another
terminal, started by another tool, and the one thing all of those share is the
state directory they were told to use.

A cancelled run marks its unfinished tasks `CANCELLED` and stops. It does not
wait for in-flight workers to be killed — it cannot kill them, and pretending
otherwise would make the state file lie about what was running.

## What the scheduler must never do

- Traverse a graph edge. It reports; the gate decides.
- Lower a budget ceiling, widen an ownership claim, or downgrade a verdict.
- Retry a task whose failure was a wrong answer. That is escalation, and calling
  it a retry hides the cost.
- Treat "flaky" as a diagnosis. A test that fails twice on the same input failed.
