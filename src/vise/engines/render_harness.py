"""Playwright adapter — the ONLY browser-dependent module in this package.

Renders a page headless, then runs an injected JS extractor that, per
requested selector, returns ``getBoundingClientRect()`` + a fixed set of
``getComputedStyle`` props + ``naturalWidth``/``Height`` for images, plus the
viewport, document scroll size, and any selector that failed to resolve. The
output is a plain-dict geometry snapshot; downstream check modules consume it
without ever importing Playwright themselves.

Playwright is imported lazily, inside the functions that use it, so this
module (and therefore ``pip install vise``) never requires it — it is an
optional extra (``vise[design]``). ``browser_status()`` lets callers decide
whether a real render is even possible before attempting one.

Vendored and extended from ``layoutlint``'s ``browser.py`` (same author,
MIT-compatible): batches multiple breakpoints under one browser launch,
reports selectors that matched nothing instead of silently dropping them, and
resolves an "effective background" per node for a contrast check.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.sync_api import Browser

# ---- The injected extractor -------------------------------------------------#
# Runs in the page. Receives the {id: selector} map and the list of style
# props. For each requested id, resolves the FIRST matching element and
# builds a node. content_rect is getBoundingClientRect() minus padding+border.
# children = ids of OTHER requested elements that are DOM-descendants.
# unresolved = ids whose selector matched nothing, or threw (invalid CSS) —
# the original layoutlint extractor dropped these silently; that is exactly
# the failure mode a quality gate must not have.
#: The colour helpers both page scripts need, defined once.
#:
#: `_STATE_COLOR_JS` used to carry its own copy of the background walk, and
#: the copy predated the two fixes the original carries — the gradient case
#: and the translucent stack. So a hover state reproduced the exact bug the
#: comments below describe as already fixed: "white on white, 1.0:1" for
#: text that reads fine. Two implementations of one rule is one too many.
_COLOR_JS = r"""
  // Alpha channel of a computed color string ("rgb(...)" / "rgba(...)").
  // A plain rgb() has no alpha component and is fully opaque.
  const alphaOf = (colorStr) => {
    const m = /rgba?\([^)]*,\s*([\d.]+)\s*\)/.exec(colorStr || "");
    return m ? parseFloat(m[1]) : 1;
  };

  // Text this element renders *itself*, not text a descendant renders. The
  // contrast of a wrapper is a question about its child's colours, not its own,
  // and `ui_contract` deliberately promotes empty positioned boxes to
  // candidates for the geometry checks — so without this the contrast check
  // judged the inherited `color` of a div that paints no glyph at all.
  const hasOwnText = (el) => {
    for (const node of el.childNodes) {
      if (node.nodeType === 3 && node.textContent.trim()) return true;
    }
    return false;
  };

  const describe = (el) => {
    if (!el) return "";
    let d = el.tagName;
    if (el.id) {
      d += "#" + el.id;
    } else if (el.className && typeof el.className === "string" && el.className.trim()) {
      d += "." + el.className.trim().split(/\s+/).join(".");
    }
    return d;
  };

  // Walk from the element up for the background a contrast check should
  // compare against. Two things the naive version got wrong, both found by
  // running this against a real page:
  //
  //  * It stopped at the FIRST background with alpha > 0. A 15%-opaque white
  //    over a purple gradient is not white — reporting it as the background
  //    produced "white on white, 1.0:1" for text that reads fine. Translucent
  //    layers are collected top-down and composited by the caller instead.
  //  * A gradient or image background leaves `background-color` transparent,
  //    so the walk sailed past it to whatever was underneath. There is no one
  //    colour behind a gradient, so the honest answer is to say the value is
  //    uncertain and let the check decline to judge, rather than invent a
  //    number.
  const effectiveBackground = (el, elCs) => {
    const stack = [];
    let node = el;
    let uncertain = false;
    let fromEl = null;
    while (node) {
      const cs2 = node === el ? elCs : getComputedStyle(node);
      if (cs2.backgroundImage && cs2.backgroundImage !== "none") {
        uncertain = true;
        fromEl = fromEl || node;
        break;
      }
      const a = alphaOf(cs2.backgroundColor);
      if (a > 0) {
        stack.push(cs2.backgroundColor);
        fromEl = fromEl || node;
        if (a >= 1) break;
      }
      if (node === document.documentElement) break;
      node = node.parentElement;
    }
    let color = stack.length ? stack[stack.length - 1] : null;
    if (color === null || (stack.length && alphaOf(color) < 1)) {
      const body = document.body ? getComputedStyle(document.body) : null;
      const under = body && alphaOf(body.backgroundColor) >= 1
        ? body.backgroundColor : "rgb(255, 255, 255)";
      stack.push(under);
      color = under;
      fromEl = fromEl || document.body;
    }
    return {
      color: stack[0] || color,
      stack,
      uncertain,
      from: fromEl ? describe(fromEl) : "default",
    };
  };

