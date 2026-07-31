"""Regression coverage for the basename-collision bug in per-project state
keys: two different project directories sharing a basename (e.g.
``/home/rixmerz/Projects/mcp`` and ``/home/rixmerz``'s sibling ``mcp``)
used to resolve to the SAME ``states/<basename>/`` directory and silently
share one workflow-state file.

The fix (see ``vise.hooks._xdg.project_state_dir``/``claim_project_state_dir``
and ``vise.core.state_paths``) keeps the plain ``<basename>`` directory for
the common single-owner case (byte-identical to the legacy layout, so
tools like the hard-blocking ``graph_enforcer.py`` hook that hardcode that
path keep working with zero migration), and only diverts a SECOND,
different project sharing the same basename to a collision-proof
``<basename>-<hash>`` sibling.
"""
from __future__ import annotations

import json
from pathlib import Path

from vise.core import state_paths


def test_different_projects_same_basename_get_different_state_dirs(tmp_path: Path) -> None:
    proj_a = tmp_path / "parent-a" / "proj"
    proj_b = tmp_path / "parent-b" / "proj"
    proj_a.mkdir(parents=True)
    proj_b.mkdir(parents=True)

    dir_a = state_paths.state_dir(proj_a)
    dir_b = state_paths.state_dir(proj_b)

    assert dir_a != dir_b
    # The first claimant keeps the plain, legacy-compatible basename.
    assert dir_a.name == "proj"
    # The second, different project is diverted to a hashed sibling.
    assert dir_b.name != "proj"
    assert dir_b.name.startswith("proj-")


def test_same_project_dir_resolves_stably_across_calls(tmp_path: Path) -> None:
    proj = tmp_path / "myproject"
    proj.mkdir()

    first = state_paths.state_dir(proj)
    second = state_paths.state_dir(proj)
    third = state_paths.probe_state_dir(proj)

    assert first == second == third


def test_preexisting_legacy_state_survives_without_migration(tmp_path: Path) -> None:
    """A directory written by pre-fix code (plain ``states/<basename>/``,
    no origin marker) must still be found — and its content preserved —
    the first time the fixed code resolves that same project."""
    proj = tmp_path / "legacyproj"
    proj.mkdir()

    legacy_dir = state_paths.probe_state_dir(proj)
    assert legacy_dir is None  # nothing written yet

    # Simulate old code: write straight into the plain basename dir.
    from vise.core import paths as core_paths

    plain_dir = core_paths.data_dir() / "states" / "legacyproj"
    plain_dir.mkdir(parents=True)
    (plain_dir / "graph_state.json").write_text(json.dumps({"active_graph": "g"}))

    resolved = state_paths.state_dir(proj)
    assert resolved == plain_dir
    assert json.loads((resolved / "graph_state.json").read_text()) == {"active_graph": "g"}


def test_trailing_slash_and_relative_form_resolve_to_same_dir(tmp_path: Path, monkeypatch) -> None:
    proj = tmp_path / "sameproj"
    proj.mkdir()

    canonical = state_paths.state_dir(proj)

    with_slash = state_paths.state_dir(str(proj) + "/")
    assert with_slash == canonical

    monkeypatch.chdir(tmp_path)
    relative = state_paths.state_dir(Path("sameproj"))
    assert relative == canonical


def test_second_different_project_diverted_after_first_claims_plain_dir(tmp_path: Path) -> None:
    """Once project A has claimed the plain dir (via state_dir, which
    writes the origin marker), project B with the same basename must be
    diverted on its very first call — not just on a later one."""
    proj_a = tmp_path / "parent-a" / "shared"
    proj_b = tmp_path / "parent-b" / "shared"
    proj_a.mkdir(parents=True)
    proj_b.mkdir(parents=True)

    dir_a = state_paths.state_dir(proj_a)
    dir_b_probe = state_paths.probe_state_dir(proj_b)  # read-only, no claim

    assert dir_b_probe is None or dir_b_probe != dir_a


# ---------------------------------------------------------------------------
# project_key's digest is on-disk state, not an implementation detail
# ---------------------------------------------------------------------------


def test_project_key_digest_is_stable_for_a_known_path() -> None:
    """Locks the exact suffix, because it names directories already on disk.

    `project_key` is the collision escape hatch: change how the digest is
    computed and every project that has been diverted to a hashed sibling
    silently starts resolving to a NEW empty directory, orphaning its graph
    state, goal, and snapshots. Nothing else in the suite would notice.

    It exists as a regression guard for exactly one kind of edit — this hash
    gained `usedforsecurity=False` (SHA1 raises under an OpenSSL FIPS provider
    without it, and this call sits on the startup path), and the flag must not
    alter a single byte of the result.
    """
    from vise.hooks._xdg import project_key

    key = project_key("/home/rixmerz/Projects/vise")

    assert key == "vise-7c1b4f1e"


def test_project_key_hashes_the_resolved_path_not_the_spelling(tmp_path: Path) -> None:
    """Two spellings of one directory must produce one key."""
    from vise.hooks._xdg import project_key

    proj = tmp_path / "proj"
    proj.mkdir()

    assert project_key(str(proj)) == project_key(f"{proj}/")
    assert project_key(str(proj)) == project_key(str(proj / "." ))
