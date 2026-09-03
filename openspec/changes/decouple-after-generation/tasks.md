## 1. The phase, without the graph

- [ ] 1.1 `src/vise/runtime/decouple.py`: `candidates(diff, index) -> list[Candidate]`, pure, no subprocess
- [ ] 1.2 Refusal list lifted verbatim from `skills/codelayer/SKILL.md` "When NOT to decouple", each rule a named predicate
- [ ] 1.3 `refuse(candidate) -> str | None` returning the rule's name; a refusal is recorded, never silent
- [ ] 1.4 `decouple_report` dataclass: found / refused (with rule) / moved / reverted / cost
- [ ] 1.5 Tests: every refusal rule has a case that trips it and a case that does not

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
