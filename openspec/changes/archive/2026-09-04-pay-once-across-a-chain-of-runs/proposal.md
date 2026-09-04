# Paying once across a chain of runs

## Why

The loop the runtime was built for is: plan → execute → observe → evaluate →
change the plan → execute again → verify → conclude. Every step exists.
`vise runtime compose` reads a stopped run into the terms the next plan is
written in; `graph_builder_*` accepts that plan under an allowlist that stops
it authoring its own gate; `runtime plan` reads the composed graph back.

Then the chain breaks. Running the composed graph means `vise runtime run`,
which starts from nothing: a new ledger at zero, and no knowledge that
`money-python`, `parser-python` and `report-python` were paid for two hours
ago. The brief's first section — *"already done — do not plan these again"* —
is addressed to a person, and a person is the only thing that can act on it.

That has two costs, and the second is the worse one. A follow-up plan that
redeclares finished work pays for it twice, which is the waste the ledger
exists to prevent. And a `--max-cost` on the follow-up bounds the follow-up
alone, so a goal pursued across four runs has no ceiling at all — each run is
under budget and the chain is unbounded. `resume` already refuses that
reasoning for one run: *"a resumed run that forgot its spend would turn
`--max-cost` into a per-attempt limit, and a loop could spend without bound by
resuming."* The same sentence is true of a chain, and nothing enforced it.

## What Changes

`vise runtime continue <run_id> --graph <composed-graph>` starts a run of a new
plan as the continuation of a recorded one.

**The spend carries.** The new run's ledger opens at what the prior run spent,
so `--max-cost` bounds the chain rather than its last link. This is the same
argument `resume` makes, applied to the case where the plan changed instead of
staying the same.

**The plan runs what it declares.** A task in the composed graph is dispatched
even where its id succeeded before, because declaring it is what asking for it
looks like and nothing here can tell whether two tasks sharing an id are the
same work. The output says which ids that applies to rather than doing it
quietly, and `--skip-done` is for the caller who knows they are the same.

This was drafted the other way round — the prior run's successes passed as
`completed`, so a composed task could depend on `parser-python` without the new
graph redeclaring it. Running it showed that is impossible and should be:
`Graph.validate` refuses a dependency on an id the node does not declare, and in
a workflow file an unknown dependency is a typo. A composed graph is
self-contained, and what the prior run left behind is on disk, not in the
schedule.

**The lineage is recorded.** `RunSpec.parent_run_id` makes the chain readable
after the fact: `status` shows it, `explain` shows it, and a `compose` of the
continuation can see what came before.

## What this deliberately does not do

**It still dispatches nothing on its own.** `continue` is a command a person
runs, and without `--yes` it prints the plan and stops, exactly like `run`.
There is no `run_start` MCP tool, by the decision at `README.md:23` that
`test_there_is_no_run_start_tool` pins, and closing the loop's bookkeeping is
not a reason to reopen its authority.

**It does not compose.** Deciding what the next plan should be is judgement,
and `compose` already refuses to attempt it. This change is only the half that
is mechanical: which work is paid for, and how much has been spent.
