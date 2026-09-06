"""The three gates that read a neighbour's files instead of asking an agent.

vise cannot call livespec or flowtrace. That was taken to mean it could know
nothing about them, so every phase depending on one asked the agent to check —
which makes the check advice, re-weighable by the party being checked. These
gates read the artifact.

What each test here is really pinning is the split every one of them makes:

    absent      -> fail closed. There is no index / no trace; the phase's
                   premise is false and it must not proceed.
    unreadable  -> unverified. vise could not tell, and a gate that refuses on
                   its own bug is how an override habit starts.
    present     -> pass, with what was found in the evidence.

Collapsing those two failure modes into one is the mistake this file exists to
catch.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from vise.engines.validators import (
    _REGISTRY,
    SymbolIndexValidator,
    TraceCapturedValidator,
    TraceErrorGoneValidator,
    build_validators,
)


@dataclass
class _Goal:
    project_dir: str
    id: str = "goal-1"
    #: `trace_captured` filters traces older than the goal. Empty means the
    #: validator cannot parse a cutoff and looks at every trace, which is the
    #: right fallback and is pinned below.
    started_at: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)


@pytest.fixture
def goal(tmp_path: Path) -> _Goal:
    return _Goal(project_dir=str(tmp_path))


def _index(project: Path, *, finished: str | None = "2026-09-06 20:00:00") -> None:
    db = project / ".mcp-docs" / "docs.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE index_run (id INTEGER PRIMARY KEY, project_id INTEGER, "
        "started_at TEXT, finished_at TEXT)"
    )
    conn.execute(
        "INSERT INTO index_run(project_id, started_at, finished_at) VALUES (1,?,?)",
        ("2026-09-06 19:59:00", finished),
    )
    conn.commit()
    conn.close()


def _event(**over) -> dict:
    row = {
        "ts": 1700000000.0, "trace_id": "a" * 32, "span_id": "b" * 16,
        "parent_id": None, "event": "enter", "lang": "python",
        "module": "shop", "class": "Cart", "method": "total", "depth": 0,
    }
    row.update(over)
    return row


def _trace(project: Path, rows: list[dict], name: str = "run.jsonl") -> Path:
    d = project / ".flowtrace"
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


#: A reproduction: one call that raised.
_FAILING = [
    _event(),
    _event(event="exit", duration_ns=10, error={"type": "ValueError", "msg": "boom"}),
]
#: The same run after the fix: same call, no error.
_FIXED = [_event(), _event(event="exit", duration_ns=10)]


# ---------------------------------------------------------------------------
# symbol_index
# ---------------------------------------------------------------------------

def test_no_index_blocks_the_node_that_writes(goal):
    rec = SymbolIndexValidator().run(goal)
    assert not rec.passed and rec.outcome == "failed"
    assert "index_project" in rec.evidence, (
        "a refusal that does not name the call that lifts it is a wall"
    )


def test_an_index_passes_and_says_when_it_ran(goal, tmp_path: Path):
    _index(tmp_path)
    rec = SymbolIndexValidator().run(goal)
    assert rec.passed and rec.outcome == "verified"
    assert "2026-09-06 20:00:00" in rec.evidence


def test_an_unreadable_index_is_unverified_not_a_refusal(goal, tmp_path: Path):
    """The distinction the gate exists to make.

    Refusing here would block a repo that *is* indexed because vise could not
    open the file — a gate failing on its own bug, which is exactly what
    teaches people to set VISE_NODE_GATE_OVERRIDE=1.
    """
    db = tmp_path / ".mcp-docs" / "docs.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"not a database")
    rec = SymbolIndexValidator().run(goal)
    assert rec.passed and rec.outcome == "unverified"
    assert rec.source == "asserted", "an unverified pass must not read as mechanical"


def test_a_half_finished_index_run_does_not_count(goal, tmp_path: Path):
    _index(tmp_path, finished=None)
    assert not SymbolIndexValidator().run(goal).passed


# ---------------------------------------------------------------------------
# trace_captured
# ---------------------------------------------------------------------------

def test_no_trace_fails_and_names_the_command(goal):
    rec = TraceCapturedValidator().run(goal)
    assert not rec.passed and rec.outcome == "failed"
    assert "flowtrace run" in rec.evidence


def test_an_empty_trace_blames_the_prefix_not_the_program(goal, tmp_path: Path):
    """flowtrace's own docs: an empty trace "looks like a bug in the code" and
    is almost always the package prefix. A gate that cannot say which of the
    two it is sends the builder to debug the wrong thing."""
    _trace(tmp_path, [])
    rec = TraceCapturedValidator().run(goal)
    assert not rec.passed
    assert "prefix" in rec.evidence
    assert "did nothing" in rec.evidence


def test_a_real_trace_passes_with_its_counts(goal, tmp_path: Path):
    _trace(tmp_path, _FAILING)
    rec = TraceCapturedValidator().run(goal)
    assert rec.passed and rec.outcome == "verified"
    assert "2 events" in rec.evidence


def test_several_interleaved_executions_are_called_out(goal, tmp_path: Path):
    """A server writes many trace ids into one file and a conclusion drawn
    across them is worthless. Passing silently would hide that."""
    rows = _FAILING + [_event(trace_id="c" * 32), _event(trace_id="d" * 32)]
    _trace(tmp_path, rows)
    rec = TraceCapturedValidator().run(goal)
    assert rec.passed
    assert "3 interleaved executions" in rec.evidence
    assert "trace_id" in rec.evidence


def test_a_trace_older_than_the_goal_does_not_satisfy_the_phase(tmp_path: Path):
    """Otherwise last week's trace passes a phase that traced nothing."""
    import os

    path = _trace(tmp_path, _FAILING)
    os.utime(path, (1, 1))
    goal = _Goal(project_dir=str(tmp_path), started_at="2026-09-06T20:00:00Z")
    rec = TraceCapturedValidator().run(goal)
    assert not rec.passed and "nothing was traced" in rec.evidence


