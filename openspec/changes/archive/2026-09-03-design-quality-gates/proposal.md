# Design quality gates

## Why

vise's UI output was measured across three of its own orchestrated projects.
The result contradicted the assumed cause. It is not that the `frontend` agent
has no taste, and it is not the known "AI default look" — none of the four
default aesthetics appear anywhere except in one throwaway file that is
excluded from lint.

What the numbers show is narrower and more actionable: **tokens get declared
and then bypassed at the call site.**

| Project | Type tokens | Actual sizes used | Hardcoded colours vs tokens | Spacing |
|---|---|---|---|---|
| `notbusy/ui` | Tailwind scale | 7 | 0 : 30 | on scale |
| `SpeedRunners-landing` | 6 declared, used twice | **24** (17 arbitrary `text-[Npx]`) | 18 : 66 | on scale |
| `wrap` | 33 | 15 | **44 : 25** | 25 raw values, incl. 5/7/9/11px |

`SpeedRunners-landing` is the proof. It runs a CI guard that checks **hex
literals only** — and it has 18 stray hex in 40,000 lines, against 17 arbitrary
font sizes. The drift is absent exactly where a check covers it and present
everywhere else. `notbusy` is the control that rules out an aesthetic cause:
the same pipeline produced 80 tokens, zero hex outside `:root`, and every
spacing value on scale.

Two further defects were reported and confirmed as uncovered by anything vise
ships: controls whose foreground fails contrast against their effective
background, and elements that overflow or collide at some breakpoint.

Prose does not fix any of this. The `web-ui-rules` skill already says to put
colours in custom properties, and the projects that ignore it ignore it
silently. **A check that runs is the only mechanism that has demonstrably
worked in this codebase's own history.**

## What Changes

Three new gates, each a dedicated validator that **fails closed**:

- **`design_tokens`** — static, pure Python, no external tool. Scans source for
  raw colour, font-size, spacing, and radius literals used outside token
  definitions, and for a declared scale that is not actually used. Also fails a
  UI that declares no `font-family` at all: shipping the framework default is a
  decision not taken.
- **`ui_layout`** — renders the page and reports elements that overflow their
  container, clip, collide, or fall off the document, per breakpoint.
- **`ui_contrast`** — renders the page and measures computed foreground against
  the *effective* background (walking ancestors) in default, hover, and focus.

The render gates share one browser harness, vendored and adapted from
`layoutlint`.

Two behaviours are deliberate and both differ from the closest existing
precedent, `QualityCheckValidator`:

1. **Missing dependency is RED, never a skip.** `QualityCheckValidator` returns
   `passed=True, source="asserted", outcome="unverified"` when its binary is
   absent. These gates return `passed=False, source="mechanical"`, matching
   `CommandExitValidator`'s documented fail-closed convention. A gate that
   cannot run must not report success.
2. **Playwright is an optional extra, never a core dependency.** It appears
   only under `vise[design]`, imported lazily inside the functions that need
   it, so `pip install vise` never pulls a browser.

## Impact

- New optional extra `vise[design]`; core install unchanged.
- `quality-gate-graph.yaml` gains `design_tokens` on `static` and the two render
  gates on `integration`.
- A repo that wires a render gate without installing the extra will see that
  node go red with a message naming the exact install command. This is the
  intended behaviour, not a regression.
- No existing validator changes behaviour. No existing check is removed.

### Why not the capability route

`recipes/capabilities.py` already ships `validate.web.layout` and
`web.screenshot` unbound, intended for an external MCP. That route cannot
satisfy this proposal: `node_gate.py` treats a non-empty recipe plan as a
failure — "planned, not executed" — so a capability gives an agent a tool to
invoke, not a gate that blocks a transition. The measurement above is precisely
about the difference between the two. The capabilities stay unbound and
unchanged; these gates are a separate mechanism.

### Why a new dependency at all

No standard-library module renders a page. Contrast against an effective
background and per-breakpoint geometry are properties of the rendered document,
not of the source, and cannot be derived by static analysis. `design_tokens`,
which *can* be static, has no dependency at all.
