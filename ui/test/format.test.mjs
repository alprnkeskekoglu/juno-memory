import assert from 'node:assert/strict'
import {
  addTurnUsage, advanceSeen, bandChildren, bar, cacheColor, cacheRatio, capsFrom, costText, countdown, debugLine, dotCount, formatLine1, formatLine2, formatStatus,
  labelFor, levelColor, line2Items, modelLabel, pace, pickStatus, plain, projectedPercent, readStatus, sessionStatusPaths, sortedWindows, statusPaths, weekWarning,
} from '../hooks/register.js'

const now = Date.parse('2026-10-19T10:00:00Z')
const H = 3600 * 1000
const fh = { kind: 'five_hour', percentUsed: 12, resetsAt: new Date(now + 3 * H + 51 * 60000).toISOString() }
// 3.5 days left: 50% of the window elapsed.
const wk = (p, left = 3.5 * 24 * H) => ({ kind: 'seven_day', percentUsed: p, resetsAt: new Date(now + left).toISOString() })
const usage = { context: { percent: 43 }, rateLimits: [fh, wk(58)] }
const status = { repo: 'webapp', ticket: 'ABC-333333', card_dirty: false, card_path: 'Projects/x/handoff.md' }
const m = (over = {}) => ({ status, mine: true, usage, now, error: false, seen: {}, ...over })
const L1 = (mm, o) => plain(formatLine1(mm, o))

// bars: default is five dots, filled ● (threshold colour), empty ○ (dimmed); filled = round(percent / 20), none under 10
assert.equal(bar(0), '○○○○○')
assert.equal(bar(6), '○○○○○')
assert.equal(bar(10), '●○○○○')
assert.equal(bar(25), '●○○○○')
assert.equal(bar(49), '●●○○○')
assert.equal(bar(50), '●●●○○')
assert.equal(bar(62), '●●●○○')
assert.equal(bar(99), '●●●●●')
assert.equal(bar(100), '●●●●●')
assert.equal(bar(43), '●●○○○')
assert.equal(bar(43).length, 5)
assert.equal(bar(12), '●○○○○')
assert.equal(bar(250), '●●●●●')
assert.equal(bar(-5), '○○○○○')
for (const [p, n] of [[0, 0], [6, 0], [9.9, 0], [10, 1], [25, 1], [30, 2], [49, 2], [50, 3], [62, 3], [99, 5], [100, 5]]) {
  assert.equal(dotCount(p), n, `dotCount(${p})`)
}
// ascii fallback
assert.equal(bar(0, true), '[-----]')
assert.equal(bar(60, true), '[###--]')
assert.equal(bar(100, true), '[#####]')
// opt-in eighth-block bars (JUNO_UI_FINE_BARS=1)
assert.equal(bar(50, false, true), '██▌░░')
assert.equal(bar(43, false, true), '██▏░░')
assert.equal(bar(12, false, true), '▋░░░░')
assert.equal(bar(49, false, true), '██▌░░')
assert.equal(bar(62, false, true), '███▏░')
// default: exactly 5 dots, only ● and ○, filled count = dotCount for every percent
for (let p = 0; p <= 100; p++) {
  const b = [...bar(p)]
  assert.equal(b.length, 5, `dots ${p}`)
  assert.ok(b.every((c) => c === '●' || c === '○'), `dots ${p} glyphs`)
  assert.equal(b.filter((c) => c === '●').length, dotCount(p), `dots ${p} filled`)
}
// fine mode: 5 cells, track = 5 - full - partial, at most one partial
for (let p = 0; p <= 100; p++) {
  const b = [...bar(p, false, true)]
  const full = b.filter((c) => c === '█').length
  const partial = b.filter((c) => '▏▎▍▌▋▊▉'.includes(c)).length
  assert.equal(b.length, 5, `fine ${p}`)
  assert.ok(partial <= 1, `fine ${p} partials`)
  assert.equal(b.filter((c) => c === '░').length, 5 - full - partial, `fine track ${p}`)
}

