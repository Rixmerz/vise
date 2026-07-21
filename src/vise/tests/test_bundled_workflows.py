"""Every bundled workflow YAML must parse — unparseable assets must never ship."""

from pathlib import Path

import pytest

from vise.engines.graph_parser import load_graph_from_file

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / "assets" / "workflows"
WORKFLOW_FILES = sorted(WORKFLOWS_DIR.glob("*.yaml"))


def test_workflows_dir_has_files():
    assert WORKFLOW_FILES, f"no workflow YAMLs found in {WORKFLOWS_DIR}"


@pytest.mark.parametrize("yaml_path", WORKFLOW_FILES, ids=lambda p: p.name)
def test_bundled_workflow_parses(yaml_path: Path):
    graph = load_graph_from_file(yaml_path)
    assert graph.nodes
    assert graph.edges
