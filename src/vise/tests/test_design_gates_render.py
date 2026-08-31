"""The render gates, against a real browser.

Everything else about these gates is provable without one: the geometry maths
in ``test_design_gates.py`` runs over hand-built snapshots, and the fail-closed
contract is a plain function call. What no synthetic fixture can prove is that
the *extractor* reports what the browser actually computed — that the ancestor
walk finds the background a transparent element really inherits, that a
generated selector resolves to exactly one element on a live page, and that
the three modules agree on a snapshot shape none of them owns alone.

These tests skip when Playwright or Chromium is absent, and that is fine — a
skipped test is a test that could not run. A skipped *gate* is the thing this
whole capability exists to outlaw, and ``test_design_gates.py`` pins that
distinction with no browser at all.

Set up with::

    pip install 'vise[design]' && playwright install chromium
"""
from __future__ import annotations

import pytest

from vise.engines.render_harness import browser_status

_available, _reason = browser_status()
pytestmark = pytest.mark.skipif(not _available, reason=_reason)


# A dark container whose children all declare `background: transparent`. The
# only way to judge their contrast is to walk up and find what actually paints.
CONTRAST_PAGE = """<html><body style="margin:0">
<div id="shell" style="background:#2b2b2b;padding:24px">
  <button id="bad" style="color:#555;background:transparent;font-size:14px">Buy</button>
  <button id="good" style="color:#fff;background:transparent;font-size:14px">Cancel</button>
  <h1 id="big" style="color:#8b8b8b;background:transparent;font-size:28px">Heading</h1>
</div></body></html>"""

# Eight siblings sharing one class. `document.querySelector` returns the first
# match, so a class-based selector would collapse these into one inspected node
# and silently drop seven — a gate reporting "clean" over one eighth of a page.
REPEATED_PAGE = """<html><body>
<nav><a href="#a">A</a><a href="#b">B</a><a href="#c">C</a></nav>
<section>{cards}</section>
</body></html>""".format(cards="".join(f'<div class="card">Card {i}</div>' for i in range(8)))

OVERLAP_PAGE = """<html><body style="margin:0;position:relative">
<div id="under" style="position:absolute;left:0;top:0;width:100px;height:100px;background:#eee"></div>
<div id="over"  style="position:absolute;left:60px;top:0;width:100px;height:100px;background:#ccc"></div>
</body></html>"""


def _snapshot(html: str, **kwargs):
    from vise.engines.render_harness import extract
    from vise.engines.ui_contract import as_selectors_by_id, derive_candidates

    candidates, skipped = derive_candidates(html)
    snapshot = extract(html, as_selectors_by_id(candidates), **kwargs)
    return candidates, skipped, snapshot


def test_effective_background_comes_from_the_painting_ancestor() -> None:
    from vise.engines.ui_contrast import REQUIRED_STYLE_PROPS

    _, _, snapshot = _snapshot(
        CONTRAST_PAGE, extra_style_props=list(REQUIRED_STYLE_PROPS)
    )

    backgrounds = {
        node["id"]: node["styles"]["effective_background"] for node in snapshot["nodes"]
    }

    assert backgrounds, "the page has inspectable elements"
    # Every element declared `background: transparent`; none may report it.
    for node_id, background in backgrounds.items():
        assert "rgba(0, 0, 0, 0)" not in background, (
            f"{node_id} reported its own transparent background instead of the "
            "ancestor's — the walk did not run"
        )
        assert background == "rgb(43, 43, 43)", f"{node_id} should inherit #2b2b2b"


def test_a_low_contrast_control_is_found_and_a_good_one_is_not() -> None:
    from vise.engines.ui_contrast import REQUIRED_STYLE_PROPS, check_nodes

    _, _, snapshot = _snapshot(
        CONTRAST_PAGE, extra_style_props=list(REQUIRED_STYLE_PROPS)
    )

    findings = {f.node_id: f for f in check_nodes(snapshot["nodes"])}
    flagged = " ".join(findings)

    assert "bad" in flagged, "#555 on #2b2b2b is 1.9:1 and must be reported"
    assert "good" not in flagged, "white on #2b2b2b passes and must not be reported"


