## ADDED Requirements

### Requirement: A run that writes requires a well-formed change

The runtime SHALL refuse to dispatch any task when the run contains at least
one task that writes and the project has no active OpenSpec change carrying a
proposal and well-formed spec deltas. The refusal SHALL happen before the first
dispatch, so a blocked run spends nothing.

#### Scenario: Project has not adopted OpenSpec
- **WHEN** a run contains a task with `writes: true`
- **AND** the project has no `openspec/` directory
- **THEN** the run refuses before dispatching anything
- **AND** its recorded cost is zero
- **AND** the reason names `openspec init`

#### Scenario: A change exists but carries no deltas
- **WHEN** `openspec/changes/add-auth/proposal.md` exists
- **AND** the change has no `specs/**/*.md` carrying a delta header
- **THEN** the run refuses
- **AND** the reason names the change and the missing delta header

#### Scenario: A requirement with no scenario blocks the run
- **WHEN** a change's delta spec declares `### Requirement: Solo` with no
  `#### Scenario:` beneath it
- **THEN** the run refuses
- **AND** the reason names the requirement `Solo`

#### Scenario: A well-formed change admits the run
- **WHEN** an active change carries a proposal and well-formed deltas
- **THEN** the gate passes
- **AND** the run dispatches normally

### Requirement: A read-only run is not gated

The runtime SHALL NOT apply the spec gate to a run in which no task writes. A
run that cannot change the working tree cannot change the system's contract,
and requiring a specification for it is ceremony rather than safety.

#### Scenario: Every task is read-only
- **WHEN** every task in a run declares `writes: false`
- **AND** the project has no `openspec/` directory
- **THEN** the gate does not block the run

### Requirement: Task completion is not required to start

The runtime SHALL NOT require a change's `tasks.md` checklist to be complete in
order to dispatch. The run is what completes those tasks; requiring them first
would gate work on its own output.

#### Scenario: A change with no ticked boxes still admits the run
- **WHEN** an active change is well-formed and its `tasks.md` shows `0/7`
- **THEN** the gate passes

### Requirement: The pinned change is the one checked

The runtime SHALL accept an optional change name. When given, only that change
satisfies the gate; when absent, any well-formed active change does.

#### Scenario: The pinned change is absent
- **WHEN** a run pins change `add-auth`
- **AND** the only active change is `add-billing`, well-formed
- **THEN** the run refuses
- **AND** the reason names `add-auth` as not found

### Requirement: The verdict is reachable without spending money

The runtime SHALL report the gate verdict from its planning surface, alongside
the other problems that prevent a run from starting.

#### Scenario: Plan reports the block
- **WHEN** a plan is produced for a writing run in a project with no
  `openspec/` root
- **THEN** the plan reports a problem naming the missing spec
- **AND** the plan reports that it will not run

### Requirement: The override is the one that already exists, and is recorded

The runtime SHALL honour `VISE_NODE_GATE_OVERRIDE=1` as the sole bypass, and
SHALL record that the gate was overridden in the run's state.

#### Scenario: Overridden run proceeds and says so
- **WHEN** a writing run starts in a project with no `openspec/` root
- **AND** `VISE_NODE_GATE_OVERRIDE=1` is set
- **THEN** the run dispatches
- **AND** its event log carries the override with the reason it bypassed

### Requirement: The gate never raises

The runtime spec gate SHALL degrade an unreadable planning tree to a refusal
with an accurate reason, and SHALL NOT propagate an exception.

#### Scenario: The changes directory is a file
- **WHEN** `openspec/changes` exists but is a regular file
- **THEN** the gate reports no well-formed change
- **AND** raises no exception
