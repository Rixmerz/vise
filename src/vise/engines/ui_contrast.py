"""Contrast checks over the geometry snapshot ``render_harness.extract`` returns.

Pure maths over dicts — no browser, no third-party imports. Consumes
``styles["color"]`` against ``styles["effective_background"]`` (the harness
already walked the ancestor chain for the latter) and flags a WCAG 2.2 AA
violation: normal text under 4.5:1, large text under 3.0:1.

``font-size``/``font-weight`` are not in ``render_harness.DEFAULT_STYLE_PROPS``
— the caller must request them via ``REQUIRED_STYLE_PROPS`` when calling
``extract(..., extra_style_props=list(REQUIRED_STYLE_PROPS))``, or every node
falls back to the strict 4.5:1 threshold (failing safe, never lenient).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

REQUIRED_STYLE_PROPS: tuple[str, ...] = ("font-size", "font-weight")

# ponytail: only the CSS keywords likely to show up as a computed background/
# color fallback (getComputedStyle never returns "red" etc, but user-authored
# inline HTML fed straight to extract() can). Not the full CSS named-color
# table — add entries if a real snapshot needs one.
_NAMED_COLORS: dict[str, tuple[float, float, float, float]] = {
    "white": (255.0, 255.0, 255.0, 1.0),
    "black": (0.0, 0.0, 0.0, 1.0),
    "transparent": (0.0, 0.0, 0.0, 0.0),
    "red": (255.0, 0.0, 0.0, 1.0),
    "green": (0.0, 128.0, 0.0, 1.0),
    "blue": (0.0, 0.0, 255.0, 1.0),
    "gray": (128.0, 128.0, 128.0, 1.0),
    "grey": (128.0, 128.0, 128.0, 1.0),
}

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_RGB_RE = re.compile(
    r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)"
)


@dataclass(frozen=True, slots=True)
class ContrastFinding:
    """A control whose foreground fails WCAG AA against its effective background."""

    node_id: str
    state: str
    ratio: float
    required: float
    foreground: str
    background: str
    background_from: str
    detail: str


def _parse_hex(value: str) -> tuple[float, float, float, float] | None:
    digits = value[1:]
    if len(digits) in (3, 4):
        digits = "".join(ch * 2 for ch in digits)
    if len(digits) == 6:
        digits += "ff"
    if len(digits) != 8:
        return None
    try:
        r, g, b, a = (int(digits[i : i + 2], 16) for i in (0, 2, 4, 6))
    except ValueError:
        return None
    return float(r), float(g), float(b), a / 255.0


def parse_color(value: str) -> tuple[float, float, float, float] | None:
    """``rgb()``/``rgba()``/``#hex``/named-ish -> ``(r, g, b, a)``, ``None`` if unparseable."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None

    hex_match = _HEX_RE.match(text)
    if hex_match:
        return _parse_hex(text)

    rgb_match = _RGB_RE.search(text)
    if rgb_match:
        try:
            r, g, b = (float(rgb_match.group(i)) for i in (1, 2, 3))
        except ValueError:
            return None
        a = float(rgb_match.group(4)) if rgb_match.group(4) is not None else 1.0
        return r, g, b, a

    return _NAMED_COLORS.get(text.lower())


