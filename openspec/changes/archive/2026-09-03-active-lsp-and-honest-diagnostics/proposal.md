# Make diagnostics honest, and put LSP navigation to work

## Why

vise declares language servers for 12 ecosystems in `plugin.json` and ships a
validator named `lsp_clean`. Neither does what the names suggest.

- **Nothing in vise references the declared servers.** They feed Claude Code's
  native `LSP` tool. No agent charter, skill, or command mentions that tool
  exists, and none of the 17 shipped agents lists `LSP` in its `tools:`
  frontmatter — so no subagent can call it even if it wanted to. The
  declaration is inert.
- **`lsp_clean` does not use LSP.** It shells out to ruff and mypy; its own
  module docstring says so. It covers `.py` only, appears on exactly one node
  of one workflow out of nine, and fails open on every axis.
- **Fail-open is currently indistinguishable from success.** "Passed because
  the file is clean" and "passed because no checker was installed" produce the
  same verdict at the gate. On the machine this was found on, `ruff` is absent
  from PATH, so the repo's only mechanical code gate passes vacuously — a
  declared gate that verifies nothing.

The last point is the load-bearing one. It is the same failure that hid a
broken MCP server for an entire release: a missing tool looked exactly like a
working one. Extending coverage before fixing it would multiply vacuous passes
instead of catching anything.

There is also a capability gap worth being precise about: the native `LSP` tool
exposes nine operations and **none of them is diagnostics**. It is navigation
(`findReferences`, `incomingCalls`, `goToDefinition`, `hover`, …). So LSP cannot
extend error gating to other languages — that requires per-language checkers —
and conversely, per-language checkers cannot tell an agent who calls the
function it is about to change. The two capabilities are complementary, not
substitutes, and this change treats them separately.

## What Changes

1. **Diagnostics evidence stops lying.** A pass from `lsp_clean` records
   *why* it passed. A pass earned by absent tooling is reported as
   unverified and is visibly distinct from a clean pass at the gate.
2. **LSP navigation becomes a briefing input.** The orchestrator resolves the
   caller set of any symbol whose signature a wave will change, and puts that
   set in the subagent's brief. The blast radius is computed once by the agent
   that has the tool, not re-derived by each builder with `Grep`.
3. **Subagents can reach LSP, under a stated condition.** Code-touching agents
   gain the `LSP` tool, and the per-language rules skills gain a concrete
   trigger — a condition that fires, not an invitation to use it when it seems
   useful.
4. **Diagnostics coverage extends past Python.** `lsp_diagnostics` grows
   per-language checkers behind the same fail-soft shell-out contract, and
   `lsp_clean` is added to the validation nodes of the workflows that touch
   code.

## Non-goals

- **An LSP client inside vise.** Speaking the protocol directly was already
  tried and abandoned (`multilspy`, dropped for reporting syntax errors only),
  it needs a long-lived stateful session in a codebase built on stateless
  bounded shell-outs, and a cold start for `jdtls` or `rust-analyzer` exceeds
  the 5-second hook timeout by orders of magnitude. Navigation stays with the
  agent-facing tool.
- **Gating on whether an agent called LSP.** Tool usage is process theater:
  satisfiable by one throwaway call, and it measures nothing about the result.
  Gates assert outcomes.
- **Renaming `lsp_clean` / `lsp_diagnostics`.** The names are wrong — they
  describe no LSP involvement — but the validator type is referenced by
  workflow YAML that users may have copied. Correcting the documentation is in
  scope; a rename with an alias is a separate change.
- **Installing language servers or linters.** Both stay the operator's choice;
  the point of this change is that their absence becomes visible rather than
  silent.
