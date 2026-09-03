"""A composed graph may not write the condition it is judged by.

`runtime/replan.py` states the principle: *"A replanner recomposes; it never
authors a gate. […] A planner allowed to write the condition it is judged by
is a planner grading its own homework, which is the exact failure mode this
codebase exists to prevent."*

The replanner is bounded by construction — it adds a `design` task with a
hard-coded role and no validators at all. The builder tools were not:
`graph_builder_add_node(validators=[...])` accepted anything, and
`graph_builder_save` checked only that the generated YAML parsed. An agent
could compose a node and choose the gate that would judge it.

The line is drawn where it can be drawn mechanically. Every allowed validator
runs vise's own reviewed logic; the two refused ones run a command the
*repository* chose, which is a different kind of trust.

This binds the builder, not YAML written by hand — a person editing a workflow
file is authoring, and the file is reviewed as a file.
"""
from __future__ import annotations

import pytest

from vise.engines.validators import _REGISTRY
from vise.tools._graph_builder import (
    BUILDER_VALIDATORS,
    BUILDER_VALIDATORS_EXCLUDED,
)


@pytest.fixture
def tools():
    registered: dict = {}

    class _FakeMCP:
        def tool(self, *a, **kw):
            def deco(fn):
                registered[fn.__name__] = fn
                return fn
            return deco

    from vise.tools._graph_builder import register_graph_builder_tools

    register_graph_builder_tools(_FakeMCP())
    return registered


# --- the list itself -------------------------------------------------------


def test_every_validator_is_decided_one_way_or_the_other():
    """Adding a validator must be a decision, not a default.

    An allowlist whose complement is implicit lets the next validator through
    by accident, and the next validator is exactly the one nobody thought
    about. Union coverage forces the author to place it.
    """
    placed = BUILDER_VALIDATORS | set(BUILDER_VALIDATORS_EXCLUDED)
    registry = set(_REGISTRY)

    assert registry - placed == set(), (
        "validators in the registry that the builder policy does not mention — "
        f"allow them or exclude them with a reason: {sorted(registry - placed)}"
    )
    assert placed - registry == set(), (
        f"the builder policy names validators that do not exist: {sorted(placed - registry)}"
    )


def test_nothing_is_both_allowed_and_excluded():
    assert not (BUILDER_VALIDATORS & set(BUILDER_VALIDATORS_EXCLUDED))


def test_every_exclusion_carries_its_reason():
    for name, why in BUILDER_VALIDATORS_EXCLUDED.items():
        assert why and len(why) > 20, f"{name} is excluded without saying why"


def test_the_two_that_run_repo_chosen_commands_are_the_excluded_ones():
    """The property that makes the split defensible rather than a taste call."""
    assert set(BUILDER_VALIDATORS_EXCLUDED) == {"command_exit", "quality_check"}


# --- the enforcement -------------------------------------------------------


def _node(tools, validators, node_id="n"):
    builder = tools["graph_builder_create"](name="g")["builder_id"]
    return tools["graph_builder_add_node"](
        builder_id=builder, node_id=node_id, name="N",
        is_start=True, validators=validators,
    ), builder


@pytest.mark.parametrize("allowed", sorted(BUILDER_VALIDATORS))
def test_an_allowed_validator_is_accepted(tools, allowed):
    out, _ = _node(tools, [{"type": allowed, "weight": 1.0}])
    assert out["success"] is True, out


@pytest.mark.parametrize("refused", sorted(BUILDER_VALIDATORS_EXCLUDED))
def test_a_refused_validator_is_named_and_explained(tools, refused):
    out, _ = _node(tools, [{"type": refused}])

    assert out["success"] is False
    assert refused in out["message"]
    assert "grading its own homework" in out["message"], (
        "the refusal must say why, or it reads as an arbitrary restriction"
    )
    assert "by hand" in out["message"], "and it must name the way that is legitimate"


def test_an_unknown_validator_is_refused_with_the_list(tools):
    out, _ = _node(tools, [{"type": "definitely_not_a_validator"}])

    assert out["success"] is False
    assert "tests_pass" in out["message"], "a refusal should show what IS allowed"


def test_a_refused_node_is_not_added(tools):
    out, builder = _node(tools, [{"type": "command_exit"}])

    assert out["success"] is False
    preview = tools["graph_builder_preview"](builder_id=builder)
    assert "definitely" not in str(preview)
    assert tools["graph_builder_add_node"](
        builder_id=builder, node_id="n", name="N", is_start=True,
    )["success"] is True, "the refused id must still be free"


def test_no_validators_at_all_is_fine(tools):
    out, _ = _node(tools, None)
    assert out["success"] is True


def test_an_empty_list_is_fine(tools):
    out, _ = _node(tools, [])
    assert out["success"] is True


def test_update_node_cannot_slip_one_past(tools):
    """The obvious way around a check on creation."""
    out, builder = _node(tools, [{"type": "tests_pass"}])
    assert out["success"] is True

    patched = tools["graph_builder_update_node"](
        builder_id=builder, node_id="n", validators=[{"type": "quality_check"}],
    )

    assert patched["success"] is False
    assert "quality_check" in patched["message"]


def test_update_node_still_accepts_an_allowed_one(tools):
    _out, builder = _node(tools, [{"type": "tests_pass"}])

    patched = tools["graph_builder_update_node"](
        builder_id=builder, node_id="n", validators=[{"type": "lint_pass"}],
    )

    assert patched["success"] is True
    assert "validators" in patched["patched"]


def test_a_mixed_list_is_refused_whole(tools):
    """One refused entry refuses the call — not a silent partial accept."""
    out, builder = _node(
        tools, [{"type": "tests_pass"}, {"type": "command_exit"}],
    )

    assert out["success"] is False
    preview = tools["graph_builder_preview"](builder_id=builder)
    assert "tests_pass" not in str(preview), "half the list was kept"
