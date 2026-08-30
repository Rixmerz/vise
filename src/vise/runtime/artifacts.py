"""What one worker hands the next — see docs/worker-contract.md § Artifacts.

Workers do not read each other's transcripts. A downstream task receives the
*conclusions* of its dependencies: a JSON payload of a declared kind, addressed
by the task that produced it.

**Writes here raise.** Every other side-channel in vise — telemetry, snapshots,
hooks — swallows its errors, because a record that can break a session is worse
than no record. An artifact is not a record: it is the next task's input. A store
that silently drops one produces a downstream worker briefed on nothing, which
fails much later and looks like a model problem.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from vise.runtime.contracts import Artifact

#: Kinds the runtime itself produces. Not a closed set — a workflow may declare
#: its own — but these are the ones the bundled roles know how to consume.
KNOWN_KINDS = (
    "research",
    "plan",
    "finding",
    "test-report",
    "verification",
    "review",
    "diff",
)

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _slug(value: str) -> str:
    """Filesystem-safe component. Rejects empty rather than inventing a name."""
    out = _SAFE.sub("-", value.strip())
    if not out or out.strip(".-") == "":
        raise ValueError(f"unusable artifact path component: {value!r}")
    return out


class ArtifactStore:
    """Filesystem-backed artifact store, one directory per run."""

    def __init__(self, root: Path | str, run_id: str) -> None:
        self.run_id = run_id
        self.root = Path(root) / "runs" / _slug(run_id) / "artifacts"

    def _path(self, task_id: str, kind: str) -> Path:
        return self.root / f"{_slug(task_id)}.{_slug(kind)}.json"

    def put(self, artifact: Artifact) -> Path:
        """Write one artifact, replacing any previous one of the same kind.

        Replacing is right: a task's second attempt supersedes its first, and
        keeping both would leave the next worker to guess which is current. The
        superseded payloads live in the attempt history, which is where a reader
        asking "what changed between attempts" is already looking.
        """
        path = self._path(artifact.task_id, artifact.kind)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(artifact.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return path

    def put_all(self, artifacts: Iterable[Artifact]) -> list[Path]:
        return [self.put(a) for a in artifacts]

    def get(self, task_id: str, kind: str) -> Artifact | None:
        path = self._path(task_id, kind)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Unreadable is not absent. Absent means the upstream task produced
            # nothing; unreadable means it produced something we have lost, and
            # briefing the next worker as though nothing existed hides that.
            raise ArtifactError(f"artifact at {path} is unreadable: {exc}") from exc
        return Artifact.from_dict(data)

    def for_task(self, task_id: str) -> tuple[Artifact, ...]:
        prefix = f"{_slug(task_id)}."
        if not self.root.is_dir():
            return ()
        out = []
        for path in sorted(self.root.glob(f"{prefix}*.json")):
            kind = path.name[len(prefix): -len(".json")]
            art = self.get(task_id, kind)
            if art is not None:
                out.append(art)
        return tuple(out)

    def inputs_for(self, dependencies: Iterable[str]) -> tuple[Artifact, ...]:
        """Every artifact produced by the tasks this one depends on."""
        out: list[Artifact] = []
        for dep in dependencies:
            out.extend(self.for_task(dep))
        return tuple(out)


class ArtifactError(Exception):
    """Raised when an artifact exists but cannot be read. Never swallowed."""
