## 1. The field

- [ ] 1.1 `ForEach` on `graph_engine.Task` — `from_task`, `items`, `max_items` — default `None`
- [ ] 1.2 The parser reads `for_each`; fails closed on a missing `from` or `items`, a non-positive cap, or a source outside the node; adds the source to `dependencies` when the author left it out
- [ ] 1.3 `Graph.validate` refuses a source that is the task itself
- [ ] 1.4 The builder round-trips it, and a graph without it is byte-identical
- [ ] 1.5 Tests: parser, validate, builder, every bundled workflow still parses

## 2. Expansion in the scheduler

- [ ] 2.1 `runtime/expand.py`: `items_from`, `children_of`, `collection` — pure, no I/O, `DEFAULT_MAX_ITEMS` with its reason
- [ ] 2.2 The scheduler expands at first readiness and joins at second; `expanded`, `expansion_truncated`, `joined` registered in `_VALID_RUN_EVENTS`
- [ ] 2.3 A missing key blocks with the reason; an empty list joins with zero; a cut is recorded
- [ ] 2.4 The replanner is handed the live task list, children included
- [ ] 2.5 Downstream briefs carry the children's artifacts and the collection
- [ ] 2.6 Resume re-expands to the same ids and keeps succeeded children
- [ ] 2.7 Tests, mock-driven, one per scenario in the delta spec, plus: a child routes as its template does
- [ ] 2.8 Re-broken: each fix turns exactly the test that names it red

## 3. The plan says what it cannot know

- [ ] 3.1 `PlannedTask.expands` (source, key, cap), rendered as a range with the source named
- [ ] 3.2 `RunPlan.estimated_cost_ceiling_usd`; the note when the ceiling does not fit and the floor does
- [ ] 3.3 `--json` carries both
- [ ] 3.4 Tests

## 4. Research gathers per sub-question

- [ ] 4.1 A `split` task in `gather` emits a `plan` artifact with `sub_questions`, read-only, research role
- [ ] 4.2 A `per-question` task with `for_each: {from: split, items: sub_questions}`; the case against stays whole-question, because a refutation of the answer needs the whole question
- [ ] 4.3 `scope`'s prompt no longer pretends its sub-questions reach the run; it says the run splits again and why
- [ ] 4.4 A test parses the bundled graph and asserts the expansion; `test_asset_honesty` and `test_orchestration_skill_sync` stay green

## 5. Convergence

- [ ] 5.1 `Until` on `Task` — `key`, `stable_for`, `max_rounds` — parsed, validated, round-tripped
- [ ] 5.2 `seen` and `rounds` on `TaskRecord`, persisted
- [ ] 5.3 After a passing round: new against seen in code; re-dispatch or stop; `round` and `converged` events
- [ ] 5.4 The next round's brief carries what was found, marked as not to be reported again
- [ ] 5.5 A failed round takes the ladder and is not a quiet round
- [ ] 5.6 Tests, one per scenario in the delta spec

## 6. The panel

- [ ] 6.1 `verifiers` on `Task`, parsed, validated (≥ 1), round-tripped
- [ ] 6.2 `LENSES` in `verify.py`; `verifier_brief(..., lens=)` appends the lens's instruction
- [ ] 6.3 The scheduler dispatches `N`, collects all, decides by majority in code; each verdict its own artifact under `task::verify[k]`
- [ ] 6.4 The plan prices a panel as `N` verifier runs
- [ ] 6.5 Tests, one per scenario in the delta spec

## 7. Docs

- [ ] 7.1 `docs/scheduler.md`: § Expansion, § Convergence, § The panel; the task-state diagram gains the join
- [ ] 7.2 `docs/worker-contract.md`: the `items` key a source emits, and what a child's brief carries
- [ ] 7.3 README: the field list and one example
- [ ] 7.4 CHANGELOG

## 8. Verification

- [ ] 8.1 ruff clean
- [ ] 8.2 Full suite green after `coverage combine`; floor holds
- [ ] 8.3 Exercised against a real recorded run with a mock worker end to end: split → expand → join → synthesize
