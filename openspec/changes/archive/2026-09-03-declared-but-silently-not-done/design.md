# Design

## Comments: find the close, do not test the end

The broken rule was `startswith(q) and endswith(q)`. It is right only when
nothing follows the value, which is exactly the assumption a comment breaks.

The fix is to look for the closing delimiter instead:

| Value | Rule |
|---|---|
| starts with `"` or `'` | take up to the next matching quote |
| starts with `[` | take up to the first `]` |
| anything else | cut at the first whitespace-preceded `#` |

That ordering is what makes `"Phase # 1"` work: a quoted value is resolved by
its quotes before the `#` rule is ever consulted, so a hash inside quotes is
content. `#fff` and `url#anchor` survive because the unquoted rule requires
whitespace before the hash, which is the behaviour that was already there and
is unchanged.

The enforcer's `_scalar` gets the same three cases. It has to: the enforcer's
whole contract, pinned by
`test_the_hook_reads_the_same_restrictions_the_workflow_declares`, is that it
resolves a node's restrictions to what `graph_parser` resolves them to.

## The telemetry test

A regex over `src/vise/runtime/**/*.py` for `.emit("literal"` compared against
`_VALID_RUN_EVENTS`, asserted both ways.

A computed event kind would not be matched — and would be a bad idea for the
reason this test exists: nothing could check it. The scan is guarded by a test
that it finds a plausible number of events at all, because a doc-sync test
that cannot see its subject reports success.

## What this does not attempt

Neither parser is being made into a YAML implementation. `parse_value` handles
the shapes graph files actually use, and the enforcer handles less than that on
purpose — it runs before every tool call, and `import yaml` was measured at
double its median startup with a half-second tail. The rule above is three
cases, not a lexer.
