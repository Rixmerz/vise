## 1. Charter validation as a runtime function

- [x] 1.1 Add `validate_charter(spec) -> list[str]` to `registry.py` — model, effort, colour, description, tools resolve, skills ship
- [x] 1.2 Point `test_agents_and_skills.py` at it so the loader and the test cannot state different bars
- [x] 1.3 Assert the whole bundled fleet passes it

## 2. Project-local agents

- [x] 2.1 `AgentRegistry.for_project(project_dir)` — bundled, then `.vise/agents/*.md` over it
- [x] 2.2 Record shadowed ids; record refusals with their reason
- [x] 2.3 Never raise: a missing, unreadable, or malformed tree degrades to what loaded
- [x] 2.4 Track each agent's origin

## 3. Wiring

- [x] 3.1 Planner and scheduler build the registry from the run's project dir
- [x] 3.2 `vise runtime agents` shows origin and shadowing
- [x] 3.3 `--dir` on `vise runtime agents` keeps working

## 4. The drift check

- [x] 4.1 Test: every role in POLICY resolves in the bundled registry or is listed as project-supplied
- [x] 4.2 List the seven currently uncovered roles with the reason, so the gap is on the record

## 5. Docs

- [x] 5.1 README: `.vise/agents/`, the shared bar, shadowing, and that shadowing is not yet constrained to narrowing

## 6. Verification

- [x] 6.1 `test_project_agents.py` covers every scenario in the delta
- [x] 6.2 Each new test checked against the unfixed code, not only against the fix
- [x] 6.3 ruff clean, full suite green, coverage floor held
