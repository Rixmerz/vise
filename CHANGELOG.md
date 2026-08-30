# Changelog

Notable changes per release. Starts at `0.1.0a11`; earlier alphas predate this
file and are described only by their commits.

Alpha means the tool surface is still moving. Where a change alters behaviour
you may already depend on, it says so under **Behaviour change**.

## [Unreleased]

### Added

- **An agent execution plane — specified, contracted, and planning; not yet
  dispatching.** vise decided *what process* a change follows and never *who
  does the work*: one session walked every phase serially, at one effort, with
  one context that grew until it was compacted. `src/vise/runtime/` is the
  plane that answers the second question, and `docs/agent-runtime.md`,
  `docs/scheduler.md`, `docs/model-routing.md` and `docs/worker-contract.md`
  specify the whole of it, including the parts this release does not implement.

  **There is no second workflow engine, deliberately.** `Node.node_type ==
  "dag"` already held tasks with dependencies, and `compute_ready_tasks`
  already computed which were unblocked. A parallel task graph with its own ids
  and its own edges would be a second engine with the same job, and the two
  would disagree within a release. So the runtime metadata lands as optional
  fields on the `Task` that exists — `role`, `ownership`, `criticality`,
  `complexity`, `writes`, `model`, `effort`, `acceptance`, `max_cost`,
  `max_turns`, `timeout_s` — each defaulting to today's behaviour. The nine
  bundled workflows declare none of them and parse byte-identically.

  Eight modules, all offline and deterministic: `contracts` (the data),
  `registry` (reads the 20 shipped charters rather than restating them),
  `routing` (model and effort, with the argument attached), `ownership` (which
  tasks may run together), `budget` (when the run stops), `artifacts` (what one
  worker hands the next), `honesty` (the four gates a claimed pass must
  survive), `planner` (waves, admission, cost).

- **`vise runtime plan` and `vise runtime agents`.** Read-only, offline. `plan`
  derives a DAG node's waves, resolves each task to an agent, routes a model
  with its reasons, and prices the run before anything starts — exiting non-zero
  when the plan has problems, so an unroutable task cannot be scripted past.
  `agents` lists what the registry can route to and names the roles that are
  ambiguous.

- **Four honesty gates, three of them ported from mini-vise.** A worker's
  `pass` is a claim, not a result. A testing role must quote a command and its
  real output; an implementing role must quote the repo's existing checks; a
  pass claiming edits must move `git status --porcelain` plus `HEAD`; and a
  pass may not have written outside its declared ownership. These fail closed —
  the inverse of vise's hook contract — with one precise exception: an
  *unknowable* tree hash produces no finding, because a gate that cannot compute
  its input and reports a violation anyway commits the error it exists to catch.

  The tree-hash rule is the only mechanical honesty check in either codebase.
  Every other one asks another model, which means every other one can be talked
  out of its finding.

- **The scheduler, and everything that makes its verdicts mean something.**
  `runtime/scheduler.py` walks a DAG node's tasks on a thread pool: dispatch
  what is ready and admissible, collect, gate, and decide retry / escalate /
  replan / stop. Around it: `state` (the inert record, persisted per run),
  `recovery` (the pure decision function), `context` (what a worker is shown —
  and, more importantly, what it is not), `verify` (the second opinion),
  `adapters/claude_code` (the only module that can spend money).

  `SUCCEEDED` now requires a second agent. Every task declaring acceptance
  criteria is checked by `vise:verifier`, given the criteria, the diff and the
  evidence — and deliberately not the implementer's prompt or summary, which are
  the two artefacts a wrong-but-confident worker produces most convincingly. A
  verifier that says *inconclusive* blocks the task rather than retrying it:
  re-running the implementer cannot fix a verifier that would not run.

- **`vise runtime run|status|explain|budget|cancel`**, and eight MCP tools
  (`agent_list`, `run_plan`, `run_list`, `run_status`, `task_list`,
  `run_explain`, `run_budget`, `run_cancel`). `run` prints the plan and its cost
  and stops there unless `--yes` is given. `cancel` writes a sentinel the loop
  polls, because the person cancelling is usually at another terminal.

  **There is no `run_start` MCP tool, deliberately.** This server runs inside a
  Claude Code session; a dispatch tool here would have the session spawning
  sessions through the one component that cannot call another server's tools.
  Same boundary `recipe_run` holds.

- **Stress, real concurrency, and failure injection.** Forty-task pseudo-random
  DAGs, a third of attempts failing, workers that always raise. The concurrency
  test detects genuine temporal overlap rather than dispatch order — and asserts
  the opposite too, since a scheduler that never overlaps anything passes every
  ownership test and is worth nothing.

- **Coverage floor raised 71 → 74 (CI 62 → 70).** Measured 75%. Every new
  runtime module is 89–100%.

- **`--isolate`: one git worktree per task.** Each writing task runs in its own
  worktree branched from HEAD, is verified there, and is integrated into the
  main tree only once it has passed. Integration is a three-way apply; a
  conflict blocks the task and names it rather than picking a side, and a
  refused apply is backed out to exactly the paths it touched so the main tree
  is never left half-patched. Off by default, and degrades to the shared tree
  with the reason on the record where it cannot run.

  This is what removes the attribution problem instead of bounding it. In one
  shared tree a git diff cannot say whose file is whose, so the ownership gate
  has to excuse paths a concurrent peer was entitled to write.

- **Three passes above the worker.** A **debugger** classifies a failure that
  named no kind, but only after the worker's own answer and a text heuristic
  have both declined — most failures name themselves and a model call to confirm
  is waste. An opt-in **adversarial review** runs once over the whole node after
  everything succeeds and parks the run if it objects, because deciding what to
  do about a shipping objection is a person's call. And under isolation a failed
  attempt's worktree is **discarded**, so the next attempt starts from HEAD
  rather than from its own failed output.

  Reassign is deliberately absent, and the reason is in `docs/scheduler.md`: the
  registry resolves a role to exactly one agent and reports ambiguity rather
  than breaking it alphabetically, so "try a different agent" would mean picking
  the one it already refused to pick by coincidence.

- **`requires_human: true` on a task** parks the run before it starts, checked
  before the budget — the point of the flag is that the work should not begin,
  and finding out only because the money ran out would be an accident.

- **The verifier reports no confidence number, deliberately.** A model's stated
  confidence is not evidence about that model's output, and a runtime that
  routes on it has made itself a consumer of exactly the signal it exists to
  check. `unmet` replaces it: which criteria were not met, named individually,
  so a reader can disagree with the claim rather than with a decimal.

### Fixed

