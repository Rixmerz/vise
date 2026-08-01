---
name: orchestration
description: The single entry point for substantial work — picks and activates the matching phase workflow, then runs wave-based parallel delegation to subagents inside it. Use when a task spans multiple domains (backend + frontend + db), has independent streams that could run in parallel, or needs 3+ sequential phases of mechanical work. Also use proactively when about to grind through 5+ file edits yourself that a well-briefed subagent could execute.
---

# orchestration

The main agent is the **engineer**: it holds intent, architecture, and user
context. Subagents are **builders**: fresh windows that execute mechanical
work from a self-contained brief. Never delegate thinking — architecture,
naming, tradeoff analysis stay with the engineer. Aggressively delegate
execution — grepping, multi-file edits, test writing, scans.

## Step 0 — is there a workflow for this?

Do this before dispatching anything. Delegation says *who does the work*; a
workflow says *what has to be true before the work is allowed to advance*.
They are different axes, and this skill used to ignore the second one
entirely — so orchestrated work skipped every phase gate on the repo.

1. Call `graph_status`. If a workflow is already active, **do not activate
   another** — read the current node's `tools_blocked` and plan around it
   (see the conflict rule below). Skip to the fleet table.
2. No workflow active, and the request is multi-step? Match it and activate:

   | The request is… | `graph_activate(graph_name=…)` |
   |---|---|
   | build/add/implement something new | `feature-dev` |
   | something is broken, failing, wrong | `debug` |
   | check quality, audit, harden | `quality-gate` |
   | review a PR / a branch | `pr-review` |
   | schema, index, data backfill | `migration` |
   | cut a release | `release` |
   | security surface | `security-audit` |

   `graph_list_available` has the full list with descriptions when none of
   these obviously fits.
3. **Say which one you activated and why, in one line.** A workflow blocks
   tools; the user must never discover it by hitting a wall.
4. Nothing fits, or the task is a one-off? Say so in one line and orchestrate
   without one. A wrong activation costs more than no activation.

### The conflict rule — this is not optional

A node's `tools_blocked` applies to subagents too. This is verified, not
assumed: with `feature-dev` on `orient`, a `general-purpose` subagent asked to
Edit a file was denied by the same PreToolUse hook that denies the main agent.
Delegation is **not** an escape hatch from a phase gate, and must never be
used as one.

Two consequences:

- **`debug-graph` blocks `Task` on every node except `fix`.** Under that
  workflow you cannot dispatch at all until you reach the fix phase. That is
  deliberate — evidence-gathering is the engineer's job — so do the reproduce
  and analyze phases yourself and delegate only once you are on `fix`.
- **Read-only phases (`orient`, `design`, `fetch`) block Edit/Write.** A
  builder dispatched there fails on its first edit. Delegate reading and
  searching in those phases; save the writing waves for the phase that allows
  writing.

If a gate is genuinely wrong for the task, `graph_deactivate` and say why —
do not route around it with a subagent.

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
