"""lsp_diagnostics — stateless shell-out diagnostics (ruff + mypy).

Ported from jig's engines/lsp_diagnostics.py (no multilspy dependency).
Shells out to ruff / mypy when present on PATH (or in the venv), degrades
gracefully when absent.

Public API
----------
lsp_diagnostics(project_dir, file_path) -> dict

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
import shutil
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SUBPROCESS_TIMEOUT: float = 30.0

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


def _find_checker(name: str) -> str | None:
    """Return the path to *name*, preferring the active venv's bin directory.

    Uses system PATH first, then the VIRTUAL_ENV env var, then the venv that
    vise itself is installed in.
    """
    found = shutil.which(name)
    if found:
        return found

    virtual_env = os.environ.get("VIRTUAL_ENV")
    if virtual_env:
        candidate = Path(virtual_env) / "bin" / name
        if candidate.exists():
            return str(candidate)

    # Walk up from this module's location looking for a sibling bin/<name>
    # (covers the vise venv layout: <venv>/lib/pythonX.Y/site-packages/...).
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "bin" / name
        if candidate.exists():
            return str(candidate)

    return None


# ---------------------------------------------------------------------------
# Individual checker runners
# ---------------------------------------------------------------------------


def _run_ruff(file_path: str) -> list[dict[str, Any]] | None:
    """Run ruff on *file_path* and return normalised diagnostic dicts, or None on failure."""
    ruff = _find_checker("ruff")
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


def _run_mypy(file_path: str) -> list[dict[str, Any]] | None:
    """Run mypy on *file_path* and return normalised diagnostic dicts, or None on failure."""
    mypy = _find_checker("mypy")
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def lsp_diagnostics(
    project_dir: str,
    file_path: str,
    tools: tuple[str, ...] = ("ruff", "mypy"),
) -> dict[str, Any]:
    """Run available checkers on *file_path* and return normalised diagnostics.

    Checkers tried (in order): ruff, mypy — filtered by *tools* (default both;
    pass ``tools=("ruff",)`` for a fast lint-only pass).  Each is optional — if
    absent it is silently skipped.  If ALL are absent the result is
    ``{"available": False}``.

    Returns::

        {
            "available": True,
            "diagnostics": [
                {"severity": "error"|"warning", "line": int, "col": int,
                 "message": str, "source": "ruff"|"mypy", "code": str}
            ],
            "tools_run": ["ruff", "mypy"],
            # "reason" only present when available=False
        }
    """
    try:
        all_diags: list[dict[str, Any]] = []
        tools_run: list[str] = []

        if "ruff" in tools:
            ruff_result = _run_ruff(file_path)
            if ruff_result is not None:
                all_diags.extend(ruff_result)
                tools_run.append("ruff")

        if "mypy" in tools:
            mypy_result = _run_mypy(file_path)
            if mypy_result is not None:
                all_diags.extend(mypy_result)
                tools_run.append("mypy")

        if not tools_run:
            return {
                "available": False,
                "diagnostics": [],
                "tools_run": [],
                "reason": (
                    "no diagnostics tool found (install ruff: pip install ruff, "
                    "or mypy: pip install mypy)"
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
