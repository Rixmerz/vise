## ADDED Requirements

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