- **The model policy is a table of defaults per kind of work, and the charter is
  a default rather than a floor.** Both were wrong in the first implementation
  and the two errors compounded.

  Each role mapped to a ladder *index*, so any default that is not a rung —
  documentation at `haiku/medium` — was inexpressible and got silently rewritten
  to the nearest rung. And an agent charter's `model` was treated as a floor the
  policy could not go below, so `docs-writer` declaring `sonnet` overruled
  documentation's `haiku`. Between them, the entire cheapest tier was
  unreachable through any bundled agent — a symptom this changelog previously
  reported as a property of the charters, when it was a bug in the router.

  Precedence is now: a model the task pins, then the policy for the kind of
  work, then the charter's own model for a role the policy does not cover. The
  table is asserted row by row, and a test pins that a default which is not a
  ladder rung survives routing intact.

### Added

- **The spec gate reaches the execution plane.** `mandatory-openspec-gate` made
  vise's spec phase impossible to talk past, and `skills/orchestration/SKILL.md`
  named delegation as the first thing that is not an escape hatch: a subagent
  hits the same gate, because it is a node gate rather than a prompt.

  The agent runtime broke that sentence. A `dag` node's tasks are dispatched by
  the scheduler as their own sessions; they never traverse the graph, so they
  reach no node gate at all — `grep -ri openspec src/vise/runtime/ docs/`
  returned nothing. A six-task run against a scratch repo wrote a whole Python
  package with no `openspec/` root anywhere and nothing to stop it. The gate
  vise ships had a side door, and it was the door vise built.

  `runtime/spec_gate.py` closes it. Asked once, before the first dispatch,
  whenever the run contains a task that writes: is there an active change with
  a proposal and well-formed spec deltas? A blocked run creates no worktrees
  and its recorded cost is zero. `vise runtime plan` reports the same verdict,
  so the block costs nothing to discover. `--change <name>` pins which change a
  run implements.

  Deliberately **not** gated on `tasks_complete` — that is the bar the node gate
  uses on the edge into the irreversible phase, where the work is done; here the
  run is what does it, and requiring a ticked checklist first would gate work on
  its own output. A run where every task declares `writes: false` is not gated
  at all: it cannot change the system's contract, so there is nothing for a
  specification to describe.

  One bypass, and it is the one that already exists: `VISE_NODE_GATE_OVERRIDE=1`.
  There is no `--no-spec-gate` flag, because a bypass that costs one keystroke
  and leaves no trace is not a gate, it is a default — and an overridden run
  records `spec_gate_overridden` rather than reporting itself as passed.

### Fixed — `./install.sh` never actually updated an installed plugin

Found while re-testing: the cached plugin under `~/.claude/plugins/cache/` was
still the copy from the first install, hours and six commits earlier. Every
session was reading that — the skills, the agents, the `lspServers` map — while
`vise doctor`, which runs from the venv's editable install, reported the working
tree. The two disagreeing is exactly how a fixed bug looks unfixed.

This is the second time the promise broke, and both times the same way. The
first fix replaced a bare "already installed" that did no work with a
`claude plugin update` call. But `update` compares the **version** in
`plugin.json` and short-circuits when it matches — `already at the latest
version (0.1.0a20)` — and a dev checkout pulls a hundred changes between version
bumps. The idempotence promise held while the update path silently did nothing,
one layer down from where it did nothing before.

The installed branch now uninstalls, clears the cache directory, and installs
again, which is the only refresh a local marketplace at an unchanged version
responds to. Safe here because vise declares no `userConfig`, so an uninstall
drops no stored option values. Verified by staling the cache on purpose and
watching a plain `./install.sh` heal it.

### Added — guidance on *when* to use a language server, where the decision happens

Fixing the servers made them work. It did not make anything use them, which was
the actual complaint: no mode of Claude reaches for `LSP` on its own.

The reason is not subtle. An agent about to change code reaches for `Grep`,
because grep is the habit and it always returns something. Handing every agent
the tool does not compete with that; naming the moment does. And the moment a
`*-rules` skill loads *is* the moment — those are keyed to the extension under
edit.

This README claimed the work was already done — *"each `*-rules` skill states
the circumstance that requires a lookup"*. Zero of the fifteen mentioned `LSP`.
All the guidance in the product was one line in `engineering-baseline` and three
in `orchestration`, both about the same narrow case.

- `engineering-baseline` now carries the language-agnostic decision once: a
  table of four questions — who calls this, where does this come from, what
  implements this, what does this file contain — each against **the search you
  would otherwise run**, because "use the language server" loses to habit while
  "`findReferences`, not grep, and here is what grep misses" does not. With both
  limits stated, since a rule with no limit gets applied where it does not hold
  and then distrusted everywhere: dynamic dispatch is invisible to every
  language server, and no server installed means no answer rather than an empty
  caller list.

- The twelve rules skills whose languages have a declared server each state what
  grep gets wrong *in that language*: `__init__.py` re-exports and decorator
  renaming in Python, barrel files and aliased imports in TypeScript, implicit
  interface satisfaction in Go, blanket impls and macro expansion in Rust,
  partial classes and explicit interface implementations in C#, traits in PHP,
  extension functions in Kotlin, protocol conformance declared in another file
  in Swift, header/definition split and preprocessor rewriting in C++. Ruby and
  Lua say the opposite where it is true: metaprogramming makes a
  `findReferences` result a floor on the caller set, not the caller set.

- `bash-rules`, `sql-rules` and `web-ui-rules` deliberately say nothing. No
  declared server covers `.sh`, `.sql`, `.css` or `.html`, and advice that
  cannot work is worse than none — it spends a call and teaches the agent the
  tool is useless.

`test_lsp_guidance_sync.py` pins both halves against `plugin.json` rather than
against a list: adding a server for shell or SQL fails the suite until that
skill gains its section. It also checks that every LSP operation named in any
asset is one the tool actually has, the same rule `test_asset_honesty.py`
applies to vise's own tools.

### Fixed — the LSP servers were declared, not usable

Reported as "the LSPs we added can't actually be used". They could not, and
`vise doctor` said they were fine. Four defects, each checked against Claude
Code's own plugin loader rather than against belief about it.

- **`jdtls` and `kotlin-lsp` were dead on arrival.** Both declared
  `startupTimeout: 120000`. The loader answers that with

      LSP server 'jdtls': startupTimeout is not yet implemented.
      Remove this field from the configuration.

  and it throws *before* the server is registered, so neither could ever start
  however well its binary was installed. Two of the twelve were unusable by
  construction. The same holds for `shutdownTimeout` and `restartOnCrash`: the
  schema accepts all three and the loader refuses all three.

