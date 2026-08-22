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
| `src/vise/assets/workflows/` | the 9 bundled `*-graph.yaml` workflows |
| `src/vise/tests/` | the whole suite — asset honesty tests live here too |
| `agents/` | 20 bundled subagent charters |
| `skills/` | 23 bundled skills (`engineering-baseline`, `security-baseline`, `ponytail`, `orchestration`, `architecture`, `agent-autoheal`, `codelayer`, `design-brief`, and the 15 `*-rules`) |
| `commands/` | `/debug` `/feature` `/quality` `/status` `/codelayer` `/debt` `/bootstrap` |
| `hooks/hooks.json` | 13 hook registrations across 11 scripts, 6 events |
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
.venv/bin/python -m coverage report --fail-under=71
```

The coverage floor is a ratchet: raise it when the real number rises, never
lower it to make a change pass.

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

### Gates fail closed — the other half of the rule

Hooks fail open. **Gates do the opposite.** `QualityCheckValidator` skips when
its binary is missing (`passed=True`, `source="asserted"`,
`outcome="unverified"`); `CommandExitValidator` and the three design gates fail
closed. The distinction is deliberate and `quality-gate-graph.yaml` documents
why. When adding a validator, decide which side it is on and say so in its
docstring — a gate that cannot run must never report success, and a hook that
raises takes the user's session down.

### Hooks fail open, on purpose

Every hook in `src/vise/hooks/` must never break the user's session. A hook that
raises takes Claude Code down with it, so broad `try/except/pass` around the
outermost handler is the contract, not sloppiness. This is why `bandit` is
gated at Medium and above — the 89 Low findings are all `B110`/`B112` on exactly
these handlers.

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

**Adding an agent, a skill, or a workflow means updating what asserts it.** If a
change makes one of these tests fail, the fix is almost never to loosen the test.

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
