# vise

Claude Code plugin (Python MCP server) with three subsystems:

- **Phase-gated workflow enforcer** — workflows are directed graphs of phases; each node can inject phase-specific prompts and *block* tools (e.g. Edit/Write during a "think" phase) until you `graph_traverse` forward. Per-node validator gates hold the transition until declared checks pass.
- **Cross-project experience memory** — learnings recorded per file/topic, semantically indexed (fastembed) with FSRS-style retrievability decay, injected back at edit time by hooks and queryable via `experience_*` tools.
- **Git snapshots** — automatic orphan-ref snapshots (`refs/vise/snapshots/<id>`) after every edit cycle (throttled), restorable without touching your branch or working history.

The MCP surface exposes **50 tools** (`graph_*`, `experience_*`, `memory_*`, `snapshot_*`, `goal_*`, `recipe_*`, `capability_*`, `next_task_*`, `vise_version`, …).

## Install

vise ships as a Claude Code plugin (`.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` + `.mcp.json` + `hooks/hooks.json`). One command:

```sh
./install.sh
```

This checks for the `claude` CLI, provisions runtime deps (a dedicated venv at `~/.local/share/vise/venv` if system `python3` lacks `fastmcp`/`fastembed`), registers the repo as a local marketplace, and installs the plugin (`claude plugin marketplace add <repo>` + `claude plugin install vise@vise`). Idempotent — safe to re-run. Restart Claude Code afterwards.

The MCP server and all hooks run through `bin/vise-run`, a launcher that prefers the vise venv's python and falls back to `python3`, with `PYTHONPATH="${CLAUDE_PLUGIN_ROOT}/src"`. No installed wheel required for plugin usage.

For non-plugin usage (standalone MCP server on PATH), install the package and use the `vise-mcp` console script instead:

```sh
uv venv && uv pip install -e .        # dev
# or: uv tool install vise-mcp       # once published
```

The hook commands likewise run via `bin/vise-run` directly from the plugin checkout.

### Hook wiring (`hooks/hooks.json`)

| Event | Matcher | Hook |
|---|---|---|
| UserPromptSubmit | `*` | `workflow_suggester.py` — suggests activating a workflow for task-shaped prompts |
| PreToolUse | `*` | `graph_enforcer.py` — blocks tools the active phase forbids (fail-open) |
| PreToolUse | `Edit\|Write` | `experience_injector.py` — injects past learnings for the touched file |
| PostToolUse | `Edit\|Write\|MultiEdit`, `Bash` | `snapshot_trigger.py` — captures a snapshot (30 s throttle) |
| PostToolUse | `Bash` | `experience_recorder.py` — records learnings from commit messages |
| PostToolUse | `mcp__.*__graph_traverse` | `workflow_post_traverse.py` — post-phase feedback |
| PostToolUse | `mcp__.*__(graph_reset\|graph_activate)` | `workflow_override_detector.py` |
| Stop | `*` | `goal_gate.py` — blocks ending the turn with an unfinished active goal |

All hooks fail open: on any internal error they exit 0 and never block the session.

## CLI

```sh
vise version
vise graph --help        # inspect/manage workflow state offline
vise experience --help   # query/record learnings from the shell
```

## What jig had that vise dropped, and why

vise is extracted from [jig](https://github.com/Rixmerz/jig), keeping only the differentiated core:

- **MCP proxy layer** (`proxy_pool` / `internal_proxy` / `execute_mcp_tool` / tool archive) — dropped. Claude Code's native ToolSearch now covers on-demand tool discovery, so proxying every configured MCP through one server is no longer needed.
- **DCC (DeltaCodeCube) glue** — reduced to a provider-agnostic stub (`engines/dcc_glue.py`). Quality-signal enrichment is pluggable; no hard dependency on a specific code-analysis backend.
- Legacy `JIG_*` env vars are still honored as fallbacks where they existed (e.g. `VISE_EMBED_MODEL` preferred, `JIG_EMBED_MODEL` legacy).

## Dev

```sh
uv venv && uv pip install -e .
python -m pytest src/vise/tests/ -q   # 426 tests
```
