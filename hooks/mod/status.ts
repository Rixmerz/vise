// Pure functions over what vise's and tasky's MCP tools answer. No `$` here,
// so the tests can hold them to the shapes the Python side actually returns.

import type { ViseStatus } from '../../types'

/** The debug workflow's phases where a fix is chosen or applied. */
export const FIX_PHASES: readonly string[] = ['hypothesize', 'fix']

/**
 * vise tools whose call can move the workflow: the mod reads `graph_status`
 * again after any of them. `test_neighbour_contract.py` holds each name to
 * the tools vise actually registers.
 */
export const MOVING_TOOLS: readonly string[] = [
  'graph_activate',
  'graph_deactivate',
  'graph_traverse',
  'graph_reset',
  'graph_set_node',
  'graph_task_complete',
  'graph_check_phrase',
  'graph_check_tool',
  'graph_override_max_visits',
]

/** Whether a tool name the model called is one of vise's that moves the workflow. */
export function movesWorkflow(toolName: string): boolean {
  if (!toolName.startsWith('mcp__') || !toolName.toLowerCase().includes('vise')) return false
  return MOVING_TOOLS.some(tool => toolName.endsWith(`__${tool}`))
}

/** What tasky's `dead_ends` answers when the repository has none. */
export const NO_DEAD_ENDS = 'No failed attempts recorded.'

type Block = { type: string; text?: string }

/**
 * The JSON object a tool returned: its structured content when the server
 * declared an output schema, else the first text block that parses as one.
 * vise's tools return dicts, which FastMCP sends as a JSON text block.
 */
export function payload(result: {
  content: readonly Block[]
  structuredContent?: unknown
  isError?: boolean
}): Record<string, unknown> | null {
  const structured = result.structuredContent
  if (structured && typeof structured === 'object') {
    const inner = (structured as { result?: unknown }).result
    return (inner && typeof inner === 'object' ? inner : structured) as Record<string, unknown>
  }
  for (const block of result.content) {
    if (block.type !== 'text' || !block.text) continue
    try {
      const parsed: unknown = JSON.parse(block.text)
      if (parsed && typeof parsed === 'object') return parsed as Record<string, unknown>
    } catch {
      // Not JSON: keep looking.
    }
  }
  return null
}

/** The text of every text block, joined: what tasky's tools return. */
export function text(result: { content: readonly Block[] }): string {
  return result.content
    .filter(block => block.type === 'text' && block.text)
    .map(block => block.text)
    .join('\n')
    .trim()
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : []
}

/**
 * `graph_status` reduced to what the mod shows, or null when no workflow is
 * active. vise answers `active: false` for "nothing is active", which is a
 * state and not an error, and `error: true` when it could not read one.
 */
export function toStatus(raw: Record<string, unknown> | null): ViseStatus | null {
  if (!raw || raw.active === false || raw.error === true) return null
  const node = raw.current_node as Record<string, unknown> | undefined
  if (!node || typeof node.id !== 'string') return null
  const edges = Array.isArray(raw.available_edges) ? raw.available_edges : []
  return {
    graph: typeof raw.graph_name === 'string' ? raw.graph_name : 'workflow',
    nodeId: node.id,
    nodeName: typeof node.name === 'string' && node.name ? node.name : node.id,
    visits: typeof node.visits === 'number' ? node.visits : 0,
    maxVisits: typeof node.max_visits === 'number' ? node.max_visits : 0,
    blocked: strings(node.tools_blocked),
    edges: edges
      .filter((edge): edge is Record<string, unknown> => !!edge && typeof edge === 'object')
      .map(edge => ({
        id: String(edge.id ?? ''),
        to: String(edge.to_name ?? edge.to ?? ''),
        condition: String(edge.condition_type ?? ''),
      }))
      .filter(edge => edge.id),
    warnings: strings(raw.warnings),
  }
}

/** One line for the status bar. */
export function statusLine(status: ViseStatus): string {
  const visits = status.maxVisits ? ` ${status.visits}/${status.maxVisits}` : ''
  return `vise · ${status.graph} › ${status.nodeName}${visits}`
}

/**
 * The system prompt section for the active phase. Short on purpose: the
 * phase's own prompt reaches the model through vise's hooks, and this is the
 * part that has to survive a compaction: where the session is and how it
 * leaves.
 */
export function phaseSection(status: ViseStatus): string {
  const lines = [
    `vise holds this session in workflow \`${status.graph}\`, phase \`${status.nodeName}\` (\`${status.nodeId}\`)` +
      (status.maxVisits ? `, visit ${status.visits} of ${status.maxVisits}.` : '.'),
    status.blocked.length
      ? `This phase blocks: ${status.blocked.join(', ')}.`
      : 'This phase blocks no tools.',
  ]
  if (status.edges.length) {
    lines.push(
      'Edges out of it: ' +
        status.edges.map(edge => `\`${edge.id}\` → ${edge.to}${edge.condition ? ` (${edge.condition})` : ''}`).join('; ') +
        '.',
    )
    lines.push(
      "Cross one with graph_traverse(edge_id=...) when the phase's work is done; the node's validators decide whether it opens.",
    )
  }
  for (const warning of status.warnings) lines.push(`Warning: ${warning}`)
  return lines.join('\n')
}

/** Where a fix is about to be chosen, keyed so one visit asks tasky once. */
export function fixPhaseKey(status: ViseStatus | null): string | null {
  if (!status || !FIX_PHASES.includes(status.nodeId)) return null
  return `${status.graph}:${status.nodeId}:${status.visits}`
}

/** The row added to the conversation, or null when there is nothing to warn of. */
export function deadEndsNote(answer: string): string | null {
  const body = answer.trim()
  if (!body || body === NO_DEAD_ENDS) return null
  return (
    'tasky (dead_ends): fixes this repository already applied, believed correct, and saw fail. ' +
    'Do not apply one of them again unless you can say why it would work now.\n' +
    body
  )
}

/**
 * The server name `$.mcp.call` takes, read off the tool list: the part
 * between `mcp__` and `__<tool>`. Several servers may expose a tool of that
 * name, so the one whose name mentions `hint` wins.
 */
export function serverFor(toolNames: readonly string[], tool: string, hint: string): string | null {
  const suffix = `__${tool}`
  for (const name of toolNames) {
    if (!name.startsWith('mcp__') || !name.endsWith(suffix)) continue
    const server = name.slice('mcp__'.length, name.length - suffix.length)
    if (server.toLowerCase().includes(hint)) return server
  }
  return null
}
