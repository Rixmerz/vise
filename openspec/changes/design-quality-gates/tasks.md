# Tasks

## 1. Wave 1 — independent, no shared files

- [x] 1.1 `engines/design_tokens.py`: scanner for colour, font-size, spacing and
      radius literals outside token definitions; declared-vs-used scale ratio;
      missing `font-family`. Pure stdlib.
- [x] 1.2 `engines/render_harness.py`: vendor `layoutlint/browser.py` with the
      launch hoisted, unresolved selectors reported, and colour in the style set.
- [x] 1.3 Investigate whether `graph_traverse` needs a required verdict.
      **It does not — premise was wrong.** `_run_node_validators` already runs
      for any node declaring validators, before any edge condition is
      evaluated, whatever the edge type. `reason` is telemetry, not a gate. The
      nodes with no validators (`orient`, `design`, `commit`, `deep-passes`)
      are read-only or terminal, where nothing mechanical exists to verify.
      The real hole is elsewhere and 3.1 closes it: `static` declares `types`,
      `complexity` and `deps`, all unbound, and `QualityCheckValidator` skips an
      unbound check as `passed=True, outcome="unverified"` — so the node can
      pass having verified almost nothing. That is why the new gates are
      dedicated fail-closed validator types and not `quality_check` entries.
- [x] 1.4 Prose: the unexplained-diff-hunk rule in `agents/reviewer.md`, the
      orchestrator-cost line in `skills/orchestration/SKILL.md`, and the
      anti-slop table in `skills/web-ui-rules/SKILL.md`.

## 2. Wave 2 — depends on the harness

- [x] 2.1 `engines/ui_checks.py`: geometry over harness output — internal clip,
      containment overflow, external collision, off-document, container
      overflow, and an `unresolved_selector` defect so a selector that matched
      nothing can never pass as clean.
- [x] 2.2 `engines/ui_contrast.py`: WCAG relative luminance, alpha compositing,
      the 4.5/3.0 thresholds with the large-text relaxation. Split into its own
      module so it can be built in parallel with 2.1.
- [x] 2.3 `engines/ui_contract.py`: derive the inspection set from the rendered
      document. Every generated selector must resolve to exactly one element —
      `querySelector` returns the first match, so a class-based rule would
      collapse eight repeated cards into one and silently drop seven.

## 3. Wave 3 — wiring

- [x] 3.1 Three validators in `engines/validators.py`, registered in
      `VALIDATOR_TYPES`, each returning `passed=False, source="mechanical"`
      when its dependency is unavailable.
- [x] 3.2 `pyproject.toml`: the `design` optional extra carrying playwright.
      Core dependencies unchanged.
- [x] 3.3 `quality-gate-graph.yaml`: `design_tokens` wired on `static`. The two
      render gates are documented in the file header with their wiring snippet
      but deliberately NOT wired — they fail closed on an unconfigured target,
      so wiring them by default would turn `integration` red on every repo that
      never opted in. Not wired means not running; wired means configured or
      red. Opt-in is the reversible choice.

## 4. Wave 4 — proof

- [x] 4.1 Tests for `design_tokens` covering every scenario in the spec,
      including the no-UI-source case.
- [x] 4.2 Tests proving each validator's unavailable path returns
      `passed=False` — with no browser installed. 33 tests. Found a real defect:
      `browser_status()` named only `pip install`, never the Chromium step, so
      an agent acting on that evidence installs one thing and stays broken.
      Both branches now carry the whole remedy.
- [x] 4.3 Fixture pages for the geometry and contrast checks, skipped when
      Chromium is absent. Caught a second real defect: `derive_candidates`
      classified nothing on a page of two absolutely-positioned empty divs
      overlapping by 40px — no text, no children, so no candidates and a clean
      verdict. Out-of-flow boxes are now containers regardless of content.
- [x] 4.4 Asset-honesty updates: CLAUDE.md counts, and any test that asserts on
      the validator registry or the graph's validator list.

## 5. Wave 5 — verify

- [x] 5.1 `ruff check . --exclude .claude` clean.
- [x] 5.2 Full suite green, coverage at or above the current floor. 68%
      before this change and 68% after, across ~800 new lines. The floor was
      lagging at 62; ratcheted to 68 per CLAUDE.md's own rule.
- [x] 5.3 Confirm a core install still imports with playwright absent. Every
      module under `vise.` walked with the import blocked: none break.
- [x] 5.4 Adversarial review of the whole diff. Verdict was **do not ship**,
      with three blocking findings, all real and all in code I wrote myself:
      `ui_contrast` discarded `derive_candidates`' skipped reasons, never read
      `snapshot["unresolved"]`, and — the serious one — the spec required
      hover and focus states and only `default` was implemented, leaving the
      `state` parameter as dead flexibility standing where the behaviour
      should have been. All three closed. Two non-blocking findings closed
      too: substring token counting (`--text-s` matching inside `--text-sm`),
      and a candidate set derived only at the widest breakpoint, which made a
      mobile-only nav unreachable at every width. A fifth false-positive class
      in the colour scanner was found independently and closed with it:
      `href="#fff"` and hex in free JSX text now need a colour-bearing
      property in front of them — SpeedRunners dropped 64 -> 39 raw_color.

## 6. Post-review hardening

- [x] 6.1 Security audit findings closed: symlink containment (CWE-59, default-on
      gate, reproduced), target-scheme allowlist (CWE-918), file-size cap and a
      timeout that is actually applied (CWE-400), NUL-delimited `git
      check-ignore` so a path containing a newline cannot desynchronise the
      ignore decision.
- [x] 6.2 Dogfooding against a real page found the last false positive: the
      ancestor walk stopped at the first background with any alpha, so a
      15%-opaque white over a purple gradient read as white and legible text
      reported 1.0:1. Translucent layers are now composited, and a gradient or
      image background is declined rather than guessed — there is no single
      colour behind a gradient.
