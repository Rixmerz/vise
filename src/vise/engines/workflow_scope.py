"""3-scope workflow directory resolver.

Scopes in ascending precedence (lowest first):
  bundled  — <package>/assets/workflows   (shipped with vise)
  user     — ~/.local/share/vise/workflows  (current global)
  project  — <project>/.claude/workflows   (highest precedence)
"""
from __future__ import annotations

from pathlib import Path

import vise
from vise.engines.config import get_global_workflows_dir


def resolve_workflow_dirs(project_dir: str | Path) -> list[tuple[str, Path]]:
    """Return workflow scope dirs in ascending precedence order (lowest first).

    Returns list of ``(scope_name, path)`` tuples.  Missing dirs are included;
    callers should skip them if they don't exist.
    """
    bundled_dir = Path(vise.__file__).parent / "assets" / "workflows"
    user_dir = get_global_workflows_dir()
    project_workflows_dir = Path(project_dir) / ".claude" / "workflows"
    return [
        ("bundled", bundled_dir),
        ("user", user_dir),
        ("project", project_workflows_dir),
    ]
