"""Tests for vise.core.xdg_migrate: the legacy → XDG data-dir one-shot merge.

Covers:
1. New legacy entries are merged; existing (by identity) entries are not
   duplicated.
2. The legacy tree is never deleted (union-only merge).
3. Running migrate() twice is idempotent (second run merges nothing new).
4. migrate() is a cheap no-op ("skipped") when the legacy dir doesn't exist.
5. migrate() is a no-op when legacy and target resolve to the same dir.
6. Project-scoped experience files merge the same way as the global one.
7. Per-project graph/workflow state dirs are copied only when absent at the
   destination — an existing destination dir is never clobbered.
8. copy-if-absent dirs/files (telemetry, usage, goal, config.json,
   vise-project.json) are copied only when missing on the target side.
9. A corrupt/truncated legacy experience_memory.json does not crash
   migrate() — it is treated as "nothing to merge" from that file.
10. The module's own __main__ self-check (`_demo`) passes when invoked
    directly, exercising the same assertions as this module's `python -m`
    entrypoint.

migrate() reads ``_paths.data_dir()`` (dynamic, already honors the
suite-wide $XDG_DATA_HOME-redirecting conftest fixture), but
``LEGACY_DATA_DIR`` is a Path resolved at import time from
``Path.home()`` — the conftest fixture does NOT patch this module, so
every test here monkeypatches ``xdg_migrate.LEGACY_DATA_DIR`` directly to
a controlled tmp_path source, per the module-attribute-not-just-env-var
trap documented in conftest.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from vise.core import paths as _paths
from vise.core import xdg_migrate


def _target_dir() -> Path:
    """Resolve the current test's XDG-isolated target dir (set by conftest)."""
    return _paths.data_dir()


def test_migrate_merges_new_entries_and_skips_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A legacy entry not already present (by id) is merged in; a legacy
    entry whose id already exists in the target is not duplicated."""
    legacy = tmp_path / "legacy-vise"
    legacy.mkdir()
    (legacy / "experience_memory.json").write_text(json.dumps({
        "entries": [
            {"id": "dup", "type": "gate_blocked", "file_pattern": "*.py", "description": "d1"},
            {"id": "new1", "type": "smell_fixed", "file_pattern": "*.ts", "description": "d2"},
        ]
    }))
    monkeypatch.setattr(xdg_migrate, "LEGACY_DATA_DIR", legacy)

    target = _target_dir()
    target.mkdir(parents=True, exist_ok=True)
    (target / "experience_memory.json").write_text(json.dumps({
        "entries": [
            {"id": "dup", "type": "gate_blocked", "file_pattern": "*.py", "description": "d1"},
        ]
    }))

    summary = xdg_migrate.migrate()

    assert summary["skipped"] is False
    assert summary["experience_merged"] == 1
    merged = json.loads((target / "experience_memory.json").read_text())
    merged_ids = {e["id"] for e in merged["entries"]}
    assert merged_ids == {"dup", "new1"}


def test_migrate_never_deletes_legacy_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a successful merge, every legacy file/dir must still exist."""
    legacy = tmp_path / "legacy-vise"
    legacy.mkdir()
    (legacy / "experience_memory.json").write_text(json.dumps({
        "entries": [{"id": "a1", "type": "x", "file_pattern": "*.py", "description": "d"}]
    }))
    (legacy / "states" / "proj").mkdir(parents=True)
    (legacy / "states" / "proj" / "graph_state.json").write_text('{"active_graph": "x"}')
    monkeypatch.setattr(xdg_migrate, "LEGACY_DATA_DIR", legacy)

    xdg_migrate.migrate()

    assert legacy.exists()
    assert (legacy / "experience_memory.json").exists()
    assert (legacy / "states" / "proj" / "graph_state.json").exists()


def test_migrate_is_idempotent_second_run_merges_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running migrate() twice must not duplicate or re-merge entries."""
    legacy = tmp_path / "legacy-vise"
    legacy.mkdir()
    (legacy / "experience_memory.json").write_text(json.dumps({
        "entries": [{"id": "a1", "type": "x", "file_pattern": "*.py", "description": "d"}]
    }))
    monkeypatch.setattr(xdg_migrate, "LEGACY_DATA_DIR", legacy)

    first = xdg_migrate.migrate()
    second = xdg_migrate.migrate()

    assert first["experience_merged"] == 1
    assert second["experience_merged"] == 0
    target = _target_dir()
    merged = json.loads((target / "experience_memory.json").read_text())
    assert len(merged["entries"]) == 1


def test_migrate_skipped_when_legacy_dir_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No legacy dir at all → cheap skip with a reason, no crash."""
    legacy = tmp_path / "does-not-exist"
    monkeypatch.setattr(xdg_migrate, "LEGACY_DATA_DIR", legacy)

    summary = xdg_migrate.migrate()

    assert summary["skipped"] is True
    assert summary["reason"]
    assert summary["experience_merged"] == 0


