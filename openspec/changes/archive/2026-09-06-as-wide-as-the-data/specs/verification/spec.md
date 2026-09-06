## ADDED Requirements

### Requirement: SUCCEEDED may require more than one opinion

The runtime SHALL, for a task declaring `verifiers: N`, brief `N` verifiers
from distinct lenses, none seeing another, and decide by majority in code.

#### Scenario: Three lenses, none shared

- **WHEN** a task declares `verifiers: 3` and its worker passes
- **THEN** three verifier briefs are dispatched, each with a different lens and none with another's answer

#### Scenario: A majority passes

- **WHEN** two of three verifiers pass and one fails
- **THEN** the task succeeds and all three verdicts are recorded as their own artifacts

#### Scenario: A majority fails

- **WHEN** two of three verifiers fail
- **THEN** the task escalates with the union of their reasons

#### Scenario: No majority either way

- **WHEN** one passes, one fails and one is inconclusive
- **THEN** the task is `BLOCKED`, because verifiers who could not decide have not decided

#### Scenario: The panel is priced

- **WHEN** a plan contains a task declaring `verifiers: 3`
- **THEN** its estimate includes three verifier runs

#### Scenario: One is the default

- **WHEN** a task declares no `verifiers`
- **THEN** exactly one verifier is dispatched, as before
