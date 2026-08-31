"""How many tests are allowed to decide, at runtime, not to run.

A skip is a test reporting that it could not check what it names. That is
legitimate for a genuinely absent dependency and is a way for a test to disarm
itself the moment the thing it guards changes shape — `test_readme_tool_count`
skipped when the README claim disappeared, which is precisely when it was needed.

The budget is not a style rule. Nothing in `-ra -q` output distinguishes "3
environment skips" from "40 tests that quietly stopped asserting", and by the
time anyone counts, the difference is a release.

Adding a skip means adding its reason here. That is the whole mechanism: it
forces the question "is this environmental, or am I hiding a failure?" to be
answered in writing, once, rather than never.
"""
from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent

#: Every `pytest.skip` the suite is allowed to carry, and why it is not a
#: disarmed assertion. Keyed by file, valued by the substring of the reason.
ALLOWED = {
    "test_edit_feedback_hook.py": ["ruff not installed"],
    "test_isolation_encoding.py": ["filesystem refuses non-UTF-8 filenames"],
    "test_snapshot_fault_injection.py": ["filesystem refuses non-UTF-8 filenames"],
    "test_lsp_clean_validator.py": ["ruff not on PATH"] * 3,
    "test_lsp_guidance_sync.py": ["not running from a plugin checkout"],
    "test_plugin_lsp_manifest.py": ["not running from a plugin checkout"],
}

_SKIP = re.compile(r"pytest\.skip\(\s*(?:f?)([\"'])(.*?)\1", re.S)


def _skips() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(TESTS.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        reasons = [m.group(2) for m in _SKIP.finditer(path.read_text(encoding="utf-8"))]
        if reasons:
            found[path.name] = reasons
    return found


def test_no_test_file_skips_without_a_declared_reason():
    undeclared = sorted(set(_skips()) - set(ALLOWED))
    assert not undeclared, (
        f"these files skip and are not in the budget: {undeclared}. A skip for a "
        f"missing dependency belongs in ALLOWED with its reason; a skip because "
        f"the thing under test changed is a disarmed assertion, not a skip"
    )


def test_the_skip_count_matches_the_budget():
    actual = {name: len(r) for name, r in _skips().items()}
    expected = {name: len(r) for name, r in ALLOWED.items()}
    assert actual == expected, (
        f"the number of skips moved: {actual} vs the declared {expected}"
    )


def test_every_declared_skip_still_says_what_it_said():
    for name, reasons in _skips().items():
        for reason, expected in zip(reasons, ALLOWED.get(name, [])):
            assert expected in reason, (
                f"{name}: a skip's reason changed to {reason!r}, which no longer "
                f"matches the declared {expected!r}"
            )


def test_a_doc_sync_test_never_skips_when_its_claim_disappears():
    """The specific shape this file was written for.

    A test that mirrors a document must fail when the document stops making the
    claim. Skipping there turns the deletion of a promise into a green run.
    """
    for name in ("test_tool_surface_sync.py", "test_doc_call_sync.py",
                 "test_version_sync.py", "test_env_var_docs_sync.py"):
        path = TESTS / name
        if not path.exists():
            continue
        assert "pytest.skip" not in path.read_text(encoding="utf-8"), (
            f"{name} guards a document; it must assert, never skip"
        )
