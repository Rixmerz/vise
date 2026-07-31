"""Every ``vise.*`` import must resolve to something that actually ships.

The failure this locks down: ``try: from vise.engines.trend_tracker import
record_snapshot / except Exception: pass``. The module never existed in this
package, so the block is a no-op that READS like a working feature — and the
bare ``except`` means no test, no run, and no user ever sees it fail. Ten of
these shipped at once, silently disabling a trend recorder, a pattern-catalog
injector, a project-metadata injector, a node enricher, a usage-state writer,
and the intent classifier behind ``VISE_AUTO_ACTIVATE`` (which therefore could
not fire under any input).

Two import shapes are checked, because the first one alone misses half:

  1. ``import vise.a.b`` / ``from vise.a.b import c`` — ``vise.a.b`` must exist.
  2. ``from vise.a import b`` where ``vise.a`` is a PACKAGE — ``b`` must be a
     real submodule or a name its ``__init__`` defines. This is the shape that
     hid ``from vise.engines import usage_local, usage_state``: ``vise.engines``
     resolves fine, so a module-path-only check calls it clean.

Sibling of test_tool_surface_sync.py: both assert that a hand-written name in
the source still points at something real.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = sorted(
    p for p in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in p.parts
)


def _resolves(module: str) -> bool:
    """True when ``module`` is importable as a module/package."""
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, AttributeError, ValueError):
        # Parent package missing (ModuleNotFoundError subclasses ImportError),
        # or a parent that is a module rather than a package.
        return False


def _is_package(module: str) -> bool:
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, AttributeError, ValueError):
        return False
    return bool(spec and spec.submodule_search_locations)


def _phantom_imports(path: Path) -> list[str]:
    """Return ``"<line>: <dotted name>"`` for each unresolvable vise import."""
    bad: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("vise.") and not _resolves(alias.name):
                    bad.append(f"{node.lineno}: import {alias.name}")
            continue

        if not isinstance(node, ast.ImportFrom):
            continue
        # Relative imports resolve against the file's own package; the absolute
        # form is what this repo uses everywhere, so only that is checked.
        if node.level != 0 or not node.module or not node.module.startswith("vise"):
            continue

        if not _resolves(node.module):
            bad.append(f"{node.lineno}: from {node.module} import ...")
            continue

        # `from <package> import name` — name must be a submodule or an
        # attribute of the package. For a plain module, imported names are
        # ordinary attributes resolved at runtime; not this test's business.
        if not _is_package(node.module):
            continue
        package = importlib.import_module(node.module)
        for alias in node.names:
            if alias.name == "*":
                continue
            if _resolves(f"{node.module}.{alias.name}"):
                continue
            if hasattr(package, alias.name):
                continue
            bad.append(f"{node.lineno}: from {node.module} import {alias.name}")
    return bad


def test_no_phantom_vise_imports() -> None:
    findings: list[str] = []
    for path in SOURCE_FILES:
        for bad in _phantom_imports(path):
            findings.append(f"{path.relative_to(PACKAGE_ROOT.parent)}:{bad}")

    assert not findings, (
        "these imports name vise modules that do not ship — the code guarded by "
        "them can never run, and a bare `except` hides that forever:\n  "
        + "\n  ".join(findings)
    )
