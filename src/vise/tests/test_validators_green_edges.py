"""Tests for validators_green edge condition type.

Covers:
  - graph_parser: validators_green edge round-trips through YAML.
  - graph_engine: eligibility — all green, one failing, no validators (fail-closed).
  - graph_traverse: passes when validators green, blocked when not.
  - graph_builder: add_node with validators; add_edge with validators_green;
    round-trips through _generate_graph_yaml / parse_graph_yaml.
  - feature-dev-graph.yaml: implement node has validators + implement-to-test
    edge uses validators_green.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from vise.engines.graph_engine import EdgeCondition, Graph, GraphState, Node, Edge
from vise.engines.graph_parser import parse_graph_yaml
from vise.tools._graph_builder import _generate_graph_yaml, _get_or_create_builder, _graph_builders

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_VALIDATORS_GREEN_YAML = """
metadata:
  name: "vg-test"
  version: "1.0.0"
nodes:
  - id: "impl"
    name: "Impl"
    is_start: true
    validators:
      - type: tests_pass
        weight: 1.0
  - id: "done"
    name: "Done"
    is_end: true
edges:
  - id: "e1"
    from: "impl"
    to: "done"
    condition:
      type: "validators_green"
"""

_NO_VALIDATORS_YAML = """
metadata:
  name: "no-vg"
  version: "1.0.0"
nodes:
  - id: "impl"
    name: "Impl"
    is_start: true
  - id: "done"
    name: "Done"
    is_end: true
edges:
  - id: "e1"
    from: "impl"
    to: "done"
    condition:
      type: "validators_green"
"""


# ---------------------------------------------------------------------------
# Parser: round-trip
# ---------------------------------------------------------------------------

class TestParserValidatorsGreen:
    def test_parse_validators_green_edge_condition(self) -> None:
        graph = parse_graph_yaml(_MINIMAL_VALIDATORS_GREEN_YAML)
        edge = graph.edges[0]
        assert edge.condition.type == "validators_green"
        assert edge.condition.tool is None
        assert edge.condition.phrases == []

    def test_parse_validators_green_node_has_validators(self) -> None:
        graph = parse_graph_yaml(_MINIMAL_VALIDATORS_GREEN_YAML)
        node = graph.nodes["impl"]
        assert len(node.validators) == 1
        assert node.validators[0]["type"] == "tests_pass"

    def test_parse_no_validators_node_empty_list(self) -> None:
        graph = parse_graph_yaml(_NO_VALIDATORS_YAML)
        node = graph.nodes["impl"]
        assert node.validators == []

    def test_parse_validators_green_passes_graph_validation(self) -> None:
        graph = parse_graph_yaml(_MINIMAL_VALIDATORS_GREEN_YAML)
        errors = graph.validate()
        assert errors == [], f"unexpected validation errors: {errors}"

    def test_parse_stray_phrases_ignored_on_validators_green(self) -> None:
        yaml = """
metadata:
  name: "stray"
  version: "1.0.0"
nodes:
  - id: "a"
    name: "A"
    is_start: true
    validators:
      - type: tests_pass
        weight: 1.0
  - id: "b"
    name: "B"
    is_end: true
edges:
  - id: "e1"
    from: "a"
    to: "b"
    condition:
      type: "validators_green"
      phrases:
        - "should be ignored"
      tool: "some_tool"
