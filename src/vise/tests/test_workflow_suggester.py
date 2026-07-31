"""Tests for workflow_suggester.py — the pre-implementation workflow prompt.

The hook no longer decides anything. It used to carry a regex intent tier that,
behind VISE_AUTO_ACTIVATE, would activate a workflow on its own guess — a
keyword match silently gating the user's tools. That tier never ran (its
classifier module never shipped) and has been removed along with the flag.
What the hook does now is hand the model the real inventory and let it choose,
which is the job the model is actually good at.
"""
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


def test_multi_step_prompt_directs_the_model_to_activate(tmp_project: Path) -> None:
    prompt = "I need to fix bug in the authentication middleware that triggers redirect loop now"
    out, _ = _run_main(prompt, {}, tmp_project)

    assert "Pick a workflow before implementing" in out
    assert "graph_activate" in out


def test_emitted_call_uses_the_real_parameter_name(tmp_project: Path) -> None:
    """`graph_name` is the actual parameter of graph_activate.

    This hook shipped telling the model to call `graph_activate(name=...)` while
    the README said `graph_id=...`; both raise TypeError. An instruction block is
    only as good as its signature, so pin the one the tool really takes.
    """
    prompt = "implement the new billing endpoint, then wire it to the invoice service"
    out, _ = _run_main(prompt, {}, tmp_project)

    assert 'graph_activate(graph_name="' in out
    assert "graph_activate(name=" not in out
    assert "graph_id" not in out


def test_available_workflows_are_listed_by_id(tmp_project: Path) -> None:
    """The model cannot pick from an inventory it was never shown."""
    library = tmp_project / ".claude" / "workflows"
    library.mkdir(parents=True)
    for stem in ("payments-graph", "onboarding-graph"):
        (library / f"{stem}.yaml").write_text("metadata:\n  name: x\nnodes: []\nedges: []\n")

    prompt = "implement the new billing endpoint, then wire it to the invoice service"
    out, _ = _run_main(prompt, {}, tmp_project)

    assert "onboarding-graph" in out
    assert "payments-graph" in out
    assert out.index("onboarding-graph") < out.index("payments-graph"), "listed unsorted"


def test_no_discoverable_workflows_degrades_to_a_usable_instruction(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty inventory must not print a bare `Available: ` and strand the model."""
    monkeypatch.setattr(ws, "_available_workflows", lambda _dir: [])

    prompt = "implement the new billing endpoint, then wire it to the invoice service"
    out, _ = _run_main(prompt, {}, tmp_project)

    assert "Available:" not in out
    assert "graph_list_available" in out


def test_inventory_failure_does_not_cost_the_user_their_prompt(
    tmp_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A UserPromptSubmit hook that raises is worse than one that says less."""
    def _boom(_dir):
        raise RuntimeError("workflows dir is unreadable")

    # Patch the SOURCE module, not the hook: `_available_workflows` imports the
    # symbol at call time, so a `ws.resolve_workflow_dirs` attribute would never
    # be consulted and the test would pass without exercising anything.
    from vise.engines import workflow_scope

    monkeypatch.setattr(workflow_scope, "resolve_workflow_dirs", _boom)

    prompt = "implement the new billing endpoint, then wire it to the invoice service"
    out, code = _run_main(prompt, {}, tmp_project)

    assert code == 0
    assert "graph_activate" in out


def test_behavior_does_not_depend_on_the_removed_auto_activate_flag(
    tmp_project: Path,
) -> None:
    """VISE_AUTO_ACTIVATE is gone; setting it must change nothing at all."""
    prompt = "implement the new billing endpoint, then wire it to the invoice service"

    off, _ = _run_main(prompt, {"VISE_AUTO_ACTIVATE": "0"}, tmp_project)
    on, _ = _run_main(prompt, {"VISE_AUTO_ACTIVATE": "1"}, tmp_project)

    assert off == on
    assert "auto-activated" not in on.lower()
    assert not (tmp_project / ".claude" / "workflow" / "graph.yaml").exists(), (
        "the hook must never activate a workflow by itself"
    )


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
