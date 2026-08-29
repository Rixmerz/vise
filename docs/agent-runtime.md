# The agent runtime

vise decides **what process** a change follows. It does not decide **who does
the work**. Today the answer is always the same: one Claude Code session walks
every phase in order, holding one context that grows until it is compacted.

That is fine for a two-file fix and wrong for anything with independent parts.
Three files that do not touch each other are still edited one after another, by
one model, at one reasoning effort, with the whole conversation in scope. The
graph knows the phases are ordered; nothing knows the *work inside a phase* is
not.

This document describes the plane that answers the second question, and — more
importantly — the boundary that keeps it from turning into a second workflow
engine.

## Two planes

```
                        Claude Code
                             │
                            MCP
                             │
        ┌────────────────────▼────────────────────┐
        │              CONTROL PLANE              │   (exists today)
        │   graph · state · gates · memory · git  │
        └────────────────────┬────────────────────┘
                             │
        ┌────────────────────▼────────────────────┐
        │             EXECUTION PLANE             │   (new)
        │  registry · router · scheduler · budget │
        └────────────────────┬────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
           worker         worker         worker
              └──────────────┼──────────────┘
                             ▼
                         artifacts
```

The control plane is the authority. The execution plane asks it for permission
and reports back; it never decides a phase transition, never overrides a gate,
and never writes graph state directly.

## The rule: no second workflow engine

vise already models workflows as directed graphs with conditional edges, per-node
tool restrictions, and validator gates. A parallel "task graph" with its own
nodes, its own edges, and its own transition rules would be a second engine with
the same job, and the two would disagree within a release.

They do not need to. `Node.node_type == "dag"` already holds a list of `Task`
objects with `dependencies`, `graph_engine.compute_ready_tasks` already computes
which of them are unblocked, and `is_dag_complete` already decides when the node
is finished. The seam the runtime needs is not a new structure — it is *more
fields on the one that exists*, plus something that executes it.

So:

- The **graph** stays the authority on what phase the project is in.
- The **DAG node** stays the authority on what work that phase contains and in
  what order.
- The **runtime** decides who runs each task, on which model, with what context,
  in which order within a wave, and when to stop.

## Two levels, and why the distinction is load-bearing

A **workflow** answers *what process must this change follow*:

```
spec → implement → test → review → done
```

A **task** answers *what concrete work must an agent do*:

```
id:         backend-auth
role:       backend
ownership:  src/auth/**
model:      sonnet
effort:     medium
```

One phase, many tasks. Collapsing the two is how orchestration frameworks end up
unable to express "three independent implementations, then one integration" —
they either make each task a phase (and the gates fire nine times for one
change) or make the phase a task (and lose the parallelism).

```
WORKFLOW  spec ──► implement ──► test ──► review ──► done
                       │
                       └── TASK GRAPH
                             ├── backend-auth    (src/auth/**)
                             ├── frontend-login  (web/src/login/**)
                             └── migration       (db/migrations/**)
```

Gates fire on the **workflow** edge. Tasks do not have gates; they have
verdicts, and the phase gate reads them.

## What the runtime is made of

| Piece | Answers | Module |
|---|---|---|
| Contracts | what a task, a result, an artifact, a run *are* | `runtime/contracts.py` |
| Agent registry | who can do this kind of work | `runtime/registry.py` |
| Model router | which model and effort, and why | `runtime/routing.py` |
| Ownership | can these two tasks run at the same time | `runtime/ownership.py` |
| Budget | may this task run at all | `runtime/budget.py` |
| Artifact store | what a worker hands the next worker | `runtime/artifacts.py` |
| Worker | how a task is actually executed | `runtime/worker.py` |
| Planner | which tasks form which wave | `runtime/planner.py` |
| Scheduler | dispatch, collect, retry, escalate | `runtime/scheduler.py` |
| Run state | what a run knows about itself | `runtime/state.py` |
| Recovery | retry vs escalate vs replan | `runtime/recovery.py` |
| Context | what a worker is shown, and what it is not | `runtime/context.py` |
| Verification | the second opinion that makes SUCCEEDED mean something | `runtime/verify.py` |
| Adapter | running a brief as a Claude Code session | `runtime/adapters/claude_code.py` |

## Testable without spending anything

Exactly one module can spend money — `adapters/claude_code.py` — and its
subprocess call goes through an injected runner, so argv construction, timeout
handling, output parsing and cost accounting are all driven in tests by recorded
fixtures. Everything else runs offline and deterministically against
`worker.MockWorker`.

That is the acceptance criterion, not a convenience. A contract that cannot be
exercised end to end by a mock worker in a unit test is underspecified, and a
scheduler you can only test by paying for it is one nobody will test.

## Where dispatch is allowed to start

From the CLI (`vise runtime run`), and not from the MCP server. vise's MCP
server runs *inside* a Claude Code session; a tool that dispatched Claude Code
subagents from in there would have the session spawning sessions through the one
component that is not allowed to call another server's tools. It is the same
boundary `recipe_run` holds: vise advises and Claude Code acts.

So the `run_*` MCP tools read runs and one of them cancels a run. None of them
starts one. The operator spending the money types the command.

## Reading a plan

`vise runtime plan` is the whole milestone made visible. Given a DAG node whose
tasks declare runtime metadata:

```yaml
nodes:
  - id: "implement"
    node_type: "dag"
    tasks:
      - id: "research-oauth"
        role: "docs"
        complexity: "low"
        writes: false
      - id: "backend-python-auth"
        role: "backend"
        criticality: "elevated"
        ownership: ["src/auth/**"]
        acceptance: ["an expired token is rejected with 401"]
        dependencies: ["research-oauth"]
      - id: "migrate-users"
        role: "migration"
        ownership: ["src/auth/**"]
        dependencies: ["research-oauth"]
```

it derives the waves, resolves each task to a bundled agent, routes a model with
the reasons attached, and prices the run:

```
wave 2  (1 task(s), ~$1.20)
  backend-python-auth      backend-python       sonnet/high
      · role backend starts at sonnet/medium
      · criticality elevated adds a rung
wave 3  (1 task(s), ~$1.20)
  migrate-users            db-migrator          sonnet/high
      · role migration starts at sonnet/high
```

`migrate-users` gets a wave of its own despite depending on the same upstream
task, because it claims `src/auth/**` and so does `backend-python-auth`. Nothing
declared that ordering; ownership derived it.

## Compatibility

Every runtime field on `Task` is optional with a default that reproduces
today's behaviour: no role, no ownership, model and effort inherited from the
invoking session. The nine bundled workflows parse unchanged, the 49 MCP tools
behave unchanged, and a repo that never touches the runtime never notices it
exists.

The runtime is additive or it is wrong. A migration that makes existing
workflows re-author themselves buys nothing that a default could not.

## Related

- [`scheduler.md`](scheduler.md) — waves, admission, retry, escalation
- [`model-routing.md`](model-routing.md) — which model, which effort, and the
  measurements behind the defaults
- [`worker-contract.md`](worker-contract.md) — the brief a worker gets and the
  result it owes back
