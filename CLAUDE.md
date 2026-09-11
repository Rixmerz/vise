# vise — project instructions

vise is a Claude Code plugin: a phase-gated workflow enforcer, a cross-project
experience memory, and git snapshots. It ships an MCP server (Python), plus
agents, skills, commands, and hooks that other repos install.

**vise's assets are read by other agents in other repos.** A sloppy line in
`skills/` or `agents/` is not a typo — it is wrong guidance executed at scale.
Treat those files with the same care as the code.

## Layout

| Path | What lives there |
|---|---|
| `src/vise/` | the MCP server, engines, hooks, CLI, recipes |
| `src/vise/engines/` | validators and the logic they gate on — the three design gates live here |
| `src/vise/assets/workflows/` | the 11 bundled `*-graph.yaml` workflows |
| `src/vise/tests/` | the whole suite — asset honesty tests live here too |
| `agents/` | 22 bundled subagent charters |
| `skills/` | 23 bundled skills (`engineering-baseline`, `security-baseline`, `ponytail`, `orchestration`, `architecture`, `agent-autoheal`, `codelayer`, `design-brief`, and the 15 `*-rules`) |
| `commands/` | `/debug` `/feature` `/quality` `/status` `/codelayer` `/debt` `/bootstrap` |
| `hooks/hooks.json` | 13 hook registrations across 11 scripts, 6 events |
| `src/vise/tools/_annotations.py` | what every MCP tool does to the world — the destructive set, readable in one screen |
| `.claude/` | vise's *own* dev-time skills (OpenSpec) — not shipped to users |
| `.vise/quality.yaml` | what vise's own quality gate runs |

## Environment — this matters more than it looks

