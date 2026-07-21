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

`install.sh` checks for the `claude` CLI, provisions runtime deps (a dedicated venv at `~/.local/share/vise/venv` if system `python3` lacks `fastmcp`/`fastembed`), registers the repo as a local plugin marketplace, and installs the plugin (`claude plugin marketplace add <repo>` + `claude plugin install vise@vise`). Idempotent — safe to re-run. Restart Claude Code afterwards.

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
├── assets/        # bundled workflows (9), recipes (11), skills
├── core/          # embeddings, session, paths, git snapshot plumbing
└── cli/           # `vise` CLI (graph/experience management offline)
```

## Configuration

Environment variables (all optional; legacy `JIG_*` names honored as fallbacks where they existed):

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

## Development

```sh
uv venv && uv pip install -e '.[dev]'
python -m pytest src/vise/tests/ -q
ruff check src/
```

Or, to reuse the plugin venv (`~/.local/share/vise/venv`), run `./install.sh --dev` — it additionally installs the `[dev]` extras (pytest, pytest-asyncio, ruff) there.

## Status

**Alpha.** Extracted from [jig](https://github.com/Rixmerz/jig), keeping the differentiated core (workflows, experience memory, snapshots) and dropping the MCP proxy layer (Claude Code's native tool discovery covers it) and hard code-analysis dependencies (now a pluggable provider stub).

## License

[MIT](LICENSE) © Rixmerz
