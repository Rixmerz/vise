"""Run a brief as a Claude Code subagent — M3 of docs/agent-runtime.md.

This is the only module in the runtime that can spend money, and it is built so
that everything deciding *whether* to spend it stays testable without doing so:
the subprocess call goes through an injected ``runner``, so the whole adapter —
argv construction, timeout handling, output parsing, cost accounting — is driven
in tests by recorded fixtures.

Two things it deliberately does not trust the model about.

**Changed paths come from git, not from the model.** The honesty gates check
what a task wrote against what it was allowed to write; reading that list from
the model's own report would let the one party being checked supply the
evidence. Git knows.

**A missing verdict is not a pass.** A run whose structured block cannot be
found or parsed reports ``inconclusive``. Every other reading — treating a
zero exit as success, or a non-empty transcript as a result — makes a model
that ignored its instructions indistinguishable from one that succeeded.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from vise.runtime.contracts import (
    Artifact,
    FailureKind,
    TaskBrief,
    TaskResult,
    Usage,
    Verdict,
)

#: The fence a worker is told to emit its result in. A named fence rather than
#: "return JSON": a model asked for bare JSON returns prose around JSON often
#: enough that the parser has to find it anyway, and a marker it was told to
#: write is a cheap check that it read the instructions at all.
RESULT_FENCE = "vise-result"

_FENCE_RE = re.compile(
    rf"```{RESULT_FENCE}\s*\n(?P<body>.*?)\n?```", re.DOTALL | re.IGNORECASE
)

#: Default ceiling for one worker. Not "unlimited": a task with no turn limit
#: that misunderstands its brief will spend the whole run's budget discovering
#: that, and the first sign will be the bill.
DEFAULT_MAX_TURNS = 20
DEFAULT_TIMEOUT_S = 900

RESULT_INSTRUCTIONS = f"""
When you are done, emit exactly one fenced block, last thing in your reply:

```{RESULT_FENCE}
{{
  "verdict": "pass" | "fail" | "inconclusive",
  "summary": "one or two sentences for a human",
  "evidence": "the command you ran and its real output, verbatim",
  "checks": "the repo's existing checks you ran, and their real output",
  "artifacts": [{{"kind": "research|plan|finding|test-report|verification|review",
                 "payload": {{}}}}]
}}
```

Rules for that block, which are gates rather than preferences:

- `pass` means the acceptance criteria are met and you can show it. If you could
  not run what would show it, the answer is `inconclusive`, not `pass`.
- `evidence` and `checks` are verbatim terminal output. Not a description of
  output, not "tests pass" — the text the terminal produced. A pass without
  them is refused before anyone reads your summary.
