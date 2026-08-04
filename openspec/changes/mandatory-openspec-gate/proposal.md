## Why

vise enforces *how* work advances (phase gates, validator gates, tool blocking)
but never enforced *that the work was specified*. Both spec-worthy workflows —
`feature-dev` and `migration` — moved from design straight into implement on a
**phrase** edge: the agent said "design complete" and the gate opened. Nothing
had to exist on disk. The result is the failure mode phase workflows are
supposed to prevent, arriving one level up: a fully gated implementation of
something nobody wrote down.

Adopting OpenSpec as a convention would not have fixed this. A convention that
an agent can skip by saying a sentence is documentation, not a gate.

## What Changes

- A new `openspec` node-gate validator with five `require` levels
  (`structure`, `change`, `deltas`, `tasks_complete`, `validated`).
- A new `spec` phase in `feature-dev` and `migration`, entered from design and
  left only on `validators_green` — no phrase can open it.
- `tasks_complete` gates the last transition before the irreversible phase:
  `validate → commit` in feature-dev, `bench → apply` in migration.
- vise adopts OpenSpec itself (`openspec/`), so the repo is subject to the gate
  it ships.

The design constraint that shaped all of it: **the mandatory levels must not
depend on the `openspec` CLI.** `openspec` is a Node package; a gate that goes
red because a teammate has not run `npm i -g` teaches people to set
`VISE_NODE_GATE_OVERRIDE=1`, and an override habit is worse than no gate. So
levels 1–4 are answered by reading `openspec/` with stdlib string work and fail
closed, while level 5 shells out to `openspec validate --strict` and
skip-passes as `source="asserted"` when the CLI is absent. Losing the CLI costs
depth, never coverage.

## Capabilities

### New Capabilities
- `openspec-gating`: reading a repo's OpenSpec planning state and gating
  workflow phase transitions on it.

### Modified Capabilities
<!-- None. vise's existing capabilities are not yet spec'd in openspec/specs/;
     this change introduces the first one rather than modifying any. -->

## Impact

- New: `src/vise/engines/openspec_profile.py`, `src/vise/tests/test_openspec_validator.py`
- Modified: `src/vise/engines/validators.py` (new validator + registry key),
  `src/vise/assets/workflows/feature-dev-graph.yaml`,
  `src/vise/assets/workflows/migration-graph.yaml`,
  `skills/orchestration/SKILL.md`, `.vise/quality.yaml`, `README.md`
- Behavioural: any repo running `feature-dev` or `migration` without an
  `openspec/` root now blocks at the `spec` phase. This is intended and is the
  point of the change; the evidence names `openspec init` as the fix.
- No new runtime dependency. The `openspec` CLI is optional.
