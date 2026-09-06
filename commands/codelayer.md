---
description: Turn the read-by-symbol layer on, off, or into warning mode; show what it would have denied
effort: low
---

Manage the CodeLayer gate for this session.

Read `$ARGUMENTS`. Accepted: `off`, `warn`, `enforce`, `status`, `warnings`.
With no argument, treat it as `status`.

**status** — report, in three lines:
- current `VISE_CODELAYER` value (or `off` if unset) and what that means
- whether the symbol layer can answer here. Run `vise neighbours`: it reads
  `.mcp-docs/docs.db` directly, so it answers whether the repo is indexed even
  when livespec is not mounted in this session — which is the case where asking
  a tool tells you nothing. Without an index the gate stands down on its own,
  and saying so is most of what this command is for.
- whether a debt baseline exists (`debt_baseline_status`, livespec's, which
  does need the MCP mounted)

**warn / enforce / off** — tell the user the exact line to add to
`.claude/settings.json` under `env`, e.g. `"VISE_CODELAYER": "warn"`, and that
it takes effect on restart. Do not edit their settings without being asked.

Recommend this order and say why in one line: `warn` first for a week, read
the false positives, then `enforce`. Going straight to `enforce` in an
unfamiliar repo is how the gate gets uninstalled on day two.

**warnings** — read `.vise/codelayer-warnings.jsonl` and report:
- how many reads it would have denied, grouped by tool
- the 10 most frequent paths
- your judgement on which of those look like **false positives** — a path the
  gate should not have caught (a config, a generated file, something outside
  the symbol index)

That judgement is the point of warning mode. A raw count tells the user
nothing; "3 of these 47 are wrong, and they are all generated protobuf stubs"
tells them whether to enforce and what to add to the scope exclusions.

If the file does not exist, say the gate has not run in warn mode yet.
