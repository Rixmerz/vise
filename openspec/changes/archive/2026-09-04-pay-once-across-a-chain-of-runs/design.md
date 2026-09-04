# Design

## Why the spend carries and the succeeded records do not

Two different questions, and conflating them would be the easy mistake.

*Spend* is about one goal. The money is gone whichever plan spent it, so a
ceiling that resets between links is not a ceiling. Carrying it is the only
reading under which `--max-cost` means anything across a chain.

*Succeeded task records* are about identity, and identity is exactly what a
new plan may have changed. `cli-python` in the composed graph is very likely
the same work as `cli-python` in the run that failed it — and it is a
different task if the composer rewrote its ownership, its acceptance, or its
role, which is the usual reason to compose at all. So a task present in the
new graph runs.

The case this design originally turned on does not exist. A composed task that
*depends* on `parser-python` without redeclaring it cannot be written:
`Graph.validate` rejects a dependency on an id the node does not declare, and
that check is correct — in a workflow file an unknown dependency is a typo, and
relaxing it to serve continuations would cost every other graph its typo check.
The first `continue` run against a real recorded run failed on exactly this,
before any of it was tested.

So the composed graph is self-contained, and what the prior run left behind
reaches it through the working tree rather than the schedule. `completed` is
still available — as `--skip-done`, opt-in, for a caller who knows a redeclared
task is the same work — but it is never the default, because the default has to
be that a plan runs what it says.

## Why not fold this into `resume`

`resume` keeps the plan and retries what did not finish. `continue` keeps the
spend and takes a new plan. They share the ledger argument and nothing else:
resume resets records to `PENDING` and re-dispatches them, which for a new
graph would mean resetting records that describe tasks the new graph does not
have.

Overloading one command with a `--graph` that silently changes its semantics
would put two behaviours behind one name, and the difference — whether the
plan is the same one — is the thing a caller most needs to be explicit about.

## Why `run(..., resume_from=)` is the entry point for both

The dispatch loop is the hardest part of this runtime to test, and it already
has one entry point on purpose. `continue_from` builds the state it wants —
records for the new graph's tasks, a ledger opened at the prior spend — and
hands it to the same loop. Nothing after that line knows which of the three
callers it came from, which is the property that keeps the loop testable.

## What a chain looks like when it goes wrong

The failure this cannot prevent: a composer that redeclares finished work.
Nothing here can catch it, because a task in the plan is a task to run. What
it can do is make the waste visible before the money moves — `continue` prints
the plan with the inherited spend on it, and without `--yes` that is all it
does.
