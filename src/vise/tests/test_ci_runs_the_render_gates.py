"""CI has to be able to run the gates vise ships.

`browser_status()` degrades to a skip when chromium is absent, which is right
for a contributor who has not installed it and wrong for the pipeline that is
supposed to be the check. CI installed `.[dev]`, which does not include
playwright, so `test_design_gates_render.py` skipped on every run and the three
design gates — the ones this repo's own CLAUDE.md calls out by name — shipped
exercised by nobody.

It also made the coverage floor unreproducible: the number measured locally
included browser-driven paths CI could not reach, so the ratchet was set from a
run CI cannot repeat.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
CI = REPO / ".github" / "workflows" / "ci.yml"


def test_ci_installs_the_design_extra():
    text = CI.read_text(encoding="utf-8")
    assert re.search(r"pip install -e '\.\[[^\]]*design[^\]]*\]'", text), (
        "CI installs no playwright, so every render test skips and the design "
        "gates are verified by nobody"
    )


def test_ci_installs_a_browser():
    text = CI.read_text(encoding="utf-8")
    assert "playwright install" in text, (
        "the design extra installs the library; the browser is a separate step"
    )


def test_the_render_tests_would_otherwise_be_invisible():
    """Their skip is silent by construction — module-level, no output.

    Which is why this is asserted against CI's config rather than by counting
    skips at runtime: a suite that skips them reports the same green as one
    that runs them.
    """
    render = Path(__file__).parent / "test_design_gates_render.py"
    text = render.read_text(encoding="utf-8")
    assert "pytestmark" in text and "skipif" in text, (
        "if this file stopped skipping, this test is guarding nothing"
    )
