## 1. The parser

- [x] 1.1 Rewrite `parse_tools_blocked` indentation-aware; stdlib-only, no new imports
- [x] 1.2 Parse only inside `nodes:`; stop at the next top-level key
- [x] 1.3 A `- id:` deeper than the node sequence indent is a task, not a node
- [x] 1.4 Accept `tools_blocked:` only at the current node's key indent
- [x] 1.5 Read the inline flow form `["A", "B"]` as well as the block sequence
- [x] 1.6 Consume block scalars (`|`, `>`) without interpreting their contents

## 2. The tests

- [x] 2.1 Pin the hook's parser against `load_graph_from_file` over every bundled workflow
- [x] 2.2 One case per failure mode: inline form, tasks-before-block, tasks-after-block, prose containing `- id:`
- [x] 2.3 Wildcard `*` and the edges section keep working
- [x] 2.4 Launch the hook as a process and assert the deny carries both channels and names the node
- [x] 2.5 Fail-open cases as processes: corrupt state, no state, enforcer disabled by config

## 3. Verification

- [x] 3.1 `ruff check . --exclude .claude` clean
- [x] 3.2 Full suite green with `coverage combine`; floor holds
- [x] 3.3 `hooks/graph_enforcer.py` coverage materially above 34 %
- [x] 3.4 Re-break each fix and confirm the named test goes red and no other — the old parser turns 10 tests red, including both process-level ones, so the bug was reachable through the real hook and not only the pure function
- [x] 3.5 CHANGELOG entry under **Behaviour change**

## 4. Found, not fixed here

- [x] 4.1 Trailing comments on block-list items are mishandled by both parsers; recorded in the proposal and CHANGELOG rather than half-fixed, because it belongs to `graph_parser` first
