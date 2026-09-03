# Design

## Placement follows the existing split

`quality_profile.py` / `openspec_profile.py` set the precedent: the logic lives
in its own `engines/` module, and `validators.py` holds only a thin dataclass
that adapts it to `ValidatorRecord`. These gates follow it exactly.

| Module | Holds |
|---|---|
| `engines/design_tokens.py` | the static scanner — pure stdlib |
| `engines/render_harness.py` | the vendored browser harness |
| `engines/ui_checks.py` | geometry and contrast maths over harness output |
| `engines/validators.py` | three thin validators, registered in `VALIDATOR_TYPES` |

Recipes were considered and rejected: `node_gate.py` treats a non-empty recipe
plan as a failure, because a recipe returns a plan for the agent rather than
executing anything. A gate has to run.

## The harness is vendored, not depended on

`layoutlint`'s `browser.py` is ~200 lines, synchronous, and — the property that
makes this work — imports playwright *lazily inside* the functions that use it.
Module import is stdlib-only. Vendoring preserves that, which is what lets
playwright live in an optional extra.

Three changes on the way in:

1. **Hoist the browser launch.** The original launches a full browser per
   breakpoint — three launches for the default three widths. One launch, one
   page per width.
2. **Report unresolved selectors.** The original's injected JS does
   `if (!el) continue;`, so a selector that matches nothing never reaches the
   checks. Silent under-reporting is the failure mode this whole change exists
   to remove, so unresolved selectors become evidence.
3. **Extract colour.** The original's style set is
   `overflow, object-fit, box-sizing, position, z-index` — no colour at all.
   The contrast check needs `color` and `background-color`, plus an ancestor
   walk to find the nearest painted background. The walk is new JS; the extra
   properties ride the existing `extra_style_props` parameter.

## Contracts are generated, not written

`layoutlint` requires a hand-authored `components: [{id, selector}]` list per
page, minimum length one. That is why it works as a tool and fails as a gate —
an unattended gate cannot ask a human to describe the page first.

The generator emits one entry per candidate element with an `nth-child` path as
the selector, so every selector resolves to exactly one element. A class-based
rule would collapse eight repeated cards into one node and silently drop seven;
`querySelector` returns the first match only.

## Fail closed, and prove it without a browser

`QualityCheckValidator` skips on a missing binary — `passed=True`,
`source="asserted"`, `outcome="unverified"`. `CommandExitValidator` fails
closed, and `quality-gate-graph.yaml` documents why that difference matters.
These gates take the fail-closed side: `passed=False`, `source="mechanical"`,
evidence naming the install command.

The dependency check runs *before* any browser work, so the unavailable path is
a plain function returning a record. `layoutlint`'s `test_cli.py` proves its
exit-code mapping by monkeypatching the runner, with no browser present; the
same approach covers every failure path here. Only the fixture tests that
assert on real geometry need Chromium, and those skip — a skipped *test* is
normal, a skipped *gate* is the thing being outlawed.

## What was investigated and rejected

A required `verdict` argument on `graph_traverse`, ported from a sibling
project that has no validators at all. Reading `_graph_transition.py` killed
it: vise already runs a node's validators before evaluating any edge
condition, so the gate exists and `reason` is telemetry. Adding a verdict
string would have been ceremony on top of a working mechanism.

The finding that survived is narrower. `quality-gate`'s `static` node declares
`types`, `complexity` and `deps`, which ship unbound, and an unbound
`quality_check` returns `passed=True, outcome="unverified"`. The node can go
green having verified almost nothing — which is the case the code's own
telemetry comment calls "the interesting failure". These gates avoid it by
construction: fail-closed validator types, not named checks.

## The repo boundary is a trust boundary

A gate reads and renders whatever the repository hands it, which makes
`.vise/quality.yaml` and the project tree attacker-controlled input for anyone
who can land a change. Three consequences, all found by auditing and each
reproduced before the fix:

- **A symlink is not a file in this project.** `Path.is_file()` follows links,
  so a committed `theme.css` pointing outside the tree was read and its
  matched fragments reached the persisted evidence. Paths are resolved and
  checked for containment before any read (CWE-59).
- **A target is a page to load, never a page to author.** The harness treats
  anything that is not a URL as inline HTML and calls `set_content` on it, so a
  non-URL entry would execute repo-supplied markup and script in the browser on
  whatever machine runs the gate. Only `http://`, `https://` and `file://`
  reach a render; anything else is refused, and refused loudly rather than
  dropped, because a gate that quietly ignores half its configuration reports
  on less than it was asked to (CWE-918).
- **Unbounded reads and renders are a gate-level denial of service.** Files
  above 2 MB are skipped rather than truncated, and the render gates' declared
  timeout is now actually passed to every browser call — it was dead config.

## Thresholds

Contrast: 4.5:1, relaxed to 3.0:1 at ≥24px or ≥18.66px bold. These are WCAG
2.2 AA, not invented numbers.

Geometry tolerance: 1.0px, the vendored default. Sub-pixel rounding differs
between engines and a tighter value produces findings nobody can act on.

Token allowances are configured per project rather than fixed, because a
codebase adopting tokens gradually needs a ratchet, not a cliff. The default
allowance is zero literals outside token definitions; a project sets its
current number and lowers it, the same way the coverage floor works.
