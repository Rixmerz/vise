"""vise insights — what the gates actually did, read back from gates.jsonl.

vise gates other repos on evidence and, until this command, could produce none
about itself. ``node_gate_state`` counts failed attempts per node, but the same
counter advances whether the gate was fixed or bypassed with
``VISE_NODE_GATE_OVERRIDE=1`` — so the one question worth asking about a gate
was the one question the stored state could not answer.

Three things this is for, in order of how much they should change a decision:

  1. **Override rate.** A gate routed around is not a gate. A high rate is a
     bug report about the gate, not about the person setting the variable.
  2. **Verified vs unverified.** A node can pass having run nothing — every
     validator skip-passes when its tool is unconfigured. A workflow whose
     checks are mostly ``unverified`` is ceremony.
  3. **Which workflows are reached at all.** ``sprint-e2e`` shipped unrouted for
     months; nothing but reading the routing table against the directory would
     have caught it.

Reads only. Never writes, never deletes, and an absent log is an empty report
rather than an error — the log is optional by design.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter


def _cmd_insights(args: argparse.Namespace) -> int:
    from vise.engines.telemetry import read_events

    events = read_events(limit=getattr(args, "limit", None))

    blocked = [e for e in events if e.get("kind") == "node_gate_blocked"]
    overridden = [e for e in events if e.get("kind") == "node_gate_overridden"]
    outcomes = [e for e in events if e.get("kind") == "validator_outcome"]
    activations = [e for e in events if e.get("kind") == "workflow_activated"]

    gate_hits = len(blocked) + len(overridden)
    by_outcome = Counter(e.get("outcome") or "unknown" for e in outcomes)
    checked = sum(by_outcome.values())

    report = {
        "events_read": len(events),
        "workflows": {
            "activations": len(activations),
            "by_graph": dict(Counter(e.get("graph") for e in activations).most_common()),
        },
        "gates": {
            "red": gate_hits,
            "blocked": len(blocked),
            "overridden": len(overridden),
            # The headline number. None of the stored graph state can produce it.
            "override_rate": round(len(overridden) / gate_hits, 3) if gate_hits else None,
            "most_overridden_nodes": dict(
                Counter(e.get("node") for e in overridden).most_common(5)
            ),
            "most_blocked_nodes": dict(
                Counter(e.get("node") for e in blocked).most_common(5)
            ),
        },
        "validators": {
            "runs": checked,
            "by_outcome": dict(by_outcome),
            # "passed" is not the same claim as "verified". This ratio is the
            # difference, and it is the one that says whether a gate means
            # anything on this machine.
            "verified_rate": (
                round(by_outcome.get("verified", 0) / checked, 3) if checked else None
            ),
            "never_verified": sorted({
                e.get("validator") for e in outcomes
                if e.get("outcome") and e.get("outcome") != "verified"
            } - {
                e.get("validator") for e in outcomes if e.get("outcome") == "verified"
            }),
        },
    }

    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
        return 0

    if not events:
        print("no gate events recorded yet — run a workflow first")
        print("(log: $VISE_TELEMETRY_DIR/gates.jsonl, or ~/.local/share/vise/telemetry/)")
        return 0

    w = report["workflows"]
    g = report["gates"]
    v = report["validators"]

    print(f"events read: {report['events_read']}")
    print()
    print(f"workflows activated: {w['activations']}")
    for name, n in w["by_graph"].items():
        print(f"  {name or '?':<18} {n}")
    print()
    print(f"red gates: {g['red']}  (blocked {g['blocked']}, overridden {g['overridden']})")
    if g["override_rate"] is not None:
        print(f"  override rate: {g['override_rate']:.0%}"
              f"{'   <-- a gate routed around is not a gate' if g['override_rate'] > 0.2 else ''}")
    for node, n in g["most_overridden_nodes"].items():
        print(f"  overridden at {node or '?':<16} {n}")
    print()
    print(f"validator runs: {v['runs']}")
    for outcome, n in sorted(v["by_outcome"].items()):
        print(f"  {outcome:<12} {n}")
    if v["verified_rate"] is not None:
        print(f"  verified rate: {v['verified_rate']:.0%}"
              f"{'   <-- mostly ceremony' if v['verified_rate'] < 0.5 else ''}")
    if v["never_verified"]:
        print(f"  never once verified: {', '.join(v['never_verified'])}")
    return 0


def add_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("insights", help="what the gates actually did")
    p.add_argument("--limit", type=int, default=None,
                   help="read only the last N events (default: all)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.set_defaults(func=_cmd_insights)
