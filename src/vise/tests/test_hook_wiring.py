"""Every hook `hooks.json` wires must exist on disk.

``hooks/hooks.json`` is the only thing that connects vise's enforcement to
Claude Code: the PreToolUse entry for ``graph_enforcer.py`` is what blocks a
tool the current phase forbids, and the Stop entry for ``goal_gate.py`` is what
holds a session open until the goal is met. A hook whose file was renamed or
moved does not raise — Claude Code runs the command, the interpreter reports a
missing path, and the tool call proceeds. Enforcement is simply gone, quietly.

Nothing else in the suite would notice: every existing hook test imports the
module directly (``from vise.hooks import graph_enforcer``) or execs it by path,
never through the JSON that production actually reads.

The reverse direction is deliberately not asserted: ``experience_index_builder.py``
lives in ``hooks/`` but is spawned detached by ``experience_injector.py``, so a
"no unwired modules" rule would need an allowlist from the first commit.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HOOKS_JSON = REPO / "hooks" / "hooks.json"
PACKAGE_ROOT = Path(__file__).resolve().parents[1]

# ${CLAUDE_PLUGIN_ROOT}/src/vise/hooks/<name>.py inside a shell command string.
_HOOK_SCRIPT = re.compile(r"src/vise/hooks/([a-z_]+\.py)")
_RUNNER = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/bin/([a-z-]+)")


def _commands() -> list[str]:
    config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    return [
        hook["command"]
        for matchers in config["hooks"].values()
        for matcher in matchers
        for hook in matcher["hooks"]
        if hook.get("type") == "command"
    ]


def test_every_wired_hook_script_exists() -> None:
    commands = _commands()
    assert commands, "hooks.json wires no commands at all"

    missing = sorted(
        {
            name
            for command in commands
            for name in _HOOK_SCRIPT.findall(command)
            if not (PACKAGE_ROOT / "hooks" / name).is_file()
        }
    )
    assert not missing, (
        "hooks.json wires these scripts but they do not exist — the hook silently "
        f"does nothing and enforcement is off: {missing}"
    )


def test_the_hook_runner_exists_and_is_executable() -> None:
    """Every hook command goes through bin/vise-run; if it is gone, all of them are."""
    runners = {name for command in _commands() for name in _RUNNER.findall(command)}
    assert runners, "no ${CLAUDE_PLUGIN_ROOT}/bin/... runner referenced in hooks.json"

    for name in sorted(runners):
        path = REPO / "bin" / name
        assert path.is_file(), f"hooks.json runs bin/{name}, which does not exist"
        assert path.stat().st_mode & 0o111, f"bin/{name} is not executable"


def test_the_enforcement_hooks_are_actually_wired() -> None:
    """Naming the files is not enough — these two must be on their events.

    Without the PreToolUse enforcer nothing blocks a forbidden tool; without the
    Stop goal gate a session ends whenever the model decides it is done. Both
    have been silently absent before (see hooks/_xdg.py: "the first time the
    phase gate actually fired").
    """
    config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))

    def _scripts_for(event: str) -> set[str]:
        return {
            name
            for matcher in config["hooks"].get(event, [])
            for hook in matcher["hooks"]
            for name in _HOOK_SCRIPT.findall(hook.get("command", ""))
        }

    assert "graph_enforcer.py" in _scripts_for("PreToolUse")
    assert "goal_gate.py" in _scripts_for("Stop")


# ---------------------------------------------------------------------------
# La documentación cuenta hooks. Los conteos escritos a mano derivan.
# ---------------------------------------------------------------------------
#
# CLAUDE.md decía "the 8 hook registrations". Ocho no era nada: ni los eventos
# (6), ni las entradas por matcher (10), ni los comandos (12), ni los scripts
# distintos (10). Había sido cierto alguna vez y nadie lo volvió a mirar.
#
# Es exactamente el problema que CLAUDE.md nombra en "Assets are asserted, not
# trusted" — solo que la regla se aplicaba a `agents/`, `skills/` y los
# workflows, y no a las afirmaciones sobre el cableado.

CLAUDE_MD = REPO / "CLAUDE.md"
README = REPO / "README.md"


def _wired_scripts() -> set[str]:
    return {m for c in _commands() for m in _HOOK_SCRIPT.findall(c)}


def test_claude_md_states_the_real_registration_counts() -> None:
    config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]
    commands = len(_commands())
    scripts = len(_wired_scripts())
    events = len(config)

    claimed = CLAUDE_MD.read_text(encoding="utf-8")
    expected = f"{commands} hook registrations across {scripts} scripts, {events} events"
    assert expected in claimed, (
        f"CLAUDE.md no dice la verdad sobre hooks.json — debería decir {expected!r}"
    )


def test_the_readme_table_names_every_wired_hook() -> None:
    """Un hook cableado y no documentado es un efecto que nadie espera."""
    readme = README.read_text(encoding="utf-8")
    missing = sorted(s for s in _wired_scripts() if s not in readme)
    assert not missing, f"cableados en hooks.json pero ausentes del README: {missing}"
