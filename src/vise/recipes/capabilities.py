"""Capability registry — closed taxonomy for cross-MCP recipe resolution.

Capabilities are short dotted strings that describe what a tool *does*,
not which MCP it lives in.  Recipe YAML references capabilities; the
resolver maps them to concrete (mcp_name, tool_name) pairs at runtime.

Extension namespace: any capability starting with ``x.`` is accepted
without error (only a warning is emitted at load time).
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Closed v1 taxonomy
# ---------------------------------------------------------------------------
CAPABILITIES: frozenset[str] = frozenset({
    # web
    "web.fetch",
    "web.scrape",
    "web.screenshot",
    "web.search",
    # validate
    "validate.web.layout",
    # Real-browser, at-rest flicker check for poll-driven render
    # architectures (pub/sub store, no VDOM/diffing, timer-driven
    # setState). The bound tool installs a MutationObserver on the region
    # root and asserts 0 childList mutations over a quiet window with
    # stable node identity — a typecheck and a green unit suite say nothing
    # about flicker. Ships UNBOUND on purpose (GAP-first): the intended
    # binding is the `watcher` MCP (eval_js + get_tree), wired per project
    # via capability_set. It is deliberately absent from INTERNAL_BINDINGS,
    # so capability_audit surfaces validate.ui.no-flicker as a GAP until
    # bound. See rules/lazy/poll-driven-render.md for the contract.
    "validate.ui.no-flicker",
    # Live end-to-end integration check for features crossing a system
    # boundary (3rd-party API, CRM, webhook, external DB, payment). The
    # bound tool fires a REAL request at the DEPLOYED entrypoint, then
    # asserts the side-effect record actually exists in the target system.
    # Bound globally to the internal stdlib e2e-runner (see INTERNAL_BINDINGS
    # below): the engine is dependency-free and project-agnostic, so it is
    # bound for every project out of the box. Project-specificity lives in
    # the recipe/validator INPUTS (which endpoint, which record to assert),
    # supplied at use time — not in the binding.
    # See rules/lazy/third-party-integration-gate.md for the contract.
    "validate.integration.e2e",
    # deploy
    "deploy.create",
    "deploy.update",
    "deploy.rollback",
    "deploy.tail_logs",
    "deploy.status",
    # db
    "db.query.read",
    "db.query.write",
    "db.migrate",
    "db.schema.inspect",
    # fs
    "fs.read",
    "fs.write",
    "fs.search",
    # code
    "code.format",
    "code.lint",
    "code.test.run",
    "code.review",
    "code.security.findings",
    # vcs
    "vcs.diff",
    "vcs.commit",
    "vcs.pr.create",
    "vcs.pr.comment",
    # notify
    "notify.email",
    "notify.chat",
    "notify.webhook",
    # ai
    "ai.summarize",
    "ai.classify",
    "ai.embed",
    # meta (vise-internal)
    "meta.list",
    "meta.health",
    "meta.record_experience",
    "meta.assert",
    # Notion drift check — detects stale notions by comparing produced_at to
    # source-file mtimes (git log -1 fallback: fs mtime). Bound internally;
    # NOT a GAP. Ships as a read-only L1-compatible capability.
    "meta.notion_drift",
    # workflow (vise-internal)
    "workflow.traverse",
    "workflow.status",
})

# ---------------------------------------------------------------------------
# Vise-internal capability bindings
# These map capability names to (mcp_name, tool_name) for tools that are
# built into vise and do not live in external proxies.
# ---------------------------------------------------------------------------
INTERNAL_BINDINGS: dict[str, tuple[str, str]] = {
    "meta.record_experience": ("experience", "experience_record"),
    "meta.list": ("vise", "proxy_list"),
    "meta.health": ("vise", "vise_version"),
    "meta.notion_drift": ("notion", "notion_drift_check"),
    "workflow.traverse": ("graph", "graph_traverse"),
    "workflow.status": ("graph", "graph_status"),
    "code.security.findings": ("dcc", "cube_get_findings"),
    "validate.integration.e2e": ("e2e", "e2e_check"),
}


# ---------------------------------------------------------------------------
# Effect classification for autonomy tiers (B)
# ---------------------------------------------------------------------------
# Each capability is tagged with its effect class:
#   read       — pure reads, no external side effects
#   write      — local mutations (filesystem, code, vise-internal state)
#   sideeffect — external/irreversible actions (VCS commits, deploys, DB writes,
#                notifications to external systems)
#
# Unknown capabilities default to "sideeffect" (most restrictive) at check time.
# notify.* is classified sideeffect here; the L1 tier grants them a carve-out
# for reporting purposes (see tiers.py).
CAPABILITY_EFFECT: dict[str, str] = {
    # web — read
    "web.fetch":              "read",
    "web.scrape":             "read",
    "web.screenshot":         "read",
    "web.search":             "read",
    # validate — read (verification, no mutations)
    "validate.web.layout":    "read",
    "validate.ui.no-flicker": "read",
    "validate.integration.e2e": "read",
    # deploy
    "deploy.create":          "sideeffect",
    "deploy.update":          "sideeffect",
    "deploy.rollback":        "sideeffect",
    "deploy.tail_logs":       "read",
    "deploy.status":          "read",
    # db
    "db.query.read":          "read",
    "db.query.write":         "sideeffect",
    "db.migrate":             "sideeffect",
    "db.schema.inspect":      "read",
    # fs
    "fs.read":                "read",
    "fs.write":               "write",
    "fs.search":              "read",
    # code
    "code.format":            "write",
    "code.lint":              "read",
    "code.test.run":          "read",
    "code.review":            "read",
    "code.security.findings": "read",
    # vcs
    "vcs.diff":               "read",
    "vcs.commit":             "sideeffect",
    "vcs.pr.create":          "sideeffect",
    "vcs.pr.comment":         "sideeffect",
    # notify — sideeffect (external); L1 grants a carve-out for reporting
    "notify.email":           "sideeffect",
    "notify.chat":            "sideeffect",
    "notify.webhook":         "sideeffect",
    # ai — read (inference; no external state mutation)
    "ai.summarize":           "read",
    "ai.classify":            "read",
    "ai.embed":               "read",
    # meta (vise-internal)
    "meta.list":              "read",
    "meta.health":            "read",
    "meta.record_experience": "write",
    "meta.assert":            "read",
    "meta.notion_drift":      "read",
    # workflow (vise-internal)
    "workflow.traverse":      "write",
    "workflow.status":        "read",
}


def is_known_capability(cap: str) -> bool:
    """Return True if *cap* is a registered capability or a valid x.* extension."""
    if cap in CAPABILITIES:
        return True
    if cap.startswith("x."):
        log.warning("[recipes] unknown extension capability %r — accepted under x.* namespace", cap)
        return True
    return False


def validate_capability(cap: str) -> bool:
    """Validate and log; return False for truly unknown (non-x.*) capabilities."""
    if cap in CAPABILITIES:
        return True
    if cap.startswith("x."):
        log.warning("[recipes] extension capability %r used in recipe", cap)
        return True
    log.error("[recipes] unknown capability %r — not in taxonomy and not an x.* extension", cap)
    return False
