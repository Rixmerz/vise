# A project can staff its own roles

## Why

`AgentRegistry.bundled()` reads exactly one directory — the installed plugin's
`agents/`. A project cannot add a capability without forking vise.

That is not a hypothetical limit. It is already binding on vise itself:

```
roles in POLICY with no agent at all:
  architecture, classify, extract, integration, inventory, replan, research
```

Seven of the fourteen roles the model policy prices resolve to nobody. All four
of the cheapest tier — `research`, `extract`, `inventory`, `classify` at
haiku/low — are unstaffed. The policy quotes a price for work the fleet cannot
take, and `resolve()` answers every one of them with *"no registered agent takes
role X"*.

The runtime's own escalation ladder has the same hole: `replan` is priced at
opus/high and the scheduler's `replanner` hook has no agent to call.

This is drift of exactly the kind `CLAUDE.md` says the suite exists to catch —
a fact restated in one place (the policy table) that no longer matches its
source (the fleet). Nothing pins the two together.

## What Changes

- `AgentRegistry.for_project(project_dir)`: bundled agents, then `.vise/agents/*.md`
  layered on top. A project id shadows a bundled one, and the shadowing is
  reported rather than silent.
- `validate_charter()` in `registry.py`: the invariants
  `test_agents_and_skills.py` already asserts about bundled charters, extracted
  so they also run at load time on project charters. The test calls the same
  function, so the two cannot drift.
- A project charter that fails validation is refused with its reason and the
  rest of the fleet keeps working. One bad file must not take a run down.
- `vise runtime agents` reports each agent's origin, and names any shadowing.
- An honesty test pins that every role the policy prices either resolves in the
  bundled registry or is listed, with a reason, as project-supplied.

## Capabilities

### New Capabilities
- `agent-registry`: which agents a run may route to, where they come from, and
  what makes a charter usable.

## Impact

- Modified: `src/vise/runtime/registry.py`, `src/vise/runtime/planner.py`,
  `src/vise/runtime/scheduler.py`, `src/vise/cli/runtime_cmd.py`,
  `src/vise/tests/test_agents_and_skills.py`, `README.md`
- New: `src/vise/tests/test_project_agents.py`
- Behavioural: a project with no `.vise/agents/` behaves exactly as before.
- No new dependency. Reading a directory of markdown is what the registry
  already does.

## Non-goals

- **Synthesising a charter.** Writing the missing agent is a separate change.
  This one is the mechanism it would need, and shipping the mechanism first
  means the synthesiser has somewhere to put its output that a person can read.
- **Selecting an agent by embedding similarity.** A task's `role` is a
  declaration, and declarations are what make a plan auditable. A cosine score
  cannot be argued with.
- **Inventing the seven missing charters.** Which roles vise should staff out of
  the box is a decision about the product, not a gap to fill silently.
