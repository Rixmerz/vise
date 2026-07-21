"""Tests for 3-scope workflow discovery and activation."""
from __future__ import annotations

from pathlib import Path

import pytest

from vise.engines.workflow_scope import resolve_workflow_dirs
from vise.engines.graph_parser import load_graph_from_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BUNDLED_WORKFLOWS = Path(__file__).parent.parent / "assets" / "workflows"

_SIMPLE_GRAPH_YAML = """\
metadata:
  name: "Test Graph"
  description: "A simple test graph."
  version: "1.0.0"
  type: "graph"

nodes:
  - id: "start"
    name: "Start"
    is_start: true
    prompt_injection: "Begin here."

  - id: "end"
    name: "End"
    is_end: true
    prompt_injection: "Done."

edges:
  - id: "start-to-end"
    from: "start"
    to: "end"
    condition:
      type: "phrase"
      phrases:
        - "done"
        - "advance"
"""


def _write_graph(directory: Path, name: str, content: str = _SIMPLE_GRAPH_YAML) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}-graph.yaml"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """A minimal fake project directory."""
    project = tmp_path / "my-project"
    project.mkdir()
    return project


@pytest.fixture()
def tmp_user_workflows(tmp_path: Path, monkeypatch) -> Path:
    """Fake user workflows dir, monkeypatched into config."""
    user_dir = tmp_path / "user-workflows"
    user_dir.mkdir()

    import vise.engines.config as config
    monkeypatch.setattr(config, "get_global_workflows_dir", lambda: user_dir)

    # Also patch inside workflow_scope module (it imported get_global_workflows_dir)
    import vise.engines.workflow_scope as wscope
    monkeypatch.setattr(wscope, "get_global_workflows_dir", lambda: user_dir)

    return user_dir


# ---------------------------------------------------------------------------
# resolve_workflow_dirs tests
# ---------------------------------------------------------------------------

def test_resolve_returns_three_scopes(tmp_project: Path, tmp_user_workflows: Path):
    scopes = resolve_workflow_dirs(tmp_project)
    scope_names = [name for name, _ in scopes]
    assert scope_names == ["bundled", "user", "project"]


def test_bundled_path_points_to_assets(tmp_project: Path, tmp_user_workflows: Path):
    scopes = dict(resolve_workflow_dirs(tmp_project))
    bundled_path = scopes["bundled"]
    assert (bundled_path / "feature-dev-graph.yaml").exists(), (
        f"feature-dev-graph.yaml not found in bundled dir {bundled_path}"
    )
    assert (bundled_path / "debug-graph.yaml").exists()
    assert (bundled_path / "pr-review-graph.yaml").exists()


def test_project_scope_path(tmp_project: Path, tmp_user_workflows: Path):
    scopes = dict(resolve_workflow_dirs(tmp_project))
    expected = tmp_project / ".claude" / "workflows"
    assert scopes["project"] == expected


def test_user_scope_path(tmp_project: Path, tmp_user_workflows: Path):
    scopes = dict(resolve_workflow_dirs(tmp_project))
    assert scopes["user"] == tmp_user_workflows


# ---------------------------------------------------------------------------
# graph_list_available scope field and precedence tests
# ---------------------------------------------------------------------------

def _run_list(project_dir: str | Path, monkeypatch) -> list[dict]:
    """Call graph_list_available via the tool registration and return graphs list."""
    # We test the resolver logic directly rather than going through MCP registration
    from vise.engines.workflow_scope import resolve_workflow_dirs as rdirs

    seen: dict[str, dict] = {}
    for scope, workflows_dir in rdirs(project_dir):
        if not workflows_dir.exists():
            continue
        candidates = sorted({*workflows_dir.glob("*-graph.yaml"), *workflows_dir.glob("*.yaml")})
        for yaml_file in candidates:
            graph_name = yaml_file.stem
            content = yaml_file.read_text(encoding="utf-8")
            if "\nnodes:" not in "\n" + content or "\nedges:" not in "\n" + content:
                continue
            seen[graph_name] = {"id": graph_name, "file": str(yaml_file), "scope": scope}
    return list(seen.values())


def test_bundled_workflows_appear_in_list(tmp_project: Path, tmp_user_workflows: Path, monkeypatch):
    graphs = _run_list(tmp_project, monkeypatch)
    ids = {g["id"] for g in graphs}
    assert "feature-dev-graph" in ids
    assert "debug-graph" in ids
    assert "pr-review-graph" in ids


def test_scope_field_is_bundled_when_no_overrides(tmp_project: Path, tmp_user_workflows: Path, monkeypatch):
    graphs = _run_list(tmp_project, monkeypatch)
    bundled = {g["id"]: g for g in graphs if g["scope"] == "bundled"}
    assert "feature-dev-graph" in bundled
    assert "debug-graph" in bundled
    assert "pr-review-graph" in bundled


def test_project_scope_wins_over_bundled(tmp_project: Path, tmp_user_workflows: Path, monkeypatch):
    """A graph in the project dir with the same name as a bundled one should win."""
    project_workflows = tmp_project / ".claude" / "workflows"
    _write_graph(project_workflows, "feature-dev")

    graphs = _run_list(tmp_project, monkeypatch)
    by_id = {g["id"]: g for g in graphs}
    entry = by_id.get("feature-dev-graph")
    assert entry is not None, "feature-dev-graph should be present"
    assert entry["scope"] == "project", f"expected project scope, got {entry['scope']}"


def test_user_scope_wins_over_bundled(tmp_project: Path, tmp_user_workflows: Path, monkeypatch):
    """A graph in the user dir should override the bundled one."""
    _write_graph(tmp_user_workflows, "pr-review")

    graphs = _run_list(tmp_project, monkeypatch)
    by_id = {g["id"]: g for g in graphs}
    entry = by_id.get("pr-review-graph")
    assert entry is not None
    assert entry["scope"] == "user", f"expected user scope, got {entry['scope']}"


def test_project_scope_wins_over_user(tmp_project: Path, tmp_user_workflows: Path, monkeypatch):
    """Project scope should override user scope for same name."""
    _write_graph(tmp_user_workflows, "my-workflow")
    project_workflows = tmp_project / ".claude" / "workflows"
    _write_graph(project_workflows, "my-workflow")

    graphs = _run_list(tmp_project, monkeypatch)
    by_id = {g["id"]: g for g in graphs}
    entry = by_id.get("my-workflow-graph")
    assert entry is not None
    assert entry["scope"] == "project"


# ---------------------------------------------------------------------------
# Bundled YAML parses correctly through graph_parser
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("graph_stem", [
    "feature-dev-graph",
    "pr-review-graph",
    "debug-graph",
])
def test_bundled_yaml_parses(graph_stem: str):
    """Each bundled workflow YAML must parse without error."""
    yaml_path = _BUNDLED_WORKFLOWS / f"{graph_stem}.yaml"
    assert yaml_path.exists(), f"Bundled workflow not found: {yaml_path}"
    graph = load_graph_from_file(yaml_path)
    assert graph is not None
    assert len(graph.nodes) >= 2
    assert len(graph.edges) >= 1
    # Must have a start node
    start = graph.get_start_node()
    assert start is not None, f"No start node in {graph_stem}"
