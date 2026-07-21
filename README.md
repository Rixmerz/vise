# vise

Claude Code plugin (Python MCP server) with three subsystems:

- **Phase-gated workflow enforcer** — graph-defined phases that gate tools until you traverse.
- **Cross-project experience memory** — semantically indexed learnings that persist across projects.
- **Git snapshots** — automatic orphan-ref snapshots of every edit cycle, restorable without touching your branch.

Extracted from [jig](https://github.com/Rixmerz/jig) **without the MCP proxy layer** — Claude Code's native ToolSearch now covers on-demand tool discovery, so vise keeps only the differentiated core.

## Dev setup

```sh
uv venv && uv pip install -e .
```

Run tests: `python -m pytest src/vise/tests/ -q`

## Status

Extraction in progress — core (paths, session, lifecycle, embeddings, embed cache) is in; enforcer, experience, and snapshot tool families land in subsequent waves.
