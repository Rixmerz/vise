"""How `tests_pass` decides WHICH pytest to run, and what it does with exit 5.

Two defects lived here, and they compounded: the gate ran a bare `pytest` off
`PATH` — a different interpreter from the one the project's dependencies live
in — and then, the moment anyone corrected that by naming their runner
explicitly, pytest's "no tests collected" stopped being recognised because the
check was `cmd[0] == "pytest"`.

Together they produced the failure vise's own CLAUDE.md warns about: a gate red
for environment reasons, which teaches people to reach for
`VISE_NODE_GATE_OVERRIDE` — the habit gates exist to prevent. vise's own
`implement` node was blocked by it.
"""
from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from vise.engines.validators import (
    TestsPassValidator,
    _invokes_pytest,
    _venv_pytest,
)


def _goal(project_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        id="g", project_dir=str(project_dir), goal="x", validator_configs=[]
    )


def _make_venv(root: Path, layout: str = ".venv/bin/python") -> Path:
    interpreter = root / layout
    interpreter.parent.mkdir(parents=True, exist_ok=True)
    interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
    interpreter.chmod(interpreter.stat().st_mode | stat.S_IXUSR)
    return interpreter


# --- which interpreter -----------------------------------------------------


def test_a_project_venv_is_preferred_over_whatever_is_on_path(tmp_path: Path) -> None:
    interpreter = _make_venv(tmp_path)

    assert _venv_pytest(str(tmp_path)) == (str(interpreter), "-m", "pytest", "-q")


def test_a_project_without_a_venv_falls_back(tmp_path: Path) -> None:
    assert _venv_pytest(str(tmp_path)) is None


def test_a_non_executable_interpreter_is_not_used(tmp_path: Path) -> None:
    interpreter = tmp_path / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")
    interpreter.chmod(0o644)

    assert _venv_pytest(str(tmp_path)) is None


def test_the_default_command_resolves_to_the_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VISE_TEST_CMD", raising=False)
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    interpreter = _make_venv(tmp_path)
    result = MagicMock(returncode=0, stdout="1 passed\n", stderr="")

    with patch("shutil.which", return_value="/usr/bin/pytest"), \
            patch("subprocess.run", return_value=result) as run:
        TestsPassValidator().run(_goal(tmp_path))

    assert run.call_args[0][0][0] == str(interpreter), (
        "a bare `pytest` off PATH is a different interpreter from the project's"
    )


def test_an_explicit_test_cmd_still_wins_over_the_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("VISE_TEST_CMD", raising=False)
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    _make_venv(tmp_path)
    result = MagicMock(returncode=0, stdout="ok\n", stderr="")

    with patch("shutil.which", return_value="/usr/bin/npm"), \
            patch("subprocess.run", return_value=result) as run:
        TestsPassValidator(test_cmd=("npm", "test")).run(_goal(tmp_path))

    assert run.call_args[0][0] == ["npm", "test"]


def test_the_env_override_still_wins_over_the_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VISE_TEST_CMD", "cargo test")
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    _make_venv(tmp_path)
    result = MagicMock(returncode=0, stdout="ok\n", stderr="")

    with patch("shutil.which", return_value="/usr/bin/cargo"), \
            patch("subprocess.run", return_value=result) as run:
        TestsPassValidator().run(_goal(tmp_path))

    assert run.call_args[0][0] == ["cargo", "test"]


# --- and whether exit 5 is recognised --------------------------------------


@pytest.mark.parametrize(
    "cmd",
    [
        ("pytest", "-q"),
        (".venv/bin/python", "-m", "pytest", "-q"),
        ("/usr/bin/python3", "-m", "pytest"),
        ("/repo/.venv/bin/pytest", "-q"),
    ],
    ids=["bare", "venv-module", "absolute-module", "venv-script"],
)
def test_pytest_is_recognised_however_it_is_spelled(cmd: tuple[str, ...]) -> None:
    assert _invokes_pytest(cmd) is True


@pytest.mark.parametrize(
    "cmd",
    [("npm", "test"), ("cargo", "test"), ("python", "-m", "unittest"), ()],
    ids=["npm", "cargo", "unittest", "empty"],
)
def test_other_runners_are_not_mistaken_for_pytest(cmd: tuple[str, ...]) -> None:
    assert _invokes_pytest(cmd) is False


def test_exit_five_does_not_block_when_pytest_runs_as_a_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression. `cmd[0] == "pytest"` was False here, so "no tests
    collected" fell through to `passed = returncode == 0` and blocked the gate
    on a repo that simply had no tests yet."""
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    result = MagicMock(returncode=5, stdout="no tests ran in 0.01s\n", stderr="")

    with patch("shutil.which", return_value="/repo/.venv/bin/python"), \
            patch("subprocess.run", return_value=result):
        record = TestsPassValidator(
            test_cmd=("/repo/.venv/bin/python", "-m", "pytest", "-q")
        ).run(_goal(tmp_path))

    assert record.passed is True, "exit 5 must not block, whichever way pytest was run"
    assert record.exit_code == 5
    assert record.outcome == "unverified", "zero tests ran — not a verified pass"
    assert "no tests collected" in record.evidence


def test_exit_five_from_a_non_pytest_runner_still_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 5 means something else to every other runner, so the escape hatch
    must stay scoped to pytest."""
    monkeypatch.setenv("VISE_GOAL_DIR", str(tmp_path / "goal"))
    result = MagicMock(returncode=5, stdout="build failed\n", stderr="")

    with patch("shutil.which", return_value="/usr/bin/npm"), \
            patch("subprocess.run", return_value=result):
        record = TestsPassValidator(test_cmd=("npm", "test")).run(_goal(tmp_path))

    assert record.passed is False
