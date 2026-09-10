"""Tests for precompact_state.py + session_restore.py — compact survival."""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

from vise.hooks import precompact_state, session_restore


def _run(module, project_dir: Path, payload: dict | None = None,
         env: dict[str, str] | None = None) -> tuple[str, int]:
    stdout = io.StringIO()
    full_env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project_dir),
                **(env or {})}
    with (
        mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload or {}))),
        mock.patch.object(sys, "stdout", stdout),
        mock.patch.dict(os.environ, full_env, clear=False),
    ):
        code = module.main()
    return stdout.getvalue(), int(code or 0)


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Project dir with isolated XDG + goal storage."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    project = tmp_path / "proj"
    (project / ".claude" / "workflow").mkdir(parents=True)
    return project


def _write_graph_state(project: Path) -> None:
    from vise.core import state_paths
    p = state_paths.graph_state_path(str(project))
    p.write_text(json.dumps({
        "active_graph": "debug-flow",
        "current_nodes": ["reproduce"],
        "node_visits": {}, "execution_path": [],
    }))


def test_precompact_no_state_silent(isolated: Path) -> None:
    out, code = _run(precompact_state, isolated)
    assert out == ""
    assert code == 0


def test_session_restore_no_state_silent(isolated: Path) -> None:
    out, code = _run(session_restore, isolated,
                     {"source": "compact"})
    assert out == ""
    assert code == 0


def test_precompact_active_workflow_emits_context(isolated: Path) -> None:
    """PreCompact must emit systemMessage, NOT hookSpecificOutput.

    hookSpecificOutput is only valid for PreToolUse, UserPromptSubmit,
    PostToolUse, PostToolBatch, Stop, SubagentStop — Claude Code rejects
    it on PreCompact ("Hook JSON output validation failed"), which meant
    this hook failed on every single /compact before the fix.
    """
    _write_graph_state(isolated)
    out, code = _run(precompact_state, isolated)
    assert code == 0
    data = json.loads(out)
    assert "hookSpecificOutput" not in data
    msg = data["systemMessage"]
    assert "debug-flow" in msg
    assert "reproduce" in msg
    assert "PRESERVE".lower() in msg.lower()


def test_session_restore_active_workflow_emits_context(isolated: Path) -> None:
    _write_graph_state(isolated)
    out, code = _run(session_restore, isolated, {"source": "startup"})
    assert code == 0
    data = json.loads(out)
    hso = data["hookSpecificOutput"]
    assert hso["hookEventName"] == "SessionStart"
    assert "debug-flow" in hso["additionalContext"]
    assert "graph_status" in hso["additionalContext"]
    assert len(hso["additionalContext"].splitlines()) <= 15


def test_session_restore_active_goal(isolated: Path) -> None:
    from vise.engines import goal_state
    goal_state.set_goal(str(isolated), "ship compact survival hooks")
    out, _ = _run(session_restore, isolated, {"source": "resume"})
    data = json.loads(out)
    assert "ship compact survival hooks" in \
        data["hookSpecificOutput"]["additionalContext"]


def test_hooks_fail_open_on_bad_stdin(isolated: Path) -> None:
    for module in (precompact_state, session_restore):
        stdout = io.StringIO()
        with (
            mock.patch.object(sys, "stdin", io.StringIO("not json")),
            mock.patch.object(sys, "stdout", stdout),
            mock.patch.dict(os.environ, {**os.environ,
                                         "CLAUDE_PROJECT_DIR": str(isolated)}),
        ):
            code = module.main()
        assert int(code or 0) == 0


def test_hooks_fail_open_on_reader_exception(isolated: Path) -> None:
    with mock.patch("vise.hooks._common.read_active_state",
                    side_effect=RuntimeError("boom")):
        for module in (precompact_state, session_restore):
            out, code = _run(module, isolated)
            assert code == 0
            assert out == ""


# ---------------------------------------------------------------------------
# Open blockers — the second half of what survives a compaction
# ---------------------------------------------------------------------------

def _write_project_memory(project: Path, entries: list[dict]) -> None:
    from vise.hooks import _xdg
    path = _xdg.project_memory_path(str(project))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": entries}))


def _entry(**over: object) -> dict:
    from datetime import datetime
    base = {
        "type": "run_blocked",
        "file_pattern": "run:feature-dev:implement",
        "domain": "runtime",
        "description": "drain_failed in run r1 — task 3: could not parse result",
        "severity": "high",
        "last_seen": datetime.now().isoformat(),
    }
    base.update(over)  # type: ignore[arg-type]
    return base


def test_an_open_blocker_is_reported(isolated: Path) -> None:
    from vise.hooks._common import read_open_blockers

    _write_project_memory(isolated, [_entry()])
    found = read_open_blockers(str(isolated))
    assert len(found) == 1
    assert found[0]["description"].startswith("drain_failed")


