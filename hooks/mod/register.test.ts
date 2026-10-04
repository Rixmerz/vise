import type { On } from 'claude-code'
import { describe, expect, test } from 'claude-code/testing'

import {
  deadEndsNote,
  fixPhaseKey,
  movesWorkflow,
  payload,
  phaseSection,
  serverFor,
  statusLine,
  toStatus,
} from './status'

// The shape vise's `graph_status` returns (src/vise/tools/_graph_query.py),
// trimmed to the fields the mod reads plus a few it must ignore.
const FIX = {
  session_id: 's1',
  graph_name: 'Universal Debug',
  current_node: {
    id: 'fix',
    name: 'Fix',
    mcps_enabled: [],
    tools_blocked: ['WebFetch'],
    is_end: false,
    visits: 1,
    max_visits: 5,
  },
  available_edges: [
    { id: 'fix-to-verify', to: 'verify', to_name: 'Verify', condition_type: 'validators_green', priority: 1 },
  ],
  total_transitions: 6,
  warnings: null,
  enabled: true,
  prompt_injection: 'a long phase prompt the section must not repeat',
  project_dir: '/repo',
}

const INACTIVE = { session_id: 's1', active: false, message: 'No active workflow', project_dir: '/repo' }

const DEAD = '- problem #3 login redirect loop (repo app): cleared the cookie on refresh | why: the token was stale'

const asText = (value: unknown) => ({ content: [{ type: 'text', text: JSON.stringify(value) }], isError: false })

describe('reading graph_status', () => {
  test('an active workflow becomes a status', () => {
    const status = toStatus(payload(asText(FIX)))
    expect(status).toEqual({
      graph: 'Universal Debug',
      nodeId: 'fix',
      nodeName: 'Fix',
      visits: 1,
      maxVisits: 5,
      blocked: ['WebFetch'],
      edges: [{ id: 'fix-to-verify', to: 'Verify', condition: 'validators_green' }],
      warnings: [],
    })
  })

  test('"nothing is active" and an error are both no status', () => {
    expect(toStatus(payload(asText(INACTIVE)))).toBe(null)
    expect(toStatus(payload(asText({ error: true, message: 'parse error' })))).toBe(null)
    expect(toStatus(payload({ content: [{ type: 'text', text: 'not json' }], isError: false }))).toBe(null)
  })

  test('structured content is read before text', () => {
    expect(payload({ content: [], structuredContent: { result: FIX } })?.graph_name).toBe('Universal Debug')
  })

  test('the section says where the session is and how it leaves, not the phase prompt', () => {
    const section = phaseSection(toStatus(FIX)!)
    expect(section).toContain('`Universal Debug`')
    expect(section).toContain('`fix-to-verify` → Verify (validators_green)')
    expect(section).toContain('graph_traverse(edge_id=...)')
    expect(section.includes('a long phase prompt')).toBe(false)
    expect(statusLine(toStatus(FIX)!)).toBe('vise · Universal Debug › Fix 1/5')
  })
})

describe('when to read again', () => {
  test("a vise call that moves the workflow, and nothing else", () => {
    expect(movesWorkflow('mcp__plugin_vise_vise__graph_traverse')).toBe(true)
    expect(movesWorkflow('mcp__plugin_vise_vise__graph_status')).toBe(false)
    expect(movesWorkflow('mcp__other__graph_traverse')).toBe(false)
    expect(movesWorkflow('Edit')).toBe(false)
  })
})

describe('the tasky bridge', () => {
  test('only the fix phases ask, once per visit', () => {
    expect(fixPhaseKey(toStatus(FIX))).toBe('Universal Debug:fix:1')
    expect(fixPhaseKey(toStatus({ ...FIX, current_node: { ...FIX.current_node, id: 'analyze' } }))).toBe(null)
    expect(fixPhaseKey(null)).toBe(null)
  })

  test('no recorded failure adds nothing to the conversation', () => {
    expect(deadEndsNote('No failed attempts recorded.')).toBe(null)
    expect(deadEndsNote('')).toBe(null)
    expect(deadEndsNote(DEAD)).toContain('problem #3')
  })

  test('the server is read off the tool list, by name', () => {
    const tools = ['Read', 'mcp__other__graph_status', 'mcp__plugin_vise_vise__graph_status', 'mcp__plugin_tasky_tasky__dead_ends']
    expect(serverFor(tools, 'graph_status', 'vise')).toBe('plugin_vise_vise')
    expect(serverFor(tools, 'dead_ends', 'tasky')).toBe('plugin_tasky_tasky')
    expect(serverFor(['Read'], 'dead_ends', 'tasky')).toBe(null)
  })
})

