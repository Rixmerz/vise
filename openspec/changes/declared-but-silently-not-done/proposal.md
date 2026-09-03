# Two things declared that silently did not happen

## Why

Both defects here share a shape, and it is the shape this repository treats as
the worst one: something an author wrote down, that the system accepted, that
then did not take effect — with no error, no warning anyone reads, and a
surface that keeps reporting success.

### A trailing comment disarmed a restriction, in both parsers

`- "Bash"  # no shell in this phase` yields `'"Bash"'` from `graph_parser` and
the whole rest of the line from the enforcer's parser. Neither string matches
a tool, so the restriction blocks nothing — from either side — and the graph
loads and activates without complaint.

This was found while rewriting the enforcer's parser and deliberately recorded
rather than fixed, because the enforcer's contract is that it agrees with
`graph_parser`: teaching only one of them to strip comments would have made
them disagree, and the test that pins them would have gone red for the right
reason on the wrong change. Fixing both together is the only way it could be
fixed at all.

The cause in `graph_parser` is ordering. The quote check runs before the
comment strip, so a quoted value carrying a comment no longer *ends* in a
quote, fails the `endswith` test, falls through to the strip, and comes back
with its quotes still on. The same ordering degrades an inline list with a
trailing comment — `["a", "b"]  # only these` — from a list to a string.

### A run event was dropped by telemetry for the third time in one release

`drained` and `drain_failed` were added to the scheduler and silently not
recorded. Then `resumed`, days later, by the same person who had just fixed
the other two. The symptom is nearly invisible: the run's own `state.json`
has the event and `vise runtime explain` shows it, while the cross-run log —
the file that answers "which roles escalate most", "what a task of this shape
usually costs" — does not, behind a `log.warning` nobody reads.

Three occurrences is not a run of bad luck. It is a contract with no
enforcement.

## What Changes

- `parse_value` takes a quoted scalar up to its **closing quote**, and an
  inline sequence up to its **closing bracket**, so a trailing comment cannot
  change what either one parses to. `#fff`, `url#anchor`, and a `#` inside
  quotes are untouched — the existing whitespace-preceded rule still governs
  unquoted scalars.
- The enforcer's `_scalar` learns the same rule, so the two stay in agreement.
- `test_telemetry_event_registry.py` pins the events the runtime emits against
  `_VALID_RUN_EVENTS`, in both directions.

## Why the telemetry test asserts a property that already holds

It fixes nothing today — the two sets agree, because `resumed` was registered
when it was caught. That is the point. It is the fourth occurrence this is
meant to stop, and the cost of keeping it passing is one line next to the
event being added.

Both directions matter. An emitted event that is not registered is data thrown
away. A registered event nobody emits is a line that outlived its reason, and
the next reader cannot tell which of the two they are looking at.
