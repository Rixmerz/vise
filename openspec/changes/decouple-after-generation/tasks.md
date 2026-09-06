## 1. The phase, without the graph

- [x] 1.1 `src/vise/runtime/decouple.py`: `triage(candidates) -> DecoupleReport`, pure, no subprocess
  — *not* `candidates(diff, index)`; vise cannot reach livespec's index, so the
  looking stays with the agent and the judging comes here. See design,
  "Correction: steps 1 and 2 are not vise's to run".
- [x] 1.2 Refusal list lifted from `skills/codelayer/SKILL.md` "When NOT to decouple", each rule a named predicate
- [x] 1.3 `refuse(candidate) -> str | None` returning the rule's name; a refusal is recorded, never silent
- [x] 1.4 `DecoupleReport` dataclass: found / refused (with rule) / moved / reverted / cost / skipped
- [x] 1.5 Tests: every refusal rule has a case that trips it and a case that does not
- [x] 1.6 The constants are asserted against the skill's own sentences, so prose and number cannot drift
- [x] 1.7 The order the rules are tried in is pinned, because only the first refusal is recorded

## 2. Reaching livespec

Reaching it is the agent's, not vise's — see design, "Correction: steps 1 and 2
are not vise's to run". So these landed as the `survey` phase's prompt, which
is the asset that tells the agent which calls to make and what to bring back.

- [x] 2.1 `compute_index_status` first; no index or a stale one takes the
      `survey → report` edge and the report's `skipped` field carries the
      reason — *not* a `decouple_skipped` event, because nothing in vise runs
      to emit one
- [x] 2.2 `search_similar` per added unit; `analyze_impact` per changed signature
- [x] 2.3 Names go through `vise.core.livespec.LIVESPEC_TOOLS`; the graph is in
      `test_livespec_contract.SPEAKERS` — the only *workflow* under the contract
- [x] 2.4 The prompt asks for exactly the fields `Candidate` declares, and a test
      parses the block to prove it — the testable half of the seam the design
      says cannot be tested end to end

## 3. The node

- [x] 3.1 ~~`decouple` node in `feature-dev-graph.yaml` between `test` and
      `validate`, `enabled: false`~~ — impossible as written and resolved the
      other way: `decouple-graph.yaml` ships as its own workflow, held in
      `_INTENTIONALLY_UNROUTED` until section 4's bar is met. `enabled` is not
      a field the parser reads, so a node in `feature-dev-graph.yaml` would run
      in every repo that types `/feature`, which is what 3.1 existed to avoid
- [x] 3.2 Exit gated on `tests_pass`, mechanically (`validators_green`, not a
      phrase). `diff_scope` ships **commented**, with the reason: its `allow:`
      globs belong to the consuming repository and an empty `allow` fails
      closed, so a bundled default would either block every run or permit
      everything — and the second reads as a gate while being none. Same
      pattern `feature-dev-graph.yaml` already uses for its coverage gate
- [x] 3.3 Prompt injection on all four phases: what the phase is for, the
      refusal list by the rule names the code returns, that the agent may
      disagree with a refusal and may not act on it, and that a red test is
      reverted rather than argued with
- [x] 3.4 `test_asset_honesty` (11 workflows now, README and CLAUDE.md counts),
      `test_orchestration_skill_sync` (allowlisted with its reason),
      `test_node_gate_coverage` (three nodes exempt, each with why),
      `test_livespec_contract` (new speaker), plus `test_decouple_graph.py`

## 4. Proving it

- [ ] 4.1 Run against three real repos with `VISE_DECOUPLE=report` (no moves) and keep the reports
- [ ] 4.2 Turn moves on only when the three reports show more accepted candidates than refusals that were wrong
- [ ] 4.3 CHANGELOG entry under **Behaviour change** when the node is enabled by default, not before

## Blocked, and why

- **Section 4 needs three real repositories with livespec mounted.** It is the
  bar this change set itself — the same one `codelayer` set for `enforce` — and
  nothing in this session can meet it. Everything else is built and the
  workflow ships runnable but unrouted, which is precisely so that the bar can
  be met by someone who has the repositories, rather than skipped.

## Resolved since, by building it

- **3.1 could not be written as specified.** `enabled: false` is not a field
  `graph_parser.py` reads; nodes carry `mcps_enabled`, which is about tool
  surface. Of the three ways out recorded here, the third was taken —
  `decouple-graph.yaml` as its own workflow in `_INTENTIONALLY_UNROUTED`. It
  is the only one that neither changes a shipped asset format for one caller
  nor puts an unproven phase in every repo's `/feature` path.
- **3.2's `diff_scope` cannot ship configured.** Found by writing it: the
  validator takes `allow:` globs relative to the repo root, and vise does not
  know the consuming repository's layout. An empty `allow` fails closed by
  design. So it ships commented with the reason, and the exit gate is
  `tests_pass` alone — which is the gate that matters here anyway, since the
  phase's whole claim is that it changes no behaviour.
