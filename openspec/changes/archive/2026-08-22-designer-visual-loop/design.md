# Design

## Why not Claude Chrome

The user's framing was that the designer should use Claude Chrome to feed back
on its own work. It cannot: the `designer` charter grants `Read, Write, Glob,
Grep, Bash, Skill`, and no `mcp__claude-in-chrome__*` tool is reachable from a
subagent at all. Building toward that framing would have produced a capability
the agent could never invoke.

`Bash` plus `Read` is the path that exists, and `Read` renders images, so the
loop closes without a browser tool.

## Why not a new subsystem

`render_harness` already launches Chromium for the gates and already carries the
`BrowserUnavailable` contract and the unavailable-message discipline. A capture
is one more thing to do with a page that is already open in code that already
exists. Playwright is already the `vise[design]` extra, so nothing new is
installed.

## Security

The scheme allowlist is not defensive boilerplate. `render_harness` treats any
non-URL target as inline HTML and calls `set_content` on it — the exact CWE-918
finding already fixed once in `design_profile._TARGET_RE`. A capture that
accepted a bare string would re-open it in a second place. The allowlist is
enforced before launch, so a refused target costs no browser.
