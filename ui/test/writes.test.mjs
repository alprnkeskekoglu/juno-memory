import assert from 'node:assert/strict'
import { formatLine1, formatLine2, plain, writeState } from '../hooks/register.js'

const now = Date.parse('2026-10-19T10:00:00Z')
const usage = { context: { percent: 43 }, rateLimits: [] }
const base = { repo: 'webapp', ticket: 'ABC-333333', card_dirty: false, card_path: 'x.md' }
const m = (over = {}, mine = true) => ({ status: { ...base, writes: 12, write_cap: 50, ...over }, mine, usage, now, error: false, seen: {} })
const L1 = (mm, o) => plain(formatLine1(mm, o))
const L2 = (mm, o) => plain(formatLine2(mm, o))
const segOf = (mm, o) => formatLine1(mm, o).find((s) => /\d+\/\d+/.test(s.t))

// shown after the card marker, before the separator
assert.match(L1(m(), { cols: 140 }), /card ✓ · ✎ 12\/50  │  ctx/)
assert.match(L1(m(), { cols: 140, ascii: true }), /card ok · w 12\/50  \|  ctx/)
// narrow: compact under 80, dropped under 60
assert.match(L1(m(), { cols: 79 }), /✎12\/50/)
assert.match(L1(m(), { cols: 79, ascii: true }), /w12\/50/)
assert.doesNotMatch(L1(m(), { cols: 59 }), /12\/50/)
assert.match(L1(m(), { cols: 80 }), /✎ 12\/50/)

// colour: dim below 80%, orange from 80%, red with a warning sign at the cap
const dimSeg = segOf(m({ writes: 39 }), { cols: 140 })
assert.deepEqual([dimSeg.dim, dimSeg.c], [true, undefined])
assert.equal(segOf(m({ writes: 40 }), { cols: 140 }).c, '#ff8c00')
assert.equal(segOf(m({ writes: 49 }), { cols: 140 }).c, '#ff8c00')
assert.equal(segOf(m({ writes: 50 }), { cols: 140 }).c, 'red')
assert.match(L1(m({ writes: 50 }), { cols: 140 }), /✎ 50\/50 ⚠/)
assert.match(L1(m({ writes: 50 }), { cols: 140, ascii: true }), /w 50\/50 !/)
assert.match(L1(m({ writes: 55 }), { cols: 140 }), /✎ 55\/50 ⚠/)
assert.doesNotMatch(L1(m({ writes: 49 }), { cols: 140 }), /⚠/)
// a raised cap moves the thresholds
assert.equal(segOf(m({ writes: 50, write_cap: 60 }), { cols: 140 }).c, '#ff8c00')

// hidden when absent, malformed, or not this session's file
for (const over of [{ writes: undefined }, { write_cap: undefined }, { write_cap: 0 }, { writes: 'x' }, { writes: -1 }, { write_cap: null }]) {
  assert.doesNotMatch(L1(m(over), { cols: 140 }), /✎/, JSON.stringify(over))
}
assert.doesNotMatch(L1(m({}, false), { cols: 140 }), /✎/)
assert.equal(writeState({ writes: 1 }), null)
assert.deepEqual(writeState({ writes: 40, write_cap: 50 }), { used: 40, cap: 50, level: 'hot' })
assert.equal(writeState({ writes: 39, write_cap: 50 }).level, 'ok')
assert.equal(writeState({ writes: 50, write_cap: 50 }).level, 'full')

// line 2 attention item only at the cap
assert.equal(L2(m({ writes: 49 }), { cols: 140 }), '')
assert.match(L2(m({ writes: 50 }), { cols: 140 }), /⚠ vault write cap reached — juno cap raise --by N/)
assert.match(L2(m({ writes: 50 }), { cols: 140, ascii: true }), /! vault write cap reached -- juno cap raise --by N/)
assert.equal(formatLine2(m({ writes: 50 }, false), { cols: 140 }).find((s) => /vault write cap/.test(s.t)), undefined)
assert.equal(formatLine2(m({ writes: 50 }), { cols: 140 }).find((s) => /vault write cap/.test(s.t)).c, 'red')
console.log('writes.test.mjs ok')