def relative_luminance(rgb: tuple[float, float, float]) -> float:
    """WCAG 2.x relative luminance for an sRGB triple in 0..255."""

    def linearize(channel: float) -> float:
        c = channel / 255.0
        if c <= 0.03928:
            return c / 12.92
        return ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linearize(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _composite(rgb: tuple[float, float, float], alpha: float, over: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(alpha * c + (1.0 - alpha) * o for c, o in zip(rgb, over, strict=True))  # type: ignore[return-value]


def contrast_ratio(fg: str, bg: str) -> float | None:
    """WCAG contrast ratio of ``fg`` over ``bg``, compositing alpha as needed. ``None`` if unparseable."""
    fg_parsed = parse_color(fg)
    bg_parsed = parse_color(bg)
    if fg_parsed is None or bg_parsed is None:
        return None

    fr, fgc, fb, fa = fg_parsed
    br, bgc, bb, ba = bg_parsed

    bg_rgb: tuple[float, float, float] = (br, bgc, bb)
    if ba < 1.0:
        # An effective_background that is itself translucent has nothing
        # behind it in this snapshot — approximate by compositing over white.
        bg_rgb = _composite(bg_rgb, ba, (255.0, 255.0, 255.0))

    fg_rgb: tuple[float, float, float] = (fr, fgc, fb)
    if fa < 1.0:
        fg_rgb = _composite(fg_rgb, fa, bg_rgb)

    l_fg = relative_luminance(fg_rgb)
    l_bg = relative_luminance(bg_rgb)
    lighter, darker = max(l_fg, l_bg), min(l_fg, l_bg)
    return (lighter + 0.05) / (darker + 0.05)


def _is_bold(font_weight: str | float | None) -> bool:
    if font_weight is None:
        return False
    text = str(font_weight).strip().lower()
    if text in ("bold", "bolder"):
        return True
    try:
        return float(text) >= 700
    except ValueError:
        return False


def required_ratio(font_size_px: float | None, font_weight: str | float | None) -> float:
    """4.5:1, relaxed to 3.0:1 for WCAG 2.2 AA "large text". Strict when size is unknown."""
    if font_size_px is None:
        return 4.5
    threshold = 18.66 if _is_bold(font_weight) else 24.0
    return 3.0 if font_size_px >= threshold else 4.5


def _parse_px(value: object) -> float | None:
    if value is None:
        return None
    match = re.match(r"^\s*([\d.]+)", str(value))
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _flatten_stack(stack: list) -> str | None:
    """Composite translucent background layers, topmost first, onto the last.

    ``stack[0]`` is the element's own background and ``stack[-1]`` is the first
    opaque thing under it. Returns an opaque ``rgb(...)`` string, or ``None``
    when any layer is unparseable.
    """
    parsed = [parse_color(layer) for layer in stack if isinstance(layer, str)]
    if not parsed or any(layer is None for layer in parsed):
        return None
    base = parsed[-1][:3]
    for layer in reversed(parsed[:-1]):
        base = _composite(layer[:3], layer[3], base)
    r, g, b = (round(channel) for channel in base)
    return f"rgb({r}, {g}, {b})"


def check_nodes(nodes: list[dict], *, state: str = "default") -> list[ContrastFinding]:
    """Flag every node whose foreground fails WCAG AA against its effective background.

    Never raises: an unparseable colour, a missing key, or a zero-area rect
    just skips that node — this runs inside a quality gate that must not go
    down over one malformed snapshot entry.
    """
    findings: list[ContrastFinding] = []
    for node in nodes:
        # ponytail: broad catch is deliberate — the harness's node shape is
        # not validated on the way in, and one bad node must not take the
        # rest of the gate down with it.
        try:
            finding = _check_one(node, state)
        except Exception:  # noqa: BLE001
            continue
        if finding is not None:
            findings.append(finding)
    return findings


def _check_one(node: dict, state: str) -> ContrastFinding | None:
    if not isinstance(node, dict):
        return None

    rect = node.get("content_rect") or node.get("rect")
    if not isinstance(rect, dict):
        return None
    if float(rect.get("width", 0)) <= 0 or float(rect.get("height", 0)) <= 0:
        return None

    styles = node.get("styles")
    if not isinstance(styles, dict):
        return None

    # Contrast is a claim about text against its background. `ui_contract`
    # deliberately promotes text-free positioned boxes to candidates — an empty
    # absolute div that lands on top of its sibling is exactly what its geometry
    # checks look for — and the same candidate list is fed here. Judging the
    # inherited `color` of a box that paints no glyph produces a finding about
    # nothing. Absent signal keeps the old behaviour: only a harness that
    # positively reports "no text of its own" gets to skip a node.
    if styles.get("has_own_text") is False:
        return None

    fg = styles.get("color")
    bg = styles.get("effective_background")
    if not isinstance(fg, str) or not isinstance(bg, str):
        return None

    # A gradient or an image behind the text has no single colour, so there is
    # no ratio to compute. Declining to judge is the honest answer; guessing
    # one produced "white on white, 1.0:1" for legible text sitting on a
    # 15%-opaque overlay above a purple gradient — a false positive of exactly
    # the kind that gets a gate switched off.
    # ponytail: no finding rather than a "cannot determine" finding. Add one if
    # anybody ever needs the count of unjudgeable elements.
    if styles.get("background_uncertain"):
        return None

    # The harness returns every translucent layer from the element down to the
    # first opaque one. Flatten them bottom-up so a 15% white over a dark card
    # reads as the near-dark colour a viewer actually sees, not as white.
    stack = styles.get("background_stack")
    if isinstance(stack, list) and len(stack) > 1:
        flattened = _flatten_stack(stack)
        if flattened is not None:
            bg = flattened

    ratio = contrast_ratio(fg, bg)
    if ratio is None:
        return None

    font_size_px = _parse_px(styles.get("font_size"))
    required = required_ratio(font_size_px, styles.get("font_weight"))
    if ratio >= required:
        return None

    bg_from = styles.get("effective_background_from", "")
    detail = (
        f"{fg} on {bg} (from {bg_from or 'default'}) is {round(ratio, 2)}:1, "
        f"needs {required}:1"
    )
    bg_parsed = parse_color(bg)
    if bg_parsed is not None and bg_parsed[3] < 1.0:
        detail += " — effective_background is translucent, composited over white"

    return ContrastFinding(
        node_id=str(node.get("id", "")),
        state=state,
        ratio=round(ratio, 2),
        required=required,
        foreground=fg,
        background=bg,
        background_from=str(bg_from),
        detail=detail,
    )
