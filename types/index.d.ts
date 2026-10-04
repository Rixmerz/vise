/** One read of vise's `graph_status`, reduced to what the mod draws. */
export type ViseStatus = {
  graph: string
  nodeId: string
  nodeName: string
  visits: number
  maxVisits: number
  blocked: string[]
  edges: { id: string; to: string; condition: string }[]
  warnings: string[]
}

/** What tasky's `dead_ends` answered for one phase, and which phase that was. */
export type DeadEnds = { phase: string; text: string }

declare module 'claude-code' {
  interface PluginState {
    'vise': {
      status: ViseStatus | null
      deadEnds: DeadEnds | null
    }
  }
}