// Nothing sits beneath the plugin in a test: these stand for the engine's own
// answers to the events the mod passes on.
function engine(on: On) {
  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('turn.start', ($, e) => ({ turnId: e.turnId }))
  on('command.register', ($, e) => ({ value: { command: e.name } }))
}

describe('in the engine', () => {
  test('the status line, the system prompt section and the failed fixes', async ($, on) => {
    engine(on)
    const asked: string[] = []
    const statuses: (string | undefined)[] = []
    on('tool.list', () => ({
      value: [
        { name: 'mcp__plugin_vise_vise__graph_status', description: '', mcp: true },
        { name: 'mcp__plugin_tasky_tasky__dead_ends', description: '', mcp: true },
      ],
    }))
    on('session.cwd', () => ({ value: '/repo' }))
    on('mcp.call', ($, e) => {
      asked.push(`${e.server}/${e.tool}`)
      return {
        value: e.tool === 'graph_status' ? asText(FIX) : { content: [{ type: 'text', text: DEAD }], isError: false },
      }
    })
    on('ui.status', ($, e) => {
      statuses.push(e.text)
      return { value: undefined }
    })
    on('prompt.compose', () => ({ sections: [{ id: 'intro', text: 'You are Claude.', scope: 'shared' as const }] }))

    await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })

    expect(statuses.at(-1)).toBe('vise · Universal Debug › Fix 1/5')
    expect(asked).toEqual(['plugin_vise_vise/graph_status', 'plugin_tasky_tasky/dead_ends'])
    // The row itself cannot be observed here: a test's hook cannot stand for
    // the bottom of session.append, which only the engine's store answers.
    // What tasky said is drawn in the pane, and deadEndsNote is tested above.
    for (const surface of ['terminal', 'desktop'] as const) {
      const pane = await $.ui.mount({
        plugin: 'vise',
        surface,
        component: 'Pane',
        requestId: 'vise-workflow',
        props: {
          title: 'vise workflow',
          isFocused: false,
          bodyColumns: 80,
          placement: 'dock',
          scroll: { offset: 0, bodyRows: 30 },
          view: {},
        },
      })
      expect(await pane.find({ type: 'Text', text: 'fix-to-verify' })).toBeDefined()
      expect(await pane.find({ type: 'Text', text: /problem #3/ })).toBeDefined()
      await pane.unmount()
    }

    const composed = await $.prompt.compose({
      model: 'claude-opus-5-5',
      promptModel: 'claude-opus-5-5',
      surfaces: ['terminal'],
      tools: [],
      outputStyle: null,
      traits: [],
    })
    const section = composed.sections.find(s => s.id === 'vise:phase')
    expect(section?.scope).toBe('session')
    expect(section?.text).toContain('phase `Fix`')

    // A second turn on the same visit does not ask tasky again.
    await $.turn.start({ text: 'go on', turnId: 't2' })
    expect(asked.filter(a => a.endsWith('/dead_ends')).length).toBe(1)
  })

  test('the options turn both additions off', { options: { phase_in_system_prompt: false, tasky_dead_ends: false } }, async ($, on) => {
    engine(on)
    const asked: string[] = []
    on('tool.list', () => ({ value: [{ name: 'mcp__plugin_vise_vise__graph_status', description: '', mcp: true }] }))
    on('session.cwd', () => ({ value: '/repo' }))
    on('mcp.call', ($, e) => {
      asked.push(e.tool)
      return { value: asText(FIX) }
    })
    on('prompt.compose', () => ({ sections: [{ id: 'intro', text: 'You are Claude.', scope: 'shared' as const }] }))

    await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
    const composed = await $.prompt.compose({
      model: 'claude-opus-5-5',
      promptModel: 'claude-opus-5-5',
      surfaces: ['terminal'],
      tools: [],
      outputStyle: null,
      traits: [],
    })
    expect(composed.sections.map(s => s.id)).toEqual(['intro'])
    expect(asked).toEqual(['graph_status'])
  })

  test('with vise unreachable the session goes on and nothing is drawn', async ($, on) => {
    engine(on)
    const statuses: (string | undefined)[] = []
    on('tool.list', () => ({ value: [] }))
    on('session.cwd', () => ({ value: '/repo' }))
    on('mcp.call', () => {
      throw new Error('no such server')
    })
    on('ui.status', ($, e) => {
      statuses.push(e.text)
      return { value: undefined }
    })
    const started = await $.session.start({ cwd: '/repo', surface: 'terminal', isInteractive: true })
    expect(started.cwd).toBe('/repo')
    expect(statuses.length).toBe(0)
  })
})
