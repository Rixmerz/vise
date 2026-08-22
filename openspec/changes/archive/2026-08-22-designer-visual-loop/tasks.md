# Tasks

- [x] Add `screenshot()` to `src/vise/engines/render_harness.py`, reusing the
      existing launch path and the `BrowserUnavailable` contract
- [x] Enforce the scheme allowlist before any browser launch
- [x] Add the `shot` subcommand in `src/vise/cli/` and wire it in `main.py`
- [x] Document the loop in `skills/design-brief/SKILL.md`
- [x] Name the loop in `agents/designer.md`
- [x] Tests: capture writes a PNG, width is honoured, non-URL refused, missing
      browser raises rather than writing a file
- [x] Full validation: ruff, suite, coverage floor
