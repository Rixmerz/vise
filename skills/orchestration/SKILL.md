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
   | establish what is true before building or deciding | `research` |
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
- **The agent runtime.** `vise runtime run` dispatches a `dag` node's tasks as
  their own sessions, which never traverse the graph and so never reach a node
  gate. That was a real hole and it is now closed by a second gate: the
  scheduler asks, once before its first dispatch, whether the project has a
  well-formed change to implement, and a run that fails it spends nothing.
  `vise runtime plan` shows the same verdict for free. See
  `docs/scheduler.md` § The spec gate.
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
| What a new or reshaped UI should look like — palette, type, layout | `vise:designer` |
| UI — components, pages, hooks, styling, accessibility | `vise:frontend` |
| Schema change, index, data backfill | `vise:db-migrator` |
| A bug — reproduce, attribute, smallest fix | `vise:debugger` |
| Unit / integration tests for landed code | `vise:tester` |
| README, changelog, API docs | `vise:docs-writer` |
| Establish facts with sources — prior art, an API's real behaviour, the case against a plan | `vise:researcher` (read-only) |
| Does finished work meet its acceptance criteria | `vise:verifier` (read-only) |
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
`security-baseline` (auditing, triaging scanner output, or a security-sensitive
diff) · `sql-rules` · `bash-rules` · `web-ui-rules` · and the twelve `<lang>-rules`
skills — `python` `typescript` `go` `rust` `java` `kotlin` `csharp` `ruby`
`php` `swift` `lua` `cpp`.

Nothing matches the language at all (Elixir, Zig, Nix, HCL) → say so in the
brief and in your report, and hold `general-purpose` to `engineering-baseline`
plus the project's existing files as the standard.

## Maximize parallelism

Measured across four real runs: the orchestrator is **60-65% of a run's total
spend**, not the subagents — one 3-node run cost $1.468 against $0.44 for every
subagent combined. Builders' work the engineer does itself is billed at the most
expensive seat in the pipeline, and pre-reading files a builder is about to read
buys that work twice at that rate.

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

## The brief — English, and everything the agent already has left out

Both directions of this channel are agent-to-agent, and both are **English**,
whatever language the session is being conducted in. Everything the subagent
loads the moment it starts is English — its charter, `engineering-baseline`,
its language rules skill — and so is every validator deny message and every
failing test you are required to quote verbatim below. A brief in another
language makes the agent translate your constraint into the language of its own
rules before it can apply it, and a constraint that comes through that trip
slightly changed still reads like a plausible instruction. You answer the
*user* in the user's language; that is a different channel.

Then the size. "Every prompt is self-contained" is in the hard rules below and
reads backwards easily: it means **the agent needs nothing out of your window**,
not *tell it everything*. It already holds its charter, its rules skills, and
the tools to open any file you name. The brief carries what it cannot get, and
nothing else:

- **Never restate a rule it preloads.** "Use type hints", "write tests",
  "follow AAA" — the specialist carries all of that already, so the words buy
  nothing. `general-purpose` is the exception, and it is handled above: it
  preloads nothing, so name the skills.
- **Never paste a file's contents.** `path/to/file.py:118-140` is the whole
  reference. Pasting the body buys that read twice — once at your rate, then
  again at the builder's when it opens the file anyway.
- **Never narrate.** No preamble, no retelling the user's request as a story,
  no closing pleasantries.

And where the cutting stops, because this is not a style exercise — a brief the
agent misreads costs a whole wasted wave, which is worth more than every word
it saved:

- The **acceptance criterion** stays exact: the command to run, and the result
  that counts as done.
- **Constraints and what not to touch stay whole sentences.** They are
  negations, and a negation with words missing reads as its opposite. "Don't
  change the signature, only add the parameter" does not survive being
  telegraphed.
- The **verbatim quote** in a re-brief stays verbatim.

Two obligations that come with citing instead of pasting, and with dispatching
at all:

- **`ls` every path the brief cites, in the environment the agent will run in.**
  A reference is only cheaper than a paste if it resolves. Working in a
  worktree it often does not — a file you wrote in the main checkout is not
  there — and an agent that cannot find a cited file stops without writing a
  line. That is the behaviour you want and it still costs the whole dispatch.
- **When the deliverable is the report rather than the file, say to write it to
  disk as it goes.** An agent that dies mid-task takes an unwritten finding
  with it, and you will not know which of them you lost.

