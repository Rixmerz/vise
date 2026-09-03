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
