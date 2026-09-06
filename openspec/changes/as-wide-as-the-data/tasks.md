## 1. The field

- [x] 1.1 `ForEach` on `graph_engine.Task` — `from_task`, `items`, `max_items` — default `None`
- [x] 1.2 The parser reads `for_each`; fails closed on a missing `from` or `items`, a non-positive cap, or a source outside the node; adds the source to `dependencies` when the author left it out
- [x] 1.3 `Graph.validate` refuses a source that is the task itself
- [x] 1.4 The builder round-trips it, and a graph without it is byte-identical
- [x] 1.5 Tests: parser, validate, builder, every bundled workflow still parses

## 2. Expansion in the scheduler

- [x] 2.1 `runtime/expand.py`: `items_from`, `children_of`, `collection` — pure, no I/O, `DEFAULT_MAX_ITEMS` with its reason
- [x] 2.2 The scheduler expands at first readiness and joins at second; `expanded`, `expansion_truncated`, `expansion_blocked`, `joined` registered in `_VALID_RUN_EVENTS`
- [x] 2.3 A missing key blocks with the reason; an empty list joins with zero; a cut is recorded
- [x] 2.4 The replanner is handed the live task list, children included
- [x] 2.5 Downstream briefs carry the children's artifacts and the collection
- [x] 2.6 Resume re-expands to the same ids from the recorded items and keeps succeeded children
- [x] 2.7 Tests, mock-driven, one per scenario in the delta spec, plus: a child routes as its template does
- [x] 2.8 Re-broken: each fix turns exactly the test that names it red

## 3. The plan says what it cannot know

- [x] 3.1 `PlannedTask.expands` (source, key, cap), rendered as a range with the source named
- [x] 3.2 `RunPlan.estimated_cost_ceiling_usd`; the note when the ceiling does not fit and the floor does
- [x] 3.3 `--json` carries both
- [x] 3.4 Tests

## 4. Research gathers per sub-question

- [x] 4.1 A `split` task in `gather` emits a `plan` artifact with `sub_questions`, read-only, research role
- [x] 4.2 A `per-question` task with `for_each: {from: split, items: sub_questions}`; the case against stays whole-question, because a refutation of the answer needs the whole question
- [x] 4.3 `scope`'s prompt no longer pretends its sub-questions reach the run; it says the run splits again and why
- [x] 4.4 A test parses the bundled graph and asserts the expansion; `test_asset_honesty` and `test_orchestration_skill_sync` stay green

## 5. Convergence

- [x] 5.1 `Until` on `Task` — `key`, `stable_for`, `max_rounds` — parsed, validated, round-tripped
- [x] 5.2 `seen`, `rounds` and `stable` on `TaskRecord`, persisted
- [x] 5.3 After a passing round: new against seen in code; re-dispatch or stop; `round` and `converged` events, registered in `_VALID_RUN_EVENTS`
- [x] 5.4 The next round's brief carries what was found, marked as not to be reported again
- [x] 5.5 A failed round takes the ladder and is not a quiet round
- [x] 5.6 Tests, one per scenario in the delta spec

## 6. The panel

- [x] 6.1 `verifiers` on `Task`, parsed, validated (≥ 1), round-tripped
- [x] 6.2 `LENSES` in `verify.py`; `verifier_brief(..., lens=)` appends the lens's instruction
- [x] 6.3 The scheduler dispatches `N`, collects all, decides by majority in code; each verdict its own artifact and its own ledger line under `task::verify[k]`, the decision under the task; `panel` event registered
- [x] 6.4 The plan prices a panel as `N` verifier runs — and, found while doing it, prices the *one* verifier it always omitted; `--no-verify` reaches the preview
- [x] 6.5 Tests, one per scenario in the delta spec

## 7. Docs

- [x] 7.1 `docs/scheduler.md`: § Expansion, § Convergence, § The panel; the task-state section says a template never runs
- [x] 7.2 `docs/worker-contract.md`: the `items` key a source emits, and what a child's brief carries
- [x] 7.3 README: the field list and one example
- [x] 7.4 CHANGELOG

## 8. Verification

- [x] 8.1 ruff clean
- [x] 8.2 Full suite green after `coverage combine`; floor holds
- [x] 8.3 Exercised against the bundled research graph with a mock worker, state on disk, read back by `vise runtime status` and `explain`: split → expanded (3) → per-question[1..3] → joined