"""


_EXTRACTOR_JS = r"""
(args) => {
  const { selectorsById, styleProps } = args;
  const px = (v) => {
    const n = parseFloat(v);
    return Number.isFinite(n) ? n : 0;
  };

  const elements = {};
  const unresolved = [];
  for (const [id, sel] of Object.entries(selectorsById)) {
    try {
      const el = document.querySelector(sel);
      elements[id] = el;
      if (!el) unresolved.push(id);
    } catch (e) {
      elements[id] = null;
      unresolved.push(id);
    }
  }

  __COLOR_HELPERS__

  const nodes = [];
  for (const [id, sel] of Object.entries(selectorsById)) {
    const el = elements[id];
    if (!el) continue;

    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);

    const padding = {
      top: px(cs.paddingTop), right: px(cs.paddingRight),
      bottom: px(cs.paddingBottom), left: px(cs.paddingLeft),
    };
    const border = {
      top: px(cs.borderTopWidth), right: px(cs.borderRightWidth),
      bottom: px(cs.borderBottomWidth), left: px(cs.borderLeftWidth),
    };
    const margin = {
      top: px(cs.marginTop), right: px(cs.marginRight),
      bottom: px(cs.marginBottom), left: px(cs.marginLeft),
    };

    const contentRect = {
      x: r.x + padding.left + border.left,
      y: r.y + padding.top + border.top,
      width: Math.max(0, r.width - padding.left - padding.right - border.left - border.right),
      height: Math.max(0, r.height - padding.top - padding.bottom - border.top - border.bottom),
    };

    const styles = {
      overflow: cs.overflowX === cs.overflowY ? cs.overflow || cs.overflowX : cs.overflowX,
      object_fit: cs.objectFit,
      box_sizing: cs.boxSizing,
      position: cs.position,
      z_index: cs.zIndex,
      padding, border, margin,
    };
    // Any extra requested style props (this is also how the DEFAULT_STYLE_PROPS
    // additions `color`/`background-color` reach `styles`), snake_cased.
    for (const prop of styleProps) {
      const camel = prop.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
      if (!(prop in styles)) {
        styles[prop.replace(/-/g, "_")] = cs[camel];
      }
    }

    // Effective background: walk from the element up through its ancestors
    // (self first) for the nearest one whose OWN background-color actually
    // paints something (alpha > 0). A transparent background lets whatever
    // sits behind it show through, so it can never be what a contrast check
    // should compare foreground text against. Falls back to <body>, then to
    // opaque white if nothing in the chain paints.
    const bg = effectiveBackground(el, cs);
    styles.effective_background = bg.color;
    styles.effective_background_from = bg.from;
    styles.background_stack = bg.stack;
    styles.background_uncertain = bg.uncertain;
    styles.has_own_text = hasOwnText(el);

    const node = {
      id,
      selector: sel,
      rect: { x: r.x, y: r.y, width: r.width, height: r.height },
      content_rect: contentRect,
      styles,
    };

    if (el.tagName === "IMG") {
      node.natural = { w: el.naturalWidth || 0, h: el.naturalHeight || 0 };
    }

    const kids = [];
    for (const [otherId, otherEl] of Object.entries(elements)) {
      if (otherId === id || !otherEl) continue;
      if (el !== otherEl && el.contains(otherEl)) kids.push(otherId);
    }
    node.children = kids;

    nodes.push(node);
  }

  const de = document.documentElement;
  return {
    nodes,
    viewport: { x: 0, y: 0, width: window.innerWidth, height: window.innerHeight },
    document: {
      width: de.scrollWidth, height: de.scrollHeight,
      scrollWidth: de.scrollWidth, scrollHeight: de.scrollHeight,
    },
    unresolved,
  };
}
"""

# Style props the JS extractor always pulls (camelCase access in-page).
# `color`/`background-color` feed the contrast check alongside effective_background.
DEFAULT_STYLE_PROPS: tuple[str, ...] = (
    "overflow",
    "object-fit",
    "box-sizing",
    "position",
    "z-index",
    "color",
    "background-color",
)

# Named after the interpreter actually running this process (sys.executable),
# not a bare "pip"/"playwright" the reader would run in whatever venv their
# shell happens to have active — which, for a plugin install, is never the
# interpreter that will execute the capture (see render_harness's callers).
_INSTALL_PIP = f"{sys.executable} -m pip install 'vise[design]'"
_INSTALL_CHROMIUM = f"{sys.executable} -m playwright install chromium"
_BOTH_COMMANDS = f"{_INSTALL_PIP} && {_INSTALL_CHROMIUM}"

_URL_RE = re.compile(r"^(https?|file)://", re.IGNORECASE)

# Same allowlist and same CWE-918 rationale as design_profile._TARGET_RE:
# a non-matching string is treated as inline HTML by extract()/set_content,
# so screenshot() must refuse it before any browser launches.
_SCREENSHOT_TARGET_RE = re.compile(r"^(?:https?|file)://\S", re.IGNORECASE)


class BrowserUnavailable(RuntimeError):
    """Raised when Playwright or its Chromium build is missing.

    Always names both install steps, even though ``browser_status()`` can
    tell them apart — a caller acting on this message needs the full recipe,
    not just the half that happens to be missing right now.
    """


def _is_url(target: str) -> bool:
    return bool(_URL_RE.match(target.strip()))


def _unavailable_message(reason: str) -> str:
    return f"{reason} Full setup: {_BOTH_COMMANDS}"


def browser_status() -> tuple[bool, str]:
    """Return ``(available, reason)`` without ever raising.

    Distinguishes Playwright not being importable from Chromium's binary
    being missing, so the reason names the exact command that fixes it.

    Every unavailable reason names the WHOLE remedy, not just the next step.
    This string is what the render validators put in their evidence, and
    ``pip install`` alone leaves the reader with a browser that still is not
    there — one install later they are stuck again with no idea why. An
    unactionable error message is the defect class these gates exist to find,
    so they do not get to ship one themselves.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # narrow-in-effect: any import failure means "unavailable"
        return False, _unavailable_message(f"playwright is not installed; run: {_INSTALL_PIP}")

    try:
        with sync_playwright() as p:
            path = p.chromium.executable_path
            if path and os.path.exists(path):
                return True, "chromium is available"
            return False, _unavailable_message(
                f"chromium is not installed; run: {_INSTALL_CHROMIUM}"
            )
    except Exception as exc:
        return False, f"playwright chromium check failed: {exc}"


