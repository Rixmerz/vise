---
name: orchestration
description: One engineer, many builders — wave-based parallel delegation to subagents. Use when a task spans multiple domains (backend + frontend + db), has independent streams that could run in parallel, or needs 3+ sequential phases of mechanical work. Also use proactively when about to grind through 5+ file edits yourself that a well-briefed subagent could execute.
---

# orchestration

The main agent is the **engineer**: it holds intent, architecture, and user
context. Subagents are **builders**: fresh windows that execute mechanical
work from a self-contained brief. Never delegate thinking — architecture,
naming, tradeoff analysis stay with the engineer. Aggressively delegate
execution — grepping, multi-file edits, test writing, scans.

## The fleet — dispatch by name, not `general-purpose`

vise ships specialist agents. Delegate to the one that matches the work — a
named specialist carries its own coding rules and effort tuning; a generic
agent carries nothing. Pass its name as `subagent_type`.

| Work | Agent |
|------|-------|
| Server-side Python / Go / Rust / TypeScript | `vise:backend-python` · `vise:backend-go` · `vise:backend-rust` · `vise:backend-typescript` |
| Server-side Java / C# / Kotlin / Ruby / PHP / C·C++ | `vise:backend-java` · `vise:backend-csharp` · `vise:backend-kotlin` · `vise:backend-ruby` · `vise:backend-php` · `vise:backend-cpp` |
| UI — components, pages, hooks, styling | `vise:frontend` |
| Schema change, index, data backfill | `vise:db-migrator` |
| A bug — reproduce, attribute, smallest fix | `vise:debugger` |
| Unit / integration tests for landed code | `vise:tester` |
| README, changelog, API docs | `vise:docs-writer` |
| Adversarial review before commit/merge | `vise:reviewer` (read-only) |
| Security surface (auth, input, secrets) | `vise:security-auditor` (read-only) |

Match the backend agent to the file's language, not the task's vibe. No
specialist fits (infra, config, glue) → `general-purpose`. Design/naming/
tradeoffs are never in this table — those stay with the engineer.

## Maximize parallelism

- Independent tasks → multiple Agent calls in **one message**. Frontend +
  backend + db migration run simultaneously, not sequentially.
- Group work into **waves by dependency**, barrier only between dependent
  waves:
  1. Domain / foundation — types, models, schema. No dependencies.
  2. Backend — handlers, endpoints, wiring. Depends on domain.
  3. Frontend — components, hooks, pages. Depends on backend API.
  4. Tests — unit + integration. Depends on implementation.
  5. Validate — build, test suite, review. Depends on everything.
- Within a wave, everything runs concurrently. Do not serialize work that
  shares no files and no data dependency.

## Hard rules

- **Never two agents writing the same file in one wave.** Partition scope
  by file ownership before dispatching.
- **Every prompt is self-contained.** The builder has a fresh window: give
  exact file paths, reference files for patterns, acceptance criteria
  (what "done" looks like), constraints, and what NOT to touch.
- **A subagent's "done" is a hypothesis.** After each wave, verify the
  actual diff (`git status` / `git diff`) and run the smallest possible
  check before advancing.
- **On failure, re-brief with the specific failure** — quote the failing
  test or error verbatim. Never re-loop the same prompt.

## Budgets

- Max 3 dispatches of the same specialist per task without changing scope.
  A third identical dispatch is a plateau signal — escalate or do it
  directly.
- Two consecutive waves with no new signal → stop and report what is
  blocked instead of spawning a third.

## When NOT to parallelize

- Tightly-coupled edits where each change informs the next.
- Tiny tasks (<3 tool calls) — briefing overhead dominates.
- The user is iterating turn-by-turn, correcting course.
- Judgment work: design, naming, tradeoffs. Do it yourself.