// model labels
assert.equal(modelLabel('claude-opus-5-5', 'full'), 'opus 5.5')
assert.equal(modelLabel('claude-sonnet-5-5', 'full'), 'sonnet 5.5')
assert.equal(modelLabel('claude-haiku-4-5', 'full'), 'haiku 4.5')
assert.equal(modelLabel('claude-opus-5-5[1m]', 'full'), 'opus 5.5·1M')
assert.equal(modelLabel('claude-opus-5-5-20260101', 'num'), 'opus 5.5')
assert.equal(modelLabel('claude-opus-5-5', 'tiny'), 'opus')
assert.equal(modelLabel('claude-opus-5-5[1m]', 'tiny'), 'opus·1M')
assert.equal(modelLabel('mystery-model', 'full'), 'mystery-model')
assert.equal(modelLabel('x'.repeat(30), 'full'), 'x'.repeat(20))
assert.equal(modelLabel(undefined, 'full'), '')
assert.equal(modelLabel('', 'full'), '')

// colour thresholds
for (const [p, c] of [[0, 'blue'], [49.9, 'blue'], [50, 'yellow'], [69, 'yellow'], [70, '#ff8c00'], [89, '#ff8c00'], [90, 'red'], [100, 'red']]) {
  assert.equal(levelColor(p), c, `levelColor(${p})`)
}

// rendered ctx meter at the sample levels: filled dots in the threshold colour, empty dots dimmed and uncoloured
const LEVELS = [
  [0, 0, 'blue'], [6, 0, 'blue'], [10, 1, 'blue'], [25, 1, 'blue'], [49, 2, 'blue'],
  [50, 3, 'yellow'], [62, 3, 'yellow'], [99, 5, 'red'], [100, 5, 'red'],
]
const dotSegsOf = (segs) => segs.filter((s) => /^[●○]+$/.test(s.t))
for (const [p, full, color] of LEVELS) {
  const segs = formatLine1(m({ usage: { context: { percent: p }, rateLimits: [] } }), { cols: 120 })
  const dots = dotSegsOf(segs)
  const fillSegs = dots.filter((s) => s.t.includes('●'))
  const trackSegs = dots.filter((s) => s.t.includes('○'))
  const fillCells = fillSegs.map((s) => s.t).join('')
  const trackCells = trackSegs.map((s) => s.t).join('')
  assert.equal([...fillCells].length, full, `${p}% filled dots`)
  assert.equal([...trackCells].length, 5 - full, `${p}% empty dots`)
  assert.equal([...fillCells, ...trackCells].length, 5, `${p}% total dots`)
  assert.equal(fillSegs.length, full ? 1 : 0, `${p}% fill segments`)
  assert.equal(trackSegs.length, full === 5 ? 0 : 1, `${p}% track segments`)
  for (const s of fillSegs) assert.equal(s.c, color, `${p}% fill colour`)
  assert.equal(color, levelColor(p), `${p}% colour matches levelColor`)
  for (const s of trackSegs) {
    assert.equal(s.dim, true, `${p}% empty dots dimmed`)
    assert.equal(s.c, undefined, `${p}% empty dots have no colour`)
  }
  assert.ok(plain(segs).includes(`ctx ${bar(p)} ${Math.round(p)}%`), `${p}% number printed after dots`)
}
// no spacer between dots
assert.ok(L1(m({ usage: { context: { percent: 62 }, rateLimits: [] } }), { cols: 120 }).includes('ctx ●●●○○ 62%'))
// fine mode renders the same segment split with eighth blocks
assert.ok(L1(m({ usage: { context: { percent: 49 }, rateLimits: [] } }), { cols: 120, fine: true }).includes('ctx ██▌░░ 49%'))
assert.ok(!/[▏▎▍▌▋▊▉]/.test(L1(m(), { cols: 120 })))

