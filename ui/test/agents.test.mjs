import assert from 'node:assert/strict'
import { agentKind, agentModelLabel, agentStrip, plain, trackAgents } from '../hooks/register.js'

const now = Date.parse('2026-10-19T10:00:00Z')
const S = 1000
const info = (id, status, description = 'task', extra = {}) => ({ id, status, description, type: 'general-purpose', ...extra })
const line = (tracked, t, o) => plain(agentStrip(tracked, t, o))

// status mapping
assert.equal(agentKind('running'), 'run')
assert.equal(agentKind('pending'), 'run')
assert.equal(agentKind('waiting'), 'run')
assert.equal(agentKind('idle'), 'idle')
assert.equal(agentKind('completed'), 'done')
assert.equal(agentKind('failed'), 'fail')
assert.equal(agentKind('killed'), 'fail')
assert.equal(agentKind('weird'), null)

// model short names
assert.equal(agentModelLabel('claude-sonnet-4-5'), 'sonnet')
assert.equal(agentModelLabel('haiku'), 'haiku')
assert.equal(agentModelLabel('claude-opus-5-5[1m]'), 'opus')
assert.equal(agentModelLabel('claude-fable-5'), 'fable')
assert.equal(agentModelLabel('gpt-x'), '')
assert.equal(agentModelLabel(undefined), '')

// tracking: since = first seen running, models joined by id, main loop only
let t = trackAgents({}, [info('a', 'running', 'fix-cap'), info('b', 'running', 'log-scan', { parentId: 'a' })], now, { a: 'claude-sonnet-4-5' })
assert.deepEqual(Object.keys(t), ['a'])
assert.equal(t.a.since, now)
assert.equal(t.a.model, 'sonnet')
t = trackAgents(t, [info('a', 'running', 'fix-cap')], now + 134 * S, {})
assert.equal(t.a.since, now, 'since is kept')
assert.equal(t.a.model, 'sonnet', 'model is kept')

// running line, full width
assert.equal(line(t, now + 134 * S, { cols: 120 }), ' ╰ agents 1 · ◐ sonnet fix-cap 2:14')

// several: running first, then idle, finished after; count = running only
let t2 = trackAgents({}, [info('a', 'running', 'fix-cap'), info('b', 'running', 'log-scan'), info('c', 'completed', 'research')], now, { a: 'sonnet', b: 'haiku', c: 'haiku' })
assert.equal(line(t2, now + 40 * S, { cols: 200 }), ' ╰ agents 2 · ◐ sonnet fix-cap 0:40  ·  ◐ haiku log-scan 0:40  ·  ✓ haiku research')
t2 = trackAgents(t2, [info('a', 'running', 'fix-cap'), info('b', 'running', 'log-scan'), info('c', 'completed', 'research'), info('d', 'idle', 'mate')], now + 41 * S, {})
assert.match(line(t2, now + 41 * S, { cols: 200 }), /  ·  ◌ mate  ·  ✓ haiku research$/)

// unknown model: omitted
assert.equal(line(trackAgents({}, [info('a', 'running', 'x')], now, {}), now, { cols: 100 }), ' ╰ agents 1 · ◐ x 0:00')

// finished: stays 60 s from first seen finished, then the line is gone
let f = trackAgents({}, [info('a', 'running', 'job')], now, { a: 'haiku' })
f = trackAgents(f, [info('a', 'completed', 'job')], now + 10 * S, {})
assert.equal(f.a.endAt, now + 10 * S)
f = trackAgents(f, [info('a', 'completed', 'job')], now + 50 * S, {})
assert.equal(f.a.endAt, now + 10 * S, 'endAt is the first finished sighting')
assert.equal(line(f, now + 69 * S, { cols: 100 }), ' ╰ agents 0 · ✓ haiku job')
assert.equal(line(f, now + 70 * S, { cols: 100 }), '')
assert.deepEqual(agentStrip(f, now + 70 * S, { cols: 100 }), [])
assert.deepEqual(Object.keys(trackAgents(f, [info('a', 'completed', 'job')], now + 71 * S, {})), [], 'expired entries are dropped')

// failed: red x, same expiry
const bad = trackAgents({}, [info('a', 'failed', 'boom'), info('k', 'killed', 'stopped')], now, { a: 'opus' })
assert.equal(line(bad, now + 1 * S, { cols: 100 }), ' ╰ agents 0 · ✗ opus boom  ·  ✗ stopped')
assert.equal(line(bad, now + 61 * S, { cols: 100 }), '')

// an agent that finishes and runs again (resume) starts over
let r = trackAgents({}, [info('a', 'completed', 'job')], now, {})
r = trackAgents(r, [info('a', 'running', 'job')], now + 5 * S, {})
assert.equal(r.a.since, now + 5 * S)
assert.equal(r.a.endAt, null)

// nothing running, nothing finished: no line (idle alone does not show it)
assert.deepEqual(agentStrip({}, now, { cols: 100 }), [])
assert.deepEqual(agentStrip(trackAgents({}, [info('a', 'idle', 'mate')], now, {}), now, { cols: 100 }), [])

// width levels: 80+ full, under 80 no elapsed, under 60 only the count
const w = trackAgents({}, [info('a', 'running', 'fix-cap')], now, { a: 'sonnet' })
assert.equal(line(w, now + 134 * S, { cols: 80 }), ' ╰ agents 1 · ◐ sonnet fix-cap 2:14')
assert.equal(line(w, now + 134 * S, { cols: 79 }), ' ╰ agents 1 · ◐ sonnet fix-cap')
assert.equal(line(w, now + 134 * S, { cols: 60 }), ' ╰ agents 1 · ◐ sonnet fix-cap')
assert.equal(line(w, now + 134 * S, { cols: 59 }), ' ╰ agents 1')

// description cut to 18 chars with an ellipsis
const long = trackAgents({}, [info('a', 'running', 'refactor the whole payment module')], now, {})
assert.equal(line(long, now, { cols: 200 }), ' ╰ agents 1 · ◐ refactor the whol… 0:00')
assert.equal(line(trackAgents({}, [info('a', 'running', '123456789012345678')], now, {}), now, { cols: 200 }), ' ╰ agents 1 · ◐ 123456789012345678 0:00')

// cut to bodyColumns with an ellipsis
const cut = line(t2, now + 41 * S, { cols: 62 })
assert.equal([...cut].length, 62)
assert.ok(cut.endsWith('…'))

// ascii mode
assert.equal(line(w, now + 5 * S, { cols: 100, ascii: true }), ' `- agents 1 · * sonnet fix-cap 0:05')
const a = trackAgents({}, [info('a', 'running', 'r'), info('b', 'completed', 'd'), info('c', 'failed', 'f'), info('d', 'idle', 'i')], now, {})
assert.equal(line(a, now, { cols: 200, ascii: true }), ' `- agents 1 · * r 0:00  ·  . i  ·  ok d  ·  x f')

// colors: running blue, done green dim, failed red, idle dim
const segs = agentStrip(a, now, { cols: 200 })
const colorOf = (g) => segs.find((s) => s.t === g)
assert.equal(colorOf('◐').c, 'blue')
assert.equal(colorOf('✓').c, 'green')
assert.equal(colorOf('✓').dim, true)
assert.equal(colorOf('✗').c, 'red')
assert.equal(colorOf('◌').dim, true)

// garbage in: never throws
assert.deepEqual(trackAgents({}, null, now, null), {})
assert.deepEqual(trackAgents({}, [null, {}, { id: 5 }], now, {}), {})
assert.deepEqual(agentStrip(undefined, now, {}), [])
console.log('ok')
