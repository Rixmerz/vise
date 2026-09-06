"""`install.sh` claims things about this repo. These check the claims.

The script's own comments record why: a hint table for LSP servers was once
duplicated here and in `plugin.json`, drifted, and printed a green tick for a
`rust-analyzer` that could not start. The lesson taken was "report through
`vise doctor` rather than re-deriving" — which only holds if the sections the
script greps for are still the sections `doctor` prints.

Nothing here runs the installer. It talks to the `claude` CLI and writes to the
user's home directory, and a test that did either would be testing the machine.
What is checkable without running it is that every name it depends on is real.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import tomllib

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "install.sh"


@pytest.fixture(scope="module")
def script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_the_script_is_valid_bash(script: str):
    """`bash -n` parses without running. A syntax error here is discovered by
    the first person to install, which is the worst possible reader."""
    done = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr


def test_every_extra_it_installs_is_one_pyproject_declares(script: str):
    """`pip install -e '.[design]'` on an undeclared extra is not an error —
    pip warns and installs nothing, so the browser never arrives and the gates
    keep failing closed with no sign of why."""
    with (REPO / "pyproject.toml").open("rb") as fh:
        declared = set(tomllib.load(fh)["project"]["optional-dependencies"])
    asked = set(re.findall(r'\$\{REPO_DIR\}\[([a-z]+)\]', script))
    assert asked, "the script installs no extras at all any more"
    assert asked <= declared, asked - declared


def test_every_flag_the_help_advertises_is_a_flag_it_parses(script: str):
    """A help text is a promise. One that lists a flag the `case` block does
    not match sends the user to a silent no-op."""
    usage = script.split("usage: ./install.sh", 1)[1].split("USAGE", 1)[0]
    advertised = set(re.findall(r"^\s{2}(--[a-z-]+)\s", usage, re.MULTILINE))
    parsed = set(re.findall(r"^\s*(--[a-z-]+)\)", script, re.MULTILINE))
    assert advertised, "the usage block advertises nothing"
    assert advertised <= parsed, advertised - parsed


def test_the_design_flag_installs_the_browser_too(script: str):
    """`pip install 'vise[design]'` alone leaves playwright with no Chromium,
    and the gates then fail closed on a message about a missing browser — one
    install later the person is stuck again. Both steps or neither."""
    block = script.split('if [ "$DESIGN" = "1" ]', 1)[1].split("\nfi\n", 1)[0]
    assert "[design]" in block
    assert "playwright install chromium" in block
    assert "${VENV_DIR}/bin/python" in block, (
        "playwright must run through this venv's own interpreter — a binary "
        "from another environment installs a build this playwright refuses"
    )


def test_the_doctor_sections_it_greps_for_are_sections_doctor_prints():
    """The whole point of reporting through `doctor` instead of re-deriving.
    A renamed heading turns the installer's report into silence, which reads
    as "nothing to install" rather than as a broken grep."""
    wanted = set(re.findall(r'for section in ([^\n]+)', SCRIPT.read_text(encoding="utf-8")))
    assert wanted, "the installer no longer reports any doctor section"
    names = re.findall(r'"([^"]+)"', wanted.pop())

    done = subprocess.run(
        [sys.executable, "-m", "vise.cli.main", "doctor"],
        capture_output=True, text=True, check=False, cwd=str(REPO), timeout=300,
    )
    printed = {
        line.strip().strip("= ").split(" (")[0]
        for line in done.stdout.splitlines()
        if line.startswith("===")
    }
    for name in names:
        assert any(heading.startswith(name) for heading in printed), (
            f"install.sh greps for a '{name}' section; doctor prints {sorted(printed)}"
        )


def test_it_says_which_missing_pieces_actually_block_a_run(script: str):
    """LSP servers stay dormant until you open that language. The render gates
    do the opposite — they fail closed. An installer that reports both in one
    undifferentiated list teaches the reader to ignore both."""
    assert "dormant" in script and "fail closed" in script
