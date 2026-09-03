# run-planning Specification

## Purpose
How a run's plan is shaped before it dispatches, and what a finished run
says about the plan that should follow it.

## Requirements

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

### Requirement: A composed graph may not author its own gate

The graph builder SHALL refuse any validator that runs a command chosen by the
repository, and SHALL accept only validators that run vise's own logic.

#### Scenario: A composed node declares an arbitrary command

- **WHEN** `graph_builder_add_node` is called with a `command_exit` or `quality_check` validator
- **THEN** the call is refused, the node is not added, and the reason names the validator and why

#### Scenario: Patching a node cannot slip one past

- **WHEN** `graph_builder_update_node` sets a refused validator on an existing node
- **THEN** the call is refused and the node keeps its previous validators

#### Scenario: A list with one refused entry

- **WHEN** a validators list mixes an allowed and a refused entry
- **THEN** the whole call is refused rather than the allowed half being kept

#### Scenario: A validator is added to the registry

- **WHEN** a new validator exists in the registry and the builder policy names it in neither set
- **THEN** the suite fails, so allowing or refusing it is a decision someone makes

### Requirement: A finished run states what the next plan needs

The runtime SHALL produce, from a recorded run, the work already succeeded,
the work that did not with its reason and classification, the observations
that are about the plan rather than the work, and the validators a composed
node may declare.

#### Scenario: Work already paid for

- **WHEN** a run is read into a compose brief and two of its tasks succeeded
- **THEN** those task ids are listed as done and are not among the unfinished

#### Scenario: A failure that says the plan was wrong

- **WHEN** a task failed with a classification in `REPLAN_KINDS`
- **THEN** the brief states that the plan was wrong rather than the work

#### Scenario: A run with nothing left

- **WHEN** every task in the run succeeded
- **THEN** the brief reports that there is nothing to compose and the command exits 3

### Requirement: Composing does not dispatch

Producing a compose brief SHALL NOT start a run, activate a workflow, or
create any run state.

#### Scenario: The brief is read

- **WHEN** `vise runtime compose` is run against any recorded run
- **THEN** no run is dispatched and the output states that a person runs the composed plan
