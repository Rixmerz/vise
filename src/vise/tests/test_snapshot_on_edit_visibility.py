"""VISE_SNAPSHOT_ON_EDIT is opt-in and off by default — regression coverage
for the tools/hook honestly surfacing that state instead of silently
returning a stale-looking snapshot list.

Covers:
- snapshot_list flags on_edit_capture_enabled=False + explains staleness
  when the env var is unset.
- snapshot_list flags on_edit_capture_enabled=True and drops the note when
  the env var is truthy.
- snapshot_create works regardless of the env var (explicit calls are not
  gated by the opt-in hook toggle).
- The hook itself still no-ops when the var is unset (no regression of the
  opt-in default).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vise.tools.snapshot import register_snapshot


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("initial", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    return repo


class _FakeMCP:
    def __init__(self) -> None:
        self.registered: dict = {}

    def tool(self, *a, **kw):
        def deco(fn):
            self.registered[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture()
def tools() -> dict:
    mcp = _FakeMCP()
    register_snapshot(mcp)
    return mcp.registered


def test_snapshot_list_flags_disabled_by_default(tools, git_repo, monkeypatch):
    monkeypatch.delenv("VISE_SNAPSHOT_ON_EDIT", raising=False)
    tools["snapshot_create"](label="x", project_dir=str(git_repo))

    result = tools["snapshot_list"](project_dir=str(git_repo))

    assert result["on_edit_capture_enabled"] is False
    assert "note" in result
    assert "off" in result["note"].lower()


def test_snapshot_list_does_not_claim_disabled_when_enabled(tools, git_repo, monkeypatch):
    monkeypatch.setenv("VISE_SNAPSHOT_ON_EDIT", "1")
    tools["snapshot_create"](label="x", project_dir=str(git_repo))

    result = tools["snapshot_list"](project_dir=str(git_repo))

    assert result["on_edit_capture_enabled"] is True
    assert "note" not in result


def test_snapshot_create_works_explicitly_while_hook_disabled(tools, git_repo, monkeypatch):
    """Explicit snapshot_create is a manual checkpoint, distinct from the
    automatic per-edit hook — it must not be gated by VISE_SNAPSHOT_ON_EDIT."""
    monkeypatch.delenv("VISE_SNAPSHOT_ON_EDIT", raising=False)

    result = tools["snapshot_create"](label="manual", project_dir=str(git_repo))

    assert "error" not in result
    assert result["label"] == "manual"


def test_hook_still_noops_when_env_var_unset(git_repo, monkeypatch):
    """Regression guard: the per-edit hook must remain opt-in (no-op by
    default), even after sharing its env-var read with the tools layer."""
    monkeypatch.delenv("VISE_SNAPSHOT_ON_EDIT", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(git_repo))

    from vise.hooks import snapshot_trigger

    payload = {"tool_name": "Write", "tool_input": {"file_path": str(git_repo / "f.txt")}}
    monkeypatch.setattr(snapshot_trigger, "_read_input", lambda: payload)

    exit_code = snapshot_trigger.main()

    assert exit_code == 0
    from vise.core import snapshots
    assert snapshots.list_all(git_repo) == []
