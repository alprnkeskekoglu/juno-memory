import assert from 'node:assert/strict'
import { effortLabel, formatLine1, plain } from '../hooks/register.js'

const now = Date.parse('2026-10-19T10:00:00Z')
const H = 3600 * 1000
const fh = { kind: 'five_hour', percentUsed: 12, resetsAt: new Date(now + 3 * H + 51 * 60000).toISOString() }
const wk = (p) => ({ kind: 'seven_day', percentUsed: p, resetsAt: new Date(now + 3.5 * 24 * H).toISOString() })
const status = { repo: 'webapp', ticket: 'ABC-333333', card_dirty: false }
const m = (over = {}) => ({ status, mine: true, usage: { context: { percent: 43 }, rateLimits: [fh, wk(58)] }, now, error: false, seen: {}, modelId: 'claude-opus-5-5', ...over })
const L1 = (mm, o) => plain(formatLine1(mm, o))

// task 2: seven_day only (plan without a 5-hour window) -> no 5h text at all
const wkOnly = (o) => L1(m({ usage: { context: { percent: 43 }, rateLimits: [wk(58)] } }), o)
assert.ok(!wkOnly({ cols: 100 }).includes('5h'), wkOnly({ cols: 100 }))
assert.ok(wkOnly({ cols: 100 }).includes('wk'))
assert.ok(!wkOnly({ cols: 100, ascii: true }).includes('5h'))
// before the first reading (no windows) the old behaviour stays
assert.ok(L1(m({ usage: { context: {}, rateLimits: [] } }), { cols: 100 }).endsWith('5h n/a  wk n/a'))
// five_hour only still shows wk n/a
assert.ok(L1(m({ usage: { context: {}, rateLimits: [fh] } }), { cols: 100 }).endsWith('5h 12% 3h51  wk n/a'))

// task 1: effort after the model label
for (const l of ['low', 'medium', 'high', 'xhigh', 'max']) assert.equal(effortLabel(l, 'wide'), l)
assert.equal(effortLabel('high', 'tiny'), '')
assert.equal(effortLabel(undefined, 'wide'), '')
assert.equal(effortLabel('bogus', 'wide'), '')
assert.equal(effortLabel(8000, 'wide'), '')
assert.ok(L1(m({ effort: 'high' }), { cols: 100 }).includes('opus 5.5 high · ABC-333333'))
assert.ok(L1(m({ effort: 'xhigh' }), { cols: 100 }).includes('opus 5.5 xhigh'))
assert.ok(!L1(m(), { cols: 100 }).includes('high'))
assert.ok(!L1(m({ effort: 'high' }), { cols: 50 }).includes('high'))
assert.ok(L1(m({ status: null, effort: 'max' }), { cols: 100 }).includes('opus 5.5 max · idle'))
assert.equal(formatLine1(m({ effort: 'high' }), { cols: 100 }).find((s) => s.t.includes('high'))?.dim, true)
// no model label -> no effort on its own
assert.ok(!L1(m({ modelId: undefined, effort: 'high' }), { cols: 100 }).includes('high'))

console.log('effort tests ok')
