---
description: Show where the active vise workflow and goal stand right now
effort: low
---

Report the current vise state concisely:

1. Call `graph_status` — active graph, current node, available edges, visit counts.
2. Call `goal_get` — the active goal and its last validator results (if any).
3. Summarize in a few lines: which phase we're in, what edge/signal comes next,
   and anything blocking (failed gate, max-visits warning). No fluff.
