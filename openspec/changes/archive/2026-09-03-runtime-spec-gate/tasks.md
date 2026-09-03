## 1. The gate

- [x] 1.1 Add `src/vise/runtime/spec_gate.py` with a `SpecGateVerdict` dataclass and `check()`, never raising
- [x] 1.2 Answer from `openspec_profile` only — no CLI, no subprocess
- [x] 1.3 Default to the `deltas` level; name the precise gap in the reason
- [x] 1.4 Support pinning one change by name
- [x] 1.5 Honour `VISE_NODE_GATE_OVERRIDE=1` and report it as overridden rather than passed

## 2. Scheduler

- [x] 2.1 Ask the gate once, before the first dispatch, only when a task writes
- [x] 2.2 Block every task with the gate's reason and finalise without dispatching
- [x] 2.3 Emit `spec_gate_blocked` / `spec_gate_overridden` into the run's events
- [x] 2.4 Keep a run whose tasks all declare `writes: false` ungated

## 3. Planning surface

- [x] 3.1 Report the verdict as a plan problem, so `vise runtime plan` shows it
- [x] 3.2 Add `--change` to `vise runtime plan` and `vise runtime run`
- [x] 3.3 Carry the pin through the `run_plan` MCP tool

## 4. Docs

- [x] 4.1 Document the gate in `docs/scheduler.md` and `docs/agent-runtime.md`
- [x] 4.2 Correct the delegation claim in `skills/orchestration/SKILL.md` — it is true again, but for a new reason
- [x] 4.3 README: the runtime section states the gate and the override

## 5. Verification

- [x] 5.1 `src/vise/tests/test_runtime_spec_gate.py` covers every scenario in the delta
- [x] 5.2 A blocked run's recorded cost is zero, asserted
- [x] 5.3 `ruff check .` clean, full suite green, coverage floor held
