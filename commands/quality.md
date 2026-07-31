---
description: Run the tiered quality gate — static → tests → security → integration, deep passes out of band
argument-hint: <what changed, or the scope to gate>
effort: high
---

Take this change through the vise `quality-gate` workflow.

1. Call `graph_activate` with `graph_name: "quality-gate"`.
2. Record the goal with `goal_set` — the scope being gated, and what "clean"
   means for it.
3. Before the first gate, look at `.vise/quality.yaml`. It declares this repo's
   commands once, keyed by check name, and it is the only thing standing
   between a real gate and a green one. If it is missing, write it — a `checks:`
   mapping from check name to argv list, e.g. `lint: ["ruff", "check", "."]` —
   naming only the tools this repo really has. Unnamed checks skip-pass and say
   so. A fuller per-ecosystem example ships at
   `<vise-bundled>/assets/quality.example.yaml`.
4. Walk the tiers in cost order. Static, tests, and security are seconds each
   and every one of them blocks; integration/e2e is the single slow node and it
   blocks once, late. Fix what a gate surfaces before you signal past it —
   nothing downstream re-runs it.
5. Read the evidence, not just the verdict. A check with no command bound
   skip-passes and records `source="asserted"`: the gate went green because
   nothing ran. Each of those is a gap to close — bind it, or name it in the
   summary. The terminal node is read-only by design; mutation and fuzz
   findings go into `experience_record` and become follow-up goals, never
   edits that no tier re-checks.

Scope: $ARGUMENTS
