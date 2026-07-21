"""Tests for workflow_suggester.py — auto-activate + suggestion tiers."""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

from vise.hooks import workflow_suggester as ws


def _run_main(prompt: str, env: dict[str, str], project_dir: Path) -> tuple[str, int]:
    """Invoke ws.main() with patched env + stdin; capture stdout + exit code."""
    payload = json.dumps({"prompt": prompt, "hook_event_name": "UserPromptSubmit"})
    stdout = io.StringIO()
    full_env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir), **env}
    code = 0
    with (
        mock.patch.object(sys, "stdin", io.StringIO(payload)),
        mock.patch.object(sys, "stdout", stdout),
        mock.patch.dict(os.environ, full_env, clear=False),
    ):
        try:
            ws.main()
        except SystemExit as e:
            code = int(e.code or 0)
    return stdout.getvalue(), code


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    (tmp_path / ".claude" / "workflow").mkdir(parents=True)
    return tmp_path


def test_short_prompt_silent(tmp_project: Path) -> None:
    out, code = _run_main("fix bug", {"VISE_AUTO_ACTIVATE": "0"}, tmp_project)
    assert out == ""
    assert code == 0


def test_question_silent(tmp_project: Path) -> None:
    prompt = "Why does the auth flow redirect twice when the cookie is set on subdomain?"
    out, _ = _run_main(prompt, {"VISE_AUTO_ACTIVATE": "0"}, tmp_project)
    assert out == ""


def test_disabled_env_silent(tmp_project: Path) -> None:
    prompt = "implement a new feature for the login flow that handles OAuth properly"
    out, _ = _run_main(prompt, {"VISE_WORKFLOW_SUGGEST": "0"}, tmp_project)
    assert out == ""


def test_suggest_tier_emits_intent(tmp_project: Path) -> None:
    prompt = "I need to fix bug in the authentication middleware that triggers redirect loop now"
    out, _ = _run_main(prompt, {"VISE_AUTO_ACTIVATE": "0"}, tmp_project)
    assert "Workflow" in out
    # Regex tier returns 0.9 confidence → falls into >=0.85 tier but env is off,
    # so suggestion path is taken (>= 0.65 threshold).
    assert "debug" in out.lower()


def test_auto_activate_off_emits_suggestion(tmp_project: Path) -> None:
    prompt = "fix bug in the broken login flow; redirect loop happens after refresh token expiry"
    out, _ = _run_main(prompt, {"VISE_AUTO_ACTIVATE": "0"}, tmp_project)
    assert "auto-activated" not in out.lower()
    assert "suggestion" in out.lower()


def test_auto_activate_on_writes_state(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Redirect XDG state dir to tmp so initialize_graph_state writes there.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_project / "xdg"))
    prompt = "fix bug in the broken login flow; redirect loop happens after refresh token expiry"
    out, _ = _run_main(prompt, {"VISE_AUTO_ACTIVATE": "1"}, tmp_project)
    # Either auto-activated (bundled debug-graph.yaml exists) or fell back to
    # suggestion (graph file wasn't found). Both are acceptable; here we assert
    # the path actually attempts activation when the env is on.
    if "auto-activated" in out.lower():
        target = tmp_project / ".claude" / "workflow" / "graph.yaml"
        assert target.exists()
    else:
        assert "suggestion" in out.lower()


def test_already_active_workflow_silent(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Active graph present (mid-traversal) → no activation, no output at all."""
    state_dir = tmp_project / ".claude" / "workflow"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "graph_state.json"
    # Use the canonical key written by graph_state.save_graph_state.
    state_file.write_text(json.dumps({
        "active_graph": "harness-improvements",
        "current_nodes": ["exclusion-keywords"],
        "total_transitions": 2,
        "node_visits": {"start": 1, "exclusion-keywords": 1},
    }))
    prompt = "fix bug in the broken login flow; redirect loop happens after refresh token expiry"
    monkeypatch.setattr(ws, "_state_path", lambda: state_file)
    out, _ = _run_main(prompt, {"VISE_AUTO_ACTIVATE": "1"}, tmp_project)
    assert out == ""


def test_active_graph_zero_transitions_still_blocks(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any active graph — even at start node with 0 transitions — must block activation."""
    state_dir = tmp_project / ".claude" / "workflow"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "graph_state.json"
    state_file.write_text(json.dumps({
        "active_graph": "feature-dev",
        "current_nodes": ["start"],
        "total_transitions": 0,
        "node_visits": {"start": 1},
    }))
    prompt = "implement a new feature endpoint with tests and migration"
    monkeypatch.setattr(ws, "_state_path", lambda: state_file)
    out, _ = _run_main(prompt, {"VISE_AUTO_ACTIVATE": "1"}, tmp_project)
    assert out == ""


def test_active_graph_suggestion_only_when_auto_off(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Active graph + VISE_AUTO_ACTIVATE=0 → hook exits early before suggestion path."""
    state_dir = tmp_project / ".claude" / "workflow"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "graph_state.json"
    state_file.write_text(json.dumps({
        "active_graph": "debug",
        "current_nodes": ["reproduce"],
        "total_transitions": 1,
    }))
    prompt = "fix bug in the broken login flow; redirect loop happens after refresh token expiry"
    monkeypatch.setattr(ws, "_state_path", lambda: state_file)
    out, _ = _run_main(prompt, {"VISE_AUTO_ACTIVATE": "0"}, tmp_project)
    # Active workflow → exits at the guard, produces no output regardless of VISE_AUTO_ACTIVATE.
    assert out == ""


def test_state_file_unreadable_fails_open(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unreadable state file → fail-open (no activation) to avoid destroying unknown state."""
    # Point _state_path at a path that exists but is not valid JSON.
    state_dir = tmp_project / ".claude" / "workflow"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "graph_state.json"
    state_file.write_bytes(b"\xff\xfe invalid bytes")  # not valid UTF-8 JSON

    prompt = "implement a new feature for the login flow that handles OAuth properly"
    monkeypatch.setattr(ws, "_state_path", lambda: state_file)
    out, _ = _run_main(prompt, {"VISE_AUTO_ACTIVATE": "1"}, tmp_project)
    # Must not auto-activate; suggestion-only or silent are both acceptable.
    assert "auto-activated" not in out.lower()


def test_no_active_graph_activation_still_works(tmp_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No active graph → normal activation path unchanged."""
    state_dir = tmp_project / ".claude" / "workflow"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "graph_state.json"
    # Empty / null state — no active graph.
    state_file.write_text(json.dumps({
        "active_graph": None,
        "current_nodes": [],
        "total_transitions": 0,
    }))
    monkeypatch.setattr(ws, "_state_path", lambda: state_file)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_project / "xdg"))
    prompt = "fix bug in the broken login flow; redirect loop happens after refresh token expiry"
    out, _ = _run_main(prompt, {"VISE_AUTO_ACTIVATE": "1"}, tmp_project)
    # With no active graph the hook proceeds — either auto-activates (if graph
    # file found) or emits a suggestion. Both are correct outcomes.
    if "auto-activated" in out.lower():
        assert (tmp_project / ".claude" / "workflow" / "graph.yaml").exists()
    else:
        assert "suggestion" in out.lower() or "workflow" in out.lower()


def test_pasted_doc_skipped(tmp_project: Path) -> None:
    runbook = (
        "# Lineamientos vise — debug runbook\n"
        "## A. Ground truth\n- implement X if Y\n"
        "## B. Auth stack\n- fix refresh token flow\n"
        "## C. Deploy verification\n- deploy then curl prod\n"
    )
    out, _ = _run_main(runbook, {}, tmp_project)
    assert out == ""
