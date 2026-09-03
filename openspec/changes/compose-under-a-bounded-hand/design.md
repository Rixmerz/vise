# Design

## Why an allowlist and not a denylist

Two validators run repo-chosen commands today, so a denylist would be two
lines and would read as sufficient. It is the wrong shape: a denylist admits
every future validator by default, and the failure mode is silent — the next
validator that shells out would be authorable by a composed graph and nobody
would notice until it mattered.

An allowlist inverts the default. Its failure mode is a composer that cannot
use a legitimate new validator, which shows up immediately as a refusal naming
the list.

The union assertion is what makes it hold:

```
BUILDER_VALIDATORS | BUILDER_VALIDATORS_EXCLUDED == set(_REGISTRY)
```

Adding a validator without placing it fails the suite. That is the same
instrument as the telemetry event registry and the skip budget — a contract
that cannot drift because drifting is what fails.

## Where the line is, and why it is defensible

Not "safe" versus "unsafe", which is a judgement that ages badly. The property
is mechanical and checkable: does this validator run a command the repository
supplied? `command_exit` takes a `cmd` tuple from the graph; `quality_check`
resolves a command from `.vise/quality.yaml`. Everything else runs vise's own
code over the tree.

A test asserts that exactly those two are the excluded set, so if a future
validator starts shelling out and is added to the allowlist, that test names
it.

## Why the builder and not the YAML

There is no way to tell an agent from a person at the tool surface, and trying
to would be security theatre. But the two paths differ in something real: a
workflow file is a file, committed and reviewed as one; the builder is an API
call made mid-session, reviewed by nobody. Constraining the API and leaving
the file alone puts the check where the absence of review is.

## Why the brief is deterministic

The composer's job — deciding what to build next — is judgement, and this
module does not attempt it. What it does is remove the part a composer should
never have to re-derive: which tasks are already paid for.

Everything in the brief comes from `RunState` by reading, with one
interpretation: a failure classified `SPEC_BUG` or `ARCHITECTURE_BUG` is
stated as "the plan was wrong, not the work", because that is what those
classifications mean and a composer that misses it will re-plan the same task.

## Why it stops before dispatching

`README.md:23` gives the reason and a test pins it: the MCP server runs
*inside* a Claude Code session, so a tool that dispatched from there would
have the session spawning sessions through the one component that cannot call
another server's tools. Composing does not need that authority, and taking it
here would smuggle a reversal of a documented decision into a feature.
