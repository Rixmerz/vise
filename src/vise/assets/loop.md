<!-- Written by `vise bootstrap --loop`. Edit it freely: vise never overwrites it. -->
Advance the active vise workflow by one step, then stop or schedule the next.

1. Call `graph_status()`. If no workflow is active, say so in one line and end
   the loop. A loop with nothing to advance is not waiting for anything.
2. Do the current phase's work, inside the tools that phase allows. Send
   reading-heavy or independent work to a subagent with a self-contained
   brief, and keep the loop yourself. A subagent's turn ends when it returns,
   so something in this session has to take its result and decide what comes
   next.
3. When the phase's exit condition holds, cross the edge with
   `graph_traverse(edge_id=...)`, using an edge id from `graph_status()`. The
   node's validators decide whether it opens. If one refuses, its evidence
   names what to fix: fix that next iteration. Never set
   `VISE_NODE_GATE_OVERRIDE` to get past it.
4. Stop the loop and say exactly what is blocking when:
   - the same gate refuses for the same reason two iterations in a row, or
   - the next step needs a decision only the user can make.
   A third attempt at the same refusal costs a turn and teaches nothing.
5. Do not push, delete, or restore a snapshot unless the conversation already
   authorized that exact action.

End every iteration with one line: the phase you are in, what changed, and what
comes next.

To stop: a self-paced loop ends with `ScheduleWakeup` and `stop: true`. A loop on
a fixed interval ends when you delete its job with `CronDelete`.
