"""Tests for `vise graph run` — the unattended cyclable-workflow driver.

All tests use ``--emit`` / ``--dry-run`` so no real ``claude`` subprocess is
ever spawned. The suite validates:

- correct command construction (``claude -p <prompt>``)
- workflow name and project dir embedded in the emitted prompt
- 3-scope resolution order (project > user > bundled) for workflow lookup
- clean error on unknown workflow name
- recursion guard (VISE_GRAPH_RUN_INNER) prevents nested invocations
- ``--dry-run`` is a recognised alias for ``--emit``
"""
from __future__ import annotations

import shlex
from pathlib import Path


from vise.cli.main import main


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_project(tmp_path: Path) -> Path:
    """Return a minimal project directory."""
    (tmp_path / ".claude").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _make_workflow(project_dir: Path, name: str, *, graph_suffix: bool = True) -> Path:
    """Write a stub workflow YAML inside the project's .claude/workflows/."""
    workflows = project_dir / ".claude" / "workflows"
    workflows.mkdir(parents=True, exist_ok=True)
    suffix = "-graph.yaml" if graph_suffix else ".yaml"
    wf = workflows / f"{name}{suffix}"
    wf.write_text(f"id: {name}\nnodes: []\n")
    return wf


def _emit(tmp_path: Path, workflow: str, *, flag: str = "--emit") -> tuple[int, str, str]:
    """Run ``vise graph run <workflow> --emit --project <tmp>`` and return (rc, out, err)."""
    import io
    import sys

    captured_out = io.StringIO()
    captured_err = io.StringIO()

    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = captured_out, captured_err
    try:
        rc = main(["graph", "run", workflow, flag, "--project", str(tmp_path)])
    finally:
        sys.stdout, sys.stderr = old_out, old_err

    return rc, captured_out.getvalue(), captured_err.getvalue()


# ---------------------------------------------------------------------------
# basic emit / dry-run
# ---------------------------------------------------------------------------

