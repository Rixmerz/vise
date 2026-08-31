#!/usr/bin/env python3
"""Workflow Auto-Suggest — UserPromptSubmit hook.

Detects multi-step task intent in the user prompt and, when no workflow
is currently active, injects a hint nudging the agent to call
``graph_list_available`` and pick a workflow before diving in.

Quiet by default: emits nothing for trivial prompts, questions, or when
a graph is already active. The hint is advisory — the agent can still
proceed without a workflow.

Protocol:
  stdin:  {"prompt": "...", "hook_event_name": "UserPromptSubmit", ...}
  stdout: optional context block (shown to Claude)
  exit 0: always
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path

from vise.hooks import _xdg

MIN_PROMPT_CHARS = 60


#: What a workflow name may contain to be listed. The stem reaches the user's
#: prompt context verbatim, so this is a syntax boundary, not a style rule.
_SAFE_STEM = re.compile(r"[A-Za-z0-9._-]{1,64}")

#: A repo cannot make the hint arbitrarily long either.
_MAX_LISTED = 40


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()[:12]


def _emit(kind: str, prompt: str, **extra: object) -> None:
    try:
        from vise.engines.telemetry import record_intervention
        record_intervention(kind, _prompt_hash(prompt), dict(extra) if extra else None)
    except Exception:
        pass

INTENT_PATTERNS = re.compile(
    r"\b("
    r"implement|implementa|build|construye|create|crea|design|disena|"
    r"refactor|refactoriza|migrate|migra|port|portea|"
    r"add\s+(feature|endpoint|module|page|component|support)|"
    r"agrega(r)?\s+(feature|endpoint|modulo|pagina|componente|soporte)|"
    r"fix\s+(bug|issue|regression|failing|broken)|"
    r"arregla|debug(ear)?|"
    r"deploy|despliega|integrate|integra|"
    r"set\s*up|setup|scaffold|"
    r"write\s+(tests|integration|e2e)|"
    r"audit|review|revisa|"
    r"optimi[sz]e|optimiza|"
    r"sprint|roadmap|epic|"
    r"plan\s+(out|the|a)|disena(r)?"
    r")\b",
    re.IGNORECASE,
)

MULTI_STEP_HINTS = re.compile(
    r"\b(then|y\s+despues|y\s+luego|after\s+that|next\s+step|first|second|"
    r"step\s+\d|fase|phase|wave|primero|luego|finalmente|finally)\b",
    re.IGNORECASE,
)

QUESTION_PATTERNS = re.compile(
    r"^\s*(why|how|what|when|where|which|que|por\s*que|como|cuando|donde|"
    r"can\s+you\s+explain|explain|explica|tell\s+me)\b",
    re.IGNORECASE,
)


def _state_path() -> Path | None:
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if not project_dir:
        return None
    xdg = _xdg.graph_state_path(project_dir)
    if xdg.exists():
        return xdg
    local = Path(project_dir) / ".claude" / "workflow" / "graph_state.json"
    return local if local.exists() else None


def _has_active_workflow() -> bool:
    """Return True when a graph is active in the state file.

    The state file written by ``graph_state.save_graph_state`` uses the
    key ``active_graph`` (a string with the graph name) — NOT ``active``.
    An earlier version of this check used ``data.get("active", False)``
    which was always False, meaning ANY active mid-traversal graph was
    silently ignored and the hook would clobber it. The correct guard is:

      active_graph set  AND  current_nodes non-empty

    We treat any non-empty ``active_graph`` string as active; checking
    ``current_nodes`` as an additional safeguard covers edge cases where
    the file was written mid-initialization.

    Fail-open: if the state file cannot be read for any reason, return
    False so the hook degrades to suggestion-only rather than blocking.
    """
    p = _state_path()
    if not p:
        return False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        # State file unreadable — fail open: do NOT auto-activate.
        # Destructive actions need positive confirmation of safety.
        return True
    # active_graph is the canonical key (set by initialize_graph_state).
    # Accept legacy keys (graph_name / graph_id / current_node) written
    # by older vise versions or external tooling.
    return bool(
        data.get("active_graph")
        or data.get("graph_id")
        or data.get("graph_name")
        or data.get("current_node")
        or data.get("current_nodes")
    )


def _looks_pasted_doc(prompt: str) -> bool:
    """Skip when prompt is a pasted reference document, not a task ask.

    Pasted runbooks/lineamientos/READMEs contain INTENT_PATTERNS verbs
    (implement, fix, deploy, refactor) as content, not as the user's
    actual request. Heuristic: >=3 markdown headings, OR a heavy
    fenced-code/heading ratio, signals reference material.
    """
    headings = sum(1 for ln in prompt.splitlines() if ln.lstrip().startswith("#"))
    if headings >= 3:
        return True
    if prompt.count("```") >= 4:
        return True
    return False


def _looks_multi_step(prompt: str) -> bool:
    if len(prompt) < MIN_PROMPT_CHARS:
        return False
    if QUESTION_PATTERNS.match(prompt):
        return False
    if _looks_pasted_doc(prompt):
        return False
    if MULTI_STEP_HINTS.search(prompt):
        return True
    return bool(INTENT_PATTERNS.search(prompt))


def _available_workflows(project_dir: str) -> list[str]:
    """Workflow ids across all three scopes, project shadowing user shadowing bundled.

    Ids only — no descriptions. The stems are already legible (`debug-graph`,
    `migration-graph`), and parsing eight YAML headers inside a hook with a 3 s
    timeout buys wording the agent can get from ``graph_list_available`` when the
    choice is not obvious. Fails to an empty list: a suggester that raises would
    cost the user their prompt.
    """
    try:
        from vise.engines.workflow_scope import resolve_workflow_dirs

        seen: list[str] = []
        for _scope, workflows_dir in reversed(resolve_workflow_dirs(project_dir)):
            if not workflows_dir.exists():
                continue
            for path in sorted(workflows_dir.glob("*.yaml")):
                # A filename is chosen by whoever wrote the repository, and this
                # stem is printed verbatim into UserPromptSubmit context on the
                # first prompt after a clone — before any tool call, under a
                # matcher of `*` with no env guard. A name carrying a newline
                # adds lines to that block. Nothing about a workflow needs a
                # character outside this set.
                if not _SAFE_STEM.fullmatch(path.stem):
                    continue
                if path.stem not in seen:
                    seen.append(path.stem)
        return sorted(seen)[:_MAX_LISTED]
    except Exception:
        return []


def main() -> None:
    if os.environ.get("VISE_WORKFLOW_SUGGEST", "1") == "0":
        sys.exit(0)
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    prompt = (payload.get("prompt") or "").strip()
    # Slash commands carry their own orchestration logic — don't reclassify
    # them and risk auto-activating a workflow that fights the command.
    if prompt.startswith("/"):
        sys.exit(0)
    if not prompt or not _looks_multi_step(prompt):
        sys.exit(0)

    if _has_active_workflow():
        sys.exit(0)

    # Picking the workflow is the AGENT's job, not this hook's. An intent tier
    # used to sit here — a regex classifier behind VISE_AUTO_ACTIVATE that would
    # activate a workflow on its own guess. It never worked (the classifier
    # module never shipped), and a regex silently gating the user's tools on a
    # keyword match is worse than the model reading the request. So the hook
    # stops nudging and starts equipping: hand over the real inventory and let
    # the model choose. No env flag, no threshold, no guess.
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", "")
    workflows = _available_workflows(project_dir) if project_dir else []
    inventory = (
        f"Available: {', '.join(workflows)}\n" if workflows
        else "Run `graph_list_available` to see what is installed.\n"
    )

    _emit("workflow_prompt", prompt, available=len(workflows))
    print(
        "## Pick a workflow before implementing\n"
        "Multi-step task detected and no workflow is active. Read the request, "
        "decide which workflow fits, and activate it before writing code — "
        "workflows enforce phase discipline, survive compaction, and gate "
        "transitions on checks that actually run.\n"
        f"{inventory}"
        '  1. `graph_activate(graph_name="<id>")` — start the one that matches.\n'
        "  2. `graph_list_available` — full descriptions if the choice is not obvious.\n"
        "  3. `graph_builder_create` — none fits and this shape of task will recur.\n"
        "If none fits, say so in one line and proceed without one."
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
