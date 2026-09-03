## Context

Two directories, one registry, and a question about who wins.

The bundled fleet is an asset the plugin ships and the suite pins. A project's
agents are files in someone's repo that nobody reviewed. Layering the second
over the first is what makes the feature useful and is also the whole risk: it
is a supply-chain edge inside the tool that is supposed to be checking the
supply chain.

## Goals / Non-Goals

**Goals**
- A project can staff a role vise does not ship.
- A project charter is held to the same bar as a bundled one.
- Shadowing is visible, never silent.
- One malformed charter costs that agent, not the run.

**Non-Goals**
- Synthesising charters, embedding-based selection, or inventing the seven
  uncovered roles. Each is argued in the proposal.
- Sandboxing what a project agent may do. A charter is data the registry reads,
  not code it executes — the tools it names still go through every gate the
  runtime already applies. Widening that surface is a different change.

## Decisions

### `.vise/agents/`, not a database

The request this answers was "store the node somewhere so later sessions reuse
it, and let me edit it". A directory of markdown in the repo answers all of it
and adds nothing: it persists, it is editable, it diffs, it reviews, and it
travels with the branch that needed it.

**Alternative rejected:** the experience memory. It is the obvious store because
it already exists and already ranks by relevance — and it applies FSRS decay,
because it is built for observations that age. A capability does not become less
true with time. Putting the fleet in there would make agents rot on a schedule
nobody chose. (Recording *which agent worked for which kind of task* does belong
there; that is an observation, and it should decay.)

### The project wins, and says so

An id collision resolves to the project's charter. That is what makes temporary
specialisation possible at all: shadowing `backend-python` with a stricter local
one is how a repo adapts an agent to itself.

Silent shadowing is the danger — a run that behaves differently from the
documented fleet for a reason nobody can see. So the registry records every
shadowed id and `vise runtime agents` prints it. The mechanism is allowed; the
invisibility is not.

### Validation is the bundled bar, extracted rather than rewritten

`test_agents_and_skills.py` already knows what a usable charter is: valid model,
valid effort, valid colour, every `tools` entry resolves, every `skills:`
reference ships, a description that names its trigger. Writing a second,
laxer check for project charters would mean the fleet has two standards and the
weaker one is the one nobody reviewed.

So the invariants move into `registry.validate_charter()` and the test calls it.
The test keeps its job — asserting the bundled fleet passes — and gains the
property that it cannot drift from what the loader enforces.

### A bad charter is refused, not fatal

`AgentRegistry.for_project` collects rejections and returns them alongside the
registry. The caller reports them; nothing raises. A typo in one project charter
must not be the reason a run cannot start — that is the same fail-open rule the
hooks follow, and for the same reason: the blast radius of strictness here is
larger than the thing it protects against, because the charter is refused either
way.

Note the asymmetry with the gates, which fail closed: a gate that cannot run
must never report success. A charter that cannot load reports nothing — the
agent simply is not there, and a task that needed it is unroutable with a reason.
The honest outcome is already the failing one.

### Shadowing may narrow, and this change does not enforce that

The rule that a project charter should only be able to make an agent *stricter*
— fewer tools, not more — is right, and it is deliberately not implemented here.
Enforcing it needs a definition of "stricter" across model, effort, tools and
skills that is worth its own argument, and getting it half-right would either
block legitimate specialisation or wave through the case it exists to stop.

What this change does is make shadowing **visible**, which is the precondition
for enforcing anything about it later. Stated here so the gap is a decision on
the record rather than an oversight.
