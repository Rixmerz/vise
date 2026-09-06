# Design

## The shape

```
implement ──► test ──► decouple ──► validate
                │          ▲   │
                │          │   └── tests_pass, diff_scope  (exit)
                └──────────┘      tests_pass                (entry)
```

`decouple` is a phase, not a validator. It has the same three properties every
vise phase has: a prompt that says what the phase is for, a tool surface, and
gates on both edges that are code somebody reviewed.

## Entry

`tests_pass` green. Nothing else. A phase that starts on failing tests has no
oracle and would be moving code it cannot prove it understands.

## What the phase does

1. **Look.** `compute_index_status`; if livespec is not mounted or the index is
   stale, emit `decouple_skipped` with the reason and exit. No guessing.
2. **Find the candidates.** For every unit the diff added or grew:
   `search_similar` on its body (the duplicate about to be created has a
   different name — that is why it got written), and `analyze_impact` on any
   signature the diff changed.
3. **Refuse first.** Apply the `codelayer` skill's refusal list before
   proposing anything: under the size floor, fewer than three consumers,
   a migration, a test, generated code. Every refusal is written down with
   its rule; a refusal is a finding.
4. **Propose, then move.** One builder task per accepted candidate, briefed
   with the unit, its closure and the target. Ownership is the files the
   closure touches and nothing wider — `diff_scope` holds it there.
5. **Prove.** `tests_pass` again. A move that turns a test red is reverted by
   the builder, not argued with.

## Exit

`tests_pass` and `diff_scope`. Both exist. Both are `source="mechanical"`.

## What it reports

`decouple_report`: candidates found, refused (with the rule), moved, reverted,
and cost. The report is the thing a person reads to decide whether the phase
earned its place — the same evidence `VISE_CODELAYER=warn` produces for the
gate it precedes.

## Why not a validator

A `no_duplication` validator was the first idea and is the wrong one. A gate
that fails on structure it cannot fix teaches the agent to route around it —
the exact failure `codelayer_gate` documents for reads. The phase fixes what it
finds or explains why it did not; the gate afterwards checks behaviour, which
is the only thing a gate should check.

## Why not during generation

Because during generation there is no oracle. The generator decides a boundary
with the least information it will ever have and no way to test the decision.
After generation there are passing tests and a finished artifact. Every
argument in `codelayer`'s "When NOT to decouple" is an argument that the
decision should wait for evidence; this phase is the earliest point at which
the evidence exists.

## Correction: steps 1 and 2 are not vise's to run

Written before section 1 was built, this design read as though vise performed
every step. It cannot perform the first two. `compute_index_status`,
`search_similar` and `analyze_impact` are livespec's, and livespec is a
separate MCP server — `src/vise/core/livespec.py` exists precisely because vise
names those tools in twenty-odd places and can check none of them. vise's MCP
server runs beside livespec's in a session, not above it; nothing in this
repository can call them.

So the phase splits where the capability does:

- **Looking is the agent's.** The phase prompt tells the agent which livespec
  calls to make and what to bring back. That is a prompt, and a prompt is
  weighed, not executed.
- **Judging is vise's.** `vise.runtime.decouple` takes candidates in the shape
  it asks for and returns what may move, what may not, and under which rule.
  That is code somebody reviewed, and it is the half worth having deterministic:
  a refusal list an agent may re-weigh is a refusal list that gets re-weighed
  in favour of moving.

This keeps the rule the proposal's open question was worried about — "vise
recomposes, never authors a gate" — and answers it. The transform lives in the
session; the judgment and the record live here. A `candidates(diff, index)`
function of the kind task 1.1 named would have had to reach the index to mean
anything, so it is not written and 1.1 now names what replaced it.

The cost of the split is a seam vise cannot test end to end: if livespec
renames a field, the agent fills a `Candidate` wrong and the refusal is wrong
with it. That is the same exposure `livespec.py` already documents for names,
and it is bounded the same way — every field `Candidate` asks for is named in
one place, and a caller that cannot fill one truthfully leaves it at its
default, which refuses.

## Correction: two of the three calls were not livespec's either

Checked against livespec 0.33 on 2026-09-06, and the design above is wrong in
the same way the shipped assets were.

**`compute_index_status` is not a livespec tool.** It was removed as one in
livespec v0.9 and survives as a module-level helper behind the
`project://index/status` resource. Step 1 of "What the phase does", the
`survey` prompt built from it, and the scenario in the delta spec all named a
call that fails — so a phase whose entire premise is "look before proposing"
opened by looking with something that does not exist.

**`analyze_impact` is the wrong call for `consumers`.** It counts every
dependency. After an `ingest_external_graph` of a Graphify graph that includes
a type used only in a parameter annotation, and livespec measured what that
does: one class went from 2 callers to 40, of which 38 were annotations. The
rule of three is about call sites, so the count comes from `who_calls`, whose
default edge set is invocation only.

What replaces step 1 is not another call. `vise/core/neighbour_state.py` reads
`.mcp-docs/docs.db` directly — livespec leaves a file, and a file is not a tool
call — so "is there an index" is answered in code by the `symbol_index`
validator on the `move` node. The prompt still tells the agent to stop and say
"no index", because the `survey → report` edge is how a run ends cleanly; the
difference is that the *writing* node no longer depends on the agent having
obeyed. A refusal in prose is advice to the party being refused.

Third correction, smaller: ingested edges outlive the index they were matched
against, and livespec reports that as `stale` in the `external_edges` block of
every graph-reading tool. Nothing read it. The survey now refuses on it, since
a consumer count taken from a graph that no longer matches the tree is a count
about code that has moved.