- **`vise doctor` reported presence as health.** Its check was `shutil.which`,
  which answers "a file with that name is on PATH" — a different question. On a
  machine where `rustup` has installed a `rust-analyzer` shim without the
  component, that file exists and exits with *"Unknown binary 'rust-analyzer' in
  official toolchain"* the moment anything runs it. Doctor printed `[OK]`. It
  now starts each server the way Claude Code does — the declared command with
  the declared args — and passes it only if it is still alive a moment later,
  which is a language server waiting for a request. A server that exits is
  reported `[ON PATH, UNVERIFIED]` with its own first line of output, never as
  broken: a check that cries wolf on a working install teaches people to ignore
  it.

  `--version` was the obvious probe and is the wrong one — `pyright-langserver`
  does not implement it and exits complaining that no transport was selected,
  so a healthy server reported as suspect.

- **`clangd` mapped `.C` and `.H`, which cannot mean anything.** Claude Code
  lowercases every `extensionToLanguage` key before building its map, so those
  collapsed onto `.c` and `.h` — already mapped to `c` in the same entry. The
  intent (treat `.C`/`.H` as C++) was unreachable and the collision invisible.

- **`install.sh` documented a `strict: false` that does not exist.** The
  server schema is a *strict* object with no such key, so the reassurance users
  were given for ten missing servers was not backed by anything in the
  manifest. The installer also kept its own twelve-entry copy of the hint table
  and its own `command -v` check — a second place to drift, and the one that
  printed the green tick for the dead `rust-analyzer`. It now calls
  `vise doctor` and prints what that found.

`test_plugin_lsp_manifest.py` pins all of it: no unimplemented field, no field
outside the schema, no uppercase extension key, no extension claimed by two
servers, and an install hint for every server declared.

### Fixed — the two findings that first shipped as known holes

Both were reported rather than fixed in the first pass because each looked like
it needed a decision. Neither did.

- **Under `--isolate`, a task could not see its dependencies' output.**
  Integration applies a verified worktree's diff to the main *working tree*
  without committing, so `HEAD` never moves — and a worktree branches from a
  commit. Every task therefore started without a single thing the run had
  produced, and a DAG with real dependencies was unbuildable under isolation. A
  six-task build showed it exactly: `cli-python` was correctly refused by its
  verifier with `ModuleNotFoundError`, because `money.py`, `parser.py` and
  `report.py` did not exist in its tree.

  A worktree is now seeded with every patch the run has already integrated, and
  that seed is committed **on the throwaway `vise/run/<id>/<task>` branch**. So
  no decision about writing into the user's history was needed after all: a
  test pins that the main branch's `HEAD` does not move. The commit is not
  cosmetic — left uncommitted, the seeded files would appear in `git diff HEAD`
  as the later task's own writes, be re-integrated under its name, and be
  refused by the ownership gate for writing outside what it declared.

  `acquire`'s docstring argued the opposite: *"tasks are independent by
  construction, and chaining them would make the order they happened to start
  in part of the result."* True of independent tasks, false of tasks that
  declare `dependencies` — and replaying the integrated set is not chaining,
  because that set is by construction what had finished and verified before
  this task began.

- **A worker that ran the code it just wrote was refused for the exhaust.**
  Proving a `pass` means executing something, and a Python worker that does
  leaves `__pycache__/*.pyc` beside its module. `git status --porcelain -uall`
  reports those, the ownership gate reads them as writing outside the task's
  declared paths, and the attempt is refused — classified `environment_bug` and
  retried, so it recovers, at the cost of an attempt plus a debugger call every
  time the model does not think to tidy up.

  The worker's environment now carries `PYTHONDONTWRITEBYTECODE=1`. Not
  creating the byproduct, rather than teaching the gate to forgive a list of
  paths: a forgiveness list grows with every ecosystem's build detritus, and
  each entry is somewhere a real escape can hide.

  Verified together on one `--isolate` run of the same six-task DAG: 6 dispatched,
  6 verified, 6 integrated, **one attempt each**, no fallback to the shared tree,
  no `__pycache__` anywhere in the tree.

### Fixed — found by installing it and running a real build

Four bugs the test suite could not have found, because each needed a real
worker, a real repo, and a person watching. Building a small Python CLI from a
six-task DAG in a scratch repo surfaced all four.

- **Notes filed as a `verification` artifact shadowed the verdict, and stalled
  the run.** `parse_verification` preferred an artifact of kind `verification`
  over the result's own verdict, and read "no `verdict` key in its payload" as
  "unreadable". But `verification` is one of the artifact kinds
  `RESULT_INSTRUCTIONS` offers every worker, so a verifier that files a criteria
  table or a test list under it is following the contract. The live `pass` it
  had already given was discarded, the task was blocked as *inconclusive*, and
  the five tasks behind it stalled — after which isolation cleanup deleted the
  correct, evidence-backed code the worker had written.

  An artifact whose `verdict` field cannot be read still stays inconclusive and
  still never falls through: that is a verifier that tried to answer and failed,
  and the honesty rule holds. An artifact with no `verdict` field at all is
  notes, and notes no longer outrank an answer.

- **The run state file was written on the first collection, not the first
  dispatch.** For the whole of the first task — the one moment someone who has
  just started a run looks at it — `vise runtime status` answered "no such run",
  and a process killed in that window left nothing behind, against the promise
  in `state.py`'s own docstring that a dead run says which tasks were in flight.

- **`vise runtime budget` printed its "per task:" heading over nothing.**
  `RunState.load` restored the run's total spend and its worker count but not
  `by_task`. The command reads only persisted state, so the one question it
  exists to answer — what did each task cost — had no answer for any run.

- **The capability tokeniser split on a hand-written list of separators.** A
  task named `Money value type (python)` tokenised to `(python)`, matched no
  capability, and came back `UNROUTABLE` with an error telling the author to
  name the capability in the task — which they had. It now splits on any run of
  non-alphanumeric characters, and the tokeniser lives in `registry.py` instead
  of in two identical private copies in `planner.py` and `scheduler.py`.

### Notes

- **Four bugs found by writing the tests, each fixed with the test that caught
  it.** Admission counted settled spend only, so four opus tasks could start
  against a budget for one — nothing was billed yet, so everything fit; the
  ledger now reserves an estimate while a task is in flight. `at_top_rung` read
  the route computed *after* the failure was recorded, which is the escalated
  tier, so a task was replanned one attempt early while it still had opus to
  try. The stall pass overwrote a specific block reason with a generic one. And
  a pinned `haiku` was priced as `sonnet`, because rung 0 is falsy and
  `tier_of(...) or fallback` fell through.

- **Ambiguity is reported, not broken alphabetically.** Twelve bundled agents
  take the `backend` role and differ only by language. A task that names none is
  reported unroutable rather than sent to whichever charter sorts first — which
  would have put a Python task on the C++ charter and made the plan read as
  though someone chose that.

