"""The rules for writing an experience entry. Two writers, one set of them.

`ExperienceMemoryStore.record` writes what the MCP tools, the node gate and the
runtime record. `hooks/experience_recorder` writes what a commit taught, as its
own interpreter, on every `git commit`. They write the same two files and they
disagreed about what an entry *is*:

=====================  ==============================  =========================
                       store                           commit hook
=====================  ==============================  =========================
identity               (type, file_pattern, domain)    (type, pattern, description)
confidence on repeat   0.95 * (1 - 0.7**n)             min(0.95, conf + 0.1)
confidence when new    0.285                           0.5, fixed
id                     uuid4[:8]                       none
first_seen             set                             never set
scope                  set                             never set
cap                    500, lowest confidence evicted  none
write                  temp file, fsync, rename        `write_text`
=====================  ==============================  =========================

The identity row is the one with teeth, and not because the store grows. The
store's own cross-process merge — `_merged_with_disk`, which is how two writers
are meant to coexist — holds one entry per *store* key and drops the rest of
what it finds on disk. So two commits touching one glob, which the hook kept
apart under its own key, became one entry the next time anything called
`save()`, and the second commit's lesson was gone. Measured, not reasoned:
`test_experience_writer_rules.py` records the two and asserts both survive.

Everything here takes plain dicts and returns them, because the store holds
dataclasses and the hook holds JSON, and a rule that only one representation
can express is a rule the other will reimplement. Standard library only, for
the usual reason: this is imported by something that runs on every commit.
"""
from __future__ import annotations

import re
import uuid
from typing import Any, Mapping

#: What makes two entries the same experience. The store's merge already used
#: this and only this, so it was never really a choice the hook got to make.
DEDUP_FIELDS: tuple[str, ...] = ("type", "file_pattern", "domain")

#: How many entries a store keeps. Applied to the union at save time, so a
#: writer that skipped it was not adding headroom, it was leaving the cap to
#: whichever process saved next.
MAX_ENTRIES = 500

#: Asymptotic confidence: 0.29 → 0.50 → 0.65 → 0.76 → ... capped short of 1.0,
#: because an experience seen ten times is still not a law. A linear +0.1 per
#: sighting reaches the cap in five and cannot distinguish five from fifty.
CONFIDENCE_CAP = 0.95
CONFIDENCE_DECAY = 0.7


def dedup_key(entry: Mapping[str, Any]) -> tuple[str, str, str]:
    """The identity of an experience: same kind, same place, same domain."""
    return tuple(str(entry.get(f) or "") for f in DEDUP_FIELDS)  # type: ignore[return-value]


def confidence_for(occurrences: int) -> float:
    """Confidence after *occurrences* sightings."""
    return min(CONFIDENCE_CAP, CONFIDENCE_CAP * (1 - CONFIDENCE_DECAY ** max(1, occurrences)))


def new_id() -> str:
    """A short id. An entry nobody can address cannot be bumped, gc'd or cited."""
    return str(uuid.uuid4())[:8]


def merge(existing: dict[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    """Fold *incoming* into *existing* in place, and return it.

    `shapes` is the one field that merges additively, and it is the reason this
    is a function rather than a convention: the counts are what a caller
    thresholds on, and prose merged by keeping the longer string discards a
    shorter follow-up silently — an `x1` bumped to `x2` is the same length.
    """
    existing["occurrences"] = int(existing.get("occurrences") or 1) + 1
    existing["confidence"] = confidence_for(existing["occurrences"])
    if incoming.get("last_seen"):
        existing["last_seen"] = incoming["last_seen"]
    if not existing.get("first_seen"):
        existing["first_seen"] = incoming.get("first_seen") or existing.get("last_seen") or ""
    if not existing.get("id"):
        existing["id"] = new_id()

    shapes = dict(existing.get("shapes") or {})
    for shape, count in (incoming.get("shapes") or {}).items():
        shapes[shape] = shapes.get(shape, 0) + count
    existing["shapes"] = shapes

    # Longer wins, which is arbitrary and stays that way: `shapes` carries what
    # is counted, so description is prose again rather than a datastore.
    if len(str(incoming.get("description") or "")) > len(str(existing.get("description") or "")):
        existing["description"] = incoming["description"]
    if incoming.get("resolution") and not existing.get("resolution"):
        existing["resolution"] = incoming["resolution"]

    related = list(existing.get("related_files") or [])
    for path in incoming.get("related_files") or []:
        if path not in related:
            related.append(path)
    existing["related_files"] = related
    return existing


def evict(entries: list[dict[str, Any]], cap: int = MAX_ENTRIES) -> list[dict[str, Any]]:
    """Trim to *cap*, dropping the least confident and then the least recent."""
    if len(entries) <= cap:
        return entries
    ranked = sorted(
        entries,
        key=lambda e: (float(e.get("confidence") or 0.0), str(e.get("last_seen") or "")),
        reverse=True,
    )
    return ranked[:cap]


def upsert(entries: list[dict[str, Any]], incoming: dict[str, Any]) -> list[dict[str, Any]]:
    """Merge *incoming* into a matching entry, or append it as a new one."""
    key = dedup_key(incoming)
    for entry in entries:
        if dedup_key(entry) == key:
            merge(entry, incoming)
            return entries
    incoming.setdefault("id", new_id())
    incoming.setdefault("first_seen", incoming.get("last_seen") or "")
    incoming["occurrences"] = int(incoming.get("occurrences") or 1)
    incoming["confidence"] = confidence_for(incoming["occurrences"])
    entries.append(incoming)
    return entries


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

#: What replaces a credential. An entry that reads `password: [REDACTED]` still
#: says a password was involved, which is the half worth remembering.
REDACTED = "[REDACTED]"

#: Key names whose value is a credential. Compared against the captured key with
#: every non-alphanumeric character stripped, lowercased, by *suffix* — so one
#: entry here covers `api_key`, `API-KEY`, `X-Api-Key` and `the api key`.
#:
#: Deliberately compound. A bare `token:` or `secret:` also matches ordinary
#: prose — "fix: rename token to symbol" — and a redaction that mangles commit
#: subjects is one somebody switches off. The standalone shapes below catch a
#: credential pasted with no key at all, which is the larger risk anyway.
_SECRET_KEY_SUFFIXES: tuple[str, ...] = (
    "authorization", "apikey", "apisecret", "apitoken",
    "accesstoken", "refreshtoken", "authtoken", "idtoken", "bearertoken",
    "sessiontoken", "accesskey", "accesskeyid", "secretkey", "clientsecret",
    "privatekey",
    "password", "passwd", "cookie", "setcookie",
)

#: Credentials that carry their own shape and need no key beside them. Each is
#: anchored and length-bounded: a pattern loose enough to match prose would be
#: removed the first week.
_SECRET_SHAPES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),               # GitHub token
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),             # GitHub fine-grained
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{16,}"),           # OpenAI / Anthropic
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                       # AWS access key id
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"),             # Slack
    # A JWT — three base64url segments. Also covers a bare `Bearer eyJ...`.
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    # A PEM block. `_untrusted` flattens before it redacts, so the whole key
    # arrives on one line and `.` spans it without DOTALL.
    # Bounded rather than open: `.*?` that can reach `$` rescans the tail once
    # per BEGIN marker. 8 KiB clears an RSA-4096 block with room over.
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.{0,8192}?"
               r"(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
)

