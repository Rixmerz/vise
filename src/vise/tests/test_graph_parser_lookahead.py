"""What vise's own YAML reader does to a comment it was not expecting.

vise parses its workflows with a hand-rolled reader rather than PyYAML, so a
workflow is only what *this* code says it is. The suite parses the bundled
graphs, but a test that asserts a node it can see is a test that cannot notice a
node that silently stopped existing.

The defect: inside a list item, a key with no inline value looks ahead one line
to decide whether what follows is a nested list or a nested dict. The look-ahead
never skipped comments, so a comment between the key and its list made the
branch fall through — dropping the value *and* every sibling key after it.
`security-audit-graph.yaml` ships exactly that shape, and its `fix-criticals`
node lost its validators, its name and its prompt.

This matters more the moment a graph is generated and stored rather than
hand-written: a reader that drops content is a persistence layer that loses it,
with no error anywhere.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from vise.engines.graph_parser import parse_graph_yaml, parse_yaml_simple

ASSETS = Path(__file__).resolve().parents[1] / "assets" / "workflows"


def _parse(text: str) -> dict:
    return parse_yaml_simple(textwrap.dedent(text))


def test_a_comment_between_a_key_and_its_list_loses_neither():
    data = _parse(
        """
        nodes:
          - id: "fix-criticals"
            validators:
              # A patch that breaks the suite is not a patch "in place".
              - type: "tests_pass"
                weight: 0.4
            name: "Fix Criticals"
        """
    )
    node = data["nodes"][0]
    assert node["validators"] == [{"type": "tests_pass", "weight": 0.4}]
    assert node["name"] == "Fix Criticals", "the sibling key after the list survived"


def test_a_comment_between_a_key_and_its_nested_dict_loses_neither():
    data = _parse(
        """
        nodes:
          - id: "n"
            limits:
              # why this ceiling
              max_cost: 5
            name: "N"
        """
    )
    node = data["nodes"][0]
    assert node["limits"] == {"max_cost": 5}
    assert node["name"] == "N"


def test_a_blank_line_between_a_key_and_its_list_loses_neither():
    data = _parse(
        """
        nodes:
          - id: "n"
            validators:

              - type: "tests_pass"
            name: "N"
        """
    )
    assert data["nodes"][0]["validators"] == [{"type": "tests_pass"}]
    assert data["nodes"][0]["name"] == "N"


def test_the_shipped_security_audit_node_still_has_its_gate():
    """The asset that carries the shape, read the way the runtime reads it."""
    graph = parse_graph_yaml(
        (ASSETS / "security-audit-graph.yaml").read_text(encoding="utf-8")
    )
    node = graph.nodes["fix-criticals"]
    assert node.validators, "fix-criticals ships with no gate at all"
    assert node.name and node.name != "fix-criticals"
    assert node.prompt_injection


@pytest.mark.parametrize("path", sorted(ASSETS.glob("*-graph.yaml")))
def test_no_bundled_node_silently_loses_a_key_to_a_comment(path):
    """Every bundled graph, compared against the reference parser.

    A hand-rolled reader is allowed to support less than PyYAML. It is not
    allowed to read the same document differently and say nothing.
    """
    yaml = pytest.importorskip("yaml")
    text = path.read_text(encoding="utf-8")
    ours = parse_yaml_simple(text)
    theirs = yaml.safe_load(text)
    for mine, ref in zip(ours.get("nodes", []), theirs.get("nodes", [])):
        missing = set(ref) - set(mine)
        assert not missing, f"{path.name}: node {ref.get('id')} lost {sorted(missing)}"
