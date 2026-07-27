"""Suite-wide isolation: never let a test touch the real vise state tree.

Autouse fixture that redirects ``$XDG_DATA_HOME`` to a per-test tmpdir for
EVERY test in the suite. Without this, tests that exercise graph_state,
experience_memory, snapshots, etc. via their real project-directory paths
end up writing into ``~/.local/share/vise`` (or the developer's real
``$XDG_DATA_HOME/vise``) — see the pollution this fixture exists to
prevent: stray ``states/<test-name>/`` directories and, worse, a test name
colliding with and clobbering a real project's live workflow state.

``vise.engines.experience_memory`` resolves ``GLOBAL_MEMORY_FILE`` and
``PROJECT_MEMORIES_DIR`` at import time (module load), so they cache the
data dir seen before this fixture's ``monkeypatch.setenv`` ever runs.
Setting the env var alone is a lie for that module — the fixture must also
recompute and patch those two module-level constants each test.
"""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated_xdg_data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Force $XDG_DATA_HOME to a per-test tmpdir; never touch real user state."""
    xdg_data_home = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data_home))

    # experience_memory.py caches data_dir()-derived paths at import time.
    # Recompute them against the patched env so this module doesn't keep
    # writing to whatever dir was resolved at first import.
    from vise.engines import experience_memory as _exp_mem
    from vise.core import paths as _paths

    fresh_global = _paths.data_dir() / "experience_memory.json"
    fresh_project = _paths.data_dir() / "project_memories"
    monkeypatch.setattr(_exp_mem, "GLOBAL_MEMORY_FILE", fresh_global)
    monkeypatch.setattr(_exp_mem, "PROJECT_MEMORIES_DIR", fresh_project)

    # vise.tools.experience does `from ... import GLOBAL_MEMORY_FILE` at
    # module level — a separate name binding the patch above does not
    # reach. Only patch it if the module is already importable/imported;
    # importing it here just to patch it is fine (it's cheap, stdlib +
    # engines only) and closes the same split-brain hole for any test
    # that later exercises the tools layer.
    try:
        from vise.tools import experience as _tools_experience
        monkeypatch.setattr(_tools_experience, "GLOBAL_MEMORY_FILE", fresh_global)
        monkeypatch.setattr(_tools_experience, "PROJECT_MEMORIES_DIR", fresh_project)
    except ImportError:
        pass

    return xdg_data_home