// line 1 at several widths
const w140 = L1(m(), { cols: 140 })
assert.ok([...w140].length <= 140)
assert.ok(w140.endsWith('wk ' + bar(58) + ' 58% ⇡8'))
const wide = L1(m(), { cols: 120 })
assert.ok([...wide].length <= 120)
assert.ok(wide.startsWith(' juno │ ABC-333333 · card ✓ '))
assert.ok(wide.endsWith(`ctx ${bar(43)} 43%  5h ${bar(12)} 12% 3h51  wk ${bar(58)} 58% ⇡8`))
const mid = L1(m(), { cols: 100 })
assert.ok(mid.endsWith('ctx 43%  5h 12% 3h51  wk 58% ⇡8'), mid)
assert.ok(!/[█░●○▏▎▍▌▋▊▉]/.test(mid))
const low = L1(m(), { cols: 70 })
assert.ok(low.endsWith('ctx 43%  5h 12%  wk 58%'), low)
assert.ok(!low.includes('3h51') && !low.includes('⇡'))
const tiny = L1(m(), { cols: 50 })
assert.ok(tiny.startsWith(' juno │ ABC-333333 · ✓ '), tiny)
assert.ok(tiny.endsWith('ctx 43%  wk 58%'), tiny)
assert.ok(!tiny.includes('5h'))
assert.ok([...tiny].length <= 50)
// left-aligned: no flex spacer pushes the meters to the edge
assert.ok(wide.includes('  │  ctx '), wide)
assert.ok(L1(m({ status: { ...status, card_dirty: true } }), { cols: 50 }).includes('ABC-333333 · ●'))
// no width known: fixed gap
assert.ok(L1(m(), {}).includes('card ✓  │  ctx'))
// model label after "juno │", dropped version at the narrowest width
const withModel = (over = {}) => m({ modelId: 'claude-opus-5-5', ...over })
assert.ok(L1(withModel(), { cols: 120 }).startsWith(' juno │ opus 5.5 · ABC-333333 · card ✓  │  ctx '))
assert.ok(L1(withModel({ modelId: 'claude-sonnet-5-5[1m]' }), { cols: 120 }).startsWith(' juno │ sonnet 5.5·1M · ABC-333333'))
assert.ok(L1(withModel(), { cols: 50 }).startsWith(' juno │ opus · ABC-333333 · ✓  │  ctx 43%  wk 58%'))
assert.ok(L1(withModel({ status: null }), { cols: 100 }).startsWith(' juno │ opus 5.5 · idle'))
assert.ok(L1(withModel({ modelId: 'mystery-model' }), { cols: 120 }).startsWith(' juno │ mystery-model · ABC-333333'))
assert.equal(L1(withModel({ error: true }), { cols: 100 }), ' juno │ ?')
// the empty dots of each meter are dimmed, the filled dots keep their colour
const barParts = formatLine1(m(), { cols: 120 })
assert.ok(barParts.some((s) => s.t === '○○○' && s.dim === true && s.c === undefined))
assert.ok(barParts.some((s) => s.t === '●●' && s.c === levelColor(43)))
// Claude Code's own band content is kept beneath ours, engine references included
const rowA = { type: 'Box', id: 'a' }
const engineRef = { type: 'engine', ref: 0 }
const pluginTree = { type: 'Box', id: 'other' }
assert.deepEqual(bandChildren([rowA], engineRef), [rowA, engineRef])
assert.deepEqual(bandChildren([rowA], pluginTree), [rowA, pluginTree])
assert.deepEqual(bandChildren([rowA], undefined), [rowA])
// repo when no ticket; omit missing parts
assert.ok(L1(m({ status: { repo: 'webapp' } }), { cols: 100 }).startsWith(' juno │ webapp · card ✓'))
assert.ok(L1(m({ status: {} }), { cols: 100 }).startsWith(' juno │ card ✓'))
assert.ok(L1(m({ status: { ticket: null, repo: 'webapp' } }), { cols: 100 }).startsWith(' juno │ webapp · card ✓'))
assert.ok(L1(m({ status: { ticket: 'T-1', card_dirty: true } }), { cols: 100 }).includes('T-1 · ● dirty'))
// ticket and repo only from this session's own status
assert.ok(L1(m({ mine: false, status: { repo: 'webapp', ticket: 'T-1' } }), { cols: 100 }).startsWith(' juno │ card ✓'))
assert.ok(L1(m({ mine: undefined }), { cols: 100 }).startsWith(' juno │ card ✓'))
assert.ok(L1(m({ status: { ticket: null, repo: null, card_dirty: true } }), { cols: 100 }).startsWith(' juno │ ● dirty'))
// width never exceeded
for (const cols of [30, 45, 59, 60, 79, 80, 109, 110, 200]) {
  const t = L1(m({ usage: { context: { percent: 95 }, rateLimits: [fh, wk(99), { kind: 'x_extra', percentUsed: 5 }] } }), { cols })
  assert.ok([...t].length <= cols, `${cols}: ${[...t].length}`)
}

