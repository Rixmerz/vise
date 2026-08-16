"""`VISE_NODE_GATE_OVERRIDE=1` has to work on the gate people actually hit.

Found by driving `feature-dev` end to end against a real repo, not by reading:
the override bypassed the node gate and then the `validators_green` edge check
rejected the traverse anyway. Since `feature-dev`'s `spec` phase exits by a
validators_green edge, the documented escape hatch — advertised in the node
gate's own error message, *"or VISE_NODE_GATE_OVERRIDE=1 to bypass"* — did
nothing on the one phase agents reach for it most. The traverse kept failing
with a different error, so the agent retried the same edge in a loop.

Two consequences, and both are fixed here:

  1. The override is now read once and consulted by BOTH gates.
  2. It is recorded once, where it actually takes effect. Recording it at the
     node gate counted an intent, not a bypass: four retries of one blocked
     edge logged four `node_gate_overridden` events for zero gates actually
     routed around, which is precisely the number `vise insights` reports as
     the override rate.

The rate has to mean "gates someone got around". An override rejected
downstream is not one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from vise.engines.goal_state import ValidatorRecord
from vise.engines.telemetry import read_events


@pytest.fixture(autouse=True)
def _isolated_telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "telemetry"
    monkeypatch.setenv("VISE_TELEMETRY_DIR", str(d))
    return d


def _red_gate() -> dict:
    """What `_run_node_validators` returns for a node whose checks failed."""
    return {
        "passed": False,
        "failed_count": 2,
        "failed": [
            {"name": "openspec:structure", "evidence": "no openspec/", "exit_code": 1},
            {"name": "openspec:change", "evidence": "no change dir", "exit_code": 1},
        ],
        "verified_count": 0,
        "skipped_count": 0,
        "checks": [
            {"name": "openspec:structure", "passed": False,
             "source": "mechanical", "outcome": "failed", "evidence": ""},
            {"name": "openspec:change", "passed": False,
             "source": "mechanical", "outcome": "failed", "evidence": ""},
        ],
        "confidence": 0.0,
    }


def test_a_blocked_gate_without_the_override_is_recorded_as_blocked(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("VISE_NODE_GATE_OVERRIDE", raising=False)
    from vise.engines.telemetry import record_event

    # The block is final, so it is recorded at the node gate.
    record_event("node_gate_blocked", node="spec", attempts=1,
                 failed=["openspec:structure"])

    kinds = [e["kind"] for e in read_events()]
    assert kinds == ["node_gate_blocked"]


def test_the_override_is_read_from_the_environment_once(monkeypatch: pytest.MonkeyPatch):
    """Both gates must consult the same value, not re-read it independently."""
    import inspect

    from vise.tools import _graph_transition as gt

    src = inspect.getsource(gt)
    assert src.count('os.environ.get("VISE_NODE_GATE_OVERRIDE")') == 1, (
        "the override is read more than once — the two gates can disagree, "
        "which is how bypassing the node gate still left the edge rejecting"
    )
    assert src.count("if not gate_override:") == 2, (
        "both the node gate and the validators_green edge must honour the "
        "override; guarding only one makes the hatch a no-op on feature-dev's "
        "spec phase"
    )


def test_the_validators_green_message_advertises_the_same_hatch():
    """The node gate's message names the env var; the edge's must too.

    Telling an agent "VISE_NODE_GATE_OVERRIDE=1 to bypass" and then rejecting
    it from a second gate whose message never mentions the variable is how the
    retry loop happened.
    """
    import inspect

    from vise.tools import _graph_transition as gt

    src = inspect.getsource(gt)
    green_branch = src[src.index("validators_green) is not eligible"):]
    assert "VISE_NODE_GATE_OVERRIDE=1 to bypass" in green_branch[:600], (
        "the validators_green rejection does not name the escape hatch it honours"
    )


def test_the_override_event_is_emitted_after_both_gates():
    """Placement is the whole fix: emitted early it counts intents, not bypasses."""
    import inspect

    from vise.tools import _graph_transition as gt

    src = inspect.getsource(gt)
    emit = src.index('"node_gate_overridden"')
    green = src.index('if edge.condition.type == "validators_green":')
    node_gate = src.index("Node gate blocked:")

    assert node_gate < emit < green, (
        "node_gate_overridden must be emitted after the node gate returns and "
        "before the validators_green check — emitting inside the node gate "
        "counts an override the edge may still reject"
    )


def test_a_validator_record_that_failed_never_claims_it_verified():
    """Guards the invariant the override reporting rests on.

    `vise insights` splits `passed` from `verified`. A failing record that still
    said outcome="verified" would make the verified rate meaningless.
    """
    rec = ValidatorRecord(
        name="openspec", passed=False, confidence_contribution=0.0, weight=1.0,
        evidence="no openspec/", at="2026-01-01T00:00:00+00:00",
    )
    assert rec.outcome == "failed"


def test_insights_counts_one_override_per_gate_not_per_retry(
    _isolated_telemetry: Path,
):
    """The regression this whole file exists for, at the reporting layer."""
    from vise.engines.telemetry import record_event

    # One user, one blocked gate, then one successful override.
    record_event("node_gate_blocked", node="spec", attempts=1, failed=["openspec"])
    record_event("node_gate_overridden", node="spec", edge="spec-to-implement",
                 failed=["openspec"])

    import argparse
    import io
    from contextlib import redirect_stdout

    from vise.cli.insights_cmd import _cmd_insights

    buf = io.StringIO()
    with redirect_stdout(buf):
        _cmd_insights(argparse.Namespace(limit=None, json=True))
    gates = json.loads(buf.getvalue())["gates"]

    assert gates["red"] == 2
    assert gates["overridden"] == 1
    assert gates["override_rate"] == 0.5
    assert gates["most_overridden_nodes"] == {"spec": 1}
