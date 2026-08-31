"""Which tasks may run at the same time — see docs/scheduler.md § Ownership.

Two agents editing the same file concurrently produce a diff neither of them
wrote, and no amount of reviewing after the fact recovers what the intent was.
Ownership is the admission rule that prevents it: every writing task declares
the paths it may touch, and the scheduler refuses to dispatch a task whose
claims intersect one already in flight.

This is not a lock. Nothing here blocks a write or rejects one after the fact;
it decides, before dispatch, whether two tasks *could* collide. Enforcement
after the fact is ``honesty.check_result``, which is a different question with a
different answer.

**Every ambiguity here resolves toward "these conflict."** A false conflict
costs wall-clock — the two tasks run in sequence instead of together. A missed
conflict costs a corrupted working tree and a diff nobody can attribute. Those
are not comparable, so the tie-breaks below are not symmetric and should not be
"fixed" to be.
"""
from __future__ import annotations

from functools import lru_cache

#: A task that declares nothing owns everything. This is the safe reading — the
#: alternative is a task with no declared claims silently colliding with every
#: other one — and it is a visible cost, since the plan renders it as a wave of
#: one rather than hiding it.
OWNS_EVERYTHING: tuple[str, ...] = ("**",)


def normalize(patterns: object) -> tuple[str, ...]:
    """Coerce a task's declared ownership into comparable glob patterns.

    An empty or unusable declaration becomes ``**`` — see OWNS_EVERYTHING.
    """
    if isinstance(patterns, str):
        patterns = [patterns]
    if not isinstance(patterns, (list, tuple)) or not patterns:
        return OWNS_EVERYTHING
    out: list[str] = []
    for raw in patterns:
        p = str(raw).strip().replace("\\", "/")
        while p.startswith("./"):
            p = p[2:]
        p = p.strip("/") if p.endswith("/") else p.lstrip("/")
        if not p:
            continue
        # A trailing slash means "this directory", which means its whole subtree.
        if str(raw).rstrip().endswith("/"):
            p = f"{p}/**"
        out.append(p)
    return tuple(out) if out else OWNS_EVERYTHING


@lru_cache(maxsize=4096)
def _segment_intersect(a: str, b: str) -> bool:
    """Can two single-segment glob patterns match a common string?

    Not ``fnmatch`` in either direction: ``a*`` and ``*b`` both match ``ab``,
    and neither fnmatches the other, so the obvious implementation reports no
    conflict for a pair that has one.
    """
    if not a and not b:
        return True
    if not a:
        return all(c == "*" for c in b)
    if not b:
        return all(c == "*" for c in a)
    ca, cb = a[0], b[0]
    # Character classes are not interpreted. Treating '[abc]' literally would
    # say '[abc]' and 'a' cannot collide, which is the unsafe direction, so a
    # class is read as "could be anything".
    if ca == "[" or cb == "[":
        return True
    if ca == "*":
        return _segment_intersect(a[1:], b) or _segment_intersect(a, b[1:])
    if cb == "*":
        return _segment_intersect(a, b[1:]) or _segment_intersect(a[1:], b)
    if ca == "?" or cb == "?" or ca == cb:
        return _segment_intersect(a[1:], b[1:])
    return False


@lru_cache(maxsize=4096)
def _path_intersect(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Can two segmented glob patterns match a common path?

    ``**`` matches zero or more whole segments, which is why this recurses on
    both sides rather than zipping.
    """
    if not a and not b:
        return True
    if not a:
        return all(s == "**" for s in b)
    if not b:
        return all(s == "**" for s in a)
    if a[0] == "**":
        return _path_intersect(a[1:], b) or _path_intersect(a, b[1:])
    if b[0] == "**":
        return _path_intersect(a, b[1:]) or _path_intersect(a[1:], b)
    if not _segment_intersect(a[0], b[0]):
        return False
    return _path_intersect(a[1:], b[1:])


def _prefixes(short: tuple[str, ...], long_: tuple[str, ...]) -> bool:
    """True when ``short`` names a directory that contains ``long_``.

    Directional on purpose. ``src/auth`` covers ``src/auth/token.py``; the
    reverse is not true, and a claim on ``src/auth`` is not a claim on ``src``.
    """
    if len(short) >= len(long_):
        return False
    return all(_segment_intersect(x, y) for x, y in zip(short, long_))


def _is_prefix(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """True when either segment list names a directory containing the other.

    This is the bare-directory case: ``src/auth`` versus ``src/auth/token.py``.
    Glob intersection alone says no — one is a file path and the other is not a
    subtree pattern — but a task claiming ``src/auth`` plainly means the
    directory. Symmetric because two *patterns* conflict whichever way round
    they are given. Resolved toward conflict, per the module docstring.
    """
    return _prefixes(a, b) or _prefixes(b, a)


def patterns_conflict(a: str, b: str) -> bool:
    """True when two ownership patterns can both match the same path."""
    sa, sb = tuple(a.split("/")), tuple(b.split("/"))
    return _path_intersect(sa, sb) or _is_prefix(sa, sb)


def conflicts(a: object, b: object) -> bool:
    """True when two ownership declarations may not run concurrently."""
    for pa in normalize(a):
        for pb in normalize(b):
            if patterns_conflict(pa, pb):
                return True
    return False


def matches(path: str, patterns: object) -> bool:
    """True when ``path`` falls inside an ownership declaration.

    Used by ``honesty`` to decide whether a written file escaped its claim. A
    concrete path has no wildcards, so this is ordinary glob matching expressed
    through the same intersection routine — one implementation, one behaviour.
    """
    p = str(path).replace("\\", "/").lstrip("/")
    segments = tuple(p.split("/"))
    for pattern in normalize(patterns):
        pat = tuple(pattern.split("/"))
        # `_prefixes`, not `_is_prefix`: admission is symmetric between two
        # patterns, but a concrete path is only inside a claim one way round.
        # Without this the two questions disagree — admission reads `src/auth`
        # as the subtree and lets the task in, then every write it makes is
        # refused as an escape and the task cannot succeed at anything.
        if _path_intersect(segments, pat) or _prefixes(pat, segments):
            return True
    return False


def escaped(changed_paths: object, patterns: object) -> tuple[str, ...]:
    """The written paths that fall outside the declared ownership.

    Returned rather than counted: a caller that only learns *how many* files
    escaped cannot report which, and "3 files escaped ownership" is not a
    finding anyone can act on.
    """
    if isinstance(changed_paths, str):
        changed_paths = [changed_paths]
    if not isinstance(changed_paths, (list, tuple)):
        return ()
    return tuple(str(p) for p in changed_paths if not matches(str(p), patterns))


def partition(claims: dict[str, object]) -> list[list[str]]:
    """Split task ids into groups that may run concurrently.

    Greedy and order-preserving: walk the ids in the order given and drop each
    into the first group where it conflicts with nothing. Greedy is not optimal
    — a smarter packing could sometimes use fewer groups — but it is stable and
    explainable, and a scheduler whose grouping shifts between runs for reasons
    nobody can reconstruct is worse than one that occasionally runs a group
    longer than it had to.
    """
    groups: list[list[str]] = []
    for task_id, claim in claims.items():
        for group in groups:
            if all(not conflicts(claim, claims[other]) for other in group):
                group.append(task_id)
                break
        else:
            groups.append([task_id])
    return groups
