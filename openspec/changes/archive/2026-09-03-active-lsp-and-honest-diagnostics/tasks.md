# Tasks

## 1. Honest fail-open evidence (lands first, alone)

- [x] 1.1 Add a three-way outcome (verified clean / unverified / failed) to the
      `lsp_clean` validator record in `src/vise/engines/validators.py`
- [x] 1.2 Keep every current fail-open path passing, but classify each as
      unverified with evidence naming the cause — checkers looked for and not
      found, no files in scope, engine exception — keeping "nothing to check"
      textually distinct from "could not check"
- [x] 1.3 Surface the unverified outcome in the node-gate result so it reaches
      the gate output, not only the stored record
- [x] 1.4 Tests: one per fail-open path asserting the outcome and that the gate
      still opens; one asserting a real clean pass is reported as verified
- [x] 1.5 Correct the docstrings in `lsp_diagnostics.py` and the `lsp_clean`
      validator to state that no LSP is involved (rename deferred — the type is
      referenced by user-copied workflow YAML)

## 2. LSP navigation in the orchestrator brief

- [x] 2.1 Add a pre-dispatch step to `skills/orchestration/SKILL.md`: resolve
      the caller set with `findReferences` / `incomingCalls` for any signature
      the wave will change, and enumerate it in every brief
- [x] 2.2 State the no-server-available fallback — text search, and the brief
      says the caller list is unverified
- [x] 2.3 Verify `test_orchestration_skill_sync.py` still passes, and extend it
      if it pins the skill's section structure

## 3. Reachable LSP for the fleet

- [x] 3.1 Add `LSP` to the `tools:` frontmatter of the 16 code-touching agents
      in `agents/` (all except `docs-writer.md`)
- [x] 3.2 Add the conditional trigger to each `skills/*-rules/SKILL.md`, phrased
      as circumstance plus consequence, never as discretion
- [x] 3.3 Check `test_asset_honesty.py` for an agent/tool inventory that must be
      updated alongside the frontmatter change

## 4. Per-language diagnostics coverage

- [x] 4.1 Extend `lsp_diagnostics` with `go vet`, `cargo check
      --message-format=json`, and `tsc --noEmit`, each behind the existing
      fail-soft shell-out contract
- [x] 4.2 Define the blocking-versus-cosmetic rule per checker, vise-owned and
      not inherited from the tool's severity field, following the ruff allowlist
      precedent
- [x] 4.3 Filter whole-project checker findings to the changed file set
- [x] 4.4 Widen `_SOURCE_EXTS` on the validator to the newly supported
      extensions
- [x] 4.5 Report per-language verification status so one absent checker does not
      suppress another language's findings
- [x] 4.6 Add `lsp_clean` to the implementation-exit node of every code-touching
      shipped workflow in `src/vise/assets/workflows/`
- [x] 4.7 Tests per checker: available-and-clean, available-and-broken,
      absent-and-skipped, plus the mixed-language case
- [x] 4.8 Keep `edit_feedback` on the fast ruff-only path — whole-project
      checkers must not enter the 5s hook budget

## 5. Close out

- [x] 5.1 Full test suite; confirm the five pre-existing failures
      (`ruff`-absent and the `test_state_paths_collision` digest) are unchanged
      and no new failure appears
- [x] 5.2 CHANGELOG entry under `[Unreleased]`
- [x] 5.3 README: correct the claim that vise's LSP declarations are used, and
      document the verified/unverified distinction
