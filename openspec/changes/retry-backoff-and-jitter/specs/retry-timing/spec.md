## ADDED Requirements

### Requirement: A retry waits before it is dispatched

When `recovery.decide` returns `retry`, the runtime SHALL NOT dispatch the next
attempt immediately. It SHALL record the earliest time the task may start again,
and the dispatch loop SHALL skip the task until that time has passed.

The delay SHALL grow exponentially in the number of retries the task has already
taken, and SHALL be capped so that no single wait can exceed the cap regardless
of the attempt count.

#### Scenario: An environment failure is retried

- **WHEN** a task fails with `ENVIRONMENT_BUG` and recovery returns `retry`
- **THEN** the task is not dispatched again until at least the base delay has
  passed, and the run records the delay it chose

#### Scenario: The delay is capped

- **WHEN** the retry count is large enough that the exponential would exceed the
  cap
- **THEN** the delay returned is the cap, not the exponential

#### Scenario: An escalation does not wait

- **WHEN** recovery returns `escalate` rather than `retry`
- **THEN** no delay is armed and the next attempt is dispatched as soon as it is
  admissible

### Requirement: Concurrent retries are decorrelated

The delay SHALL include a random component, so that tasks that failed together
do not retry together. The random component SHALL NOT be able to reduce the
delay below half of the exponential value for that attempt.

#### Scenario: Parallel tasks fail in the same window

- **WHEN** several tasks fail with the same environment failure at the same time
- **THEN** their retries are spread across a window rather than dispatched in
  the same instant

#### Scenario: Jitter cannot cancel the wait

- **WHEN** the random draw is at its lowest
- **THEN** the delay is still at least half the exponential value, so the retry
  cannot land back inside the window it is waiting out

### Requirement: A waiting retry does not stall the run or end it

A task waiting out its delay SHALL NOT block another task that is ready and
admissible. The run SHALL NOT be reported as stalled, and SHALL NOT finish,
while a task is only waiting for its delay to pass.

#### Scenario: A healthy task shares the run with a waiting one

- **WHEN** one task is waiting out a retry delay and another is ready
- **THEN** the ready task is dispatched without waiting for the first

#### Scenario: Nothing is running and one task is waiting

- **WHEN** no task is in flight and the only remaining work is a task waiting
  out its delay
- **THEN** the run waits for the delay rather than reporting the task blocked
  and finishing

#### Scenario: The wall-clock ceiling still ends the run

- **WHEN** a task is waiting out a delay and the run reaches its wall-clock
  ceiling
- **THEN** the run stops, as it would with the task in flight

### Requirement: The delay is reproducible from the record

The function that computes a delay SHALL take its source of randomness as a
parameter, so that a caller can reproduce any delay the runtime chose.
`recovery.decide` SHALL remain a pure function of the result and its history,
reading no clock and drawing no randomness.

#### Scenario: A fixed random source gives a fixed delay

- **WHEN** the delay is computed twice with the same retry count and the same
  random source
- **THEN** both calls return the same value

#### Scenario: The decision is unchanged

- **WHEN** `recovery.decide` is called for a failing task
- **THEN** its returned action, state and reason do not depend on any clock or
  random draw
