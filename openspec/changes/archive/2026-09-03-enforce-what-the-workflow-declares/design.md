# Design

## Why not the real parser

The obvious fix is to delete the second parser and call the first one. It was
measured rather than argued:

```
intérprete solo              mediana 25.4 ms   max  29.8 ms
+ import yaml + safe_load    mediana 47.9 ms   max 513.2 ms
```

This hook runs before every tool call. Doubling its median and accepting a
half-second tail is not a trade a gate on the hot path gets to make. The
docstring's original reasoning stands; what was missing is that a light parser
still has to be a *correct* one.

## What the parser must model

Just enough YAML to answer one question — which tools does this node block —
and nothing more:

| Concern | Rule |
|---|---|
| Scope | Parse only inside the top-level `nodes:` key; stop at the next top-level key |
| Node identity | A node is `- id:` at the sequence indent directly under `nodes:` |
| Nesting | A `- id:` deeper than that indent is a task, not a node |
| Key ownership | `tools_blocked:` counts only at the current node's key indent |
| Value forms | Inline flow `["A", "B"]` and the block sequence that follows an empty value |
| Opaque regions | `key: \|` and `key: >` consume every following line more indented than the key, uninterpreted |

Everything else in the file is skipped rather than guessed at. A key the
parser does not recognise ends any list it was collecting, which is what it
already did and is still right.

## The test that matters

Two synthetic cases per failure mode are worth having, but they only prove the
shapes someone thought of. The load-bearing test is the comparison:

```
for every bundled *-graph.yaml:
    hand = parse_tools_blocked(text)
    real = {node.id: node.tools_blocked for node in load_graph_from_file(path)}
    assert hand == real
```

This is the check that found the bug, and it is the one that keeps the two
parsers honest as workflows are added — including the `dag`-shaped ones the
runtime is moving toward, which are exactly the shape that breaks today.

## Coverage

`hooks/graph_enforcer.py` read 34 %: the entire parser and the entire deny
path were uncovered, while the existing tests exercised only the escape-hatch
allowlist. The new tests reach the deny path by launching the hook as its own
process with a real graph and real state — the discipline `CLAUDE.md` states
for every hook, applied to the one that blocks the most.