- Do not list the files you changed. That is read from git, not from you.
"""


class AdapterError(Exception):
    """Raised when the CLI itself is unusable — missing, or not executable.

    Fails closed and loudly. A worker that cannot start is a configuration
    problem, and degrading it to "the task failed" would spend the escalation
    ladder proving that `claude` is still not installed.
    """


CompletedProcess = subprocess.CompletedProcess


def _default_runner(argv: Sequence[str], **kwargs: Any) -> CompletedProcess:
    return subprocess.run(list(argv), **kwargs)  # noqa: S603 - argv is built here, not user text


def _worker_env() -> dict[str, str]:
    """The worker's environment: this process's, plus one byte-code setting.

    A Python worker that runs the code it just wrote — which the honesty gates
    require it to do, since a `pass` without evidence is refused — leaves
    ``__pycache__/*.pyc`` beside it. ``git status --porcelain -uall`` reports
    those, the ownership gate reads them as writing outside the task's declared
    paths, and a task that did exactly what it was asked is refused for the
    exhaust of proving it. That cost a whole attempt plus a debugger call in a
    real run.

    Not creating the byproduct is the fix, rather than teaching the gate to
    forgive a list of paths. A forgiveness list is a hole that grows: every
    ecosystem has its own build detritus, and each entry is somewhere a real
    escape can hide. `PYTHONDONTWRITEBYTECODE` costs the worker a little import
    time and leaves the gate exactly as strict as it was.
    """
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


@dataclass
class ClaudeCodeWorker:
    """A ``Worker`` that runs each brief as a headless Claude Code session."""

    project_dir: str | Path = "."
    executable: str = "claude"
    #: Injected so the adapter is testable without spending anything. Signature
    #: matches ``subprocess.run``.
    runner: Callable[..., CompletedProcess] = _default_runner
    max_turns: int = DEFAULT_MAX_TURNS
    timeout_s: int = DEFAULT_TIMEOUT_S
    #: Passed through to the CLI. Left at None so the adapter inherits whatever
    #: the operator configured rather than quietly widening permissions — an
    #: orchestrator that hands every worker `--dangerously-skip-permissions`
    #: has removed the one check a human still had.
    permission_mode: str | None = None
    extra_args: tuple[str, ...] = ()
    #: Recorded for `vise explain`: the argv of every dispatch, in order.
    calls: list[list[str]] = field(default_factory=list)

    # --- the protocol ----------------------------------------------------

    def run(self, brief: TaskBrief) -> TaskResult:
        # The brief's workdir wins over the adapter's. One adapter serves a
        # whole run, and under worktree isolation each task runs somewhere
        # different — a worker pinned to the main tree would write there while
        # its baseline was taken in a worktree, and be refused for a tree it
        # never touched.
        workdir = brief.workdir or str(self.project_dir)
        before = _tracked_paths(workdir)
        argv = self.build_argv(brief)
        self.calls.append(list(argv))
        timeout = brief.budget.timeout_s or self.timeout_s
        try:
            proc = self.runner(
                argv,
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=_worker_env(),
            )
        except subprocess.TimeoutExpired:
            return TaskResult(
                task_id=brief.task_id,
                verdict=Verdict.INCONCLUSIVE,
                summary=f"the worker exceeded its {timeout}s timeout",
                classification=FailureKind.ENVIRONMENT_BUG,
                model=brief.model,
                effort=brief.effort,
                usage=Usage(wall_time_s=float(timeout)),
            )
        except FileNotFoundError as exc:
            raise AdapterError(
                f"{self.executable!r} is not on PATH — a worker that cannot start is a "
                f"configuration problem, not a failed task"
            ) from exc

        after = _tracked_paths(workdir)
        changed = tuple(sorted(_changed(before, after)))
        return self.parse(brief, proc, changed_paths=changed)

    # --- argv ------------------------------------------------------------

    def build_argv(self, brief: TaskBrief) -> list[str]:
        """The command line for one brief.

        ``--model`` and ``--effort`` come from the router; ``--max-turns`` from
        the task's budget, falling back to the adapter's. Every one of them is a
        ceiling somebody chose, and none of them defaults to absent.
        """
        argv = [
            self.executable,
            "-p", self.compose_prompt(brief),
            "--model", brief.model,
            "--output-format", "json",
        ]
        if brief.effort:
            argv += ["--effort", brief.effort]
        turns = brief.budget.max_turns or self.max_turns
        if turns:
            argv += ["--max-turns", str(turns)]
        if self.permission_mode:
            argv += ["--permission-mode", self.permission_mode]
        argv += list(self.extra_args)
        return argv

    def compose_prompt(self, brief: TaskBrief) -> str:
        """The brief, plus the contract for how to answer it."""
        parts = [brief.render(), "", RESULT_INSTRUCTIONS.strip()]
        if brief.tools_blocked:
            parts.insert(
                1,
                "you may not use these tools: " + ", ".join(brief.tools_blocked),
            )
        return "\n".join(parts)

    # --- parsing ---------------------------------------------------------

    def parse(
        self,
        brief: TaskBrief,
        proc: CompletedProcess,
        *,
        changed_paths: tuple[str, ...] = (),
    ) -> TaskResult:
        """Turn one CLI invocation into a TaskResult."""
        envelope = _load_json(proc.stdout)
        usage = _usage_from(envelope)
        text = _result_text(envelope, proc)

        if envelope is None:
            return TaskResult(
                task_id=brief.task_id,
                verdict=Verdict.INCONCLUSIVE,
                summary=(
                    f"the CLI produced no JSON envelope (exit {proc.returncode}); "
                    f"stderr: {(proc.stderr or '').strip()[:300]}"
                ),
                classification=FailureKind.ENVIRONMENT_BUG,
                changed_paths=changed_paths,
                usage=usage,
                model=brief.model,
                effort=brief.effort,
            )

        if envelope.get("is_error"):
            return TaskResult(
                task_id=brief.task_id,
                verdict=Verdict.INCONCLUSIVE,
                summary=f"the session errored: {str(envelope.get('subtype') or text)[:300]}",
                classification=FailureKind.ENVIRONMENT_BUG,
                changed_paths=changed_paths,
                usage=usage,
                model=brief.model,
                effort=brief.effort,
            )

        block = extract_result_block(text)
        if block is None:
            # It ran, it said things, and it did not answer in the shape it was
            # told to. Reading that as a pass would make a model that ignored
            # its instructions indistinguishable from one that succeeded.
            return TaskResult(
                task_id=brief.task_id,
                verdict=Verdict.INCONCLUSIVE,
                summary=(
                    "the worker emitted no vise-result block, so it reported no "
                    "verdict — treated as inconclusive rather than read out of prose"
                ),
                evidence=text[-2000:],
                changed_paths=changed_paths,
                usage=usage,
                model=brief.model,
                effort=brief.effort,
            )

        verdict = _verdict_of(block)
        return TaskResult(
            task_id=brief.task_id,
            verdict=verdict,
            summary=str(block.get("summary") or "")[:2000],
            evidence=str(block.get("evidence") or ""),
            checks=str(block.get("checks") or ""),
            changed_paths=changed_paths,
            artifacts=_artifacts_of(block, brief),
            usage=usage,
            classification=_classification_of(block, verdict),
            model=brief.model,
            effort=brief.effort,
        )


# --- helpers --------------------------------------------------------------


def extract_result_block(text: str) -> dict[str, Any] | None:
    """The parsed ``vise-result`` block, or None when there is not exactly one.

    None on *two or more* as well as on zero: a reply carrying two verdicts has
    not decided, and picking one is inventing the decision it did not make.
    """
    if not text:
        return None
    matches = _FENCE_RE.findall(text)
    if len(matches) != 1:
        return None
    try:
        parsed = json.loads(matches[0])
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _verdict_of(block: Mapping[str, Any]) -> Verdict:
    raw = block.get("verdict")
    if isinstance(raw, str):
        try:
            return Verdict(raw.strip().lower())
        except ValueError:
            pass
    return Verdict.INCONCLUSIVE


def _classification_of(block: Mapping[str, Any], verdict: Verdict) -> FailureKind | None:
    if verdict is Verdict.PASS:
        return None
    raw = block.get("classification") or block.get("kind")
    if not isinstance(raw, str):
        return None
    try:
        return FailureKind(raw.strip().lower())
    except ValueError:
        return None


def _artifacts_of(block: Mapping[str, Any], brief: TaskBrief) -> tuple[Artifact, ...]:
    raw = block.get("artifacts")
    if not isinstance(raw, list):
        return ()
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        payload = item.get("payload")
        if not isinstance(kind, str) or not kind.strip():
            continue
        out.append(Artifact(
            run_id=brief.run_id,
            task_id=brief.task_id.split("::")[0],
            kind=kind.strip(),
            payload=payload if isinstance(payload, dict) else {"value": payload},
        ))
    return tuple(out)


def _load_json(stdout: str | None) -> dict[str, Any] | None:
    if not stdout:
        return None
    try:
        parsed = json.loads(stdout)
    except (ValueError, TypeError):
        # Some invocations emit a JSON object per line. Take the last one that
        # parses — the CLI's final result message.
        for line in reversed(stdout.strip().splitlines()):
            try:
                candidate = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(candidate, dict):
                return candidate
        return None
    if isinstance(parsed, list):
        dicts = [p for p in parsed if isinstance(p, dict)]
        return dicts[-1] if dicts else None
    return parsed if isinstance(parsed, dict) else None


def _result_text(envelope: Mapping[str, Any] | None, proc: CompletedProcess) -> str:
    if envelope is not None:
        for key in ("result", "text", "content"):
            value = envelope.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return proc.stdout or ""


def _usage_from(envelope: Mapping[str, Any] | None) -> Usage:
    if not envelope:
        return Usage()
    usage = envelope.get("usage")
    usage = usage if isinstance(usage, Mapping) else {}
    duration_ms = envelope.get("duration_ms")
    return Usage(
        tokens_in=_int(usage.get("input_tokens")),
        tokens_out=_int(usage.get("output_tokens")),
        cost_usd=_float(envelope.get("total_cost_usd") or envelope.get("cost_usd")),
        wall_time_s=_float(duration_ms) / 1000.0 if duration_ms else 0.0,
    )


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _tracked_paths(project_dir: str | Path) -> dict[str, str] | None:
    """Every path git reports as changed, mapped to its status code.

    None when git cannot answer. The caller reads None as "cannot tell", and
    reports no changed paths rather than an empty list that would read as
    "nothing changed".
    """
    if not shutil.which("git"):
        return None
    try:
        proc = subprocess.run(
            # -uall, not the default: plain --porcelain collapses an untracked
            # directory to `src/`, so a task that creates the first file under
            # a new directory reports `src/` as its changed path and the
            # ownership gate refuses `src/auth/**` work for writing outside its
            # claim. Found by an end-to-end run, not by a unit test — every
            # unit test wrote into a directory that already existed.
            ["git", "status", "--porcelain", "-uall"],
            cwd=str(project_dir), capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if len(line) > 3:
            out[line[3:].strip()] = line[:2]
    return out


def _changed(before: dict[str, str] | None, after: dict[str, str] | None) -> set[str]:
    """Paths whose git status appeared or changed while the worker ran."""
    if before is None or after is None:
        return set()
    moved = {p for p, code in after.items() if before.get(p) != code}
    # A path that was dirty and is now clean was reverted or committed — still
    # a change this worker made, and still its business to have declared.
    return moved | (set(before) - set(after))
