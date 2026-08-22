"""``screenshot()`` (render_harness) and the ``vise shot`` CLI wired to it.

Two of these tests need a real browser to prove a real PNG comes out; the
other three prove the refusal paths (bad target, missing browser, CLI exit
code) and never need one, so they run unconditionally the way
``test_design_gates.py`` proves the fail-closed contract with no browser at
all.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from vise.engines.render_harness import BrowserUnavailable, browser_status, screenshot

_available, _reason = browser_status()
requires_browser = pytest.mark.skipif(not _available, reason=_reason)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

_HTML = "<html><body style='margin:0'><h1>hi</h1></body></html>"


def _png_width(path: Path) -> int:
    """Read the IHDR width field (big-endian u32 at byte offset 16)."""
    data = path.read_bytes()
    return struct.unpack(">I", data[16:20])[0]


@requires_browser
def test_screenshot_writes_a_real_png_for_a_file_target(tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text(_HTML, encoding="utf-8")
    out = tmp_path / "shots" / "out.png"

    result = screenshot(f"file://{page}", out)

    assert result.exists()
    assert result.read_bytes().startswith(_PNG_MAGIC)


@requires_browser
def test_screenshot_width_is_honoured(tmp_path: Path) -> None:
    page = tmp_path / "page.html"
    page.write_text(_HTML, encoding="utf-8")
    narrow = tmp_path / "narrow.png"
    wide = tmp_path / "wide.png"

    screenshot(f"file://{page}", narrow, width=375, full_page=False)
    screenshot(f"file://{page}", wide, width=1280, full_page=False)

    assert _png_width(narrow) == 375
    assert _png_width(wide) == 1280


def test_a_non_url_target_raises_value_error_and_launches_no_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(
        "vise.engines.render_harness.browser_status",
        lambda: calls.append(1) or (True, "ok"),
    )
    out = tmp_path / "out.png"

    with pytest.raises(ValueError, match="http://, https://, or file://"):
        screenshot("<script>not a url</script>", out)

    assert not calls, "target validation must reject before any browser check runs"
    assert not out.exists()


def test_missing_browser_raises_and_leaves_no_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reason = (
        "playwright is not installed; run: pip install 'vise[design]'. "
        "Full setup: pip install 'vise[design]' && playwright install chromium"
    )
    monkeypatch.setattr(
        "vise.engines.render_harness.browser_status", lambda: (False, reason)
    )
    out = tmp_path / "out.png"

    with pytest.raises(BrowserUnavailable) as exc_info:
        screenshot("https://example.com", out)

    assert "pip install 'vise[design]'" in str(exc_info.value)
    assert "playwright install chromium" in str(exc_info.value)
    assert not out.exists()


def test_cli_exits_nonzero_on_a_refused_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from vise.cli.main import main

    rc = main(["shot", "not-a-url", "--out", str(tmp_path / "out.png")])

    assert rc != 0
    assert not (tmp_path / "out.png").exists()


def test_cli_exits_nonzero_when_browser_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vise.cli.main import main

    monkeypatch.setattr(
        "vise.engines.render_harness.browser_status",
        lambda: (False, "playwright is not installed; run: pip install 'vise[design]'"),
    )

    rc = main(["shot", "https://example.com", "--out", str(tmp_path / "out.png")])

    assert rc != 0
    assert not (tmp_path / "out.png").exists()


@requires_browser
def test_cli_main_writes_a_real_png_on_success(tmp_path: Path) -> None:
    from vise.cli.main import main

    page = tmp_path / "page.html"
    page.write_text(_HTML, encoding="utf-8")
    out = tmp_path / "shots" / "out.png"

    rc = main(["shot", f"file://{page}", "--out", str(out)])

    assert rc == 0
    assert out.exists()
    assert out.read_bytes().startswith(_PNG_MAGIC)


def test_failed_capture_removes_a_stale_file_at_out_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reason = (
        "playwright is not installed; run: pip install 'vise[design]'. "
        "Full setup: pip install 'vise[design]' && playwright install chromium"
    )
    monkeypatch.setattr(
        "vise.engines.render_harness.browser_status", lambda: (False, reason)
    )
    out = tmp_path / "out.png"
    out.write_bytes(b"stale png bytes from a previous capture")

    with pytest.raises(BrowserUnavailable):
        screenshot("https://example.com", out)

    assert not out.exists()


@requires_browser
def test_failed_network_capture_removes_a_stale_file_at_out_path(
    tmp_path: Path,
) -> None:
    from playwright.sync_api import Error as PlaywrightError

    out = tmp_path / "out.png"
    out.write_bytes(b"stale png bytes from a previous capture")

    with pytest.raises(PlaywrightError):
        screenshot("http://127.0.0.1:1/", out, timeout_ms=1000)

    assert not out.exists()


@requires_browser
def test_failed_capture_leaves_no_empty_directory_for_a_nested_out_path(
    tmp_path: Path,
) -> None:
    from playwright.sync_api import Error as PlaywrightError

    out = tmp_path / "does" / "not" / "exist" / "out.png"

    with pytest.raises(PlaywrightError):
        screenshot("http://127.0.0.1:1/", out, timeout_ms=1000)

    assert not out.exists()
    assert not out.parent.exists()


def test_the_real_unavailable_message_names_both_install_steps() -> None:
    """The remedy string the code actually produces, not one a test wrote.

    Every other unavailable-path test monkeypatches ``browser_status`` and then
    asserts against the string it just injected — which proves the caller
    propagates a message, never that the message is complete. That left the
    "names both install steps" guarantee untested against the real producer:
    ``_unavailable_message`` could drop the ``playwright install chromium``
    half and the whole suite would stay green while users hit a dead end one
    install short of a working browser.
    """
    from vise.engines.render_harness import _unavailable_message

    message = _unavailable_message("playwright is not installed")

    assert "pip install 'vise[design]'" in message
    assert "playwright install chromium" in message


def test_a_symlink_destination_is_refused_and_its_target_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--out`` pointing at a symlink must not reach the file it points to.

    ``Path.resolve()`` follows the final component, and this function both
    writes to that path and unlinks it on failure — so a committed
    ``shots/out.png -> ~/.ssh/id_rsa`` was written through on success and
    DELETED on a capture that merely failed to load a page. Reproduced before
    the fix: the symlink survived and its target's contents were gone. CWE-59,
    the same link-following class already fixed once in
    ``design_tokens._within_project``.
    """
    secret = tmp_path / "secret.txt"
    secret.write_text("do not clobber me")
    link = tmp_path / "out.png"
    link.symlink_to(secret)

    called: list[str] = []
    monkeypatch.setattr(
        "vise.engines.render_harness.browser_status",
        lambda: (called.append("probed"), (True, "chromium is available"))[1],
    )

    with pytest.raises(ValueError, match="symlink"):
        screenshot("https://example.com", link)

    assert secret.read_text() == "do not clobber me"
    assert link.is_symlink()
    assert not called, "the refusal must happen before any browser work"