// idle and error
const idle = L1(m({ status: null }), { cols: 100 })
assert.ok(idle.startsWith(' juno │ idle'))
assert.ok(idle.includes('ctx 43%'))
assert.equal(L1(m({ error: true }), { cols: 100 }), ' juno │ ?')
assert.deepEqual(formatLine1(m({ error: true }), {}), [{ t: ' juno │ ?', dim: true }])
assert.deepEqual(formatLine2(m({ error: true, status: { ...status, errors_24h: 3 } }), {}), [])
assert.equal(L1(m({ error: true }), { ascii: true }), ' juno | ?')

// meters: colours, warn, hide ctx, n/a, ordering, other kinds
const segsOf = (mm, o) => formatLine1(mm, o)
const find = (segs, text) => segs.find((s) => s.t.includes(text))
const hot = segsOf(m({ usage: { context: { percent: 95 }, rateLimits: [] } }), { cols: 120 })
assert.equal(find(hot, '95%').c, 'red')
assert.ok(plain(hot).includes('95%⚠'))
assert.equal(find(segsOf(m({ usage: { context: { percent: 75 }, rateLimits: [] } }), { cols: 120 }), '75%').c, '#ff8c00')
assert.equal(find(segsOf(m({ usage: { context: { percent: 55 }, rateLimits: [] } }), { cols: 120 }), '55%').c, 'yellow')
assert.equal(find(segsOf(m({ usage: { context: { percent: 10 }, rateLimits: [] } }), { cols: 120 }), '10%').c, 'blue')
assert.ok(!plain(segsOf(m({ usage: { context: { percent: 89 }, rateLimits: [] } }), { cols: 120 })).includes('⚠'))
const noCtx = L1(m({ usage: { context: {}, rateLimits: [] } }), { cols: 100 })
assert.ok(!noCtx.includes('ctx') && noCtx.endsWith('5h n/a  wk n/a'), noCtx)
assert.ok(L1(m({ usage: undefined }), { cols: 100 }).endsWith('5h n/a  wk n/a'))
const opus = { kind: 'seven_day_opus', percentUsed: 48, resetsAt: wk(1).resetsAt }
const ord = (rl) => L1(m({ usage: { context: {}, rateLimits: rl } }), { cols: 120 })
assert.equal(ord([opus, wk(58), fh]), ord([fh, opus, wk(58)]))
assert.ok(ord([opus, wk(58), fh]).indexOf('5h') < ord([opus, wk(58), fh]).indexOf('wk'))
assert.ok(ord([opus, wk(58), fh]).indexOf('wk') < ord([opus, wk(58), fh]).indexOf('seven_day_opus'))
assert.ok(ord([opus, wk(58), fh]).endsWith(`seven_day_opus ${bar(48)} 48%`))
assert.ok(L1(m({ usage: { context: {}, rateLimits: [fh] } }), { cols: 100 }).endsWith('5h 12% 3h51  wk n/a'))
assert.deepEqual(sortedWindows([wk(1), wk(9)]).map((w) => w.percentUsed), [9])
assert.equal(labelFor('seven_day'), 'wk')
assert.equal(labelFor('spend_limit'), 'spend_limit')
// pace: shown only when spending faster than even pace (> +2), orange, red above +15; nothing otherwise
const paceSeg = (p, left) => find(segsOf(m({ usage: { context: {}, rateLimits: [wk(p, left)] } }), { cols: 120 }), '⇡')
const elapsed40 = 4.2 * 24 * H // 40% of the 7-day window elapsed
const elapsed75 = 1.75 * 24 * H // 75% elapsed
assert.equal(paceSeg(58).t, ' ⇡8'); assert.equal(paceSeg(58).c, '#ff8c00')
assert.equal(paceSeg(55, elapsed40).t, ' ⇡15'); assert.equal(paceSeg(55, elapsed40).c, '#ff8c00')
assert.equal(paceSeg(70, elapsed40).t, ' ⇡30'); assert.equal(paceSeg(70, elapsed40).c, 'red')
assert.equal(paceSeg(42, elapsed40), undefined) // exactly +2
assert.equal(paceSeg(49, elapsed75), undefined) // headroom
assert.equal(paceSeg(38, elapsed40), undefined) // headroom, no ⇣ form
assert.ok(!L1(m({ usage: { context: {}, rateLimits: [wk(49, elapsed75)] } }), { cols: 120 }).match(/[⇡⇣]/))

