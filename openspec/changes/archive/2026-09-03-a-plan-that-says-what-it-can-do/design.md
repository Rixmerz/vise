# Design

## The ceiling

The waves a plan renders are already narrowed twice: by dependencies, then by
`_split_on_ownership(planned, max_parallel)`. Reading the ceiling off those
waves would therefore report the budget back to itself — a graph capped at 3
would always "reach" 3.

So the ceiling is computed from the *raw* dependency waves, split on ownership
with the cap lifted:

```
ceiling = max over raw waves of (widest ownership group, unbounded)
```

`critical_path` is `len(raw_waves)` — waves are derived from the dependency
edges, so their count is the longest chain.

## notes vs problems

`problems` is load-bearing: `_cmd_plan` returns 1 when it is non-empty and
`_cmd_run` refuses to dispatch. That is right for an unroutable task or a
dependency cycle. It is wrong for "you asked for three lanes and can use two",
which describes a plan that is correct and will run fine.

A third channel is cheaper than overloading either: `notes` renders under the
totals, changes no exit code, and is where any future observation of this kind
belongs.

## Resume, without touching the loop

`Scheduler.run` builds `RunState.for_tasks(spec, by_id)` and then enters a
dispatch loop that is 80 lines of interlocking local state — pending futures,
briefs, baselines, worktrees. Extracting a second entry point into that loop
would put the least testable part of the runtime at risk to serve the most
testable part.

Instead `run` takes `resume_from: RunState | None`. When given, it is used
instead of a fresh state; everything after is unchanged, including the spec
gate (re-checked, correctly — the repository may have moved) and the worktree
pool (re-opened).

`Scheduler.resume(state, tasks)` does the state surgery and delegates:

| Carried over | Reset |
|---|---|
| `ledger.spent` — money already gone still counts | non-terminal records → `PENDING` |
| `replans` — a resume is not a fresh replan budget | `human_gate`, `cancelled`, `cancel_reason` |
| events, and the succeeded records with their results | `finished_at`, stale `ledger.reserved` |

A task in the graph with no record gets one, so a graph that grew since the
run stopped is handled without a special case.

## Finding the graph again

`RunSpec` stores `graph_name` (the file stem) and `node_id` but not the path.
`vise runtime resume <run_id>` resolves the stem through the same workflow
scope search `graph_activate` uses, and `--graph` overrides it for a graph that
has since moved. A run whose graph cannot be found fails with that as the
reason rather than a traceback.
