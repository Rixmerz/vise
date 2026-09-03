## ADDED Requirements

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
