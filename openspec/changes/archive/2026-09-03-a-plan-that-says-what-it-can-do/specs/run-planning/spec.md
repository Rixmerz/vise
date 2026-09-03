## ADDED Requirements

### Requirement: A plan reports the concurrency it can actually reach

The planner SHALL report the greatest number of tasks that can run at once
given the declared dependencies and ownership conflicts, and the length of the
longest dependency chain.

#### Scenario: A chain of dependent tasks

- **WHEN** every task depends on the one before it
- **THEN** the reported concurrency ceiling is 1 and the critical path equals the task count

#### Scenario: Independent tasks

- **WHEN** no task declares a dependency and none of their ownership overlaps
- **THEN** the reported concurrency ceiling equals the task count and the critical path is 1

#### Scenario: Tasks that overlap on ownership

- **WHEN** two independent tasks claim the same path
- **THEN** the concurrency ceiling counts them as one, because they can never run together

### Requirement: A budget that cannot be used is reported, not enforced

The planner SHALL add a note when the declared `max_parallel` exceeds the
concurrency ceiling, and SHALL NOT treat it as a problem.

#### Scenario: More lanes declared than the graph can use

- **WHEN** a plan declares `max_parallel: 3` for a graph whose ceiling is 2
- **THEN** the plan carries a note naming both numbers and `problems` stays empty

#### Scenario: The plan still runs

- **WHEN** the only observation about a plan is over-declared parallelism
- **THEN** `vise runtime plan` exits zero

## ADDED Requirements

### Requirement: A stopped run can be resumed

The runtime SHALL continue a run from its recorded state, keeping work that
succeeded and re-attempting everything that did not.

#### Scenario: A run parked for a person

- **WHEN** a run stopped with a human gate set and one task succeeded
- **THEN** resuming clears the gate, keeps the succeeded task's result, and returns the rest to pending

#### Scenario: Spend carries over

- **WHEN** a run that already spent money is resumed
- **THEN** the ledger's spend is preserved so the run's cost ceiling still binds

#### Scenario: A stale reservation is released

- **WHEN** a run stopped while a task's estimated cost was still reserved
- **THEN** resuming releases that reservation

#### Scenario: Resuming a run that finished

- **WHEN** every task in the recorded run succeeded
- **THEN** resuming reports there is nothing to do and dispatches nothing
