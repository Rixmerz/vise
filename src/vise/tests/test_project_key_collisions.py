"""Two checkouts named the same thing are two projects, in every store.

`_xdg.project_state_dir` solved this once: the plain basename is used until an
origin marker shows a *different* absolute path already claimed it, at which
point the second project gets a `<basename>-<hash>` sibling. `states/` uses it,
and `project_memories/` reuses the very key it resolves.

Two places never got it, and both were reached from a directory name alone:

  - goal state, keyed `<basename>.json`, so `~/a/api` and `~/b/api` shared one
    goal file — opening the second read the first one's objective,
    `goal_complete` closed the wrong one, and with `VISE_GOAL_GATE=1` the Stop
    hook held a session open on an objective belonging to another repo;
  - `vise experience gc` and `vise experience stats`, which rebuilt the store
    path from the basename instead of asking the resolver, so gc deleted from
    (or reported on) a store the loader never loads.

These pin all three trees against the one resolver.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from vise.core.state_paths import state_dir
from vise.engines import goal_state
from vise.hooks import _xdg


@pytest.fixture()
def twins(tmp_path: Path) -> tuple[Path, Path]:
    """Two different projects that happen to share a directory name."""
    first = tmp_path / "clientA" / "api"
    second = tmp_path / "clientB" / "api"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    # The first one claims the plain basename, exactly as a real session does.
    state_dir(str(first))
    return first, second


# --- goal state ------------------------------------------------------------


def test_two_projects_sharing_a_basename_get_two_goal_files(
    twins: tuple[Path, Path],
) -> None:
    first, second = twins

    assert goal_state._path_for(str(first)) != goal_state._path_for(str(second))


def test_the_unclaimed_twin_never_reads_the_other_ones_goal(
    twins: tuple[Path, Path],
) -> None:
    """The failure as a person meets it: the wrong objective, in the wrong repo."""
    first, second = twins
    goal_state.set_goal(
        project_dir=str(first),
        goal="ship the billing endpoint",
        acceptance_criteria=["tests pass"],
        complexity="medium",
    )

    assert goal_state.get_goal(str(second)) is None
    assert goal_state.get_goal(str(first)).goal == "ship the billing endpoint"


def test_completing_one_twins_goal_leaves_the_other_alone(
    twins: tuple[Path, Path],
) -> None:
    first, second = twins
    goal_state.set_goal(
        project_dir=str(first), goal="A", acceptance_criteria=[], complexity="low"
    )
    goal_state.set_goal(
        project_dir=str(second), goal="B", acceptance_criteria=[], complexity="low"
    )

    goal_state.mark_complete(str(first))

    assert goal_state.get_goal(str(second)).goal == "B"
    assert goal_state.get_goal(str(second)).status != "complete"


def test_clearing_one_twins_goal_does_not_clear_the_other(
    twins: tuple[Path, Path],
) -> None:
    first, second = twins
    goal_state.set_goal(
        project_dir=str(first), goal="A", acceptance_criteria=[], complexity="low"
    )
    goal_state.set_goal(
        project_dir=str(second), goal="B", acceptance_criteria=[], complexity="low"
    )

    goal_state.clear_goal(str(first))

    assert goal_state.get_goal(str(first)) is None
    assert goal_state.get_goal(str(second)) is not None


def test_the_ordinary_single_project_path_is_unchanged(tmp_path: Path) -> None:
    """One project per basename is the common case; it must not migrate."""
    project = tmp_path / "solo"
    project.mkdir()

    assert goal_state._path_for(str(project)).name == "solo.json"


def test_goal_state_resolves_through_the_one_resolver(tmp_path: Path) -> None:
    """A local copy of the join is exactly how this store drifted the first time."""
    project = tmp_path / "solo"
    project.mkdir()

    assert goal_state._path_for(str(project)) == _xdg.goal_path(str(project))


def test_the_goal_dir_override_still_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "goals-elsewhere"
    monkeypatch.setenv("VISE_GOAL_DIR", str(elsewhere))
    project = tmp_path / "solo"
    project.mkdir()

    assert goal_state._path_for(str(project)).parent == elsewhere


def test_a_relative_project_dir_lands_on_the_same_file_as_the_absolute_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A goal tool reached from inside the project must not open a second file."""
    project = tmp_path / "solo"
    project.mkdir()
    absolute = goal_state._path_for(str(project))

    monkeypatch.chdir(project)

    assert goal_state._path_for(".") == absolute
    assert goal_state._path_for(str(project)) == absolute


