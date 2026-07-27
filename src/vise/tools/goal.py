"""goal_* tools — durable goal contract + validator surface for the autonomous loop.

The storage contract lives in ``vise.engines.goal_state``.
Validators live in ``vise.engines.validators``.
These tools sit on vise's surface because they are called frequently during
an autonomous session (set once, validate repeatedly, complete/abandon at end).
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from vise.core.session import resolve_project_dir
from vise.engines import goal_state as engine
from vise.engines import validators as val_engine


def _read_settings(settings_path: Path) -> dict:
    """Read .claude/settings.json, returning empty dict if missing or invalid."""
    if not settings_path.exists():
        return {}
    try:
        return json.loads(settings_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_settings_atomic(settings_path: Path, data: dict) -> None:
    """Write settings atomically via tmp + rename."""
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = settings_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(settings_path)


def _synthesize_and_activate_workflow(goal, resolved_dir: str) -> str:
    """Workflow auto-synthesis was removed along with the swarm/workflow-synth
    branch. ``goal_bootstrap`` callers still get a usable result — the goal,
    autonomy toggle, agent deployment, and schedule items all succeed; only
    the synthesized YAML graph is skipped. Existing graphs in
    ``.claude/workflows/`` continue to work via ``graph_activate``.
    """
    return ""


def register_goal(mcp) -> None:

    @mcp.tool()
    def goal_set(
        goal: str,
        project_dir: str | None = None,
        acceptance_criteria: list[str] | None = None,
        target_confidence: float | None = None,
        complexity: str = "unknown",
        validator_configs: list[dict] | None = None,
        preferred_model: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Set the active goal for this project, replacing any prior goal.

        Args:
            goal: High-level objective text. Required.
            project_dir: Project directory. Optional after set_session.
            acceptance_criteria: List of human-readable criteria for success.
            target_confidence: Override auto-derived target (0.0-1.0).
            complexity: simple | medium | complex | unknown (default).
                Determines default target_confidence when not overridden.
            validator_configs: List of validator config dicts. Each must have
                a "type" key (tests_pass, lint_pass, command_exit, files_exist)
                and optional overrides (weight, cmd, paths, etc.).
            preferred_model: Optional Claude Code model spec for this sprint,
                e.g. "opus" or "sonnet-low". Stored on the goal for the
                supervisor's resume prompt — switching the model is the
                agent's call inside Claude Code, not vise's.
            session_id: Optional session id.
        """
        resolved_dir, _ = resolve_project_dir(project_dir, session_id)
        g = engine.set_goal(
            project_dir=resolved_dir,
            goal=goal,
            acceptance_criteria=acceptance_criteria,
            target_confidence=target_confidence,
            complexity=complexity,
            validator_configs=validator_configs,
            preferred_model=preferred_model or "",
        )
        return {
            "success": True,
            "id": g.id,
            "goal": g.goal,
            "target_confidence": g.target_confidence,
            "complexity": g.complexity,
            "status": g.status,
            "started_at": g.started_at,
            "project_dir": g.project_dir,
            "preferred_model": g.preferred_model,
        }

    @mcp.tool()
    def goal_get(
        project_dir: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Return the current goal for this project.

        Returns ``{"found": False}`` when no goal is set.
        """
        resolved_dir, _ = resolve_project_dir(project_dir, session_id)
        g = engine.get_goal(resolved_dir)
        if g is None:
            return {"found": False, "project_dir": resolved_dir}
        return {"found": True, "goal": asdict(g)}

    @mcp.tool()
    def goal_clear(
        project_dir: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Remove the current goal file for this project. Idempotent."""
        resolved_dir, _ = resolve_project_dir(project_dir, session_id)
        removed = engine.clear_goal(resolved_dir)
        return {"success": True, "removed": removed, "project_dir": resolved_dir}

    @mcp.tool()
    def goal_validate(
        project_dir: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Run all configured validators against the active goal.

        Persists results and confidence back to the goal file, appends a
        ``validator_run`` history event, and if ``confidence >= target``
        automatically marks the goal complete.

        Returns confidence score, per-validator results, and whether the
        goal is now complete.
        """
        resolved_dir, _ = resolve_project_dir(project_dir, session_id)
        g = engine.get_goal(resolved_dir)
        if g is None:
            return {"found": False, "project_dir": resolved_dir}

        results, confidence = val_engine.run_validators(g)
        result_dicts = [asdict(r) for r in results]

        engine.update_goal(
            resolved_dir,
            last_results=results,
            confidence=confidence,
        )
        engine.append_history(
            resolved_dir,
            event="validator_run",
            detail=f"confidence={confidence:.3f} target={g.target_confidence:.3f}",
        )
        # Structured numeric series for plateau detection — read by
        # engines.goal_gate.detect_plateau without parsing free text.
        engine.append_history(
            resolved_dir,
            event="confidence_update",
            detail=f"{confidence:.3f}",
        )

        is_complete = confidence >= g.target_confidence
        if is_complete:
            engine.mark_complete(resolved_dir)
            engine.append_history(resolved_dir, event="complete",
                                  detail=f"confidence={confidence:.3f} >= target={g.target_confidence:.3f}")

        return {
            "found": True,
            "confidence": confidence,
            "target": g.target_confidence,
            "complete": is_complete,
            "results": result_dicts,
            "project_dir": resolved_dir,
        }

    @mcp.tool()
    def goal_complete(
        project_dir: str | None = None,
        session_id: str | None = None,
        force: bool = False,
    ) -> dict:
        """Mark the active goal as complete.

        Self-grading guard: refuses to complete unless at least one mechanical
        validator has passed (a ``ValidatorRecord`` with ``passed`` and
        ``source == "mechanical"`` in the goal's ``last_results``). Run
        ``goal_validate`` first to populate that. Pass ``force=true`` to
        complete anyway — this is audited in the goal history.

        Args:
            project_dir: Project directory. Optional after set_session.
            session_id: Optional session id.
            force: Complete even with no mechanical validator pass (audited).
        """
        resolved_dir, _ = resolve_project_dir(project_dir, session_id)
        g = engine.get_goal(resolved_dir)
        if g is None:
            return {"found": False, "project_dir": resolved_dir}

        has_mechanical_pass = any(
            r.passed and getattr(r, "source", "mechanical") == "mechanical"
            for r in g.last_results
        )

        if not has_mechanical_pass and not force:
            return {
                "success": False,
                "reason": "no mechanical validator has passed; run goal_validate first (or pass force=true)",
                "project_dir": resolved_dir,
            }

        completed = engine.mark_complete(resolved_dir)
        if completed is None:
            return {"found": False, "project_dir": resolved_dir}
        if has_mechanical_pass:
            engine.append_history(resolved_dir, event="complete", detail="human-confirmed")
        else:
            engine.append_history(resolved_dir, event="complete",
                                  detail="force-complete (no mechanical pass)")
        return {"success": True, "status": completed.status, "project_dir": resolved_dir}

    @mcp.tool()
    def goal_abandon(
        project_dir: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Mark the active goal as abandoned."""
        resolved_dir, _ = resolve_project_dir(project_dir, session_id)
        g = engine.mark_abandoned(resolved_dir)
        if g is None:
            return {"found": False, "project_dir": resolved_dir}
        engine.append_history(resolved_dir, event="abandoned", detail="human-abandoned")
        return {"success": True, "status": g.status, "project_dir": resolved_dir}

    @mcp.tool()
    def goal_bootstrap(
        goal: str,
        complexity: str = "unknown",
        target_confidence: float | None = None,
        acceptance_criteria: list[str] | None = None,
        validator_configs: list[dict] | None = None,
        preferred_model: str = "",
        enable_autonomy: bool = True,
        synthesize_workflow: bool = True,
        deploy_subagents: bool = True,
        tech_stack: list[str] | None = None,
        project_dir: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        """Atomic bootstrap: set goal, synthesize+activate workflow, enable
        autonomy, and (optionally) queue a restart_claude sequence so a fresh
        Claude session resumes work.

        All side effects happen in this single MCP call so the model cannot
        half-execute the bootstrap.

        Args:
            goal: High-level objective text. Required.
            complexity: simple | medium | complex | unknown (default).
            target_confidence: Override auto-derived target (0.0-1.0).
            acceptance_criteria: List of human-readable criteria for success.
            validator_configs: List of validator config dicts.
            preferred_model: Optional Claude Code model spec, e.g. "opus".
            enable_autonomy: Set VISE_AUTONOMY=1 in .claude/settings.json.
            synthesize_workflow: Synthesize and activate a workflow from the goal.
            deploy_subagents: Gate the restart_claude enqueue (Step 6) — vise
                does not ship an agent-deployment hub, so this never deploys
                agents itself. Kept for callers that pass ``False`` post-restart
                to avoid re-queuing.
            tech_stack: Accepted for API compatibility; unused (no deployment
                step consumes it).
            project_dir: Project directory. Optional after set_session.
            session_id: Optional session id.
        """
        resolved_dir, _ = resolve_project_dir(project_dir, session_id)
        settings_path = Path(resolved_dir) / ".claude" / "settings.json"

        # --- Step 1: Validate settings.json exists ---
        if not settings_path.exists():
            return {
                "ok": False,
                "step": "preflight",
                "error": "no .claude/settings.json — run vise init first",
            }

        # --- Step 1.5: Idempotency guard ---
        # If an active goal with the same text already finished its
        # bootstrap, do NOT mint a fresh UUID and re-overwrite
        # history. This is the protection against /vise-goal being
        # called a second time after /setup-agents already brought
        # the project up. A different goal text always proceeds
        # normally — the guard is text-equality, not just presence.
        existing = engine.get_goal(resolved_dir)
        if (
            existing is not None
            and existing.status == "active"
            and existing.goal == goal
            and existing.bootstrapped
        ):
            return {
                "ok": True,
                "already_bootstrapped": True,
                "goal_id": existing.id,
                "workflow_name": None,
                "autonomy_enabled": True,
                "agents_deployed": [],
                "scheduled_items": [],
                "pane_target": None,
                "next": "goal already active; skipped re-bootstrap",
            }

        # --- Step 2: Set goal ---
        try:
            g = engine.set_goal(
                project_dir=resolved_dir,
                goal=goal,
                acceptance_criteria=acceptance_criteria,
                target_confidence=target_confidence,
                complexity=complexity,
                validator_configs=validator_configs,
                preferred_model=preferred_model,
            )
        except Exception as exc:
            return {"ok": False, "step": "goal_set", "error": str(exc)}

        # Stamp the bootstrap marker so a subsequent /vise-goal call
        # with the same goal text short-circuits via the guard above.
        from datetime import datetime, timezone
        stamped = engine.update_goal(
            resolved_dir,
            bootstrapped=True,
            bootstrap_completed_at=datetime.now(timezone.utc).isoformat(),
        )
        if stamped is not None:
            g = stamped

        goal_id = g.id
        workflow_name: str | None = None

        # --- Step 3: Synthesize + activate workflow ---
        if synthesize_workflow:
            try:
                workflow_name = _synthesize_and_activate_workflow(g, resolved_dir)
            except Exception as exc:
                return {
                    "ok": False,
                    "step": "workflow",
                    "error": str(exc),
                    "goal_id": goal_id,
                    "autonomy_enabled": False,
                    "agents_deployed": [],
                    "scheduled_items": [],
                }

        # --- Step 4: Enable autonomy ---
        autonomy_enabled = False
        if enable_autonomy:
            try:
                data = _read_settings(settings_path)
                env = data.setdefault("env", {})
                env["VISE_AUTONOMY"] = "1"
                _write_settings_atomic(settings_path, data)
                autonomy_enabled = True
            except Exception as exc:
                return {
                    "ok": False,
                    "step": "autonomy",
                    "error": str(exc),
                    "goal_id": goal_id,
                    "workflow_name": workflow_name,
                    "autonomy_enabled": False,
                    "agents_deployed": [],
                    "scheduled_items": [],
                }

        # Step 5 (agent deployment) was removed: vise ships no agent-deployment
        # hub, so this never wrote anything — see git history for the deleted
        # `_deploy_agents_inline`. `agents_deployed` stays empty; `deploy_subagents`
        # still gates the restart_claude enqueue below (Step 6).
        agents_deployed: list[str] = []

        # --- Step 6: Record next_task + enqueue schedule items ---
        scheduled_items: list[str] = []
        pane_target: str | None = None

        try:
            from vise.engines import schedule as sched_engine
            from vise.engines import usage_state
        except ImportError:
            # vise ships no schedule/usage engines — skip restart enqueue.
            sched_engine = None
            usage_state = None

        pane_target = usage_state.default_target() if usage_state is not None else None

        # The restart_and_prompt enqueue is gated on ``deploy_subagents``
        # because that's the user signal "I'm bootstrapping a fresh
        # session". The post-restart ``/vise-goal`` call passes
        # ``deploy_subagents=False`` so it does NOT queue another restart
        # (which would loop). ``/setup-agents`` handles its own
        # restart enqueue via the low-level ``schedule_add`` tool.
        if deploy_subagents and pane_target:
            try:
                from vise.engines import next_task as nt_engine
                nt_engine.record(
                    project_dir=resolved_dir,
                    summary=(
                        f"Goal bootstrap complete. Goal `{goal_id}` set: {goal[:100]}. "
                        f"Workflow: {workflow_name or 'none'}."
                    ),
                    task_description=f"Resume goal {goal_id}: {goal[:80]}",
                )

                resume_directive = (
                    f"Resume goal `{goal_id}`: {goal[:100]}. "
                    f"Workflow `{workflow_name or 'active workflow'}` is active. "
                    f"Target confidence: {g.target_confidence}. "
                    "Call `graph_status` to see the current phase and continue."
                )

                sched_engine.add(
                    resolved_dir, "prompt",
                    value=resume_directive, target=pane_target,
                    clear_first=True,
                )
                scheduled_items.append("prompt")

            except Exception as exc:
                # Non-fatal: goal is set, autonomy enabled
                scheduled_items = [f"schedule_error: {exc}"]

        next_msg = (
            "daemon will execute prompt (clear_first=True)"
            if pane_target and scheduled_items and not any("error" in s for s in scheduled_items)
            else "no pane target — restart Claude manually to resume"
        )

        return {
            "ok": True,
            "goal_id": goal_id,
            "workflow_name": workflow_name,
            "autonomy_enabled": autonomy_enabled,
            "agents_deployed": agents_deployed,
            "scheduled_items": scheduled_items,
            "pane_target": pane_target,
            "next": next_msg,
        }



# Back-compat alias
register_goal_tools = register_goal
