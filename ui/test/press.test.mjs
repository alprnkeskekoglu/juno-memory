import assert from 'node:assert/strict'
import { cardPaneContent, doctorPaneContent, formatLine1, formatLine2, line2Items, plain, register } from '../hooks/register.js'

const now = Date.parse('2026-10-19T10:00:00Z')
const base = { repo: 'webapp', ticket: 'ABC-1', card_dirty: false, card_path: '/v/Projects/x/handoff.md' }
const m = (status, over = {}) => ({ status, mine: true, usage: { rateLimits: [] }, now, error: false, seen: {}, ...over })
const pressed = (segs) => segs.filter((s) => s.press).map((s) => s.press)

// which items are pressable
assert.deepEqual(pressed(formatLine1(m({ ...base, card_dirty: true }), { cols: 120 })), ['card-dirty'])
assert.deepEqual(pressed(formatLine1(m({ ...base, card_dirty: true }), { cols: 50 })), ['card-dirty'])
assert.deepEqual(pressed(formatLine1(m({ ...base }), { cols: 120 })), [], 'card ok: not pressable')
assert.deepEqual(pressed(formatLine1(m({ ...base, card_dirty: true, card_auto: true }), {})), [])
assert.deepEqual(pressed(formatLine2(m({ ...base, card_dirty: true, errors_24h: 3 }), {})), ['doctor', 'card-stale'])
assert.deepEqual(pressed(formatLine2(m({ ...base, errors_24h: 0 }), {})), [])
assert.deepEqual(pressed(formatLine2(m({ ...base, card_dirty: true, card_auto: true }), {})), [])
assert.deepEqual(pressed(formatLine2(m({ ...base }, { usage: { rateLimits: [{ kind: 'five_hour', percentUsed: 95 }] } }), {})), [], 'limit warning not pressable')
// text and colour unchanged
const items = line2Items({ ...base, card_dirty: true, errors_24h: 2 }, null, now, {}, {})
assert.equal(items[0].t, '⚠ 2 hook errors in 24h — juno doctor')
assert.equal(items[0].c, 'red')
assert.equal(items[1].c, '#ff8c00')
assert.ok(plain(formatLine1(m({ ...base, card_dirty: true }), { cols: 120 })).startsWith(' juno │ ABC-1 · ● dirty'))

// card pane
assert.deepEqual(cardPaneContent('# Card\nbody\n', 'p', 50), { dim: false, text: '# Card\nbody\n' })
assert.deepEqual(cardPaneContent(null, '/a/b.md', 50), { dim: true, text: 'Card file not found: /a/b.md' })
assert.equal(cardPaneContent(undefined, 'p').dim, true)
assert.equal(cardPaneContent('  \n', 'p').dim, true)
assert.equal(cardPaneContent('a\nb\nc\nd', 'p', 2).text, 'a\nb')
assert.equal([...cardPaneContent('x'.repeat(50000), 'p', 50).text].length, 20000)

// doctor pane
assert.deepEqual(doctorPaneContent({ running: true }), { dim: true, text: 'running…' })
assert.deepEqual(doctorPaneContent({ result: { exitCode: 0, stdout: 'ok\n', stderr: 'noise' } }, 50), { dim: false, text: 'ok' })
assert.equal(doctorPaneContent({ result: { exitCode: 1, stdout: 'a', stderr: 'boom' } }, 50).text, 'a\nboom')
assert.equal(doctorPaneContent({ result: { exitCode: 2, stdout: '', stderr: 'boom' } }, 50).text, 'boom')
assert.deepEqual(doctorPaneContent({ result: { exitCode: 0, stdout: '', stderr: '' } }), { dim: true, text: 'no output' })
assert.equal(doctorPaneContent({ error: 'spawn juno ENOENT\nstack' }).text, 'juno doctor could not run: spawn juno ENOENT')

// hook wiring with a fake host: press runs the right calls, drawing and polling never run a process
const hooks = []
register((...a) => hooks.push(a))
const band = hooks.find((h) => h[1]?.component === 'AbovePrompt')
const cardHook = hooks.find((h) => h[1]?.requestId === 'juno-card')
const docHook = hooks.find((h) => h[1]?.requestId === 'juno-doctor')
assert.equal(cardHook[1].component, 'Pane')
assert.equal(docHook[1].component, 'Pane')
const el = (type) => (props) => ({ type, props })
const calls = []
const tick = () => new Promise((r) => setTimeout(r, 0))
const $ = {
  clock: { every: () => ({ cancel() {} }), now: async () => now },
  env: { get: async (k) => ({ HOME: '/h' })[k] },
  session: { id: async () => 'sid1', usage: async () => ({ rateLimits: [] }), model: async () => 'claude-opus-5-5' },
  fs: {
    stat: async () => ({ mtimeMs: now }),
    read: async (p) => {
      calls.push(['read', p])
      if (p.endsWith('.json')) return JSON.stringify({ ...base, card_dirty: true, errors_24h: 1, segment_id: 'sid1' })
      return 'CARD TEXT'
    },
  },
  process: { run: async (argv) => { calls.push(['run', argv]); return { exitCode: 1, stdout: 'out', stderr: 'err' } } },
  ui: {
    resolve: () => ({ Box: el('Box'), Text: el('Text'), Button: el('Button') }),
    open: async (a) => { calls.push(['open', a]) },
    close: async (a) => { calls.push(['close', a]) },
    invalidate() {}, log() {},
  },
}
const start = hooks.find((h) => h[0] === 'session.start')
await start[1]($, {}, async (e) => e)
await tick()
assert.ok(!calls.some((c) => c[0] === 'run'), 'no process on start or poll')
const tree = await band[2]($, { props: { bodyColumns: 120 } }, async () => null)
const buttons = []
const walk = (n) => { if (n?.type === 'Button') buttons.push(n); for (const c of [].concat(n?.props?.children ?? [])) walk(c) }
walk(tree)
assert.deepEqual(buttons.map((b) => b.props.key).sort(), ['card-dirty', 'card-stale', 'doctor'])
assert.ok(buttons.every((b) => b.props.plain === true && b.props.children[0].props.underline === true))
assert.ok(!calls.some((c) => c[0] === 'run'), 'drawing never runs a process')

buttons.find((b) => b.props.key === 'card-stale').props.onPress()
await tick()
assert.deepEqual(calls.filter((c) => c[0] === 'open'), [['open', { id: 'juno-card', title: 'Juno card' }]])
assert.ok(calls.some((c) => c[0] === 'read' && c[1] === base.card_path))
let pane = await cardHook[2]($, { viewport: { rows: 24 } }, async () => null)
assert.equal(pane.props.children[1].props.children, 'CARD TEXT')
assert.equal(pane.props.children[0].props.role, 'dismiss')
pane.props.children[0].props.onPress()
assert.deepEqual(calls.at(-1), ['close', { id: 'juno-card' }])

buttons.find((b) => b.props.key === 'doctor').props.onPress()
await tick()
assert.deepEqual(calls.filter((c) => c[0] === 'run').map((c) => c[1]), [['juno', 'doctor']])
pane = await docHook[2]($, {}, async () => null)
assert.equal(pane.props.children[1].props.children, 'out\nerr')

// a throwing resolve falls through to next
let nexted = false
await cardHook[2]({ ui: { resolve() { throw new Error('x') } } }, {}, async () => { nexted = true })
assert.ok(nexted)
console.log('press.test.mjs ok')
