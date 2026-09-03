## Context

The node gate and the execution plane fail in opposite directions, and the fix
has to respect both.

`OpenSpecValidator` gates a *transition*: the workflow is sitting on `spec` and
wants to reach `implement`. It runs on every traverse, and its verdict is about
the repository. The scheduler gates a *dispatch*: N briefs are about to become
N subprocesses, each of which can write to the tree and each of which costs
money. Its verdict has to be reached once, before any of that, or the gate is
an audit rather than a gate.

## Goals / Non-Goals

**Goals**
- A run that would write without a spec does not start, and spends nothing.
- The block is visible from `vise runtime plan`, which costs nothing to run.
- One bypass vocabulary in the whole product, and it records the attempt.

**Non-Goals**
- Building the DAG from `tasks.md`. A run would then be the execution of an
  approved change, which is a larger and separate idea; this change only
  decides whether a hand-written DAG may run.
- Gating read-only runs. A research or review DAG writes nothing and specifying
  it first is ceremony, not safety.
- Shelling out to the `openspec` CLI. The runtime gate is structural only, for
  the same reason levels 1–4 of the node gate are.

## Decisions

### Check once, before the first dispatch — not per task

A per-task check would run the same repository-level query N times and could
report different answers within one run as tasks integrate. Worse, it would let
the first three tasks spend money before the fourth discovered the project has
no specs. The question "does this project have a plan" is asked once, and the
run either starts or does not.

**Alternative rejected:** blocking each writing task individually as it becomes
ready. It reads as more granular but is strictly worse — money is already spent
by the time the block appears, and the run ends half-applied.

### The gate is about writing, so a read-only run is exempt

`Task.writes` already exists and already drives ownership admission. A run
whose tasks all declare `writes: false` cannot change the system's contract, so
there is nothing for a spec to describe. Exempting it keeps the gate's red
meaning "you are about to build something nobody wrote down".

### Default `deltas`, never `tasks_complete`

The node gate uses `tasks_complete` on the edge into the irreversible phase,
because by then the work is done. A runtime dispatch is the *opposite* end: the
run is what ticks those boxes. Requiring them first would gate the work on its
own output and no run could ever start.

### One bypass, and it is the one that already exists

`VISE_NODE_GATE_OVERRIDE=1` bypasses the node gate and records the attempt. The
runtime gate honours the same variable and emits a `spec_gate_overridden` event
into the run's state, so `vise runtime explain` shows it.

**Alternative rejected:** a `--no-spec-gate` flag. It is more discoverable,
which is precisely the problem: a bypass that costs one keystroke and leaves no
trace is not a gate, it is a default. `.vise/quality.yaml` already states the
repo's position — an override habit is worse than no gate — and the way to
avoid teaching the habit is to not make it convenient.

### Surface the verdict in `plan`, which is free

`vise runtime plan` already refuses to run a plan with problems and prints each
one. The gate verdict joins that list, so the discoverable path costs nothing
and the expensive path is never where you first learn you are blocked.

### The gate never raises

`openspec_profile` degrades every malformed input to an empty result rather
than raising, and the gate keeps that property: an unreadable `openspec/` tree
is "no well-formed change", which is a red gate with an accurate reason, not a
crash that takes the run down.
