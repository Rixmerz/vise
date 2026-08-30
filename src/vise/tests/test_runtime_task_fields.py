"""The runtime's task metadata lives on the graph's own Task, not beside it.

That decision is the one worth pinning: a second task type with its own ids and
its own dependency edges would be a second workflow engine, and the two would
disagree within a release. These tests hold the seam — the parser reads the new
fields, the builder writes them back, an invalid enum fails closed, and a
workflow that declares none of them is byte-identical to what it was before.
"""
from __future__ import annotations

import pytest

from vise.engines.graph_parser import GraphParseError, parse_graph_yaml
from vise.tools._graph_builder import _generate_graph_yaml

RUNTIME_YAML = """
metadata:
  name: "runtime-fields"
  version: "1.0"
nodes:
  - id: "implement"
    name: "Implement"
    node_type: "dag"
    is_start: true
    is_end: true
    tasks:
      - id: "backend-auth"
        name: "JWT middleware"
        role: "backend"
        criticality: "elevated"
        complexity: "high"
        model: "opus"
        effort: "high"
        max_cost: 1.5
        max_turns: 12
        timeout_s: 120
        ownership:
          - "src/auth/**"
        acceptance:
          - "login returns 200"
      - id: "review"
        name: "Review"
        role: "review"
        writes: false
        dependencies:
          - "backend-auth"
edges: []
"""

PLAIN_YAML = """
metadata:
  name: "plain"
  version: "1.0"
nodes:
  - id: "implement"
    name: "Implement"
    node_type: "dag"
    is_start: true
    is_end: true
    tasks:
      - id: "t1"
        name: "One"
edges: []
"""


def _tasks(yaml_text: str):
    graph = parse_graph_yaml(yaml_text)
    return {t.id: t for t in graph.nodes["implement"].tasks}


def test_every_runtime_field_survives_the_parser():
    task = _tasks(RUNTIME_YAML)["backend-auth"]
    assert task.role == "backend"
    assert task.criticality == "elevated"
    assert task.complexity == "high"
    assert task.model == "opus"
    assert task.effort == "high"
    assert task.ownership == ["src/auth/**"]
    assert task.acceptance == ["login returns 200"]
    assert (task.max_cost, task.max_turns, task.timeout_s) == (1.5, 12, 120)
    assert task.writes is True


def test_writes_false_is_read_as_false_not_as_a_truthy_string():
    assert _tasks(RUNTIME_YAML)["review"].writes is False


def test_a_task_declaring_nothing_keeps_todays_defaults():
    task = _tasks(PLAIN_YAML)["t1"]
    assert task.role is None
    assert task.ownership == []
    assert task.criticality == "routine"
    assert task.complexity == "medium"
    assert task.writes is True
    assert (task.model, task.effort) == (None, None)


@pytest.mark.parametrize(
    "field,value",
    [("criticality", "urgent"), ("complexity", "extreme"), ("effort", "turbo")],
)
def test_an_invalid_runtime_enum_fails_closed(field, value):
    """A typo'd criticality falling back to 'routine' would route a
    security-critical task to the cheapest model and look like it worked."""
    bad = PLAIN_YAML.replace('        name: "One"', f'        name: "One"\n        {field}: "{value}"')
    with pytest.raises(GraphParseError) as exc:
        parse_graph_yaml(bad)
    assert field in str(exc.value)


def test_a_single_string_ownership_is_accepted_as_a_one_item_list():
    yaml_text = PLAIN_YAML.replace(
        '        name: "One"', '        name: "One"\n        ownership: "src/**"'
    )
    assert _tasks(yaml_text)["t1"].ownership == ["src/**"]


def test_the_builder_emits_the_runtime_fields_it_was_given():
    builder = {
        "metadata": {"name": "b", "version": "1.0"},
        "nodes": [{
            "id": "implement", "name": "Implement", "node_type": "dag", "is_start": True, "is_end": True,
            "tasks": [{
                "id": "t1", "name": "One", "role": "backend", "criticality": "critical",
                "ownership": ["src/**"], "acceptance": ["it works"], "writes": False,
                "model": "opus", "max_cost": 2.0,
            }],
        }],
        "edges": [],
    }
    rendered = _generate_graph_yaml(builder)
    for fragment in ('role: "backend"', 'criticality: "critical"', "writes: false",
                     '- "src/**"', '- "it works"', 'model: "opus"', "max_cost: 2.0"):
        assert fragment in rendered, fragment
    assert _tasks(rendered)["t1"].role == "backend"


def test_the_builder_writes_nothing_for_a_task_that_declared_nothing():
    """A graph authored without runtime metadata must round-trip unchanged."""
    builder = {
        "metadata": {"name": "b", "version": "1.0"},
        "nodes": [{
            "id": "implement", "name": "Implement", "node_type": "dag", "is_start": True, "is_end": True,
            "tasks": [{"id": "t1", "name": "One"}],
        }],
        "edges": [],
    }
    rendered = _generate_graph_yaml(builder)
    for absent in ("role:", "criticality:", "complexity:", "ownership:", "writes:",
                   "acceptance:", "max_cost:", "max_turns:", "timeout_s:"):
        assert absent not in rendered, absent


def test_the_bundled_workflows_still_parse():
    """The nine shipped graphs predate every field above and must be untouched."""
    from pathlib import Path

    import vise
    workflows = Path(vise.__file__).parent / "assets" / "workflows"
    graphs = sorted(workflows.glob("*-graph.yaml"))
    assert graphs, "no bundled workflows found"
    for path in graphs:
        graph = parse_graph_yaml(path.read_text(encoding="utf-8"))
        assert graph.nodes, path.name


def test_requires_human_survives_the_parser_and_the_builder():
    yaml_text = PLAIN_YAML.replace(
        '        name: "One"', '        name: "One"\n        requires_human: true'
    )
    assert _tasks(yaml_text)["t1"].requires_human is True

    builder = {
        "metadata": {"name": "b", "version": "1.0"},
        "nodes": [{
            "id": "implement", "name": "Implement", "node_type": "dag",
            "is_start": True, "is_end": True,
            "tasks": [{"id": "t1", "name": "One", "requires_human": True}],
        }],
        "edges": [],
    }
    rendered = _generate_graph_yaml(builder)
    assert "requires_human: true" in rendered
    assert _tasks(rendered)["t1"].requires_human is True


def test_a_task_that_declares_nothing_does_not_require_a_human():
    assert _tasks(PLAIN_YAML)["t1"].requires_human is False
