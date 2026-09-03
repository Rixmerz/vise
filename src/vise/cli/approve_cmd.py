"""``vise approve`` — consent to the commands a repository's quality profile runs.

See ``vise.core.consent`` for why this exists. The command is deliberately
dull: it prints the exact argv it is approving, because the thing being
consented to is that argv, and a person who has not read it has not consented.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from vise.core import consent


def _cmd_approve(args: argparse.Namespace) -> int:
    from vise.engines.quality_profile import UnboundCheck, list_checks, resolve_check

    project = Path(args.project_dir or ".").expanduser().resolve()
    declared = list_checks(project)
    names = declared if args.all else list(args.checks or [])

    if args.list:
        if not declared:
            print(f"no quality profile with checks under {project}")
            return 0
        for name in declared:
            cmd = resolve_check(project, name)
            if isinstance(cmd, UnboundCheck):
                print(f"  {name:<12} {'unbound':<11} ({cmd.reason.value})")
                continue
            state = consent.approval_state(project, name, cmd)
            print(f"  {name:<12} {state:<11} {' '.join(cmd)}")
        return 0

    if not names:
        print("vise approve: name a check, or pass --all (see --list)")
        return 2

    if args.revoke:
        for name in names:
            gone = consent.revoke(project, name)
            print(f"  {name:<12} {'revoked' if gone else 'was not approved'}")
        return 0

    rc = 0
    for name in names:
        cmd = resolve_check(project, name)
        if isinstance(cmd, UnboundCheck):
            print(f"  {name:<12} cannot approve — {cmd.reason.value} in .vise/quality.yaml")
            rc = 1
            continue
        record = consent.approve(project, name, cmd)
        print(f"  {name:<12} approved {record['digest'][:12]}  {' '.join(cmd)}")
    if rc == 0 and names:
        print(f"\nrecorded in {consent.approvals_path()} for {project}")
    return rc


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "approve",
        help="consent to the commands .vise/quality.yaml runs (by digest, per machine)",
    )
    p.add_argument("checks", nargs="*", help="check names from .vise/quality.yaml")
    p.add_argument("--all", action="store_true", help="every check the profile declares")
    p.add_argument("--list", action="store_true", help="show each check's approval state")
    p.add_argument("--revoke", action="store_true", help="withdraw approval instead")
    p.add_argument("--project-dir", default=None, help="defaults to the cwd")
    p.set_defaults(func=_cmd_approve)
