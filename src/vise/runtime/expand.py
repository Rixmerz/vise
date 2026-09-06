"""One task per item of an upstream result — docs/scheduler.md § Expansion.

A ``dag`` node's tasks are a list written before anyone has seen the data.
This module is the half of "one agent per item" that the YAML cannot say:
given a template task and the list an upstream task produced, the children;
given the children's outcomes, the collection a downstream task reads. All of
it is pure. Reading the list from the store, deciding when to expand and when
to join, and dispatching the children are the scheduler's, and the children
are ordinary tasks once they exist — routed, priced, gated, verified and
escalated like any other.

Three rules run through it:

- **The width comes from an artifact, never from prose.** A worker says what
  the items are by emitting a payload list; nothing here reads a transcript.
- **The cap is reported, never silent.** A list longer than ``max_items`` is
  cut, and the cut is a number on the record.
- **Found nothing and could not look are different facts.** An empty list is
  a join with zero children; a missing key is a block with a reason.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

from vise.engines.graph_engine import Task
from vise.runtime.contracts import Artifact

#: How many children one expansion may create when the task did not say.
#: Not 250 — a child is a ``claude -p`` session on this machine, not a VM on
#: someone's fleet, and a default that could spend 250× one task's estimate on
#: one list is a default nobody would defend after the bill.
DEFAULT_MAX_ITEMS = 25

#: The artifact kind a join writes under the template's id.
COLLECTION_KIND = "collection"

#: ``{item}`` and ``{item.key}``. Nothing else in braces is touched, so a
#: prompt that quotes a code block is not mangled by a formatter.
_PLACEHOLDER = re.compile(r"\{item(?:\.([A-Za-z0-9_-]+))?\}")


@dataclass(frozen=True)
class Listing:
    """What the source's artifacts said about the key.

    ``items`` is ``None`` when the key was not there — as opposed to there and
    empty, which is a list of nothing and a finding in its own right.
    ``carried`` names the keys that were there, so the reason for a block can
    say what the source did produce.
    """

    items: tuple[Any, ...] | None
    carried: tuple[str, ...] = ()

    @property
    def found(self) -> bool:
        return self.items is not None


@dataclass(frozen=True)
class Expansion:
    """The children a template became, and what the cap left out."""

    children: tuple[Task, ...]
    items: tuple[Any, ...]
    dropped: int = 0


def cap_for(task: Task) -> int:
    """The width this task may reach. Never zero and never unlimited."""
    declared = task.for_each.max_items if task.for_each is not None else 0
    return declared if declared > 0 else DEFAULT_MAX_ITEMS


def listing(artifacts: Iterable[Artifact], key: str) -> Listing:
    """The list under ``key`` in the first artifact that carries it as a list."""
    carried: set[str] = set()
    for art in artifacts:
        payload = art.payload if isinstance(art.payload, Mapping) else {}
        carried.update(str(k) for k in payload)
        value = payload.get(key)
        if isinstance(value, list):
            return Listing(tuple(value), tuple(sorted(carried)))
    return Listing(None, tuple(sorted(carried)))


def child_id(template_id: str, index: int) -> str:
    """``gather[3]``. Brackets, not ``::``: the double colon marks a task's
    auxiliary agents — its verifier, its debugger, its re-specifier. A child is
    not auxiliary to the template; it is the work."""
    return f"{template_id}[{index}]"


def text_of(value: Any) -> str:
    """An item as text: a string verbatim, anything else as stable JSON.

    Public because convergence keys findings the same way an expansion renders
    them, and two spellings of "the item as text" would drift.
    """
    return value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)


def render(text: str, item: Any) -> str:
    """Substitute ``{item}`` and ``{item.key}``; leave every other brace alone."""

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key is None:
            return text_of(item)
        if isinstance(item, Mapping) and key in item:
            return text_of(item[key])
        return match.group(0)

    return _PLACEHOLDER.sub(substitute, text)


def item_line(index: int, total: int, item: Any) -> str:
    """The line every child's prompt ends with, placeholder or not, so a
    template that never wrote ``{item}`` still tells its child which one it is."""
    return f"item {index}/{total}: {text_of(item)}"


def expand(template: Task, items: Sequence[Any], *, cap: int) -> Expansion:
    """The children of ``template``, one per item up to ``cap``, in order.

    Deterministic by construction — the same template and list produce the
    same ids — which is what lets a resumed run derive its children again and
    find their records already there.

    Every list on the child is a fresh copy. The replanner rewrites a task's
    ``dependencies`` in place, and children sharing one list with the template
    would all acquire each other's re-specification tasks.

    ``for_each`` is the one field cleared: a child must never expand again.
    Everything else carries, ``until`` and ``verifiers`` included, because the
    template describes the work and the children are the work — so a template
    declaring both means "sweep each item until it goes quiet", and one
    declaring a panel means each child gets one.
    """
    kept = tuple(items[:cap])
    total = len(kept)
    children = []
    for index, item in enumerate(kept, start=1):
        prompt = render(template.prompt, item) if template.prompt else ""
        prompt = f"{prompt}\n\n{item_line(index, total, item)}".strip()
        children.append(replace(
            template,
            id=child_id(template.id, index),
            name=f"{template.name} [{index}/{total}]",
            prompt=prompt,
            dependencies=list(template.dependencies),
            outputs=dict(template.outputs),
            tools_blocked=list(template.tools_blocked),
            mcps_enabled=list(template.mcps_enabled),
            ownership=[render(pattern, item) for pattern in template.ownership],
            acceptance=[render(criterion, item) for criterion in template.acceptance],
            for_each=None,
        ))
    return Expansion(tuple(children), kept, len(items) - total)


def collection(
    run_id: str,
    template_id: str,
    *,
    source: str,
    key: str,
    items: Sequence[Any],
    outcomes: Mapping[str, str],
    dropped: int = 0,
) -> Artifact:
    """What a downstream task reads about the whole expansion.

    Small on purpose: the children's own artifacts travel beside it in the
    brief. This one says how wide the fan-out was, what each item was, how
    each child ended, and how many items the cap left out — the number that
    must not go missing.
    """
    child_ids = [child_id(template_id, i) for i in range(1, len(items) + 1)]
    return Artifact(
        run_id=run_id,
        task_id=template_id,
        kind=COLLECTION_KIND,
        payload={
            "source": source,
            "key": key,
            "count": len(items),
            "dropped": dropped,
            "children": [
                {"task_id": cid, "item": item, "state": outcomes.get(cid, "")}
                for cid, item in zip(child_ids, items)
            ],
        },
    )
