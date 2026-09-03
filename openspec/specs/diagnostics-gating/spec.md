# diagnostics-gating Specification

## Purpose
TBD - created by archiving change active-lsp-and-honest-diagnostics. Update Purpose after archive.
## Requirements
### Requirement: A pass earned by absent tooling is reported as unverified

The `lsp_clean` validator SHALL classify every pass as either **verified clean**
or **unverified**, and SHALL surface that classification in its validator
record. A pass that no checker contributed to SHALL NOT be presentable as a
clean pass.

The gate still opens on `unverified` — the fail-open contract is unchanged. What
changes is that the operator can tell the two apart.

#### Scenario: No checker is installed
- **WHEN** `lsp_clean` runs against a changed Python file
- **AND** neither `ruff` nor `mypy` is available on PATH or in the venv
- **THEN** the validator passes
- **AND** its record reports the outcome as unverified
- **AND** its evidence names the checkers it looked for and did not find

#### Scenario: A checker ran and found nothing
- **WHEN** `lsp_clean` runs against a changed Python file
- **AND** `ruff` is available and reports no blocking diagnostics
- **THEN** the validator passes
- **AND** its record reports the outcome as verified clean
- **AND** its evidence names the checkers that ran

#### Scenario: Nothing was in scope to check
- **WHEN** `lsp_clean` runs and no changed file matches a supported language
- **THEN** the validator passes
- **AND** its record reports the outcome as unverified
- **AND** its evidence distinguishes "no files in scope" from "no checker
  available", because the first means there was nothing to verify and the
  second means verification was attempted and unavailable

#### Scenario: The diagnostics engine raises
- **WHEN** the underlying diagnostics call raises an unexpected exception
- **THEN** the validator passes
- **AND** its record reports the outcome as unverified
- **AND** no exception propagates to the caller

#### Scenario: Unverified is visible where the decision is made
- **WHEN** a node gate completes with an unverified `lsp_clean` result
- **THEN** the unverified outcome appears in the gate's reported result
- **AND** is not confined to a log file or debug-only output

### Requirement: Blocking diagnostics are named per checker, never inherited from the tool

For every checker it supports, the diagnostics engine SHALL decide
error-versus-warning severity from an explicit vise-owned rule, and SHALL NOT
adopt a checker's own severity field for that decision.

Cosmetic findings are warnings. A validator that blocks a wave on style gets
switched off, which costs more than the style it enforced.

#### Scenario: A checker marks a cosmetic finding as an error
- **WHEN** a checker reports a style or unused-import finding with its own
  severity set to error
- **THEN** the diagnostics engine classifies that finding as a warning
- **AND** `lsp_clean` does not fail because of it

#### Scenario: A genuinely broken file blocks
- **WHEN** a changed file contains a syntax error or an undefined name
- **AND** a checker for that language is available
- **THEN** `lsp_clean` fails
- **AND** its evidence names the file, the line, and the diagnostic

### Requirement: Diagnostics cover the languages the workflows are used on

The diagnostics engine SHALL support checkers for Go, Rust, and TypeScript in
addition to Python, under the same fail-soft contract: an absent checker is
skipped, a checker error is skipped, and neither raises.

#### Scenario: A changed Go file with an available checker
- **WHEN** `lsp_clean` runs and a changed `.go` file is in scope
- **AND** `go` is available on PATH
- **THEN** the Go checker runs and its findings are classified
- **AND** the outcome is verified clean or failed, never unverified

#### Scenario: A mixed-language change with one checker missing
- **WHEN** the changed set contains both a `.py` and a `.go` file
- **AND** `ruff` is available but `go` is not
- **THEN** the validator reports which languages were verified and which were
  not
- **AND** an absent Go checker does not suppress the Python findings

#### Scenario: A whole-project checker is filtered to the changed set
- **WHEN** a checker can only run against a whole project rather than one file
- **THEN** its findings are filtered to the changed files before classification
- **AND** a diagnostic in an unchanged file does not fail the gate

### Requirement: Code-touching workflows validate diagnostics

Every shipped workflow whose phases modify source code SHALL carry the
`lsp_clean` validator on the node that gates exit from its implementation work.

#### Scenario: A workflow that edits code
- **WHEN** a shipped workflow contains a phase that permits Edit or Write on
  source files
- **THEN** that workflow declares `lsp_clean` on the node gating exit from
  that phase

#### Scenario: A read-only workflow
- **WHEN** a shipped workflow permits no source modification in any phase
- **THEN** it is not required to declare `lsp_clean`
