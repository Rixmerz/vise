# The spec gate reaches the execution plane

## Why

`mandatory-openspec-gate` made the spec phase impossible to talk past. Its
argument, in `skills/orchestration/SKILL.md`, names delegation as the first
thing that is *not* an escape hatch:

> **Delegando.** A subagent hits the same gate — it is a node gate, not a
> prompt. Dispatching a builder from `spec` does not move the workflow.

That sentence was true when the only way to reach a worker was through the
graph. The agent runtime broke it. A `dag` node's tasks are dispatched by
`vise.runtime.scheduler` as `claude -p` subprocesses; they never traverse the
graph, so they never reach a node gate at all. `grep -ri openspec
src/vise/runtime/ docs/` returns nothing.

This is not theoretical. A six-task DAG run against a scratch repo wrote a
whole Python package — four implementation tasks, a test suite, a README —
with no `openspec/` root anywhere and nothing to stop it. The gate vise ships
has a side door, and it is the door vise itself just built.

## What Changes

- A new `vise.runtime.spec_gate` module answering one question: may this run
  dispatch work that writes?
- `Scheduler.run` asks it once, before the first dispatch, whenever the run
  contains a task that writes. A run that fails the gate spends nothing.
- `vise runtime plan` reports the same verdict under `problems`, so the block
  is visible before anyone spends money rather than after.
- `--change <name>` pins one change; without it any well-formed active change
  satisfies the gate.

The default is `deltas`: an active change with a `proposal.md` and well-formed
spec deltas. Deliberately **not** `tasks_complete` — the run is what ticks
those boxes, and requiring them first would gate the work on its own output.

## Capabilities

### New Capabilities
- `runtime-spec-gating`: refusing to dispatch writing tasks in a project with
  no well-formed OpenSpec change.

## Impact

- New: `src/vise/runtime/spec_gate.py`,
  `src/vise/tests/test_runtime_spec_gate.py`
- Modified: `src/vise/runtime/scheduler.py`, `src/vise/runtime/planner.py`,
  `src/vise/cli/runtime_cmd.py`, `src/vise/tools/runtime.py`,
  `docs/scheduler.md`, `docs/agent-runtime.md`, `README.md`
- Behavioural: `vise runtime run` in a project with no `openspec/` root now
  refuses before dispatching. Read-only runs are unaffected. This is the point
  of the change; the evidence names `openspec init`.
- No new dependency. `openspec_profile` is stdlib string work and the CLI stays
  optional — the runtime gate never shells out.
