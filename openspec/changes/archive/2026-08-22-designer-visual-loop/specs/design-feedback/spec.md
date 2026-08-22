# design-feedback

## ADDED Requirements

### Requirement: Render a target to an image file

The render harness SHALL capture a page to a PNG on disk, using the browser it
already drives for the render gates, so that an agent with `Bash` and `Read` can
look at what it designed.

#### Scenario: A local page is captured

- **WHEN** `screenshot()` is called with a `file://` target and an output path
- **THEN** a PNG is written at that path and the path is returned

#### Scenario: The viewport width is honoured

- **WHEN** `screenshot()` is called with a width of 375
- **THEN** the page is rendered at that width, so a mobile layout is captured
  as it renders on mobile and not as a scaled-down desktop layout

#### Scenario: No browser is available

- **WHEN** Playwright or Chromium is missing
- **THEN** `BrowserUnavailable` is raised carrying the whole remedy, matching
  what `browser_status()` already reports — never a partially written file

### Requirement: Only loadable schemes are rendered

The capture SHALL accept only `http://`, `https://` and `file://` targets, the
same allowlist the design profile applies to gate targets.

A target is a page to LOAD, never a page to author. The harness treats a
non-URL as inline HTML and calls `set_content` on it, so accepting an arbitrary
string would run repo-authored markup and script in the browser. CWE-918.

#### Scenario: A non-URL target is refused

- **WHEN** the capture is asked to render a string that is not one of the three
  allowed schemes
- **THEN** it is refused with a message naming what is allowed, and no browser
  is launched

### Requirement: The capture is reachable from a subagent

vise SHALL expose the capture as a `vise shot` CLI subcommand, because the
`designer` agent holds `Bash` but no browser tool, and a capability an agent
cannot invoke is not a capability.

#### Scenario: The designer captures and reads

- **WHEN** the designer runs `vise shot <target> --out <path>` and then reads
  that path
- **THEN** it sees the rendered page and can revise its brief against what
  actually rendered

#### Scenario: The command reports an unusable environment

- **WHEN** `vise shot` runs where no browser is installed
- **THEN** it exits non-zero and prints the full remedy, rather than writing a
  zero-byte file and exiting clean
