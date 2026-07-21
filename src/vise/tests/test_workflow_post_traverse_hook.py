"""Tests for the workflow_post_traverse hook usage-block injection.

The hook reads usage_state.json (written by the harvester pane) and
emits a compact one-line block to stderr at every phase boundary. The
model sees it as part of the PostToolUse output and uses it to decide
whether to schedule a clear + resume before the next phase.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_HOOK = Path(__file__).parent.parent / "hooks" / "workflow_post_traverse.py"


def _seed_usage_state(usage_dir: Path, payload: dict) -> None:
    """Write a fully-formed state.json mirroring what the harvester would."""
    usage_dir.mkdir(parents=True, exist_ok=True)
    (usage_dir / "state.json").write_text(json.dumps(payload), encoding="utf-8")


def _run_hook(hook_input: dict, project_dir: Path, usage_dir: Path) -> subprocess.CompletedProcess[str]:
    src_dir = str(Path(__file__).parent.parent.parent)
    return subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        env={
            **__import__("os").environ,
            "PYTHONPATH": src_dir,
            "CLAUDE_PROJECT_DIR": str(project_dir),
            "VISE_USAGE_DIR": str(usage_dir),
        },
        timeout=10,
    )


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    return p


@pytest.fixture()
def usage_dir(tmp_path: Path) -> Path:
    return tmp_path / "usage"


# ---------------------------------------------------------------------------
# Usage block emission — happy path
# ---------------------------------------------------------------------------

def test_hook_emits_usage_block_to_stderr_when_state_is_full(project_dir: Path, usage_dir: Path):
    _seed_usage_state(usage_dir, {
        "context_used": 100_000,
        "context_total": 1_000_000,
        "context_pct": 10,
        "session_pct": 23,
        "session_reset": "6:30 pm",
    })

    result = _run_hook(
        {
            "tool_name": "mcp__vise__graph_traverse",
            "tool_result": {"from_node": "design", "to_node": "implement"},
        },
        project_dir,
        usage_dir,
    )

    assert result.returncode == 0, result.stderr
    assert "📊 100k/1M | 23% usage reset at 6:30 pm" in result.stderr


def test_hook_still_emits_transition_line_alongside_usage_block(project_dir: Path, usage_dir: Path):
    _seed_usage_state(usage_dir, {"session_pct": 18})

    result = _run_hook(
        {
            "tool_name": "mcp__vise__graph_traverse",
            "tool_result": {"from_node": "design", "to_node": "implement"},
        },
        project_dir,
        usage_dir,
    )

    assert "⚡ design → implement" in result.stderr, (
        "transition line must still be emitted — usage block is additive"
    )


# ---------------------------------------------------------------------------
# Stdout contract preserved — always emits the approve decision
# ---------------------------------------------------------------------------

def test_hook_stdout_is_approve_decision_with_usage_block_present(project_dir: Path, usage_dir: Path):
    _seed_usage_state(usage_dir, {"session_pct": 50, "session_reset": "2:00 am"})

    result = _run_hook(
        {
            "tool_name": "mcp__vise__graph_traverse",
            "tool_result": {"from_node": "x", "to_node": "y"},
        },
        project_dir,
        usage_dir,
    )

    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"decision": "approve"}


# ---------------------------------------------------------------------------
# Edge cases — no usage state, partial state, non-traverse tool
# ---------------------------------------------------------------------------

def test_hook_no_usage_state_emits_no_block_but_still_approves(project_dir: Path, usage_dir: Path):
    # Deliberately do NOT seed usage_dir
    result = _run_hook(
        {
            "tool_name": "mcp__vise__graph_traverse",
            "tool_result": {"from_node": "a", "to_node": "b"},
        },
        project_dir,
        usage_dir,
    )

    assert result.returncode == 0
    assert "📊" not in result.stderr, "no state → no block, do not invent values"
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"decision": "approve"}


def test_hook_non_traverse_tool_skips_usage_block(project_dir: Path, usage_dir: Path):
    _seed_usage_state(usage_dir, {"session_pct": 99})

    result = _run_hook(
        {
            "tool_name": "mcp__vise__some_other_tool",
            "tool_result": {},
        },
        project_dir,
        usage_dir,
    )

    assert result.returncode == 0
    assert "📊" not in result.stderr, (
        "usage block must only fire for graph_traverse — other tools are out of scope"
    )