- **Model routing defaults are measured, not guessed.** The orchestrator-effort
  and implementer-model sweeps behind the table in `docs/model-routing.md` were
  run in mini-vise against test oracles written independently of the pipeline.
  The short version: implementers stay on sonnet, because the one gap opus
  closed was a reviewer-charter problem that costs nothing to fix directly and
  2× per run to fix by paying for a bigger implementer.

## [0.1.0a20] — 2026-08-22

### Added

- **The `designer` can look at what it designed.** `vise shot <target> --out
  <path> [--width N] [--height N] [--viewport]` renders a page to a PNG using
  the browser the render gates already drive. The designer runs it with `Bash`
  and reads the image with `Read`, which renders images.

  The gap it closes was measured, not assumed: an implementation of a designer
  brief shipped 9px of horizontal scroll at 375px, and `ui_layout` caught it.
  The designer did not, because it had no way to look. The one that decides
  could not see; the one that sees could not decide.

  Two constraints shaped the approach. **Claude Chrome is not reachable from a
  subagent** — the `designer` charter grants `Read, Write, Glob, Grep, Bash,
  Skill`, and no `mcp__claude-in-chrome__*` tool exists there at all, so
  building toward that framing would have shipped a capability the agent could
  never invoke. And **nothing new is installed**: `render_harness` already
  drives Playwright, which is already the `vise[design]` extra.

  The capture inherits the scheme allowlist (`http`/`https`/`file`), enforced
  before any browser launch. The harness treats a non-URL target as inline HTML
  and calls `set_content` on it, so accepting a bare string would have
  re-opened the CWE-918 finding already fixed in `design_profile._TARGET_RE`.

  A screenshot shows one viewport. It does not measure contrast and does not
  find an overflow at another breakpoint — `ui_contrast` and `ui_layout` do
  that, and `skills/design-brief` says so plainly.

### Fixed

- **Setting `VISE_TEST_CMD` broke vise's own test suite.** `vise bootstrap`
  instructs users to put it in their settings; six tests then read that ambient
  value instead of controlling it, so a contributor who followed vise's own
  documented setup got a red suite they did not cause. An autouse fixture now
  clears `VISE_TEST_CMD`/`VISE_LINT_CMD` for every test, the way the existing
  fixture isolates `$XDG_DATA_HOME`. A test that wants a specific command still
  sets it itself.

- **Two design-gate tests established "Playwright is missing" by hoping the
  machine lacked it.** They now simulate absence explicitly, so they exercise
  the fail-closed branch they are named after regardless of what is installed.

- **The unavailable-browser remedy string was never asserted against the code
  that produces it.** With every unavailable-path test injecting its own
  message, `_unavailable_message` could have dropped the `playwright install
  chromium` half and the suite would have stayed green while users hit a dead
  end one install short of a working browser.

### Fixed — found by review of the above, before it shipped

The first cut of `vise shot` passed its own tests and was wrong in four ways
an adversarial review caught. They are listed because each is the same shape:
a thing that looked like it worked.

- **The command did not exist for a plugin user.** The assets told the designer
  to run `vise shot`, but the plugin ships only `bin/vise-run` and puts no
  `vise` on PATH, so the designer got `command not found` — which prints no
  remedy, and was therefore the one failure the skill's own honesty clause did
  not cover. The assets now name `vise-run -m vise.cli.main shot` for that
  install, and the remedy names the interpreter that actually needs the extra
  rather than assuming the project venv.

- **A failed capture left the previous screenshot in place.** The designer is
  told to capture and then read the path, so one that missed the exit code read
  last week's image and revised its brief against a UI that no longer existed.
  Any failure now leaves the destination absent: a missing file is an
  unambiguous signal, a stale image is a plausible lie.

- **`--out` pointing at a symlink was followed.** `Path.resolve()` resolves the
  final component, so a committed `shots/out.png -> ~/.ssh/id_rsa` was written
  through on success and deleted on failure. Symlink destinations are now
  refused before any browser work. CWE-59.

- **A dev server that was not running answered with a 55-line traceback.** The
  most likely user mistake now gets one line naming the target that could not
  be loaded.

Also fixed one file over: `ui_contract._require_browser` appended a second
`Full setup:` to a reason that already carried one.

### Changed

- The coverage floor rises 69 → 71, following the real number.

## [0.1.0a19] — 2026-08-21

A patch release, and every fix in it is the same defect: **a gate that reported
nothing because of a name.** Two silenced by the name of a file under scan, one
by where a tool was installed. None of them failed loudly — each returned a
clean or complete-looking answer, which is the shape a gate must never have.

### Fixed

- **`design_tokens` treated a stylesheet named `tokens.css` as a token-config
  module.** `_CONFIG_FILE_RE` matches on filename alone, so the file took the
  config path — which parses a JavaScript `fontSize: {...}` object literal and
  understands nothing else — and returned before the CSS declaration parser ran.
  Identical content gave `tokens_declared=0` in `tokens.css` and `1` in
  `palette.css`, so `scale_bypassed` could never fire in a repo whose token file
  carries the most obvious name a design system has. The shortcut now applies
  only to JS/TS modules; stylesheets are always parsed as stylesheets.

- **`tailwind.config.mjs` and `.cjs` were never read.** `_CONFIG_EXTENSIONS`
  named both, but `UI_EXTENSIONS` never collected them — a dead line that read
  as support it did not deliver. The two most common modern Tailwind config
  filenames were skipped entirely, leaving `declared_type_tokens` empty and
  `scale_bypassed` unable to fire there either. Both extensions now sit in
  `UI_EXTENSIONS` and in `_SIGNAL_REQUIRED_EXTENSIONS`, so a stray `.mjs`
  carrying a hex still needs styling signal before it counts as UI source.

- **`vise bootstrap` did not detect a venv-installed `detect-secrets`.** It
  probed PATH only, but detect-secrets is a Python package: on a repo that
  followed vise's own setup instructions it lives in `.venv/bin` and never
  reaches PATH. Detection reported "no detect-secrets" against repos that had
  it installed and working. This direction of error is the worse one — an
  unbound check is presented to the user as a real gap to close *or accept
  knowingly*, so under-detection talks them into accepting a hole that does not
  exist. `secrets` now leads with the venv form, matching the ordering the
  Python block already used for `unit`, `lint` and `types`.

### Behaviour change

The first two fixes **raise strictness**. A repo with a `theme.css` or
`tokens.scss` carrying call-site literals now emits findings from that file
where the early return suppressed them, and a Tailwind `.mjs` config can now
make `scale_bypassed` fire. These are true positives, but a gate that was green
on 0.1.0a18 can turn red on upgrade with no change to the code it grades.
Record what the repo has today under `design.allowances` and ratchet it down —
that is what the allowance block is for.

### Changed

- The coverage floor rises 68 → 69, following the real number.

## [0.1.0a18] — 2026-08-21

### Added

