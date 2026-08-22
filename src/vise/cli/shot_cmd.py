"""`vise shot` — save a screenshot of a rendered URL, for a quick visual check."""

from __future__ import annotations

import argparse
import sys


def _cmd_shot(args: argparse.Namespace) -> int:
    from vise.engines.render_harness import BrowserUnavailable, screenshot

    try:
        out = screenshot(
            args.target,
            args.out,
            width=args.width,
            height=args.height,
            full_page=not args.viewport,
        )
    except (BrowserUnavailable, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(str(out))
    return 0


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("shot", help="save a screenshot of a rendered URL")
    p.add_argument("target", help="http(s):// or file:// URL to render")
    p.add_argument("--out", required=True, help="path to write the PNG to")
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=800)
    p.add_argument(
        "--viewport",
        action="store_true",
        help="capture only the viewport instead of the full scrollable page",
    )
    p.set_defaults(func=_cmd_shot)
