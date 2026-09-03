# openspec-gating Specification

## Purpose
TBD - created by archiving change mandatory-openspec-gate. Update Purpose after archive.
## Requirements
### Requirement: Structural planning state is readable without external tooling

vise SHALL determine a repository's OpenSpec planning state — the presence of a
root, the set of active changes, their artifacts, their spec deltas, and their
task progress — by reading files directly, without invoking the `openspec` CLI
or any other external process.

This is what allows the gate to be mandatory. A gate whose verdict depends on a
globally-installed Node package goes red for reasons unrelated to the work,
which trains users to bypass it.

#### Scenario: Planning state resolved with no CLI installed
- **WHEN** the `openspec` CLI is absent from PATH
- **AND** the repository has `openspec/changes/add-auth/` containing
  `proposal.md`, `tasks.md`, and `specs/auth/spec.md`
- **THEN** vise reports one active change named `add-auth`
- **AND** reports its proposal present, its deltas parsed, and its task counts

#### Scenario: Archived changes are excluded
- **WHEN** a change directory lives under `openspec/changes/archive/`
- **THEN** it is not reported as an active change

#### Scenario: Delta syntax shown inside a fenced code block is not counted
- **WHEN** a delta spec file contains `## ADDED Requirements` inside a fenced
  code block
- **THEN** that occurrence contributes no delta header and no requirement
- **AND** a file whose only delta syntax is fenced is not well-formed

#### Scenario: An unreadable planning tree degrades instead of raising
- **WHEN** `openspec/changes` exists but is a file rather than a directory
- **THEN** vise reports zero active changes
- **AND** raises no exception

### Requirement: Structural gate levels fail closed

The `openspec` validator SHALL fail — never skip — when a structural
requirement level is unsatisfied. The levels `structure`, `change`, `deltas`,
and `tasks_complete` are structural. Its evidence SHALL name the specific
missing artifact and the command that produces it.

#### Scenario: Repository has not adopted OpenSpec
- **WHEN** a node gates on any structural level
- **AND** the repository has no `openspec/` directory
- **THEN** the validator fails
- **AND** the evidence names `openspec init`

#### Scenario: A requirement carries no scenario
- **WHEN** a node gates on `deltas`
- **AND** a delta spec declares `### Requirement: Solo` with no
  `#### Scenario:` block beneath it
- **THEN** the validator fails
- **AND** the evidence names the requirement `Solo`

#### Scenario: An unrecognised require level fails closed
- **WHEN** a node declares `require` with a value outside the five known levels
- **THEN** the validator fails
- **AND** the evidence lists the valid levels

#### Scenario: A scaffolded but unwritten task list is not complete
- **WHEN** a node gates on `tasks_complete`
- **AND** the change's `tasks.md` contains zero checklist boxes
- **THEN** the validator fails

### Requirement: Strict validation degrades without inventing a verdict

The `validated` level SHALL invoke `openspec validate --all --strict` when the
CLI is available and SHALL record `source="mechanical"`. When the CLI is
unavailable, or when it reports that zero items existed to validate, the level
SHALL pass with `source="asserted"` so that completion grading does not treat
it as verified evidence.

#### Scenario: CLI absent
- **WHEN** the `openspec` CLI is not on PATH
- **THEN** the `validated` level passes with `source="asserted"`
- **AND** the evidence names the install command

#### Scenario: CLI reports an empty item set
- **WHEN** `openspec validate` exits 0 having validated zero items
- **THEN** the level passes with `source="asserted"`
- **AND** the evidence states that nothing was validated

#### Scenario: CLI reports an invalid change
- **WHEN** `openspec validate` reports one of two items invalid
- **THEN** the level fails with `source="mechanical"`
- **AND** the evidence quotes the CLI's error message and the failing item id

### Requirement: Contract-changing workflows cannot reach implementation unspecified

The `feature-dev` and `migration` workflows SHALL each contain a `spec` phase
positioned between design and implementation, whose outgoing edge condition is
`validators_green`. No phrase SHALL advance past it.

#### Scenario: Implementation is unreachable without a proposal
- **WHEN** `feature-dev` is active on the `spec` node
- **AND** no well-formed change exists under `openspec/changes/`
- **THEN** the transition to `implement` is blocked
- **AND** the block reports which artifact is missing

#### Scenario: Delegation does not bypass the phase
- **WHEN** the active node is `spec`
- **AND** work is dispatched to a subagent
- **THEN** the workflow does not advance, because the gate reads disk state
  rather than agent output

#### Scenario: Bug-fix workflows carry no spec phase
- **WHEN** the `debug` workflow is active
- **THEN** no OpenSpec gate applies, because restoring specified behaviour
  changes no contract

### Requirement: Irreversible phases are unreachable with unfinished tasks

The last transition before an irreversible phase SHALL gate on
`require: tasks_complete`, so that the change authorising the work is fully
discharged first. This applies to `validate → commit` in `feature-dev` and
`bench → apply` in `migration`.

#### Scenario: Unticked tasks block the commit phase
- **WHEN** `feature-dev` is active on `validate`
- **AND** the active change's `tasks.md` has one of two boxes ticked
- **THEN** the transition to `commit` is blocked
- **AND** the evidence reports `1/2 tasks`

#### Scenario: The gate lives on the transitioning node
- **WHEN** a workflow's final node is marked `is_end`
- **THEN** the task-completion gate is declared on its predecessor, because a
  node gate runs on traverse out and an end node never transitions
