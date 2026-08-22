"""Derives ``render_harness.extract()``'s selector list instead of requiring it.

``layoutlint`` (the tool ``ui_checks``/``ui_contrast`` are adapted from) makes a
human hand-author ``components: [{id, selector}]`` before it can run at all —
fine for a person driving the tool, unusable for an unattended gate, which has
nobody to ask. This module renders the page once and walks the live DOM to
build that list itself: every interactive control, every block of text, every
layout container a geometry/contrast check would want to inspect.

The one property everything here is built to preserve: ``render_harness``'s
injected extractor resolves each requested selector with
``document.querySelector`` — first match only. A selector like ``.card`` on a
page with eight cards silently inspects one and drops seven, and the gate
would report the page clean. Every ``Candidate.selector`` this module emits is
therefore verified in-page with
``document.querySelectorAll(selector).length === 1`` before it is returned;
anything that fails that check is dropped and counted in ``skipped_reasons``
instead of silently shipped.

Playwright is imported lazily, the same way ``render_harness`` does it, so
importing this module never requires it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from vise.engines.render_harness import BrowserUnavailable, browser_status

_URL_RE = re.compile(r"^(https?|file)://", re.IGNORECASE)

# ponytail: render_harness exposes no "run this JS in a page" hook, only
# extract()/extract_breakpoints() which are shaped around a fixed selector
# extractor. Re-launching the browser here (mirroring render_harness's own
# launch code) is the smallest way to reuse its availability contract
# (browser_status/BrowserUnavailable) without adding an exec hook nobody else
# needs yet. Add one to render_harness if a third caller needs raw page access.

_INTERACTIVE = "interactive"
_TEXT = "text"
_CONTAINER = "container"

# ---- The injected candidate-deriver -----------------------------------------#
# Runs in the page. Classifies every element into interactive/text/container
# (or drops it), caps at `limit` keeping interactive first then container
# then text, and for each kept element builds a selector verified with
# querySelectorAll(...).length === 1 before it is ever handed back — the
# uniqueness property render_harness's first-match extractor depends on.
_DERIVE_JS = r"""
(args) => {
  const { limit } = args;
  const INTERACTIVE_SEL = "a[href], button, input, select, textarea, summary, " +
    "[role=button], [role=link], [role=switch], [role=tab], [tabindex]:not([tabindex='-1'])";
  const CONTAINER_SEL = "main, header, nav, footer, aside, section, article, [role=main]";
  const TEXT_TAGS = new Set(["H1","H2","H3","H4","H5","H6","P","LI","LABEL","TD","TH","FIGCAPTION","BLOCKQUOTE"]);
  const SKIP_ANCESTOR_SEL = "script, style, template, svg";

  const matches = (el, sel) => {
    try { return el.matches(sel); } catch (e) { return false; }
  };

  const isHidden = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden") return true;
    if (parseFloat(cs.opacity) === 0) return true;
    const r = el.getBoundingClientRect();
    return r.width <= 0 || r.height <= 0;
  };

  const hasOwnText = (el) => {
    for (const node of el.childNodes) {
      if (node.nodeType === 3 && node.textContent.trim()) return true;
    }
    return false;
  };

  const classify = (el) => {
    if (matches(el, INTERACTIVE_SEL)) return "interactive";
    if (matches(el, CONTAINER_SEL)) return "container";
    const cs = getComputedStyle(el);
    if (el.children.length >= 2) {
      const overflow = cs.overflowX === cs.overflowY ? (cs.overflow || cs.overflowX) : cs.overflowX;
      if (overflow && overflow !== "visible") return "container";
    }
    // A taken-out-of-flow box is a collision candidate whether or not it holds
    // text or children. Requiring text or 2+ children missed exactly the class
    // of element the geometry checks exist for: an empty positioned div that
    // lands on top of its sibling. Found by a real render — a page of two
    // absolutely-positioned empty divs overlapping by 40px produced ZERO
    // candidates and the gate reported clean.
    if (cs.position === "absolute" || cs.position === "fixed" || cs.position === "sticky") {
      return "container";
    }
    const tag = el.tagName;
    if (TEXT_TAGS.has(tag)) return "text";
    if ((tag === "SPAN" || tag === "DIV") && hasOwnText(el)) return "text";
    return null;
  };

  const all = document.body ? Array.from(document.body.querySelectorAll("*")) : [];
  const picked = [];
  for (const el of all) {
    if (el.closest(SKIP_ANCESTOR_SEL)) continue;
    if (isHidden(el)) continue;
    const role = classify(el);
    if (!role) continue;
    picked.push({ el, role });
  }

  const priority = { interactive: 0, container: 1, text: 2 };
  const ordered = picked
    .map((p, idx) => ({ el: p.el, role: p.role, idx }))
    .sort((a, b) => (priority[a.role] - priority[b.role]) || (a.idx - b.idx));

  const skipped = [];
  let kept = ordered;
  if (ordered.length > limit) {
    skipped.push(
      `limit: dropped ${ordered.length - limit} of ${ordered.length} candidates over limit=${limit}`
    );
    kept = ordered.slice(0, limit);
  }

  const idCount = (id) => document.querySelectorAll("#" + CSS.escape(id)).length;

  // Walks up from `el` to the nearest ancestor with a page-unique id (or to
  // :root), recording an {tag, nth-among-parent's-children} step per level.
  const pathFrom = (el) => {
    const steps = [];
    let node = el;
    let anchorSelector = ":root";
    while (node && node !== document.documentElement) {
      const parent = node.parentElement;
      if (!parent) break;
      if (node.id && idCount(node.id) === 1) {
        anchorSelector = "#" + CSS.escape(node.id);
        break;
      }
      const siblings = Array.from(parent.children);
      steps.unshift({ tag: node.tagName.toLowerCase(), nth: siblings.indexOf(node) + 1 });
      node = parent;
    }
    return { steps, anchorSelector };
  };

  const candidates = [];
  let uniqueFailures = 0;
  for (const { el, role } of kept) {
    let readableId = null;
    let selector = null;

    if (el.id && idCount(el.id) === 1) {
      readableId = "#" + el.id;
      selector = "#" + CSS.escape(el.id);
    } else {
      for (const attr of ["data-testid", "data-component"]) {
        const v = el.getAttribute(attr);
        if (!v) continue;
        const cand = `[${attr}="${CSS.escape(v)}"]`;
        if (document.querySelectorAll(cand).length === 1) {
          readableId = cand;
          selector = cand;
          break;
        }
      }
    }

    if (!selector) {
      const { steps, anchorSelector } = pathFrom(el);
      const readablePath = steps.map((s) => `${s.tag}:${s.nth}`).join(">");
      readableId = anchorSelector === ":root"
        ? (readablePath || "html")
        : anchorSelector + (readablePath ? ">" + readablePath : "");
      const cssPath = steps.map((s) => `${s.tag}:nth-child(${s.nth})`).join(" > ");
      selector = cssPath ? `${anchorSelector} > ${cssPath}` : anchorSelector;
    }

    if (document.querySelectorAll(selector).length !== 1) {
      uniqueFailures += 1;
      continue;
    }

    candidates.push({ id: readableId, selector, tag: el.tagName.toLowerCase(), role });
  }

  if (uniqueFailures > 0) {
    skipped.push(`uniqueness: dropped ${uniqueFailures} candidates with no page-unique selector`);
  }

  return { candidates, skipped };
}
"""


def _is_url(target: str) -> bool:
    return bool(_URL_RE.match(target.strip()))


def _require_browser() -> None:
    # `reason` is already the whole remedy — browser_status() ran it through
    # render_harness._unavailable_message(), which names the interpreter and
    # both install steps. Appending another "Full setup: ..." here duplicated
    # it verbatim; every render_harness caller hits this same trap, which is
    # why the fix belongs in one place rather than four (root-cause, not
    # patched per call site).
    available, reason = browser_status()
    if not available:
        raise BrowserUnavailable(reason)


@dataclass(frozen=True, slots=True)
class Candidate:
    """One element chosen for inspection, with a selector proven unique in-page."""

    id: str  # stable, human-readable: "main>section:2>button:1" or the element's own id
    selector: str  # resolves to exactly ONE element
    tag: str
    role: str  # "interactive" | "text" | "container" — why it was picked


def derive_candidates(
    target: str,
    *,
    breakpoint: int = 1280,
    height: int = 900,
    limit: int = 400,
    timeout_ms: int = 15000,
) -> tuple[list[Candidate], list[str]]:
    """Render ``target`` and derive the inspection set.

    ``target`` follows the harness convention: a ``http(s)://``/``file://`` URL
    is navigated, anything else is treated as inline HTML.

    Returns ``(candidates, skipped_reasons)``. Raises ``BrowserUnavailable``
    when Playwright/Chromium is missing — never returns an empty set to paper
    over an unavailable browser. Any other in-page failure (bad selector CSS,
    a detached node) is caught by the extractor itself and surfaces as a
    ``skipped_reasons`` entry, not an exception.
    """
    _require_browser()

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": int(breakpoint), "height": int(height)})
            try:
                if _is_url(target):
                    page.goto(target, wait_until="networkidle", timeout=timeout_ms)
                else:
                    page.set_content(target, wait_until="networkidle", timeout=timeout_ms)
                page.wait_for_timeout(50)  # let layout settle, same as render_harness
                result: dict[str, Any] = page.evaluate(_DERIVE_JS, {"limit": int(limit)})
            finally:
                page.close()
        finally:
            browser.close()

    candidates = [
        Candidate(id=item["id"], selector=item["selector"], tag=item["tag"], role=item["role"])
        for item in result["candidates"]
    ]
    return candidates, list(result["skipped"])


def as_selectors_by_id(candidates: list[Candidate]) -> dict[str, str]:
    """Shape ``derive_candidates``' output for ``render_harness.extract()``."""
    return {c.id: c.selector for c in candidates}
