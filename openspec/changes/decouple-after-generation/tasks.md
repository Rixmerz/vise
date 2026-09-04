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

- [ ] 2.1 Detect the index once (`compute_index_status`); stale or absent → `decouple_skipped` with the reason, no guessing
- [ ] 2.2 `search_similar` per added unit; `analyze_impact` per changed signature
- [ ] 2.3 Names go through `vise.core.livespec.LIVESPEC_TOOLS`; `test_livespec_contract.py` covers the new speaker

## 3. The node

- [ ] 3.1 `decouple` node in `feature-dev-graph.yaml` between `test` and `validate`, `enabled: false`
- [ ] 3.2 Entry edge gated on `tests_pass`; exit edge on `tests_pass` and `diff_scope`
- [ ] 3.3 Prompt injection: what the phase is for, the refusal list, and that a red test is reverted, not argued with
- [ ] 3.4 `test_asset_honesty` and `test_orchestration_skill_sync` updated for the node

## 4. Proving it

- [ ] 4.1 Run against three real repos with `VISE_DECOUPLE=report` (no moves) and keep the reports
- [ ] 4.2 Turn moves on only when the three reports show more accepted candidates than refusals that were wrong
- [ ] 4.3 CHANGELOG entry under **Behaviour change** when the node is enabled by default, not before

## Blocked, and why

- **3.1 cannot be written as specified.** `enabled: false` is not a field
  `graph_parser.py` reads. Nodes carry `mcps_enabled`, which is about tool
  surface, not about whether the node runs; there is no node-level off switch
  in the format. So adding a `decouple` node to `feature-dev-graph.yaml` puts
  it in the live path of every repo that runs `/feature`, which is exactly what
  3.1 was written to avoid. Three ways out, none of them free: teach the parser
  an `enabled` field (a change to a shipped asset format, for one caller);
  ship `decouple-graph.yaml` as its own workflow and hold it in
  `_INTENTIONALLY_UNROUTED` until it is proven (the allowlist exists and
  `test_unrouted_allowlist_stays_honest` keeps it from rotting); or leave
  section 3 until section 4 says the phase is worth shipping. Not decided here.
- **Section 4 needs three real repos.** It is the bar this change set itself,
  and nothing in this session can meet it.