- **Three design quality gates**, all of which fail closed. `design_tokens`
  scans source for colour, font-size, spacing and radius literals written where
  the project already declares a token, for a declared scale nobody references,
  and for a UI that declares no `font-family` at all. `ui_layout` renders the
  page and reports overflow, clipping, collision and off-document content per
  breakpoint. `ui_contrast` measures computed foreground against the *effective*
  background — the nearest ancestor that actually paints one — in the default,
  hover and focus states, at WCAG 2.2 AA.

  They exist because measuring vise's own UI output across three orchestrated
  projects contradicted the assumed cause. It is not missing taste, and it is
  not the known AI default look: tokens get declared and then bypassed at the
  call site. One project runs a CI guard that checks hex literals only, and has
  18 stray hex in 40,000 lines against 17 arbitrary font sizes — the drift is
  absent exactly where a check reaches and present everywhere else.

- `designer` agent and the `design-brief` skill: what a UI should look like is
  decided and written down before it is built, by a role that does not
  implement it.

- Composition rules in `web-ui-rules`, which was 74 lines of mechanically
  correct CSS with no aesthetic content at all.

### Behaviour change

- `tests_pass` now prefers `<project>/.venv/bin/python -m pytest -q` over a bare
  `pytest` when the project has a venv. A bare `pytest` resolves through `PATH`
  to a different interpreter from the one the project's dependencies live in,
  and reports failures that do not exist — it blocked this repo's own
  `implement` node on a test that was not broken. `VISE_TEST_CMD` and an
  explicit `test_cmd` still win.

- pytest's exit 5 (*no tests collected*) is recognised however the runner was
  spelled. It was matched by `cmd[0] == "pytest"`, so naming the runner
  explicitly made a repo with no tests yet block the gate instead of being
  waved through. Still scoped to pytest: exit 5 means something else to npm and
  cargo.

- `ponytail` no longer treats visual design as decoration to cut. "Shortest
  diff" applied to a stylesheet produces browser defaults, and unstyled is not
  lazy — it is unfinished.

### Fixed

- Six false-positive classes in the colour scanner, five found by auditing real
  output rather than by reasoning: HTML numeric entities read as hex,
  values inside comments, `@property { initial-value }`, a local variable named
  `style` dragging business logic into the UI scan, `href="#fff"`, and hex in
  free JSX text.

- Translucent background layers are composited instead of taken at face value.
  The ancestor walk stopped at the first background with any alpha, so a
  15%-opaque white over a gradient read as white and legible text reported
  1.0:1. A gradient or image background is now declined rather than guessed —
  there is no single colour behind a gradient.

### Security

- **CWE-59**, on a gate that is on by default: `Path.is_file()` follows
  symlinks, so a committed `theme.css` pointing outside the tree was read and
  its matched fragments reached the persisted evidence. Paths are resolved and
  checked for containment before any read.
- **CWE-918**: a `design.targets` entry that is not a URL fell through to
  `page.set_content`, executing repo-supplied markup in the browser on whatever
  machine runs the gate. Only `http://`, `https://` and `file://` reach a
  render, and a refused target fails the gate rather than being dropped.
- **CWE-400**: files above 2 MB are skipped rather than read whole, and the
  render gates' declared timeout is now actually passed to every browser call.

### Note

Playwright is an optional extra (`vise[design]`), imported lazily inside the
functions that need it, so `pip install vise` never pulls a browser. The two
render gates are deliberately not wired into the bundled `quality-gate` graph:
they fail closed on an unconfigured target, so wiring them by default would
turn `integration` red on every repo that never opted in. The graph file
carries the snippet that turns them on.

## [0.1.0a17] — 2026-08-18

### Added

- **`vise bootstrap` y `/bootstrap`** — configurar un repo destino. Instalar el
  plugin trae agentes, skills, comandos y hooks; lo que no puede traer es la
  parte que es *sobre ese repo*: qué comando corre los tests acá, qué significa
  `sast` en un proyecto Go. Eso vive en `.vise/quality.yaml` y en dos variables
  de entorno, y se escribía a mano — que mayormente significaba no escribirlo,
  así que las puertas skip-pasaban y el enforcement por el que uno instala vise
  nunca corría.

  La regla que le da forma: **ligar un check solo cuando su herramienta está**.
  Un perfil que nombra `pytest` en un repo sin pytest no crea rigor, crea una
  puerta roja por razones de entorno — y eso enseña a exportar
  `VISE_NODE_GATE_OVERRIDE=1`. Verifica el módulo, no el intérprete, y exige
  evidencia de adopción para las herramientas que sin config son ruido puro
  (mypy, eslint) pero no para las que corren configless (`go vet`,
  `cargo clippy`).

- **`orchestration` ahora informa a los subagentes sobre la capa de símbolos.**
  La skill `codelayer` existía y nada la conectaba: ningún agente la precargaba
  y ningún charter mencionaba `read_unit` ni `search_similar`, así que un
  builder despachado no tenía forma de saber que esas tools existen. Se chequea
  una vez con `compute_index_status` y, si livespec está montado, van al brief.

### Fixed

- **`a16` publicó el número sin el contenido.** El commit de `/bootstrap`
  aterrizó *después* del release, así que `main` declaraba una versión que ya
  estaba instalada y `claude plugin update` no traía nada — el updater compara
  la versión declarada, no el commit. Es exactamente lo que advierte el
  docstring de `test_version_sync.py`, citado en el PR de `a16` y cometido un
  commit más tarde.

## [0.1.0a16] — 2026-08-17

### Added

- **CodeLayer, del lado de vise** — `codelayer_gate`, la skill `codelayer`, y
  los comandos `/codelayer` y `/debt`.

  Un repo bien factorizado le cuesta *más* a un agente que uno mal factorizado:
  más archivos, más saltos, más contexto quemado. Estructura y navegabilidad
  tiran en contra, y el agente resuelve la tensión escribiendo código acoplado.
  El hook redirige las lecturas de fuente a las tools de símbolo de livespec, y
  su mensaje de denegación trae la llamada de reemplazo ya formada — un deny
  que solo dice "no" se rodea con `cat`, después con `sed`.

  Tres modos: `off` (default, inerte), `warn` (registra lo que habría negado,
  no bloquea nada) y `enforce`. Un modo desconocido cae a `off`, así que un
  typo en la variable no se convierte en una puerta que bloquea. Configs,
  tests, docs, migraciones y manifiestos nunca se gatean.

  `/codelayer warnings` no reporta un conteo crudo sino un juicio sobre cuáles
  parecen falsos positivos: "3 de 47 están mal y son todos stubs generados" es
  lo que decide si conviene enforcar; "47" no dice nada.

  `/debt` maneja el baseline de duplicación de livespec.

### Fixed

