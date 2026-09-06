"""`vise neighbours` — the disk state, made answerable by a person.

The command exists because "is the symbol layer available here?" used to be
answerable only by asking an agent to make a tool call. It reads the same files
the `symbol_index` and `trace_captured` gates read, so someone debugging a
refusal sees exactly what refused rather than a different opinion.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from vise.cli import neighbours_cmd


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch):
    """The render line shells out to Playwright. Every test here is about the
    three neighbours, and a real browser probe would make them slow and
    machine-dependent."""
    monkeypatch.setattr(
        neighbours_cmd, "browser_status_quiet", lambda: (False, "not installed here")
    )


class _Args:
    def __init__(self, project_dir: Path):
        self.project_dir = str(project_dir)


def _run(project: Path, capsys) -> str:
    assert neighbours_cmd._cmd_neighbours(_Args(project)) == 0
    return capsys.readouterr().out


def test_it_names_every_neighbour_even_when_none_are_there(tmp_path, capsys):
    out = _run(tmp_path, capsys)
    for name in ("livespec", "flowtrace", "Graphify"):
        assert name in out


def test_it_prints_the_minimum_versions_from_the_contract(tmp_path, capsys):
    """A version is the one fact about a neighbour a person can check in a
    minute, and the reason vise's guidance broke was that nothing carried it."""
    from vise.core.neighbours import MINIMUM_VERSIONS

    out = _run(tmp_path, capsys)
    for name, version in MINIMUM_VERSIONS.items():
        assert name in out and version in out


def test_it_explains_what_an_unindexed_repo_means_for_the_gates(tmp_path, capsys):
    """Reporting "no index" without saying what follows leaves the reader to
    guess whether their gate is broken or behaving."""
    out = _run(tmp_path, capsys)
    assert "symbol_index" in out and "stands down" in out


def test_an_indexed_repo_does_not_get_the_refusal_note(tmp_path, capsys):
    db = tmp_path / ".mcp-docs" / "docs.db"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE index_run (id INTEGER PRIMARY KEY, project_id INTEGER, "
        "started_at TEXT, finished_at TEXT)"
    )
    conn.execute(
        "INSERT INTO index_run(project_id, started_at, finished_at) "
        "VALUES (1,'a','2026-09-06 20:00:00')"
    )
    conn.commit()
    conn.close()
    out = _run(tmp_path, capsys)
    assert "2026-09-06 20:00:00" in out
    assert "stands down" not in out


def test_it_reports_the_browser_the_render_gates_would_use(tmp_path, capsys):
    """Not a neighbour — the question people are really asking when they ask
    whether layout-inspector is needed."""
    out = _run(tmp_path, capsys)
    assert "render gates" in out and "not installed here" in out


def test_the_render_probe_never_lets_playwright_noise_reach_the_report(monkeypatch):
    """`browser_status()` returns the right answer and then Playwright's
    teardown writes a TargetClosedError traceback to stderr at interpreter
    exit. A person reading a status command must not be shown that."""
    import subprocess

    from vise.cli import _browser_probe

    monkeypatch.undo()
    calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        assert kw.get("capture_output") is True, (
            "the probe must capture the subprocess's stderr, not inherit it"
        )
        return subprocess.CompletedProcess(cmd, 0, "1\tchromium is available\n", "NOISE")

    monkeypatch.setattr(subprocess, "run", fake_run)
    ok, why = _browser_probe.browser_status_quiet()
    assert (ok, why) == (True, "chromium is available")
    assert calls and "-c" in calls[0]


def test_a_probe_that_says_nothing_useful_is_not_read_as_available(monkeypatch):
    import subprocess

    from vise.cli import _browser_probe

    monkeypatch.undo()
    monkeypatch.setattr(
        subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "boom"),
    )
    ok, why = _browser_probe.browser_status_quiet()
    assert not ok and "could not determine" in why


