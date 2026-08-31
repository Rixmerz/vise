"""lsp_diagnostics — stateless shell-out diagnostics (ruff/mypy, go vet,
cargo check, tsc).

No LSP is involved despite the name (kept for compatibility with workflow
YAML that references the ``lsp_clean`` validator type — see
``vise.engines.validators.LspCleanValidator``). No multilspy dependency
either: shells out to per-language checkers when present on PATH (or in the
venv), degrades gracefully when absent.

Public API
----------
lsp_diagnostics(project_dir, file_path) -> dict
    Per-file checkers: ruff, mypy (Python), go vet (Go). Safe to call once
    per changed file.
lsp_diagnostics_project(project_dir) -> dict
    Whole-project checkers: cargo check (Rust), tsc (TypeScript). These
    cannot be pointed at a single file — call once per project and filter
    the returned diagnostics (each carries a "file" key) to the changed set.

Fail-soft contract
------------------
- Missing checker (shutil.which miss) → skip, record in tools_run, continue.
- ALL checkers absent → {"available": False, "reason": "no diagnostics tool…"}.
- Checker process error / parse failure → log, skip that checker.
- NEVER raise.  NEVER hang (bounded timeout on each subprocess).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SUBPROCESS_TIMEOUT: float = 30.0
# Whole-project checkers (cargo, tsc) compile/typecheck the entire project,
# not one file — their timeout budget is the project's, not the file's.
_PROJECT_SUBPROCESS_TIMEOUT: float = 120.0

# Allowlist of ruff codes that count as blocking errors in the lsp_clean
# validator.  Only genuinely-broken code belongs here — syntax errors and
# hard undefined-name violations.  Everything else (style, imports, SIM*, etc.)
# is a warning so that cosmetic lint does NOT block workflow waves.
#
# NOTE: ruff 0.6+ emits a "severity" field in its JSON output, but we do NOT
# trust it for error/warning classification.  Ruff marks style codes such as
# F401 (unused import) and SIM105 (use contextlib.suppress) as "error" in its
# JSON even though they are cosmetic — relying on ruff's native severity would
# cause lsp_clean to block on harmless lint.  The allowlist below is the sole
# authority for what counts as a blocking error.
_RUFF_ERROR_PREFIXES: tuple[str, ...] = (
    "E9",   # syntax errors (E999 = SyntaxError, E902 = TokenError, etc.)
)
_RUFF_ERROR_CODES: frozenset[str] = frozenset({
    "F821",        # undefined name
    "F822",        # undefined name in __all__
    "F823",        # local variable referenced before assignment
    "F831",        # duplicate argument in function definition
    "invalid-syntax",  # ruff 0.8+ emits this code for syntax errors (replaces E999 in some builds)
})


def _severity_for_ruff(code: str) -> str:
    """Map a ruff diagnostic code to 'error' or 'warning'.

    Only codes in _RUFF_ERROR_CODES or matching _RUFF_ERROR_PREFIXES are
    blocking errors.  All other codes (F401, F811, SIM*, E1-E7, W*, B*, C*,
    UP*, I*, etc.) are warnings.  Unknown codes default to warning
    (conservative: only the explicit allowlist should block a workflow wave).
    """
    if code in _RUFF_ERROR_CODES:
        return "error"
    for prefix in _RUFF_ERROR_PREFIXES:
        if code.startswith(prefix):
            return "error"
    return "warning"


# ---------------------------------------------------------------------------
# Checker path resolution
# ---------------------------------------------------------------------------


#: A single indexed path lookup; anything slower than this is a broken repo.
_GIT_PROVENANCE_TIMEOUT_S = 10


def _find_checker(name: str, project_dir: str | Path | None = None) -> str | None:
    """Return the path to *name*, preferring the active venv's bin directory.

    The venv comes first, and the docstring used to say so while the code did
    the opposite — `shutil.which` ran first, so a system checker shadowed the
    project's every time. That is not a cosmetic ordering: a type checker
    outside the project's environment cannot see the project's dependencies, so
    it reports `Cannot find implementation or library stub for "numpy"` about a
    numpy that is installed. Three of those were blocking vise's own `validate`
    gate, which is the failure `CLAUDE.md` warns about for `pytest` in exactly
    these words — a gate that goes red for environment reasons teaches people
    to set VISE_NODE_GATE_OVERRIDE=1.

    Order: the checked project's own venv, then the active venv, then the venv
    vise is installed in, then PATH.

    The project comes first because that is the only environment guaranteed to
    hold the dependencies the code under check imports. Everything downstream of
    it is a guess — and the gate runs inside the MCP server, whose cwd and venv
    have nothing to do with the repo being validated. That is how this gate came
    to report missing stubs for a numpy the project has installed.
    """
    audited = Path(project_dir).resolve() if project_dir is not None else None

    if audited is not None:
        candidate = audited / ".venv" / "bin" / name
        if _runnable(candidate) and _is_locally_built(candidate, audited):
            return str(candidate)

    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        candidate = Path(virtual_env) / "bin" / name
        if _runnable(candidate):
            return str(candidate)

    # A checkout's own .venv, which is what CLAUDE.md tells contributors to
    # build and what every documented command here runs through.
    cwd = Path.cwd().resolve()
    for base in (cwd, *cwd.parents):
        candidate = base / ".venv" / "bin" / name
        if _runnable(candidate) and _is_locally_built(candidate, audited):
            return str(candidate)

    # Walk up from this module's location looking for a sibling bin/<name>
    # (covers the vise venv layout: <venv>/lib/pythonX.Y/site-packages/...).
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "bin" / name
        if _runnable(candidate):
            return str(candidate)

    found = shutil.which(name)
    if found:
        return found

    return None


def _runnable(candidate: Path) -> bool:
    """A file that can actually be executed.

    ``.exists()`` was the whole test, so a *directory* named ``ruff`` — or a
    non-executable file — was returned as the checker and every later call
    failed somewhere less obvious.
    """
    return candidate.is_file() and os.access(candidate, os.X_OK)


def _is_locally_built(candidate: Path, audited: Path | None) -> bool:
    """False when this binary arrived with the repository under audit.

    ``edit_feedback`` is registered under ``PostToolUse`` for
    ``Edit|Write|MultiEdit`` with no env guard, so the first ``.py`` edit in a
    freshly cloned repository executed whatever that repository had committed at
    ``.venv/bin/ruff``. No vise command, no workflow, no prompt.

    Provenance rather than a blanket opt-in, because the reason project-local
    checkers come first is real: it is the only environment holding the
    dependencies the code under check imports. A ``.venv`` git is tracking came
    *with* the clone; one git does not know about was built by the person
    running vise, which is every ordinary case and stays preferred.

    What this does not cover: a tree that arrived some other way — an unpacked
    archive, a shared mount — is indistinguishable from one built in place.
    ``VISE_TRUST_PROJECT_TOOLS=1`` is the escape hatch for a repository that
    really does vendor its toolchain.
    """
    if audited is None:
        return True
    try:
        candidate.relative_to(audited)
    except ValueError:
        return True  # not inside the tree under audit, so not shipped with it
    if os.environ.get("VISE_TRUST_PROJECT_TOOLS") == "1":
        return True
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", str(candidate)],
            cwd=str(audited), capture_output=True, timeout=_GIT_PROVENANCE_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return True  # cannot tell, and refusing every checker helps nobody
    return tracked.returncode != 0


# ---------------------------------------------------------------------------
# Individual checker runners
# ---------------------------------------------------------------------------


def _run_ruff(file_path: str, project_dir: str | None = None) -> list[dict[str, Any]] | None:
    """Run ruff on *file_path* and return normalised diagnostic dicts, or None on failure."""
    ruff = _find_checker("ruff", project_dir)
    if not ruff:
        return None

    try:
        result = subprocess.run(
            [ruff, "check", "--output-format", "json", file_path],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            shell=False,
        )
        # ruff exits 0 (clean) or 1 (findings) or 2 (internal error)
        if result.returncode == 2:
            log.warning("[lsp_diagnostics] ruff internal error: %s", result.stderr[:300])
            return None

        raw = json.loads(result.stdout or "[]")
        diags: list[dict[str, Any]] = []
        for item in raw:
            code = item.get("code") or ""
            location = item.get("location", {})
            # Always use the allowlist-based classifier — do NOT trust ruff's
            # native "severity" field (see module-level note).
            severity = _severity_for_ruff(code)
            diags.append(
                {
                    "severity": severity,
                    "line": location.get("row", 0),
                    "col": location.get("column", 0),
                    "message": item.get("message", ""),
                    "source": "ruff",
                    "code": code,
                }
            )
        return diags

    except subprocess.TimeoutExpired:
        log.warning("[lsp_diagnostics] ruff timed out on %s", file_path)
        return None
    except Exception as exc:
        log.warning("[lsp_diagnostics] ruff failed: %s", exc)
        return None


def _run_mypy(file_path: str, project_dir: str | None = None) -> list[dict[str, Any]] | None:
    """Run mypy on *file_path* and return normalised diagnostic dicts, or None on failure."""
    mypy = _find_checker("mypy", project_dir)
    if not mypy:
        return None

    try:
        result = subprocess.run(
            [mypy, "--no-error-summary", "--show-column-numbers", file_path],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            shell=False,
        )
        diags: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            # Format: file.py:LINE:COL: SEVERITY: message  [error-code]
            parts = line.split(":", 4)
            if len(parts) < 4:
                continue
            try:
                line_num = int(parts[1].strip())
                col_num = int(parts[2].strip())
                rest = parts[3].strip()
                severity_and_msg = parts[4].strip() if len(parts) > 4 else rest
                if rest.lower().startswith("error"):
                    severity = "error"
                    msg = severity_and_msg
                elif rest.lower().startswith("warning"):
                    severity = "warning"
                    msg = severity_and_msg
                elif rest.lower().startswith("note"):
                    continue  # skip notes
                else:
                    continue
                diags.append(
                    {
                        "severity": severity,
                        "line": line_num,
                        "col": col_num,
                        "message": msg.strip(),
                        "source": "mypy",
                        "code": "",
                    }
                )
            except (ValueError, IndexError):
                continue
        return diags

    except subprocess.TimeoutExpired:
        log.warning("[lsp_diagnostics] mypy timed out on %s", file_path)
        return None
    except Exception as exc:
        log.warning("[lsp_diagnostics] mypy failed: %s", exc)
        return None


def _run_go_vet(project_dir: str, file_path: str) -> list[dict[str, Any]] | None:
    """Run `go vet` on *file_path* and return normalised diagnostic dicts, or None on failure.

    Unlike ruff, `go vet` has no style/lint mode to conflate with correctness
    — every finding it reports (bad Printf verbs, unreachable code, lock
    copies, …) is a genuine bug class, not a preference. So no allowlist is
    needed here: every finding is classified "error" directly.
    """
    go = _find_checker("go")
    if not go:
        return None

    try:
        result = subprocess.run(
            [go, "vet", file_path],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            shell=False,
        )
        # go vet writes findings to stderr, one per line, plus "# <package>"
        # header lines to skip. Two output shapes: a real vet finding
        # ("path/to/file.go:LINE:COL: message") and a module-load failure
        # ("vet: ./main.go:6:2: undefined: X", no package header) — the
        # latter still has a usable file:line:col, just prefixed by "vet: ".
        diags: list[dict[str, Any]] = []
        for line in result.stderr.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("vet: "):
                line = line[len("vet: "):]
            parts = line.split(":", 3)
            if len(parts) < 4:
                continue
            try:
                line_num = int(parts[1].strip())
                col_num = int(parts[2].strip())
            except ValueError:
                continue
            diags.append(
                {
                    "severity": "error",
                    "line": line_num,
                    "col": col_num,
                    "message": parts[3].strip(),
                    "source": "go vet",
                    "code": "",
                }
            )
        # A non-zero exit with nothing parsed means the tool failed for a
        # reason unrelated to findings (module resolution, file outside a
        # module, internal error) — reporting "[]" here would read as a
        # clean pass. Only a genuine zero-diagnostics-and-zero-exit run is a
        # verified clean result; see the ruff precedent above.
        if result.returncode != 0 and not diags:
            log.warning(
                "[lsp_diagnostics] go vet exited %d with nothing parsed on %s: %s",
                result.returncode, file_path, result.stderr[:300],
            )
            return None
        return diags

    except subprocess.TimeoutExpired:
        log.warning("[lsp_diagnostics] go vet timed out on %s", file_path)
        return None
    except Exception as exc:
        log.warning("[lsp_diagnostics] go vet failed: %s", exc)
        return None


def _run_cargo_check(project_dir: str) -> list[dict[str, Any]] | None:
    """Run `cargo check --message-format=json` once for the whole project.

    Rust compiles per-crate — cargo cannot check a single file. Each returned
    diagnostic carries an absolute "file" key so the caller can filter to the
    changed-file set.

    Blocking rule: trust rustc's own `level` field directly (error → blocking,
    warning → cosmetic). Unlike ruff, rustc does not mark style lints as
    "error" — its error level already means "does not compile" — so no
    separate allowlist is needed.
    """
    cargo = _find_checker("cargo")
    if not cargo:
        return None

    try:
        result = subprocess.run(
            [cargo, "check", "--message-format=json"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=_PROJECT_SUBPROCESS_TIMEOUT,
            shell=False,
        )
        diags: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("reason") != "compiler-message":
                continue
            message = obj.get("message") or {}
            level = message.get("level", "")
            if level == "error":
                severity = "error"
            elif level == "warning":
                severity = "warning"
            else:
                continue  # note, help, ice-summary, etc. — not a diagnostic
            spans = message.get("spans") or []
            primary = next((s for s in spans if s.get("is_primary")), spans[0] if spans else {})
            file_name = primary.get("file_name", "")
            if not file_name:
                continue
            code = message.get("code") or {}
            diags.append(
                {
                    "severity": severity,
                    "line": primary.get("line_start", 0),
                    "col": primary.get("column_start", 0),
                    "message": message.get("message", ""),
                    "source": "cargo",
                    "code": code.get("code", "") if isinstance(code, dict) else "",
                    "file": str(Path(project_dir) / file_name),
                }
            )
        # cargo exits non-zero both for "found errors" (diags is non-empty —
        # the normal, working case) and for "could not even start" (e.g. no
        # Cargo.toml in project_dir — empty stdout, diags stays empty). Only
        # the latter is a checker failure; conflating it with a clean pass
        # would report "rust: verified" on a project cargo never actually
        # checked.
        if result.returncode != 0 and not diags:
            log.warning(
                "[lsp_diagnostics] cargo check exited %d with nothing parsed in %s: %s",
                result.returncode, project_dir, result.stderr[:300],
            )
            return None
        return diags

    except subprocess.TimeoutExpired:
        log.warning("[lsp_diagnostics] cargo check timed out in %s", project_dir)
        return None
    except Exception as exc:
        log.warning("[lsp_diagnostics] cargo check failed: %s", exc)
        return None


# tsc --pretty false output: "path/to/file.ts(10,5): error TS2345: message"
_TSC_LINE_RE = re.compile(r"^(.+?)\((\d+),(\d+)\):\s+(error|warning)\s+(TS\d+):\s+(.*)$")


def _run_tsc(project_dir: str) -> list[dict[str, Any]] | None:
    """Run `tsc --noEmit` once for the whole project.

    TypeScript type-checks against the whole program (imports, tsconfig) —
    tsc cannot be pointed at a single file. Each returned diagnostic carries
    an absolute "file" key so the caller can filter to the changed-file set.

    Blocking rule: every diagnostic tsc emits under --noEmit is a genuine
    type error, not a style choice — there is no cosmetic-lint mode to guard
    against here, so tsc's own error/warning label is trusted directly.
    """
    tsc = _find_checker("tsc")
    if not tsc:
        return None

    try:
        result = subprocess.run(
            [tsc, "--noEmit", "--pretty", "false"],
            cwd=project_dir,
            capture_output=True,
            text=True,
            timeout=_PROJECT_SUBPROCESS_TIMEOUT,
            shell=False,
        )
        diags: list[dict[str, Any]] = []
        for line in result.stdout.splitlines():
            m = _TSC_LINE_RE.match(line.strip())
            if not m:
                continue
            file_name, line_s, col_s, severity, code, msg = m.groups()
            diags.append(
                {
                    "severity": severity,
                    "line": int(line_s),
                    "col": int(col_s),
                    "message": msg.strip(),
                    "source": "tsc",
                    "code": code,
                    "file": str(Path(project_dir) / file_name),
                }
            )
        # Same non-zero-with-nothing-parsed guard as cargo/ruff: tsc exits
        # non-zero both for "found type errors" (diags non-empty) and for
        # "could not even run" (e.g. tsconfig.json not at project_dir —
        # TS5058/TS18003 on stderr or an unmatched stdout line, diags empty).
        # The latter must read as unverified, not as a clean pass.
        if result.returncode != 0 and not diags:
            log.warning(
                "[lsp_diagnostics] tsc exited %d with nothing parsed in %s: %s",
                result.returncode, project_dir, (result.stdout or result.stderr)[:300],
            )
            return None
        return diags

    except subprocess.TimeoutExpired:
        log.warning("[lsp_diagnostics] tsc timed out in %s", project_dir)
        return None
    except Exception as exc:
        log.warning("[lsp_diagnostics] tsc failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def lsp_diagnostics(
    project_dir: str,
    file_path: str,
    tools: tuple[str, ...] = ("ruff", "mypy"),
) -> dict[str, Any]:
    """Run available per-file checkers on *file_path* and return normalised diagnostics.

    Checkers tried: ruff, mypy (Python), go_vet (Go) — filtered by *tools*
    (default ``("ruff", "mypy")``; pass ``tools=("ruff",)`` for a fast
    lint-only pass, or ``tools=("go_vet",)`` for a Go file). Each is optional
    — if absent it is silently skipped. If ALL requested tools are absent the
    result is ``{"available": False}``.

    cargo (Rust) and tsc (TypeScript) are NOT available here — they cannot be
    pointed at a single file. Use ``lsp_diagnostics_project`` for those.

    Returns::

        {
            "available": True,
            "diagnostics": [
                {"severity": "error"|"warning", "line": int, "col": int,
                 "message": str, "source": "ruff"|"mypy"|"go vet", "code": str}
            ],
            "tools_run": ["ruff", "mypy"],
            # "reason" only present when available=False
        }
    """
    try:
        all_diags: list[dict[str, Any]] = []
        tools_run: list[str] = []

        if "ruff" in tools:
            ruff_result = _run_ruff(file_path, project_dir)
            if ruff_result is not None:
                all_diags.extend(ruff_result)
                tools_run.append("ruff")

        if "mypy" in tools:
            mypy_result = _run_mypy(file_path, project_dir)
            if mypy_result is not None:
                all_diags.extend(mypy_result)
                tools_run.append("mypy")

        if "go_vet" in tools:
            go_result = _run_go_vet(project_dir, file_path)
            if go_result is not None:
                all_diags.extend(go_result)
                tools_run.append("go_vet")

        if not tools_run:
            return {
                "available": False,
                "diagnostics": [],
                "tools_run": [],
                "reason": (
                    "no diagnostics tool found (install ruff: pip install ruff, "
                    "or mypy: pip install mypy, or go: https://go.dev/dl/)"
                ),
            }

        return {
            "available": True,
            "diagnostics": all_diags,
            "tools_run": tools_run,
        }

    except Exception as exc:
        log.warning("[lsp_diagnostics] unexpected error: %s", exc)
        return {
            "available": False,
            "diagnostics": [],
            "tools_run": [],
            "reason": f"internal error: {exc}",
        }


def lsp_diagnostics_project(
    project_dir: str,
    tools: tuple[str, ...] = ("cargo", "tsc"),
) -> dict[str, Any]:
    """Run available *whole-project* checkers once and return unfiltered diagnostics.

    cargo and tsc cannot be pointed at a single file — they compile/typecheck
    the whole crate/program. Each returned diagnostic carries an absolute
    "file" key so the caller can filter to the changed-file set before
    classification; a diagnostic in an unchanged file must not fail the gate.

    Same fail-soft contract as ``lsp_diagnostics``: absent checker → skipped,
    checker error / unparsable output → logged and skipped, never raises.

    Returns the same shape as ``lsp_diagnostics`` (diagnostics additionally
    carry a "file" key).
    """
    try:
        all_diags: list[dict[str, Any]] = []
        tools_run: list[str] = []

        if "cargo" in tools:
            cargo_result = _run_cargo_check(project_dir)
            if cargo_result is not None:
                all_diags.extend(cargo_result)
                tools_run.append("cargo")

        if "tsc" in tools:
            tsc_result = _run_tsc(project_dir)
            if tsc_result is not None:
                all_diags.extend(tsc_result)
                tools_run.append("tsc")

        if not tools_run:
            return {
                "available": False,
                "diagnostics": [],
                "tools_run": [],
                "reason": (
                    "no whole-project diagnostics tool found (install cargo: "
                    "https://rustup.rs, or tsc: npm install -g typescript)"
                ),
            }

        return {
            "available": True,
            "diagnostics": all_diags,
            "tools_run": tools_run,
        }

    except Exception as exc:
        log.warning("[lsp_diagnostics] project-level unexpected error: %s", exc)
        return {
            "available": False,
            "diagnostics": [],
            "tools_run": [],
            "reason": f"internal error: {exc}",
        }
