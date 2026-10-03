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


# --- plugin options -----------------------------------------------------------
#
# `/plugin` → vise → Configure (or a `/config` row) hands a hook its options as
# CLAUDE_PLUGIN_OPTION_<KEY>. The launcher is the one place they become the
# VISE_* switches the code reads, and an explicit VISE_* always wins.

_SWITCHES = ("VISE_GOAL_GATE", "VISE_SNAPSHOT_ON_EDIT", "VISE_WORKFLOW_SUGGEST", "VISE_CODELAYER")


def _switches(tmp_path, **env: str) -> dict[str, str]:
    xdg = tmp_path / "xdg"
    py = xdg / "vise" / "venv" / "bin" / "python"
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text(
        "#!/bin/sh\n" + "".join(f'echo "{k}=${{{k}-unset}}"\n' for k in _SWITCHES),
        encoding="utf-8",
    )
    py.chmod(0o755)
    out = _run({"XDG_DATA_HOME": str(xdg), "HOME": str(tmp_path / "home"), **env})
    assert out.returncode == 0, out.stderr
    seen = dict(line.split("=", 1) for line in out.stdout.splitlines())
    return {k: v for k, v in seen.items() if k in _SWITCHES}


@pytest.fixture
def _no_switches(monkeypatch):
    for key in _SWITCHES:
        monkeypatch.delenv(key, raising=False)
    for key in ("GOAL_GATE", "SNAPSHOT_ON_EDIT", "WORKFLOW_SUGGEST", "CODELAYER"):
        monkeypatch.delenv(f"CLAUDE_PLUGIN_OPTION_{key}", raising=False)


def test_options_left_at_their_defaults_change_nothing(tmp_path, _no_switches):
    seen = _switches(
        tmp_path,
        CLAUDE_PLUGIN_OPTION_GOAL_GATE="false",
        CLAUDE_PLUGIN_OPTION_SNAPSHOT_ON_EDIT="false",
        CLAUDE_PLUGIN_OPTION_WORKFLOW_SUGGEST="true",
        CLAUDE_PLUGIN_OPTION_CODELAYER="",
    )
    assert set(seen.values()) == {"unset"}


def test_options_turned_on_become_the_switches(tmp_path, _no_switches):
    seen = _switches(
        tmp_path,
        CLAUDE_PLUGIN_OPTION_GOAL_GATE="true",
        CLAUDE_PLUGIN_OPTION_SNAPSHOT_ON_EDIT="True",
        CLAUDE_PLUGIN_OPTION_WORKFLOW_SUGGEST="false",
        CLAUDE_PLUGIN_OPTION_CODELAYER="warn",
    )
    assert seen == {
        "VISE_GOAL_GATE": "1",
        "VISE_SNAPSHOT_ON_EDIT": "1",
        "VISE_WORKFLOW_SUGGEST": "0",
        "VISE_CODELAYER": "warn",
    }


def test_an_explicit_switch_wins_over_the_option(tmp_path, _no_switches):
    seen = _switches(
        tmp_path,
        VISE_GOAL_GATE="0",
        CLAUDE_PLUGIN_OPTION_GOAL_GATE="true",
        VISE_CODELAYER="enforce",
        CLAUDE_PLUGIN_OPTION_CODELAYER="off",
    )
    assert seen["VISE_GOAL_GATE"] == "0"
    assert seen["VISE_CODELAYER"] == "enforce"


@pytest.mark.parametrize("value", ["${user_config.goal_gate}", "maybe", "2"])
def test_a_value_that_is_not_exactly_on_leaves_the_switch_off(tmp_path, _no_switches, value):
    """An older Claude Code may hand the MCP server the placeholder unsubstituted."""
    seen = _switches(tmp_path, CLAUDE_PLUGIN_OPTION_GOAL_GATE=value)
    assert seen["VISE_GOAL_GATE"] == "unset"


def test_an_unknown_codelayer_mode_leaves_the_gate_off(tmp_path, _no_switches):
    seen = _switches(tmp_path, CLAUDE_PLUGIN_OPTION_CODELAYER="block-everything")
    assert seen["VISE_CODELAYER"] == "unset"


def _gate(fragment: str) -> str:
    """The shell test in front of a hooks.json command that names `fragment`."""
    import json

    hooks = json.loads((LAUNCHER.parents[1] / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    for groups in hooks["hooks"].values():
        for group in groups:
            for hook in group["hooks"]:
                if fragment in hook["command"]:
                    return hook["command"].split(" && ", 1)[0]
    raise AssertionError(f"no hook command names {fragment}")


@pytest.mark.parametrize("fragment,option,on,off", [
    ("snapshot_trigger.py\" --pre", "CLAUDE_PLUGIN_OPTION_SNAPSHOT_ON_EDIT", "true", "false"),
    ("codelayer_gate.py", "CLAUDE_PLUGIN_OPTION_CODELAYER", "warn", "off"),
])
def test_a_shell_gated_hook_opens_on_its_option_and_stays_shut_at_the_default(
    fragment, option, on, off, _no_switches,
):
    """These two gate in the shell so that, off, they cost no interpreter per call."""
    gate = _gate(fragment)

    def opens(value: str | None) -> bool:
        env = {k: v for k, v in os.environ.items() if k != option}
        if value is not None:
            env[option] = value
        return subprocess.run(["bash", "-c", gate], env=env).returncode == 0

    assert opens(on)
    assert not opens(off)
    assert not opens(None)