// weekly warning: 70% or more, or more than 10 points ahead of the elapsed time; projection is linear
const wkSegs = (p, left) => segsOf(m({ usage: { context: {}, rateLimits: [wk(p, left)] } }), { cols: 120 })
const wkLabel = (p, left) => find(wkSegs(p, left), 'wk')
const projSeg = (p, left) => find(wkSegs(p, left), '→')
assert.equal(weekWarning(58, wk(58).resetsAt, now), false) // +8
assert.equal(weekWarning(60, wk(60).resetsAt, now), false) // exactly +10
assert.equal(weekWarning(61, wk(61).resetsAt, now), true) // +11
assert.equal(weekWarning(70, wk(70, elapsed75).resetsAt, now), true) // 70% even under headroom
assert.equal(weekWarning(69, wk(69, elapsed75).resetsAt, now), false)
assert.equal(weekWarning('x', wk(1).resetsAt, now), false)
assert.equal(projectedPercent(58, wk(58).resetsAt, now), 116)
assert.equal(projectedPercent(72, wk(72, elapsed75).resetsAt, now), 96)
assert.equal(projectedPercent(5, wk(5, 6.95 * 24 * H).resetsAt, now), null) // under 5% elapsed
assert.equal(projectedPercent(5, 'bad', now), null)
assert.equal(wkLabel(58).dim, true); assert.equal(projSeg(58), undefined)
assert.equal(wkLabel(65).c, '#ff8c00'); assert.equal(wkLabel(65).bold, true)
assert.equal(projSeg(65).t, ' →130%'); assert.equal(projSeg(65).c, 'red')
assert.equal(projSeg(72, elapsed75).t, ' →96%'); assert.equal(projSeg(72, elapsed75).c, '#ff8c00')
assert.ok(L1(m({ usage: { context: {}, rateLimits: [wk(65)] } }), { cols: 120, ascii: true }).endsWith('65% +15 ->130%'))
assert.ok(L1(m({ usage: { context: {}, rateLimits: [wk(65)] } }), { cols: 70 }).endsWith('wk 65%'))

// session cost and cache hit ratio
assert.equal(costText(1.234), '$1.23'); assert.equal(costText(0), '$0.00'); assert.equal(costText(123.4), '$123')
assert.equal(costText(undefined), ''); assert.equal(costText(-1), ''); assert.equal(costText(NaN), '')
assert.equal(cacheRatio(null), null); assert.equal(cacheRatio({ read: 0, all: 0 }), null)
assert.equal(cacheRatio({ read: 91, all: 100 }), 91)
const t1 = addTurnUsage(null, { input_tokens: 10, cache_read_input_tokens: 80, cache_creation_input_tokens: 10, output_tokens: 5 })
assert.deepEqual(t1, { read: 80, all: 100 })
assert.deepEqual(addTurnUsage(t1, { input_tokens: 0, cache_read_input_tokens: 100, cache_creation_input_tokens: 0 }), { read: 180, all: 200 })
assert.deepEqual(addTurnUsage(t1, undefined), t1)
assert.deepEqual(addTurnUsage(t1, { input_tokens: 'x', cache_read_input_tokens: -5 }), t1)
assert.equal(cacheColor(80), 'green'); assert.equal(cacheColor(79), 'yellow'); assert.equal(cacheColor(50), 'yellow'); assert.equal(cacheColor(49), '#ff8c00')
const spend = { cost: { usd: 4.2 } }
const spendM = m({ usage: { context: { percent: 43 }, rateLimits: [fh, wk(58)], ...spend }, cache: { read: 91, all: 100 } })
assert.ok(L1(spendM, { cols: 140 }).endsWith('wk ' + bar(58) + ' 58% ⇡8  $4.20  cache 91%'))
assert.ok(L1(spendM, { cols: 100 }).endsWith('wk 58% ⇡8  $4.20  c 91%'))
assert.ok(L1(spendM, { cols: 70 }).endsWith('wk 58%  $4.20'))
assert.ok(L1(spendM, { cols: 50 }).endsWith('ctx 43%  wk 58%'))
assert.ok(find(formatLine1(spendM, { cols: 140 }), '91%').c === 'green')
assert.ok(!L1(m({ usage: { context: {}, rateLimits: [wk(58)], ...spend } }), { cols: 140 }).includes('cache'))
assert.ok(!L1(m({ cache: { read: 0, all: 0 } }), { cols: 140 }).includes('$'))

