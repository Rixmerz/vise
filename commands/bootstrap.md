---
description: Set this repo up for vise — detect its tools, write the quality profile, name the env vars the gates need
effort: low
---

Get vise working *in this repo*. Installing the plugin ships the agents,
skills, commands and hooks; what it cannot ship is the part that is about this
repo specifically. That is what this fixes.

**1. Run `vise bootstrap --dry-run`** and show the user what it detected before
writing anything. If they are happy, re-run without `--dry-run`. It refuses to
overwrite an existing `.vise/quality.yaml` unless given `--force`.

Read the output honestly. A check appearing under "Not bound" is not a failure
of the tool — it means the repo does not have that tool, or has it installed
without ever configuring it. Say which of the two, because the fix differs:
install it, or add its config, or accept the gap knowingly.

**2. Report the two environment variables** it printed and tell the user to put
them in `.claude/settings.json` under `env`. Do not edit that file yourself
unless asked — it is theirs, and it often holds things vise has no business
touching.

Be direct about the consequence of skipping this: without `VISE_TEST_CMD` and
`VISE_LINT_CMD`, the `tests_pass` and `lint_pass` node-gate validators fall
back to `pytest` and `ruff`. On a repo that uses neither they report
`unverified` — the gate exists and does not bite, which reads as green.

**3. Check the Python environment.** Call any vise MCP tool (`vise_version` is
cheapest). If it fails, the plugin's files loaded but its server did not: the
install copies files without provisioning a venv. `bin/vise-run` prints the
fix; relay it.

**4. Mention the opt-ins, once, without pushing them.** All off by default:

- `VISE_SNAPSHOT_ON_EDIT=1` — snapshots per edit, and before destructive shell
  commands
- `VISE_GOAL_GATE=1` — the Stop hook holds the turn open on an unfinished goal
- `VISE_CODELAYER=warn` — read-by-symbol gate in recording mode

Recommend `warn` over `enforce` for the last one and say why in one line: it
logs what it *would* have denied, so the false-positive rate gets measured
before anything blocks.

**5. If livespec is mounted**, suggest `index_project` and then
`debt_baseline_capture` — without a baseline, `search_similar` reports every
pre-existing near-duplicate in the repo and the noise buries the finding that
matters.

Finish with one line on what is now enforced and what is still skip-passing.
That distinction is the whole point of the exercise.
