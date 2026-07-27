"""One-shot migration from the legacy hardcoded data dir to the XDG one.

Prior to this fix, the hard-blocking hooks under ``vise/hooks/`` ignored
``$XDG_DATA_HOME`` and always wrote to ``~/.local/share/vise`` (see
``vise.hooks._xdg``), while the MCP tools honored it via
``vise.core.paths.data_dir()``. Users with ``$XDG_DATA_HOME`` set ended up
with two disjoint data trees. :func:`migrate` merges the legacy tree into
the resolved (correct) tree without ever deleting the legacy copy.

Safe to run repeatedly: entries are unioned by identity, and files/dirs are
only copied when absent on the target side.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from vise.core import paths as _paths

LEGACY_DATA_DIR = Path.home() / ".local" / "share" / "vise"

# Copied wholesale only if missing on the target side.
_COPY_IF_ABSENT_DIRS = ("telemetry", "usage", "goal")
_COPY_IF_ABSENT_FILES = ("config.json", "vise-project.json")


def _entry_identity(entry: dict[str, Any]) -> tuple:
    """Identity for dedup: prefer the ``id`` field; fall back to the
    natural key the hooks' own upsert logic uses (type, file_pattern,
    description) for entries written without an id — see
    ``vise.hooks.experience_recorder._find_duplicate``."""
    entry_id = entry.get("id")
    if entry_id:
        return ("id", entry_id)
    return ("natural", entry.get("type", ""), entry.get("file_pattern", ""), entry.get("description", ""))


def _merge_experience_file(legacy_path: Path, target_path: Path) -> int:
    """Union legacy entries into target's experience_memory.json.

    Returns the number of entries carried over from the legacy file that
    were not already present in the target (by identity). Never drops an
    entry from either side.
    """
    if not legacy_path.exists():
        return 0

    try:
        legacy_data = json.loads(legacy_path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    legacy_entries = legacy_data.get("entries", [])
    if not legacy_entries:
        return 0

    if target_path.exists():
        try:
            target_data = json.loads(target_path.read_text(encoding="utf-8"))
        except Exception:
            target_data = {"entries": []}
    else:
        target_data = {"entries": []}

    target_entries = target_data.get("entries", [])
    seen = {_entry_identity(e) for e in target_entries}

    merged = 0
    for entry in legacy_entries:
        key = _entry_identity(entry)
        if key in seen:
            continue
        target_entries.append(entry)
        seen.add(key)
        merged += 1

    if merged:
        target_data["entries"] = target_entries
        target_data["count"] = len(target_entries)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(json.dumps(target_data, indent=2), encoding="utf-8")

    return merged


def _copy_missing_project_states(legacy_states: Path, target_states: Path) -> list[str]:
    if not legacy_states.exists():
        return []
    copied = []
    for project_dir in sorted(p for p in legacy_states.iterdir() if p.is_dir()):
        dest = target_states / project_dir.name
        if dest.exists():
            continue
        shutil.copytree(project_dir, dest)
        copied.append(project_dir.name)
    return copied


def migrate() -> dict[str, Any]:
    """Merge the legacy ``~/.local/share/vise`` tree into the XDG-resolved
    data dir. No-op (and cheap) if they already resolve to the same path.

    Returns a summary dict describing what was merged/copied. Never
    raises; never deletes the legacy tree.
    """
    target_dir = _paths.data_dir()
    summary: dict[str, Any] = {
        "legacy_dir": str(LEGACY_DATA_DIR),
        "target_dir": str(target_dir),
        "skipped": False,
        "experience_merged": 0,
        "project_experience_merged": {},
        "states_copied": [],
        "dirs_copied": [],
        "files_copied": [],
    }

    if LEGACY_DATA_DIR.resolve() == target_dir.resolve() or not LEGACY_DATA_DIR.exists():
        summary["skipped"] = True
        summary["reason"] = "no split-brain (same dir or legacy dir absent)"
        return summary

    try:
        # 1. Global experience memory.
        summary["experience_merged"] = _merge_experience_file(
            LEGACY_DATA_DIR / "experience_memory.json",
            target_dir / "experience_memory.json",
        )

        # 2. Per-project experience memory.
        legacy_proj_dir = LEGACY_DATA_DIR / "project_memories"
        if legacy_proj_dir.exists():
            for proj_dir in sorted(p for p in legacy_proj_dir.iterdir() if p.is_dir()):
                legacy_file = proj_dir / "experience_memory.json"
                if not legacy_file.exists():
                    continue
                n = _merge_experience_file(
                    legacy_file,
                    target_dir / "project_memories" / proj_dir.name / "experience_memory.json",
                )
                if n:
                    summary["project_experience_merged"][proj_dir.name] = n

        # 3. Per-project graph/workflow states.
        summary["states_copied"] = _copy_missing_project_states(
            LEGACY_DATA_DIR / "states", target_dir / "states"
        )

        # 4. Misc dirs/files, copy-if-absent only.
        for name in _COPY_IF_ABSENT_DIRS:
            legacy_sub = LEGACY_DATA_DIR / name
            target_sub = target_dir / name
            if legacy_sub.exists() and not target_sub.exists():
                shutil.copytree(legacy_sub, target_sub)
                summary["dirs_copied"].append(name)

        for name in _COPY_IF_ABSENT_FILES:
            legacy_file = LEGACY_DATA_DIR / name
            target_file = target_dir / name
            if legacy_file.exists() and not target_file.exists():
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy_file, target_file)
                summary["files_copied"].append(name)
    except Exception as exc:  # pragma: no cover - defensive, migration must not crash the CLI
        summary["error"] = str(exc)

    return summary


__all__ = ["LEGACY_DATA_DIR", "migrate"]


def _demo() -> None:
    """assert-based self-check: legacy/target merge is union, idempotent,
    and never deletes the legacy tree. Run: python -m vise.core.xdg_migrate
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        legacy = tmp_path / "home" / ".local" / "share" / "vise"
        target = tmp_path / "xdg" / "vise"
        (legacy / "states" / "proj").mkdir(parents=True)
        (legacy / "states" / "proj" / "graph_state.json").write_text('{"active_graph": "x"}')
        legacy.mkdir(parents=True, exist_ok=True)
        (legacy / "experience_memory.json").write_text(json.dumps({
            "entries": [
                {"id": "a1", "type": "gate_blocked", "file_pattern": "*.py", "description": "d1"},
                {"type": "smell_fixed", "file_pattern": "*.ts", "description": "d2"},  # no id
            ]
        }))
        target.mkdir(parents=True)
        (target / "experience_memory.json").write_text(json.dumps({
            "entries": [
                {"id": "a1", "type": "gate_blocked", "file_pattern": "*.py", "description": "d1"},  # dup
                {"id": "b2", "type": "impact_high", "file_pattern": "*.go", "description": "d3"},
            ]
        }))

        global LEGACY_DATA_DIR
        orig_legacy = LEGACY_DATA_DIR
        LEGACY_DATA_DIR = legacy
        orig_data_dir = _paths.data_dir
        _paths.data_dir = lambda: target
        try:
            summary = migrate()
            assert summary["experience_merged"] == 1, summary  # only d2 is new
            merged = json.loads((target / "experience_memory.json").read_text())
            ids_or_desc = {e.get("id") or e["description"] for e in merged["entries"]}
            assert ids_or_desc == {"a1", "b2", "d2"}, ids_or_desc
            assert (target / "states" / "proj" / "graph_state.json").exists()
            assert legacy.exists(), "legacy tree must never be deleted"

            # Idempotent: running again merges nothing new.
            summary2 = migrate()
            assert summary2["experience_merged"] == 0, summary2
            merged2 = json.loads((target / "experience_memory.json").read_text())
            assert len(merged2["entries"]) == 3, merged2
        finally:
            LEGACY_DATA_DIR = orig_legacy
            _paths.data_dir = orig_data_dir

    print("xdg_migrate self-check: OK")


if __name__ == "__main__":
    _demo()