// line 2: priority, content, expiry
const full = m({
  status: { ...status, card_dirty: true, errors_24h: 2, last_rule: { scope: 'repo:webapp', text: 'y'.repeat(80) } },
  usage: { context: { percent: 10 }, rateLimits: [fh, wk(93)] },
  seen: { ruleAt: now - 5000, reminderAt: now - 1000 },
})
assert.equal(formatLine2(full, { cols: 400 }).length > 0, true)
assert.equal(plain(formatLine2(full, { cols: 400 })),
  ' ╰ ⚠ 2 hook errors in 24h — juno doctor  ·  ▸ handoff card out of date: Projects/x/handoff.md' +
  '  ·  + rule added (repo:webapp) ' + 'y'.repeat(50) + '  ·  ▸ handoff reminder sent  ·  ⚠ wk at 93%')
assert.deepEqual(line2Items(full.status, full.usage, now, full.seen).map((s) => s.c), ['red', '#ff8c00', 'green', undefined, 'red'])
const only = (over) => plain(formatLine2(m(over), { cols: 200 }))
assert.equal(only({}), '')
assert.equal(formatLine2(m(), {}).length, 0)
assert.equal(only({ status: { ...status, errors_24h: 0 } }), '')
assert.equal(only({ status: { ...status, card_dirty: true, card_path: null } }), ' ╰ ▸ handoff card out of date')
assert.equal(only({ status: { ...status, card_dirty: true, card_auto: true } }), '')
assert.equal(only({ status: { ...status, last_rule: 'plain text' }, seen: { ruleAt: now - 59999 } }), ' ╰ + rule added (?) plain text')
assert.equal(only({ seen: { ruleAt: now - 60000 } }), '')
assert.equal(only({ seen: { ruleAt: null, reminderAt: now - 61000 } }), '')
assert.equal(only({ seen: { reminderAt: now - 59000 } }), ' ╰ ▸ handoff reminder sent')
assert.equal(only({ usage: { context: {}, rateLimits: [{ kind: 'five_hour', percentUsed: 90 }] } }), ' ╰ ⚠ 5h at 90%')
assert.equal(only({ usage: { context: {}, rateLimits: [{ kind: 'five_hour', percentUsed: 89.4 }] } }), '')
assert.equal(only({ status: null, usage: { context: {}, rateLimits: [{ kind: 'x', percentUsed: 100 }] } }), ' ╰ ⚠ x at 100%')
// truncation with ellipsis
const cut = plain(formatLine2(full, { cols: 40 }))
assert.equal([...cut].length, 40)
assert.ok(cut.endsWith('…'))
assert.ok(cut.startsWith(' ╰ ⚠ 2 hook errors in 24h'))

// NO_COLOR / ASCII / fine bars
assert.deepEqual(capsFrom({}), { ascii: false, color: true, fine: false })
assert.deepEqual(capsFrom({ NO_COLOR: '1' }), { ascii: false, color: false, fine: false })
assert.deepEqual(capsFrom({ NO_COLOR: '' }), { ascii: false, color: true, fine: false })
assert.equal(capsFrom({ TERM: 'dumb' }).ascii, true)
assert.equal(capsFrom({ LANG: 'C' }).ascii, true)
assert.equal(capsFrom({ LANG: 'en_US.UTF-8' }).ascii, false)
assert.equal(capsFrom({ LANG: 'C', LC_ALL: 'en_US.utf8' }).ascii, false)
assert.equal(capsFrom({ JUNO_UI_ASCII: '1', LANG: 'en_US.UTF-8' }).ascii, true)
assert.equal(capsFrom({ JUNO_UI_FINE_BARS: '1' }).fine, true)
assert.equal(capsFrom({ JUNO_UI_FINE_BARS: '0' }).fine, false)
const a1 = L1(m({ usage: { context: { percent: 95 }, rateLimits: [fh, wk(38)] } }), { cols: 120, ascii: true })
assert.ok(a1.startsWith(' juno | ABC-333333 · card ok'), a1)
assert.ok(a1.includes('ctx [#####] 95%!'))
assert.ok(a1.includes('5h [#----] 12% 3h51'))
assert.ok(a1.endsWith('wk [##---] 38%'), a1)
assert.ok(/^[\x20-\x7e·]*$/.test(a1), a1)
const a2 = plain(formatLine2(m({ status: { ...status, card_dirty: true, errors_24h: 1 } }), { cols: 200, ascii: true }))
assert.equal(a2, ' `- ! 1 hook errors in 24h -- juno doctor  ·  > handoff card out of date: Projects/x/handoff.md')
assert.ok(L1(m({ status: { ...status, card_dirty: true } }), { cols: 100, ascii: true }).includes('* dirty'))

