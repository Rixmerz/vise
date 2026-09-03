## ADDED Requirements

### Requirement: Decoupling is a phase after tests pass

The system SHALL offer a `decouple` phase that is entered only when the
`tests_pass` validator is green and exited only when `tests_pass` and
`diff_scope` are green. The phase SHALL NOT define a validator of its own.

#### Scenario: Tests are red

- **WHEN** the workflow reaches `decouple` and `tests_pass` is not green
- **THEN** the phase is not entered and the reason names `tests_pass`

#### Scenario: A move turns a test red

- **WHEN** a builder task moves a unit and `tests_pass` goes red
- **THEN** the move is reverted and recorded as `reverted` in the report

### Requirement: Refusals come from the codelayer skill

The phase SHALL apply the refusal rules in `skills/codelayer/SKILL.md`
("When NOT to decouple") before proposing any move, and SHALL record each
refusal with the rule that produced it.

#### Scenario: A unit under the size floor

- **WHEN** a candidate module is under the documented size floor
- **THEN** it is refused with the rule `size_floor` and no task is dispatched

#### Scenario: Fewer than three consumers

- **WHEN** a candidate abstraction has fewer than three consumers
- **THEN** it is refused with the rule `rule_of_three`

### Requirement: No index, no guessing

The phase SHALL do nothing when the symbol index is absent or stale, and SHALL
report `decouple_skipped` with the reason.

#### Scenario: livespec is not mounted

- **WHEN** `compute_index_status` is unavailable
- **THEN** the phase emits `decouple_skipped` and the workflow continues

### Requirement: The report is the evidence

The phase SHALL produce a `decouple_report` listing candidates found, refused
with rule, moved, reverted, and cost, so a person can judge whether the phase
earned its place before it is enabled by default.

#### Scenario: A run with only refusals

- **WHEN** every candidate is refused
- **THEN** the report lists each with its rule and reports zero moves and zero cost beyond the look
