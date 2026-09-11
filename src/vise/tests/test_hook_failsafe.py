"""A hook that fails open leaves a mark, and the next session reads it out.

Hooks swallow their exceptions by contract — one that raises takes the user's
session down. The cost was that a broken hook looked exactly like a working one:
the experience went unrecorded, the blockers went unsurfaced, and the user saw a
session that worked. `CLAUDE.md` already refuses this collapse for gates and for
neighbours ("absent and unreadable are different"); these tests apply it to
vise's own hooks.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from vise.hooks import _failsafe

HOOKS = Path(_failsafe.__file__).parent


def test_a_note_survives_to_the_next_read():
    _failsafe.note("experience_recorder", ValueError("boom"))
    drained = _failsafe.drain()
    assert [e["hook"] for e in drained] == ["experience_recorder"]
    assert "ValueError: boom" in drained[0]["error"]


def test_draining_forgets_so_the_notice_does_not_nag_forever():
    """A notice that reappears every session is one that gets ignored, which is
    the failure mode it exists to fix."""
    _failsafe.note("codelayer_gate", RuntimeError("x"))
    assert _failsafe.drain()
    assert _failsafe.drain() == []


def test_nothing_to_report_is_not_a_report():
    assert _failsafe.drain() == []
    assert _failsafe.summarise([]) == []


def test_the_ledger_is_capped():
    for i in range(_failsafe._CAP * 3):
        _failsafe.note("experience_injector", ValueError(str(i)))
    assert len(_failsafe.drain()) == _failsafe._CAP


def test_a_stale_note_is_dropped_rather_than_reported():
    _failsafe.note("precompact_state", ValueError("old"))
    path = _failsafe._path()
    entries = json.loads(path.read_bytes())
    entries[0]["at"] = time.time() - _failsafe._TTL_SECONDS - 1
    path.write_text(json.dumps(entries))
    assert _failsafe.drain() == []


def test_the_summary_redacts_before_it_reaches_the_model():
    """The notice is injected into a session's context. An exception message is
    exactly the place a connection string shows up."""
    _failsafe.note("edit_feedback", ValueError("api_key=hunter2hunter2 refused"))
    text = "\n".join(_failsafe.summarise(_failsafe.drain()))
    assert "hunter2hunter2" not in text
    assert "[REDACTED]" in text


def test_repeats_of_one_hook_collapse_into_a_count():
    for _ in range(3):
        _failsafe.note("experience_injector", ValueError("same"))
    _failsafe.note("codelayer_gate", ValueError("other"))
    text = "\n".join(_failsafe.summarise(_failsafe.drain()))
    assert "experience_injector (3x)" in text
    assert "- codelayer_gate:" in text, "a single failure carries no count"


def test_note_never_raises_even_when_the_ledger_cannot_be_written(monkeypatch):
    """This is what runs when something else has already broken."""
    monkeypatch.setattr(_failsafe, "_path", lambda: (_ for _ in ()).throw(OSError("nope")))
    _failsafe.note("experience_recorder", ValueError("boom"))  # must not raise
    assert _failsafe.drain() == []


# --- the hooks themselves -------------------------------------------------


@pytest.mark.parametrize("hook,payload", [
    ("experience_injector.py", {"tool_name": "Edit", "tool_input": {"file_path": "a.py"}}),
    ("experience_recorder.py", {"tool_name": "Edit", "tool_input": {"file_path": "a.py"}}),
    ("codelayer_gate.py", {"tool_name": "Read", "tool_input": {"file_path": "a.py"}}),
])
def test_a_hook_that_blows_up_records_it_and_still_approves(hook, payload, tmp_path):
    """The failure is injected by pointing the hook's data directory at a file,
    so every path it builds underneath raises NotADirectoryError. What is under
    test is the outermost handler: it must still print an approval AND leave a
    mark."""
    broken = tmp_path / "not-a-dir"
    broken.write_text("")
    env = dict(os.environ, XDG_DATA_HOME=str(broken),
               CLAUDE_PROJECT_DIR=str(tmp_path), PYTHONPATH=str(HOOKS.parent.parent))
    proc = subprocess.run([sys.executable, str(HOOKS / hook)],
                          input=json.dumps(payload), capture_output=True,
                          text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    # Approval is the contract; it must hold whether or not the hook broke.
    if proc.stdout.strip():
        assert "approve" in proc.stdout


def test_session_start_reports_a_failure_even_with_no_workflow_active(tmp_path):
    """`session_restore` used to return early when nothing was active, so on a
    machine with no workflow running the notice had nowhere to come out."""
    data = tmp_path / "data"
    data.mkdir()
    env = dict(os.environ, XDG_DATA_HOME=str(data), CLAUDE_PROJECT_DIR=str(tmp_path),
               PYTHONPATH=str(HOOKS.parent.parent))
    ledger = data / "vise" / "hook_failures.json"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps([
        {"hook": "experience_recorder", "error": "OSError: disk full", "at": time.time()},
    ]))

    proc = subprocess.run([sys.executable, str(HOOKS / "session_restore.py")],
                          input="{}", capture_output=True, text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    context = payload["hookSpecificOutput"]["additionalContext"]
    assert "failed open" in context
    assert "experience_recorder" in context
    assert "disk full" in context
    assert not ledger.exists(), "reported once, then forgotten"
