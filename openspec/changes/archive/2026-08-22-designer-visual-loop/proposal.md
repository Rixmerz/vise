# Close the designer's visual loop

## Why

The `designer` agent decides what a UI should look like and writes the brief the
implementer follows. It never sees the result. Measured on a real run: the
implementation of a designer brief shipped 9px of horizontal scroll at 375px,
and the `ui_layout` gate caught it — the designer did not, because it had no way
to look.

That is the asymmetry this change closes. **The one that decides cannot see; the
one that sees cannot decide.**

## What changes

A designer working from a brief can render a target and look at the result
inside its own turn, then correct the brief before handing off.

Two constraints shaped the approach, both discovered by reading rather than
assumed:

- **Claude Chrome is not reachable from a subagent.** The `designer` charter
  grants `Read, Write, Glob, Grep, Bash, Skill` — no `mcp__claude-in-chrome__*`
  tool. The loop has to run through `Bash`, and the image comes back through
  `Read`, which renders images.
- **Nothing has to be installed that is not already optional.** `render_harness`
  already drives Playwright for the render gates, and Playwright is already the
  `vise[design]` extra. The capture is a function beside the extraction, not a
  new subsystem.

## Impact

- New: `render_harness.screenshot()`, and a `vise shot` CLI subcommand over it.
- Changed: `skills/design-brief/SKILL.md` documents the loop; `agents/designer.md`
  names it.
- No new dependency. No change to any gate's verdict.
