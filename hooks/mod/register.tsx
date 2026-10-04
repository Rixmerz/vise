// The mod: the part of vise that has to run inside Claude Code.
//
// vise is an MCP server, and an MCP server cannot call another server's tools:
// that is why vise names livespec's and tasky's tools in prose and never calls
// them. A mod runs in the engine, and `$.mcp.call` reaches every connected
// server. So this module reads vise's own `graph_status` to draw the phase, and
// asks tasky for the fixes that already failed instead of telling the model to.
//
// Every hook fails open. A hook that throws is skipped by the engine anyway;
// the try blocks are here so a failed read never costs the call it rode on.

import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { DeadEnds, ViseStatus } from '../../types'
import {
  deadEndsNote,
  fixPhaseKey,
  movesWorkflow,
  payload,
  phaseSection,
  serverFor,
  statusLine,
  text,
  toStatus,
} from './status'

const PANE = 'vise-workflow'
const COMMAND = 'vise-workflow'

const status = atom({ plugin: 'vise', key: 'status' } as const, null as ViseStatus | null)
const deadEnds = atom({ plugin: 'vise', key: 'deadEnds' } as const, null as DeadEnds | null)

/** Spellings `$.mcp.call` accepts for a server installed from a marketplace, or added by hand. */
const FALLBACK = {
  vise: ['plugin_vise_vise', 'vise'],
  tasky: ['plugin_tasky_tasky', 'tasky'],
} as const

async function call(
  $: EngineInterface,
  tool: string,
  hint: keyof typeof FALLBACK,
  args: Record<string, unknown>,
) {
  let found: string | null = null
  try {
    found = serverFor((await $.tool.list()).map(t => t.name), tool, hint)
  } catch {
    // An unreadable tool list is no reason not to try the usual names.
  }
  const servers = found ? [found] : [...FALLBACK[hint]]
  for (const server of servers) {
    try {
      const result = await $.mcp.call(server, tool, args)
      if (!result.isError) return result
    } catch {
      // Not this name: try the next.
    }
  }
  return null
}

async function refresh($: EngineInterface, options: Record<string, unknown>): Promise<void> {
  let cwd = ''
  try {
    cwd = await $.session.cwd()
  } catch {
    return
  }
  const result = await call($, 'graph_status', 'vise', { project_dir: cwd })
  // No answer is not "no workflow": leave what was last read, and say nothing new.
  if (!result) return
  const now = toStatus(payload(result))
  await update($, status, () => now)
  $.ui.status(now ? statusLine(now) : undefined)

  const phase = fixPhaseKey(now)
  if (!phase || options.tasky_dead_ends === false) return
  const known = await read($, deadEnds)
  if (known?.phase === phase) return
  const answer = await call($, 'dead_ends', 'tasky', { limit: 10 })
  if (!answer) return
  const body = text(answer)
  await update($, deadEnds, () => ({ phase, text: body }))
  const note = deadEndsNote(body)
  if (!note) return
  try {
    await $.session.append({ message: { type: 'user', content: [{ type: 'text', text: note }] } })
  } catch {
    // A run no plugin may shape refuses the row; the pane still shows it.
  }
}

export const register: Register = (on, options) => {
  const opts = (options ?? {}) as Record<string, unknown>

  on('session.start', async ($, e, next) => {
    try {
      await $.command.register({
        name: COMMAND,
        description: 'Show the active vise workflow: phase, blocked tools, exits, failed fixes',
      })
      await refresh($, opts)
    } catch {
      // Fail open.
    }
    return next(e)
  })

  on('turn.start', async ($, e, next) => {
    try {
      await refresh($, opts)
    } catch {
      // Fail open.
    }
    return next(e)
  })

  on('tool.call', async ($, e, next) => {
    const ran = await next(e)
    if (movesWorkflow(String(e.tool))) {
      try {
        await refresh($, opts)
      } catch {
        // Fail open.
      }
    }
    return ran
  })

  on('prompt.compose', async ($, e, next) => {
    const composed = await next(e)
    if (opts.phase_in_system_prompt === false) return composed
    try {
      const now = await read($, status)
      if (!now) return composed
      return {
        ...composed,
        sections: [...composed.sections, { id: 'vise:phase', text: phaseSection(now), scope: 'session' as const }],
      }
    } catch {
      return composed
    }
  })

  on('command.run', { command: COMMAND }, async $ => {
    await refresh($, opts)
    await $.ui.open({ id: PANE, title: 'vise workflow' })
    const now = await read($, status)
    return { text: now ? statusLine(now) : 'vise: no workflow is active.' }
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e)
    const now = await read($, status)
    const dead = await read($, deadEnds)
    if (!now) {
      return (
        <Box flexDirection="column">
          <Text dimColor>No vise workflow is active.</Text>
          <Text dimColor>graph_activate(graph_name="...") starts one.</Text>
        </Box>
      )
    }
    return (
      <Box flexDirection="column">
        <Text bold>{now.graph}</Text>
        <Text>
          {now.nodeName} ({now.nodeId}){now.maxVisits ? `, visit ${now.visits}/${now.maxVisits}` : ''}
        </Text>
        <Text dimColor>blocks: {now.blocked.length ? now.blocked.join(', ') : 'nothing'}</Text>
        {now.edges.map(edge => (
          <Text>
            → {edge.id}: {edge.to}
            {edge.condition ? ` (${edge.condition})` : ''}
          </Text>
        ))}
        {now.warnings.map(warning => (
          <Text color="yellow">{warning}</Text>
        ))}
        {dead && dead.text ? <Text dimColor>tasky dead_ends: {dead.text}</Text> : null}
      </Box>
    )
  })
}