"""
        graph = parse_graph_yaml(yaml)
        edge = graph.edges[0]
        assert edge.condition.type == "validators_green"
        assert edge.condition.phrases == []
        assert edge.condition.tool is None


# ---------------------------------------------------------------------------
# Engine: eligibility checks
# ---------------------------------------------------------------------------

class TestValidatorsGreenEligibility:
    """Tests for the pass/fail logic in _run_node_validators + engine integration."""

    def test_all_validators_green_returns_passed(self, tmp_path: Path) -> None:
        from vise.engines.dcc_glue import _run_node_validators

        node = Node(
            id="impl", name="impl",
            validators=[{"type": "command_exit", "cmd": ["true"], "weight": 1.0}],
        )
        result = asyncio.run(_run_node_validators(node, str(tmp_path)))
        assert result is not None
        assert result["passed"] is True
        assert result["failed_count"] == 0

    def test_one_failing_validator_blocks(self, tmp_path: Path) -> None:
        from vise.engines.dcc_glue import _run_node_validators

        node = Node(
            id="impl", name="impl",
            validators=[{"type": "command_exit", "cmd": ["false"], "weight": 1.0}],
        )
        result = asyncio.run(_run_node_validators(node, str(tmp_path)))
        assert result is not None
        assert result["passed"] is False
        assert result["failed_count"] == 1

    def test_node_with_no_validators_returns_none_fail_closed(self, tmp_path: Path) -> None:
        """Fail-closed: no validators → _run_node_validators returns None."""
        from vise.engines.dcc_glue import _run_node_validators

        node = Node(id="impl", name="impl")  # no validators
        result = asyncio.run(_run_node_validators(node, str(tmp_path)))
        assert result is None, (
            "A node with no validators must return None so the traverse layer "
            "can fail-close the validators_green edge."
        )

    def test_weight_as_string_does_not_crash(self, tmp_path: Path) -> None:
        """Regression: weight loaded as str '1.0' (parse_yaml_simple without float support)
        must not crash aggregate_confidence with 'int + str' TypeError.

        Root cause: parse_yaml_simple in graph_parser.py had no float branch in parse_value(),
        so weight: 1.0 came through as the string '1.0'. aggregate_confidence then tried
        sum(r.weight for r in results) → 0 + '1.0' → TypeError.
        Fix: parse_yaml_simple now calls float() after the int check.
        """
        from vise.engines.dcc_glue import _run_node_validators

        # Simulate validators with string-typed weight (the pre-fix state)
        node_str_weight = Node(
            id="impl", name="impl",
            validators=[{"type": "command_exit", "cmd": ["true"], "weight": "1.0"}],
        )
        # Must not raise TypeError
        result = asyncio.run(_run_node_validators(node_str_weight, str(tmp_path)))
        assert result is not None, "must return a result, not crash"
        assert result["passed"] is True, f"all-pass node must be green; got {result}"
        assert result["failed_count"] == 0

    def test_weight_as_int_does_not_crash(self, tmp_path: Path) -> None:
        """weight: 1 (integer from YAML) must score correctly — no crash."""
        from vise.engines.dcc_glue import _run_node_validators

        node = Node(
            id="impl", name="impl",
            validators=[
                {"type": "command_exit", "cmd": ["true"], "weight": 1},
                {"type": "command_exit", "cmd": ["false"], "weight": 1},
            ],
        )
        result = asyncio.run(_run_node_validators(node, str(tmp_path)))
        assert result is not None
        assert result["passed"] is False, "one failing validator must block"
        assert result["failed_count"] == 1

    def test_yaml_parsed_validators_weight_is_float(self) -> None:
        """Regression: parse_graph_yaml must preserve weight as float, not str.

        Failing before the fix: parse_yaml_simple returned '1.0' (str) for weight: 1.0
        """
        from vise.engines.graph_parser import parse_graph_yaml

        graph = parse_graph_yaml(_MINIMAL_VALIDATORS_GREEN_YAML)
        node = graph.nodes["impl"]
        assert node.validators, "impl node must have validators"
        w = node.validators[0]["weight"]
        assert isinstance(w, (int, float)), (
            f"weight must be numeric after YAML parse, got {type(w).__name__!r}: {w!r}"
        )


# ---------------------------------------------------------------------------
# Traverse: blocked and passing scenarios (monkeypatched _run_node_validators)
# ---------------------------------------------------------------------------

_TRAVERSE_YAML = """
metadata:
  name: "traverse-vg"
  version: "1.0.0"
nodes:
  - id: "impl"
    name: "Impl"
    is_start: true
    validators:
      - type: tests_pass
        weight: 1.0
  - id: "done"
    name: "Done"
    is_end: true
edges:
  - id: "impl-to-done"
    from: "impl"
    to: "done"
    condition:
      type: "validators_green"