# --- the two writers ------------------------------------------------------


def test_a_node_gate_lesson_lands_where_the_readers_look(
    twins: tuple[Path, Path],
) -> None:
    """`load` without `project_dir` falls back to the legacy bare-basename path.

    Every reader resolves the collision-proof one, so a lesson written to the
    legacy path went where nothing looks — and on a collision it went into
    another project's store.
    """
    from types import SimpleNamespace

    from vise.engines.node_gate import _record_node_gate_failure
    from vise.engines.experience_memory import get_project_experience_store

    _, second = twins
    _record_node_gate_failure(
        str(second),
        "implement",
        [SimpleNamespace(name="tests_pass", evidence="3 failed")],
    )

    entries = get_project_experience_store(str(second)).entries
    assert entries, "the lesson was written somewhere the reader does not look"


def test_a_node_gate_lesson_never_lands_in_the_other_twins_store(
    twins: tuple[Path, Path],
) -> None:
    from types import SimpleNamespace

    from vise.engines.node_gate import _record_node_gate_failure
    from vise.engines.experience_memory import get_project_experience_store

    first, second = twins
    _record_node_gate_failure(
        str(second),
        "implement",
        [SimpleNamespace(name="tests_pass", evidence="3 failed")],
    )

    assert not get_project_experience_store(str(first)).entries


def test_two_twins_do_not_interleave_their_evidence_logs(
    twins: tuple[Path, Path],
) -> None:
    """Timestamped logs never overwrite, but mixed ones cannot be told apart."""
    from types import SimpleNamespace

    from vise.engines.validators import _persist_evidence

    first, second = twins
    a = _persist_evidence(
        SimpleNamespace(project_dir=str(first), id="a"), "tests_pass", "A"
    )
    b = _persist_evidence(
        SimpleNamespace(project_dir=str(second), id="b"), "tests_pass", "B"
    )

    assert a and b
    assert Path(a).parent != Path(b).parent


# --- the experience CLI ----------------------------------------------------


def test_experience_stats_reports_the_store_it_actually_read(
    twins: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """A reported path that is not the path in use is a lie about the data."""
    import argparse
    import json

    from vise.cli.experience_cmd import _cmd_stats

    _, second = twins
    rc = _cmd_stats(argparse.Namespace(project_dir=str(second), json=True))
    assert rc == 0

    reported = Path(json.loads(capsys.readouterr().out)["storage"]["project_file"])
    assert reported == _xdg.project_memory_path(str(second))


def test_experience_gc_never_reaches_into_the_other_twins_store(
    twins: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """gc deletes. Pointing it at a store by basename pointed it at a stranger."""
    import argparse
    import json

    from vise.cli.experience_cmd import _cmd_gc

    first, second = twins
    # Only the claimed twin has a store on disk.
    first_store = _xdg.project_memory_path(str(first))
    first_store.parent.mkdir(parents=True, exist_ok=True)
    first_store.write_text('{"entries": []}', encoding="utf-8")

    rc = _cmd_gc(
        argparse.Namespace(
            project_dir=str(second), apply=False, stats=True, json=True
        )
    )
    assert rc == 0

    out = json.loads(capsys.readouterr().out)
    touched = {r.get("store_path") for r in out.get("reports", [])}
    assert str(first_store) not in touched, (
        "gc for the unclaimed twin reported on the other project's store"
    )
