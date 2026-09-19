"""The context-cost hook measures; it does not guess, and it does not nag.

What is pinned: the size comes from the response the tool actually returned;
a small result is silent; a big one is named once with the bounded form of
the same call, three times per tool and then never; the session milestone
fires once per multiple with a breakdown; the ledger stays small; and a hook
that breaks says so in the failsafe ledger rather than in the session.
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

from vise.hooks import context_cost


def _run(payload: object, env: dict[str, str] | None = None) -> tuple[str, int]:
    stdout = io.StringIO()
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    with (
        mock.patch.object(sys, "stdin", io.StringIO(raw)),
        mock.patch.object(sys, "stdout", stdout),
        mock.patch.dict(os.environ, env or {}, clear=False),
    ):
        code = context_cost.main()
    return stdout.getvalue(), int(code or 0)


def _context(out: str) -> str:
    return json.loads(out)["hookSpecificOutput"]["additionalContext"]


def _bash(n_bytes: int, session: str = "s1") -> dict:
    return {
        "tool_name": "Bash",
        "tool_input": {"command": "cat big.log"},
        "tool_response": {"stdout": "x" * n_bytes, "stderr": ""},
        "session_id": session,
    }


def test_the_size_is_the_response_not_an_estimate():
    assert context_cost._size_of("abc") == 3
    assert context_cost._size_of({"stdout": "héllo"}) == len('{"stdout": "héllo"}'.encode())
    assert context_cost._size_of(None) == 0


def test_a_small_result_is_silent_but_counted(tmp_path: Path):
    out, code = _run(_bash(500))
    assert code == 0 and out == ""
    ledger = json.loads(context_cost._ledger_path("s1").read_text())
    assert ledger["calls"] == 1 and ledger["by_tool"]["Bash"] > 500


def test_a_big_result_is_named_once_with_the_bounded_form():
    out, _ = _run(_bash(40 * 1024))
    text = _context(out)
    assert "That Bash result was 40 KB" in text
    assert "| tail -n 60" in text


def test_the_third_note_is_the_last_for_that_tool():
    for _ in range(3):
        out, _ = _run(_bash(40 * 1024, session="s2"))
        assert "That Bash result" in _context(out)
    out, _ = _run(_bash(40 * 1024, session="s2"))
    assert out == "", "the fourth big result is the agent's decision"


def test_the_session_milestone_fires_once_per_multiple_with_a_breakdown():
    env = {"VISE_CONTEXT_SESSION_KB": "64", "VISE_CONTEXT_CALL_KB": "1000"}
    _run(_bash(30 * 1024, session="s3"), env)
    out, _ = _run(_bash(40 * 1024, session="s3"), env)
    text = _context(out)
    assert "put 70 KB into this session's context over 2 calls" in text
    assert "(Bash 70 KB)" in text
    out, _ = _run(_bash(10 * 1024, session="s3"), env)
    assert out == "", "the same multiple is not reported twice"
    out, _ = _run(_bash(60 * 1024, session="s3"), env)
    assert "140 KB" in _context(out)


def test_tools_it_does_not_measure_are_ignored():
    out, code = _run({"tool_name": "Edit", "tool_response": "x" * 100_000, "session_id": "s4"})
    assert code == 0 and out == ""
    assert not context_cost._ledger_path("s4").exists()


def test_the_ledger_keeps_aggregates_only():
    ledger: dict = {}
    for n in range(50):
        context_cost.account(ledger, "Read", 1000 + n, call_kb=32, session_kb=256)
    assert ledger["calls"] == 50
    assert len(ledger["largest"]) == 5
    assert ledger["largest"][0]["bytes"] == 1049
    assert len(json.dumps(ledger)) < 600


@pytest.mark.parametrize("payload", ["not json", "[]", "", '{"tool_name": "Bash"}'])
def test_garbage_input_is_silent_and_exits_zero(payload: str):
    out, code = _run(payload)
    assert code == 0 and out == ""


def test_a_failure_is_noted_to_the_ledger_not_the_session(monkeypatch: pytest.MonkeyPatch):
    from vise.hooks import _failsafe

    monkeypatch.setattr(context_cost, "_ledger_path", lambda _s: (_ for _ in ()).throw(RuntimeError("disk")))
    out, code = _run(_bash(40 * 1024, session="s5"))
    assert code == 0 and out == ""
    noted = _failsafe.drain()
    assert any(n.get("hook") == "context_cost" for n in noted)


def test_the_hook_runs_as_its_own_interpreter(tmp_path: Path):
    """The contract is the process, not the function."""
    import subprocess

    script = Path(context_cost.__file__)
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps(_bash(40 * 1024, session="s6")),
        capture_output=True, text=True, timeout=30,
        env={**os.environ, "PYTHONPATH": str(script.parents[2])},
    )
    assert proc.returncode == 0
    assert "That Bash result was 40 KB" in _context(proc.stdout)
