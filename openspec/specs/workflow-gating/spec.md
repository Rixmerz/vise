# workflow-gating Specification

## Purpose
What a workflow declares about a node — the tools it forbids, the checks it
runs — and the guarantee that a declaration is enforced rather than merely
recorded.

## Requirements

### Requirement: The enforcer reads the same block list the workflow declares

The PreToolUse enforcer SHALL resolve a node's `tools_blocked` to the same
value the graph parser resolves for that node, for every workflow vise ships.

#### Scenario: The bundled library agrees

- **WHEN** every bundled `*-graph.yaml` is read by both the enforcer's parser and `graph_parser.load_graph_from_file`
- **THEN** the two produce the same node ids and the same `tools_blocked` for each node

#### Scenario: A block list written inline

- **WHEN** a node declares `tools_blocked: ["Bash", "Write"]`
- **THEN** the enforcer blocks `Bash` and `Write` at that node

#### Scenario: A block list written as a nested sequence

- **WHEN** a node declares `tools_blocked:` followed by `- "Bash"` on the next line
- **THEN** the enforcer blocks `Bash` at that node

### Requirement: A node's tasks do not consume its restrictions

The enforcer SHALL attribute `tools_blocked` to the node that declares it,
regardless of whether the node also declares a `tasks:` list and regardless of
the order of the two keys.

#### Scenario: Tasks are declared before the block list

- **WHEN** a `dag` node declares `tasks:` and then `tools_blocked: ["Bash"]`
- **THEN** the enforcer blocks `Bash` at that node

#### Scenario: Task ids are not nodes

- **WHEN** a `dag` node declares tasks with their own ids
- **THEN** those ids do not appear as nodes in the enforcer's mapping

### Requirement: Prose cannot declare structure

The enforcer SHALL NOT interpret the contents of a block scalar as graph
structure.

#### Scenario: A prompt containing a YAML-shaped line

- **WHEN** a node's `prompt_injection: |` contains a line beginning `- id:`
- **THEN** no node by that id appears in the mapping and the declaring node keeps its own `tools_blocked`

### Requirement: The enforcer still fails open, audibly

The enforcer SHALL approve the tool call on any internal error, and SHALL
report the error on stderr rather than silently.

#### Scenario: The state file cannot be read

- **WHEN** the graph state file contains malformed JSON
- **THEN** the hook prints an approve decision on stdout and names the error on stderr

#### Scenario: No workflow is active

- **WHEN** no graph state exists for the project
- **THEN** the hook approves

### Requirement: A comment does not change what a value means

Both graph parsers SHALL resolve a scalar or sequence to the same value
whether or not it carries a trailing comment.

#### Scenario: A quoted block-list item with a comment

- **WHEN** a node declares `tools_blocked:` with the item `- "Bash"  # no shell`
- **THEN** both parsers resolve it to `Bash` and the enforcer blocks `Bash`

#### Scenario: An inline sequence with a comment

- **WHEN** a key is declared as `["a", "b"]  # only these`
- **THEN** it parses to a two-item list, not a string

#### Scenario: A hash that is content

- **WHEN** a value is `"#fff"`, `url#anchor`, or `"Phase # 1"`
- **THEN** the hash is preserved

### Requirement: Every run event the runtime emits is recorded

The cross-run telemetry log SHALL accept every event kind the runtime emits,
and SHALL declare no kind that nothing emits.

#### Scenario: A new event kind is added to the scheduler

- **WHEN** the runtime emits an event kind absent from the telemetry registry
- **THEN** the suite fails and names the unregistered kinds

#### Scenario: An event kind stops being emitted

- **WHEN** the telemetry registry declares a kind nothing emits
- **THEN** the suite fails and names it