def _resolve_style_props(extra_style_props: list[str] | None) -> list[str]:
    style_props = list(DEFAULT_STYLE_PROPS)
    if extra_style_props:
        for prop in extra_style_props:
            if prop not in style_props:
                style_props.append(prop)
    return style_props


def _extract_on_browser(
    browser: Browser,
    target: str,
    selectors_by_id: dict[str, str],
    style_props: list[str],
    breakpoint: int,
    height: int,
    wait_until: str,
    timeout_ms: int,
) -> dict[str, Any]:
    page = browser.new_page(viewport={"width": int(breakpoint), "height": int(height)})
    try:
        if _is_url(target):
            page.goto(target, wait_until=wait_until, timeout=timeout_ms)
        else:
            page.set_content(target, wait_until=wait_until, timeout=timeout_ms)
        # Let layout + image decode settle.
        page.wait_for_timeout(50)
        return page.evaluate(
            _EXTRACTOR_JS,
            {"selectorsById": selectors_by_id, "styleProps": style_props},
        )
    finally:
        page.close()


# Reads ONE element's foreground and effective background after a pseudo-state
# has been forced on it. Deliberately narrow: hovering re-runs layout, so a
# full snapshot per element per state would be N round trips of the expensive
# kind. Contrast only needs two colours.
_STATE_COLOR_JS = r"""
(args) => {
  const { selector, styleProps } = args;
  let el = null;
  try { el = document.querySelector(selector); } catch (e) { el = null; }
  if (!el) return null;
  __COLOR_HELPERS__

  const cs = getComputedStyle(el);
  const r = el.getBoundingClientRect();
  const bg = effectiveBackground(el, cs);
  const styles = {
    color: cs.color,
    background_color: cs.backgroundColor,
    effective_background: bg.color,
    effective_background_from: bg.from,
    background_stack: bg.stack,
    background_uncertain: bg.uncertain,
    has_own_text: hasOwnText(el),
  };
  for (const prop of styleProps) {
    const camel = prop.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    styles[prop.replace(/-/g, "_")] = cs[camel];
  }
  return { rect: { x: r.x, y: r.y, width: r.width, height: r.height }, styles };
}
"""


# One definition, two page scripts. Substituted at import rather than at call
# time so a template that forgets the placeholder fails loudly here.
for _name in ("_EXTRACTOR_JS", "_STATE_COLOR_JS"):
    _src = globals()[_name]
    if "__COLOR_HELPERS__" not in _src:
        raise AssertionError(f"{_name} lost its __COLOR_HELPERS__ placeholder")
    globals()[_name] = _src.replace("__COLOR_HELPERS__", _COLOR_JS.strip())