def test_migrate_skipped_when_legacy_and_target_are_same_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If LEGACY_DATA_DIR resolves to the same path as the XDG target
    (e.g. XDG_DATA_HOME unset and pointing at the legacy default), migrate
    must skip rather than merge a directory into itself."""
    same_dir = tmp_path / "same-vise"
    same_dir.mkdir()
    (same_dir / "experience_memory.json").write_text(json.dumps({"entries": []}))
    monkeypatch.setattr(xdg_migrate, "LEGACY_DATA_DIR", same_dir)
    monkeypatch.setattr(_paths, "data_dir", lambda: same_dir)

    summary = xdg_migrate.migrate()

    assert summary["skipped"] is True
    assert summary["reason"] == "no split-brain (same dir or legacy dir absent)"


def test_migrate_merges_project_scoped_experience_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per-project experience_memory.json files merge the same way as the
    global one, keyed by project directory name."""
    legacy = tmp_path / "legacy-vise"
    proj_dir = legacy / "project_memories" / "myrepo"
    proj_dir.mkdir(parents=True)
    (proj_dir / "experience_memory.json").write_text(json.dumps({
        "entries": [{"id": "p1", "type": "x", "file_pattern": "*.py", "description": "d"}]
    }))
    monkeypatch.setattr(xdg_migrate, "LEGACY_DATA_DIR", legacy)

    summary = xdg_migrate.migrate()

    assert summary["project_experience_merged"] == {"myrepo": 1}
    target = _target_dir()
    merged_file = target / "project_memories" / "myrepo" / "experience_memory.json"
    assert merged_file.exists()
    merged = json.loads(merged_file.read_text())
    assert merged["entries"][0]["id"] == "p1"


def test_migrate_copies_missing_project_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A legacy project state dir absent on the target side is copied over."""
    legacy = tmp_path / "legacy-vise"
    (legacy / "states" / "newproj").mkdir(parents=True)
    (legacy / "states" / "newproj" / "graph_state.json").write_text('{"active_graph": "g"}')
    monkeypatch.setattr(xdg_migrate, "LEGACY_DATA_DIR", legacy)

    summary = xdg_migrate.migrate()

    assert summary["states_copied"] == ["newproj"]
    target = _target_dir()
    assert (target / "states" / "newproj" / "graph_state.json").exists()


def test_migrate_does_not_clobber_existing_target_state_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the target already has a state dir for a project, the legacy
    copy must be skipped entirely — an existing destination is never
    overwritten."""
    legacy = tmp_path / "legacy-vise"
    (legacy / "states" / "proj1").mkdir(parents=True)
    (legacy / "states" / "proj1" / "graph_state.json").write_text('{"active_graph": "legacy-value"}')
    monkeypatch.setattr(xdg_migrate, "LEGACY_DATA_DIR", legacy)

    target = _target_dir()
    (target / "states" / "proj1").mkdir(parents=True)
    (target / "states" / "proj1" / "graph_state.json").write_text('{"active_graph": "target-value"}')

    summary = xdg_migrate.migrate()

    assert summary["states_copied"] == []
    kept = json.loads((target / "states" / "proj1" / "graph_state.json").read_text())
    assert kept["active_graph"] == "target-value", "existing destination state was clobbered"


def test_migrate_copies_missing_dirs_and_files_only_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """telemetry/usage/goal dirs and config.json/vise-project.json files
    are copied only when absent on the target side."""
    legacy = tmp_path / "legacy-vise"
    (legacy / "telemetry").mkdir(parents=True)
    (legacy / "telemetry" / "log.jsonl").write_text("{}\n")
    (legacy / "usage").mkdir(parents=True)
    (legacy / "config.json").write_text('{"k": "legacy"}')
    monkeypatch.setattr(xdg_migrate, "LEGACY_DATA_DIR", legacy)

    target = _target_dir()
    target.mkdir(parents=True, exist_ok=True)
    (target / "usage").mkdir(parents=True)  # already present -> must NOT be reported copied
    (target / "usage" / "marker.txt").write_text("target-owns-this")

    summary = xdg_migrate.migrate()

    assert "telemetry" in summary["dirs_copied"]
    assert "usage" not in summary["dirs_copied"], "existing target dir was reported as copied"
    assert "config.json" in summary["files_copied"]
    assert (target / "telemetry" / "log.jsonl").exists()
    # target's own usage/ contents must be untouched
    assert (target / "usage" / "marker.txt").read_text() == "target-owns-this"


def test_migrate_handles_corrupt_legacy_json_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truncated/corrupt legacy experience_memory.json must not raise —
    migrate() treats it as nothing-to-merge from that file and continues."""
    legacy = tmp_path / "legacy-vise"
    legacy.mkdir()
    (legacy / "experience_memory.json").write_text("{not valid json!!!")
    monkeypatch.setattr(xdg_migrate, "LEGACY_DATA_DIR", legacy)

    summary = xdg_migrate.migrate()

    assert "error" not in summary
    assert summary["experience_merged"] == 0
    assert summary["skipped"] is False


def test_demo_self_check_passes() -> None:
    """The module's own __main__ assert-based self-check (never run by
    pytest collection since it's gated behind ``if __name__ ==
    "__main__"``) is invoked directly here so its assertions are exercised
    as part of the suite."""
    xdg_migrate._demo()
