# Retry backoff and jitter

## Why

A task classified `ENVIRONMENT_BUG` or returning `inconclusive` gets exactly one
retry at the same rung (`DEFAULT_MAX_ENV_RETRIES = 1`). That retry is dispatched
immediately.

Measured against the live scheduler with a worker that fails every dispatch the
way a rate limit does — six read-only tasks, `max_parallel=6`:

| | |
|---|---|
| gap between attempt 1 and attempt 2 | 2.5 ms min, 3.0 ms max |
| spread of the five retries across tasks | 1.1 ms |
| outcome | run halted, no task completed |

Two defects, and the second is the one that costs the run.

**The retry lands inside the condition it is retrying.** A rate limit, a
saturated API, a machine still finishing a `npm install` — none of them clear in
three milliseconds. The one retry the task is allowed is spent on a question
whose answer cannot have changed, and spending it is what sends the task to
`waiting_human`, which stops the whole run.

**Every parallel worker retries in the same millisecond.** N workers that were
rate-limited together retry together, which is the thundering herd that produced
the rate limit. Backoff alone does not fix this; identical delays stay identical.

## What changes

- A retry is deferred rather than dispatched: the task carries the earliest time
  it may start again, and the dispatch loop skips it until then.
- The delay is exponential in the retries already taken, capped, and jittered.
- Escalation is unaffected. A bigger model is not waiting for a condition to
  clear, and making it wait would buy nothing but wall clock.

## Impact

- Affected specs: `retry-timing` (new)
- Affected code: `runtime/recovery.py`, `runtime/scheduler.py`, `runtime/state.py`
- A run that hits a transient failure takes seconds longer and is more likely to
  finish. A run that hits a real failure is unchanged in every respect but when
  its second attempt starts.
