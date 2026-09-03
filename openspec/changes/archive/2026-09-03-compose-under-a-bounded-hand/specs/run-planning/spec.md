## ADDED Requirements

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
