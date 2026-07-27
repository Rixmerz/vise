# vise

Phase-gated workflows, cross-project experience memory, and git snapshots for Claude Code — as a plugin.

[![status: alpha](https://img.shields.io/badge/status-alpha-orange)]() [![python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)]() [![license: MIT](https://img.shields.io/badge/license-MIT-green)]()

vise is a Python MCP server + hook suite that gives Claude Code sessions structure and memory: workflows are enforced as directed graphs of phases, learnings persist across projects, and every edit cycle is snapshotted for instant rollback.

## Features

- **Phase-gated workflow enforcer** — workflows are directed graphs; each node can inject phase-specific prompts, enable/block tools (e.g. no Edit/Write during a "think" phase), and hold transitions behind per-node validator gates until declared checks pass. 9 bundled workflows (feature-dev, debug, PR review, release, security audit, DB migration, …) plus a `graph_builder_*` API to author your own.
- **Cross-project experience memory** — learnings recorded per file/topic, semantically indexed (fastembed) with FSRS-style retrievability decay. Hooks inject relevant past learnings when you edit a file; `experience_*` tools query them on demand.
- **Git snapshots** — automatic orphan-ref snapshots (`refs/vise/snapshots/<id>`) after every edit cycle (30 s throttle). Restore any snapshot without touching your branch or reflog.
- **Goals & gates** — `goal_*` tools plus a Stop hook that blocks ending the turn with an unfinished active goal.
- **Recipes & capabilities** — declarative multi-step recipes (`recipe_run`) with capability bindings that survive MCP renames.
- **Agent autoheal skill** — bundled skill for recovering stuck agent loops (hot/cold two-path protocol).

The MCP surface exposes **50 tools**: `graph_*`, `experience_*`, `snapshot_*`, `goal_*`, `recipe_*`, `capability_*`, `vise_version`, …

## Install

Requirements: Claude Code (`claude` CLI), Python 3.11+, git.

```sh
git clone https://github.com/Rixmerz/vise && cd vise
./install.sh
```

`install.sh` checks for the `claude` CLI, provisions runtime deps (a dedicated venv under the vise data dir — `$XDG_DATA_HOME/vise/venv`, falling back to `~/.local/share/vise/venv` — if system `python3` lacks `fastmcp`/`fastembed`), registers the repo as a local plugin marketplace, and installs the plugin (`claude plugin marketplace add <repo>` + `claude plugin install vise@vise`). Idempotent — safe to re-run. Restart Claude Code afterwards.

The MCP server and all hooks run through `bin/vise-run`, a launcher that prefers the vise venv's python and falls back to `python3`. No installed wheel required for plugin usage.

For standalone (non-plugin) usage, install the package and use the `vise-mcp` console script:

```sh
uv venv && uv pip install -e .
```

## Quick start

Inside a Claude Code session with vise loaded:

1. **Activate a workflow** — ask for a feature; the `workflow_suggester` hook proposes one, or call `graph_activate(graph_id="feature-dev-graph")`. `graph_list_available` shows all 9 bundled workflows.
2. **Work the phases** — `graph_traverse` advances between nodes. The enforcer blocks tools the current phase forbids; validator gates (tests, lint, capabilities) must pass before a gated transition.
3. **Roll back** — `snapshot_list` then `snapshot_restore(snap_id=...)` to undo an edit cycle without `git reset`.
4. **Recover a stuck loop** — the bundled `agent-autoheal` skill walks a hot/cold recovery protocol.

> Note: vise's MCP tools take a `project_dir` argument — pass the absolute project root on the first call of a session; it is remembered and later calls can omit it.

## How it works

vise wires into Claude Code through `hooks/hooks.json`:

| Event | Matcher | Hook | Does |
|---|---|---|---|
| UserPromptSubmit | `*` | `workflow_suggester.py` | Suggests activating a workflow for task-shaped prompts |
| PreToolUse | `*` | `graph_enforcer.py` | Blocks tools the active phase forbids (fail-open) |
| PreToolUse | `Edit\|Write` | `experience_injector.py` | Injects past learnings for the touched file |
| PostToolUse | `Edit\|Write\|MultiEdit`, `Bash` | `snapshot_trigger.py` | Captures a git snapshot (30 s throttle) |
| PostToolUse | `Bash` | `experience_recorder.py` | Records learnings from commit `Why:` messages |
| PostToolUse | `mcp__.*__graph_traverse` | `workflow_post_traverse.py` | Post-phase feedback |
| PostToolUse | `mcp__.*__(graph_reset\|graph_activate)` | `workflow_override_detector.py` | Detects workflow overrides |
| Stop | `*` | `goal_gate.py` | Blocks ending the turn with an unfinished active goal |

All hooks fail open: on any internal error they exit 0 and never block the session.

## Architecture

```
src/vise/
├── server.py      # FastMCP stdio server (50 tools)
├── engines/       # graph engine, experience memory + FSRS, goal gate,
│                  # validators, snapshots, telemetry
├── tools/         # MCP tool surfaces (graph, experience, goal, snapshot,
│                  # recipes, bootstrap)
├── hooks/         # Claude Code hook entry points (see table above)
├── assets/        # bundled workflows (9), recipes (11)
├── core/          # embeddings, session, paths, git snapshot plumbing
└── cli/           # `vise` CLI (graph/experience management offline)
```

## Configuration

Environment variables (all optional):

| Variable | Purpose |
|---|---|
| `VISE_GOAL_DIR` | Override goal-state directory |
| `VISE_GOAL_GATE` | Enable/disable the Stop-hook goal gate |
| `VISE_GOAL_GATE_OVERRIDE` | One-shot bypass of the goal gate |
| `VISE_GOAL_GATE_MAX_ATTEMPTS` / `VISE_GOAL_GATE_PLATEAU_WINDOW` | Gate retry/plateau tuning |
| `VISE_AUTO_ACTIVATE` | Auto-activate suggested workflows |
| `VISE_WORKFLOW_SUGGEST` | Toggle the workflow suggester hook |
| `VISE_NODE_GATE_OVERRIDE` | One-shot bypass of a node validator gate |
| `VISE_AUTONOMY` | Autonomy level for loop recipes |
| `VISE_LOOP_COST_CAP` | Cost cap for loop recipes |
| `VISE_EMBED_MODEL` / `VISE_EMBED_IDLE_TIMEOUT` | fastembed model + idle unload |
| `VISE_TELEMETRY_DIR` / `VISE_USAGE_DIR` | Telemetry/usage output dirs |
| `VISE_JUDGE_CMD` | External judge command for AI validators |

## LSP servers

`.claude-plugin/plugin.json` declares `lspServers` — Claude Code's own LSP
client reads this map (extension → server) to give agents `hover` /
`documentSymbol` / `findReferences` / `incomingCalls` on your source. vise
does **not** install these toolchains; it only declares which binary to
launch per file extension. Install what you need:

| Server | Extensions | Install |
|---|---|---|
| `clangd` | `.c .h .cpp .cc .cxx .hpp .hxx` | `apt install clangd` / `brew install llvm` |
| `csharp-ls` | `.cs` | `dotnet tool install -g csharp-ls` |
| `gopls` | `.go` | `go install golang.org/x/tools/gopls@latest` |
| `intelephense` | `.php` | `npm install -g intelephense` |
| `jdtls` | `.java` | https://github.com/eclipse-jdtls/eclipse.jdt.ls |
| `kotlin-lsp` | `.kt .kts` | https://github.com/Kotlin/kotlin-lsp |
| `lua` | `.lua` | https://github.com/LuaLS/lua-language-server |
| `pyright` | `.py .pyi` | `npm install -g pyright` (or `pip install pyright`) |
| `ruby-lsp` | `.rb .rake .gemspec .ru .erb` | `gem install ruby-lsp` |
| `rust-analyzer` | `.rs` | `rustup component add rust-analyzer` |
| `sourcekit-lsp` | `.swift` | bundled with the Swift toolchain |
| `typescript` | `.ts .tsx .js .jsx .mts .cts .mjs .cjs` | `npm install -g typescript-language-server typescript` |

Run `vise doctor` to see which of these actually resolve on `PATH` on your
machine, plus the status of vise's own `ruff`/`mypy` diagnostics shell-out
and any pending XDG state migration.

### Deno projects — opt-in, and why it isn't the default

A Deno project (`deno.json`/`deno.jsonc` at the root, no `node_modules`)
fails **every** LSP call under `typescript-language-server`. That server
hard-requires a `typescript` package in the workspace's `node_modules`,
which a Deno project never has — deps are `jsr:`/`npm:` specifiers in
`deno.json`. The failure is total, not degraded: even `documentSymbol`,
which resolves no imports, dies at `initialize`:

```
Could not find a valid TypeScript installation. Please ensure that the
"typescript" dependency is installed in the workspace ... Exiting.
```

`deno lsp` is the correct server, and it works — verified end-to-end
through this plugin (`documentSymbol`, `findReferences`, `incomingCalls`
all returned correct results against clangd on the same wiring, so the
plugin → tool → server → handshake path is sound).

**It is not enabled by default, deliberately.** `deno` would have to claim
`.ts .tsx .js .jsx .mts` — every one of which `typescript` already claims.
The manifest schema (read out of Claude Code's own LSP Zod schema:
`command`, `args`, `extensionToLanguage`, `transport`, `env`,
`initializationOptions`, `settings`, `workspaceFolder`, `startupTimeout`,
plus `lspServers` accepting a record, a `.lsp.json` path, or an array of
either) has **no priority field, no workspace-root marker, and no
project-level override** — `lspServers` is plugin-scoped only. So which
server wins an extension both claim is **undetermined**, and shipping the
collision would make `.ts` behavior a coin flip for every user, including
Node users for whom it works today. A deterministic opt-in beats a
nondeterministic default.

To switch a machine over to Deno, add this to `lspServers` in
`.claude-plugin/plugin.json` **and remove the same five extensions from the
`typescript` entry** so exactly one server owns them:

```jsonc
"deno": {
  "command": "deno",
  "args": ["lsp"],
  "extensionToLanguage": {
    ".ts": "typescript", ".tsx": "typescriptreact",
    ".js": "javascript", ".jsx": "javascriptreact", ".mts": "typescript"
  }
}
```

`vise doctor` detects this case and prints the block for you. **Restart
Claude Code afterwards** — the LSP server map is read once at session
start, so editing it mid-session has no effect (verified).

This stops being a manual step if Claude Code ever ships per-project
`lspServers` resolution; the tie-break signal is trivial (`deno.json` ⇒
Deno, `package.json` ⇒ Node).

## Development

```sh
uv venv && uv pip install -e '.[dev]'
python -m pytest src/vise/tests/ -q
ruff check src/
```

Or, to reuse the plugin venv (`$XDG_DATA_HOME/vise/venv`, falling back to `~/.local/share/vise/venv`), run `./install.sh --dev` — it additionally installs the `[dev]` extras (pytest, pytest-asyncio, ruff) there.

## Status

**Alpha.** Extracted from an earlier in-house orchestrator, keeping the differentiated core (workflows, experience memory, snapshots) and deliberately dropping two things it carried: an MCP proxy layer (Claude Code's native tool discovery covers it) and hard code-analysis dependencies (structural analysis stays external).

## License

[MIT](LICENSE) © Rixmerz