def test_large_text_is_judged_against_the_relaxed_threshold() -> None:
    """The 28px heading measures ~4.15:1 — under AA's 4.5, over large-text's 3.0.

    It is the case that proves the relaxation is load-bearing rather than
    decorative: at 14px this exact colour pair would be a finding.
    """
    from vise.engines.ui_contrast import REQUIRED_STYLE_PROPS, check_nodes, contrast_ratio

    _, _, snapshot = _snapshot(
        CONTRAST_PAGE, extra_style_props=list(REQUIRED_STYLE_PROPS)
    )

    ratio = contrast_ratio("rgb(139, 139, 139)", "rgb(43, 43, 43)")
    findings = {f.node_id for f in check_nodes(snapshot["nodes"])}

    assert ratio is not None and 3.0 < ratio < 4.5, (
        f"the fixture must sit between the two thresholds to prove anything; got {ratio}"
    )
    assert not any("big" in node_id for node_id in findings)


def test_every_one_of_eight_repeated_siblings_is_inspected_separately() -> None:
    candidates, skipped, snapshot = _snapshot(REPEATED_PAGE)

    selectors = [c.selector for c in candidates]

    assert len(selectors) == len(set(selectors)), "a duplicated selector inspects one element twice"
    assert not snapshot["unresolved"], f"selectors that matched nothing: {snapshot['unresolved']}"
    # Eight cards plus three links, at minimum. Containers are extra.
    assert len(snapshot["nodes"]) >= 11, (
        f"only {len(snapshot['nodes'])} nodes — repeated siblings were collapsed"
    )
    assert not any("limit" in s for s in skipped), "this page is far under the cap"


def test_an_unresolved_selector_is_reported_rather_than_dropped() -> None:
    from vise.engines.render_harness import extract
    from vise.engines.ui_checks import check_snapshot

    snapshot = extract(REPEATED_PAGE, {"real": "nav", "ghost": "#nothing-here"})

    assert snapshot["unresolved"] == ["ghost"]
    kinds = {d.kind for d in check_snapshot(snapshot)}
    assert "unresolved_selector" in kinds, (
        "a selector that matched nothing must reach the checks; the tool this was "
        "adapted from dropped them silently"
    )


def test_overlapping_siblings_are_detected_on_a_real_render() -> None:
    from vise.engines.ui_checks import check_snapshot

    _, _, snapshot = _snapshot(OVERLAP_PAGE)

    collisions = [d for d in check_snapshot(snapshot) if d.kind == "external_collision"]

    assert collisions, "two boxes overlapping by 40px must collide"
    assert collisions[0].delta_px == pytest.approx(40.0, abs=1.0)


# Black on white at rest, near-invisible on hover and focus. A control that
# passes when nobody is touching it is the common shape of this defect: hover
# styles get written by hand and checked by eye, if at all.
PSEUDO_STATE_PAGE = """<html><body style="margin:0;background:#fff">
<style>
  #b { color:#000; background:#fff; font-size:14px; border:0 }
  #b:hover { color:#d8d8d8 }
  #b:focus { color:#c0c0c0 }
</style>
<button id="b">Buy</button></body></html>"""


