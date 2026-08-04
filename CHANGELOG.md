# Changelog

Notable changes per release. Starts at `0.1.0a11`; earlier alphas predate this
file and are described only by their commits.

Alpha means the tool surface is still moving. Where a change alters behaviour
you may already depend on, it says so under **Behaviour change**.

## [Unreleased]

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