- **`diff_scope` bloqueaba con el árbol completamente limpio.** `graph_activate`
  escribe `.claude/workflow/graph.yaml` dentro del proyecto, y en cualquier repo
  que no lo tuviera gitignoreado ese archivo contaba como trabajo fuera de la
  partición — así que el primer traverse después de activar un workflow lo
  bloqueaba un archivo que **vise acababa de escribir**. Un gate que falla
  cuando no hiciste nada es un gate que se apaga.

  El estado propio de vise (`.claude/workflow/`, `.claude/settings.local.json`)
  queda excluido por path, no por `--exclude-standard`: depender del
  `.gitignore` del consumidor significa que vise escribe un archivo y después
  culpa al usuario por él. `.claude/workflows/` (plural) NO está excluido — esos
  grafos los escribe el usuario, y moverlos fuera del scope declarado es
  justamente lo que este validator existe para notar.

  Encontrado corriendo los dos validators como node gate de un grafo real; los
  unit tests no podían verlo porque arman un repo git pelado, sin estado de vise.


- **`VISE_NODE_GATE_OVERRIDE=1` did nothing on the gate people actually hit.**
  It bypassed the node gate and then the `validators_green` edge check rejected
  the traverse anyway, from a second gate that never consulted the variable.
  `feature-dev`'s `spec` phase exits by a validators_green edge, so the escape
  hatch — advertised in the node gate's own message, *"or
  VISE_NODE_GATE_OVERRIDE=1 to bypass"* — was inert exactly where an agent
  reaches for it, and the traverse kept failing with a different error, so the
  agent retried the same edge in a loop. Found by driving `feature-dev` end to
  end against a real repo, not by reading the code.

  The override is now read once and consulted by both gates, and the
  validators_green rejection names it too.

- **The override rate counted intents, not bypasses.** `node_gate_overridden`
  was emitted at the node gate, before the validators_green edge had its say —
  so four retries of one blocked edge logged four routed-around gates for zero
  actual bypasses. It is now emitted once, after both gates, only when the
  traverse really proceeds. The number `vise insights` reports means what it
  says again.


### Added

- **`vise insights` — vise can finally produce evidence about itself.** It
  gates other repos on evidence and recorded none of its own: nothing said which
  workflow ran, which gate blocked, or whether anyone was setting
  `VISE_NODE_GATE_OVERRIDE=1` and walking through. `node_gate_state` counts
  failed attempts, but that counter advances identically whether the gate was
  fixed or bypassed, so the one question worth asking about a gate was
  unanswerable. `gates.jsonl` now records `workflow_activated`,
  `node_gate_blocked`, `node_gate_overridden`, and `validator_outcome` — the
  last on green gates too, because a node that passes having verified nothing
  never produces a blocked event and is the case most worth catching. Two
  numbers carry the report: the **override rate** (a gate routed around is not a
  gate) and the **verified rate** (`passed` is not `verified`; it names any
  validator that has never once verified anything on this machine). Every write
  is best-effort — a telemetry call that can break a session is worse than no
  telemetry.
- **`shapes` on an experience entry — the repeat count that could not be
  counted.** `agent-autoheal` decides anecdote-vs-pattern on whether the same
  failure shape appears twice for one agent, had nowhere to store that, and so
  encoded incidents as `;;`-separated segments inside `description` — which
  merges by keeping the **longest** string. A shorter follow-up was discarded
  while the write still reported success, and a tally in prose was the worst
  case of all: `x1` → `x2` measures the same, so the increment never won. The
  cold path's trigger could not be incremented through the store that held it.
  `shapes: dict[str, int]` merges additively; `experience_record` takes a
  `shape` and returns `shape_count`. The skill drops its whole
  storage-workaround section.
- **`no_new_deps` and `diff_scope` validators.** Two rules that existed only as
  prose. `ponytail` requires a stated reason for a new dependency and nothing
  measured whether one appeared; `skills/orchestration` requires a wave to
  partition scope by file ownership and nothing measured that either. Neither
  validator bans anything — naming a package in `allow:` *is* the stated reason,
  and `diff_scope` passes for any file inside the declared partition. Both
  fail-open (`unverified`, never a block) with no git, no manifest, or an
  unresolvable base; `diff_scope` with an empty `allow` fails **closed**, since
  a scope gate that permits everything when misconfigured is worse than no gate.
  `diff_scope` also reads untracked files — a brand-new file outside the
  partition is the case worth catching and `git diff` never sees it.
- **README documents the validator registry**, with a test that a new validator
  cannot ship undiscoverable — the same orphan failure as `swift-rules`, one
  layer down.
- **`engineering-baseline` — the general rules every agent now carries, and the
  precedence rule that settles conflicts.** vise shipped twelve per-language
  rules skills and no language-agnostic ones: nothing on errors, secrets,
  dependencies, naming, or reporting, and — worse — no stated order between the
  three instruction sources an agent loads at once. `python-rules` said "use
  uv" while the repo used pip; `ponytail` said "stdlib first" while
  `typescript-rules` mandated Zod; nothing said which wins, so the agent picked
  whichever it read last. The new skill states a five-rung order (user request
  → safety → the project's existing conventions → the language rules →
  minimalism) and every one of the 19 agents preloads it.
- **Three new rules skills for surfaces agents were already touching blind.**
  `sql-rules` (parameterization, reversible DDL, expand/contract, concurrent
  index creation) — `db-migrator` wrote SQL with no SQL rules at all.
  `bash-rules` (strict mode, quoting, no `eval`) — 18 agents have `Bash`.
  `web-ui-rules` (semantic HTML, accessibility, CSS, XSS sinks) — `frontend`
  claimed to cover styling and accessibility while preloading only
  `typescript-rules`.
- **`backend-swift` and `backend-lua`.** `swift-rules` and `lua-rules` shipped
  with no agent preloading them and no row in the orchestration fleet table,
  while `plugin.json` configured `sourcekit-lsp` and a Lua server — so the
  setup looked complete and Swift work still routed to `general-purpose`, which
  preloads nothing. Both rules skills had never applied to a single file.
- **Every `*-rules` skill has a `## Security` section, CWE-tagged.** Only
  `php-rules` carried a prepared-statements line; `java`, `kotlin`, `csharp`,
  `go`, `rust`, and `python` rules had no injection rule at all, which made
  "follow the language rules" mean something different per language. Every
  bullet now carries the CWE to cite — a finding without an ID is a sentence,
  with one it is a class that can be deduped across a report and compared
  across languages.
