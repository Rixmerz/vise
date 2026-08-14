# Changelog

Notable changes per release. Starts at `0.1.0a11`; earlier alphas predate this
file and are described only by their commits.

Alpha means the tool surface is still moving. Where a change alters behaviour
you may already depend on, it says so under **Behaviour change**.

## [Unreleased]

### Added

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
- **Every `*-rules` skill has a `## Security` section.** Only `php-rules`
  carried a prepared-statements line; `java`, `kotlin`, `csharp`, `go`, `rust`,
  and `python` rules had no injection rule at all, which made "follow the
  language rules" mean something different per language.
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