def test_an_unparseable_start_time_looks_at_every_trace(tmp_path: Path):
    """No cutoff is the honest fallback: refusing a real trace because vise
    could not read its own timestamp would fail on vise's bug."""
    import os

    path = _trace(tmp_path, _FAILING)
    os.utime(path, (1, 1))
    goal = _Goal(project_dir=str(tmp_path), started_at="not a timestamp")
    assert TraceCapturedValidator().run(goal).passed


# ---------------------------------------------------------------------------
# trace_error_gone — the pair, which is where the value is
# ---------------------------------------------------------------------------

def test_without_a_reproduction_there_is_nothing_to_compare(goal, tmp_path: Path):
    _trace(tmp_path, _FIXED)
    rec = TraceErrorGoneValidator().run(goal)
    assert rec.passed and rec.outcome == "unverified"
    assert "trace_captured" in rec.evidence, (
        "the fix for an empty comparison is to gate the reproduction phase; "
        "say so rather than passing quietly"
    )


def test_a_failure_that_still_raises_blocks_the_fix(goal, tmp_path: Path):
    """`tests_pass` is green for a fix that deleted the failing test. This is
    the gate that says the traced call itself stopped raising."""
    _trace(tmp_path, _FAILING)
    assert TraceCapturedValidator().run(goal).passed

    rec = TraceErrorGoneValidator().run(goal)
    assert not rec.passed and rec.outcome == "failed"
    assert "shop.Cart.total" in rec.evidence


def test_a_failure_that_stopped_raising_passes(goal, tmp_path: Path):
    _trace(tmp_path, _FAILING)
    TraceCapturedValidator().run(goal)

    _trace(tmp_path, _FIXED, name="after.jsonl")
    rec = TraceErrorGoneValidator().run(goal)
    assert rec.passed and rec.outcome == "verified"
    assert "shop.Cart.total" in rec.evidence


def test_a_reproduction_with_no_error_event_cannot_be_verified(goal, tmp_path: Path):
    """The trace ran and nothing in it raised, so this gate saw no failure to
    check. Reporting "verified" would claim a comparison nobody made."""
    _trace(tmp_path, _FIXED)
    TraceCapturedValidator().run(goal)
    rec = TraceErrorGoneValidator().run(goal)
    assert rec.passed and rec.outcome == "unverified"
    assert "no error event" in rec.evidence


def test_a_missing_verification_trace_is_a_failure_not_a_pass(goal, tmp_path: Path):
    """A fix nobody re-measured is a fix nobody verified — and the debug
    workflow's verify phase demands the same capture be re-run."""
    _trace(tmp_path, _FAILING)
    TraceCapturedValidator().run(goal)
    for stale in (tmp_path / ".flowtrace").glob("*.jsonl"):
        stale.unlink()
    rec = TraceErrorGoneValidator().run(goal)
    assert not rec.passed and rec.outcome == "failed"
    assert "Re-run the exact capture" in rec.evidence


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "name", ["symbol_index", "trace_captured", "trace_error_gone"]
)
def test_each_gate_is_reachable_from_a_workflow_file(name: str):
    assert name in _REGISTRY
    built = build_validators([{"type": name, "weight": 0.5}])
    assert [v.name for v in built] == [name]
    assert built[0].weight == 0.5
