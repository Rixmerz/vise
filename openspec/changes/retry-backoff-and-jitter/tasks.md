# Tasks

## 1. Delay policy

- [x] 1.1 Add `RETRY_BASE_S`, `RETRY_CAP_S` and `retry_delay_s(retries_taken, *, rand)`
      to `runtime/recovery.py`, with the equal-jitter reasoning in the docstring
- [x] 1.2 Keep `decide()` free of the clock and of randomness

## 2. Carrying the deadline

- [x] 2.1 Add `not_before` to `TaskRecord` in `runtime/state.py`, in epoch seconds
- [x] 2.2 Round-trip it through `to_dict` / `from_dict` so a resume honours it

## 3. Dispatch

- [x] 3.1 Arm the delay in `scheduler._collect` and in the verification-rejection
      path, on `RETRY` only, never on `ESCALATE`
- [x] 3.2 Skip a task in `_dispatch_ready` while `not_before` is in the future,
      before the brief is built
- [x] 3.3 Make the main loop wait rather than report the run stalled
- [x] 3.4 Add `backoff_base_s` to `SchedulerConfig` so tests can drive it to zero

## 4. Tests

- [x] 4.1 Delay is exponential, capped, and floored at half
- [x] 4.2 A fixed random source reproduces a delay
- [x] 4.3 An escalation arms no delay
- [x] 4.4 A waiting task does not block a ready peer
- [x] 4.5 A run with only a waiting task does not finish early
- [x] 4.6 `not_before` survives a state round trip

## 5. Evidence

- [x] 5.1 Re-run the outage benchmark and record gap, spread and outcome
- [x] 5.2 Update CHANGELOG.md and CLAUDE.md