def test_a_control_that_fails_only_on_hover_or_focus_is_found() -> None:
    from vise.engines.render_harness import INTERACTIVE_STATES, extract_states
    from vise.engines.ui_contract import derive_candidates
    from vise.engines.ui_contrast import REQUIRED_STYLE_PROPS, check_nodes

    candidates, _ = derive_candidates(PSEUDO_STATE_PAGE)
    interactive = {c.id: c.selector for c in candidates if c.role == "interactive"}
    assert interactive, "the button must be classified interactive or nothing is hovered"

    by_state = extract_states(
        PSEUDO_STATE_PAGE,
        interactive,
        states=INTERACTIVE_STATES,
        extra_style_props=list(REQUIRED_STYLE_PROPS),
    )

    flagged = {
        state: [f.ratio for f in check_nodes(nodes, state=state)]
        for state, nodes in by_state.items()
    }

    assert flagged.get("hover"), "hover styles were never applied or never measured"
    assert flagged.get("focus"), "focus styles were never applied or never measured"
    assert all(r < 4.5 for ratios in flagged.values() for r in ratios)


def test_the_same_control_passes_at_rest() -> None:
    """Proves the states above are what failed, not the element in general."""
    from vise.engines.ui_contrast import REQUIRED_STYLE_PROPS, check_nodes

    _, _, snapshot = _snapshot(
        PSEUDO_STATE_PAGE, extra_style_props=list(REQUIRED_STYLE_PROPS)
    )

    assert not [f for f in check_nodes(snapshot["nodes"]) if "b" in f.node_id], (
        "black on white is 21:1 — a finding here means the default state is broken too"
    )


# Two navs, each visible only at its own width. Deriving the inspection set at
# one breakpoint means the other nav is `display:none` there and never becomes
# a candidate — so its layout defects are unreachable at every breakpoint, and
# the gate reports clean on a page it never looked at.
BREAKPOINT_PAGE = """<html><head><style>
  #desktop { display: block } #mobile { display: none }
  @media (max-width: 500px) { #desktop { display: none } #mobile { display: block } }
</style></head><body>
<nav id="desktop"><a href="#a">Desktop</a></nav>
<nav id="mobile"><a href="#b">Mobile</a></nav>
</body></html>"""


def test_elements_hidden_at_one_width_are_still_derived_at_another() -> None:
    from vise.engines.ui_contract import derive_candidates

    narrow = {c.id for c in derive_candidates(BREAKPOINT_PAGE, breakpoint=375)[0]}
    wide = {c.id for c in derive_candidates(BREAKPOINT_PAGE, breakpoint=1280)[0]}

    assert any("mobile" in i for i in narrow), "the mobile nav exists at 375px"
    assert not any("mobile" in i for i in wide), "...and is display:none at 1280px"
    assert any("desktop" in i for i in wide)
    assert not any("desktop" in i for i in narrow)

    union = narrow | wide
    assert any("mobile" in i for i in union) and any("desktop" in i for i in union), (
        "a validator deriving at one width only would inspect half this page; "
        "UiLayoutValidator unions across every configured breakpoint for this reason"
    )


# White text on a 15%-opaque white overlay above a dark ground. The overlay is
# the first ancestor with alpha > 0, so a walk that stops there reports the
# background as near-white and the text as white-on-white at 1.0:1 — for text
# that reads perfectly well. Found by running the gate against a real page.
TRANSLUCENT_PAGE = """<html><body style="margin:0;background:#2b2b2b">
<div style="background:rgba(255,255,255,0.15);padding:20px">
  <p id="t" style="color:#fff;font-size:14px;background:transparent">Readable</p>
</div></body></html>"""

# A gradient has no single colour behind the text, so no ratio is knowable.
GRADIENT_PAGE = """<html><body style="margin:0">
<div style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);padding:20px">
  <p id="g" style="color:#fff;font-size:14px;background:transparent">On a gradient</p>
</div></body></html>"""


def test_translucent_layers_are_composited_not_taken_at_face_value() -> None:
    from vise.engines.ui_contrast import REQUIRED_STYLE_PROPS, check_nodes

    _, _, snapshot = _snapshot(
        TRANSLUCENT_PAGE, extra_style_props=list(REQUIRED_STYLE_PROPS)
    )
    node = next(n for n in snapshot["nodes"] if "t" in n["id"])

    stack = node["styles"]["background_stack"]

    assert len(stack) >= 2, f"the overlay and the ground must both be captured; got {stack}"
    assert not check_nodes(snapshot["nodes"]), (
        "white on a 15% overlay above #2b2b2b is legible; flagging it is the "
        "false positive this composite exists to prevent"
    )


