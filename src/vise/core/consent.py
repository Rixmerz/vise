"""Consent for a command the repository chose.

``.vise/quality.yaml`` is committed with the repository, so the commands in it
were written by whoever wrote the repository — and ``QualityCheckValidator``
runs them and grades the exit code as ``source="mechanical"``. A freshly cloned
repository could therefore run anything it liked the first time a node gate
asked for ``sast``, and have the result count as verification.

This module is the answer: a command runs only after someone on *this machine*
approved *that command*. Approval is by digest, so a repository that edits the
command after it was approved is back to unapproved — the thing consented to is
the command, not the check's name.

Where consent comes from, in the order people meet it:

- ``vise bootstrap`` wrote the profile with the person present, so it approves
  what it wrote. A profile that arrived with a clone was never bootstrapped
  here and needs ``vise approve``.
- ``vise approve <check>`` (or ``--all``) after reading the file.
- ``VISE_TRUST_PROJECT_TOOLS=1`` trusts every repository-provided command and
  binary. It already meant that for a checker committed under ``.venv/``; a
  command committed under ``.vise/`` is the same trust decision.

The record lives in user scope (``$XDG_DATA_HOME/vise/approved_checks.json``),
never in the repository: a consent file the repository could commit would be
consent the repository gave itself.
"""
from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from vise.core.atomic import write_atomic
from vise.core.paths import data_dir

TRUST_ENV = "VISE_TRUST_PROJECT_TOOLS"

_VERSION = 1

#: The three answers ``approval_state`` gives. ``changed`` is the interesting
#: one: the check was approved once and the repository has since rewritten
#: its command, which is exactly the case that must not inherit the approval.
APPROVED = "approved"
CHANGED = "changed"
UNAPPROVED = "unapproved"


def approvals_path() -> Path:
    return data_dir() / "approved_checks.json"


def _key(project_dir: str | Path) -> str:
    return str(Path(project_dir).resolve())


def command_digest(cmd: Sequence[str]) -> str:
    """A digest of the argv, argument boundaries included.

    Joined on NUL rather than space so ``["a b", "c"]`` and ``["a", "b c"]``
    differ: they run differently, so they are different commands.
    """
    raw = "\0".join(cmd).encode("utf-8", "surrogateescape")
    return hashlib.sha256(raw).hexdigest()


def _load() -> dict:
    path = approvals_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": _VERSION, "projects": {}}
    if not isinstance(data, dict) or not isinstance(data.get("projects"), dict):
        return {"version": _VERSION, "projects": {}}
    return data


def _save(data: dict) -> None:
    path = approvals_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def approved(project_dir: str | Path) -> dict[str, dict]:
    """``{check: {"digest", "command", "approved_at"}}`` for one project."""
    projects = _load()["projects"]
    record = projects.get(_key(project_dir), {})
    return record if isinstance(record, dict) else {}


def approval_state(project_dir: str | Path, check: str, cmd: Sequence[str]) -> str:
    record = approved(project_dir).get(check)
    if not isinstance(record, dict):
        return UNAPPROVED
    return APPROVED if record.get("digest") == command_digest(cmd) else CHANGED


def is_approved(project_dir: str | Path, check: str, cmd: Sequence[str]) -> bool:
    return approval_state(project_dir, check, cmd) == APPROVED


def trusted(project_dir: str | Path, check: str, cmd: Sequence[str]) -> bool:
    """May this command run? The env var is the blanket, approval the specific."""
    if os.environ.get(TRUST_ENV) == "1":
        return True
    return is_approved(project_dir, check, cmd)


def approve(project_dir: str | Path, check: str, cmd: Sequence[str]) -> dict:
    data = _load()
    record = {
        "digest": command_digest(cmd),
        "command": list(cmd),
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    data["projects"].setdefault(_key(project_dir), {})[check] = record
    _save(data)
    return record


def revoke(project_dir: str | Path, check: str) -> bool:
    data = _load()
    project = data["projects"].get(_key(project_dir))
    if not isinstance(project, dict) or check not in project:
        return False
    del project[check]
    if not project:
        del data["projects"][_key(project_dir)]
    _save(data)
    return True
