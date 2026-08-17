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
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from fnmatch import fnmatch
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from vise.engines.goal_state import Goal, ValidatorRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_path_like(exe: str) -> bool:
    return os.sep in exe or bool(os.altsep and os.altsep in exe)


def _runnable(exe: str, project_dir: str) -> bool:
    """Can ``exe`` actually be executed the way the validator will run it?

    The check and the run must agree on a working directory. ``shutil.which``
    resolves a path-like command against the CURRENT process's cwd — the MCP
    server's, which is wherever Claude Code was launched — while the command
    itself runs with ``cwd=project_dir``. So every relative command failed the
    pre-check and skip-passed forever while being perfectly runnable:
    ``node_modules/.bin/eslint`` (the standard JS invocation), ``.venv/bin/pytest``,
    ``./scripts/check.sh``. Green gate, evidence reading "not on PATH", nothing run.

    Bare names still go through PATH. ``Path(project_dir) / exe`` leaves an
    absolute ``exe`` untouched, so those keep working too.
    """
    if _is_path_like(exe):
        candidate = Path(project_dir) / exe
        return candidate.is_file() and os.access(candidate, os.X_OK)
    return shutil.which(exe) is not None


def _not_found_reason(exe: str) -> str:
    return "not found in the project" if _is_path_like(exe) else "not on PATH"


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
    from vise.hooks._xdg import goal_dir
    return goal_dir()


