"""The script every hook and the MCP server actually start through.

`bin/vise-run` decides which interpreter runs vise. Every hook registration in
`hooks/hooks.json` and the MCP server entry go through it, so a wrong branch here
does not break one feature — it breaks all of them, and it breaks them the way
this script was written to prevent: silently, with Claude Code simply listing no
vise tools.

Until now it was asserted about (`"Every hook command goes through bin/vise-run"`)
and never executed, and `bin/` is not in `[tool.coverage.run] source`, so no
number anywhere reflected it. These run it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[3] / "bin" / "vise-run"

pytestmark = pytest.mark.skipif(
    not LAUNCHER.exists() or not shutil.which("bash"),
    reason="needs bin/vise-run and bash",
)


def _stub_python(path: Path, marker: str) -> None:
    """An executable that identifies itself and echoes its arguments."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\necho "{marker}"\necho "args:$@"\n', encoding="utf-8")
    path.chmod(0o755)


def _run(env_overrides: dict, *args: str) -> subprocess.CompletedProcess:
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("XDG_DATA_HOME", "HOME", "VIRTUAL_ENV")
    }
    env.update(env_overrides)
    return subprocess.run(
        ["bash", str(LAUNCHER), *args],
        capture_output=True, text=True, env=env, timeout=60,
    )


def test_the_xdg_venv_wins_over_the_legacy_path(tmp_path):
    """Both exist; the XDG one is the current install and must be chosen."""
    xdg = tmp_path / "xdg"
    home = tmp_path / "home"
    _stub_python(xdg / "vise" / "venv" / "bin" / "python", "XDG")
    _stub_python(home / ".local" / "share" / "vise" / "venv" / "bin" / "python",
                 "LEGACY")

    out = _run({"XDG_DATA_HOME": str(xdg), "HOME": str(home)}, "-c", "pass")

    assert out.stdout.splitlines()[0] == "XDG", out.stderr


def test_the_legacy_path_still_runs_an_install_made_before_xdg(tmp_path):
    """A pre-XDG install has to keep working without a reinstall."""
    home = tmp_path / "home"
    _stub_python(home / ".local" / "share" / "vise" / "venv" / "bin" / "python",
                 "LEGACY")

    out = _run({"XDG_DATA_HOME": str(tmp_path / "empty-xdg"), "HOME": str(home)},
               "-c", "pass")

    assert out.stdout.splitlines()[0] == "LEGACY", out.stderr


def test_a_relative_xdg_data_home_is_ignored(tmp_path):
    """The launcher's own comment: honour XDG only when set AND absolute.

    Drifting from `vise.hooks._xdg` here is how the launcher and the rest of
    vise would disagree about where vise is installed.
    """
    home = tmp_path / "home"
    _stub_python(home / ".local" / "share" / "vise" / "venv" / "bin" / "python",
                 "LEGACY")

    out = _run({"XDG_DATA_HOME": "relative/path", "HOME": str(home)}, "-c", "pass")

    assert out.stdout.splitlines()[0] == "LEGACY", out.stderr


def test_a_non_executable_candidate_is_skipped(tmp_path):
    """`-x`, not `-e`: a file that cannot be run is not an interpreter."""
    home = tmp_path / "home"
    xdg = tmp_path / "xdg"
    broken = xdg / "vise" / "venv" / "bin" / "python"
    _stub_python(broken, "XDG")
    broken.chmod(0o644)
    _stub_python(home / ".local" / "share" / "vise" / "venv" / "bin" / "python",
                 "LEGACY")

    out = _run({"XDG_DATA_HOME": str(xdg), "HOME": str(home)}, "-c", "pass")

    assert out.stdout.splitlines()[0] == "LEGACY", out.stderr


def test_arguments_reach_the_chosen_interpreter_unchanged(tmp_path):
    """The launcher execs; it must not eat or reorder what it was given."""
    xdg = tmp_path / "xdg"
    _stub_python(xdg / "vise" / "venv" / "bin" / "python", "XDG")

    out = _run({"XDG_DATA_HOME": str(xdg), "HOME": str(tmp_path / "home")},
               "-m", "vise.server", "--flag", "a b")

    assert "args:-m vise.server --flag a b" in out.stdout


def test_with_no_interpreter_anywhere_it_explains_itself_and_fails(tmp_path):
    """The silent-failure case this script exists to make loud.

    `claude plugin install` copies files without provisioning a venv. If the
    system python3 also cannot import the deps, the only useful thing left is a
    non-zero exit and instructions on stderr — never a silent exec.
    """
    empty = tmp_path / "nothing"
    empty.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    # A python3 that exists but cannot import the runtime deps.
    (fake_bin / "python3").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (fake_bin / "python3").chmod(0o755)

    out = _run({"XDG_DATA_HOME": str(empty), "HOME": str(empty),
                "PATH": f"{fake_bin}:/usr/bin:/bin"}, "-c", "pass")

    assert out.returncode != 0
    assert "fastmcp" in out.stderr
    assert out.stdout.strip() == "", "nothing may reach stdout before the JSON-RPC stream"