def test_a_gradient_background_is_declined_rather_than_guessed() -> None:
    from vise.engines.ui_contrast import REQUIRED_STYLE_PROPS, check_nodes

    _, _, snapshot = _snapshot(
        GRADIENT_PAGE, extra_style_props=list(REQUIRED_STYLE_PROPS)
    )

    uncertain = [n for n in snapshot["nodes"] if n["styles"].get("background_uncertain")]

    assert uncertain, "an element over a gradient must be marked uncertain"
    assert not check_nodes(snapshot["nodes"]), (
        "there is no single colour behind a gradient, so there is no ratio to "
        "report — inventing one is worse than declining"
    )


# --- the state walk is the same walk ------------------------------------

GRADIENT_BUTTON_PAGE = """<html><body style="margin:0">
<div style="background:linear-gradient(135deg,#667eea 0%,#764ba2 100%);padding:20px">
  <button id="cta" style="color:#fff;background:transparent;border:0;font-size:14px">
    Hover me
  </button>
</div></body></html>"""

TRANSLUCENT_BUTTON_PAGE = """<html><body style="margin:0;background:#2b2b2b">
<div style="background:rgba(255,255,255,0.15);padding:20px">
  <button id="cta" style="color:#fff;background:transparent;border:0;font-size:14px">
    Hover me
  </button>
</div></body></html>"""


def _hover_nodes(page: str) -> dict:
    from vise.engines.render_harness import INTERACTIVE_STATES, extract_states
    from vise.engines.ui_contrast import REQUIRED_STYLE_PROPS

    return extract_states(
        page, {"cta": "#cta"},
        states=INTERACTIVE_STATES,
        extra_style_props=list(REQUIRED_STYLE_PROPS),
    )


def test_a_gradient_is_declined_in_hover_state_too() -> None:
    """The state walk carried a stale copy of the default-state walk.

    The copy predated both fixes the original documents — the gradient case and
    the translucent stack — so a hover measurement reproduced verbatim the
    "white on white, 1.0:1" the comments describe as already fixed.
    """
    from vise.engines.ui_contrast import check_nodes

    by_state = _hover_nodes(GRADIENT_BUTTON_PAGE)

    for state, nodes in by_state.items():
        assert nodes, f"{state} produced no measurement at all"
        assert all(n["styles"].get("background_uncertain") for n in nodes), (
            f"{state}: a control over a gradient must be marked uncertain, "
            f"got {[n['styles'].get('effective_background') for n in nodes]}"
        )
        assert not check_nodes(nodes, state=state), (
            f"{state}: there is no single colour behind a gradient to compare "
            f"against, so there is no ratio to report"
        )


def test_a_translucent_layer_is_composited_in_hover_state_too() -> None:
    from vise.engines.ui_contrast import check_nodes

    by_state = _hover_nodes(TRANSLUCENT_BUTTON_PAGE)

    for state, nodes in by_state.items():
        stack = nodes[0]["styles"].get("background_stack")
        assert stack and len(stack) >= 2, (
            f"{state}: the overlay and the ground must both be captured; got {stack}"
        )
        assert not check_nodes(nodes, state=state), (
            f"{state}: white on a 15% overlay above #2b2b2b is legible"
        )


def test_the_state_walk_reports_a_text_signal() -> None:
    """`has_own_text` has to reach the checker from both harness paths."""
    by_state = _hover_nodes(TRANSLUCENT_BUTTON_PAGE)

    for state, nodes in by_state.items():
        assert nodes[0]["styles"]["has_own_text"] is True, state
