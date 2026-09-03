# agent-registry Specification

## Purpose
TBD - created by archiving change project-local-agents. Update Purpose after archive.
## Requirements
### Requirement: A project may staff roles the plugin does not ship

The runtime SHALL load agent charters from the project's `.vise/agents/`
directory in addition to the bundled fleet, so a role vise does not ship can be
staffed without forking it.

#### Scenario: A project adds a role nobody bundled
- **WHEN** `.vise/agents/researcher.md` declares role `research`
- **AND** no bundled agent takes that role
- **THEN** a task declaring `role: research` resolves to `researcher`

#### Scenario: A project with no agents directory is unchanged
- **WHEN** the project has no `.vise/agents/`
- **THEN** the registry contains exactly the bundled fleet

#### Scenario: An unreadable agents directory is not fatal
- **WHEN** `.vise/agents` exists but is a regular file
- **THEN** the registry contains the bundled fleet
- **AND** no exception is raised

### Requirement: A project charter shadows a bundled one, visibly

A project charter whose id matches a bundled agent SHALL replace it, and the
registry SHALL record that the replacement happened.

#### Scenario: Shadowing takes effect
- **WHEN** `.vise/agents/backend-python.md` exists
- **THEN** routing a `backend`/`python` task reaches the project's charter

#### Scenario: Shadowing is reported
- **WHEN** a project charter shadows a bundled agent
- **THEN** the registry reports that id as shadowed
- **AND** `vise runtime agents` marks it

### Requirement: Every charter meets the same bar

A charter SHALL be validated against the same invariants whether it is bundled
or project-local: a valid model, a valid effort, a valid colour, a description,
every `tools` entry resolving to a real tool, and every `skills:` reference
naming a skill that ships.

#### Scenario: A project charter naming a tool that does not exist is refused
- **WHEN** a project charter lists a tool no agent can call
- **THEN** it is not added to the registry
- **AND** the reason names the tool

#### Scenario: A project charter with no description is refused
- **WHEN** a project charter omits its description
- **THEN** it is not added to the registry

#### Scenario: The bundled fleet passes the same check
- **WHEN** every bundled charter is validated
- **THEN** none is refused

### Requirement: One bad charter costs one agent

A project charter that fails to load or validate SHALL be refused with its
reason, and SHALL NOT prevent the remaining agents from loading or a run from
starting.

#### Scenario: A malformed charter beside a valid one
- **WHEN** `.vise/agents/` holds one valid charter and one with no frontmatter
- **THEN** the valid one is in the registry
- **AND** the invalid one is reported as refused, with its reason
- **AND** no exception is raised

### Requirement: The origin of every agent is reportable

The runtime SHALL report, for each agent it can route to, whether it came from
the bundled fleet or from the project.

#### Scenario: Listing agents shows where each came from
- **WHEN** the agent list is produced for a project with local charters
- **THEN** each row states its origin

### Requirement: A priced role is staffable or declared unstaffed

Every role the model policy prices SHALL either resolve to exactly one bundled
agent, or be listed as project-supplied with a reason, so the policy table and
the fleet cannot drift apart unnoticed.

#### Scenario: A newly priced role with no agent fails the check
- **WHEN** the policy prices a role that no bundled agent takes
- **AND** that role is not listed as project-supplied
- **THEN** the honesty check fails, naming the role

#### Scenario: A role staffed only by projects is allowed when declared
- **WHEN** `research` is priced, has no bundled agent, and is listed as
  project-supplied
- **THEN** the honesty check passes