// card_auto (mode "end"): no card marker, no dangling separator
const auto = { ...status, card_auto: true }
for (const dirty of [false, true]) {
  const st = { ...auto, card_dirty: dirty }
  const w1 = L1(m({ status: st }), { cols: 100 })
  assert.ok(!w1.includes('card') && !w1.includes('dirty') && !w1.includes('✓') && !w1.includes('●  '), w1)
  assert.ok(w1.includes('ABC-333333  │  ctx'), w1)
  assert.ok(!w1.includes(' ·  │'), w1)
  const n1 = L1(m({ status: st }), { cols: 50 })
  assert.ok(n1.startsWith(' juno │ ABC-333333  │  ctx') && !n1.includes('●') && !n1.includes('✓'), n1)
  const c1 = L1(m({ status: st }), { cols: 100, ascii: true })
  assert.ok(!c1.includes('dirty') && !c1.includes('card') && !c1.includes('ok') && c1.includes('ABC-333333  |  ctx'), c1)
  assert.ok(L1(m({ status: { ...st, ticket: null, repo: null } }), { cols: 100 }).startsWith(' juno │'))
}
assert.ok(!L1(m({ status: { ...auto, ticket: null, repo: null } }), { cols: 100 }).includes('card'))
assert.ok(!L1(m({ status: { ...auto, card_dirty: true } }), { cols: 100 }).match(/ · {1,2}│/))

// formatStatus joins both lines
assert.equal(formatStatus(m(), { cols: 100 }).includes('\n'), false)
assert.equal(formatStatus(full, { cols: 200 }).split('\n').length, 2)

