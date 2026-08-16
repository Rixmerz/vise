"""The gate log has to answer the one question stored state cannot.

``node_gate_state`` counts failed attempts per node, and that counter advances
identically whether the gate was fixed or bypassed with
``VISE_NODE_GATE_OVERRIDE=1``. So "is anyone routing around this gate?" — the
only question that says whether a gate is working — was unanswerable from
everything vise persisted.

``gates.jsonl`` answers it. These tests pin the three properties that make it
trustworthy:

  1. It records the override as a DISTINCT kind from the block, or the log
     inherits the same blindness as the counter it replaces.
  2. It records every validator outcome, on green gates too. A node that passes
     having verified nothing is the failure mode worth catching, and it never
     produces a blocked event.
  3. It never raises. A telemetry write that can break a session is worse than
     no telemetry, so an unwritable directory is a warning and a no-op.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from vise.engines.telemetry import read_events, record_event


@pytest.fixture(autouse=True)
def _isolated_telemetry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "telemetry"
    monkeypatch.setenv("VISE_TELEMETRY_DIR", str(d))
    return d


def test_event_round_trips(_isolated_telemetry: Path):
    record_event("workflow_activated", graph="feature-dev", node="orient")

    events = read_events()
    assert len(events) == 1
    assert events[0]["kind"] == "workflow_activated"
    assert events[0]["graph"] == "feature-dev"
    assert events[0]["ts"], "every event carries a timestamp"


def test_override_is_a_different_kind_from_block():
    """The whole point. Collapsing these reproduces the blindness of `attempts`."""
    record_event("node_gate_blocked", node="spec", attempts=1)
    record_event("node_gate_overridden", node="spec", attempts=2)

    kinds = [e["kind"] for e in read_events()]
    assert kinds == ["node_gate_blocked", "node_gate_overridden"]


def test_unknown_kinds_are_dropped_not_written():
    """The allowlist is the schema. A typo'd kind must not silently pollute."""
    record_event("workflow_activated", graph="debug")
    record_event("definitely_not_a_kind", graph="debug")

    assert [e["kind"] for e in read_events()] == ["workflow_activated"]


def test_reader_skips_a_torn_line_instead_of_giving_up(_isolated_telemetry: Path):
    """Several processes append; a crash leaves a partial final line."""
    record_event("workflow_activated", graph="debug")
    path = _isolated_telemetry / "gates.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"kind": "workflow_activ')  # torn write, no newline

    events = read_events()
    assert len(events) == 1, "one good line must survive a broken one"
    assert events[0]["graph"] == "debug"


def test_reading_an_absent_log_is_empty_not_an_error():
    """The log is optional; a fresh install has never written one."""
    assert read_events() == []


def test_limit_returns_the_most_recent(_isolated_telemetry: Path):
    for i in range(5):
        record_event("workflow_activated", graph=f"g{i}")

    recent = read_events(limit=2)
    assert [e["graph"] for e in recent] == ["g3", "g4"]


def test_a_failing_write_never_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """A hook path calls this. Raising here takes the user's session with it."""
    blocked = tmp_path / "not-a-dir"
    blocked.write_text("i am a file, not a directory")
    monkeypatch.setenv("VISE_TELEMETRY_DIR", str(blocked / "nested"))

    record_event("workflow_activated", graph="debug")  # must not raise
    assert read_events() == []


def test_events_are_one_json_object_per_line(_isolated_telemetry: Path):
    """Append-only JSONL: concurrent writers must never need to rewrite the file."""
    record_event("workflow_activated", graph="a")
    record_event("workflow_activated", graph="b")

    lines = (_isolated_telemetry / "gates.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        json.loads(line)


# ---------------------------------------------------------------------------
# The reader that turns the log into a decision
# ---------------------------------------------------------------------------

def _insights(**kw) -> dict:
    """Run the CLI's report builder and return its JSON payload."""
    import argparse
    import io
    from contextlib import redirect_stdout

    from vise.cli.insights_cmd import _cmd_insights

    ns = argparse.Namespace(limit=None, json=True, **kw)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = _cmd_insights(ns)
    assert rc == 0
    return json.loads(buf.getvalue())


def test_insights_computes_the_override_rate():
    record_event("node_gate_blocked", node="spec")
    record_event("node_gate_blocked", node="spec")
    record_event("node_gate_overridden", node="spec")
    record_event("node_gate_overridden", node="validate")

    gates = _insights()["gates"]
    assert gates["red"] == 4
    assert gates["overridden"] == 2
    assert gates["override_rate"] == 0.5
    assert gates["most_overridden_nodes"] == {"spec": 1, "validate": 1}


def test_insights_separates_passed_from_verified():
    """A validator that skip-passes reports passed=True and outcome=unverified.

    Counting those as working is the false green the `outcome` field was added
    to prevent; a report that only counted `passed` would reintroduce it.
    """
    for _ in range(3):
        record_event("validator_outcome", node="validate", validator="lint_pass",
                     passed=True, outcome="unverified", source="asserted")
    record_event("validator_outcome", node="validate", validator="tests_pass",
                 passed=True, outcome="verified", source="mechanical")

    v = _insights()["validators"]
    assert v["runs"] == 4
    assert v["by_outcome"] == {"unverified": 3, "verified": 1}
    assert v["verified_rate"] == 0.25
    assert v["never_verified"] == ["lint_pass"], (
        "a validator that has never once verified anything on this machine is "
        "the actionable finding — it is installed nowhere or bound to nothing"
    )


def test_a_validator_that_verified_once_is_not_reported_as_never():
    record_event("validator_outcome", validator="lint_pass", outcome="unverified")
    record_event("validator_outcome", validator="lint_pass", outcome="verified")

    assert _insights()["validators"]["never_verified"] == []


def test_insights_on_an_empty_log_reports_no_rates():
    """Zero events must yield None, never a divide-by-zero or a fake 0%."""
    report = _insights()
    assert report["events_read"] == 0
    assert report["gates"]["override_rate"] is None
    assert report["validators"]["verified_rate"] is None
