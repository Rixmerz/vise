"""`git`, `find` y `docker` no son comandos de solo lectura.

`snapshot_trigger` se autodescribe como "a black-box recorder" y decidía qué
grabar mirando el primer token contra un conjunto que incluía `git`, `find` y
`docker` enteros. Medido corriendo el hook como subproceso:

    se salta  git reset --hard HEAD~1
    se salta  git checkout -- .
    se salta  git clean -fdx
    se salta  docker run -v $PWD:/w img rm -rf /w/src
    se salta  find . -name '*.py' -delete

Una grabadora de caja negra que deja de grabar exactamente cuando corren los
comandos destructivos no es una grabadora de caja negra.

Un segundo agujero del mismo origen: `ls && rm -rf build` se clasificaba por su
primer token y se saltaba entero.

Lo que este cambio NO arregla, y conviene decirlo acá porque el test podría
leerse como que sí: el hook es PostToolUse, así que la captura ocurre *después*
del comando. Grabar tras un `git reset --hard` documenta el destrozo, no
recupera lo destruido. Cerrar esa ventana necesita una captura PreToolUse, que
es una decisión de diseño aparte con su propio costo por llamada.

"Read-only" acá significa read-only *respecto del árbol de trabajo*, que es lo
único que un snapshot captura: `git config --global` escribe un archivo y
pertenece al conjunto; `git stash` no.
"""
from __future__ import annotations

import pytest

from vise.hooks.snapshot_trigger import _is_read_only_command, _should_skip


def _bash(cmd: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


# ---------------------------------------------------------------------------
# la regresión — comandos destructivos que se saltaban
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "git reset --hard HEAD~1",
    "git checkout -- .",
    "git clean -fdx",
    "git restore src/",
    "git stash",
    "git switch otra-rama",
    "git rebase main",
    "git apply parche.diff",
    "find . -name '*.py' -delete",
    "find . -type f -exec rm {} ;",
    "docker run -v $PWD:/w img rm -rf /w/src",
    "docker compose down -v",
])
def test_a_destructive_command_is_recorded(cmd: str):
    assert not _is_read_only_command(cmd), f"{cmd!r} toca el árbol de trabajo"
    assert not _should_skip(_bash(cmd))


def test_a_chained_command_is_judged_by_all_its_segments():
    """`ls && rm -rf build` se clasificaba por `ls` y se saltaba entero."""
    assert not _is_read_only_command("ls && rm -rf build")
    assert not _is_read_only_command("git status; git reset --hard")
    assert not _is_read_only_command("cat x.txt | tee y.txt")


# ---------------------------------------------------------------------------
# lo que sigue saltándose — si no, cada `git status` dispara una captura
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "ls -la",
    "cat README.md",
    "grep -rn foo src/",
    "rg --files",
    "pwd",
    "wc -l src/*.py",
    "git status",
    "git log --oneline -10",
    "git diff HEAD",
    "git show abc123",
    "git rev-parse HEAD",
    "git config --global user.name x",
    "find . -name '*.py'",
    "find src -type d",
    "docker ps -a",
    "docker logs contenedor",
    "grep -rn foo src/ | wc -l",
    "ls && pwd",
])
def test_a_read_only_command_is_still_skipped(cmd: str):
    assert _is_read_only_command(cmd), f"{cmd!r} no toca el árbol de trabajo"
    assert _should_skip(_bash(cmd))


# ---------------------------------------------------------------------------
# la dirección en la que conviene equivocarse
# ---------------------------------------------------------------------------

def test_an_unknown_command_is_treated_as_a_writer():
    """Equivocarse acá cuesta un snapshot throttleado; al revés cuesta trabajo."""
    assert not _is_read_only_command("herramienta-rara --hace-algo")
    assert not _is_read_only_command("python build.py")
    assert not _is_read_only_command("npm install")


def test_a_git_subcommand_nobody_listed_is_treated_as_a_writer():
    assert not _is_read_only_command("git alguna-cosa-nueva")


def test_bare_git_is_not_read_only():
    """`git` sin subcomando imprime ayuda, pero la regla no debe adivinar."""
    assert not _is_read_only_command("git")


# ---------------------------------------------------------------------------
# el resto del contrato de _should_skip
# ---------------------------------------------------------------------------

def test_edit_and_write_always_record():
    assert not _should_skip({"tool_name": "Edit", "tool_input": {}})
    assert not _should_skip({"tool_name": "Write", "tool_input": {}})


@pytest.mark.parametrize("tool", ["Read", "Glob", "Grep", "Task", "WebFetch"])
def test_a_tool_that_cannot_write_is_skipped(tool: str):
    assert _should_skip({"tool_name": tool, "tool_input": {}})


def test_an_empty_or_malformed_command_is_skipped():
    assert _should_skip(_bash(""))
    assert _should_skip(_bash("   "))
    assert _should_skip({"tool_name": "Bash", "tool_input": {"command": None}})
    assert _should_skip({"tool_name": "Bash", "tool_input": {}})
    assert _should_skip({"tool_name": "Bash"})
