"""Pure geometry checks over a ``render_harness.extract()`` snapshot.

No browser, no Playwright, no third-party imports — every function here is
plain arithmetic on the dicts ``render_harness`` produces. Adapted from
``layoutlint``'s ``checks/`` package, with one structural difference: that
implementation checks a hand-declared containment *contract* (agent says
"this is a child of that"); this one has no contract, so containment and
container-overflow are inferred from each node's ``children`` list — the
DOM-descendant ids the extractor already resolved. Alignment/misalignment is
opt-in in the reference and needs that same hand-written contract we
deliberately do not have here, so it is not implemented.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Any

Rect = dict[str, float]

_CLIP_OVERFLOWS = {"hidden", "clip", "scroll", "auto"}


@dataclass(frozen=True)
class Defect:
    kind: str
    a: str
    b: str | None
    delta_px: float
    breakpoint: int | None
    severity: str
    detail: str


# --------------------------------------------------------------------------- #
# Rect primitives — mirrors layoutlint's geometry.py, trimmed to what's used.
# --------------------------------------------------------------------------- #
def _edges(rect: Rect) -> tuple[float, float, float, float]:
    x = float(rect["x"])
    y = float(rect["y"])
    w = float(rect["width"])
    h = float(rect["height"])
    return x, y, x + w, y + h


def _valid_rect(rect: Any) -> bool:
    if not isinstance(rect, dict):
        return False
    try:
        for key in ("x", "y", "width", "height"):
            float(rect[key])
    except (KeyError, TypeError, ValueError):
        return False
    return True


def _content_box(node: dict) -> Rect | None:
    cr = node.get("content_rect")
    if _valid_rect(cr):
        return {k: float(cr[k]) for k in ("x", "y", "width", "height")}
    return node.get("rect") if _valid_rect(node.get("rect")) else None


def _overflow_amount(child: Rect, parent: Rect, tol: float) -> float:
    pl, pt, pr, pb = _edges(parent)
    cl, ct, cr, cb = _edges(child)
    spill_left = max(0.0, (pl - tol) - cl)
    spill_top = max(0.0, (pt - tol) - ct)
    spill_right = max(0.0, cr - (pr + tol))
    spill_bottom = max(0.0, cb - (pb + tol))
    return max(spill_left, spill_top, spill_right, spill_bottom)


def _union_rect(rects: list[Rect]) -> Rect:
    lefts, tops, rights, bottoms = zip(*(_edges(r) for r in rects), strict=True)
    return {
        "x": min(lefts),
        "y": min(tops),
        "width": max(rights) - min(lefts),
        "height": max(bottoms) - min(tops),
    }


def _intersection(a: Rect, b: Rect) -> Rect | None:
    al, at, ar, ab = _edges(a)
    bl, bt, br, bb = _edges(b)
    ix, iy = max(al, bl), max(at, bt)
    ir, ib = min(ar, br), min(ab, bb)
    if ir <= ix or ib <= iy:
        return None
    return {"x": ix, "y": iy, "width": ir - ix, "height": ib - iy}


def _overflow_style(node: dict) -> str:
    styles = node.get("styles")
    if not isinstance(styles, dict):
        return "visible"
    value = styles.get("overflow")
    return value if isinstance(value, str) else "visible"


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #
def _valid_nodes(snapshot: dict) -> dict[str, dict]:
    """id -> node, dropping anything without a usable id/rect. Never raises."""
    nodes = snapshot.get("nodes")
    if not isinstance(nodes, list):
        return {}
    out: dict[str, dict] = {}
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str) or not _valid_rect(node.get("rect")):
            continue
        out[node_id] = node
    return out


def _check_containment(
    nodes: dict[str, dict], breakpoint: int | None, tol: float
) -> list[Defect]:
    out: list[Defect] = []
    for parent_id, parent in nodes.items():
        children = parent.get("children")
        if not isinstance(children, list):
            continue
        parent_content = _content_box(parent)
        if parent_content is None:
            continue
        parent_overflow = _overflow_style(parent)
        for child_id in children:
            child = nodes.get(child_id)
            if child is None:
                continue
            delta = _overflow_amount(child["rect"], parent_content, tol)
            if delta <= 0.0:
                continue
            if parent_overflow in _CLIP_OVERFLOWS:
                kind, severity = "internal_clip", "error"
            else:
                kind, severity = "containment_overflow", "warn"
            out.append(
                Defect(
                    kind=kind,
                    a=child_id,
                    b=parent_id,
                    delta_px=round(delta, 4),
                    breakpoint=breakpoint,
                    severity=severity,
                    detail=(
                        f"{child_id} spills {delta:.1f}px past {parent_id}'s content box "
                        f"(overflow: {parent_overflow})"
                    ),
                )
            )
    return out


def _related(a_id: str, b_id: str, nodes: dict[str, dict]) -> bool:
    a_children = nodes.get(a_id, {}).get("children") or []
    b_children = nodes.get(b_id, {}).get("children") or []
    return b_id in a_children or a_id in b_children


def _check_collision(nodes: dict[str, dict], breakpoint: int | None, tol: float) -> list[Defect]:
    out: list[Defect] = []
    for a_id, b_id in combinations(nodes, 2):
        if _related(a_id, b_id, nodes):
            continue
        a_rect, b_rect = nodes[a_id]["rect"], nodes[b_id]["rect"]
        inter = _intersection(a_rect, b_rect)
        if inter is None:
            continue
        # tolerance: shrink the overlap requirement so edge-touching isn't a collision.
        if min(inter["width"], inter["height"]) <= tol:
            continue
        delta = min(inter["width"], inter["height"])
        out.append(
            Defect(
                kind="external_collision",
                a=a_id,
                b=b_id,
                delta_px=round(delta, 4),
                breakpoint=breakpoint,
                severity="error",
                detail=f"{a_id} and {b_id} overlap by {delta:.1f}px and are not related",
            )
        )
    return out


def _check_offpage(
    nodes: dict[str, dict], document: Any, breakpoint: int | None, tol: float
) -> list[Defect]:
    if not isinstance(document, dict):
        return []
    try:
        width = float(document.get("scrollWidth", document.get("width", 0)))
        height = float(document.get("scrollHeight", document.get("height", 0)))
    except (TypeError, ValueError):
        return []
    if width <= 0 or height <= 0:
        return []
    bounds = {"x": 0.0, "y": 0.0, "width": width, "height": height}

    out: list[Defect] = []
    for node_id, node in nodes.items():
        delta = _overflow_amount(node["rect"], bounds, tol)
        if delta <= 0.0:
            continue
        out.append(
            Defect(
                kind="offpage",
                a=node_id,
                b=None,
                delta_px=round(delta, 4),
                breakpoint=breakpoint,
                severity="error",
                detail=f"{node_id} falls {delta:.1f}px outside the document bounds",
            )
        )
    return out


def _check_container_overflow(
    nodes: dict[str, dict], breakpoint: int | None, tol: float
) -> list[Defect]:
    out: list[Defect] = []
    for node_id, node in nodes.items():
        overflow = _overflow_style(node)
        if overflow in _CLIP_OVERFLOWS:
            continue
        children = node.get("children")
        if not isinstance(children, list):
            continue
        child_rects = [nodes[cid]["rect"] for cid in children if cid in nodes]
        if not child_rects:
            continue
        content = _content_box(node)
        if content is None:
            continue
        union = _union_rect(child_rects)
        delta = _overflow_amount(union, content, tol)
        if delta <= 0.0:
            continue
        out.append(
            Defect(
                kind="container_overflow",
                a=node_id,
                b=None,
                delta_px=round(delta, 4),
                breakpoint=breakpoint,
                severity="error",
                detail=(
                    f"{node_id}'s {len(child_rects)} children exceed its content box "
                    f"by {delta:.1f}px"
                ),
            )
        )
    return out


def _check_unresolved(snapshot: dict, breakpoint: int | None) -> list[Defect]:
    unresolved = snapshot.get("unresolved")
    if not isinstance(unresolved, list):
        return []
    out: list[Defect] = []
    for node_id in unresolved:
        if not isinstance(node_id, str):
            continue
        out.append(
            Defect(
                kind="unresolved_selector",
                a=node_id,
                b=None,
                delta_px=0.0,
                breakpoint=breakpoint,
                severity="error",
                detail=f"selector for {node_id!r} matched nothing",
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def check_snapshot(
    snapshot: dict, *, breakpoint: int | None = None, tolerance_px: float = 1.0
) -> list[Defect]:
    """Run every check against one harness snapshot. Never raises.

    A malformed node (missing ``rect``, non-numeric coordinates) is skipped
    rather than aborting the whole scan.
    """
    if not isinstance(snapshot, dict):
        return []
    nodes = _valid_nodes(snapshot)
    out: list[Defect] = []
    out.extend(_check_containment(nodes, breakpoint, tolerance_px))
    out.extend(_check_collision(nodes, breakpoint, tolerance_px))
    out.extend(_check_offpage(nodes, snapshot.get("document"), breakpoint, tolerance_px))
    out.extend(_check_container_overflow(nodes, breakpoint, tolerance_px))
    out.extend(_check_unresolved(snapshot, breakpoint))
    return out


def check_breakpoints(
    snapshots: dict[int, dict], *, tolerance_px: float = 1.0
) -> list[Defect]:
    """Run :func:`check_snapshot` per breakpoint and dedupe across them.

    The same defect recurring at several widths is one problem: identical
    ``(kind, a, b)`` collapses into the entry with the largest ``delta_px``,
    keeping that entry's breakpoint, with the count folded into ``detail``.
    """
    best: dict[tuple[str, str, str | None], tuple[Defect, int]] = {}
    for bp, snapshot in snapshots.items():
        for defect in check_snapshot(snapshot, breakpoint=bp, tolerance_px=tolerance_px):
            key = (defect.kind, defect.a, defect.b)
            prior = best.get(key)
            if prior is None or defect.delta_px > prior[0].delta_px:
                best[key] = (defect, prior[1] + 1 if prior else 1)
            else:
                best[key] = (prior[0], prior[1] + 1)

    out: list[Defect] = []
    for defect, count in best.values():
        if count > 1:
            defect = Defect(
                kind=defect.kind,
                a=defect.a,
                b=defect.b,
                delta_px=defect.delta_px,
                breakpoint=defect.breakpoint,
                severity=defect.severity,
                detail=f"{defect.detail} (seen at {count} breakpoints)",
            )
        out.append(defect)
    return out
