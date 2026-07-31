"""``VISE_*`` knobs and README rows must describe each other, in both directions.

Direction 1 — a knob the code reads with no README row is undiscoverable. This
already shipped once and was fixed by hand ("two env vars were undocumented"),
which means it can regress by hand too.

Direction 2 — a README row nothing reads is a promise vise does not keep. This
one found three at once: ``VISE_JUDGE_CMD`` (documented as driving "AI
validators"; the string `judge` appeared nowhere in the package except an error
message telling users to set it), plus ``VISE_AUTONOMY`` and
``VISE_AUTO_ACTIVATE``, whose consumers never shipped.

Both directions have to understand how this package actually reads env vars, or
they produce noise instead of findings:

  - ``os.environ.get("VISE_X")`` / ``os.environ["VISE_X"]`` — the literal form.
  - ``ON_EDIT_ENV_VAR = "VISE_SNAPSHOT_ON_EDIT"`` then ``environ.get(CONST)`` —
    a module constant holding the name.
  - ``_env("EMBED_CACHE_DIR")`` where the helper prepends ``VISE_`` — the name
    never appears in full anywhere in the source.

A WRITE is not a read: ``env["VISE_AUTONOMY"] = "1"`` is how vise hands a flag
to a child process or settings file, and it says nothing about whether anything
ever consumes it. Direction 2 deliberately does not count those.

Sibling of test_tool_surface_sync.py and test_no_phantom_imports.py.
"""
from __future__ import annotations

import re
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]
README = REPO_ROOT / "README.md"

# Implementation details a user has no reason to set. An entry here must be
# genuinely internal — adding a user-facing knob to silence this test is the
# failure mode it exists to catch.
INTERNAL = {
    # Recursion guard `vise graph run` sets on the subprocess it spawns so a
    # nested run cannot fork bomb.
    "VISE_GRAPH_RUN_INNER",
}

# Documented, deliberately unread, and the README row must say so out loud. That
# pairing is what keeps this set from becoming a place to hide dead knobs: the
# caveat text below has to appear in the row, or the test fails anyway.
#
# Currently EMPTY, and that is the point. It briefly held VISE_AUTO_ACTIVATE and
# VISE_AUTONOMY; both were resolved by deleting the knob rather than by
# explaining it, which is the outcome this set should always be pushing toward.
DOCUMENTED_AS_NOT_WIRED: dict[str, str] = {}

_ENV_LITERAL = re.compile(r"""environ(?:\.get)?[(\[]\s*["'](VISE_[A-Z0-9_]+)["']""")
_ENV_VIA_CONST = re.compile(r"""^\s*[A-Z][A-Z0-9_]*\s*=\s*["'](VISE_[A-Z0-9_]+)["']""", re.M)
_ENV_SUFFIX_HELPER = re.compile(r"""_env\(\s*["']([A-Z0-9_]+)["']""")
_PREPENDS_PREFIX = re.compile(r"""["']VISE_["']\s*\+""")


def _sources() -> list[Path]:
    return sorted(
        p for p in PACKAGE_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts and "tests" not in p.parts
    )


def _read_names() -> dict[str, str]:
    """Every VISE_* name the package READS, mapped to where it was found."""
    found: dict[str, str] = {}
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        rel = str(path.relative_to(REPO_ROOT))
        names = set(_ENV_LITERAL.findall(text)) | set(_ENV_VIA_CONST.findall(text))
        if _PREPENDS_PREFIX.search(text):
            names |= {f"VISE_{suffix}" for suffix in _ENV_SUFFIX_HELPER.findall(text)}
        for name in names:
            found.setdefault(name, rel)
    return found


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def _documented() -> set[str]:
    return set(re.findall(r"VISE_[A-Z0-9_]+", _readme()))


def test_every_env_var_read_is_documented() -> None:
    documented = _documented()
    undocumented = {
        name: src for name, src in _read_names().items()
        if name not in documented and name not in INTERNAL
    }
    assert not undocumented, (
        "read by the code, absent from README.md — a knob nobody can find:\n  "
        + "\n  ".join(f"{n}  ({src})" for n, src in sorted(undocumented.items()))
    )


def test_every_documented_env_var_is_read() -> None:
    read = set(_read_names())
    unread = sorted(
        name for name in _documented()
        if name not in read and name not in DOCUMENTED_AS_NOT_WIRED
    )
    assert not unread, (
        "README documents these but nothing reads them — wire them up, drop the "
        f"row, or label the row and list them in DOCUMENTED_AS_NOT_WIRED: {unread}"
    )


def test_not_wired_knobs_are_labelled_as_such_in_the_readme() -> None:
    """The escape hatch above only holds while the README admits the caveat."""
    text = _readme()
    for name, caveat in DOCUMENTED_AS_NOT_WIRED.items():
        row = next((ln for ln in text.splitlines() if f"`{name}`" in ln), None)
        assert row, f"{name} is listed as not-wired but has no README row at all"
        assert caveat in row, (
            f"{name}'s README row must carry the caveat {caveat!r} so readers are "
            f"not told to set a knob that does nothing. Row: {row.strip()}"
        )
