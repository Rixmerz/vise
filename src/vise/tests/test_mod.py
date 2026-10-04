"""`hooks/mod/` is TypeScript that Claude Code loads, and only Claude Code can check it.

`claude plugin validate` reads vise's manifest and `hooks/hooks.json` — command
hooks and the mod's module side by side — the way the engine will, and `claude plugin test` runs the mod's own tests against the
engine's hooks. Both need the `claude` CLI at 2.1.287 or later, the release
that added mods, so on a machine without it these skip and say why. Where it is
present, a mod that no longer loads fails here rather than in someone's session.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
MOD = REPO  # the mod ships inside vise: the plugin root is what loads
MINIMUM = (2, 1, 287)


def _claude_version() -> tuple[int, ...] | None:
    exe = shutil.which("claude")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(int(part) for part in match.groups()) if match else None


VERSION = _claude_version()
pytestmark = pytest.mark.skipif(
    VERSION is None or VERSION < MINIMUM,
    reason=f"needs the claude CLI at {'.'.join(map(str, MINIMUM))} or later (found {VERSION})",
)


def _claude(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["claude", "plugin", *args], capture_output=True, text=True, timeout=300, cwd=REPO,
    )


def test_the_mod_validates():
    done = _claude("validate", str(MOD))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Validation passed" in done.stdout


def test_the_mods_own_tests_pass():
    done = _claude("test", str(MOD))
    assert done.returncode == 0, done.stdout + done.stderr
    assert " 0 fail" in done.stdout + done.stderr
