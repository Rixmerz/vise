## 1. The bounded hand

- [x] 1.1 `BUILDER_VALIDATORS` — every validator that runs vise's own logic
- [x] 1.2 `BUILDER_VALIDATORS_EXCLUDED` carries the reason as data, not a comment
- [x] 1.3 `add_node` refuses; the node is not added and the id stays free
- [x] 1.4 `update_node` refuses, so the check cannot be walked around
- [x] 1.5 A mixed list is refused whole, never partially accepted

## 2. The brief

- [x] 2.1 `runtime/compose.py`: succeeded, unfinished with reason and classification, plan-level observations, lessons, spend
- [x] 2.2 A classification in `REPLAN_KINDS` is stated as the plan being wrong
- [x] 2.3 The allowed validators are stated so the composer is not guessing
- [x] 2.4 `vise runtime compose <run_id> [--json]`; exit 3 when there is nothing to compose

## 3. Tests

- [x] 3.1 Union coverage: every registry validator is allowed or excluded with a reason
- [x] 3.2 Exactly the two that run repo-chosen commands are the excluded set
- [x] 3.3 Each allowed validator is accepted; each refused one is named and explained
- [x] 3.4 `update_node` and mixed lists cannot slip one past
- [x] 3.5 The brief separates paid-for work from unfinished, keeps the classification when the result lost it, dedupes plan-level lines
- [x] 3.6 The command renders, speaks JSON, and exits 3 on a finished run
- [x] 3.7 Re-broken: removing the check fails six; allowing `command_exit` fails three; losing the succeeded split fails five

## 4. Verification

- [x] 4.1 ruff clean
- [x] 4.2 Full suite green with `coverage combine`; floor holds
- [x] 4.3 CHANGELOG

## 5. Decisions this records rather than makes

- [x] 5.1 Who may author a gate — settled as the mechanical allowlist above
- [x] 5.2 Whether a composed plan dispatches itself — settled as no; `run_start` stays absent and its test stands
