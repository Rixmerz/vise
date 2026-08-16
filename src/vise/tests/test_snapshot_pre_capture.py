"""Capturar después de un comando destructivo documenta el destrozo.

`snapshot_trigger` era solo PostToolUse. La secuencia medida antes de este
cambio, con la función activada:

    t=0   Edit  -> snapshot v1
    t=5   Edit  -> throttled (30 s, documentado y a propósito)
    t=10  git checkout -- .        <- v2 destruido

    contenido en disco: v0
    v2 recuperable desde algún snapshot: False

Arreglar la clasificación de comandos (que `git checkout` contara como
escritor) no alcanzaba: la captura seguía ocurriendo *después*. Grabar tras un
`git reset --hard` documenta el destrozo, no recupera lo destruido.

Por eso existe el modo `--pre`, y por eso NO respeta el throttle: el throttle
es exactamente lo que descartó v2. Lo que acota la historia en su lugar es
`snapshots.create(dedup=True)` — una ráfaga de comandos destructivos sin
ediciones entre medio describe un solo árbol, así que escribe un solo
snapshot.

El costo se paga en `hooks.json`, no acá: el gate de shell prueba la
*presencia* de VISE_SNAPSHOT_ON_EDIT antes de arrancar Python. Prueba
presencia y no valor a propósito — la ortografía de "verdadero"
(`1`/`true`/`yes`/`on`) se decide en un solo lugar,
`core.snapshots.on_edit_capture_enabled`, y una segunda copia de esa regla en
shell es como el hook y `snapshot_list` terminarían en desacuerdo.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from vise.core import snapshots

HOOK = Path(__file__).resolve().parents[1] / "hooks" / "snapshot_trigger.py"
HOOKS_JSON = Path(__file__).resolve().parents[3] / "hooks" / "hooks.json"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    r = tmp_path / "proj"
    (r / "src").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(r)], check=True)
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")
    (r / "src/a.py").write_text("v0\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    monkeypatch.setenv("VISE_SNAPSHOT_ON_EDIT", "1")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(r))
    return r


def _fire(repo: Path, tool: str, cmd: str | None = None, *, pre: bool = False) -> str:
    """Corre el hook como subproceso, que es como Claude Code lo corre."""
    import os

    argv = [sys.executable, str(HOOK)] + (["--pre"] if pre else [])
    payload = {"tool_name": tool, "tool_input": {"command": cmd} if cmd else {}}
    p = subprocess.run(
        argv, input=json.dumps(payload), capture_output=True, text=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(repo)},
    )
    assert p.returncode == 0, "un hook nunca puede fallar la sesión"
    return p.stderr


def _snap_count(repo: Path) -> int:
    return len(snapshots.list_all(repo))


# ---------------------------------------------------------------------------
# la regresión, de punta a punta
# ---------------------------------------------------------------------------

def test_work_dropped_by_the_throttle_survives_a_destructive_command(repo: Path):
    """La secuencia entera que perdía trabajo."""
    (repo / "src/a.py").write_text("v1\n", encoding="utf-8")
    _fire(repo, "Edit")                                    # captura v1

    (repo / "src/a.py").write_text("v2 IMPORTANTE\n", encoding="utf-8")
    _fire(repo, "Edit")                                    # throttled: v2 sin capturar

    _fire(repo, "Bash", "git checkout -- .", pre=True)     # captura v2 antes del destrozo
    _git(repo, "checkout", "--", ".")

    assert (repo / "src/a.py").read_text(encoding="utf-8") == "v0\n"
    recoverable = any(
        "v2" in _git(repo, "show", f"{s.ref}:src/a.py")
        for s in snapshots.list_all(repo)
    )
    assert recoverable, "el trabajo que el throttle descartó sigue siendo irrecuperable"


def test_the_pre_capture_ignores_the_throttle(repo: Path):
    """Respetarlo reintroduce el bug: el throttle es lo que descartó el trabajo."""
    (repo / "src/a.py").write_text("v1\n", encoding="utf-8")
    _fire(repo, "Edit")
    before = _snap_count(repo)

    (repo / "src/a.py").write_text("v2\n", encoding="utf-8")
    _fire(repo, "Bash", "git reset --hard", pre=True)      # dentro de los 30 s

    assert _snap_count(repo) == before + 1


def test_a_burst_of_destructive_commands_writes_one_snapshot(repo: Path):
    """`dedup=True` es lo que acota la historia sin volver a poner un throttle."""
    (repo / "src/a.py").write_text("v1\n", encoding="utf-8")
    _fire(repo, "Bash", "git clean -fdx", pre=True)
    after_first = _snap_count(repo)

    for _ in range(5):
        _fire(repo, "Bash", "git clean -fdx", pre=True)

    assert _snap_count(repo) == after_first, (
        "cinco comandos sin ediciones entre medio describen un solo árbol"
    )


def test_a_read_only_command_captures_nothing(repo: Path):
    (repo / "src/a.py").write_text("v1\n", encoding="utf-8")
    before = _snap_count(repo)
    _fire(repo, "Bash", "git status", pre=True)
    _fire(repo, "Bash", "ls -la", pre=True)
    assert _snap_count(repo) == before


def test_pre_mode_ignores_tools_other_than_bash(repo: Path):
    """Edit/Write ya los cubre el hook post; capturar dos veces es ruido."""
    (repo / "src/a.py").write_text("v1\n", encoding="utf-8")
    before = _snap_count(repo)
    _fire(repo, "Edit", pre=True)
    _fire(repo, "Write", pre=True)
    assert _snap_count(repo) == before


def test_the_feature_stays_off_by_default(repo: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("VISE_SNAPSHOT_ON_EDIT", raising=False)
    (repo / "src/a.py").write_text("v1\n", encoding="utf-8")
    _fire(repo, "Bash", "git reset --hard", pre=True)
    assert _snap_count(repo) == 0


def test_the_label_says_what_it_was_protecting_against(repo: Path):
    (repo / "src/a.py").write_text("v1\n", encoding="utf-8")
    _fire(repo, "Bash", "git reset --hard HEAD~1", pre=True)
    assert "git reset --hard" in snapshots.list_all(repo)[-1].label


def test_the_hook_never_fails_the_session_on_a_non_git_dir(tmp_path: Path):
    """El contrato fail-open: sin repo git, silencio y exit 0."""
    import os

    plain = tmp_path / "sin-git"
    plain.mkdir()
    p = subprocess.run(
        [sys.executable, str(HOOK), "--pre"],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "rm -rf x"}}),
        capture_output=True, text=True,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(plain),
             "VISE_SNAPSHOT_ON_EDIT": "1"},
    )
    assert p.returncode == 0


# ---------------------------------------------------------------------------
# dedup, en el nivel del engine
# ---------------------------------------------------------------------------

def test_dedup_reuses_the_snapshot_when_the_tree_is_unchanged(repo: Path):
    first = snapshots.create(repo, label="uno", dedup=True)
    second = snapshots.create(repo, label="dos", dedup=True)
    assert first is not None and second is not None
    assert second.id == first.id
    assert _snap_count(repo) == 1


def test_dedup_still_captures_when_the_tree_changed(repo: Path):
    first = snapshots.create(repo, label="uno", dedup=True)
    (repo / "src/a.py").write_text("cambio\n", encoding="utf-8")
    second = snapshots.create(repo, label="dos", dedup=True)
    assert first is not None and second is not None
    assert second.id != first.id
    assert _snap_count(repo) == 2


def test_dedup_is_off_for_an_explicit_checkpoint(repo: Path):
    """`snapshot_create` es un marcador deliberado; devolver uno viejo sorprende."""
    first = snapshots.create(repo, label="antes-del-experimento")
    second = snapshots.create(repo, label="otro-marcador")
    assert first is not None and second is not None
    assert second.id != first.id
    assert second.label == "otro-marcador"


# ---------------------------------------------------------------------------
# el gate de shell — el costo lo paga hooks.json, no el hook
# ---------------------------------------------------------------------------

def _pre_registration() -> dict:
    wired = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    for entry in wired["hooks"]["PreToolUse"]:
        for hook in entry["hooks"]:
            if "snapshot_trigger.py" in hook["command"]:
                return {**hook, "matcher": entry["matcher"]}
    raise AssertionError("snapshot_trigger no está registrado en PreToolUse")


def test_the_pre_hook_is_wired_only_for_bash():
    assert _pre_registration()["matcher"] == "Bash"


def test_the_pre_hook_is_wired_with_the_pre_flag():
    assert "--pre" in _pre_registration()["command"]


def test_the_shell_gate_runs_before_python_starts():
    """Sin el gate, todo usuario paga ~30 ms de arranque de intérprete por Bash."""
    cmd = _pre_registration()["command"]
    assert cmd.index("VISE_SNAPSHOT_ON_EDIT") < cmd.index("vise-run"), (
        "el gate tiene que estar antes del lanzador, o no ahorra nada"
    )


def test_the_shell_gate_tests_presence_not_value():
    """La ortografía de "verdadero" se decide en un solo lugar, en Python.

    Un `= "1"` en shell haría que VISE_SNAPSHOT_ON_EDIT=true activara el hook
    post y no el pre, que es la clase exacta de desacuerdo que
    `on_edit_capture_enabled` existe para evitar.
    """
    cmd = _pre_registration()["command"]
    assert "-n " in cmd
    for truthy in ('"1"', "= 1", "=1"):
        assert f'VISE_SNAPSHOT_ON_EDIT:-}}" {truthy}' not in cmd


def test_the_gate_never_fails_the_hook():
    """`|| true`: un hook que sale distinto de cero rompe la sesión del usuario."""
    assert _pre_registration()["command"].rstrip().endswith("|| true")


def test_the_truthy_rule_lives_in_exactly_one_place():
    import inspect

    from vise.core import snapshots as core

    src = inspect.getsource(core)
    assert src.count("_TRUTHY = ") == 1
    assert "on_edit_capture_enabled" in src
