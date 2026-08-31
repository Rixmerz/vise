"""The hand-maintained tool lists must match the real MCP surface.

Two places name vise's tools by hand: ``VISE_TOOLS`` in
``test_asset_honesty.py`` (the allowlist ``INTERNAL_BINDINGS`` may point at)
and the count in ``README.md``. Both were correct when written and both are
mirrors, which is exactly the shape that drifts silently — the README said
50 while the registry had 49, and a stale allowlist would let a binding to a
removed tool pass the honesty test it exists to prevent.

Counting from the registry here means the next tool added or removed fails a
test instead of quietly making a document wrong.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]


def _registered_tool_names() -> set[str]:
    from vise.server import mcp
    from vise.tools.bootstrap import register_all

    register_all(mcp)
    import asyncio

    tools = asyncio.run(mcp._list_tools())
    return {getattr(t, "name", str(t)) for t in tools}


def _declared_in_asset_honesty() -> set[str]:
    src = (Path(__file__).parent / "test_asset_honesty.py").read_text(encoding="utf-8")
    m = re.search(r"VISE_TOOLS\s*=\s*frozenset\(\{(.*?)\}\)", src, re.S)
    assert m, "VISE_TOOLS frozenset not found in test_asset_honesty.py"
    return set(re.findall(r'"([a-z_]+)"', m.group(1)))


def test_asset_honesty_allowlist_matches_registry():
    real = _registered_tool_names()
    declared = _declared_in_asset_honesty()
    assert declared == real, (
        f"VISE_TOOLS drifted from the registry — "
        f"only in the list: {sorted(declared - real)}; "
        f"only in the registry: {sorted(real - declared)}"
    )


def test_readme_tool_count_matches_registry():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    m = re.search(r"exposes \*\*(\d+) tools\*\*", readme)
    assert m is not None, (
        "the README must state an exact tool count — this test exists because "
        "it once said 50 while the registry had 49, and a test that skips when "
        "the claim it guards is deleted guards nothing"
    )
    assert int(m.group(1)) == len(_registered_tool_names())