def test_graph_run_emit_exits_zero(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _make_workflow(project, "test-workflow")
    rc, out, _ = _emit(tmp_path, "test-workflow")
    assert rc == 0


def test_graph_run_emit_contains_claude(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _make_workflow(project, "my-flow")
    rc, out, _ = _emit(tmp_path, "my-flow")
    assert rc == 0
    assert "claude" in out


def test_graph_run_emit_contains_dash_p_flag(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _make_workflow(project, "my-flow")
    rc, out, _ = _emit(tmp_path, "my-flow")
    assert rc == 0
    # shlex.join wraps arguments; '-p' must appear as a distinct token
    tokens = shlex.split(out.strip())
    assert "-p" in tokens


def test_graph_run_emit_contains_skip_permissions_flag(tmp_path: Path) -> None:
    """The unattended driver must pass --dangerously-skip-permissions.

    No human is present to answer a permission prompt; on an untrusted
    workspace a gated tool (Bash etc.) would otherwise stall forever. The
    flag must appear as a distinct token in the emitted argv (and therefore
    in the real spawn, which uses the same cmd list).
    """
    project = _make_project(tmp_path)
    _make_workflow(project, "my-flow")
    rc, out, _ = _emit(tmp_path, "my-flow")
    assert rc == 0
    tokens = shlex.split(out.strip())
    assert "--dangerously-skip-permissions" in tokens


def test_graph_run_emit_prompt_contains_workflow_name(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _make_workflow(project, "sprint-e2e-graph")
    rc, out, _ = _emit(tmp_path, "sprint-e2e-graph")
    assert rc == 0
    assert "sprint-e2e-graph" in out


def test_graph_run_emit_prompt_contains_project_dir(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _make_workflow(project, "debug")
    rc, out, _ = _emit(tmp_path, "debug")
    assert rc == 0
    assert str(tmp_path.resolve()) in out


def test_graph_run_dry_run_is_alias_for_emit(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    _make_workflow(project, "debug")
    rc, out, _ = _emit(tmp_path, "debug", flag="--dry-run")
    assert rc == 0
    assert "claude" in out


# ---------------------------------------------------------------------------
# emitted command shape
# ---------------------------------------------------------------------------

def test_graph_run_emit_command_is_parseable_by_shlex(tmp_path: Path) -> None:
    """The emitted line must parse cleanly as a shell command."""
    project = _make_project(tmp_path)
    _make_workflow(project, "feature-dev")
    rc, out, _ = _emit(tmp_path, "feature-dev")
    assert rc == 0
    tokens = shlex.split(out.strip())
    # Minimum: ["claude", "-p", "<prompt>"]
    assert len(tokens) >= 3
    assert tokens[0] == "claude"


def test_graph_run_emit_prompt_mentions_graph_activate(tmp_path: Path) -> None:
    """Emitted prompt tells the agent to call graph_activate."""
    project = _make_project(tmp_path)
    _make_workflow(project, "feature-dev")
    rc, out, _ = _emit(tmp_path, "feature-dev")
    assert rc == 0
    assert "graph_activate" in out


def test_graph_run_emit_prompt_mentions_graph_traverse(tmp_path: Path) -> None:
    """Emitted prompt tells the agent to call graph_traverse."""
    project = _make_project(tmp_path)
    _make_workflow(project, "feature-dev")
    rc, out, _ = _emit(tmp_path, "feature-dev")
    assert rc == 0
    assert "graph_traverse" in out


# ---------------------------------------------------------------------------
# workflow resolution — 3-scope cascade
# ---------------------------------------------------------------------------

def test_graph_run_resolves_graph_suffix_variant(tmp_path: Path) -> None:
    """'debug' resolves to 'debug-graph.yaml' (tries -graph.yaml first)."""
    project = _make_project(tmp_path)
    _make_workflow(project, "debug", graph_suffix=True)  # writes debug-graph.yaml
    rc, out, _ = _emit(tmp_path, "debug")
    assert rc == 0
    assert "debug" in out


def test_graph_run_resolves_plain_yaml_variant(tmp_path: Path) -> None:
    """'demo' resolves to 'demo.yaml' when no -graph.yaml exists."""
    project = _make_project(tmp_path)
    _make_workflow(project, "demo", graph_suffix=False)  # writes demo.yaml
    rc, out, _ = _emit(tmp_path, "demo")
    assert rc == 0
    assert "demo" in out


def test_graph_run_resolves_bundled_workflow(tmp_path: Path, capsys) -> None:
    """A bundled workflow (debug-graph.yaml) is found without a project copy."""
    _make_project(tmp_path)
    # Do NOT create a local workflow — rely on bundled 'debug-graph.yaml'
    rc = main(["graph", "run", "debug", "--emit", "--project", str(tmp_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "claude" in out
    assert "debug" in out


# ---------------------------------------------------------------------------
# unknown workflow
# ---------------------------------------------------------------------------

def test_graph_run_unknown_workflow_exits_nonzero(tmp_path: Path) -> None:
    _make_project(tmp_path)
    rc, _, err = _emit(tmp_path, "workflow-that-does-not-exist-xyz")
    assert rc != 0


def test_graph_run_unknown_workflow_reports_name_in_stderr(tmp_path: Path) -> None:
    _make_project(tmp_path)
    rc, _, err = _emit(tmp_path, "nonexistent-abc")
    assert rc != 0
    assert "nonexistent-abc" in err


def test_graph_run_unknown_workflow_does_not_print_command(tmp_path: Path) -> None:
    _make_project(tmp_path)
    rc, out, _ = _emit(tmp_path, "nonexistent-abc")
    assert rc != 0
    assert "claude" not in out


# ---------------------------------------------------------------------------
# recursion guard
# ---------------------------------------------------------------------------

def test_graph_run_recursion_guard_exits_nonzero(tmp_path: Path, monkeypatch) -> None:
    """If VISE_GRAPH_RUN_INNER=1 is set, the command refuses to run."""
    monkeypatch.setenv("VISE_GRAPH_RUN_INNER", "1")
    project = _make_project(tmp_path)
    _make_workflow(project, "debug")
    # Even with a valid workflow and --emit, the guard fires first.
    rc, _, err = _emit(tmp_path, "debug")
    assert rc != 0
    assert "VISE_GRAPH_RUN_INNER" in err or "nest" in err.lower()
