"""What a worker is shown — see docs/worker-contract.md § The brief.

Handing every worker the whole session is the default that makes multi-agent
orchestration cost more than doing the work serially, and it degrades quality on
top: the three relevant files compete with forty that are not.

So the resolver assembles a *bounded* brief context from four sources, cheapest
signal first:

  ownership   the files this task may write, so it knows its own surface
  experience  what vise already learned about those files, one line each
  diff        what the run has changed so far, as names and counts, not content
  inputs      upstream artifacts — supplied by the scheduler, not here

Everything is capped, and the caps are the point. A resolver that dumps two
hundred paths has rebuilt the problem it exists to solve, just with extra steps.
Every truncation says how much it dropped, because a silently truncated context
is one the worker will confidently reason from as though it were complete.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from vise.runtime import ownership as _own

#: Caps. Small on purpose. Raise one only with a measurement that says the
#: worker needed what was cut.
MAX_FILES = 40
MAX_EXPERIENCE = 5
MAX_DIFF_PATHS = 20
GIT_TIMEOUT_S = 10

#: Directories never worth a worker's attention, and expensive to walk.
_SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".tox", "target",
    "vendor", ".next", ".idea", ".vscode", "site-packages",
})


@dataclass
class ContextResolver:
    """Turns a task into the handful of lines a worker actually needs.

    Every source degrades to nothing rather than raising. A context line is an
    aid; a resolver that can take the run down because git was slow has
    inverted its own importance. This is the one place in the runtime that
    follows the *hook* contract rather than the gate contract, and it does so
    because nothing here is a gate — no decision is made from its output.
    """

    project_dir: str | Path
    max_files: int = MAX_FILES
    max_experience: int = MAX_EXPERIENCE
    max_diff_paths: int = MAX_DIFF_PATHS
    include_experience: bool = True
    include_diff: bool = True

    def resolve(self, task: Any) -> tuple[str, ...]:
        """The context block for one task, already bounded and ordered."""
        lines: list[str] = []
        owned = self.owned_files(task)
        lines.extend(self._render_owned(task, owned))
        if self.include_experience:
            lines.extend(self.experience_lines(owned))
        if self.include_diff:
            lines.extend(self.diff_lines())
        return tuple(lines)

    # --- sources ---------------------------------------------------------

    def owned_files(self, task: Any) -> list[str]:
        """Existing files inside the task's ownership, capped and sorted.

        A task that owns everything gets no file list at all. Walking a whole
        repository to hand a worker forty arbitrary paths is worse than handing
        it none: the paths look like a selection, and they are not.
        """
        patterns = getattr(task, "ownership", ()) or ()
        if not patterns or _own.normalize(patterns) == _own.OWNS_EVERYTHING:
            return []
        root = Path(self.project_dir)
        if not root.is_dir():
            return []
        found: list[str] = []
        try:
            for path in sorted(root.rglob("*")):
                if len(found) > self.max_files:
                    break
                if any(part in _SKIP_DIRS for part in path.parts):
                    continue
                if not path.is_file():
                    continue
                rel = path.relative_to(root).as_posix()
                if _own.matches(rel, patterns):
                    found.append(rel)
        except (OSError, ValueError):
            return found
        return found

    def experience_lines(self, paths: Sequence[str]) -> list[str]:
        """What vise already learned about these files, one line each.

        This is the reason the memory exists and the cheapest thing in the
        brief: "this module had timezone-offset problems" reaches the worker as
        one line instead of as a thread it has to re-read.
        """
        if not paths:
            return []
        try:
            from vise.engines.experience_memory import get_project_experience_store

            store = get_project_experience_store(str(self.project_dir))
        except Exception:  # noqa: BLE001 - context is an aid, never a gate
            return []

        seen: dict[str, float] = {}
        for path in paths[: self.max_files]:
            try:
                hits = store.query(path, top_n=self.max_experience)
            except Exception:  # noqa: BLE001 - same contract
                continue
            for entry, score in hits:
                text = (entry.description or "").strip()
                if text and score > seen.get(text, 0.0):
                    seen[text] = score
        if not seen:
            return []
        ranked = sorted(seen.items(), key=lambda kv: -kv[1])[: self.max_experience]
        return ["what vise already learned about this area:"] + [
            f"  - {text}" for text, _ in ranked
        ]

    def diff_lines(self) -> list[str]:
        """What the run has changed so far — names and counts, never content.

        A worker that needs a file's contents can read the file; it has the
        tools. What it cannot get on its own is the knowledge that another task
        already touched this area in this run.
        """
        try:
            proc = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self.project_dir), capture_output=True, text=True,
                timeout=GIT_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if proc.returncode != 0 or not proc.stdout.strip():
            return []
        entries = [line[3:].strip() for line in proc.stdout.splitlines() if len(line) > 3]
        if not entries:
            return []
        shown = entries[: self.max_diff_paths]
        out = ["already modified in this working tree:"] + [f"  {p}" for p in shown]
        if len(entries) > len(shown):
            out.append(f"  (+{len(entries) - len(shown)} more not listed)")
        return out

    # --- rendering -------------------------------------------------------

    def _render_owned(self, task: Any, owned: Sequence[str]) -> list[str]:
        patterns = getattr(task, "ownership", ()) or ()
        if not patterns:
            return []
        if not owned:
            return [f"no existing files match {', '.join(patterns)} — this is new ground"]
        shown = list(owned[: self.max_files])
        out = ["files inside your ownership:"] + [f"  {p}" for p in shown]
        if len(owned) > len(shown):
            out.append(
                f"  (+{len(owned) - len(shown)} more — your claim is wider than this "
                f"list; narrow the ownership or the task)"
            )
        return out