// debug, seen
assert.equal(debugLine([{ kind: 'seven_day', percentUsed: 1, resetsAt: 'x', extra: 1 }]), 'rateLimits [{"kind":"seven_day","percentUsed":1,"resetsAt":"x"}]')
const first = advanceSeen(undefined, { rules_added_seq: 7, last_reminder_seq: 3 }, now)
assert.deepEqual(first, { rules: 7, reminder: 3, ruleAt: null, reminderAt: null })
assert.deepEqual(advanceSeen(first, { rules_added_seq: 7, last_reminder_seq: 3 }, now + 2000), first)
assert.deepEqual(advanceSeen(first, { rules_added_seq: 8, last_reminder_seq: 3 }, now + 2000), { rules: 8, reminder: 3, ruleAt: now + 2000, reminderAt: null })
assert.deepEqual(advanceSeen({ ...first, ruleAt: 5 }, { rules_added_seq: 7, last_reminder_seq: 4 }, now + 4000), { rules: 7, reminder: 4, ruleAt: 5, reminderAt: now + 4000 })
// status.json paths: JUNO_STATE wins, empty counts as unset, else macOS then XDG under HOME
const macDir = '/home/a/Library/Application Support/juno'
const xdgDir = '/home/a/.local/state/juno'
assert.deepEqual(statusPaths({ HOME: '/home/a', JUNO_STATE: '/tmp/state' }), ['/tmp/state/status.json'])
assert.deepEqual(statusPaths({ HOME: '/home/a', JUNO_STATE: '/tmp/state/' }), ['/tmp/state/status.json'])
assert.deepEqual(statusPaths({ HOME: '/home/a', JUNO_STATE: '' }), [macDir + '/status.json', xdgDir + '/status.json'])
assert.deepEqual(statusPaths({ HOME: '/home/a' }), [macDir + '/status.json', xdgDir + '/status.json'])
assert.deepEqual(statusPaths({ HOME: '/home/a', XDG_STATE_HOME: '/xdg/' }), [macDir + '/status.json', '/xdg/juno/status.json'])
assert.deepEqual(statusPaths({ HOME: '/home/a', XDG_STATE_HOME: 'relative' }), [macDir + '/status.json', xdgDir + '/status.json'])
assert.deepEqual(statusPaths({ JUNO_STATE: '/tmp/state' }), ['/tmp/state/status.json'])
assert.deepEqual(statusPaths({}), [])
// per-session file: <state>/status/<session id>.json, only for a safe id
const SID = '2b1f0c7e-1d2a-4c3b-9e8f-0a1b2c3d4e5f'
assert.deepEqual(sessionStatusPaths({ JUNO_STATE: '/tmp/state/' }, SID), [`/tmp/state/status/${SID}.json`])
assert.deepEqual(sessionStatusPaths({ HOME: '/home/a' }, SID), [`${macDir}/status/${SID}.json`, `${xdgDir}/status/${SID}.json`])
assert.deepEqual(sessionStatusPaths({ HOME: '/home/a' }, '../x'), [])
assert.deepEqual(sessionStatusPaths({ HOME: '/home/a' }, ''), [])
assert.deepEqual(sessionStatusPaths({ HOME: '/home/a' }, undefined), [])
assert.deepEqual(sessionStatusPaths({}, SID), [])
// which status the band shows
const own = { segment_id: SID, ticket: 'ABC-1' }
const ours = { segment_id: SID, ticket: 'ABC-2' }
const theirs = { segment_id: 'other-session', ticket: 'ABC-9' }
assert.deepEqual(pickStatus(own, theirs, SID), { status: own, mine: true })
assert.deepEqual(pickStatus(null, theirs, SID), { status: null, mine: false })
assert.deepEqual(pickStatus(null, ours, SID), { status: ours, mine: true })
assert.deepEqual(pickStatus(null, null, SID), { status: null, mine: false })
assert.deepEqual(pickStatus(null, theirs, undefined), { status: theirs, mine: false })
assert.deepEqual(pickStatus(own, theirs, '../x'), { status: theirs, mine: false })
assert.ok(L1(m({ ...pickStatus(null, theirs, SID) }), { cols: 100 }).startsWith(' juno │ idle'))
assert.ok(!L1(m({ ...pickStatus(null, theirs, undefined) }), { cols: 100 }).includes('ABC-9'))
assert.ok(L1(m({ ...pickStatus(own, theirs, SID) }), { cols: 100 }).startsWith(' juno │ ABC-1 · card ✓'))
// readStatus against a fake $: own file first, else status.json only when it names this session
const fakeFs = (files, sid, mtimeMs = now, envVars = { HOME: '/h', JUNO_STATE: '/st' }) => ({
  env: { get: async (k) => envVars[k] },
  session: { id: async () => { if (sid instanceof Error) throw sid; return sid } },
  ui: { log() {} },
  fs: {
    stat: async (p) => { if (!(p in files)) throw new Error('ENOENT: ' + p); return { mtimeMs } },
    read: async (p) => JSON.stringify(files[p]),
  },
})
const ownFile = `/st/status/${SID}.json`
assert.deepEqual(await readStatus(fakeFs({ [ownFile]: own, '/st/status.json': theirs }, SID), now), { status: own, mine: true, sid: SID })
assert.deepEqual(await readStatus(fakeFs({ '/st/status.json': theirs }, SID), now), { status: null, mine: false, sid: SID })
assert.deepEqual(await readStatus(fakeFs({ '/st/status.json': ours }, SID), now), { status: ours, mine: true, sid: SID })
assert.deepEqual((await readStatus(fakeFs({ '/st/status.json': theirs }, new Error('no id')), now)).status, theirs)
assert.equal((await readStatus(fakeFs({ [ownFile]: own }, SID, now - 11 * 60000), now)).status, null)
// without JUNO_STATE the macOS dir is tried first and the XDG dir after it
const noState = { HOME: '/h' }
const macFile = `/h/Library/Application Support/juno/status.json`
const xdgFile = '/h/.local/state/juno/status.json'
assert.deepEqual((await readStatus(fakeFs({ [macFile]: ours }, SID, now, noState), now)).status, ours)
assert.deepEqual((await readStatus(fakeFs({ [xdgFile]: ours }, SID, now, noState), now)).status, ours)
assert.deepEqual((await readStatus(fakeFs({ [macFile]: theirs, [xdgFile]: ours }, SID, now, noState), now)).status, null)
console.log('ok')
