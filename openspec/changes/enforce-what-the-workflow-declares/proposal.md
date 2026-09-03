# The enforcer blocks what the workflow declares

## Why

`graph_enforcer.py` is the PreToolUse gate: it decides whether a tool call
runs, and it is the mechanism every bundled workflow's `tools_blocked` relies
on. It reads the graph with a hand-rolled ~25-line YAML parser, kept
deliberately stdlib-only because the hook runs before *every* tool call.

Keeping it light was right. Leaving it structurally blind was not. The parser
never learned about indentation, so it mis-reads three shapes that
`graph_parser.py` — the real parser, the one that decides what a workflow
*means* — accepts:

1. **The inline list form.** `tools_blocked: ["Bash", "Write"]` parses to `[]`.
   Measured against the real parser: it returns `['Bash', 'Write']`. So a
   workflow author writes a gate, vise accepts the workflow, and the gate
   blocks nothing.

2. **A node with tasks.** When a `dag` node's `tasks:` list comes before its
   `tools_blocked:`, the block list is attributed to the last task id and
   `blocked_map.get(node)` returns `[]`. Every `dag` node is exposed, and `dag`
   nodes are the direction the runtime is going.

3. **Block scalars.** A `prompt_injection: |` whose prose contains a line
   starting with `- id:` creates a phantom node and takes the real node's
   `tools_blocked` with it.

Each failure is silent and each fails **open**. That is the inverse of this
repo's rule — hooks fail open, gates fail closed — and worse than either,
because the workflow, the docs and `graph_status` all keep reporting a gate
that is not there.

Found by comparing the two parsers over the bundled library: `research-graph`
already yields four phantom nodes (`primary`, `secondary`, `against`,
`adjacent` — the `gather` node's task ids). No bundled workflow loses a real
block today, because none happens to use an affected shape. That is luck, and
luck is not a gate.

## What Changes

- `parse_tools_blocked` becomes indentation-aware, staying stdlib-only: it
  parses only inside `nodes:`, treats a `- id:` deeper than the node sequence
  as a task rather than a node, accepts `tools_blocked:` only at the current
  node's key indent, reads the inline flow form as well as the block list, and
  consumes block scalars without interpreting them.
- The suite pins the hook's parser against `graph_parser.load_graph_from_file`
  across every bundled workflow — node ids and per-node `tools_blocked`. That
  comparison is what found this, and it is what stops it recurring.
- The block-decision path is exercised by launching the hook as its own
  process, the way `CLAUDE.md` requires of every hook and the way this one's
  most load-bearing branch never was: it read 34 %, with the whole parser and
  the whole deny path uncovered.

## What Does Not Change

- The hook stays stdlib-only. Using `yaml.safe_load` would make the two
  parsers agree by construction and was measured instead of assumed: it takes
  the hook's median startup from 25 ms to 48 ms with a 513 ms tail, on the hot
  path of every tool call. The docstring's argument for a hand parser holds;
  only its implementation was incomplete.
- The hook still fails open on any error, and still says so on stderr.
- The allowlist, the deny message and the two output channels are untouched.

## Found here, deliberately not fixed here

A **trailing comment on a block-list item** — `- "Bash"  # no shell` — is
mishandled by *both* parsers. The graph loads, and neither side ends up
blocking `Bash`: `graph_parser` yields `'"Bash"'` with the quotes still on,
and the enforcer yields the whole line after the first quote. Measured, not
inferred.

It is left alone because it belongs to `graph_parser` first. This change's
contract is that the enforcer agrees with the real parser; teaching only the
enforcer to strip comments would make the two diverge again, and the test that
pins them would go red for the right reason on the wrong change. No bundled
workflow uses the shape today.

## Behaviour Change

A workflow that declared `tools_blocked` in an affected shape was not being
enforced. After this change it is. A repository relying — knowingly or not —
on a gate that never fired will start seeing it block. That is the gate doing
what its author wrote, but it is a change in observable behaviour and belongs
in the CHANGELOG under that heading.
