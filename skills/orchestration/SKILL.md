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
   | a multi-day slice, end to end | `sprint-e2e` |

   `graph_list_available` has the full list with descriptions when none of
   these obviously fits.
3. **Say which one you activated and why, in one line.** A workflow blocks
   tools; the user must never discover it by hitting a wall.
4. Nothing fits, or the task is a one-off? Say so in one line and orchestrate
   without one. A wrong activation costs more than no activation.

## Step 0.5 — the spec phase is mandatory, and you cannot talk your way past it

`feature-dev` and `migration` both carry a `spec` node between design and
implement. Its exit edge is `validators_green`, so unlike a phrase edge there
is no sentence you can say to open it — the gate reads `openspec/` off disk and
opens only when a well-formed change proposal is actually there.

What "well-formed" means, concretely, because this is where it goes red:

- `openspec/changes/<name>/proposal.md` exists
- `openspec/changes/<name>/specs/<capability>/spec.md` carries a delta header
  (`## ADDED Requirements`, `## MODIFIED Requirements`, `## REMOVED Requirements`)
- **every `### Requirement:` has at least one `#### Scenario:`** — the single
  most common failure; a requirement with no scenario is rejected outright
- `tasks.md` is the real checklist, because `validate` (feature-dev) and
  `bench` (migration) will not open until every box in it is ticked

Get there with `openspec new change <name>`, then `openspec status --change
<name> --json` for the artifact order. The bundled `/opsx:propose` skill drives
the whole sequence if you prefer.

Three things that are *not* escape hatches:

- **Delegating.** A subagent hits the same gate — it is a node gate, not a
  prompt. Dispatching a builder from `spec` does not move the workflow.
- **`VISE_NODE_GATE_OVERRIDE=1`.** It bypasses the block and records the
  attempt. Using it because the proposal is unwritten is the habit the gate
  exists to prevent; using it because the *gate* is wrong is a bug report.
- **Skipping the workflow.** If the work genuinely doesn't change the system's
  contract, don't activate `feature-dev` for it — say so in one line and
  orchestrate bare. A wrong activation costs more than no activation.

Bug fixes are the honest exception: `debug` has no spec phase on purpose. A fix
that restores specified behaviour changes no contract, and forcing a proposal
for it would be ceremony. A fix that *changes* behaviour is a feature — use
`feature-dev`.

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
| Server-side Swift / Lua (Vapor, OpenResty, engine glue) | `vise:backend-swift` · `vise:backend-lua` |
| UI — components, pages, hooks, styling, accessibility | `vise:frontend` |
| Schema change, index, data backfill | `vise:db-migrator` |
| A bug — reproduce, attribute, smallest fix | `vise:debugger` |
| Unit / integration tests for landed code | `vise:tester` |
| README, changelog, API docs | `vise:docs-writer` |
| Adversarial review before commit/merge | `vise:reviewer` (read-only) |
| Security surface (auth, input, secrets) | `vise:security-auditor` (read-only) |

Match the backend agent to the file's language, not the task's vibe.
Design/naming/tradeoffs are never in this table — those stay with the engineer.

### When no specialist fits

Infra, config, CI, Dockerfiles, Terraform, glue → `general-purpose`. That agent
**preloads nothing**: no `engineering-baseline`, no language rules, no
`ponytail`. A brief that does not say so gets an agent working without any of
vise's conventions.

So when you dispatch `general-purpose` to touch code, name the skills in the
brief: *"Load the `engineering-baseline` and `bash-rules` skills before your
first edit."* The rules that ship and apply:

`engineering-baseline` (always) · `ponytail` (always, when writing) ·
`sql-rules` · `bash-rules` · `web-ui-rules` · and the twelve `<lang>-rules`
skills — `python` `typescript` `go` `rust` `java` `kotlin` `csharp` `ruby`
`php` `swift` `lua` `cpp`.

Nothing matches the language at all (Elixir, Zig, Nix, HCL) → say so in the
brief and in your report, and hold `general-purpose` to `engineering-baseline`
plus the project's existing files as the standard.

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

## Resolve the caller set before you dispatch the wave

If a wave will change the parameters or return type of a symbol used outside
its own file, resolve that symbol's references **before** dispatching: the
native `LSP` tool's `findReferences` and `incomingCalls` (plus
`goToImplementation` for an interface method) take `filePath`, `line`,
`character` and return the actual call sites. Enumerate the result in **every**
brief in that wave.

The builders have `LSP` too, for what a brief could not anticipate. That is not
a reason to leave this to them: resolving the caller set once beats each builder
re-deriving it from its own fresh window and disagreeing about the answer — and
the caller set is the input to the file-ownership partition in the hard rules
below. You cannot say who owns which file until you know which files the
signature reaches, so this runs before the wave is even shaped.

`LSP` is navigation only. It has no diagnostics operation — it answers "what
touches this", never "is this broken". Error checking is the validator's job.

**No language server configured for that language?** Fall back to text search,
and say in the brief that the caller list is unverified and may be incomplete.
A missing server never blocks the dispatch — it downgrades the evidence.

A wave that only adds new code, touching no existing signature, needs none of
this. Skip it and dispatch.

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
