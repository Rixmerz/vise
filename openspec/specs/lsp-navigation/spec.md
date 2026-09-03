# lsp-navigation Specification

## Purpose
TBD - created by archiving change active-lsp-and-honest-diagnostics. Update Purpose after archive.
## Requirements
### Requirement: The orchestrator resolves blast radius before dispatching a wave that changes a signature

The orchestration skill SHALL instruct the engineer to resolve the caller set of
any symbol whose public signature a wave will change, using the `LSP` tool, and
to enumerate that set in the brief of every subagent in the wave.

This belongs to the engineer, not the builders. A caller set decides how the
wave is partitioned by file ownership, and partitioning is a decision the
orchestration skill already reserves for the engineer.

#### Scenario: A wave changes a public function signature
- **WHEN** the engineer is about to dispatch a wave that alters the parameters
  or return type of a symbol used outside its own file
- **THEN** the skill directs it to resolve that symbol's references before
  dispatch
- **AND** to include the resulting caller list in each brief

#### Scenario: No language server is available for the file type
- **WHEN** the resolution is attempted and no server is configured for that
  language
- **THEN** the skill directs the engineer to proceed with a text search and to
  state in the brief that the caller list is unverified
- **AND** the absence of a server does not block the dispatch

#### Scenario: A wave changes no signatures
- **WHEN** a wave's work adds new code without altering an existing signature
- **THEN** no caller resolution is required

### Requirement: Code-touching agents can reach the LSP tool

Every shipped agent whose charter permits modifying or reviewing source code
SHALL list the `LSP` tool in its available tools.

An instruction to use a tool that is absent from the agent's allowlist is inert.
This requirement is what makes the trigger below reachable.

#### Scenario: An implementation agent
- **WHEN** a shipped agent's tools include Edit or Write for source files
- **THEN** its tools also include `LSP`

#### Scenario: A read-only review agent
- **WHEN** a shipped agent reviews or audits code without modifying it
- **THEN** its tools include `LSP`

#### Scenario: A non-code agent
- **WHEN** a shipped agent's charter covers only prose artifacts
- **THEN** it is not required to include `LSP`

### Requirement: The LSP trigger is a condition, not a suggestion

Each per-language rules skill SHALL state the circumstance that requires an LSP
lookup and the consequence of the result. It SHALL NOT phrase the instruction as
a discretionary suggestion such as using the tool when helpful.

A discretionary instruction reproduces the state this change exists to remove:
the tool is available, nothing says when to reach for it, and whether it gets
used comes down to the model's mood.

#### Scenario: The trigger fires on a signature change
- **WHEN** a rules skill describes changing a symbol used outside its file
- **THEN** it names the LSP operation to run
- **AND** states what the result obligates — that every caller either satisfies
  the new signature or is updated within the same change

#### Scenario: Discretionary phrasing is absent
- **WHEN** a rules skill mentions the LSP tool
- **THEN** it does not present the lookup as optional or as a matter of the
  agent's judgment

### Requirement: LSP usage is never itself a gate

No validator or node gate SHALL assert that an agent invoked the `LSP` tool.

Such an assertion is satisfiable by a single call with a discarded result, so it
would raise a measured number while measuring nothing. The outcome that matters
is asserted by the diagnostics half of this change.

#### Scenario: A proposed gate on tool invocation
- **WHEN** a workflow node is authored to require evidence of an LSP call
- **THEN** that requirement is rejected as unverifiable in substance

#### Scenario: The outcome is gated instead
- **WHEN** a signature change leaves a caller referring to the old signature
- **THEN** the diagnostics validator for that language reports the failure
