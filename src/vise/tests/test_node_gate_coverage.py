"""No node goes mechanically unchecked by accident.

Measured across the bundled workflows when this was written: **46 of 49 edges
were `phrase` edges** (94%), and only 14 of 54 nodes declared validators (26%).
Translated: most of vise's phase gating was the agent asserting it had done
something, not a check that it had. vise presents itself as an enforcer; at
that ratio it was a discipline assistant.

The fix is not validators everywhere. Many phases are genuinely cognitive —
`understand`, `hypothesize`, `triage` — and hanging a fake check on one is
worse than having none: it teaches people to export
`VISE_NODE_GATE_OVERRIDE=1`, which is the habit the gates exist to prevent.

The criterion that was applied, and that this test holds:

    A node whose own SIGNAL already asserts something mechanical has to
    check it. A node that does not has to say why, here.

`debug:reproduce` was the clearest case: the node asks in prose for *"Confirm
that at least one test fails"* and there was no way to express that, so the
strongest gate in the whole debug workflow was a phrase. The `tests_fail`
validator came from there.

What this test protects is not the number. It is that the next node added
without validators is a decision rather than an oversight.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

WORKFLOWS = pathlib.Path(__file__).resolve().parents[1] / "assets" / "workflows"


# ---------------------------------------------------------------------------
# Nodes deliberately left without a mechanical check, each with its reason.
#
# Adding an entry here is admitting the node closes on the agent's judgement.
# That is a legitimate answer; what is not legitimate is not answering.
# ---------------------------------------------------------------------------

_COGNITIVE = "a reading-and-judgement phase: produces no artifact a machine can read"
_PROSE = "the artifact is prose for humans; its quality is not checkable by exit code"
_EXTERNAL = "the effect lives outside the repo (GitHub, the network), not in the working tree"
_PROJECT = (
    "checkable in principle, but only against project conventions vise does not know"
)
_RESEARCH = (
    "the artifact is a claim about the world, not a working tree: there is no "
    "suite to run and no exit code to consult. This workflow's real check is "
    "the citation reopened in `verify`, and no validator in the registry can "
    "confirm that anyone opened it"
)

_NO_INDEX = (
    "the artifact is a list of candidates that came out of livespec, another "
    "MCP server: vise cannot call it and therefore cannot check what it "
    "returned. This workflow's real check is `tests_pass` in `move`, which is "
    "the only thing that can say whether the move preserved behaviour"
)

UNVERIFIED_BY_DESIGN: dict[tuple[str, str], str] = {
    # --- decouple ------------------------------------------------------------
    ("decouple", "survey"): _NO_INDEX,
    ("decouple", "triage"): (
        "the decision is mechanical, but `vise.runtime.decouple.triage` takes "
        "it inside the session rather than a validator from the registry: the "
        "node writes nothing a gate could read out of the working tree"
    ),
    ("decouple", "report"): _PROSE,
    # --- research ------------------------------------------------------------
    # vise's gates are built for repositories. This is the first workflow whose
    # product is an answer rather than a diff, and saying so here is more
    # honest than inventing a validator that pretends to check it.
    ("research", "scope"): _RESEARCH,
    ("research", "gather"): _RESEARCH,
    ("research", "verify"): _RESEARCH,
    ("research", "contradict"): _RESEARCH,
    ("research", "synthesize"): _PROSE,
    # --- debug ---------------------------------------------------------------
    ("debug", "understand"): _COGNITIVE,
    ("debug", "classify"): _COGNITIVE,
    ("debug", "analyze"): _COGNITIVE,
    ("debug", "hypothesize"): _COGNITIVE,
    ("debug", "strategy-tests"): _COGNITIVE,
    ("debug", "strategy-flowtrace"): (
        "the node produces artifacts — profiler reports, and a flowtrace trace "
        "when that server is in the session — but neither exists in a repo "
        "that never opted into the tool. `trace_captured` ships commented in "
        "the file itself, with the reason, the way `diff_scope` does in "
        "decouple: a gate that fails closed on an artifact nobody produces "
        "blocks every other repo"
    ),
    ("debug", "strategy-hybrid"): _COGNITIVE,
    ("debug", "unreproducible"): (
        "this is the exit for the case where there is NO reproduction; "
        "requiring a green check would require the opposite of what the node "
        "means"
    ),
    ("debug", "report"): _PROSE,
    # --- dogfood -------------------------------------------------------------
    ("dogfood", "run-on-self"): _COGNITIVE,
    ("dogfood", "capture-issues"): _COGNITIVE,
    ("dogfood", "triage"): _COGNITIVE,
    ("dogfood", "file"): _EXTERNAL,
    # --- feature-dev ---------------------------------------------------------
    ("feature-dev", "orient"): _COGNITIVE,
    ("feature-dev", "design"): _COGNITIVE,
    ("feature-dev", "commit"): (
        "the commit already went through `validate`, which does check; running "
        "the suite again here only adds latency to a node that does not change "
        "the tree"
    ),
    # --- migration -----------------------------------------------------------
    ("migration", "design"): _COGNITIVE,
    ("migration", "apply"): (
        "applying the migration runs against a real system (a database, a "
        "service); vise cannot tell an application failure from an absent "
        "environment, and being wrong here blocks a workflow halfway through"
    ),
    # --- pr-review -----------------------------------------------------------
    ("pr-review", "fetch"): _EXTERNAL,
    ("pr-review", "analyze"): _COGNITIVE,
    ("pr-review", "comment"): _EXTERNAL,
    # --- quality-gate --------------------------------------------------------
    ("quality-gate", "deep-passes"): (
        "the four phases before it already check mechanically; this node is the "
        "deepening loop over what they reported"
    ),
    # --- release -------------------------------------------------------------
    ("release", "changelog"): _PROSE,
    ("release", "version-bump"): _PROJECT,
    ("release", "tag"): _PROJECT,
    ("release", "notify"): _EXTERNAL,
    # --- security-audit ------------------------------------------------------
    ("security-audit", "scan"): (
        "deliberately ungated: a scanner exiting non-zero here means it FOUND "
        "something, which is the node's expected result. The node says so in "
        "its own comment"
    ),
    ("security-audit", "triage"): _COGNITIVE,
    ("security-audit", "doc"): _PROSE,
    # --- sprint-e2e ----------------------------------------------------------
    ("sprint-e2e", "orient"): _COGNITIVE,
    ("sprint-e2e", "contract"): _PROJECT,
    ("sprint-e2e", "close"): _PROSE,
    # --- ui-feature ----------------------------------------------------------
    ("ui-feature", "orient"): _COGNITIVE,
    # `flow` and `look` both close on "the brief exists", which is mechanical —
    # and uncheckable, because the brief goes wherever this repo keeps design
    # decisions: the change proposal, a DESIGN.md, a UX.md. A `file_exists`
    # gate would have to name one of them and would fail closed on every repo
    # that chose another.
    ("ui-feature", "flow"): _PROJECT,
    ("ui-feature", "look"): _PROJECT,
}


def _graphs() -> list[tuple[str, dict]]:
    return [
        (p.stem.removesuffix("-graph"), yaml.safe_load(p.read_text(encoding="utf-8")))
        for p in sorted(WORKFLOWS.glob("*-graph.yaml"))
    ]


def _all_nodes() -> list[tuple[str, str, dict]]:
    return [(wf, n["id"], n) for wf, g in _graphs() for n in (g.get("nodes") or [])]


# ---------------------------------------------------------------------------
# the invariant
# ---------------------------------------------------------------------------

def test_every_node_either_verifies_or_says_why_not():
    """The whole rule, in one assertion."""
    silent = [
        (wf, nid)
        for wf, nid, n in _all_nodes()
        if not n.get("validators") and (wf, nid) not in UNVERIFIED_BY_DESIGN
    ]
    assert not silent, (
        "these nodes check nothing and do not declare why:\n  "
        + "\n  ".join(f"{wf}:{nid}" for wf, nid in silent)
        + "\n\nAdd validators, or an UNVERIFIED_BY_DESIGN entry with the reason."
    )


def test_the_exemption_list_has_no_stale_entries():
    """A renamed node, or one that gained validators, leaves litter here."""
    real = {(wf, nid) for wf, nid, _ in _all_nodes()}
    verified = {(wf, nid) for wf, nid, n in _all_nodes() if n.get("validators")}

    ghosts = sorted(k for k in UNVERIFIED_BY_DESIGN if k not in real)
    assert not ghosts, f"exemptions for nodes that no longer exist: {ghosts}"

    redundant = sorted(k for k in UNVERIFIED_BY_DESIGN if k in verified)
    assert not redundant, (
        f"exemptions for nodes that DO check — delete the excuse: {redundant}"
    )


def test_every_exemption_states_a_reason():
    empty = sorted(k for k, v in UNVERIFIED_BY_DESIGN.items() if not (v or "").strip())
    assert not empty, f"exemptions with no reason: {empty}"


# ---------------------------------------------------------------------------
# the ratchet — so the ratio cannot quietly go backwards
# ---------------------------------------------------------------------------

# Raise it when the real number rises; never lower it to make a change pass.
# Same ratchet as the coverage floor in CLAUDE.md.
MIN_VERIFIED_NODES = 24


def test_the_number_of_mechanically_gated_nodes_does_not_regress():
    verified = [1 for _wf, _nid, n in _all_nodes() if n.get("validators")]
    assert len(verified) >= MIN_VERIFIED_NODES, (
        f"{len(verified)} nodes check mechanically, the floor is "
        f"{MIN_VERIFIED_NODES} — validators were taken off a node"
    )


# ---------------------------------------------------------------------------
# a declared validator has to actually exist
# ---------------------------------------------------------------------------

def test_every_declared_validator_type_is_in_the_registry():
    """A typo'd type is not a lax node: `build_validators` fails closed."""
    from vise.engines.validators import _REGISTRY

    unknown = sorted({
        v.get("type") or v.get("name")
        for _wf, _nid, n in _all_nodes()
        for v in (n.get("validators") or [])
        if (v.get("type") or v.get("name")) not in _REGISTRY
    })
    assert not unknown, f"validator types that do not exist: {unknown}"


@pytest.mark.parametrize("wf,nid", [
    ("debug", "reproduce"),
    ("debug", "verify"),
    ("sprint-e2e", "e2e"),
    ("migration", "reversibility-check"),
    ("security-audit", "fix-criticals"),
    # The node this workflow exists for. Folded into `build` it is the work
    # that gets cut the moment the happy path is demoable.
    ("ui-feature", "states"),
])
def test_the_nodes_whose_signal_is_a_mechanical_claim_check_it(wf: str, nid: str):
    """The concrete cases that motivated the change, pinned one by one."""
    node = next(n for w, i, n in _all_nodes() if (w, i) == (wf, nid))
    assert node.get("validators"), f"{wf}:{nid} asserts something mechanical and does not check it"


def test_reproduce_checks_that_a_test_actually_fails():
    """`tests_pass` here would be exactly the opposite of what the node asks."""
    node = next(n for w, i, n in _all_nodes() if (w, i) == ("debug", "reproduce"))
    types = {v.get("type") for v in node["validators"]}
    assert "tests_fail" in types
    assert "tests_pass" not in types
