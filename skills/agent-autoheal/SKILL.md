---
name: agent-autoheal
description: Two-path protocol for failing subagents. Hot path (during task) — re-brief the same agent with the quoted failure, record the incident under a shape slug in that agent's charter-keyed ledger, escalate after 2 misses; never edits agent files. Cold path (batched heal) — only when that ledger counts the SAME shape twice for one agent, classify root cause and apply ONE surgical fix. Use after a subagent fails review/validators, or when experience_query on an agent's charter path surfaces a repeat failure shape.
---

# agent-autoheal

Incidents are keyed on **the agent's own charter file** — `agents/<name>.md` (or
`.claude/agents/<name>.md`). That is the file the cold path may end up editing, so
`experience_query` on that path is the retrieval.

Repeats are counted in the entry's **`shapes`** map: a slug you choose naming the
*kind* of failure, incremented additively on every record. That count is what the
cold path thresholds on. Never encode a tally in the description — descriptions
merge by keeping the longest string, so `x1` → `x2` measures the same and the
bump is discarded while the write still reports success.

## Pick a shape slug

`agent:<name>/<kind>` — the agent it happened to, and what went wrong, in kebab
case. Same failure, same slug, every time; that is the entire mechanism.

```
agent:debugger/no-repro          agent:frontend/index-as-key
agent:backend-go/ignored-ctx     agent:tester/asserts-nothing
```

Too specific and every incident is unique, so nothing ever reaches two. Too
broad and unrelated failures collide into a false pattern. Name the *defect*,
not the symptom or the file.

## Hot path — during task (cheap, NEVER edits agents)

- Dispatch → review/validators fail → **RE-BRIEF**: re-dispatch the SAME agent,
  quoting the concrete failure verbatim in the prompt.
- Either way, record the incident:
  ```
  experience_record(
    type="gate_blocked",
    file_path="agents/<name>.md",
    severity="high",
    shape="agent:<name>/<kind>",
    description="<the verbatim failure, one incident, no tally>",
    scope="project",
  )
  ```
  The response carries `shape_count` — the running total for that slug. Read it
  instead of counting anything yourself.
- Fail 2nd time → **escalate**: different agent, or the orchestrator does it.
  Never a 3rd identical attempt.

`gate_blocked` because review and validators ARE gates; the landed fix is
`gate_resolved`, a separate entry that never inflates the failure count.

## Cold path — the actual heal (batched, evidence-driven)

**Trigger:** `shape_count >= 2` for one `agent:<name>/<kind>` slug. One failure
is an anecdote; two is a pattern. Read the count from the last
`experience_record` response, or from `shapes` on the `experience_query` result
for `agents/<name>.md`.

Only then:

1. Read the incidents behind that slug — the descriptions on that entry.
2. Classify the common root cause (pick one):
   - **(a) Briefing gap** — fix the orchestrator's dispatch pattern, NOT the agent.
   - **(b) Charter gap** — add ONE surgical DO/DON'T rule (<10 lines) to that
     charter file, with the concrete example from the incidents.
   - **(c) Tool gap** — add the tool to frontmatter `tools:`.
   - **(d) Procedure gap** — write/extend a runbook
     (`.claude/runbooks/<agent>/<case>.md`), not the charter.
   - **(e) Model/effort mismatch** — adjust model or effort setting.
3. Apply the one fix.
4. `experience_record(type="gate_resolved", file_path="agents/<name>.md",
   shape="agent:<name>/<kind>", description="healed",
   resolution="<the one edit>")`

## Where the heal is allowed to land

Check this before applying a **(b) charter gap** fix, because the obvious target
is the wrong one half the time.

- `.claude/agents/<name>.md` — the project's own agent. **Editable.** It is
  under version control and survives everything.
- `<plugin-root>/agents/<name>.md` — a bundled vise agent, living wherever the
  plugin was installed. **A charter edit here is silently reverted on the next
  plugin update.** Never write the heal there.

To heal a bundled agent, copy the charter to `.claude/agents/<name>.md` first
and edit the copy — a project-level agent of the same name takes precedence over
the plugin's. Say in your report that you shadowed a bundled agent, because the
copy now stops receiving upstream improvements to that charter.

If the failure is really vise's fault rather than this project's, the fix is a
bug report against vise, not a local shadow. Prefer classification **(a)
briefing gap** or **(d) procedure gap** for bundled agents — both land in files
the project owns and neither gets overwritten.

## Verification

- The next REAL dispatch is the test — no synthetic re-runs.
- Agent .md files are read fresh per spawn — no session restart needed.

## Guards

- Never delete existing rules while adding one.
- Agent files <150 lines; over → split into runbooks.
- One incident never justifies a charter edit.
- Never edit a charter inside the plugin install directory — shadow it under
  `.claude/agents/` instead.

## What the store does and does not give you

- Retrieval matches **file paths** only, generalized: `agents/debugger.md` and
  `agents/frontend.md` both become `agents/*.md`. One entry therefore holds
  several agents' incidents — the `agent:<name>/` slug prefix is what separates
  them, so filter `shapes` by prefix rather than assuming the entry is yours.
- `occurrences` counts records merged onto the entry, across every shape. It is
  not your repeat count. `shapes[slug]` is.
- Leave `min_score` at its default when querying; raising it hides a ledger
  whose description text does not resemble your query.