del _name, _src


INTERACTIVE_STATES: tuple[str, ...] = ("hover", "focus")


def extract_states(
    target: str,
    selectors_by_id: dict[str, str],
    *,
    states: tuple[str, ...] = INTERACTIVE_STATES,
    breakpoint: int = 1280,
    height: int = 900,
    extra_style_props: list[str] | None = None,
    wait_until: str = "networkidle",
    timeout_ms: int = 15000,
) -> dict[str, list[dict[str, Any]]]:
    """Read each element's colours again with ``:hover`` / ``:focus`` applied.

    Returns ``{state: [node, ...]}`` where each node carries ``id``,
    ``selector``, ``rect`` and the same ``styles`` keys the contrast check
    reads. An element that cannot take the state (it moved, it is disabled, it
    is covered) is skipped for that state only — never for the others, and
    never silently for all of them: the caller still sees every state it asked
    for, with the elements that answered.

    A control whose contrast is fine at rest and fails on hover is the case
    this exists for, and it is common: hover styles are written by hand far
    more often than they are checked.
    """
    available, reason = browser_status()
    if not available:
        # `reason` is already the whole remedy — browser_status() ran it
        # through _unavailable_message. Wrapping it again printed the
        # "Full setup:" line twice.
        raise BrowserUnavailable(reason)

    style_props = _resolve_style_props(extra_style_props)
    from playwright.sync_api import sync_playwright

    out: dict[str, list[dict[str, Any]]] = {state: [] for state in states}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(
                viewport={"width": int(breakpoint), "height": int(height)}
            )
            try:
                if _is_url(target):
                    page.goto(target, wait_until=wait_until, timeout=timeout_ms)
                else:
                    page.set_content(target, wait_until=wait_until, timeout=timeout_ms)
                page.wait_for_timeout(50)

                for state in states:
                    for node_id, selector in selectors_by_id.items():
                        try:
                            if state == "hover":
                                page.hover(selector, timeout=1500)
                            elif state == "focus":
                                page.focus(selector, timeout=1500)
                            else:
                                continue
                            page.wait_for_timeout(20)
                            payload = page.evaluate(
                                _STATE_COLOR_JS,
                                {"selector": selector, "styleProps": style_props},
                            )
                        except Exception:  # noqa: BLE001 - one element, one state
                            continue
                        if not payload:
                            continue
                        out[state].append(
                            {"id": node_id, "selector": selector, **payload}
                        )
            finally:
                page.close()
        finally:
            browser.close()
    return out


def extract(
    target: str,
    selectors_by_id: dict[str, str],
    *,
    breakpoint: int = 1280,
    height: int = 900,
    extra_style_props: list[str] | None = None,
    wait_until: str = "networkidle",
    timeout_ms: int = 15000,
) -> dict[str, Any]:
    """Render ``target`` at ``breakpoint`` width and extract geometry.

    ``target`` is a URL (http/https/file) or inline HTML. Returns
    ``{nodes, viewport, document, unresolved}`` — the geometry snapshot the
    checks consume. Raises ``BrowserUnavailable`` if Playwright/Chromium is
    missing; never falls back to an empty/successful-looking result.
    """
    available, reason = browser_status()
    if not available:
        # `reason` is already the whole remedy — browser_status() ran it
        # through _unavailable_message. Wrapping it again printed the
        # "Full setup:" line twice.
        raise BrowserUnavailable(reason)

    from playwright.sync_api import sync_playwright

    style_props = _resolve_style_props(extra_style_props)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            return _extract_on_browser(
                browser, target, selectors_by_id, style_props, breakpoint, height, wait_until, timeout_ms
            )
        finally:
            browser.close()


def extract_breakpoints(
    target: str,
    selectors_by_id: dict[str, str],
    breakpoints: list[int],
    **kwargs: Any,
) -> dict[int, dict[str, Any]]:
    """Render ``target`` once per width in ``breakpoints``, return ``{bp: snapshot}``.

    Launches Chromium exactly once and opens one page per breakpoint — the
    original vendored version launched/closed a whole browser per breakpoint.
    Accepts the same keyword args as ``extract`` (``height``,
    ``extra_style_props``, ``wait_until``, ``timeout_ms``).
    """
    available, reason = browser_status()
    if not available:
        # `reason` is already the whole remedy — browser_status() ran it
        # through _unavailable_message. Wrapping it again printed the
        # "Full setup:" line twice.
        raise BrowserUnavailable(reason)

    from playwright.sync_api import sync_playwright

    extra_style_props = kwargs.pop("extra_style_props", None)
    height = kwargs.pop("height", 900)
    wait_until = kwargs.pop("wait_until", "networkidle")
    timeout_ms = kwargs.pop("timeout_ms", 15000)
    style_props = _resolve_style_props(extra_style_props)

    out: dict[int, dict[str, Any]] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            for bp in breakpoints:
                out[bp] = _extract_on_browser(
                    browser, target, selectors_by_id, style_props, bp, height, wait_until, timeout_ms
                )
        finally:
            browser.close()
    return out


