# Composing the next plan, under a hand that is bounded

## Why

vise's two planes have been complete and disconnected. `graph_builder_*`
accepts a whole plan — tasks with ownership, model, effort, acceptance, and
the node's validators. `RunState` records everything the next plan would want:
which tasks succeeded, which did not and why, what the verifier said, why a
replan declined, what the run learned. Between the two there was a person
reading `state.json`.

Wiring them was blocked on a contradiction the code states about itself.
`runtime/replan.py`:

> **A replanner recomposes; it never authors a gate.** That boundary is the
> whole reason vise can claim anything. […] A planner allowed to write the
> condition it is judged by is a planner grading its own homework, which is
> the exact failure mode this codebase exists to prevent.

The replanner is bounded by construction: it adds one `design` task with a
hard-coded role and no validators at all. The **builder was not**.
`graph_builder_add_node(validators=[...])` accepted anything, and
`graph_builder_save` checked only that the generated YAML parsed. Composing
and gating were the same privilege, held by whoever called the tool.

## What Changes

### The hand is bounded first

`BUILDER_VALIDATORS` names the validators a composed graph may declare. The
line is drawn where it can be drawn mechanically rather than by taste: every
allowed validator runs vise's own reviewed logic. The two that are refused —
`command_exit` and `quality_check` — run a command the *repository* chose,
which is a different kind of trust and one a composed graph must not grant
itself.

`BUILDER_VALIDATORS_EXCLUDED` carries the reason as data, not as a comment, so
the suite can assert that every validator in the registry appears in exactly
one of the two sets. Adding a validator is then a decision someone has to
make, rather than a default that lets the next one through by accident — and
the next one is exactly the one nobody thought about.

Both `add_node` and `update_node` enforce it; a refused list refuses the whole
call rather than keeping the allowed half.

This binds the **builder**, not YAML written by hand. A person editing a
workflow file is authoring, and the file is reviewed as a file. These tools are
the surface an agent composes through, so they carry the constraint.

### Then the brief

`vise runtime compose <run_id>` reads a run into the terms the next plan is
written in: what is already paid for, what is not and why, what was wrong with
the plan rather than the work, what the project's memory now carries, and the
validators a composed node may declare.

It is deliberately deterministic. *What* to build next is a judgement and this
makes no attempt at it. What a composer must not have to re-derive is which
work already succeeded — getting that wrong is how a follow-up plan repeats a
task that was paid for, which is the waste the ledger exists to prevent.

## What this deliberately does not do

**It dispatches nothing.** There is no `run_start` MCP tool, by a decision
documented at `README.md:23` and pinned by `test_there_is_no_run_start_tool`,
and this change does not reopen it. The composed graph is something a person
reads and runs — and the plan they read now reports its own concurrency
ceiling and cost, which is what makes that reading cheap.

Exit code 3 when a run has nothing to compose, distinct from 0, so a script
can tell "there is a plan to write" from "this run is finished".