def test_bootstrap_asks_the_same_quiet_way(monkeypatch):
    """`vise bootstrap` is where someone decides whether this repo is set up
    correctly. A Playwright traceback right after "browser found" reads as the
    tool crashing on the sentence that said everything was fine."""
    from vise.cli import bootstrap_cmd

    monkeypatch.undo()
    monkeypatch.setattr(
        bootstrap_cmd, "browser_status_quiet", lambda: (True, "chromium is available")
    )
    assert "browser found" in bootstrap_cmd._design_gates_report()


def test_the_render_line_reports_a_configured_repo(tmp_path, monkeypatch, capsys):
    """The one path where the render gates could actually run."""
    monkeypatch.setattr(
        neighbours_cmd, "browser_status_quiet", lambda: (True, "chromium is available")
    )
    (tmp_path / ".vise").mkdir()
    (tmp_path / ".vise" / "quality.yaml").write_text(
        "design:\n  targets: ['file:///a.html', 'file:///b.html']\n"
        "  breakpoints: [375, 1280]\n",
        encoding="utf-8",
    )
    out = _run(tmp_path, capsys)
    assert "ready — 2 target(s), 2 breakpoints" in out


def test_a_browser_with_nothing_configured_is_not_reported_as_ready(tmp_path, monkeypatch, capsys):
    """The gates fail closed on an empty target list rather than skipping, and
    a status line saying "ready" would make that read as a bug."""
    monkeypatch.setattr(
        neighbours_cmd, "browser_status_quiet", lambda: (True, "chromium is available")
    )
    out = _run(tmp_path, capsys)
    assert "fail closed with nothing to render" in out


def test_the_render_line_survives_an_unreadable_quality_profile(tmp_path, monkeypatch, capsys):
    """A status command that raises on a malformed config is a status command
    that stops working exactly when someone needs it."""
    monkeypatch.setattr(
        neighbours_cmd, "browser_status_quiet", lambda: (True, "chromium is available")
    )
    (tmp_path / ".vise").mkdir()
    (tmp_path / ".vise" / "quality.yaml").write_text("design: [not, a, mapping\n")
    out = _run(tmp_path, capsys)
    assert "render gates" in out


def test_a_stale_graph_gets_a_note_saying_what_it_costs(tmp_path, capsys):
    """Reporting "differs" without saying what follows leaves the reader to
    guess whether a caller count they are about to act on is trustworthy."""
    import sqlite3
    import time

    db = tmp_path / ".mcp-docs" / "docs.db"
    db.parent.mkdir(parents=True)
    graph = tmp_path / "graphify-out" / "graph.json"
    graph.parent.mkdir(parents=True)
    graph.write_text('{"nodes": [], "links": []}', encoding="utf-8")
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE index_run (id INTEGER PRIMARY KEY, project_id INTEGER, "
        "started_at TEXT, finished_at TEXT)"
    )
    conn.execute(
        "INSERT INTO index_run(project_id, started_at, finished_at) "
        "VALUES (1,'a','2026-09-06 20:00:00')"
    )
    conn.execute(
        "CREATE TABLE external_ingest (project_id INTEGER, origin TEXT, "
        "graph_path TEXT, graph_mtime REAL, graph_size INTEGER, graph_hash TEXT, "
        "relations TEXT, edges_written INTEGER, ingested_at REAL)"
    )
    conn.execute(
        "INSERT INTO external_ingest VALUES "
        "(1,'external:graphify',?,?,999999,'x','calls',5,?)",
        (str(graph), graph.stat().st_mtime, time.time()),
    )
    conn.commit()
    conn.close()

    out = _run(tmp_path, capsys)
    assert "no longer on disk" in out and "re-ingested" in out


def test_a_probe_that_cannot_even_start_is_not_read_as_available(monkeypatch):
    import subprocess

    from vise.cli import _browser_probe

    def boom(cmd, **kw):
        raise OSError("no interpreter")

    monkeypatch.undo()
    monkeypatch.setattr(subprocess, "run", boom)
    ok, why = _browser_probe.browser_status_quiet()
    assert not ok and "OSError" in why
