"""experience_query scope=project must not read as 'no memory anywhere' when
the zero is a scope artifact (container repo, no per-project store) and the
global store actually has entries."""
from __future__ import annotations

from pathlib import Path

import pytest


class _FakeMCP:
    def __init__(self) -> None:
        self.registered: dict = {}

    def tool(self, *a, **kw):
        def deco(fn):
            self.registered[fn.__name__] = fn
            return fn
        return deco


def _register(tmp_path: Path):
    from vise.tools import experience as experience_tools

    mcp = _FakeMCP()
    experience_tools.register_experience(mcp)
    return mcp.registered["experience_query"]


@pytest.fixture(autouse=True)
def _reset_global_store_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_experience_store() caches a module-level singleton that outlives
    the per-test XDG isolation fixture — reset it so entries from an earlier
    test in this file don't leak into a later one."""
    from vise.engines import experience_memory as _exp_mem

    monkeypatch.setattr(_exp_mem, "_experience_store", None)


def test_project_scope_zero_matches_hints_global_when_global_has_entries(
    tmp_path: Path,
) -> None:
    from vise.engines.experience_memory import ExperienceEntry, get_experience_store

    query = _register(tmp_path)

    global_store = get_experience_store()
    global_store.record(ExperienceEntry(
        type="gate_blocked", file_pattern="src/foo.py", keywords=["foo"],
        domain="backend", description="something happened", severity="medium",
        project_origin="other-repo", scope="global",
    ))
    global_store.save()

    result = query(file_path="src/bar.py", project_dir=str(tmp_path))
    assert result["matches"] == 0
    assert result["hint"] is not None
    assert "scope" in result["hint"]
    assert "global" in result["hint"]


def test_both_scopes_empty_no_misleading_hint(tmp_path: Path) -> None:
    query = _register(tmp_path)
    result = query(file_path="src/bar.py", project_dir=str(tmp_path))
    assert result["matches"] == 0
    assert result["hint"] is None