- **`security-baseline` — how to name, rank, and triage a finding.** vise had
  security *advice* and no security *vocabulary*: zero mentions of CWE, CVE,
  OWASP, or supply chain across every agent and skill. The new skill carries a
  CWE-indexed surface checklist (the OWASP Top 10 in the order a code reader
  meets it), a severity ladder anchored on the preconditions an attacker needs,
  the reachability-first protocol for a dependency advisory, and supply-chain
  rules. `security-auditor` and `reviewer` preload it.
  **Deliberately excluded: CVSS scoring.** An agent reading a diff cannot know
  the deployment, the network exposure, or the data classification, so a derived
  `7.5` is fabricated precision that outranks its own evidence. Assets may quote
  a project's score and never produce one — `test_asset_coverage.py` enforces
  that, matching the instruction rather than the word so prose about CVSS stays
  legal.
- **`security-auditor` runs the scanners instead of auditing by reading.** A
  code read cannot see a vulnerable transitive dependency, and reasoning about
  versions from memory turns a stale advisory into a false all-clear. It now
  runs the project's `sast`/`sca`/`secrets` checks, reports a scanner that is
  not installed as *not checked* rather than as clean, and quotes advisory IDs
  and version ranges from the tool's output rather than from memory.
- **`CLAUDE.md`.** vise gates other repos on having project instructions and
  had none of its own — including the `.venv/bin/python` rule that
  `.vise/quality.yaml` already explains at length to nobody who reads it first.
- **`test_asset_coverage.py`,** pinning the structural invariants no
  frontmatter check could see: every agent preloads the baseline, every
  code-touching agent can reach language rules, no rules skill is orphaned,
  the twelve backend charters carry one identical contract, and an agent never
  promises something its granted tools cannot do.
- **Validator passes say whether anything was actually verified.** Every
  validator record carries an `outcome` of `verified`, `unverified`, or
  `failed`. The fail-open contract is unchanged — a repo with no linter is not
  blocked — but all 14 fail-open skip-pass paths across `lsp_clean` (7,
  one per language plus the shared ones), `tests_pass`, `lint_pass`,
  `quality_check`, and `openspec` now report
  `unverified` instead of presenting as clean, and the outcome reaches the gate
  result rather than only the stored record. Evidence keeps "nothing to check"
  distinct from "could not check", because they share an outcome and mean
  opposite things. This was a live hole, not a theoretical one: `lint_pass`
  sits on `feature-dev`'s `validate` node at weight 0.5, so on a machine
  without its linter that node reported a confident green having run nothing.
  The validator docstring had described this exact failure — *"Green gate,
  evidence reading 'not on PATH', nothing run"* — since before the fix existed.
- **Diagnostics cover Go, Rust, and TypeScript.** `lsp_diagnostics` gained
  `go vet`, `cargo check`, and `tsc --noEmit` under the same fail-soft
  shell-out contract, and `lsp_clean`'s file filter widened past `.py`.
  Whole-project checkers run once and have their findings filtered to the
  changed set, so a diagnostic in an untouched file cannot fail a gate, and a
  checker that exits non-zero having produced no parsed diagnostic — `cargo`
  with no `Cargo.toml`, `tsc` with no `tsconfig.json`, a Go file outside a
  module — reports `unverified` rather than the clean pass an empty finding
  list would otherwise imply. `lsp_clean` is bounded to 180s per run in total,
  and languages it did not reach in that budget are reported unverified instead
  of the gate hanging. Each
  checker's blocking-versus-cosmetic rule is vise's own, never inherited from
  the tool's severity field — the precedent ruff set by marking unused imports
  as errors. `lsp_clean` now also gates the implementation-exit node of
  `debug`, `migration`, `quality-gate`, `security-audit`, and `sprint-e2e`,
  where previously it ran on one node of one workflow out of nine.
- **The declared language servers are finally reachable.** vise declared
  `lspServers` for 12 ecosystems while no agent listed the `LSP` tool, so
  nothing could call them. The 18 code-touching agents now carry it, each
  `*-rules` skill states the circumstance that requires a lookup rather than
  suggesting one, and the orchestration skill has the engineer resolve a
  symbol's caller set before dispatching a wave that changes its signature —
  the caller set being the input to the wave's file-ownership partition. No
  gate asserts that an agent called `LSP`: that is satisfiable by one call with
  a discarded result, so it would raise a measured number while measuring
  nothing.

### Changed

- **The five language-agnostic code agents can now load language rules.**
  `tester`, `debugger`, `db-migrator`, `reviewer`, and `security-auditor`
  carried `ponytail` and nothing else — the whole conventions layer reached 11
  of 17 agents. They now have the `Skill` tool and a charter section naming
  which `*-rules` skill to load for the file at hand.
- **The twelve `backend-*` charters are one contract again.** `backend-python`
  and `backend-typescript` carried "validate external input at boundaries;
  parameterize every query" and "no dead code or broken imports left behind";
  the other eight did not, so the same request met a different standard per
  language. `backend-cpp` and `backend-rust` moved to `effort: high`.
- **Tooling mandates moved to `## Tooling — greenfield defaults only`.** A
  rules skill telling an agent "don't use pip", "use structlog", or "use
  neverthrow" was, in a repo that had already chosen, an instruction to migrate
  a toolchain as a side effect of an unrelated change. Those lines now say they
  apply only where the project has no incumbent.
- **`docs-writer` has `Bash`.** Its charter promised "keep examples runnable —
  copy-paste must work against the current code" while granting no tool that
  could run anything; the promise was unkeepable by construction.
- **`security-auditor` no longer preloads `ponytail`.** It writes no code, and
  "the shortest thing that works" is the wrong lens for an audit.
- **`cpp-rules` triggers on `.cxx`, `.hxx`, `.C`, and `.H`.** Its description —
  which is what decides whether a skill loads — listed five extensions while
  `plugin.json` mapped clangd over nine.
- **`go-rules` no longer calls `gorilla/mux` unfit for new projects.** It has
  been maintained again since 2023, and the rule was pushing migrations off a
  perfectly good incumbent. `python-rules` no longer presents `asyncio.TaskGroup`
  as a drop-in for `gather`; `gather(return_exceptions=True)` has no TaskGroup
  equivalent.
- **`security-audit`'s `verify` node gates on something.** It shipped with four
  commented-out `command_exit` scanner lines, so the node whose whole claim is
  "the criticals are gone" checked nothing. They were commented for a real
  reason — `command_exit` fails *closed* on a missing binary and would block
  every repo without that toolchain — so the replacement is `quality_check`,
  which reads the command out of `.vise/quality.yaml` and skip-passes with an
  honest "not configured" record. `scan` stays deliberately ungated and now says
  why: node validators run on every traverse, so gating `scan` on SAST would
  block the path to `triage` exactly when the scanner found something.
- **`orchestration` routes to `sprint-e2e`, `backend-swift`, and
  `backend-lua`,** and says out loud that `general-purpose` preloads nothing —
  so a brief dispatching it must name the skills to load.
