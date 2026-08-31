"""Which `ruff` gets executed, and who put it there.

`_find_checker` prefers the checked project's own `.venv` — for a good reason,
documented where it is implemented: that is the only environment holding the
dependencies the code under check imports, and without it this gate reported
missing stubs for a numpy the project had installed.

The cost of that preference was never stated. `edit_feedback` is registered
under `PostToolUse` for `Edit|Write|MultiEdit` with no env guard, so editing any
`.py` file in a freshly cloned repository executed whatever that repository had
committed at `.venv/bin/ruff`. No vise command, no workflow, no prompt.

The discriminator is provenance, not a global opt-in: a `.venv` git is tracking
arrived *with* the repository and is attacker-controlled; one git does not know
about was built locally by the person running vise. Keeping the second one
working is what stops this fix from re-breaking the numpy case.
"""
from __future__ import annotations

import os
import subprocess

from vise.engines.lsp_diagnostics import _find_checker, lsp_diagnostics

SENTINEL_SCRIPT = "#!/bin/sh\ntouch \"$(dirname \"$0\")/../../SENTINEL\"\nexit 0\n"


def _venv_checker(root, name="ruff", body=SENTINEL_SCRIPT):
    binary = root / ".venv" / "bin" / name
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(body, encoding="utf-8")
    binary.chmod(0o755)
    return binary


def _git_repo(root):
    def run(*args):
        subprocess.run(args, cwd=root, capture_output=True, check=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@example.com")
    run("git", "config", "user.name", "t")
    return run


def test_a_committed_project_checker_is_not_executed(tmp_path, monkeypatch):
    monkeypatch.delenv("VISE_TRUST_PROJECT_TOOLS", raising=False)
    run = _git_repo(tmp_path)
    _venv_checker(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    run("git", "add", "-A", "-f")
    run("git", "commit", "-qm", "ships a checker")

    assert _find_checker("ruff", tmp_path) != str(tmp_path / ".venv" / "bin" / "ruff")

    lsp_diagnostics(str(tmp_path), str(tmp_path / "a.py"), tools=("ruff",))
    assert not (tmp_path / "SENTINEL").exists(), "the repo's own binary was run"


def test_a_locally_built_venv_is_still_preferred(tmp_path, monkeypatch):
    """The reason the project venv comes first must survive the fix."""
    monkeypatch.delenv("VISE_TRUST_PROJECT_TOOLS", raising=False)
    run = _git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".venv/\n", encoding="utf-8")
    run("git", "add", "-A")
    run("git", "commit", "-qm", "seed")
    binary = _venv_checker(tmp_path)

    assert _find_checker("ruff", tmp_path) == str(binary)


def test_a_project_that_is_not_a_repo_still_uses_its_venv(tmp_path, monkeypatch):
    """Nothing arrived with a directory that was never cloned."""
    monkeypatch.delenv("VISE_TRUST_PROJECT_TOOLS", raising=False)
    binary = _venv_checker(tmp_path)
    assert _find_checker("ruff", tmp_path) == str(binary)


def test_an_explicit_opt_in_runs_the_committed_checker(tmp_path, monkeypatch):
    """A repo that really does vendor its toolchain can say so."""
    monkeypatch.setenv("VISE_TRUST_PROJECT_TOOLS", "1")
    run = _git_repo(tmp_path)
    binary = _venv_checker(tmp_path)
    run("git", "add", "-A", "-f")
    run("git", "commit", "-qm", "vendored")

    assert _find_checker("ruff", tmp_path) == str(binary)


def test_a_non_executable_project_checker_is_skipped(tmp_path, monkeypatch):
    """`.exists()` is not `is runnable` — a directory named ruff passed it."""
    monkeypatch.delenv("VISE_TRUST_PROJECT_TOOLS", raising=False)
    binary = _venv_checker(tmp_path)
    binary.chmod(0o644)
    assert _find_checker("ruff", tmp_path) != str(binary)


def test_a_directory_named_like_a_checker_is_skipped(tmp_path, monkeypatch):
    monkeypatch.delenv("VISE_TRUST_PROJECT_TOOLS", raising=False)
    fake = tmp_path / ".venv" / "bin" / "ruff"
    fake.mkdir(parents=True)
    assert _find_checker("ruff", tmp_path) != str(fake)


def test_the_cwd_walk_does_not_reach_into_the_audited_tree(tmp_path, monkeypatch):
    """The walk up from cwd extends the same reach to go, cargo and tsc."""
    monkeypatch.delenv("VISE_TRUST_PROJECT_TOOLS", raising=False)
    run = _git_repo(tmp_path)
    _venv_checker(tmp_path, name="tsc")
    run("git", "add", "-A", "-f")
    run("git", "commit", "-qm", "ships tsc")
    monkeypatch.chdir(tmp_path)

    found = _find_checker("tsc", tmp_path)
    assert found != str(tmp_path / ".venv" / "bin" / "tsc")
    assert found is None or not str(found).startswith(str(tmp_path) + os.sep)
