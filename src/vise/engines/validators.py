"""Pluggable validators that produce ValidatorRecords from a Goal.

Kept intentionally small. The HTTP/HTML/contrast/screenshot validators were
removed along with the swarm/workflow-synth pieces — they were exceeding the
scope of the auto-clear + assign-next-task loop. The four below cover the
``tests pass``, ``lint pass``, ``arbitrary command exits 0``, and ``files
exist`` cases that the loop actually depends on.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from vise.engines.goal_state import Goal, ValidatorRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- consistency guard (Edit 6) -------------------------------------------
# Markers that indicate a concealed failure even when the process exits 0.
_FAILED_RE = re.compile(r"\b[1-9]\d* failed\b")
_FAILURE_MARKERS: tuple[str, ...] = (
    "Traceback (most recent call last):",
    "panic:",
)


def _scan_for_failure_marker(output: str) -> bool:
    """True if *output* contains any known failure marker."""
    if _FAILED_RE.search(output):
        return True
    return any(marker in output for marker in _FAILURE_MARKERS)


def _apply_consistency_guard(passed: bool, combined_output: str, evidence: str) -> tuple[bool, str]:
    """Force-fail an otherwise-passing result when the full output betrays a
    concealed failure. Never touches an already-failing result.
    """
    if passed and _scan_for_failure_marker(combined_output):
        return False, evidence + " [forced-fail: output contained failure marker]"
    return passed, evidence


# --- evidence persistence (Edit 5) ----------------------------------------

def _goal_state_dir() -> Path:
    return Path(os.environ.get("VISE_GOAL_DIR", Path.home() / ".local/share/vise/goal"))


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "unnamed"


def _persist_evidence(goal: Goal, validator_name: str, combined_output: str) -> str:
    """Write COMPLETE stdout+stderr to a per-goal evidence log. Returns the
    absolute path written, or "" if persistence failed (non-fatal).
    """
    try:
        goal_name = _sanitize(Path(goal.project_dir).resolve().name or goal.id)
        evidence_dir = _goal_state_dir() / "evidence" / goal_name
        evidence_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        log_path = evidence_dir / f"{_sanitize(validator_name)}-{ts}.log"
        log_path.write_text(combined_output, encoding="utf-8")
        return str(log_path)
    except Exception:
        return ""


# --- MCP envelope unwrap (capability path) --------------------------------

def _unwrap_tool_output(output: Any) -> tuple[Any, bool]:
    """Unwrap a ``_call_tool`` return into the actual tool result.

    Subprocess proxies return a JSON-RPC envelope::

        {"jsonrpc": "2.0", "id": N, "result": {
            "structuredContent": {...},
            "content": [{"type": "text", "text": "<json>"}],
            "isError": false}}

    Internal proxies may return the tool result dict directly.

    Returns ``(unwrapped, is_error)`` where ``is_error`` reflects the envelope's
    ``isError`` flag (subprocess transport-level failure). ``unwrapped`` is the
    best available view of the actual tool result.
    """
    if not isinstance(output, dict):
        return output, False

    if "result" in output and isinstance(output["result"], dict):
        result = output["result"]
        is_error = bool(result.get("isError", False))

        if "structuredContent" in result:
            return result["structuredContent"], is_error

        content = result.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and first.get("type") == "text":
                text = first.get("text", "")
                try:
                    return json.loads(text), is_error
                except (json.JSONDecodeError, TypeError):
                    return text, is_error

        return result, is_error

    # Not an envelope — internal proxy returned the result directly.
    return output, False


def _capability_passed(unwrapped: Any, is_error: bool) -> bool:
    """Pass predicate shared by run/run_async.

    Fail when: envelope reported ``isError``; OR result is a dict with an
    ``error`` key; OR result is a dict with ``ok`` present and falsy.
    Pass when: no error key AND (``ok`` truthy when present, else True), or a
    non-dict / ok-less dict.
    """
    if is_error:
        return False
    if isinstance(unwrapped, dict):
        if "error" in unwrapped:
            return False
        return bool(unwrapped.get("ok", True))
    return True


class Validator(Protocol):
    name: str
    weight: float

    def run(self, goal: Goal) -> ValidatorRecord: ...


# --- builtins --------------------------------------------------------------

@dataclass
class TestsPassValidator:
    weight: float = 0.4
    name: str = "tests_pass"
    test_cmd: tuple[str, ...] = ("pytest", "-q")

    def run(self, goal: Goal) -> ValidatorRecord:
        # Set-once project override: the graph node hardcodes `pytest` via
        # `type: tests_pass`, which is wrong for any non-Python repo. Rather
        # than autodetect the runner (guessing pm + script name, JS-only,
        # fragile), let the project name its own test command once in
        # .claude/settings.json env. Explicit YAML `test_cmd` still wins.
        cmd = self.test_cmd
        env_cmd = os.environ.get("VISE_TEST_CMD", "").strip()
        if env_cmd and cmd == ("pytest", "-q"):
            import shlex
            cmd = tuple(shlex.split(env_cmd))

        if not cmd or not shutil.which(cmd[0]):
            return ValidatorRecord(
                name=self.name, passed=False, confidence_contribution=0.0,
                weight=self.weight, evidence=f"{cmd[0] if cmd else '<empty>'} not on PATH", at=_now(),
                source="mechanical", exit_code=None,
            )
        r = subprocess.run(
            list(cmd), cwd=goal.project_dir,
            capture_output=True, text=True, check=False, timeout=600,
        )
        # pytest exit 5 = "no tests collected". Non-blocking (blocking here was
        # the original false negative), but NOT a silent green: the evidence
        # names the escape hatch so a JS/TS repo that happens to have pytest on
        # PATH doesn't read as "verified" when zero tests ran. Scoped to pytest;
        # other runners give exit 5 a different meaning.
        if cmd[0] == "pytest" and r.returncode == 5:
            combined = (r.stdout or "") + (r.stderr or "")
            log_path = _persist_evidence(goal, self.name, combined)
            return ValidatorRecord(
                name=self.name, passed=True, confidence_contribution=self.weight,
                weight=self.weight,
                evidence="pytest: no tests collected (skipped) — set VISE_TEST_CMD if this repo's tests run elsewhere",
                at=_now(), source="mechanical", exit_code=r.returncode,
                full_output_path=log_path,
            )
        passed = r.returncode == 0
        combined = (r.stdout or "") + (r.stderr or "")
        ev = (r.stdout.splitlines()[-1] if r.stdout else "") or r.stderr[:200]
        ev = ev[:300]
        passed, ev = _apply_consistency_guard(passed, combined, ev)
        log_path = _persist_evidence(goal, self.name, combined)
        return ValidatorRecord(
            name=self.name, passed=passed,
            confidence_contribution=self.weight if passed else 0.0,
            weight=self.weight, evidence=ev, at=_now(),
            source="mechanical", exit_code=r.returncode, full_output_path=log_path,
        )


@dataclass
class LintPassValidator:
    weight: float = 0.15
    name: str = "lint_pass"

    def run(self, goal: Goal) -> ValidatorRecord:
        # Set-once project override, mirroring tests_pass/VISE_TEST_CMD: the node
        # hardcodes ruff, wrong for any non-Python repo. Let the project name its
        # own lint command once in .claude/settings.json env.
        env_cmd = os.environ.get("VISE_LINT_CMD", "").strip()
        if env_cmd:
            import shlex
            cmd: tuple[str, ...] = tuple(shlex.split(env_cmd))
        else:
            cmd = ("ruff", "check", ".", "--exclude", ".claude")

        if not cmd or not shutil.which(cmd[0]):
            # Lint is advisory (low weight). A missing linter must NOT block the
            # gate on a repo that simply doesn't use it — skip-pass (fail-open),
            # consistent with lsp_clean. Evidence names the escape hatch.
            missing = cmd[0] if cmd else "<empty>"
            return ValidatorRecord(
                name=self.name, passed=True, confidence_contribution=self.weight,
                weight=self.weight,
                evidence=f"lint skipped: {missing} not on PATH — set VISE_LINT_CMD to lint this repo",
                at=_now(), source="asserted", exit_code=None,
            )
        r = subprocess.run(
            list(cmd), cwd=goal.project_dir,
            capture_output=True, text=True, check=False, timeout=60,
        )
        passed = r.returncode == 0
        return ValidatorRecord(
            name=self.name, passed=passed,
            confidence_contribution=self.weight if passed else 0.0,
            weight=self.weight,
            evidence=(r.stdout[:300] or "clean") if passed else r.stdout[:300],
            at=_now(),
            source="mechanical", exit_code=r.returncode,
        )


@dataclass
class CommandExitValidator:
    """Run a shell command (no shell=True) and pass if exit 0."""
    cmd: tuple[str, ...]
    weight: float = 0.2
    name: str = "command_exit"
    timeout: int = 120

    def run(self, goal: Goal) -> ValidatorRecord:
        try:
            r = subprocess.run(
                list(self.cmd), cwd=goal.project_dir,
                capture_output=True, text=True, check=False, timeout=self.timeout,
            )
            passed = r.returncode == 0
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            return ValidatorRecord(
                name=self.name, passed=False, confidence_contribution=0.0,
                weight=self.weight, evidence=str(e)[:300], at=_now(),
                source="mechanical", exit_code=None,
            )
        combined = (r.stdout or "") + (r.stderr or "")
        ev = (r.stdout[-300:] or r.stderr[-300:])
        passed, ev = _apply_consistency_guard(passed, combined, ev)
        log_path = _persist_evidence(goal, self.name, combined)
        return ValidatorRecord(
            name=self.name, passed=passed,
            confidence_contribution=self.weight if passed else 0.0,
            weight=self.weight, evidence=ev,
            at=_now(),
            source="mechanical", exit_code=r.returncode, full_output_path=log_path,
        )


@dataclass
class FileExistsValidator:
    paths: tuple[str, ...]
    weight: float = 0.1
    name: str = "files_exist"

    def run(self, goal: Goal) -> ValidatorRecord:
        proj = Path(goal.project_dir)
        missing = [p for p in self.paths if not (proj / p).exists()]
        passed = not missing
        return ValidatorRecord(
            name=self.name, passed=passed,
            confidence_contribution=self.weight if passed else 0.0,
            weight=self.weight,
            evidence="all present" if passed else f"missing: {missing}",
            at=_now(),
            source="mechanical", exit_code=0 if passed else 1,
        )


@dataclass
class CapabilityValidator:
    """Validate by invoking a resolved capability tool (cross-MCP).

    The capability is resolved against the project's capability assignments
    and user pins, then the bound (mcp, tool) is called with ``args``. The
    tool's response decides pass/fail:

    - tool returned ``{"error": ...}``           -> fail
    - tool returned ``{"ok": False, ...}``       -> fail
    - tool returned ``{"ok": True, ...}`` or any non-dict / ok-less dict -> pass

    This handles layoutlint-style ``ok:false`` failures AND generic tools
    that report success implicitly (no ``ok`` key, no ``error`` key).
    """
    capability: str
    args: dict = field(default_factory=dict)
    weight: float = 1.0
    name: str = "capability"

    def _resolve(self, goal) -> tuple[str, str] | ValidatorRecord:
        """Resolve the capability to (mcp, tool) or return a fail record."""
        # vise ships no recipes/capability registry yet — treat a missing
        # registry as an unresolved capability (graceful gate failure).
        try:
            from vise.recipes.loader import load_capabilities, load_user_pins
            from vise.recipes.resolver import resolve_capability
        except ImportError:
            return ValidatorRecord(
                name=self.name, passed=False, confidence_contribution=0.0,
                weight=self.weight,
                evidence=(
                    f"capability '{self.capability}' unresolved — "
                    "no capability registry installed"
                ),
                at=_now(), source="mechanical", exit_code=None,
            )

        resolved = resolve_capability(
            self.capability,
            load_capabilities(goal.project_dir),
            load_user_pins(goal.project_dir),
        )
        if resolved is None:
            return ValidatorRecord(
                name=self.name, passed=False, confidence_contribution=0.0,
                weight=self.weight,
                evidence=(
                    f"capability '{self.capability}' unresolved — "
                    "bind via capability_set"
                ),
                at=_now(), source="mechanical", exit_code=None,
            )
        return resolved

    def _record_from_output(self, goal, output: Any) -> ValidatorRecord:
        """Build a ValidatorRecord from a raw ``_call_tool`` return.

        Unwraps the MCP JSON-RPC envelope, applies the shared pass predicate,
        and persists the unwrapped summary as evidence. Shared by run/run_async.
        """
        unwrapped, is_error = _unwrap_tool_output(output)
        passed = _capability_passed(unwrapped, is_error)
        combined = repr(unwrapped)
        if is_error:
            combined = f"[isError] {combined}"
        ev = combined[:300]
        log_path = _persist_evidence(goal, self.name, combined)
        return ValidatorRecord(
            name=self.name, passed=passed,
            confidence_contribution=self.weight if passed else 0.0,
            weight=self.weight, evidence=ev, at=_now(),
            source="mechanical", exit_code=0 if passed else 1,
            full_output_path=log_path,
        )

    def _record_from_raise(self, goal, mcp: str, tool: str, e: Exception) -> ValidatorRecord:
        combined = f"capability '{self.capability}' ({mcp}.{tool}) raised: {e}"
        log_path = _persist_evidence(goal, self.name, combined)
        return ValidatorRecord(
            name=self.name, passed=False, confidence_contribution=0.0,
            weight=self.weight, evidence=combined[:300], at=_now(),
            source="mechanical", exit_code=None, full_output_path=log_path,
        )

    def run(self, goal) -> ValidatorRecord:
        """Sync path for goal_validate / CLI (no running event loop)."""
        resolved = self._resolve(goal)
        if isinstance(resolved, ValidatorRecord):
            return resolved
        from vise.recipes.runner import _call_tool

        mcp, tool = resolved
        try:
            output = asyncio.run(_call_tool(mcp, tool, self.args))
        except Exception as e:
            return self._record_from_raise(goal, mcp, tool, e)
        return self._record_from_output(goal, output)

    async def run_async(self, goal) -> ValidatorRecord:
        """Loop-aware path for the node-gate (already inside a running loop).

        Awaits ``_call_tool`` directly instead of ``asyncio.run`` — the latter
        raises ``RuntimeError`` when called from inside a running event loop.
        """
        resolved = self._resolve(goal)
        if isinstance(resolved, ValidatorRecord):
            return resolved
        from vise.recipes.runner import _call_tool

        mcp, tool = resolved
        try:
            output = await _call_tool(mcp, tool, self.args)
        except Exception as e:
            return self._record_from_raise(goal, mcp, tool, e)
        return self._record_from_output(goal, output)


# --- lsp_clean validator ---------------------------------------------------

@dataclass
class LspCleanValidator:
    """Gate that fails when changed `.py` files carry ERROR-severity diagnostics.

    Shells out to ruff (+ optionally mypy) via ``lsp_diagnostics`` — never
    uses multilspy which only reports syntax errors.

    Fail-open contract:
    - No checker installed            → pass (reason recorded in evidence).
    - No changed files                → pass.
    - Diagnostics engine unavailable  → pass.
    - Any unexpected exception        → pass (never block a wave on tooling bugs).
    """

    weight: float = 0.3
    name: str = "lsp_clean"

    # Extensions to include in the changed-files filter.
    _SOURCE_EXTS: frozenset[str] = field(
        default_factory=lambda: frozenset({".py"}),
        init=False, repr=False, compare=False,
    )

    def _changed_files(self, project_dir: str) -> list[str]:
        """Return source files changed vs origin/main (uncommitted + untracked)."""
        try:
            # Staged + unstaged changes vs HEAD
            r1 = subprocess.run(
                ["git", "diff", "--name-only", "HEAD"],
                cwd=project_dir, capture_output=True, text=True, check=False, timeout=15,
            )
            # Also pick up changes vs origin/main (the phase baseline) when HEAD is clean
            r2 = subprocess.run(
                ["git", "diff", "--name-only", "origin/main"],
                cwd=project_dir, capture_output=True, text=True, check=False, timeout=15,
            )
            names: set[str] = set()
            for line in (r1.stdout + "\n" + r2.stdout).splitlines():
                name = line.strip()
                if name and Path(name).suffix in self._SOURCE_EXTS:
                    names.add(str(Path(project_dir) / name))
        except Exception:
            return []

        return [p for p in sorted(names) if Path(p).is_file()]

    def run(self, goal) -> ValidatorRecord:  # goal: Goal | SimpleNamespace
        try:
            from vise.engines.lsp_diagnostics import lsp_diagnostics

            project_dir: str = str(goal.project_dir)
            changed = self._changed_files(project_dir)

            if not changed:
                return ValidatorRecord(
                    name=self.name, passed=True,
                    confidence_contribution=self.weight,
                    weight=self.weight,
                    evidence="lsp_clean: no changed source files (skipped)",
                    at=_now(), source="mechanical", exit_code=0,
                )

            errors: list[str] = []
            any_tool_available = False

            for file_path in changed:
                result = lsp_diagnostics(project_dir, file_path)
                if result.get("available"):
                    any_tool_available = True
                    for diag in result.get("diagnostics", []):
                        if diag.get("severity") == "error":
                            rel = Path(file_path).relative_to(project_dir)
                            errors.append(
                                f"{rel}:{diag.get('line', '?')} "
                                f"{diag.get('code', '')} {diag.get('message', '')}"
                            )

            if not any_tool_available:
                return ValidatorRecord(
                    name=self.name, passed=True,
                    confidence_contribution=self.weight,
                    weight=self.weight,
                    evidence="lsp_clean: no diagnostics tool available (skipped)",
                    at=_now(), source="asserted", exit_code=0,
                )

            passed = not errors
            if passed:
                evidence = f"lsp_clean: {len(changed)} file(s) clean"
            else:
                lines = "; ".join(errors[:5])
                if len(errors) > 5:
                    lines += f" … (+{len(errors) - 5} more)"
                evidence = f"lsp_clean: {len(errors)} error(s): {lines}"

            return ValidatorRecord(
                name=self.name, passed=passed,
                confidence_contribution=self.weight if passed else 0.0,
                weight=self.weight,
                evidence=evidence[:500],
                at=_now(), source="mechanical", exit_code=0 if passed else 1,
            )

        except Exception as exc:
            return ValidatorRecord(
                name=self.name, passed=True,
                confidence_contribution=self.weight,
                weight=self.weight,
                evidence=f"lsp_clean: internal error (fail-open): {exc}"[:300],
                at=_now(), source="asserted", exit_code=None,
            )


@dataclass
class UnknownValidator:
    """Fail-closed stand-in for a validator config with an unrecognized type.

    Built by ``build_validators`` when a config's ``type``/``name`` matches no
    registry key. Always fails so a typo — or a validator from a newer vise the
    current install can't run — blocks the gate instead of passing unchecked.
    """
    bad_type: str
    weight: float = 1.0
    name: str = "unknown_validator"

    def run(self, goal: Goal) -> ValidatorRecord:
        return ValidatorRecord(
            name=self.name, passed=False, confidence_contribution=0.0,
            weight=self.weight,
            evidence=(
                f"unknown validator type {self.bad_type!r} — fix the graph or "
                f"upgrade vise; valid types: {sorted(_REGISTRY)}"
            ),
            at=_now(), source="mechanical", exit_code=None,
        )


def _isfloatable(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


# --- registry --------------------------------------------------------------

_REGISTRY: dict[str, Callable[..., Validator]] = {
    "tests_pass": TestsPassValidator,
    "lint_pass": LintPassValidator,
    "command_exit": CommandExitValidator,
    "files_exist": FileExistsValidator,
    "capability": CapabilityValidator,
    "lsp_clean": LspCleanValidator,
}


def build_validators(configs: list[dict]) -> list[Validator]:
    """Build validator instances from config dicts.

    Preferred shape: ``{"type": "tests_pass", "weight": 0.4, ...kwargs}``

    Fallback: when ``type`` is absent but ``name`` matches a registry key,
    ``name`` is treated as an alias for ``type``. When both are present,
    ``type`` takes precedence.
    """
    out: list[Validator] = []
    for cfg in configs:
        t = cfg.get("type")
        if t not in _REGISTRY:
            name_as_type = cfg.get("name")
            if name_as_type in _REGISTRY:
                t = name_as_type
            else:
                # Fail closed: silently dropping an unknown type let the node
                # pass with nothing checked (false green). A synthetic failing
                # validator surfaces the misconfig without crashing either path
                # — a typo OR a newer-version validator this vise can't enforce
                # must block, not pass unseen.
                out.append(UnknownValidator(
                    bad_type=str(t if t is not None else cfg.get("name")),
                    weight=float(cfg.get("weight", 1.0)) if _isfloatable(cfg.get("weight")) else 1.0,
                ))
                continue
        kwargs = {k: v for k, v in cfg.items() if k not in ("type", "name")}
        if "cmd" in kwargs and isinstance(kwargs["cmd"], list):
            kwargs["cmd"] = tuple(kwargs["cmd"])
        if "paths" in kwargs and isinstance(kwargs["paths"], list):
            kwargs["paths"] = tuple(kwargs["paths"])
        if "test_cmd" in kwargs and isinstance(kwargs["test_cmd"], list):
            kwargs["test_cmd"] = tuple(kwargs["test_cmd"])
        # Coerce weight to float at the boundary — YAML loaders or JSON
        # round-trips may deliver weight as int or str (e.g. '1.0').
        # aggregate_confidence does sum(r.weight ...) which crashes on str.
        if "weight" in kwargs:
            import contextlib
            with contextlib.suppress(TypeError, ValueError):
                kwargs["weight"] = float(kwargs["weight"])
        out.append(_REGISTRY[t](**kwargs))
    return out


def aggregate_confidence(results: list[ValidatorRecord]) -> float:
    if not results:
        return 0.0
    total_w = sum(r.weight for r in results)
    if total_w <= 0:
        return 0.0
    return sum(r.weight * (1.0 if r.passed else 0.0) for r in results) / total_w


def run_validators(goal: Goal) -> tuple[list[ValidatorRecord], float]:
    vs = build_validators(goal.validator_configs)
    results = [v.run(goal) for v in vs]
    confidence = aggregate_confidence(results)
    return results, confidence