def test_a_blocker_with_its_resolver_present_is_closed(isolated: Path) -> None:
    """`run_blocked` and `run_succeeded` meet on (file_pattern, domain)."""
    from vise.hooks._common import read_open_blockers

    _write_project_memory(isolated, [
        _entry(),
        _entry(type="run_succeeded", description="2 task(s) succeeded in run r2"),
    ])
    assert read_open_blockers(str(isolated)) == []


def test_a_blocker_outside_the_window_is_dropped(isolated: Path) -> None:
    from datetime import datetime, timedelta

    from vise.hooks._common import read_open_blockers

    old = (datetime.now() - timedelta(days=40)).isoformat()
    _write_project_memory(isolated, [_entry(last_seen=old)])
    assert read_open_blockers(str(isolated)) == []


@pytest.mark.parametrize("stamp", ["", "not-a-date"])
def test_an_undatable_blocker_is_dropped(isolated: Path, stamp: str) -> None:
    """The opposite of the rule a gate follows, because nothing is decided here.

    An undated line cannot be ranked against dated ones without claiming a
    position it has not earned.
    """
    from vise.hooks._common import read_open_blockers

    _write_project_memory(isolated, [_entry(last_seen=stamp)])
    assert read_open_blockers(str(isolated)) == []


def test_a_node_gate_failure_stays_open_because_nothing_writes_smell_fixed(
    isolated: Path,
) -> None:
    """Pins a limitation, not a preference.

    `smell_fixed`, `gate_resolved` and `tension_resolved` are all in
    `VALID_TYPES` and no writer in vise emits any of them. Mapping
    `smell_introduced` to `smell_fixed` would therefore close nothing while
    looking like it closed something. If a writer appears, this test is the
    place that has to change on purpose.
    """
    from vise.hooks._common import read_open_blockers

    _write_project_memory(isolated, [
        _entry(type="smell_introduced", file_pattern="node:implement",
               domain="", description="Node gate failed at 'implement': coverage"),
        _entry(type="smell_fixed", file_pattern="node:implement", domain="",
               description="fixed"),
    ])
    found = read_open_blockers(str(isolated))
    assert len(found) == 1
    assert found[0]["type"] == "smell_introduced"


def test_blockers_are_ranked_worst_first_and_capped(isolated: Path) -> None:
    from vise.hooks._common import read_open_blockers

    _write_project_memory(isolated, [
        _entry(file_pattern=f"run:g:n{i}", severity=sev, description=f"e{i}")
        for i, sev in enumerate(["low", "critical", "medium", "high", "low", "high"])
    ])
    found = read_open_blockers(str(isolated), limit=3)
    assert [e["severity"] for e in found] == ["critical", "high", "high"]


def test_a_corrupt_store_reports_nothing_rather_than_raising(isolated: Path) -> None:
    from vise.hooks import _xdg
    from vise.hooks._common import read_open_blockers

    path = _xdg.project_memory_path(str(isolated))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert read_open_blockers(str(isolated)) == []


def test_reading_blockers_does_not_write_to_the_store(isolated: Path) -> None:
    """`ExperienceMemoryStore.query` bumps FSRS recall and saves.

    A hook on that path would raise the stability of whatever was recent every
    time a session compacted — the store would learn from being read.
    """
    from vise.hooks import _xdg
    from vise.hooks._common import read_open_blockers

    _write_project_memory(isolated, [_entry()])
    path = _xdg.project_memory_path(str(isolated))
    before = path.read_bytes()
    read_open_blockers(str(isolated))
    assert path.read_bytes() == before


def test_precompact_reports_blockers_with_no_workflow_active(isolated: Path) -> None:
    """An open failure is worth carrying even when no workflow is running."""
    _write_project_memory(isolated, [_entry()])
    out, code = _run(precompact_state, isolated)
    assert code == 0
    message = json.loads(out)["systemMessage"]
    assert "could not parse result" in message
    assert "[high]" in message


def test_the_blocker_section_is_not_labelled_preserve_verbatim(
    isolated: Path,
) -> None:
    """It may never have been mentioned in the conversation being summarized.

    Telling a summarizer to preserve a line that was never there is how a
    summary acquires things that did not happen.
    """
    _write_project_memory(isolated, [_entry()])
    _write_graph_state(isolated)
    out, _ = _run(precompact_state, isolated)
    message = json.loads(out)["systemMessage"]
    preserve, blockers = message.split("Known unresolved failures")
    assert "verbatim" in preserve
    assert "verbatim" not in blockers
    assert "do not introduce the rest" in blockers


def test_precompact_still_silent_when_nothing_active_and_nothing_open(
    isolated: Path,
) -> None:
    _write_project_memory(isolated, [])
    out, code = _run(precompact_state, isolated)
    assert out == ""
    assert code == 0
