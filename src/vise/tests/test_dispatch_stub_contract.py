"""The capability validator's stub detection must track the real stub.

`CapabilityValidator` tells two failures apart — "the capability is unbound"
(fix: `capability_set`) and "it resolved but vise cannot dispatch it" (fix:
run the tool yourself) — by recognising `recipes.runner._call_tool`'s reply
via `_is_dispatch_stub`, which matches on the substring
``"no MCP dispatch layer"``.

That string is written by hand in three places: the producer, the detector,
and a test fixture that copies it. A hand-maintained mirror is what silently
drifts — reword the producer and the copied fixture still passes while the
detector stops matching, sending the user back to blaming the binding for a
problem binding cannot fix.

So this asserts the contract against the **real** `_call_tool` output rather
than a transcription of it.
"""
from __future__ import annotations

import asyncio

from vise.engines.validators import _is_dispatch_stub
from vise.recipes.runner import _call_tool


def test_detector_recognises_the_real_call_tool_reply():
    mcp, tool = "layoutlint", "layout_check"
    output = asyncio.run(_call_tool(mcp, tool, {"target": "x"}))
    assert _is_dispatch_stub(output, mcp, tool), (
        f"_call_tool's reply is no longer recognised as the dispatch stub: {output!r}. "
        "The validator will now report the wrong remediation for a bound capability."
    )


def test_detector_rejects_a_real_tool_result():
    # An in-host embedder monkeypatches _call_tool with a real dispatcher; its
    # results must never be mistaken for the stub, or every genuine check would
    # be reported as undispatchable.
    assert not _is_dispatch_stub({"ok": True}, "layoutlint", "layout_check")
    assert not _is_dispatch_stub({"status": "unresolved"}, "layoutlint", "layout_check")


def test_detector_rejects_a_stub_for_a_different_tool():
    # Guards the mcp/tool identity check: a stub reply that names another tool
    # is not evidence about *this* capability.
    output = asyncio.run(_call_tool("other", "thing", {}))
    assert not _is_dispatch_stub(output, "layoutlint", "layout_check")
