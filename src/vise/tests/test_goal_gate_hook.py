"""The Stop hook, launched the way Claude Code launches it.

``test_goal_gate.py`` covers ``engines.goal_gate.evaluate`` and covered it
well; the script that Claude Code actually runs — reads the environment, loads
the goal, builds the ``Decision``, prints the block — had 0% coverage. It is the
one hook that does not fail open into "nothing happens": a bug in this glue
does not take the session down, it holds the turn open, re-invoking the agent
with the reason as its instruction until a gate that never evaluates releases.
So it is the hook that least of all gets to be tested by import.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from vise.engines import goal_state

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "goal_gate.py"


def _run(project: Path, env: dict[str, str], stdin: str = "{}") -> tuple[int, str, str]:
    p = subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(project), **env},
        capture_output=True, text=True, timeout=60,
    )
    return p.returncode, p.stdout, p.stderr


def _block(stdout: str) -> dict | None:
    stdout = stdout.strip()
    return json.loads(stdout) if stdout else None


def test_off_by_default_the_hook_prints_nothing_and_exits_zero(tmp_path):
    goal_state.set_goal(str(tmp_path), "finish", ["it works"])
    env = {k: v for k, v in os.environ.items() if k != "VISE_GOAL_GATE"}
    p = subprocess.run(
        [sys.executable, str(HOOK)], input="{}",
        env={**env, "CLAUDE_PROJECT_DIR": str(tmp_path)},
        capture_output=True, text=True, timeout=60,
    )
    assert p.returncode == 0
    assert p.stdout.strip() == ""


def test_enabled_with_no_goal_it_releases(tmp_path):
    rc, out, _ = _run(tmp_path, {"VISE_GOAL_GATE": "1"})
    assert rc == 0
    assert _block(out) is None


def test_enabled_with_an_unfinished_goal_it_blocks_with_the_goal_in_the_reason(tmp_path):
    goal_state.set_goal(str(tmp_path), "make the tests green", ["pytest exits 0"])

    rc, out, err = _run(tmp_path, {"VISE_GOAL_GATE": "1"})

    assert rc == 0, err
    decision = _block(out)
    assert decision is not None, f"no block printed; stderr: {err}"
    assert decision["decision"] == "block"
    assert "make the tests green" in decision["reason"]
    assert "pytest exits 0" in decision["reason"]


def test_the_override_releases_it(tmp_path):
    goal_state.set_goal(str(tmp_path), "g", [])
    rc, out, _ = _run(tmp_path, {"VISE_GOAL_GATE": "1", "VISE_GOAL_GATE_OVERRIDE": "1"})
    assert rc == 0
    assert _block(out) is None


def test_the_cancel_file_releases_it(tmp_path):
    goal_state.set_goal(str(tmp_path), "g", [])
    cancel = tmp_path / ".claude" / "state" / "goal-cancel"
    cancel.parent.mkdir(parents=True)
    cancel.write_text("")

    rc, out, _ = _run(tmp_path, {"VISE_GOAL_GATE": "1"})
    assert rc == 0
    assert _block(out) is None


def test_a_completed_goal_does_not_block(tmp_path):
    goal_state.set_goal(str(tmp_path), "g", [])
    goal_state.mark_complete(str(tmp_path))

    rc, out, _ = _run(tmp_path, {"VISE_GOAL_GATE": "1"})
    assert rc == 0
    assert _block(out) is None


def test_garbage_on_stdin_never_takes_the_session_down(tmp_path):
    goal_state.set_goal(str(tmp_path), "g", [])
    rc, out, _ = _run(tmp_path, {"VISE_GOAL_GATE": "1"}, stdin="not json at all")
    assert rc == 0
    assert _block(out) is not None, "the gate reads goal state, not stdin; it must still decide"


def test_an_unreadable_goal_file_releases_rather_than_trapping(tmp_path):
    """A hook bug must never hold a turn open with a reason it cannot compute."""
    goal_state.set_goal(str(tmp_path), "g", [])
    goal_state._path_for(str(tmp_path)).write_text("{corrupt", encoding="utf-8")

    rc, out, err = _run(tmp_path, {"VISE_GOAL_GATE": "1"})
    assert rc == 0
    assert _block(out) is None, out
