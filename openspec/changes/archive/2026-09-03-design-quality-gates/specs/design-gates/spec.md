# design-gates

## ADDED Requirements

### Requirement: Static design-token discipline is enforced

The system SHALL provide a `design_tokens` validator that scans a project's
UI source for design values written as literals where a declared token exists,
and fails when the count exceeds the configured allowance.

It SHALL require no external tool, so it can never be unavailable.

#### Scenario: A stray colour literal outside the token block fails the gate

- **GIVEN** a project whose stylesheet defines colour tokens in a `:root` block
- **AND** a component that writes `color: #3355ff` directly instead of a token
- **WHEN** the `design_tokens` validator runs
- **THEN** it returns `passed=False`
- **AND** the evidence names the file, the line, and the literal

#### Scenario: A declared type scale that is bypassed fails the gate

- **GIVEN** a project declaring six font-size tokens
- **AND** seventeen distinct arbitrary font-size values used in components
- **WHEN** the `design_tokens` validator runs
- **THEN** it returns `passed=False`
- **AND** the evidence reports how many declared tokens exist and how many
  arbitrary values were found

#### Scenario: A UI that declares no font-family fails the gate

- **GIVEN** a project with UI source and no `font-family` declaration anywhere
- **WHEN** the `design_tokens` validator runs
- **THEN** it returns `passed=False`
- **AND** the evidence states that shipping the framework default face is a
  decision not taken

#### Scenario: A disciplined project passes

- **GIVEN** a project whose colours all resolve through custom properties
- **AND** whose spacing values all fall on its declared scale
- **AND** which declares at least one `font-family`
- **WHEN** the `design_tokens` validator runs
- **THEN** it returns `passed=True` with `source="mechanical"`

#### Scenario: A project with no UI source is not judged

- **GIVEN** a project containing no stylesheet and no component files
- **WHEN** the `design_tokens` validator runs
- **THEN** it returns `passed=True`
- **AND** the evidence states that no UI source was found

### Requirement: Rendered layout defects are detected per breakpoint

The system SHALL provide a `ui_layout` validator that renders a target page at
each configured breakpoint and fails when an element overflows its container,
is clipped, collides with a sibling, or falls outside the document bounds.

Each finding SHALL carry the breakpoint and the pixel magnitude of the defect.

#### Scenario: An element colliding with a sibling fails the gate

- **GIVEN** a page where two elements overlap by 40 pixels at 1280px wide
- **WHEN** the `ui_layout` validator runs
- **THEN** it returns `passed=False`
- **AND** the evidence names both elements, the breakpoint, and the 40px delta

#### Scenario: A defect present only at one breakpoint is still caught

- **GIVEN** a page that lays out correctly at 1280px
- **AND** whose content overflows its container at 375px
- **WHEN** the `ui_layout` validator runs against both breakpoints
- **THEN** it returns `passed=False`
- **AND** the evidence names 375 as the breakpoint

#### Scenario: A clean page passes

- **GIVEN** a page with no overflow, clipping, collision, or off-document element
- **WHEN** the `ui_layout` validator runs
- **THEN** it returns `passed=True` with `source="mechanical"`

### Requirement: Rendered contrast is measured against the effective background

The system SHALL provide a `ui_contrast` validator that renders a target page
and, for every interactive and text-bearing element, measures the computed
foreground colour against the nearest ancestor that paints a non-transparent
background.

It SHALL evaluate the default, hover, and focus states, and SHALL apply the
WCAG threshold of 4.5:1, relaxed to 3.0:1 for text at least 24px, or at least
18.66px when bold.

#### Scenario: A control failing contrast only on hover fails the gate

- **GIVEN** a button whose default state measures 7:1
- **AND** whose hover state measures 2.1:1 against the same background
- **WHEN** the `ui_contrast` validator runs
- **THEN** it returns `passed=False`
- **AND** the evidence names the element, the `hover` state, and the ratio

#### Scenario: The effective background comes from an ancestor

- **GIVEN** an element with a transparent background inside a dark container
- **AND** whose foreground fails against the container's colour
- **WHEN** the `ui_contrast` validator runs
- **THEN** it returns `passed=False`
- **AND** the measured background is the container's, not the page default

#### Scenario: Large text uses the relaxed threshold

- **GIVEN** a 28px heading measuring 3.4:1 against its background
- **WHEN** the `ui_contrast` validator runs
- **THEN** that heading does not produce a finding

### Requirement: A gate that cannot run reports failure, never success

Every validator added by this capability SHALL return `passed=False` with
`source="mechanical"` when a dependency it needs is unavailable.

No validator added by this capability SHALL return `passed=True` with
`outcome="unverified"`.

#### Scenario: Playwright is not installed

- **GIVEN** a project where the `vise[design]` extra has not been installed
- **WHEN** the `ui_contrast` or `ui_layout` validator runs
- **THEN** it returns `passed=False`
- **AND** the evidence names the exact command that installs the extra

#### Scenario: Playwright is installed but the browser binary is missing

- **GIVEN** an environment where playwright imports but Chromium is absent
- **WHEN** a render validator runs
- **THEN** it returns `passed=False`
- **AND** the evidence names the browser install command

#### Scenario: The exit-code contract holds without a browser present

- **GIVEN** a test environment with no Chromium installed
- **WHEN** the validators' failure paths are exercised
- **THEN** every path is assertable without launching a browser

### Requirement: The render gates require no hand-authored selector contract

The system SHALL derive the set of elements to inspect from the rendered
document, and SHALL NOT require the user to write a per-page list of CSS
selectors before a render gate can run.

Any selector the system generates SHALL resolve to exactly one element.

#### Scenario: A page of repeated cards inspects every card

- **GIVEN** a page containing eight elements sharing one class
- **WHEN** a render validator derives its inspection set
- **THEN** all eight elements are inspected
- **AND** no generated selector matches more than one element

#### Scenario: A selector that resolves to nothing is reported, not skipped

- **GIVEN** an inspection set containing a selector that matches no element
- **WHEN** a render validator runs
- **THEN** the unresolved selector appears in the evidence
- **AND** it is not silently dropped

### Requirement: Installing vise does not install a browser

The core `vise` distribution SHALL NOT declare playwright as a dependency, and
no module imported at package-import time SHALL import playwright.

#### Scenario: A core install imports cleanly

- **GIVEN** an environment with vise installed and playwright absent
- **WHEN** every vise module is imported
- **THEN** no ImportError is raised

#### Scenario: The static gate works without the extra

- **GIVEN** an environment with vise installed and playwright absent
- **WHEN** the `design_tokens` validator runs
- **THEN** it produces a real pass or fail, not an unavailable result