Every command runs through `.venv/bin/python`. A bare `pytest` resolves against
whatever is on `PATH` (often the MCP server's interpreter), finds a Python
without vise's dependencies, and reports failures that do not exist. A gate that
goes red for environment reasons teaches people to set
`VISE_NODE_GATE_OVERRIDE=1`, which is the habit the gates exist to prevent.

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

## Verify before reporting done

```bash
.venv/bin/python -m ruff check . --exclude .claude
.venv/bin/python -m coverage run -m pytest -q
.venv/bin/python -m coverage combine
.venv/bin/python -m coverage report
```

`combine` is not optional. Every hook is tested by launching it as its own
interpreter — the only way to test something whose contract is "must never take
the session down" — and those children write their own data files. Reporting
without combining measures the parent process only, which is why `hooks/` used
to read 0% while being among the best-covered code in the repo.

The coverage floor is a ratchet: raise it when the real number rises, never
lower it to make a change pass. It lives in **one** place, `[tool.coverage.report]
fail_under` in `pyproject.toml`. It used to live in four — CI said 70,
`.vise/quality.yaml` said 71, this file said 74 — so "the coverage gate" meant a
different number depending on which command you ran, and the lowest one won.
Never pass `--fail-under` on a command line.

## Conventions

- Python 3.11+, `src/` layout, `from __future__ import annotations` at the top
  of new modules.
- Follow `skills/engineering-baseline/SKILL.md` and
  `skills/python-rules/SKILL.md`. vise gates other repos on these; it holds
  itself to them first.
- `ruff` is the only linter. `BLE001` is deliberately not selected — see below.
- Tests go in `src/vise/tests/`, named `test_<subject>.py`. The autouse fixture
  in `conftest.py` redirects `$XDG_DATA_HOME`; never bypass it, or a test will
  clobber a real project's live workflow state.
- **Everything committed here is in English** — code, comments, docstrings,
  assets, the changelog, commit messages, PR titles and bodies. Not a style
  preference: `orchestration` requires the agent channel to be English because
  everything a subagent preloads is, and a repo that ships those assets while
  writing its own tests in another language is not holding itself to the rule it
  gates other repos on. Thirteen test files predate this and are still Spanish;
  translate one when you are already editing it, not as a sweep. The Spanish in
  `hooks/workflow_suggester.py` is different and stays — it is a regex matching
  what a user types.
- **No client or engagement names, ever.** Field reports are the best source of
  rules this repo has, and they arrive from real client work. Describe the
  *shape* of the system the failure happened in — "an Electron desktop app with
  its own API" — and never who paid for it. This is a public repository, and the
  one place the name cannot be taken back out of is a commit message.
- **A field report is generalized, never transcribed.** An incident arrives as a
  specific failure in a specific system, and the specifics are what make it
  credible to the person who lived it. What belongs in an asset is the *shape*:
  the behaviour that went wrong, and the check that would have caught it. The
  test of a rule is whether it fires on a repository that shares none of the
  reporting system's vocabulary — and an example written in that vocabulary
  narrows the rule to readers who already know it. Generic technology stays:
  a test runner's include globs, a root `tsconfig.json`, an exhaustive `switch`
  over a union. The reporting system's business language and its own symbol
  names do not — quoting them ships someone else's context inside guidance that
  runs in every repo that installs vise.

### Gates fail closed — the other half of the rule

Hooks fail open. **Gates do the opposite.** `QualityCheckValidator` skips when
its binary is missing (`passed=True`, `source="asserted"`,
`outcome="unverified"`); `CommandExitValidator` and the three design gates fail
closed. The distinction is deliberate and `quality-gate-graph.yaml` documents
why. When adding a validator, decide which side it is on and say so in its
docstring — a gate that cannot run must never report success, and a hook that
raises takes the user's session down.

A command named in `.vise/quality.yaml` was chosen by the repository. It runs
only after `vise approve <check>` on this machine (`vise bootstrap` approves
what it writes) or under `VISE_TRUST_PROJECT_TOOLS=1`; until then the check
reports `asserted`/`unverified`. `src/vise/core/consent.py` has the reasoning.

### Hooks fail open, on purpose

Every hook in `src/vise/hooks/` must never break the user's session. A hook that
raises takes Claude Code down with it, so broad `try/except/pass` around the
outermost handler is the contract, not sloppiness. This is why `bandit` is
gated at Medium and above. There are 137 Low findings: 34 `B110`/`B112` on
exactly those handlers, 88 `B404`/`B603`/`B607` on the `subprocess` calls the
CLI and the validators are made of, 14 `B101` on asserts, and one `B105` that
reads `PASS = "pass"` in an enum as a hardcoded password. This file said "the 89
Low findings are all `B110`/`B112`", which was true of neither the count nor the
composition — restate a number here only after running the command.

Medium and above is zero and gates. Keep it there: `bandit` is the one check in
CI that reads vise's own code for a security defect, and a High finding it
raised on this branch (a `sha1` that was not for security) was correct about the
ambiguity even though it was wrong about the risk.

**A hook that fails open says so.** The outermost handler still swallows the
exception, but it calls `hooks/_failsafe.note()` on the way past, and
`session_restore` reads the ledger out at the next `SessionStart` and clears it.
Without that, an experience went unrecorded, a blocker went unsurfaced, a
snapshot went untaken — and the user saw a session that worked. This is the same
distinction the neighbours section draws: absent and unreadable are different,
and "could not tell" has to report something. The ledger is standard library
only and every function in it swallows its own errors, because it is what runs
when something else has already broken; losing a note is acceptable, raising
from there is not. A new hook wires its outermost handler to it.

## Assets are asserted, not trusted

Facts restated in prose drift from their source. The suite pins them:

| Test | Pins |
|---|---|
| `test_agents_and_skills.py` | agent/skill frontmatter: valid model, effort, color, every `tools` entry resolves, every `skills:` reference ships |
| `test_orchestration_skill_sync.py` | every workflow the orchestration skill routes to exists, and every bundled workflow is routable |
| `test_asset_honesty.py` | no workflow names a tool vise does not expose |
| `test_doc_call_sync.py` / `test_version_sync.py` | README claims and version strings match reality |
| `test_asset_coverage.py` | every validator in the registry is documented in the README — a workflow author cannot use one they cannot find |
| `test_gate_visibility.py` | the `static` node carries both kinds: named checks that skip when unbound, and `design_tokens`, which never can |
| `test_neighbour_contract.py` | every tool name an asset teaches belongs to vise or to a neighbour in `core/neighbours.py` — and every pinned name is still referenced somewhere |
| `test_tool_annotations.py` | every MCP tool declares what it does to the world, the four hints are internally consistent, and the four destructive ones say so in the title a host shows |

**Adding an agent, a skill, or a workflow means updating what asserts it.** If a
change makes one of these tests fail, the fix is almost never to loosen the test.

## The neighbours

vise names tools belonging to `livespec`, `flowtrace` and `layout-inspector` in
about thirty places and can call none of them: MCP has no server-to-server
channel. `src/vise/core/neighbours.py` is the one place those names live, and
`test_neighbour_contract.py` holds every asset to it.

That contract keeps vise consistent with itself, which is not the same as
correct. It shipped `locate` and `compute_index_status` — one that livespec has
never had, one removed in its v0.9 — in the `codelayer_gate` deny message and
the decouple survey, the two surfaces most dependent on being obeyed. Nothing
here could have caught it. So:

- **Check a name against the neighbour before you add it**, and record the
  release you checked in `MINIMUM_VERSIONS`. A version is the one fact about
  another repository that a person can verify in a minute.
- **Every livespec example takes `workspace`.** It is required on every call;
  there is no environment fallback.
- **What vise *can* check is the file.** `core/neighbour_state.py` reads
  livespec's index, flowtrace's newest trace and the provenance of a Graphify
  ingest, with the standard library and without raising. Prefer that over a
  phase prompt asking an agent to check — a refusal in prose is advice to the
  party being checked. `vise neighbours` prints what it sees.
- **Absent and unreadable are different.** A known absence fails a gate closed;
  "could not tell" must report `unverified`. Collapsing them makes a gate
  refuse on vise's own bug, which is how an override habit starts.

## Writing agents and skills

- An agent's `description` is what routes work to it — it must name the
  trigger conditions, not just the role.
- A `*-rules` skill's `description` decides whether it loads at all. It must
  list every file extension the rules apply to; an extension missing there means
  the skill silently never fires on those files.
- Every `*-rules` skill states the precedence pointer to `engineering-baseline`
  and keeps its `## Security` section last — security outranks style, and the
  reader should hit it after the style rules, not before. Each bullet in that
  section carries the CWE to cite when reporting it.
- No asset may tell an agent to produce a CVSS score. It cannot know the
  deployment, exposure, or data classification, and an invented number carries
  more authority than its evidence. Rank on attacker preconditions instead —
  `skills/security-baseline/SKILL.md` has the ladder.
- Third-party packages in a rules skill belong under
  `## Tooling — greenfield defaults only`. A rules skill must never tell an
  agent to migrate a project's toolchain as a side effect of another change.
- Agent charters stay under 150 lines. Longer belongs in a skill or a runbook.

## Don't

- Don't add a dependency to `pyproject.toml` without saying which stdlib option
  failed.
- Don't edit an asset without checking whether a test in `src/vise/tests/`
  asserts something about it.
- Don't lower the coverage threshold, and don't add `VISE_NODE_GATE_OVERRIDE=1`
  to any script or CI step.
- Don't commit anything into `.claude/` expecting users to get it — that
  directory is vise's own dev setup and ships to nobody.
- Don't register an MCP tool with a bare `@mcp.tool()`. Use
  `@_ann.annotated(mcp)` and add the tool to the table in `tools/_annotations.py`.
  The host renders a tool's name, its title and its raw arguments when it asks a
  person to approve a call, and nothing else — unannotated, `graph_status`, which
  reads a JSON file, and `snapshot_restore`, which overwrites the working tree,
  arrive looking alike. An unlisted name gets the most cautious hints at runtime
  and fails `test_tool_annotations.py` in CI.
- Don't rank experience entries by adding a match score to a confidence score.
  `engines/relevance.py` multiplies instead, and the docstring says why: a
  probability scales evidence, it does not substitute for it. Under the old sum,
  an entry that matched nothing at all still scored `confidence * 0.15` and
  outranked entries that matched.
- Don't make the scheduler wait by sleeping. A retry's backoff lives on the task
  as `not_before` and the dispatch loop skips it; a `time.sleep` in the collect
  or dispatch path holds the whole loop, so one task waiting out a rate limit
  freezes every healthy task in the run. The same edit has a second trap: the
  loop breaks when nothing dispatched and nothing is in flight, so a task that is
  only waiting must be distinguished from one that is blocked, or the retry never
  happens *and* the recorded reason is wrong.
- Don't give an escalation a backoff. `retry` answers a failure outside the work
  and waits for it to clear; `escalate` runs a bigger model against the same
  task and is waiting for nothing. Conflating them is the same mistake
  `recovery.py` exists to prevent, priced in wall clock instead of dollars.
