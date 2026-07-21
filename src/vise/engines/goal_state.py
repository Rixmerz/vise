"""Durable goal state for vise's autonomous loop."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional


class GoalStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETE = "complete"
    ABANDONED = "abandoned"


class Complexity(str, Enum):
    SIMPLE = "simple"      # target_confidence 1.0
    MEDIUM = "medium"      # 0.95
    COMPLEX = "complex"    # 0.90
    UNKNOWN = "unknown"    # 0.90 default


_DEFAULT_TARGET: dict[Complexity, float] = {
    Complexity.SIMPLE: 1.0,
    Complexity.MEDIUM: 0.95,
    Complexity.COMPLEX: 0.90,
    Complexity.UNKNOWN: 0.90,
}


@dataclass
class ValidatorRecord:
    name: str
    passed: bool
    confidence_contribution: float  # 0.0–1.0 — weight * passed
    weight: float                    # 0.0–1.0
    evidence: str
    at: str  # ISO8601
    source: str = "mechanical"       # mechanical | asserted — self-grading guard
    exit_code: int | None = None     # process exit code where meaningful
    full_output_path: str = ""       # path to persisted full stdout+stderr log


@dataclass
class GoalHistoryEvent:
    event: str   # started | rotated | restarted | validator_run | confidence_update | complete | abandoned
    at: str
    detail: str = ""


@dataclass
class Goal:
    id: str
    project_dir: str
    goal: str
    acceptance_criteria: list[str]
    target_confidence: float
    complexity: str
    status: str
    started_at: str
    updated_at: str
    attempts: int = 0
    confidence: float = 0.0
    bootstrapped: bool = False
    bootstrap_completed_at: str = ""
    validator_configs: list[dict] = field(default_factory=list)
    last_results: list[ValidatorRecord] = field(default_factory=list)
    history: list[GoalHistoryEvent] = field(default_factory=list)
    preferred_model: str = ""


def _goal_dir() -> Path:
    p = Path(os.environ.get("VISE_GOAL_DIR", Path.home() / ".local/share/vise/goal"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _path_for(project_dir: str) -> Path:
    name = Path(project_dir).resolve().name or "unnamed"
    return _goal_dir() / f"{name}.json"


def default_target_confidence(complexity: Complexity | str) -> float:
    if isinstance(complexity, str):
        try:
            complexity = Complexity(complexity)
        except ValueError:
            complexity = Complexity.UNKNOWN
    return _DEFAULT_TARGET[complexity]


def set_goal(
    project_dir: str,
    goal: str,
    acceptance_criteria: list[str] | None = None,
    target_confidence: float | None = None,
    complexity: str = Complexity.UNKNOWN.value,
    validator_configs: list[dict] | None = None,
    preferred_model: str = "",
) -> Goal:
    now = datetime.now(timezone.utc).isoformat()
    g = Goal(
        id=str(uuid.uuid4()),
        project_dir=project_dir,
        goal=goal,
        acceptance_criteria=acceptance_criteria or [],
        target_confidence=target_confidence if target_confidence is not None else default_target_confidence(complexity),
        complexity=complexity,
        status=GoalStatus.ACTIVE.value,
        started_at=now,
        updated_at=now,
        validator_configs=validator_configs or [],
        history=[GoalHistoryEvent(event="started", at=now, detail=goal[:120])],
        preferred_model=preferred_model,
    )
    _write(g)
    return g


def get_goal(project_dir: str) -> Optional[Goal]:
    p = _path_for(project_dir)
    if not p.exists():
        return None
    raw = json.loads(p.read_text(encoding="utf-8"))
    return _from_dict(raw)


def clear_goal(project_dir: str) -> bool:
    p = _path_for(project_dir)
    if not p.exists():
        return False
    p.unlink()
    return True


def update_goal(project_dir: str, **fields) -> Optional[Goal]:
    g = get_goal(project_dir)
    if not g:
        return None
    for k, v in fields.items():
        if hasattr(g, k):
            setattr(g, k, v)
    g.updated_at = datetime.now(timezone.utc).isoformat()
    _write(g)
    return g


def append_history(project_dir: str, event: str, detail: str = "") -> Optional[Goal]:
    g = get_goal(project_dir)
    if not g:
        return None
    g.history.append(GoalHistoryEvent(event=event, at=datetime.now(timezone.utc).isoformat(), detail=detail))
    g.updated_at = datetime.now(timezone.utc).isoformat()
    _write(g)
    return g


def mark_complete(project_dir: str) -> Optional[Goal]:
    return update_goal(project_dir, status=GoalStatus.COMPLETE.value)


def mark_abandoned(project_dir: str) -> Optional[Goal]:
    return update_goal(project_dir, status=GoalStatus.ABANDONED.value)


def _write(g: Goal) -> None:
    p = _path_for(g.project_dir)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(g), indent=2, default=str), encoding="utf-8")
    tmp.replace(p)


def _from_dict(d: dict) -> Goal:
    d = dict(d)
    d["last_results"] = [ValidatorRecord(**r) for r in d.get("last_results", [])]
    d["history"] = [GoalHistoryEvent(**h) for h in d.get("history", [])]
    d.setdefault("bootstrapped", False)
    d.setdefault("bootstrap_completed_at", "")
    return Goal(**d)
