---
name: agent-autoheal
description: Two-path protocol for failing subagents. Hot path (during task) — re-brief the same agent with the quoted failure, record the failure shape, escalate after 2 misses; never edits agent files. Cold path (batched heal) — only when experience memory shows >=2 failures of the same shape for one agent, classify root cause and apply ONE surgical fix. Use after a subagent fails review/validators, or when experience_query surfaces a repeat failure pattern for an agent.
---

# agent-autoheal

## Hot path — during task (cheap, NEVER edits agents)

- Dispatch → review/validators fail → **RE-BRIEF**: re-dispatch the SAME agent, quoting the concrete failure verbatim in the prompt.
- Pass → done. Record the failure: `experience_record` with tag `agent:<name>` + a short failure-shape slug (e.g. `agent:tester missed-entrypoint`).
- Fail 2nd time → **escalate**: different agent, or the orchestrator does it. Never a 3rd identical attempt.

## Cold path — the actual heal (batched, evidence-driven)

**Trigger:** `experience_query` shows ≥2 failures of the SAME shape for `agent:<name>`. One failure = anecdote; two = pattern.

Only then:

1. Read the N incidents.
2. Classify the common root cause (pick one):
   - **(a) Briefing gap** — fix the orchestrator's dispatch pattern, NOT the agent.
   - **(b) Charter gap** — add ONE surgical DO/DON'T rule (<10 lines) to `.claude/agents/<name>.md`, with the concrete example from the incidents.
   - **(c) Tool gap** — add the tool to frontmatter `tools:`.
   - **(d) Procedure gap** — write/extend a runbook (`.claude/runbooks/<agent>/<case>.md`), not the charter.
   - **(e) Model/effort mismatch** — adjust model or effort setting.
3. Apply the one fix.

## Verification

- The next REAL dispatch is the test — no synthetic re-runs.
- Agent .md files are read fresh per spawn — no session restart needed.

## Guards

- Never delete existing rules while adding one.
- Agent files <150 lines; over → split into runbooks.
- One incident never justifies a charter edit.
