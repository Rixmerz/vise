"""Resolve a project's declared quality-check commands from ``.vise/quality.yaml``.

Lets a repo name the shell commands for its own quality checks ONCE (unit
tests, lint, SAST, SCA, complexity, ...) so bundled workflows can gate on
checks like SAST without hardcoding a command that does not exist on most
machines::

    checks:
      unit: ["pytest", "-q"]
      lint: ["ruff", "check", "."]
      sast: ["bandit", "-r", "src"]

``VISE_QUALITY_PROFILE`` overrides the profile path with an absolute path to
a YAML file (used by tests, and by repos that keep config elsewhere).

Read on every traverse — must never raise. Every malformed/missing input
degrades to an ``UnboundCheck`` reason instead of an exception.
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import yaml  # pyyaml

QUALITY_PROFILE_ENV = "VISE_QUALITY_PROFILE"


class UnboundReason(str, Enum):
    """Why a requested check has no runnable command."""

    NO_PROFILE = "no_profile"      # no quality.yaml (missing/unreadable/malformed/no `checks:` map)
    NOT_LISTED = "not_listed"      # profile exists, but this check key is absent/empty


@dataclass(frozen=True, slots=True)
class UnboundCheck:
    """A quality check with no command bound to it — never an exception."""

    reason: UnboundReason


def _profile_path(project_dir: str | Path) -> Path:
    import os

    override = os.environ.get(QUALITY_PROFILE_ENV, "").strip()
    if override:
        return Path(override)
    return Path(project_dir) / ".vise" / "quality.yaml"


def _load_checks_map(project_dir: str | Path) -> dict | None:
    """Return the ``checks:`` mapping, or None if unusable for any reason."""
    path = _profile_path(project_dir)
    try:
        if not path.is_file():
            return None
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    checks = raw.get("checks")
    if not isinstance(checks, dict):
        return None
    return checks


def list_checks(project_dir: str | Path) -> list[str]:
    """Every check name the profile declares, in file order. Empty if no profile."""
    checks = _load_checks_map(project_dir)
    return [str(k) for k in checks] if checks else []


def resolve_check(project_dir: str | Path, check_name: str) -> tuple[str, ...] | UnboundCheck:
    """Resolve *check_name* to a command tuple, or an ``UnboundCheck`` reason.

    Never raises. A string value (``unit: "pytest -q"``) is shlex-split.
    An absent key, an empty command (empty list, or a string that
    shlex-splits to nothing), or a string shlex cannot parse (e.g. an
    unbalanced quote) is ``NOT_LISTED`` — the profile exists, this check
    just isn't usable as written. A missing/unreadable/malformed profile,
    or one with no usable ``checks:`` mapping, is ``NO_PROFILE``.
    """
    checks = _load_checks_map(project_dir)
    if checks is None:
        return UnboundCheck(reason=UnboundReason.NO_PROFILE)

    if check_name not in checks:
        return UnboundCheck(reason=UnboundReason.NOT_LISTED)

    value = checks[check_name]
    if isinstance(value, str):
        try:
            cmd = tuple(shlex.split(value))
        except ValueError:
            return UnboundCheck(reason=UnboundReason.NOT_LISTED)
    elif isinstance(value, list):
        cmd = tuple(str(v) for v in value)
    else:
        return UnboundCheck(reason=UnboundReason.NOT_LISTED)

    if not cmd:
        return UnboundCheck(reason=UnboundReason.NOT_LISTED)
    return cmd
