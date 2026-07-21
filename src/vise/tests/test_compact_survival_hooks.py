"""Tests for precompact_state.py + session_restore.py — compact survival."""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

from vise.hooks import precompact_state, session_restore


def _run(module, project_dir: Path, payload: dict | None = None,
         env: dict[str, str] | None = None) -> tuple[str, int]:
    stdout = io.StringIO()
    full_env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir),
                **(env or {})}
    with (
        mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload or {}))),
        mock.patch.object(sys, "stdout", stdout),
        mock.patch.dict(os.environ, full_env, clear=False),
    ):
        code = module.main()
    return stdout.getvalue(), int(code or 0)


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Project dir with isolated XDG + goal storage."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    project = tmp_path / "proj"
    (project / ".claude" / "workflow").mkdir(parents=True)
    return project


def _write_graph_state(project: Path) -> None:
    from vise.core import state_paths
    p = state_paths.graph_state_path(str(project))
    p.write_text(json.dumps({
        "active_graph": "debug-flow",
        "current_nodes": ["reproduce"],
        "node_visits": {}, "execution_path": [],
    }))


def test_precompact_no_state_silent(isolated: Path) -> None:
    out, code = _run(precompact_state, isolated)
    assert out == ""
    assert code == 0


def test_session_restore_no_state_silent(isolated: Path) -> None:
    out, code = _run(session_restore, isolated,
                     {"source": "compact"})
    assert out == ""
    assert code == 0


def test_precompact_active_workflow_emits_context(isolated: Path) -> None:
    _write_graph_state(isolated)
    out, code = _run(precompact_state, isolated)
    assert code == 0
    data = json.loads(out)
    hso = data["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreCompact"
    assert "debug-flow" in hso["additionalContext"]
    assert "reproduce" in hso["additionalContext"]
    assert "PRESERVE".lower() in hso["additionalContext"].lower()


def test_session_restore_active_workflow_emits_context(isolated: Path) -> None:
    _write_graph_state(isolated)
    out, code = _run(session_restore, isolated, {"source": "startup"})
    assert code == 0
    data = json.loads(out)
    hso = data["hookSpecificOutput"]
    assert hso["hookEventName"] == "SessionStart"
    assert "debug-flow" in hso["additionalContext"]
    assert "graph_status" in hso["additionalContext"]
    assert len(hso["additionalContext"].splitlines()) <= 15


def test_session_restore_active_goal(isolated: Path) -> None:
    from vise.engines import goal_state
    goal_state.set_goal(str(isolated), "ship compact survival hooks")
    out, _ = _run(session_restore, isolated, {"source": "resume"})
    data = json.loads(out)
    assert "ship compact survival hooks" in \
        data["hookSpecificOutput"]["additionalContext"]


def test_hooks_fail_open_on_bad_stdin(isolated: Path) -> None:
    for module in (precompact_state, session_restore):
        stdout = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", io.StringIO("not json")),
            mock.patch.object(sys, "stdout", stdout),
            mock.patch.dict(os.environ, {**os.environ,
                                         "CLAUDE_PROJECT_DIR": str(isolated)}),
        ):
            code = module.main()
        assert int(code or 0) == 0


def test_hooks_fail_open_on_reader_exception(isolated: Path) -> None:
    with mock.patch("vise.hooks._common.read_active_state",
                    side_effect=RuntimeError("boom")):
        for module in (precompact_state, session_restore):
            out, code = _run(module, isolated)
            assert code == 0
            assert out == ""
