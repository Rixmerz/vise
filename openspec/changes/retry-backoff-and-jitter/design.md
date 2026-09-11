# Design

## Where the wait happens

Not in `_dispatch_ready`, and not in the worker. Sleeping at either point holds
the dispatch loop, so one task backing off would freeze every healthy task in
the run — trading a bounded failure for an unbounded one.

The task carries `not_before`, and `_dispatch_ready` skips it until then. The
main loop already waits in `WAIT_SLICE_S` slices; the only change it needs is to
keep waiting instead of concluding the run is stalled.

That last part is the trap. The loop reads:

```python
if not pending:
    if not dispatched:
        self._block_stalled(state, by_id)
    if state.is_done() or not dispatched:
        break
```

A task deferred for backoff dispatches nothing, so without a check the run would
mark it blocked and exit — the retry never happening, and the reason recorded
being wrong as well.

## Why `not_before` is wall clock

`time.monotonic()` is the right clock for measuring a duration and the wrong one
for a value that outlives the process. `RunState` is persisted and resumed; a
monotonic deadline read back after a restart is meaningless. Seconds since the
epoch survive the round trip, and a resume that happens after the delay has
passed correctly finds the task ready.

## Why equal jitter and not full jitter

Full jitter — `U(0, d)` — is the usual recommendation, and it decorrelates best.
It is wrong here. A task gets **one** environment retry. A draw near zero spends
that retry inside the same window the delay exists to clear, which is the defect
being fixed, and the run stops.

Equal jitter — `d/2 + U(0, d/2)` — keeps a guaranteed floor of half the delay
while still spreading N workers across a window. The floor is the property that
matters when there is no second chance.

## Why escalation is excluded

`recovery.decide` returns `escalate` when the work was attempted and wrong. The
next attempt runs a different, larger model against the same task. Nothing about
that is waiting for a transient condition, so a delay there buys wall clock and
nothing else.

## Purity

`recovery.py` states that everything in it is a pure function of a result and
its history, with no clock. `decide()` stays exactly that. `retry_delay_s()`
takes its random source as a parameter and defaults it, so the module reads no
global clock and a test reproduces any delay by passing a fixed draw.
