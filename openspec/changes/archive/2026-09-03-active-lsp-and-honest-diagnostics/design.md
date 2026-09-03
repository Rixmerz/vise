# Design

## Two capabilities, deliberately separate

| | Verification | Navigation |
|---|---|---|
| Question | "is this code broken?" | "what does this code touch?" |
| Mechanism | shell-out to per-language checkers | Claude Code's native `LSP` tool |
| Consumer | `lsp_clean` validator, `edit_feedback` hook | orchestrator brief, agent charters |
| Enforceable | yes — a gate can assert it | no — it is an input to judgment |

Conflating these is what produced the current state: a validator named after
LSP that never speaks LSP, and twelve declared servers no code path reaches.
The two halves below never share a mechanism.

## Verification: making fail-open legible

The fail-open contract stays. Blocking a wave because a repo does not use mypy
would be wrong, and a validator that raises on tooling bugs is worse than one
that passes. What changes is that the *reason* survives into the verdict.

Three outcomes, currently collapsed into one:

- **verified clean** — a checker ran, found no blocking diagnostics
- **unverified** — no checker available, or no changed files in scope
- **failed** — a checker ran and found blocking diagnostics

`unverified` still opens the gate. It must be visibly not a pass. The
distinction lives in the validator record, so it reaches both the gate output
and `graph_status`, not only a log file nobody reads.

A subtlety that decides the shape: "no changed files" and "no checker" are both
`unverified` but mean opposite things. The first says there was nothing to
check; the second says there was something to check and we could not. They stay
distinguishable in the evidence text even though they share an outcome.

### Per-language checkers

Each language gets a checker chosen for **fail-fast, no-project-setup**
operation, because the validator runs on a file list, not a build:

| Language | Checker | Blocking diagnostics |
|---|---|---|
| Python | `ruff` (+ `mypy`) | already implemented — the allowlist model |
| Go | `go vet` | all findings |
| Rust | `cargo check --message-format=json` | `level: error` |
| TypeScript | `tsc --noEmit` | all errors |

The existing ruff allowlist is the precedent worth preserving: ruff's own
`severity` field marks cosmetic lint as `error`, so vise classifies by an
explicit code allowlist instead of trusting the tool. Every checker added here
needs the same question answered — *which of this tool's findings mean the code
is actually broken* — and cosmetic findings must stay warnings, or `lsp_clean`
starts blocking waves on style and gets disabled.

Whole-project checkers (`cargo check`, `tsc`) cannot be pointed at one file.
They run once per project and their findings are filtered to the changed set,
which also means their timeout budget is the project's, not the file's.

## Navigation: briefing, not gating

The orchestrator holds the `LSP` tool; the fleet does not. That asymmetry is
the design, not an accident to fix: resolving a caller set once and passing it
down is cheaper than seventeen builders each re-deriving it, and it matches the
skill's existing rule that briefs are self-contained and thinking is never
delegated. A caller set *is* thinking — it is the blast radius that decides how
the wave is partitioned.

So the primary integration is a step in the orchestration skill, before
dispatch: when a wave will change a public signature, resolve its callers and
enumerate them in the brief.

Subagents still get the tool, for the case the brief cannot anticipate — a
builder discovering mid-edit that a symbol has implementations it was not told
about. The trigger in the rules skills is written as a **condition with a
consequence**, not a recommendation:

> Before changing a public signature, run `findReferences` on it. That caller
> list is the change's blast radius — every entry either compiles against the
> new signature or gets updated in this change.

Contrast with "use LSP when it would help", which is exactly the
agent-discretion non-behavior this change exists to remove.

### Why not gate on it

A validator can observe that an `LSP` call happened. It cannot observe that the
result changed the work, and satisfying it costs one call with a discarded
result. It would raise the measured number while lowering the real one. The
outcome that actually matters — no caller left compiling against an old
signature — is what the verification half already asserts.

## Ordering

The evidence fix lands first and alone. Every later item adds checkers or
consumers whose value depends on being able to tell a real pass from an absent
one; shipping them first would produce more gates with the same blind spot.

## Risks

- **New checkers block on cosmetic findings.** The allowlist question above is
  per-checker work; getting it wrong makes `lsp_clean` an obstacle and it gets
  removed from the workflows. Mitigation: default new findings to warning, and
  promote to blocking only with a named code.
- **`unverified` becomes wallpaper.** If most gates report unverified, the
  distinction stops carrying information. That is a true signal about the
  repo's tooling, not a reason to hide it — but it argues for the outcome being
  visible at the gate, where it is actionable, rather than only in a record.
- **Whole-project checkers blow the latency budget.** `tsc --noEmit` on a large
  project is slow. It stays out of `edit_feedback` (5s hook timeout) and runs
  only in the validator, which is a phase boundary and can afford it.