Where a run's money actually goes is measured above: the orchestrator, at
60-65%. Trimming prose in the brief is not that lever. Not pre-reading the
files the builder is about to read is.

## Neighbours: three servers vise names and cannot call

Three MCP servers do things vise's gates cannot, and a session can hold all of
them. vise has no server-to-server channel, so none of this is something vise
does — it is what you put in a brief, because **a builder in a fresh context
window has no way to know these exist.**

| Server | Answers | Present when |
|---|---|---|
| `livespec` | what *could* run — the symbol graph | `.mcp-docs/docs.db` in the repo |
| `flowtrace` | what *did* run — a real execution | `.flowtrace/*.jsonl` in the repo |
| `layout-inspector` | how it *renders* — measured geometry | no repo footprint; check the tool surface |

One rule covers all three: **a brief naming tools the builder does not have is
worse than one that says nothing.** Check your own tool surface first. If a
server is absent, say so in the brief and hold the builder to the evidence it
does have.

Each ships its own skill and subagent that cover *how* to drive it. Your job is
*when*, and what to bring back.

### livespec — brief for the symbol layer

Read by symbol instead of by file and a builder gets the body it needs plus the
*signatures* of what that body calls and the *definitions* of the types in
those signatures, in one call. Measured on a real repo: **86% fewer tokens than
opening the files an honest reader would open**, median 497 tokens per unit.

**Every call takes `workspace`** — the absolute repo root, required, no
environment fallback. Put it in the brief; an example that omits it teaches a
call that raises.

- **`quick_orient(qname)`** for first contact — metadata, top callers, top
  callees, linked Specs, and `is_entry_point` so a symbol with no callers is
  not misread as dead. **`find_symbol(query)`** when the name is a guess.
- **`read_unit(qname)`** instead of reading the file. Say which symbols.
- **`search_similar(code)` before writing any new helper.** This is the one
  that pays for itself: the duplicate a builder is about to create has a
  *different name* — that is why it gets written — so neither grep nor the
  builder's memory of the repo will find it.
- **`resolve_location(path, line)`** when a stack trace or a failing test
  points at a line.
- **`git_diff_impact(base_ref, head_ref)`** for the verification wave. It is
  livespec's own CI entry point: changed files, the callers they reach, and
  **which test files are likely to break**. That last list is what turns "run
  the suite" into "run these first", and it is the single most useful thing
  livespec offers a reviewer.

Two things to pass on, because they change what the builder should trust:
`unresolved_types` in a closure is a **real gap** — a type the closure promised
and did not deliver, to be read before relying on its shape — while
`external_types` just means a dependency owns it. And if the repo runs the
CodeLayer gate in `enforce`, reads by path are denied outright; a brief that
tells a builder to "read `src/foo.py`" sends it into a wall.

The `codelayer` skill has the full picture, including when *not* to decouple.
It is not preloaded on any agent on purpose: it is dead weight in the repos
that do not have the index, and it loads on its own description when they do.

**If `graphify-out/graph.json` is also there**, a second extractor is in play
and it installs its own hook that pushes back on raw reads. The two do not
compete — Graphify orients at the scale of a subsystem, livespec commits at the
scale of an edit — so the ladder is one `graphify query` to orient, then the
symbol layer. Two cautions for the brief: `ingest_external_graph` may have
added edges livespec's resolver missed, so **a caller count from
`analyze_impact` can include a type used only in an annotation** (use
`who_calls` when the number decides something); and two extractors agreeing is
still static analysis, never evidence that a path runs.

### flowtrace — when the question is what actually happened

Reading the source stops helping when the call order is not what anyone
expected, a value is already wrong before it reaches the suspected code, or a
path nobody knew about was taken. `flowtrace run -- <command>` instruments
Java, Python, Node/TypeScript and Go **without touching the source** and writes
`.flowtrace/<timestamp>.jsonl`: paired enter/exit events with arguments,
results, durations and errors, under W3C trace ids that survive a hop between
processes.

Brief for it on a performance or integration wave, and on any debug wave where
a repro exists but the cause does not:

- **`log_open(path)`** first — every other call takes the session id it
  returns.
- **`trace_find_error()`** for a failure: the failing span and its ancestry.
  Read the arguments of each ancestor going down; the point where a value first
  becomes wrong is usually several frames above where the exception surfaced.
