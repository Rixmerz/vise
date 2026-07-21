"""Tests for the edit_feedback PostToolUse hook (ruff fast-path lint)."""
from __future__ import annotations

import io
import json

import pytest

from vise.hooks import edit_feedback


def _run_hook(monkeypatch, payload: dict) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return edit_feedback.main()


def _payload(file_path: str, tool: str = "Edit") -> dict:
    return {"tool_name": tool, "tool_input": {"file_path": file_path}}


def test_feedback_emitted_for_undefined_name(tmp_path, monkeypatch, capsys):
    # Arrange: a .py file with an F821 (undefined name)
    bad = tmp_path / "bad.py"
    bad.write_text("print(undefined_variable)\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    # Act
    rc = _run_hook(monkeypatch, _payload(str(bad)))

    # Assert: exit 0, stderr mentions F821 with file:line
    assert rc == 0
    err = capsys.readouterr().err
    assert "F821" in err
    assert "bad.py:1" in err
    assert "[vise.lint]" in err


def test_silent_for_clean_file(tmp_path, monkeypatch, capsys):
    clean = tmp_path / "clean.py"
    clean.write_text('X = 1\nprint(X)\n', encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    rc = _run_hook(monkeypatch, _payload(str(clean), tool="Write"))

    assert rc == 0
    assert capsys.readouterr().err == ""


def test_silent_for_non_python_file(tmp_path, monkeypatch, capsys):
    txt = tmp_path / "notes.txt"
    txt.write_text("undefined_variable everywhere\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    rc = _run_hook(monkeypatch, _payload(str(txt)))

    assert rc == 0
    assert capsys.readouterr().err == ""


def test_silent_for_non_edit_tool(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.py"
    bad.write_text("print(undefined_variable)\n", encoding="utf-8")

    rc = _run_hook(monkeypatch, _payload(str(bad), tool="Bash"))

    assert rc == 0
    assert capsys.readouterr().err == ""


def test_silent_exit0_on_engine_failure(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.py"
    bad.write_text("print(undefined_variable)\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    def _boom(*args, **kwargs):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(
        "vise.engines.lsp_diagnostics.lsp_diagnostics", _boom
    )

    rc = _run_hook(monkeypatch, _payload(str(bad)))

    assert rc == 0
    assert capsys.readouterr().err == ""


def test_silent_for_missing_file(tmp_path, monkeypatch, capsys):
    rc = _run_hook(monkeypatch, _payload(str(tmp_path / "gone.py")))
    assert rc == 0
    assert capsys.readouterr().err == ""


def test_garbage_stdin_is_fail_open(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json{{{"))
    assert edit_feedback.main() == 0
    assert capsys.readouterr().err == ""


def test_engine_ruff_only_tools_param(tmp_path):
    # Backward-compat: default runs both; tools=("ruff",) skips mypy
    from vise.engines.lsp_diagnostics import lsp_diagnostics

    f = tmp_path / "m.py"
    f.write_text("print(undefined_variable)\n", encoding="utf-8")
    res = lsp_diagnostics(str(tmp_path), str(f), tools=("ruff",))
    if res.get("available"):
        assert res["tools_run"] == ["ruff"]
        assert any(d["code"] == "F821" for d in res["diagnostics"])
    else:
        pytest.skip("ruff not installed")