#: `postgres://user:pw@host` — the shape a commit body about a connection string
#: arrives in, and the one no key-name rule sees.
_URL_USERINFO_RE = re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")

#: The pieces of a `key: value` / `key=value` pair, matched separately.
#:
#: One regex over the whole pair does not work, and the way it fails is the
#: common case rather than a corner: in `chore: api_key=hunter2` the leftmost
#: match takes `chore` as the key and `api_key=hunter2` as the value, finds
#: `chore` innocent, and *consumes the span* — so the real pair is never
#: examined. Every commit subject has a conventional-commit prefix in front of
#: it, so that shape is most of the input, not an edge of it.
#:
#: Scanning separators instead means every `:` and `=` is considered as its own
#: pair, and a key that sits behind another separator is still reached.
_SEP_RE = re.compile(r"[:=]")
_KEY_MAX = 41
_KEY_TAIL_RE = re.compile(r"[A-Za-z][A-Za-z0-9._\- ]{0,40}$")
_VALUE_RE = re.compile(r"""\s*("[^"\n]*"|'[^'\n]*'|[^\s,;)\]}]+)""")


def _redact_pairs(text: str) -> str:
    """Mask the value of every `key: value` pair whose key names a credential."""
    spans: list[tuple[int, int]] = []
    for sep in _SEP_RE.finditer(text):
        # From at most one key-length back, never from 0. Searching the whole
        # prefix at every separator is quadratic, and this runs on a commit body
        # of no fixed size inside a hook that must not stall a `git commit`.
        key_match = _KEY_TAIL_RE.search(
            text, max(0, sep.start() - _KEY_MAX), sep.start())
        if not key_match:
            continue
        key = "".join(ch for ch in key_match.group(0) if ch.isalnum()).lower()
        if not key.endswith(_SECRET_KEY_SUFFIXES):
            continue
        value = _VALUE_RE.match(text, sep.end())
        if not value or not value.group(1):
            continue
        # Left to right, skipping anything that falls inside a span already
        # claimed — `api_key: client_secret=x` matches twice, nested.
        if spans and value.start(1) < spans[-1][1]:
            continue
        spans.append((value.start(1), value.end(1)))

    if not spans:
        return text
    out: list[str] = []
    cursor = 0
    for start, end in spans:
        out.append(text[cursor:start])
        out.append(REDACTED)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def redact(text: str) -> str:
    """Mask credentials in prose before it is written to an experience store.

    Both writers file prose they did not author — a commit subject and body are
    written by whoever wrote the repository, a runtime lesson quotes an error
    string — and both file it into the *global* store, where
    `experience_injector` later surfaces it into sessions working on unrelated
    projects. `_untrusted` already made that text read as data rather than as
    instructions; it did nothing about the text being a secret. A token pasted
    into a commit body therefore travelled from one repository to every other
    one on the machine.

    Over-redaction and under-redaction are not symmetric here, but only just:
    masking a word costs a little fidelity in a store nobody reads directly,
    while missing one leaks a live credential across repository boundaries — and
    a rule that mangles ordinary prose gets switched off, which leaks all of
    them. Hence compound key names only, plus shapes that are unmistakable.

    This is a filter, not a guarantee. It knows the shapes listed above and no
    others; a bespoke credential with no key beside it still goes through.
    """
    if not text:
        return ""
    out = _URL_USERINFO_RE.sub(REDACTED, str(text))
    # Key/value before shapes, not after. A shape that fires first leaves
    # `api_key=[REDACTED]` behind, and the value pattern then stops at the `]`
    # it just inserted and emits a second one — `[REDACTED]]`.
    out = _redact_pairs(out)
    for shape in _SECRET_SHAPES:
        out = shape.sub(REDACTED, out)
    return out


__all__ = [
    "CONFIDENCE_CAP",
    "CONFIDENCE_DECAY",
    "DEDUP_FIELDS",
    "MAX_ENTRIES",
    "REDACTED",
    "confidence_for",
    "dedup_key",
    "evict",
    "merge",
    "new_id",
    "redact",
    "upsert",
]