- **`log_aggregate(...)`** over `duration_ns` grouped by method for "why is
  this slow", then subtract child time from parent time before concluding.
- **`trace_tree(trace_id)`** to see what actually ran — the cheapest way to
  discover the code you were reading was never called.
- **`trace_diff(a, b)`** for a regression: capture the good revision and the
  bad, and read spans present in only one against duration deltas.

Four things to put in the brief, because each one turns a wrong conclusion into
a right one:

- **Scope to one `trace_id` first.** A server writes many interleaved
  executions into one file, and a conclusion drawn across them is worthless.
- **An absent method was not instrumented, not not-run.** Scoping to the
  package prefix is mandatory in practice, so framework and stdlib frames are
  missing by design — and an empty trace is almost always that prefix, not a
  program that did nothing.
- **A duration can exceed its parent's.** A span that starts async work and
  returns without awaiting it closes while the child runs; negative self-time
  means the parent did not do the work.
- **A trace holds arguments and return values.** Secrets are redacted by key
  name and `.flowtrace/` is gitignored by the CLI, but neither is a reason to
  paste one into a report.

### layout-inspector — the gate is not the diagnosis

vise ships three gates that drive a real browser: `ui_layout` (overflow,
clipping, collision, off-document, per breakpoint), `ui_contrast` (WCAG against
the *effective* background) and `design_tokens`. Unlike the repo checks, these
**fail closed** — a gate that cannot evaluate must never report success.

What they tell you is *whether*. They cannot tell you *why*, and a builder
handed "ui_layout failed: 40px overlap at 375" and nothing else will guess at a
cause, change a CSS rule, and re-run the gate to find out. That loop is
expensive and it is not the gate's job.

`layout-inspector` is the other half: same measurement approach, different
shape — tools an agent calls rather than a gate that blocks.

- **`check_environment()`** the moment any call fails to launch a browser,
  before concluding anything else. It reports whether a usable Chromium is
  there and the exact command to install it.
- **`detect_issues(url)`** to reproduce the finding as measured geometry,
  worst-first, with severity split by intent — two elements at `z-index: auto`
  are probably a bug, one with an explicit z-index is probably a deliberate
  overlay.
- **`element_context(url, selector)`** to root-cause *before* touching CSS. The
  stacking chain names the property that trapped a z-index; the clipping
  ancestors name the box that hides content. This is the call that turns the
  guess-and-re-run loop into one edit.
- **`compare_viewports(url)`** after the fix. It already diffs:
  `breakpoint_specific` is the responsive regression, `at_every_viewport` is a
  general defect. The repair that fixes 375px is the one most likely to break
  1280.
- **`accessibility_spatial(url)`** for target sizes and covered controls — WCAG
  2.2 SC 2.5.8. vise has **no** gate for this, so it is not a second opinion,
  it is the only one.

Two things a brief has to carry, or the builder loses a cycle to each:

**Read the `page` block before any finding.** A non-200 status or a page error
means the measurement may be of an error page. vise's own render gates do not
check this, so layout-inspector is where a 404 gets caught.

**The two vocabularies differ.** Translate, or a builder cannot reproduce what
the gate reported:

| vise gate says | ask layout-inspector for |
|---|---|
| `external_collision` | `overlap`, then `element_context` on either element |
| `container_overflow` | `clipped_by_ancestor` / `clipped_right` |
| `offpage` | `offscreen_horizontal`, and `horizontal_scroll` for the page-level cause |
| `unresolved_selector` | nothing — the selector matched nothing; that is vise's config, not the page |

**They do not share a browser.** Each runs Playwright in its own environment
and each Playwright demands its own Chromium revision, so this is two installs,
not one:

```
<vise venv>/python -m pip install 'vise[design]'
<vise venv>/python -m playwright install chromium     # for the gates
uv run --project <layout-inspector plugin root> playwright install chromium
```

Pointing both at one binary is possible — layout-inspector reads
`LAYOUT_INSPECTOR_CHROMIUM` and `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` — but
vise reads neither, so the gates use whatever their own Playwright resolves.
The render gates also need at least one `design.targets` entry in
`.vise/quality.yaml`; without it they fail closed rather than skipping, which
is deliberate and reads as a bug the first time.

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
