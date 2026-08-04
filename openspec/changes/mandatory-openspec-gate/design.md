## Context

The gate has to be mandatory and it has to be honest, and those pull in
opposite directions. `.vise/quality.yaml` states the repo's own position on
this: *"A gate that goes red for environment reasons is worse than no gate: it
teaches you to pass VISE_NODE_GATE_OVERRIDE=1."* The existing `quality_check`
validator resolves that tension by skip-passing every unbound check with
`source="asserted"` — honest, but the opposite of mandatory.

OpenSpec ships as a Node CLI (`@fission-ai/openspec`). Gating on it naively
gives one of two bad outcomes: hard-fail everywhere it is not installed, or
skip-pass and be advisory again.

## Goals / Non-Goals

**Goals**
- A gate that cannot be opened by an agent asserting the work is specified.
- A red gate that always means "the plan is missing", never "your machine is".
- Evidence precise enough to fix without reading the validator's source.

**Non-Goals**
- Retro-specifying vise's existing 49 tools into `openspec/specs/`. Specs grow
  as changes land; a wholesale backfill would be invention, not documentation.
- Path-scoped tool blocking. vise's `tools_blocked` is tool-name only, so the
  `spec` phase cannot physically prevent editing `src/`. The gate, not the
  block, is what makes the phase mandatory.
- New MCP tools. The agent reaches the CLI through Bash; vise's contribution is
  the gate.

## Decisions

### Split the check into a structural tier and a semantic tier

Levels 1–4 (`structure`, `change`, `deltas`, `tasks_complete`) are answered by
`openspec_profile.py` — stdlib path and regex work over files the repo already
owns. They run everywhere, always, and fail closed. Level 5 (`validated`)
shells out to the CLI and degrades to `source="asserted"` when it is missing.

This is what resolves the tension. The mandatory part depends on nothing
external; the part that depends on something external is not the mandatory
part. Losing the CLI narrows depth, never coverage.

**Alternative rejected:** binding `spec: ["openspec", "validate", ...]` in
`.vise/quality.yaml` and gating with `quality_check`. One line instead of a
module — but `quality_check` skip-passes when unbound, so on any machine
without the CLI the "mandatory" spec gate would silently pass. The binding is
still added for ad-hoc `quality-gate` runs, where advisory is the right mode.

### `validators_green`, not a phrase, on the spec exit edge

Every other design→implement edge in the bundled workflows is a phrase edge.
That is exactly the hole: "design complete" is something an agent says, not
something that is true. `validators_green` makes the edge condition a disk read.

### Treat a vacuous CLI pass as asserted, not mechanical

`openspec validate --all --strict` exits 0 against an empty `openspec/`. Taken
at face value that is a green spec gate for a repo with no specs. The validator
parses `--json` and, when `summary.totals.items == 0`, passes as
`source="asserted"` with evidence saying nothing was validated — so
`goal_complete` will not grade it as verified. The `deltas` level is what makes
content mandatory; `validated` only says the content that exists is well-formed.

### Task-completion gates live on the predecessor node

A node gate runs on traverse *out* of its node. `commit` and `apply` are
`is_end` and never transition, so validators declared there would never run.
`tasks_complete` therefore sits on `validate` (feature-dev) and `bench`
(migration).

### Strip fenced code before parsing deltas

A proposal that documents the delta syntax would otherwise be read as declaring
requirements — a well-formed change on vise's reading that the CLI then
rejects. Fence stripping keeps the two parsers agreeing.

### `debug` gets no spec phase

A fix that restores specified behaviour changes no contract. Requiring a
proposal for it is ceremony, and ceremony is what gets gates disabled. A fix
that *changes* behaviour is a feature and belongs in `feature-dev`.

## Risks / Trade-offs

- **Existing repos block on first `feature-dev` run.** Intended, and the
  evidence names `openspec init`. Repos that do not want it should not activate
  a workflow that carries the phase.
- **`spec` cannot stop code edits.** Mitigated by the gate, not eliminated.
  Path-scoped blocking would close it and is a plausible follow-up: it needs
  `graph_parser._parse_node`, the hand-rolled `parse_tools_blocked` in
  `graph_enforcer.py`, and the hook's block test to all learn about paths.
- **Two parsers for one format.** vise's structural reader and the CLI can
  drift. Bounded by keeping the reader structural — presence, headers, counts —
  and leaving semantics to the CLI.
