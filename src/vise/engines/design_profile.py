"""Resolve a project's design-gate configuration from ``.vise/quality.yaml``.

The design gates read an optional ``design:`` block that sits beside the
``checks:`` map ``vise.engines.quality_profile`` already reads::

    design:
      targets: ["http://localhost:3000/", "file:///abs/path/index.html"]
      breakpoints: [375, 768, 1280]
      allowances:
        raw_color: 0
        raw_font_size: 12

``targets`` is what the render gates load. ``allowances`` is a ratchet for the
static token gate: record what a repo has today, then lower it, the way a
coverage floor works. Every allowance defaults to zero.

Shares ``VISE_QUALITY_PROFILE`` with the quality profile — one override for
one file.

Read on every traverse, so like ``quality_profile`` this must never raise. A
missing file, a malformed document, or a ``design:`` block of the wrong shape
all degrade to :data:`EMPTY`, never to an exception. An empty config is not the
same as a passing one: a render gate that finds no targets fails closed, and
that decision belongs to the validator, not here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml  # pyyaml

from vise.engines.quality_profile import QUALITY_PROFILE_ENV

DEFAULT_BREAKPOINTS: tuple[int, ...] = (375, 768, 1280)

# A target is a page to LOAD, never a page to author. The harness treats
# anything that is not a URL as inline HTML and calls `set_content` on it, so a
# non-URL entry here would run attacker-authored markup and script inside the
# headless browser on whatever machine runs the gate. Only these three schemes
# reach a render; everything else is rejected and reported. CWE-918.
_TARGET_RE = re.compile(r"^(?:https?|file)://\S", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class DesignConfig:
    """What the design gates were told to look at. Never partially valid."""

    targets: tuple[str, ...] = ()
    breakpoints: tuple[int, ...] = DEFAULT_BREAKPOINTS
    allowances: dict[str, int] = field(default_factory=dict)
    # Entries that were named but are not loadable. Kept rather than dropped:
    # a gate that quietly ignores half its configuration reports on less than
    # it was asked to and calls it clean.
    rejected_targets: tuple[str, ...] = ()

    def allowance(self, kind: str) -> int:
        """How many findings of ``kind`` this repo tolerates. Zero unless said."""
        return int(self.allowances.get(kind, 0))


EMPTY = DesignConfig()


def _profile_path(project_dir: str | Path) -> Path:
    import os

    override = os.environ.get(QUALITY_PROFILE_ENV, "").strip()
    if override:
        return Path(override)
    return Path(project_dir) / ".vise" / "quality.yaml"


def _coerce_targets(raw: object) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split configured targets into (loadable, rejected)."""
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return (), ()
    keep: list[str] = []
    drop: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not value.strip():
            continue
        target = value.strip()
        (keep if _TARGET_RE.match(target) else drop).append(target)
    return tuple(keep), tuple(drop)


def _coerce_breakpoints(raw: object) -> tuple[int, ...]:
    if not isinstance(raw, list):
        return DEFAULT_BREAKPOINTS
    widths: list[int] = []
    for value in raw:
        try:
            width = int(value)
        except (TypeError, ValueError):
            continue
        if width > 0:
            widths.append(width)
    return tuple(sorted(set(widths))) or DEFAULT_BREAKPOINTS


def _coerce_allowances(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        try:
            count = int(value)
        except (TypeError, ValueError):
            continue
        # A negative allowance would be stricter than zero, which is already
        # the floor. Clamp rather than reject: a typo must not make the gate
        # unsatisfiable.
        out[key] = max(0, count)
    return out


def load_design_config(project_dir: str | Path) -> DesignConfig:
    """Read the ``design:`` block. Returns :data:`EMPTY` for anything unusable."""
    path = _profile_path(project_dir)
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return EMPTY
    if not isinstance(document, dict):
        return EMPTY
    block = document.get("design")
    if not isinstance(block, dict):
        return EMPTY
    targets, rejected = _coerce_targets(block.get("targets"))
    return DesignConfig(
        targets=targets,
        breakpoints=_coerce_breakpoints(block.get("breakpoints")),
        allowances=_coerce_allowances(block.get("allowances")),
        rejected_targets=rejected,
    )
