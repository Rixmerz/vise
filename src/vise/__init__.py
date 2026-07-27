"""vise — phase-gated workflows, experience memory, and git snapshots for Claude Code."""
from __future__ import annotations

# Keep in sync with pyproject.toml [project].version and
# .claude-plugin/plugin.json .version — test_version_sync.py enforces it.
# This is what vise_version reports, so a stale value here makes the tool
# lie about which build is running even when the right code is loaded.
__version__ = "0.1.0a10"

__all__ = ["__version__"]
