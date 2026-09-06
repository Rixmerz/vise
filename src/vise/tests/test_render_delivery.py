"""A page that was never served must not measure as a clean one.

`ui_layout` and `ui_contrast` fail closed on a missing browser and on an
unconfigured target, on the stated principle that a gate which could not check
must never report success. Neither read the HTTP status, so a 404 measured
green: an error page has no overflow, no collisions and nothing off-document.
The gate was fail-closed about its own preconditions and fail-open about
whether it had been given the page at all.

These tests run against a real HTTP server rather than a faked response,
because the thing under test is what Playwright hands back from a navigation.
"""
from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from vise.engines.render_harness import browser_status
from vise.engines.ui_checks import check_snapshot

_PAGE = b"<html><body><div id='a' style='width:100px'>hello</div></body></html>"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's interface
        status = 404 if self.path.startswith("/missing") else 200
        body = b"<html><body><h1>Not Found</h1></body></html>" if status == 404 else _PAGE
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: D102 - silence the test output
        return


@pytest.fixture(scope="module")
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


needs_browser = pytest.mark.skipif(
    not browser_status()[0], reason="no chromium — the render gates cannot run"
)


# ---------------------------------------------------------------------------
# The check itself, which needs no browser
# ---------------------------------------------------------------------------

def test_a_snapshot_from_an_error_page_yields_one_defect_naming_the_cause():
    """One defect that says HTTP 404 beats forty about an error page's boxes."""
    snapshot = {
        "delivery": {"status": 404, "ok": False, "url": "http://x/missing", "kind": "http"},
        "nodes": {}, "viewport": {"width": 1280, "height": 720},
        "document": {"width": 1280, "height": 720}, "unresolved": [],
    }
    defects = check_snapshot(snapshot, breakpoint=1280)
    assert [d.kind for d in defects] == ["page_not_delivered"]
    assert defects[0].severity == "error"
    assert "404" in defects[0].detail


def test_a_delivered_page_is_checked_normally():
    snapshot = {
        "delivery": {"status": 200, "ok": True, "url": "http://x/", "kind": "http"},
        "nodes": {}, "viewport": {"width": 1280, "height": 720},
        "document": {"width": 1280, "height": 720}, "unresolved": [],
    }
    assert check_snapshot(snapshot, breakpoint=1280) == []


def test_a_snapshot_with_no_delivery_block_is_not_flagged():
    """Inline HTML and `file://` have no status, and every snapshot taken
    before this existed has no block at all. Neither is a served error."""
    snapshot = {
        "nodes": {}, "viewport": {"width": 1280, "height": 720},
        "document": {"width": 1280, "height": 720}, "unresolved": [],
    }
    assert check_snapshot(snapshot, breakpoint=1280) == []


@pytest.mark.parametrize("kind", ["inline", "no-response"])
def test_a_target_with_no_server_is_never_flagged(kind: str):
    snapshot = {
        "delivery": {"status": None, "ok": True, "url": "", "kind": kind},
        "nodes": {}, "viewport": {"width": 1280, "height": 720},
        "document": {"width": 1280, "height": 720}, "unresolved": [],
    }
    assert check_snapshot(snapshot, breakpoint=1280) == []


# ---------------------------------------------------------------------------
# Against a real server, because the subject is what a navigation returns
# ---------------------------------------------------------------------------

@needs_browser
def test_the_harness_reports_the_status_a_real_server_returned(server):
    from vise.engines.render_harness import extract

    ok = extract(f"{server}/", {"a": "#a"}, breakpoint=1280)
    assert ok["delivery"]["status"] == 200 and ok["delivery"]["ok"]

    missing = extract(f"{server}/missing", {"a": "#a"}, breakpoint=1280)
    assert missing["delivery"]["status"] == 404
    assert not missing["delivery"]["ok"]


@needs_browser
def test_a_404_is_a_finding_end_to_end(server):
    """The whole path: navigate, extract, check. Before this, the 404 came
    back with an empty node set and `check_snapshot` reported nothing — which
    the gate summarised as "0 elements inspected, no blocking defects"."""
    from vise.engines.render_harness import extract

    snapshot = extract(f"{server}/missing", {"a": "#a"}, breakpoint=1280)
    defects = check_snapshot(snapshot, breakpoint=1280)
    assert [d.kind for d in defects] == ["page_not_delivered"]


@needs_browser
def test_deriving_the_inspection_set_refuses_an_undelivered_page(server):
    """`derive_candidates` is the other half. A 404 derives a few candidates
    from the error page, every one of which passes every check."""
    from vise.engines.render_harness import PageNotDelivered
    from vise.engines.ui_contract import derive_candidates

    found, _ = derive_candidates(f"{server}/", breakpoint=1280)
    assert found, "the served page derives candidates"

    with pytest.raises(PageNotDelivered) as raised:
        derive_candidates(f"{server}/missing", breakpoint=1280)
    assert "404" in str(raised.value)


@needs_browser
def test_the_layout_gate_fails_on_a_404_and_names_it(server, tmp_path):
    """The behaviour that matters: a gate whose whole claim is that a pass
    means something was checked."""
    from dataclasses import dataclass

    from vise.engines.validators import UiLayoutValidator

    (tmp_path / ".vise").mkdir()
    (tmp_path / ".vise" / "quality.yaml").write_text(
        f"design:\n  targets: ['{server}/missing']\n  breakpoints: [1280]\n",
        encoding="utf-8",
    )

    @dataclass
    class _Goal:
        project_dir: str

    rec = UiLayoutValidator().run(_Goal(project_dir=str(tmp_path)))
    assert not rec.passed and rec.outcome == "failed"
    assert "404" in rec.evidence
    assert "raised" not in rec.evidence, (
        "a served error page is not vise's bug — an evidence line reading "
        "'ui_layout raised' sends the reader to the wrong repository"
    )
