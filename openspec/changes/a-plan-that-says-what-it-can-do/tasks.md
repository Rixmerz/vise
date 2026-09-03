## 1. The plan tells the truth about its width

- [x] 1.1 `RunPlan` gains `concurrency_ceiling`, `critical_path` and `notes`
- [x] 1.2 Compute the ceiling from raw dependency waves with the cap lifted, so it does not report the budget back
- [x] 1.3 Note when `max_parallel` exceeds the ceiling; never a problem, never a non-zero exit
- [x] 1.4 Render and `to_dict` carry all three

## 2. Resume

- [x] 2.1 `Scheduler.run` accepts `resume_from: RunState | None`
- [x] 2.2 `Scheduler.resume` keeps spend and replans, resets non-terminal records, clears the gate, releases stale reservations
- [x] 2.3 `vise runtime resume <run_id> [--graph]` re-resolves the node through workflow scope
- [x] 2.4 A run with nothing left to do reports it and dispatches nothing
- [x] 2.5 `--isolate`, `--no-verify` and `--change` restated on resume — the run's `SchedulerConfig` is not in `RunSpec`, so it cannot be recovered and must not be silently dropped

## 3. Tests

- [x] 3.1 Ceiling: chain, independent, ownership-overlap, read-only, single task
- [x] 3.2 The ledger workload's real shape yields ceiling 2 — the case that started this
- [x] 3.3 Over-declared parallelism is a note, `problems` empty, exit zero
- [x] 3.4 Resume keeps a succeeded task, re-runs a blocked one, clears the gate
- [x] 3.5 Resume preserves spend and releases stale reservations
- [x] 3.6 CLI resume: reports what it would retry, refuses an unfindable graph, says when there is nothing to do
- [x] 3.7 Re-broken — reading the ceiling off the narrowed waves fails exactly `test_the_ceiling_is_not_the_budget_reported_back`; forgetting the spend fails the two that name it; re-running succeeded work fails five

## 4. Verification

- [x] 4.1 ruff clean
- [x] 4.2 Full suite green with `coverage combine`; floor holds
- [x] 4.3 Re-break each fix and confirm the named test goes red
- [x] 4.4 CHANGELOG

## 5. Found while building this

- [x] 5.1 Telemetry dropped the new `resumed` event — `_VALID_RUN_EVENTS` must list every run event or the cross-run log skips it. Same defect class as `drained`/`drain_failed` earlier in this release; registered and noted in the CHANGELOG.
