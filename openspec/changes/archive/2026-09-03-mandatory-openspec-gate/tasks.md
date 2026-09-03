## 1. Structural reader

- [x] 1.1 Add `src/vise/engines/openspec_profile.py` with `openspec_root()` and `active_changes()`, never raising
- [x] 1.2 Parse delta headers, requirements, and orphan requirements (requirements with no scenario)
- [x] 1.3 Parse `tasks.md` checklist totals, treating zero boxes as not complete
- [x] 1.4 Strip fenced code blocks so documented syntax is not counted as declared
- [x] 1.5 Exclude `changes/archive/` and dot-directories from active changes
- [x] 1.6 Support the `VISE_OPENSPEC_ROOT` override

## 2. Validator

- [x] 2.1 Add `OpenSpecValidator` with the five `require` levels
- [x] 2.2 Fail closed on a missing root, naming `openspec init`
- [x] 2.3 Name the precise gap in evidence (missing files / missing header / orphan requirement / task progress)
- [x] 2.4 Support the optional `change:` field to pin one change by name
- [x] 2.5 Fail closed on an unrecognised or empty `require`
- [x] 2.6 Run `openspec validate --all --strict --json` for the `validated` level
- [x] 2.7 Skip-pass as `asserted` when the CLI is absent, and when it validated zero items
- [x] 2.8 Fall back to the exit code when the JSON payload is unparseable
- [x] 2.9 Register `openspec` in `_REGISTRY`

## 3. Workflows

- [x] 3.1 Add the `spec` node to `feature-dev-graph.yaml`, entered from `design`
- [x] 3.2 Make `spec-to-implement` a `validators_green` edge
- [x] 3.3 Gate `validate` on `tasks_complete` so `commit` is unreachable with unfinished tasks
- [x] 3.4 Point the `implement` and `commit` prompts at `tasks.md` and `openspec archive`
- [x] 3.5 Mirror the same shape in `migration-graph.yaml` (`spec` node, `bench` gated on `tasks_complete`)
- [x] 3.6 Confirm both graphs parse and `Graph.validate()` returns no errors

## 4. Adoption and docs

- [x] 4.1 Run `openspec init` on the vise repo
- [x] 4.2 Bind a `spec` check in `.vise/quality.yaml` for ad-hoc quality-gate runs
- [x] 4.3 Document the mandatory gate and `VISE_OPENSPEC_ROOT` in `README.md`
- [x] 4.4 Add the Step 0.5 policy section to `skills/orchestration/SKILL.md`
- [x] 4.5 Write this change proposal so the repo satisfies the gate it ships

## 5. Verification

- [x] 5.1 Add `src/vise/tests/test_openspec_validator.py` covering the reader and every require level
- [x] 5.2 Full suite green except the pre-existing `test_state_paths_collision` failure
- [x] 5.3 `ruff check .` clean
- [x] 5.4 `openspec validate --all --strict` passes on this repo
