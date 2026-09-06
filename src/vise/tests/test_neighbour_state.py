"""vise reads its neighbours' files. These pin what it reads.

Every fixture here has the shape of a real artifact, checked against one:
the `index_run` row comes from a livespec index of a real repo, the trace
lines are copied from flowtrace's own committed golden capture
(`examples/golden/error/python/expected.jsonl`), and the `external_ingest`
columns are livespec's migration 23. Inventing the shapes would make these
tests pass against a reader that cannot read anything real.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from vise.core.neighbour_state import (
    error_signature,
    graph_state,
    index_state,
    summary,
    trace_state,
)

# ---------------------------------------------------------------------------
# Fixtures, in the shape of the real thing
# ---------------------------------------------------------------------------

#: livespec's `index_run`, columns as in storage/schema.sql.
_INDEX_RUN = """
CREATE TABLE index_run (
    id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    files_total INTEGER DEFAULT 0,
    files_changed INTEGER DEFAULT 0,
    symbols_total INTEGER DEFAULT 0,
    edges_total INTEGER DEFAULT 0
)
"""

#: livespec's `external_ingest`, migration 23.
_EXTERNAL_INGEST = """
CREATE TABLE external_ingest (
    project_id INTEGER NOT NULL,
    origin TEXT NOT NULL,
    graph_path TEXT NOT NULL,
    graph_mtime REAL,
    graph_size INTEGER,
    graph_hash TEXT,
    relations TEXT NOT NULL,
    edges_written INTEGER NOT NULL,
    ingested_at REAL NOT NULL,
    PRIMARY KEY (project_id, origin)
)
"""

#: Two enter/exit pairs, the inner one raising — flowtrace's golden fixture.
_TRACE = [
    {"ts": 1700000000.0, "trace_id": "f10c17ace" + "0" * 23, "span_id": "0" * 15 + "1",
     "parent_id": None, "event": "enter", "thread": "MainThread", "lang": "python",
     "module": "error_fixture", "class": "", "method": "outer",
     "visibility": "public", "args": {"n": 7}, "depth": 0},
    {"ts": 1700000000.001, "trace_id": "f10c17ace" + "0" * 23, "span_id": "0" * 15 + "2",
     "parent_id": "0" * 15 + "1", "event": "enter", "thread": "MainThread",
     "lang": "python", "module": "error_fixture", "class": "", "method": "inner",
     "visibility": "public", "args": {"n": 7}, "depth": 1},
    {"ts": 1700000000.002, "trace_id": "f10c17ace" + "0" * 23, "span_id": "0" * 15 + "2",
     "parent_id": "0" * 15 + "1", "event": "exit", "module": "error_fixture",
     "class": "", "method": "inner", "duration_ns": 1000, "depth": 1,
     "error": {"type": "ValueError", "msg": "boom"}},
    {"ts": 1700000000.003, "trace_id": "f10c17ace" + "0" * 23, "span_id": "0" * 15 + "1",
     "parent_id": None, "event": "exit", "module": "error_fixture", "class": "",
     "method": "outer", "duration_ns": 3000, "depth": 0,
     "error": {"type": "ValueError", "msg": "boom"}},
]


def _index(project: Path, *, finished: str | None = "2026-09-06 20:08:45") -> Path:
    db = project / ".mcp-docs" / "docs.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute(_INDEX_RUN)
    conn.execute(
        "INSERT INTO index_run(project_id, started_at, finished_at) VALUES (1, ?, ?)",
        ("2026-09-06 20:08:40", finished),
    )
    conn.commit()
    conn.close()
    return db


def _trace(project: Path, rows: list[dict] | None = None, name: str = "t.jsonl") -> Path:
    d = project / ".flowtrace"
    d.mkdir(parents=True, exist_ok=True)
    path = d / name
    body = "".join(json.dumps(r) + "\n" for r in (_TRACE if rows is None else rows))
    path.write_text(body, encoding="utf-8")
    return path


def _graph(project: Path, payload: str = '{"nodes": [], "links": []}') -> Path:
    path = project / "graphify-out" / "graph.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return path


def _record_ingest(project: Path, graph: Path, *, size: int | None = None) -> None:
    db = project / ".mcp-docs" / "docs.db"
    stat = graph.stat()
    conn = sqlite3.connect(db)
    conn.execute(_EXTERNAL_INGEST)
    conn.execute(
        "INSERT INTO external_ingest VALUES (1,'external:graphify',?,?,?,?,'calls',5,?)",
        (str(graph), stat.st_mtime,
         stat.st_size if size is None else size, "deadbeef", time.time()),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# livespec
# ---------------------------------------------------------------------------

def test_no_database_is_a_known_absence(tmp_path: Path):
    state = index_state(tmp_path)
    assert state.known and not state.indexed
    assert state.refuses, "a repo with no index must make a phase stand down"


def test_a_finished_run_is_a_usable_index(tmp_path: Path):
    _index(tmp_path)
    state = index_state(tmp_path)
    assert state.indexed and not state.refuses
    assert state.last_run_at == "2026-09-06 20:08:45"


def test_a_database_with_no_finished_run_is_not_an_index(tmp_path: Path):
    """`index_project` crashed halfway, or the DB was created by another tool."""
    _index(tmp_path, finished=None)
    state = index_state(tmp_path)
    assert state.known and not state.indexed and state.refuses


def test_an_older_livespec_without_index_run_is_not_an_index(tmp_path: Path):
    db = tmp_path / ".mcp-docs" / "docs.db"
    db.parent.mkdir(parents=True)
    sqlite3.connect(db).close()
    state = index_state(tmp_path)
    assert state.known and not state.indexed


def test_an_unreadable_database_is_unknown_and_does_not_refuse(tmp_path: Path):
    """The distinction the whole module exists for.

    "No index" is a refusal. "Cannot tell" must not be, or a gate refuses on
    its own bug and teaches the user to switch it off.
    """
    db = tmp_path / ".mcp-docs" / "docs.db"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"this is not a database, it is a haiku about one")
    state = index_state(tmp_path)
    assert not state.known
    assert not state.refuses, "an unreadable database must never read as absent"
    assert "could not read" in state.detail


# ---------------------------------------------------------------------------
# flowtrace
# ---------------------------------------------------------------------------

def test_a_trace_is_counted_by_events_traces_and_errors(tmp_path: Path):
    _trace(tmp_path)
    state = trace_state(tmp_path)
    assert (state.events, state.traces, state.errors) == (4, 1, 2)
    assert not state.empty and state.malformed == 0


def test_an_empty_trace_is_distinguished_from_no_trace(tmp_path: Path):
    """flowtrace's own docs: an empty trace is the package prefix, not a bug
    in the traced program. A gate that cannot tell those apart reports the
    wrong cause."""
    absent = trace_state(tmp_path)
    assert absent.known and absent.path is None and not absent.empty

    _trace(tmp_path, rows=[])
    present = trace_state(tmp_path)
    assert present.path is not None and present.empty


def test_a_stale_trace_does_not_satisfy_a_phase(tmp_path: Path):
    _trace(tmp_path)
    fresh = trace_state(tmp_path, newer_than=time.time() + 60)
    assert fresh.known and fresh.path is None
    assert "newer than this phase" in fresh.detail


def test_the_newest_trace_wins(tmp_path: Path):
    old = _trace(tmp_path, name="old.jsonl")
    import os
    os.utime(old, (1, 1))
    _trace(tmp_path, name="new.jsonl")
    assert trace_state(tmp_path).path.name == "new.jsonl"


def test_a_half_written_line_is_reported_not_fatal(tmp_path: Path):
    """The capture layer appends while the program runs."""
    path = _trace(tmp_path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write('{"ts": 170000000')
    state = trace_state(tmp_path)
    assert state.events == 4 and state.malformed == 1


def test_a_json_line_that_is_not_an_object_counts_as_malformed(tmp_path: Path):
    """A valid JSON line is not automatically an event.

    Found by re-breaking: removing the `malformed += 1` on this branch left
    every test green, because the only bad line under test was invalid JSON.
    A bare array or string parses fine and has no `event`, `module` or
    `trace_id` — counting it as an event would inflate the number a gate reads.
    """
    path = _trace(tmp_path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write('[1, 2, 3]\n"just a string"\n')
    state = trace_state(tmp_path)
    assert state.events == 4, "a non-object line was counted as an event"
    assert state.malformed == 2


def test_the_error_signature_names_the_failing_calls(tmp_path: Path):
    _trace(tmp_path)
    assert error_signature(tmp_path) == ("error_fixture.inner", "error_fixture.outer")


def test_the_error_signature_is_empty_without_a_trace(tmp_path: Path):
    assert error_signature(tmp_path) == ()


# ---------------------------------------------------------------------------
# Graphify
# ---------------------------------------------------------------------------

def test_a_graph_nobody_ingested_says_so(tmp_path: Path):
    """The table exists (livespec v0.33+) but nobody ran the ingest."""
    _index(tmp_path)
    _graph(tmp_path)
    conn = sqlite3.connect(tmp_path / ".mcp-docs" / "docs.db")
    conn.execute(_EXTERNAL_INGEST)
    conn.commit()
    conn.close()
    state = graph_state(tmp_path)
    assert state.present and not state.ingested
    assert "never ingested" in state.detail


def test_an_ingested_graph_that_has_not_moved_is_the_same_graph(tmp_path: Path):
    _index(tmp_path)
    graph = _graph(tmp_path)
    _record_ingest(tmp_path, graph)
    state = graph_state(tmp_path)
    assert state.ingested and state.freshness == "same"


def test_a_resized_graph_is_definitely_not_the_ingested_one(tmp_path: Path):
    _index(tmp_path)
    graph = _graph(tmp_path)
    _record_ingest(tmp_path, graph, size=999999)
    state = graph_state(tmp_path)
    assert state.freshness == "differs"
    assert "ingest_external_graph" in state.detail, (
        "a stale verdict must name the call that fixes it"
    )


def test_a_rewritten_same_size_graph_is_unknown_not_stale(tmp_path: Path):
    """Graphify rewrites graph.json on every run, including rebuilds that
    change nothing. Crying stale on a moved mtime would train the reader to
    ignore the field — livespec learned this and hashes content instead."""
    _index(tmp_path)
    graph = _graph(tmp_path)
    _record_ingest(tmp_path, graph)
    import os
    os.utime(graph, (time.time() + 500, time.time() + 500))
    state = graph_state(tmp_path)
    assert state.freshness == "unknown"
    assert "content hash" in state.detail


def test_an_index_without_migration_23_is_not_an_error(tmp_path: Path):
    """livespec below v0.33 has no `external_ingest` table at all."""
    _index(tmp_path)
    _graph(tmp_path)
    state = graph_state(tmp_path)
    assert state.present and not state.ingested
    assert "no ingest" in state.detail


def test_no_graph_is_silent(tmp_path: Path):
    assert not graph_state(tmp_path).present


# ---------------------------------------------------------------------------
# The whole picture
# ---------------------------------------------------------------------------

def test_summary_names_every_neighbour_even_when_absent(tmp_path: Path):
    text = summary(tmp_path)
    for name in ("livespec", "flowtrace", "Graphify"):
        assert name in text, f"{name} missing from the summary"


# ---------------------------------------------------------------------------
# The failure paths, which are the ones that must never raise
# ---------------------------------------------------------------------------

def test_a_trace_directory_that_cannot_be_listed_is_unknown(tmp_path: Path, monkeypatch):
    """Callers are a PreToolUse hook and a validator. Neither survives an
    exception from here, and neither should treat one as "no trace"."""
    import vise.core.neighbour_state as mod

    _trace(tmp_path)

    def boom(project):
        raise PermissionError("nope")

    monkeypatch.setattr(mod, "_trace_files", boom)
    state = trace_state(tmp_path)
    assert not state.known and "PermissionError" in state.detail


def test_an_unreadable_graph_state_is_reported_not_raised(tmp_path: Path, monkeypatch):
    import vise.core.neighbour_state as mod

    _index(tmp_path)
    _graph(tmp_path)

    def boom(db):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(mod, "_connect", boom)
    state = graph_state(tmp_path)
    assert "could not read" in state.detail


def test_the_error_signature_survives_a_broken_trace(tmp_path: Path, monkeypatch):
    """It feeds a gate's evidence. Returning nothing is a wrong answer this
    module can live with; raising is not."""
    import vise.core.neighbour_state as mod

    _trace(tmp_path)
    monkeypatch.setattr(mod, "trace_state", lambda *a, **k: 1 / 0)
    assert error_signature(tmp_path) == ()


def test_a_malformed_line_never_stops_the_error_signature(tmp_path: Path):
    path = _trace(tmp_path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    assert error_signature(tmp_path) == ("error_fixture.inner", "error_fixture.outer")


def test_a_graph_with_no_index_beside_it_is_still_reported(tmp_path: Path):
    """Graphify does not need livespec. A graph with nothing to ingest it is a
    normal state, and saying "no graph" would be wrong."""
    _graph(tmp_path)
    state = graph_state(tmp_path)
    assert state.present and not state.ingested
    assert "no livespec index" in state.detail