def screenshot(
    target: str,
    out_path: str | Path,
    *,
    width: int = 1280,
    height: int = 800,
    full_page: bool = True,
    wait_until: str = "networkidle",
    timeout_ms: int = 15000,
) -> Path:
    """Render ``target`` and save a PNG to ``out_path``.

    ``target`` MUST be a URL (http/https/file) — unlike ``extract()``, this
    never treats a non-matching string as inline HTML: screenshot() has no
    behavioural need for that path, so it is refused rather than accepted and
    trusted (CWE-918, same allowlist as ``design_profile._TARGET_RE``). The
    refusal happens before any browser launch. Raises ``BrowserUnavailable``
    if Playwright/Chromium is missing.

    Guarantees, not hopes: on any failure (navigation error, missing browser,
    a raised exception mid-capture) ``out_path`` does not exist afterwards.
    A previous capture sitting there is REMOVED rather than left, because the
    caller is told to capture and then read that path: a stale image is a
    plausible lie, while a missing file is an unambiguous signal. No empty
    directory is created on the way there either. The PNG bytes are
    captured into memory first; only once they exist does this function touch
    the filesystem, writing them to a temp file next to ``out_path`` and
    ``Path.replace()``-ing it into place, which is atomic within one
    filesystem. A crash between those two steps leaves the temp file, which is
    cleaned up before the exception propagates.
    """
    if not _SCREENSHOT_TARGET_RE.match(target.strip()):
        raise ValueError(
            f"screenshot target must start with http://, https://, or file:// — got {target!r}"
        )

    # NOT Path.resolve() — that follows a symlink at the final component, and
    # this function both writes to that path and, on failure, unlinks it. A
    # committed `shots/out.png -> ~/.ssh/id_rsa` would be written through and
    # then DELETED by a capture that merely failed to load a page. CWE-59, the
    # same link-following class already fixed once in
    # design_tokens._within_project. The parent is resolved (that is what makes
    # the path absolute and normalises `..`); the final component never is.
    destination = Path(out_path).expanduser()
    resolved = destination.parent.resolve() / destination.name
    if resolved.is_symlink():
        raise ValueError(
            f"refusing to write through a symlink: {resolved} — "
            "point --out at a real path"
        )

    # A failed capture must not leave a PREVIOUS capture at the path the caller
    # is about to read. Leaving it looks like success: the designer is told to
    # capture and then Read the file, so one that misses the exit code reads
    # last week's screenshot and revises its brief against a UI that no longer
    # exists. A missing file is an unambiguous signal; a stale image is a
    # plausible lie, and this repo's thesis is that something which could not
    # do the work must never look like it did.
    #
    # This covers EVERY failure, not just a navigation error. An earlier
    # version raised BrowserUnavailable before this point and wrapped only
    # goto/screenshot, so a missing browser or a failed chromium launch left
    # the stale file sitting there — while the docstring promised otherwise.
    # The danger is identical whichever way the capture failed, so the
    # guarantee has to be too.
    #
    # Losing the old image is the cost, and it is the right one: passing
    # --out <path> is a request to replace what is there.
    stale = resolved.exists()

    try:
        available, reason = browser_status()
        if not available:
            # `reason` is already the whole remedy — browser_status() ran it
            # through _unavailable_message. Wrapping it again printed the
            # "Full setup:" line twice.
            raise BrowserUnavailable(reason)

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(
                    viewport={"width": int(width), "height": int(height)}
                )
                try:
                    page.goto(target, wait_until=wait_until, timeout=timeout_ms)
                    png_bytes = page.screenshot(full_page=full_page)
                finally:
                    page.close()
            finally:
                browser.close()
    except BaseException:
        if stale:
            resolved.unlink(missing_ok=True)
        raise

    # Only now that the capture succeeded do we touch the filesystem — a
    # failure above never gets far enough to create even an empty directory.
    resolved.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=resolved.parent, prefix=f".{resolved.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(png_bytes)
        Path(tmp_name).replace(resolved)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        if stale:
            resolved.unlink(missing_ok=True)
        raise
    return resolved
