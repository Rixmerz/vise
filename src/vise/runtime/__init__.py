"""The agent execution plane — see docs/agent-runtime.md.

vise's control plane decides *what process* a change follows. This package
decides *who does the work*: which agent, on which model, at what effort, in
what order, within which budget, and when to stop and ask a person.

Nothing here traverses a graph edge, writes graph state, or overrides a gate.
The control plane stays the authority; the runtime asks and reports.

Nothing here calls a model either — not yet. Every module in this milestone runs
offline and deterministically, and the worker is a protocol with a recording
mock behind it. That is the acceptance criterion for the contracts: a contract
no mock can exercise end to end is a contract that has not been specified.
"""
from __future__ import annotations

__all__ = [
    "Artifact",
    "Attempt",
    "Criticality",
    "Complexity",
    "FailureKind",
    "RunBudget",
    "RunSpec",
    "TaskBrief",
    "TaskBudget",
    "TaskResult",
    "TaskState",
    "Usage",
    "Verdict",
]

from vise.runtime.contracts import (
    Artifact,
    Attempt,
    Complexity,
    Criticality,
    FailureKind,
    RunBudget,
    RunSpec,
    TaskBrief,
    TaskBudget,
    TaskResult,
    TaskState,
    Usage,
    Verdict,
)