- **`agent-autoheal` says where a heal may land.** Its cold path edited
  `agents/<name>.md`; for a bundled agent that file lives in the plugin install
  directory and is overwritten on the next update, losing the heal silently.
  Bundled charters are now shadowed under `.claude/agents/` or fixed by
  briefing instead.
### Fixed

- **A marketplace install exposed no MCP tools, silently.** `claude plugin
  install vise@rixmerz` copies the plugin's files but provisions no Python
  environment, so `bin/vise-run` fell through to system `python3` — which
  usually lacks `fastmcp` — and exec'd it anyway. The server then died on
  `import fastmcp` during startup, with the traceback going somewhere the user
  never looks, and Claude Code simply listed zero vise tools. Skills, commands,
  and agents kept loading (they are plain files), which made the install look
  successful. The launcher now verifies the interpreter can import `fastmcp` and
  `fastembed` before exec'ing it, and otherwise prints how to provision a venv
  and exits non-zero. The venv fast path is unchanged and skips the probe.

## [0.1.0a14] — 2026-07-31

### Fixed

- **`experience_derive_checklist` fabricated conventions.** The note extractor
  split resolution text on a bare `.`, so it truncated at the first period of
  any kind — a path (`~/.local/share/vise`), a version (`0.65`), a directory
  (`.venv`) — and then hard-cut at 120 characters mid-word. The five fragments
  it produced are rendered under the heading *"Conventions observed"* and
  injected into an agent's context, so a mangled fragment is not a display
  glitch: it is an invented convention presented as learned experience. Real
  output before the fix: `"Why: the hooks hardcoded ~/"`. Now splits on
  `". "` and truncates at a word boundary.
- **`secrets` is bindable after all.** Previously documented as impossible
  because gitleaks is a Go binary. `detect-secrets` is pure Python, ships from
  `[dev]`, and is now bound in this repo's own profile.

## [0.1.0a13] — 2026-07-31

### Added

- **`sast` and `sca` bound in vise's own quality profile.** vise shipped a
  security-audit workflow and had never run a scanner on itself; the `security`
  node read `verified=0 skipped=4`.

### Fixed

- **SHA1 without `usedforsecurity=False` in `hooks/_xdg.py`.** Found by the
  bandit binding above. Not lint appeasement: under an OpenSSL FIPS provider
  plain `hashlib.sha1()` raises, and that call resolves every state directory,
  so vise would fail to start on a FIPS host rather than degrade. The flag
  leaves the digest byte-identical — changing it would orphan every state dir
  already on disk — and a test now locks the exact suffix.
- **`quality.example.yaml` recommended `pip-audit --strict`.** `--strict` fails
  when *any* distribution cannot be audited, and a project pip-installed into
  its own venv is not on PyPI, so the shipped example exited 1 forever on
  essentially every Python repo mid-development.

### Notes

`sast` is bound at `--severity-level medium` on purpose. All 89 Low findings
are B110/B112 against the deliberate fail-open handlers every vise hook is
required to have; gating on them means 89 permanent findings or 89 `#nosec`
comments, and neither is a security control.

## [0.1.0a12] — 2026-07-31

### Fixed

- **A quality check bound to a project-relative command never ran.** The
  pre-flight existence check and the run disagreed about the working directory:
  `shutil.which()` resolves a path-like command against the *current process's*
  cwd — the MCP server's — while the command itself runs with
  `cwd=project_dir`. Every relative command skip-passed forever with evidence
  reading `not on PATH`, when PATH had never been consulted. That covers
  `node_modules/.bin/eslint`, which is how essentially every JS repo invokes
  its linter, plus `.venv/bin/pytest` and `./scripts/check.sh`.
- **Snapshotting vise broke vise's own tracked quality profile.**
  `_ensure_state_dir_gitignored` did not recognise `.vise/*` as covering the
  directory, so it appended `.vise/` underneath `!.vise/quality.yaml`. Git
  never descends into an excluded directory, so the re-include below it became
  dead and the tracked file would drop out on the next `git add`.

### Behaviour change

- Validator records from `quality_check` are now named for the **check**
  (`quality_check:sast`), not the validator type. A `security` node declares
  four of them; a `failed[]` entry reading `quality_check` said a gate blocked
  and nothing about which defect class did it. Anything parsing that field by
  exact string will need updating.

## [0.1.0a11] — 2026-07-31

### Added

- **`quality_check` validator and the quality-gate workflow.** A workflow node
  gates on a check *name*; `.vise/quality.yaml` says what that name runs in this
  repo. One workflow ships to every language without guessing a toolchain.
  Tiers: static → tests → security → integration, with mutation testing and
  fuzzing deliberately **out of band** (a full mutation run as a gate would
  stall every traversal; fuzzing has no completion condition at all).
- **Gate visibility.** Traversal results now report `verified` vs `skipped`
  counts and per-check evidence. A gate that ran 13 checks and one that skipped
  all 13 previously returned the same thing.
- `VISE_QUALITY_PROFILE` to override the profile path.

### Fixed

- **`tests_pass` deadlocked every repo whose runner vise cannot find.** It
  failed *closed* on a missing binary, and release-graph declares it on the
  START node — so the workflow could not be entered at all. Now fails open with
  `source="asserted"`, matching `lint_pass`/`lsp_clean`.
- **Experience memory persisted the first occurrence and dropped every repeat.**
  `record()` saved on the new-entry branch only, so `occurrences` was pinned at
  1 on disk. `save()` is now atomic (temp file + `fsync` + `replace`).
- **The lint gate's rule set floated with whatever ruff CI installed.** With no
  `select`, the rule set is whatever the installed ruff calls default, and that
  moves between versions — CI resolved to a ruff whose expanded default
  reported 318 findings against a tree that was clean locally. Now pinned.
- **`graph_traverse` ran the node's validators twice** on a `validators_green`
  edge — on feature-dev's `test` node, that meant running the entire suite twice
  per attempt to leave the phase.

### Removed

- `workflow_override_detector` hook, the `demo-feature` workflow, and the
  `clean_context`/`prior_summary` parameters on `graph_traverse`.

### Known gaps

- **No telemetry on whether any of this fires.** 17 nodes ship `tools_blocked`
  and 13 quality checks ship declared, with a single event kind between them.
  There is no way to know whether a gate blocks in real use, whether a skipped
  check is ever bound, or whether an agent obeys an injected prompt.
- **`phrase` edges are advisory.** `graph_traverse(edge_id=)` never checks the
  phrase, so vise cannot gate on a human signal — human-reviewed acceptance
  tests and manual spot checks are out of scope until that changes.
- **All 11 bundled recipes ship unbound** (19 capabilities, 0 default
  bindings). This is deliberate and stated in each recipe's description, but it
  means `recipe_*` and `capability_*` do nothing until you bind them.