def _sanitize(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "unnamed"


def _last_line(output: str, limit: int = 300) -> str:
    """The line a test runner puts its summary on, bounded for a gate message."""
    lines = [ln for ln in (output or "").splitlines() if ln.strip()]
    return lines[-1][:limit] if lines else ""


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


def _is_dispatch_stub(output: Any, mcp: str, tool: str) -> bool:
    """True if *output* is vise's own ``_call_tool`` stub reply — i.e. the
    capability resolved fine, but vise has no dispatch layer to actually call
    it (as opposed to the capability being unresolved, which fails earlier in
    ``CapabilityValidator._resolve`` and must keep blaming the binding).
    """
    return (
        isinstance(output, dict)
        and output.get("status") == "unresolved"
        and output.get("mcp_name") == mcp
        and output.get("tool_name") == tool
        and "no MCP dispatch layer" in str(output.get("reason", ""))
    )


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
            # Fail-open, same contract as lint_pass / lsp_clean: say why, don't
            # block. Failing CLOSED here turned every node that declares
            # `tests_pass` into a hard deadlock on any repo whose runner vise
            # cannot find — and release-graph declares it on its START node, so
            # that workflow was unusable from its first traverse on a non-Python
            # repo. This is the same argument feature-dev-graph.yaml already
            # makes for shipping the coverage `command_exit` entries commented.
            # `source="asserted"`, NOT "mechanical": nothing ran, so goal_complete
            # still refuses to grade this as a verified pass.
            missing = cmd[0] if cmd else "<empty>"
            return ValidatorRecord(
                name=self.name, passed=True, confidence_contribution=self.weight,
                weight=self.weight,
                evidence=(
                    f"tests skipped: {missing} not on PATH — "
                    f"set VISE_TEST_CMD to run this repo's suite"
                ),
                at=_now(), source="asserted", exit_code=None,
                outcome="unverified",
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
                full_output_path=log_path, outcome="unverified",
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
class TestsFailValidator:
    """A reproduction is a test that FAILS. Until then there is nothing to fix.

    `debug-graph.yaml`'s reproduce node already demands this in prose —
    *"Confirm that at least one test fails"* — and had no way to check it, so
    the strongest gate in the whole debug workflow was a phrase the agent
    emitted about itself. An unreproduced bug that proceeds to `fix` is how a
    debugging session ends up editing code on a hypothesis.

    Deliberately NOT the negation of `tests_pass`:

      - a runner that is missing is unverified, not "reproduced" — inverting a
        skip into a green would manufacture a reproduction out of a missing
        binary, which is the worst possible direction to be wrong here;
      - "no tests collected" is likewise unverified: zero tests is not a
        failing test;
      - a crashed runner (import error, config error) is unverified too, and
        this is the case the naive `returncode != 0` gets wrong — a broken
        conftest exits nonzero and looks exactly like a reproduction.
    """

    weight: float = 0.4
    name: str = "tests_fail"
    test_cmd: tuple[str, ...] = ("pytest", "-q")

    def run(self, goal: Goal) -> ValidatorRecord:
        cmd = self.test_cmd
        env_cmd = os.environ.get("VISE_TEST_CMD", "").strip()
        if env_cmd and cmd == ("pytest", "-q"):
            import shlex
            cmd = tuple(shlex.split(env_cmd))

        if not cmd or not shutil.which(cmd[0]):
            missing = cmd[0] if cmd else "<empty>"
            return ValidatorRecord(
                name=self.name, passed=True, confidence_contribution=self.weight,
                weight=self.weight,
                evidence=(
                    f"reproduction not checked: {missing} not on PATH — "
                    f"set VISE_TEST_CMD to run this repo's suite"
                ),
                at=_now(), source="asserted", exit_code=None,
                outcome="unverified",
            )

        r = subprocess.run(
            list(cmd), cwd=goal.project_dir,
            capture_output=True, text=True, check=False, timeout=600,
        )
        combined = (r.stdout or "") + (r.stderr or "")
        log_path = _persist_evidence(goal, self.name, combined)

        # pytest's exit codes separate "tests ran and some failed" (1) from
        # "the runner never got that far" (2 usage, 3 internal, 4 cmdline,
        # 5 nothing collected). Only 1 is a reproduction.
        if cmd[0] == "pytest":
            if r.returncode == 1:
                return ValidatorRecord(
                    name=self.name, passed=True,
                    confidence_contribution=self.weight, weight=self.weight,
                    evidence=f"reproduced: {_last_line(combined)}",
                    at=_now(), source="mechanical", exit_code=r.returncode,
                    full_output_path=log_path,
                )
            if r.returncode == 0:
                return ValidatorRecord(
                    name=self.name, passed=False, confidence_contribution=0.0,
                    weight=self.weight,
                    evidence="suite is green — nothing reproduced yet",
                    at=_now(), source="mechanical", exit_code=r.returncode,
                    full_output_path=log_path,
                )
            return ValidatorRecord(
                name=self.name, passed=True,
                confidence_contribution=self.weight, weight=self.weight,
                evidence=(
                    f"runner exited {r.returncode} without running tests "
                    f"(collection or config error) — not a reproduction"
                ),
                at=_now(), source="mechanical", exit_code=r.returncode,
                full_output_path=log_path, outcome="unverified",
            )

        # Unknown runner: nonzero means *something* failed, but this validator
        # cannot tell a failing test from a broken invocation, and claiming a
        # reproduction it cannot distinguish is the mistake it exists to avoid.
        return ValidatorRecord(
            name=self.name, passed=True,
            confidence_contribution=self.weight, weight=self.weight,
            evidence=(
                f"{cmd[0]} exited {r.returncode}; vise cannot tell a failing "
                f"test from a broken run for this runner — asserting, not verifying"
            ),
            at=_now(), source="asserted", exit_code=r.returncode,
            full_output_path=log_path, outcome="unverified",
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
                outcome="unverified",
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


# --- git-diff validators ---------------------------------------------------
#
# Both close a gap between what an agent charter PROMISES and what anything
# checks. `ponytail` says a new dependency needs a stated reason; nothing
# measured whether one appeared. The orchestration skill says a wave must not
# write outside its partition; nothing measured that either. A promise no gate
# can read is a preference, and preferences drift.

# Dependency manifests worth watching, across the ecosystems vise ships rules
# for. Lockfiles are included deliberately: a transitive addition is still a new
# trust relationship, and it is the one nobody declares.
_DEP_MANIFESTS: tuple[str, ...] = (
    "pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.cfg",
    "Pipfile", "uv.lock", "poetry.lock",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "go.mod", "go.sum",
    "Cargo.toml", "Cargo.lock",
    "pom.xml", "build.gradle", "build.gradle.kts", "gradle/libs.versions.toml",
    "Gemfile", "Gemfile.lock",
    "composer.json", "composer.lock",
    "Package.swift", "Package.resolved",
    "*.csproj", "packages.config", "Directory.Packages.props",
)

# Diff noise that is never a dependency, whatever the manifest's format.
_DEP_NOISE = re.compile(r"^\+\s*(#|//|$|\[|\{|\}|version\s*=|name\s*=)")


def _git(args: list[str], cwd: str, timeout: int = 30) -> subprocess.CompletedProcess | None:
    """Run git, or return None when git is unusable here. Never raises."""
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            check=False, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None


def _unverified(name: str, weight: float, why: str) -> ValidatorRecord:
    """A pass that checked nothing, labelled as such.

    Same contract every other validator here follows: never block a repo over
    tooling it does not have, and never let that pass read as clean.
    """
    return ValidatorRecord(
        name=name, passed=True, confidence_contribution=0.0, weight=weight,
        evidence=why, at=_now(), source="asserted", exit_code=None,
        outcome="unverified",
    )


@dataclass
class NoNewDepsValidator:
    """Fail when a dependency manifest gained entries in the diff being gated.

    Not a ban — an accounting check. `ponytail`'s ladder ends with "write it
    yourself", and `engineering-baseline` requires a stated reason why the
    stdlib and the existing manifest could not do it. This makes an undeclared
    addition visible at the gate instead of at review time, and `allow` is how a
    declared one passes: naming the package in the node config IS the statement.

    Fail-open, like every validator here: no git, no manifests, or an unknown
    base ref reports `unverified` rather than blocking.
    """
    base: str = "HEAD"
    allow: tuple[str, ...] = ()
    weight: float = 0.3
    name: str = "no_new_deps"
    timeout: int = 30

    def run(self, goal: Goal) -> ValidatorRecord:
        proj = goal.project_dir

        if (r := _git(["rev-parse", "--git-dir"], proj, self.timeout)) is None or r.returncode != 0:
            return _unverified(self.name, self.weight, "not a git repository — nothing to diff")
        if (r := _git(["rev-parse", "--verify", self.base], proj, self.timeout)) is None or r.returncode != 0:
            return _unverified(self.name, self.weight, f"base ref {self.base!r} does not resolve")

        # Glob entries (`*.csproj`) must be EXPANDED, not passed through: a
        # pattern always "exists" as a string, so forwarding it unresolved made
        # a repo with no manifest at all look like it had one, and the
        # never-checked case reported itself as verified.
        present: list[str] = []
        root = Path(proj)
        for m in _DEP_MANIFESTS:
            if "*" in m:
                present.extend(
                    str(hit.relative_to(root)) for hit in sorted(root.glob(m))
                )
            elif (root / m).exists():
                present.append(m)
        if not present:
            return _unverified(self.name, self.weight, "no dependency manifest in this repo")

        diff = _git(["diff", "-U0", self.base, "--", *present], proj, self.timeout)
        if diff is None or diff.returncode != 0:
            return _unverified(self.name, self.weight, "git diff failed against the manifests")

        added: list[str] = []
        current = ""
        for line in diff.stdout.splitlines():
            if line.startswith("+++ b/"):
                current = line[6:]
                continue
            if not line.startswith("+") or line.startswith("+++"):
                continue
            if _DEP_NOISE.match(line):
                continue
            body = line[1:].strip()
            if not body:
                continue
            # `allow` matches on substring: a manifest line is
            # `requests==2.31.0` in one ecosystem and `"requests": "^2.31"` in
            # another, and a node config should not have to know which.
            if any(pkg and pkg in body for pkg in self.allow):
                continue
            added.append(f"{current}: {body}"[:160])

        passed = not added
        evidence = (
            f"no manifest additions vs {self.base} ({len(present)} manifest(s) checked)"
            if passed else
            f"{len(added)} dependency line(s) added vs {self.base}: " + "; ".join(added[:5])
        )
        return ValidatorRecord(
            name=self.name, passed=passed,
            confidence_contribution=self.weight if passed else 0.0,
            weight=self.weight, evidence=evidence[:300], at=_now(),
            source="mechanical", exit_code=0 if passed else 1,
        )


# vise's own bookkeeping, written into the project by vise itself. It must
# never count as work the agent did outside its partition: `graph_activate`
# writes `.claude/workflow/graph.yaml`, so a diff_scope gate blocked on a
# COMPLETELY CLEAN tree in any repo that had not gitignored it — a gate that
# fails when you have done nothing is a gate you turn off.
#
# Hard-excluded rather than left to `--exclude-standard`, because relying on
# the consumer's .gitignore means vise writes a file and then blames the user
# for it. vise's own .gitignore carries `.claude/workflow/`, which is the
# admission that this is vise's, not theirs.
#
# `.claude/workflows/` (plural) is deliberately NOT here — those are graphs the
# user authored, and moving one outside the declared scope is exactly the kind
# of thing this validator exists to notice.
_VISE_STATE_PREFIXES: tuple[str, ...] = (
    ".claude/workflow/",
    ".claude/settings.local.json",
)


def _is_vise_state(path: str) -> bool:
    return any(
        path == prefix.rstrip("/") or path.startswith(prefix)
        for prefix in _VISE_STATE_PREFIXES
    )


@dataclass
class DiffScopeValidator:
    """Fail when the diff touches a file outside the node's declared scope.

    The orchestration skill's hard rule is "never two agents writing the same
    file in one wave — partition scope by file ownership before dispatching".
    Nothing enforced it, so a builder that wandered outside its partition was
    caught by review or not at all.

    `allow` is a list of fnmatch globs, relative to the repo root. Empty
    `allow` FAILS CLOSED — same choice `quality_check` makes for an empty
    `check:`. A scope gate that permits everything when misconfigured is worse
    than no gate, because it reads as one.
    """
    allow: tuple[str, ...] = ()
    base: str = "HEAD"
    weight: float = 0.3
    name: str = "diff_scope"
    timeout: int = 30

    def run(self, goal: Goal) -> ValidatorRecord:
        proj = goal.project_dir

        if not self.allow:
            return ValidatorRecord(
                name=self.name, passed=False, confidence_contribution=0.0,
                weight=self.weight,
                evidence="diff_scope misconfigured: no `allow:` globs given in the graph node",
                at=_now(), source="mechanical", exit_code=None,
            )

        if (r := _git(["rev-parse", "--git-dir"], proj, self.timeout)) is None or r.returncode != 0:
            return _unverified(self.name, self.weight, "not a git repository — nothing to diff")
        if (r := _git(["rev-parse", "--verify", self.base], proj, self.timeout)) is None or r.returncode != 0:
            return _unverified(self.name, self.weight, f"base ref {self.base!r} does not resolve")

        # Tracked changes plus untracked files: a builder creating a brand-new
        # file outside its partition is exactly the case this exists to catch,
        # and `git diff` alone never sees it.
        changed: list[str] = []
        tracked = _git(["diff", "--name-only", self.base], proj, self.timeout)
        if tracked is None or tracked.returncode != 0:
            return _unverified(self.name, self.weight, "git diff failed")
        changed.extend(tracked.stdout.split())
        untracked = _git(
            ["ls-files", "--others", "--exclude-standard"], proj, self.timeout
        )
        if untracked is not None and untracked.returncode == 0:
            changed.extend(untracked.stdout.split())

        if not changed:
            return _unverified(self.name, self.weight, f"no files changed vs {self.base}")

        changed = [f for f in changed if not _is_vise_state(f)]
        if not changed:
            return _unverified(
                self.name, self.weight,
                f"no files changed vs {self.base} (excluding vise's own state)",
            )

        outside = sorted({
            f for f in changed
            if not any(fnmatch(f, pattern) for pattern in self.allow)
        })
        passed = not outside
        evidence = (
            f"all {len(set(changed))} changed file(s) inside scope"
            if passed else
            f"{len(outside)} file(s) outside {list(self.allow)}: " + ", ".join(outside[:8])
        )
        return ValidatorRecord(
            name=self.name, passed=passed,
            confidence_contribution=self.weight if passed else 0.0,
            weight=self.weight, evidence=evidence[:300], at=_now(),
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

    def _record_from_output(self, goal, mcp: str, tool: str, output: Any) -> ValidatorRecord:
        """Build a ValidatorRecord from a raw ``_call_tool`` return.

        Unwraps the MCP JSON-RPC envelope, applies the shared pass predicate,
        and persists the unwrapped summary as evidence. Shared by run/run_async.

        A resolved capability vise itself cannot dispatch (the ``_call_tool``
        stub) is a distinct failure mode from "unresolved" — the binding is
        fine, vise just has no cross-MCP dispatch layer. Never blame
        ``capability_set`` for that; tell the caller which tool to run.
        """
        if _is_dispatch_stub(output, mcp, tool):
            evidence = (
                f"capability '{self.capability}' resolved to {mcp}.{tool} but vise "
                f"has no dispatch layer to call it — run {mcp}.{tool} with args "
                f"{self.args!r} yourself, then re-traverse to record the result"
            )
            log_path = _persist_evidence(goal, self.name, evidence)
            return ValidatorRecord(
                name=self.name, passed=False, confidence_contribution=0.0,
                weight=self.weight, evidence=evidence[:500], at=_now(),
                source="mechanical", exit_code=None, full_output_path=log_path,
            )
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
        return self._record_from_output(goal, mcp, tool, output)

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
        return self._record_from_output(goal, mcp, tool, output)


# --- lsp_clean validator ---------------------------------------------------

# Extension → language name, used to route each changed file to its checker
# and to report per-language verification status (a missing Go checker must
# never suppress Python findings, and vice versa).
_LSP_LANG_EXTS: dict[str, frozenset[str]] = {
    "python": frozenset({".py"}),
    "go": frozenset({".go"}),
    "rust": frozenset({".rs"}),
    "typescript": frozenset({".ts", ".tsx"}),
}

# Languages whose checker runs once per changed file: the tools passed to
# ``lsp_diagnostics`` and the label(s) recorded in ``checkers_tried``. Kept
# as one mapping (rather than two near-identical 16-line if/elif blocks in
# ``run``) so adding a per-file language is a dict entry, not a copy-pasted
# branch.
_LSP_PER_FILE_LANGS: dict[str, tuple[tuple[str, ...], frozenset[str]]] = {
    "python": (("ruff", "mypy"), frozenset({"ruff", "mypy"})),
    "go": (("go_vet",), frozenset({"go"})),
}
# Languages whose checker runs once for the whole project (cargo/tsc cannot
# be pointed at a single file) — findings are filtered to the changed set.
_LSP_PROJECT_LANG_TOOL: dict[str, str] = {"rust": "cargo", "typescript": "tsc"}


def _ext_to_lang(suffix: str) -> str | None:
    for lang, exts in _LSP_LANG_EXTS.items():
        if suffix in exts:
            return lang
    return None


@dataclass
class LspCleanValidator:
    """Gate that fails when changed source files carry blocking diagnostics.

    No LSP involved despite the name (kept for compatibility with workflow
    YAML that references the ``lsp_clean`` validator type). Shells out to
    per-language checkers via ``lsp_diagnostics`` / ``lsp_diagnostics_project``
    — never uses multilspy, which only reports syntax errors.

    Covers Python (ruff + mypy), Go (go vet), Rust (cargo check), and
    TypeScript (tsc). Each language's verification status is independent: an
    absent checker for one language never suppresses another language's
    findings (see ``_LSP_LANG_EXTS`` / the per-language loop in ``run``).

    Fail-open contract — every path below still passes, but the record's
    ``outcome`` says which kind of pass it was:
    - No checker installed for any changed language → pass, outcome="unverified".
    - No changed files                → pass, outcome="unverified".
    - Diagnostics engine unavailable  → pass, outcome="unverified".
    - Any unexpected exception        → pass, outcome="unverified"
      (never block a wave on tooling bugs).
    - At least one checker ran, nothing blocking → pass, outcome="verified".
    - A checker ran and found blocking findings  → fail, outcome="failed".
    """

    weight: float = 0.3
    name: str = "lsp_clean"

    # Total wall-clock budget for one lsp_clean run, across every checker it
    # invokes. Two whole-project checkers (cargo + tsc) can land in the same
    # gate at 120s each on top of a per-file ruff/mypy pass — unbounded, that
    # is one MCP call running for minutes. Exceeding this stops the run
    # early: languages not yet checked are reported unverified rather than
    # left to whatever the OS eventually does.
    time_budget_s: float = 180.0

    # Extensions to include in the changed-files filter — derived from
    # _LSP_LANG_EXTS so the two can never drift apart (a language added to
    # one without the other used to make by_lang come up empty silently).
    _SOURCE_EXTS: frozenset[str] = field(
        default_factory=lambda: frozenset().union(*_LSP_LANG_EXTS.values()),
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
            from vise.engines.lsp_diagnostics import (
                lsp_diagnostics,
                lsp_diagnostics_project,
            )

            project_dir: str = str(goal.project_dir)
            changed = self._changed_files(project_dir)

            if not changed:
                return ValidatorRecord(
                    name=self.name, passed=True,
                    confidence_contribution=self.weight,
                    weight=self.weight,
                    evidence="lsp_clean: nothing to check — no changed source files (skipped)",
                    at=_now(), source="mechanical", exit_code=0,
                    outcome="unverified",
                )

            # Group changed files by language so one absent checker cannot
            # suppress findings from a language whose checker IS available.
            by_lang: dict[str, list[str]] = {}
            for f in changed:
                lang = _ext_to_lang(Path(f).suffix)
                if lang:
                    by_lang.setdefault(lang, []).append(f)

            errors: list[str] = []
            lang_status: dict[str, str] = {}
            checkers_tried: set[str] = set()
            start = time.monotonic()
            budget_hit = False

            for lang, files in by_lang.items():
                if time.monotonic() - start > self.time_budget_s:
                    # Budget already spent by earlier languages in this same
                    # run — do not start another checker. Reported the same
                    # as "unverified" (the gate still opens) but the overall
                    # evidence names the reason so it isn't confused with
                    # "no checker installed".
                    lang_status[lang] = "unverified"
                    budget_hit = True
                    continue

                if lang in _LSP_PER_FILE_LANGS:
                    tools, tried_names = _LSP_PER_FILE_LANGS[lang]
                    checkers_tried.update(tried_names)
                    any_available = False
                    for file_path in files:
                        result = lsp_diagnostics(project_dir, file_path, tools=tools)
                        if result.get("available"):
                            any_available = True
                            for diag in result.get("diagnostics", []):
                                if diag.get("severity") == "error":
                                    rel = Path(file_path).relative_to(project_dir)
                                    errors.append(
                                        f"{rel}:{diag.get('line', '?')} "
                                        f"{diag.get('code', '')} {diag.get('message', '')}"
                                    )
                    lang_status[lang] = "verified" if any_available else "unverified"

                elif lang in _LSP_PROJECT_LANG_TOOL:
                    tool = _LSP_PROJECT_LANG_TOOL[lang]
                    checkers_tried.add(tool)
                    result = lsp_diagnostics_project(project_dir, tools=(tool,))
                    if result.get("available"):
                        lang_status[lang] = "verified"
                        # Whole-project run — filter findings to the changed
                        # set so a diagnostic in an untouched file never
                        # fails the gate.
                        changed_set = {str(Path(p).resolve()) for p in files}
                        for diag in result.get("diagnostics", []):
                            diag_file = diag.get("file", "")
                            try:
                                resolved = str(Path(diag_file).resolve())
                            except Exception:
                                resolved = diag_file
                            if resolved not in changed_set:
                                continue
                            if diag.get("severity") == "error":
                                try:
                                    rel = Path(diag_file).relative_to(project_dir)
                                except ValueError:
                                    rel = Path(diag_file).name
                                errors.append(
                                    f"{rel}:{diag.get('line', '?')} "
                                    f"{diag.get('code', '')} {diag.get('message', '')}"
                                )
                    else:
                        lang_status[lang] = "unverified"

            any_verified = any(status == "verified" for status in lang_status.values())
            status_str = "; ".join(
                f"{lang}: {status}" for lang, status in sorted(lang_status.items())
            )
            if budget_hit:
                status_str += f" [time budget of {self.time_budget_s:.0f}s exceeded]"

            if not any_verified:
                tried = ", ".join(sorted(checkers_tried))
                return ValidatorRecord(
                    name=self.name, passed=True,
                    confidence_contribution=self.weight,
                    weight=self.weight,
                    # Deliberately does NOT claim the checker was absent. This
                    # branch is also reached when a checker was found, ran, and
                    # returned no verdict (cargo with no Cargo.toml, tsc with no
                    # tsconfig) — saying "install one" there is the same species
                    # of false evidence this outcome field exists to end. The
                    # cause is logged by the runner that gave up.
                    evidence=(
                        f"lsp_clean: could not check — no checker returned a verdict "
                        f"(tried {tried}; either absent or it failed to run — see the "
                        f"vise log) [{status_str}]"
                    )[:500],
                    at=_now(), source="asserted", exit_code=0,
                    outcome="unverified",
                )

            passed = not errors
            if passed:
                evidence = f"lsp_clean: {len(changed)} file(s) clean [{status_str}]"
            else:
                lines = "; ".join(errors[:5])
                if len(errors) > 5:
                    lines += f" … (+{len(errors) - 5} more)"
                evidence = f"lsp_clean: {len(errors)} error(s): {lines} [{status_str}]"

            return ValidatorRecord(
                name=self.name, passed=passed,
                confidence_contribution=self.weight if passed else 0.0,
                weight=self.weight,
                evidence=evidence[:500],
                at=_now(), source="mechanical", exit_code=0 if passed else 1,
                outcome="verified" if passed else "failed",
            )

        except Exception as exc:
            return ValidatorRecord(
                name=self.name, passed=True,
                confidence_contribution=self.weight,
                weight=self.weight,
                evidence=f"lsp_clean: could not check — internal error (fail-open): {exc}"[:300],
                at=_now(), source="asserted", exit_code=None,
                outcome="unverified",
            )


@dataclass
class QualityCheckValidator:
    """Gate on a repo-declared quality check (SAST/SCA/complexity/...) from
    ``.vise/quality.yaml`` — see ``vise.engines.quality_profile``.

    Resolution cascade. Every unbound case is a SKIP: ``passed=True``,
    ``source="asserted"`` (never "mechanical" — nothing ran, so
    ``goal_complete`` must not grade this as verified), evidence naming the
    exact next step. Only a real run (case 4) is ``source="mechanical"``.

    1. no profile file            -> not configured, create the profile
    2. profile exists, key absent -> not configured, add the key
    3. configured, binary missing -> skipped, binary not on PATH
    4. otherwise                  -> run it, passed = (exit == 0)

    ``check`` has no required value: an empty ``check`` (misconfiguration —
    nobody said which check to run) FAILS closed, matching how
    ``UnknownValidator`` fails closed on a bad ``type``. A missing ``check``
    key must never crash ``build_validators`` (no try/except there), so this
    field carries a default rather than being required.
    """

    check: str = ""
    weight: float = 0.2
    name: str = "quality_check"
    timeout: int = 120

    def run(self, goal: Goal) -> ValidatorRecord:
        if not self.check:
            return ValidatorRecord(
                name=self.name, passed=False, confidence_contribution=0.0,
                weight=self.weight,
                evidence="quality_check misconfigured: no `check:` name given in the graph node",
                at=_now(), source="mechanical", exit_code=None,
            )

        # Every record this validator emits is named for the CHECK, not the type.
        # A node here declares four of these (security: sast/sca/secrets/contracts),
        # so a bare "quality_check" in the failed[] list told you a gate went red
        # and nothing about which defect class did it.
        label = f"{self.name}:{self.check}"

        from vise.engines.quality_profile import UnboundCheck, UnboundReason, resolve_check

        resolved = resolve_check(goal.project_dir, self.check)
        if isinstance(resolved, UnboundCheck):
            if resolved.reason is UnboundReason.NO_PROFILE:
                evidence = (
                    f"quality check '{self.check}' not configured — create "
                    ".vise/quality.yaml with a `checks:` map"
                )
            else:
                evidence = (
                    f"quality check '{self.check}' not configured — add "
                    f"`{self.check}:` to checks: in .vise/quality.yaml"
                )
            return ValidatorRecord(
                name=label, passed=True, confidence_contribution=self.weight,
                weight=self.weight, evidence=evidence,
                at=_now(), source="asserted", exit_code=None,
                outcome="unverified",
            )

        cmd = resolved
        if not _runnable(cmd[0], goal.project_dir):
            return ValidatorRecord(
                name=label, passed=True, confidence_contribution=self.weight,
                weight=self.weight,
                evidence=(
                    f"quality check '{self.check}' skipped — {cmd[0]} "
                    f"{_not_found_reason(cmd[0])}"
                ),
                at=_now(), source="asserted", exit_code=None,
                outcome="unverified",
            )

        try:
            r = subprocess.run(
                list(cmd), cwd=goal.project_dir,
                capture_output=True, text=True, check=False, timeout=self.timeout,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            return ValidatorRecord(
                name=label, passed=False, confidence_contribution=0.0,
                weight=self.weight, evidence=str(e)[:300], at=_now(),
                source="mechanical", exit_code=None,
            )
        passed = r.returncode == 0
        ev = (r.stdout[-300:] or r.stderr[-300:])
        return ValidatorRecord(
            name=label, passed=passed,
            confidence_contribution=self.weight if passed else 0.0,
            weight=self.weight, evidence=ev, at=_now(),
            source="mechanical", exit_code=r.returncode,
        )


#: Requirement levels ``OpenSpecValidator`` understands, weakest first. Order
#: matters only for documentation; each level is checked independently.
_OPENSPEC_LEVELS: tuple[str, ...] = (
    "structure", "change", "deltas", "tasks_complete", "validated",
)


def _parse_openspec_report(stdout: str) -> tuple[int, int, list[str]] | None:
    """``(items, failed, error_messages)`` from ``openspec validate --json``.

    Returns None when the payload cannot be read, so the caller can fall back
    to the exit code rather than inventing a verdict. The CLI may print a
    spinner line before the JSON, hence the slice from the first brace.
    """
    start = stdout.find("{")
    if start < 0:
        return None
    try:
        data = json.loads(stdout[start:])
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    totals = (data.get("summary") or {}).get("totals") or {}
    try:
        items = int(totals.get("items", 0))
        failed = int(totals.get("failed", 0))
    except (TypeError, ValueError):
        return None
    messages: list[str] = []
    for item in data.get("items") or []:
        if not isinstance(item, dict) or item.get("valid"):
            continue
        for issue in item.get("issues") or []:
            if isinstance(issue, dict) and issue.get("level") == "ERROR":
                messages.append(f"{item.get('id', '?')}: {issue.get('message', '')}")
    return items, failed, messages


@dataclass
class OpenSpecValidator:
    """Gate on OpenSpec spec-driven planning state — see
    ``vise.engines.openspec_profile``.

    Unlike ``quality_check``, levels 1-4 of this validator FAIL CLOSED when
    unsatisfied. That is the point: OpenSpec is mandatory, and the four
    structural levels are answered by reading ``openspec/`` with stdlib string
    work, so a red gate always means "the plan is missing", never "your machine
    is missing a tool". The fix is a repo-local command (``openspec init``,
    write the proposal), not an install every teammate has to repeat.

    Level 5 (``validated``) is the exception and the only tier that shells out.
    ``openspec`` is a Node CLI; when it is absent this SKIPS with
    ``source="asserted"`` and evidence naming the install, matching how
    ``quality_check`` reports an unbound check. Structure stays enforced by
    levels 1-4 either way, so skipping here narrows depth, not coverage.

    ``require`` levels:

    - ``structure``      — an ``openspec/`` root exists
    - ``change``         — at least one active change with a ``proposal.md``
    - ``deltas``         — that change carries well-formed spec deltas
                           (delta headers present, every requirement has a
                           scenario)
    - ``tasks_complete`` — every checklist box in ``tasks.md`` is ticked
    - ``validated``      — ``openspec validate --all --strict`` exits 0

    ``change`` optionally pins one change by directory name; the default
    accepts any active change, which is the common single-change-in-flight
    case. An unrecognized ``require`` fails closed, like ``UnknownValidator``.
    """

    require: str = "change"
    change: str = ""
    weight: float = 1.0
    name: str = "openspec"
    timeout: int = 120

    def run(self, goal: Goal) -> ValidatorRecord:
        label = f"{self.name}:{self.require or '?'}"

        def _rec(passed: bool, evidence: str, *, source: str = "mechanical",
                 exit_code: int | None = None,
                 outcome: str | None = None) -> ValidatorRecord:
            kwargs: dict[str, Any] = dict(
                name=label, passed=passed,
                confidence_contribution=self.weight if passed else 0.0,
                weight=self.weight, evidence=evidence[:300], at=_now(),
                source=source, exit_code=exit_code,
            )
            # Only the fail-open skip paths pass outcome explicitly — every
            # other call here is a real structural or CLI check and keeps the
            # ValidatorRecord default ("verified").
            if outcome is not None:
                kwargs["outcome"] = outcome
            return ValidatorRecord(**kwargs)

        if self.require not in _OPENSPEC_LEVELS:
            return _rec(False, (
                f"openspec misconfigured: unknown require={self.require!r} — "
                f"valid levels: {list(_OPENSPEC_LEVELS)}"
            ))

        from vise.engines.openspec_profile import active_changes, openspec_root

        if openspec_root(goal.project_dir) is None:
            return _rec(False, (
                "no openspec/ root — this repo has not adopted OpenSpec. "
                "Run `openspec init` and propose the change before implementing."
            ))
        if self.require == "structure":
            return _rec(True, "openspec/ root present")

        if self.require == "validated":
            return self._run_cli(goal, label, _rec)

        changes = active_changes(goal.project_dir)
        if self.change:
            changes = [c for c in changes if c.name == self.change]
            if not changes:
                return _rec(False, (
                    f"change {self.change!r} not found under openspec/changes/ "
                    "— create it with `openspec new change`"
                ))
        if not changes:
            return _rec(False, (
                "no active change under openspec/changes/ — the work in flight "
                "is unspecified. Run `openspec new change <name>` (or /opsx:propose) first."
            ))

        if self.require == "change":
            ok = [c for c in changes if c.has_proposal]
            if ok:
                return _rec(True, f"active change(s) with a proposal: {', '.join(c.name for c in ok)}")
            return _rec(False, (
                "change(s) " + ", ".join(c.name for c in changes) +
                " have no proposal.md — write the proposal before implementing"
            ))

        if self.require == "deltas":
            ok = [c for c in changes if c.deltas.well_formed]
            if ok:
                return _rec(True, (
                    "well-formed spec deltas in: " +
                    ", ".join(f"{c.name} ({c.deltas.requirements} req)" for c in ok)
                ))
            return _rec(False, "; ".join(self._delta_gap(c) for c in changes))

        # tasks_complete
        ok = [c for c in changes if c.tasks_complete]
        if ok:
            return _rec(True, "tasks complete in: " + ", ".join(
                f"{c.name} ({c.tasks_summary})" for c in ok))
        return _rec(False, "no change has all tasks ticked — " + "; ".join(
            f"{c.name}: {c.tasks_summary}" if c.has_tasks else f"{c.name}: no tasks.md"
            for c in changes))

    @staticmethod
    def _delta_gap(c: Any) -> str:
        """Why this change's deltas are not well-formed, named precisely."""
        d = c.deltas
        if not d.files:
            return f"{c.name}: no specs/**/*.md delta files"
        if d.headers == 0:
            return f"{c.name}: no `## ADDED|MODIFIED|REMOVED|RENAMED Requirements` header"
        if d.requirements == 0:
            return f"{c.name}: no `### Requirement:` blocks"
        return (f"{c.name}: requirement(s) with no `#### Scenario:` — "
                + ", ".join(d.orphan_requirements[:5]))

    def _run_cli(self, goal: Goal, label: str, _rec: Callable[..., ValidatorRecord]) -> ValidatorRecord:
        if not _runnable("openspec", goal.project_dir):
            return _rec(True, (
                "openspec CLI not on PATH — structural levels still enforced; "
                "install with `npm i -g @fission-ai/openspec` for strict validation"
            ), source="asserted", outcome="unverified")
        try:
            r = subprocess.run(
                ["openspec", "validate", "--all", "--strict", "--json"],
                cwd=goal.project_dir, capture_output=True, text=True,
                check=False, timeout=self.timeout,
                # The CLI phones home unless told otherwise; a gate must not
                # make a network call to decide whether a repo is compliant.
                env={**os.environ, "OPENSPEC_TELEMETRY": "0", "NO_COLOR": "1"},
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            return _rec(False, str(e))

        report = _parse_openspec_report(r.stdout)
        if report is None:
            # Unparseable output — trust the exit code, quote what we got.
            ev = (r.stdout[-300:] or r.stderr[-300:] or "openspec validate --all --strict --json")
            return _rec(r.returncode == 0, ev, exit_code=r.returncode)

        items, failed, messages = report
        if items == 0:
            # Exit 0 over an empty set is a vacuum, not a verification. Marking
            # it "asserted" keeps goal_complete from grading it as verified —
            # the same rule quality_check applies to an unbound check. The
            # `deltas` level is what makes content mandatory; this level only
            # says the content that exists is well-formed.
            return _rec(True, (
                "openspec validate found nothing to validate — no specs and no "
                "changes exist yet; gate the `deltas` level to require content"
            ), source="asserted", outcome="unverified")
        if failed:
            return _rec(False, (
                f"openspec validate --strict: {failed}/{items} invalid — "
                + "; ".join(messages[:3])
            ), exit_code=r.returncode)
        return _rec(True, f"openspec validate --strict: {items}/{items} valid",
                    exit_code=r.returncode)


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
    "tests_fail": TestsFailValidator,
    "lint_pass": LintPassValidator,
    "command_exit": CommandExitValidator,
    "files_exist": FileExistsValidator,
    "capability": CapabilityValidator,
    "lsp_clean": LspCleanValidator,
    "quality_check": QualityCheckValidator,
    "openspec": OpenSpecValidator,
    "no_new_deps": NoNewDepsValidator,
    "diff_scope": DiffScopeValidator,
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
