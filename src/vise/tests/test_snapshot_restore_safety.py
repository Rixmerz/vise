"""Un restore no puede sorprender al usuario con cambios en su índice.

Encontrado corriendo el ciclo completo `create -> desastre -> restore` contra un
repo git real, que es la única forma de verlo: ambos defectos viven en la
interacción con git, y los tests unitarios de `snapshots.py` nunca miraban el
índice del usuario ni el contenido del árbol capturado.

  1. `create` hacía `git add -A` sin excluir `.vise/`. El `.gitignore` que
     cubre ese directorio lo escribe `_journal_path`, que corre *después* de
     `write-tree`: en el primer snapshot de un repo la línea todavía no existe,
     así que el barrido se tragaba `.vise/tmp.index.lock` — el lockfile que el
     propio git estaba sosteniendo en ese instante. Quedaba en el árbol para
     siempre.

  2. `restore` hacía `git checkout <ref> -- .`, que escribe índice *y* working
     tree. Cada archivo restaurado quedaba staged en silencio. El docstring
     prometía lo contrario ("the user decides whether to stage/commit"), y un
     `git commit` posterior se llevaba el rollback entero junto con lo que el
     usuario sí había preparado a propósito.

El segundo es el que importa: es pérdida potencial de trabajo del usuario, no
ruido.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vise.core import snapshots


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    (r / "src").mkdir(parents=True)
    _git_init = subprocess.run(["git", "init", "-q", str(r)], check=True)
    assert _git_init.returncode == 0
    _git(r, "config", "user.email", "t@example.invalid")
    _git(r, "config", "user.name", "t")
    (r / "src/app.py").write_text("ORIGINAL\n", encoding="utf-8")
    (r / "src/other.py").write_text("keep\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    return r


def _tree_paths(repo: Path, ref: str) -> set[str]:
    out = _git(repo, "ls-tree", "-r", "--name-only", ref)
    return {ln for ln in out.splitlines() if ln}


# ---------------------------------------------------------------------------
# 1 — el árbol capturado no contiene el estado interno de vise
# ---------------------------------------------------------------------------

def test_the_first_snapshot_of_a_repo_excludes_vises_own_state(repo: Path):
    """El primero es el caso que fallaba: aún no hay .gitignore que lo cubra."""
    assert not (repo / ".gitignore").exists()

    snap = snapshots.create(repo, label="primero")
    assert snap is not None

    captured = _tree_paths(repo, snap.ref)
    assert {"src/app.py", "src/other.py"} <= captured, captured
    assert not any(p.startswith(".vise/") for p in captured), (
        "el snapshot se tragó el directorio de estado de vise"
    )


def test_no_snapshot_ever_carries_gits_own_index_lock(repo: Path):
    """`tmp.index.lock` es el lock que `add -A` sostiene mientras corre."""
    first = snapshots.create(repo, label="uno")
    (repo / "src/app.py").write_text("cambio\n", encoding="utf-8")
    second = snapshots.create(repo, label="dos")
    assert first is not None and second is not None

    for snap in (first, second):
        assert "tmp.index.lock" not in " ".join(_tree_paths(repo, snap.ref))


def test_a_force_tracked_file_under_vise_is_still_captured(repo: Path):
    """vise misma versiona `.vise/quality.yaml` con el par `.vise/*` + `!...`.

    La exclusión se apoya en el .gitignore del proyecto, así que un archivo que
    el repo trackea a propósito sigue entrando al snapshot — es contenido real
    del proyecto, y dejarlo fuera haría que un restore lo borrara del árbol.
    Lo que nunca entra es el estado efímero, que es de lo que se trataba.
    """
    (repo / ".gitignore").write_text(".vise/*\n!.vise/quality.yaml\n", encoding="utf-8")
    (repo / ".vise").mkdir(exist_ok=True)
    (repo / ".vise/quality.yaml").write_text("gates: []\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "profile")

    snap = snapshots.create(repo, label="con-perfil")
    assert snap is not None
    captured = _tree_paths(repo, snap.ref)
    assert ".vise/quality.yaml" in captured
    assert ".vise/snapshots.jsonl" not in captured
    assert not any(p.endswith(".lock") for p in captured), captured


# ---------------------------------------------------------------------------
# 2 — restore no toca el índice del usuario
# ---------------------------------------------------------------------------

def _staged(repo: Path) -> set[str]:
    out = _git(repo, "diff", "--cached", "--name-only")
    return {ln for ln in out.splitlines() if ln}


def test_restore_rewrites_the_worktree(repo: Path):
    snap = snapshots.create(repo, label="antes")
    assert snap is not None
    (repo / "src/app.py").write_text("DESTRUIDO\n", encoding="utf-8")
    (repo / "src/other.py").unlink()

    snapshots.restore(repo, snap.id, dry_run=False)

    assert (repo / "src/app.py").read_text(encoding="utf-8") == "ORIGINAL\n"
    assert (repo / "src/other.py").exists(), "el archivo borrado no resucitó"


def test_restore_stages_nothing(repo: Path):
    """La regresión central: el docstring prometía esto y no lo cumplía."""
    snap = snapshots.create(repo, label="antes")
    assert snap is not None
    (repo / "src/app.py").write_text("DESTRUIDO\n", encoding="utf-8")

    snapshots.restore(repo, snap.id, dry_run=False)

    assert _staged(repo) == set(), (
        "restore dejó archivos staged: el siguiente `git commit` del usuario se "
        "lleva el rollback entero sin que lo haya pedido"
    )


def test_restore_preserves_what_the_user_had_already_staged(repo: Path):
    """El índice es del usuario. Un restore no lo edita, ni para agregar ni para quitar."""
    (repo / "src/deliberado.py").write_text("staged a proposito\n", encoding="utf-8")
    _git(repo, "add", "src/deliberado.py")

    snap = snapshots.create(repo, label="antes")
    assert snap is not None
    (repo / "src/app.py").write_text("DESTRUIDO\n", encoding="utf-8")

    snapshots.restore(repo, snap.id, dry_run=False)

    assert _staged(repo) == {"src/deliberado.py"}


def test_restore_leaves_head_and_the_branch_alone(repo: Path):
    head = _git(repo, "rev-parse", "HEAD")
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    snap = snapshots.create(repo, label="antes")
    assert snap is not None
    (repo / "src/app.py").write_text("DESTRUIDO\n", encoding="utf-8")

    snapshots.restore(repo, snap.id, dry_run=False)

    assert _git(repo, "rev-parse", "HEAD") == head
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == branch


def test_a_dry_run_touches_neither_disk_nor_index(repo: Path):
    snap = snapshots.create(repo, label="antes")
    assert snap is not None
    (repo / "src/app.py").write_text("DESTRUIDO\n", encoding="utf-8")

    out = snapshots.restore(repo, snap.id, dry_run=True)

    assert (repo / "src/app.py").read_text(encoding="utf-8") == "DESTRUIDO\n"
    assert _staged(repo) == set()
    assert isinstance(out, str)


def test_restore_leaves_files_created_after_the_snapshot(repo: Path):
    """Documentado a propósito: rollback no es descartar trabajo que el snapshot no vio."""
    snap = snapshots.create(repo, label="antes")
    assert snap is not None
    (repo / "src/nuevo.py").write_text("trabajo posterior\n", encoding="utf-8")

    snapshots.restore(repo, snap.id, dry_run=False)

    assert (repo / "src/nuevo.py").exists()


def test_restore_leaves_no_temporary_index_behind(repo: Path):
    snap = snapshots.create(repo, label="antes")
    assert snap is not None
    snapshots.restore(repo, snap.id, dry_run=False)

    leftovers = sorted(p.name for p in (repo / ".vise").iterdir())
    assert "restore.index" not in leftovers
    assert not any(n.endswith(".lock") for n in leftovers), leftovers


def test_an_unknown_snapshot_id_raises_rather_than_touching_the_worktree(repo: Path):
    (repo / "src/app.py").write_text("en curso\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unknown snapshot"):
        snapshots.restore(repo, "no-existe", dry_run=False)
    assert (repo / "src/app.py").read_text(encoding="utf-8") == "en curso\n"
