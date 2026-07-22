---
description: Start the evidence-first debug workflow — reproduce → classify → analyze → fix → verify
argument-hint: <the bug / failing behavior>
effort: high
---

Drive this bug through the vise `debug` workflow.

1. Call `graph_activate` with `graph_name: "debug"`.
2. Record the goal with `goal_set` from the report below.
3. Enter the **understand** phase — read and reproduce before touching code.
   Follow each phase's injected prompt; classify the bug so the graph routes to
   the right strategy. Consider dispatching `vise:debugger` for the diagnosis.
4. The fix phase gates on `tests_pass` — do not claim done until it's green.

Bug: $ARGUMENTS