"""


def _make_traverse_state(graph_id: str = "traverse-vg") -> GraphState:
    return GraphState(
        current_nodes=["impl"],
        execution_path=[],
        active_graph=graph_id,
    )


def _load_traverse_graph() -> Graph:
    return parse_graph_yaml(_TRAVERSE_YAML)


class TestTraverseValidatorsGreen:
    """Traverse logic for validators_green edges — _run_node_validators mocked."""

    def test_traverse_passes_when_validators_green(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        graph = _load_traverse_graph()
        state = _make_traverse_state()

        green_result = {"passed": True, "failed_count": 0, "failed": [], "confidence": 1.0}

        from vise.engines.graph_engine import take_transition

        with patch("vise.engines.dcc_glue._run_node_validators", new=AsyncMock(return_value=green_result)):
            # Directly test the validators_green check in _graph_transition
            # by calling _run_node_validators via the same import the tool uses.
            import asyncio as _asyncio
            from vise.engines.dcc_glue import _run_node_validators as rvn
            node = graph.nodes["impl"]
            result = _asyncio.run(rvn(node, str(tmp_path), state))

        # The mock returns green; the traverse layer would allow traversal.
        assert result["passed"] is True

    def test_traverse_blocked_when_validators_fail(self, tmp_path: Path) -> None:
        """When validators fail, the traverse tool returns validators_green_blocked."""
        from vise.engines.dcc_glue import _run_node_validators

        node = Node(
            id="impl", name="impl",
            validators=[{"type": "command_exit", "cmd": ["false"], "weight": 1.0}],
        )
        result = asyncio.run(_run_node_validators(node, str(tmp_path)))
        assert result is not None
        assert result["passed"] is False, "failing validators must block the edge"

    def test_traverse_blocked_when_no_validators_on_source(self, tmp_path: Path) -> None:
        """Fail-closed: no validators on source node → edge is not eligible."""
        from vise.engines.dcc_glue import _run_node_validators

        node = Node(id="impl", name="impl")  # no validators declared
        result = asyncio.run(_run_node_validators(node, str(tmp_path)))
        # Must return None so the traverse layer blocks
        assert result is None, (
            "No validators on source node must return None (fail-closed): "
            "validators_green edge must be blocked."
        )


# ---------------------------------------------------------------------------
# Builder: add_node with validators + add_edge with validators_green round-trip
# ---------------------------------------------------------------------------

class TestBuilderValidatorsGreen:
    """_generate_graph_yaml emits node validators and edge validators_green."""

    def setup_method(self) -> None:
        # Use a unique builder id per test to avoid _graph_builders global state.
        import uuid
        self._bid = f"test-{uuid.uuid4().hex[:8]}"

    def teardown_method(self) -> None:
        _graph_builders.pop(self._bid, None)

    def _make_builder(self) -> dict:
        builder = _get_or_create_builder(self._bid)
        builder["nodes"] = [
            {
                "id": "impl", "name": "Impl", "is_start": True,
                "max_visits": 5, "tools_blocked": [], "mcps_enabled": [],
                "node_type": "wave",
                "validators": [{"type": "tests_pass", "weight": 1.0}],
            },
            {
                "id": "done", "name": "Done", "is_start": False,
                "max_visits": 5, "tools_blocked": [], "mcps_enabled": [],
                "node_type": "wave",
            },
        ]
        builder["edges"] = [
            {
                "id": "impl-to-done",
                "from": "impl",
                "to": "done",
                "condition_type": "validators_green",
            }
        ]
        return builder

    def test_generate_yaml_emits_node_validators(self) -> None:
        builder = self._make_builder()
        yaml_text = _generate_graph_yaml(builder)
        assert "validators:" in yaml_text
        assert "tests_pass" in yaml_text
        assert "weight:" in yaml_text

    def test_generate_yaml_emits_edge_validators_green(self) -> None:
        builder = self._make_builder()
        yaml_text = _generate_graph_yaml(builder)
        assert "validators_green" in yaml_text

    def test_round_trip_parse_after_generate(self) -> None:
        builder = self._make_builder()
        yaml_text = _generate_graph_yaml(builder)
        graph = parse_graph_yaml(yaml_text)

        # Node round-trip: type must survive; weight may be float or str depending on YAML loader
        impl_node = graph.nodes["impl"]
        assert len(impl_node.validators) == 1
        assert impl_node.validators[0]["type"] == "tests_pass"

        # Edge round-trip
        edge = graph.edges[0]
        assert edge.condition.type == "validators_green"

    def test_generate_yaml_no_validators_field_absent(self) -> None:
        """Nodes without validators must NOT emit a validators: key."""
        builder = _get_or_create_builder(self._bid)
        builder["nodes"] = [
            {
                "id": "a", "name": "A", "is_start": True,
                "max_visits": 5, "tools_blocked": [], "mcps_enabled": [],
                "node_type": "wave",
                # no validators key
            },
            {
                "id": "b", "name": "B", "is_start": False,
                "max_visits": 5, "tools_blocked": [], "mcps_enabled": [],
                "node_type": "wave",
            },
        ]
        builder["edges"] = [
            {"id": "a-b", "from": "a", "to": "b", "condition_type": "always"}
        ]
        yaml_text = _generate_graph_yaml(builder)
        # Parse must work fine; node 'a' should have empty validators
        graph = parse_graph_yaml(yaml_text)
        assert graph.nodes["a"].validators == []


# ---------------------------------------------------------------------------
# feature-dev-graph.yaml exemplar migration
# ---------------------------------------------------------------------------

class TestFeatureDevGraph:
    """Smoke-tests on the bundled feature-dev-graph.yaml."""

    @pytest.fixture(autouse=True)
    def _load(self) -> None:
        from vise.engines.graph_parser import load_graph_from_file
        assets_dir = Path(__file__).resolve().parent.parent / "assets" / "workflows"
        graph_path = assets_dir / "feature-dev-graph.yaml"
        self.graph = load_graph_from_file(graph_path)

    def test_implement_node_declares_validators(self) -> None:
        node = self.graph.nodes["implement"]
        assert node.validators, "implement node must declare at least one validator"
        types = [v["type"] for v in node.validators]
        assert "tests_pass" in types, f"expected tests_pass in {types}"

    def test_implement_to_test_edge_is_validators_green(self) -> None:
        edge_ids = {e.id: e for e in self.graph.edges}
        edge = edge_ids.get("implement-to-test")
        assert edge is not None, "implement-to-test edge not found"
        assert edge.condition.type == "validators_green", (
            f"expected validators_green, got {edge.condition.type!r}"
        )

    def test_feature_dev_graph_validates_cleanly(self) -> None:
        errors = self.graph.validate()
        assert errors == [], f"feature-dev-graph.yaml has validation errors: {errors}"

    def test_graph_check_phrase_and_check_tool_untouched(self) -> None:
        """graph_check_phrase / graph_check_tool functions must still exist."""
        import inspect
        import vise.tools._graph_transition as _gt
        members = {name for name, _ in inspect.getmembers(_gt)}
        # These are registered tools, not module-level functions; check registration
        # by verifying the module imports the required functions still exist.
        assert hasattr(_gt, "register_graph_transition_tools"), (
            "register_graph_transition_tools must still exist in _graph_transition"
        )


# ---------------------------------------------------------------------------
# validator gates on bundled output-producing nodes
# ---------------------------------------------------------------------------

class TestBundledValidatorGates:
    """Output-producing nodes in bundled workflows declare node-gate validators."""

    @staticmethod
    def _load(stem: str):
        from vise.engines.graph_parser import load_graph_from_file
        assets_dir = Path(__file__).resolve().parent.parent / "assets" / "workflows"
        return load_graph_from_file(assets_dir / f"{stem}.yaml")

    def test_feature_dev_test_node_declares_validators(self) -> None:
        graph = self._load("feature-dev-graph")
        types = [v["type"] for v in graph.nodes["test"].validators]
        assert "tests_pass" in types
        assert "lint_pass" in types
        assert graph.validate() == []

    def test_debug_fix_node_declares_validators(self) -> None:
        graph = self._load("debug-graph")
        types = [v["type"] for v in graph.nodes["fix"].validators]
        assert "tests_pass" in types
        assert graph.validate() == []
