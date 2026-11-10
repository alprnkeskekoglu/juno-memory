// Display only: no tool.call/prompt hooks, no writes. The one process is `juno doctor`, run on a press of the hook-error item.
const MAC_REL = '/Library/Application Support/juno'
const XDG_REL = '/.local/state/juno'
const POLL_MS = 2000
const STALE_MS = 10 * 60 * 1000
const FRESH_MS = 60 * 1000
const WEEK_MS = 7 * 24 * 3600 * 1000
const WARN_PERCENT = 70
const WARN_PACE = 10
const MIN_ELAPSED = 5
const CELLS = 5
const ORANGE = '#ff8c00'
const GREEN = 'green'
const EIGHTHS = ' ▏▎▍▌▋▊▉█'
const TRACK = '░'
const DOT_ON = '●'
const DOT_OFF = '○'
const MODEL_RE = /^claude-(opus|sonnet|haiku)-(\d+)-(\d+)(?:-\d{8})?$/
const SAFE_ID = /^[A-Za-z0-9_-]{1,128}$/

let timer
let lastDebug
let loggedPath // status file path last written to the debug log
let model // { status, mine, sid, usage, now, error, seen, caps, modelId }; undefined before the first refresh
let usageNow // latest usage from session.measure, shared with the poll
let cacheTotals = { read: 0, all: 0 } // main-loop prompt tokens: cache-read and total, from turn.complete
let modelId // main loop model id, re-read from $.session.model() on each poll

const UNICODE = { bar: '│', branch: '╰', warn: '⚠', mark: '▸', dot: '●', ok: '✓', up: '⇡', proj: '→', ell: '…', dash: '—' }
const ASCII = { bar: '|', branch: '`-', warn: '!', mark: '>', dot: '*', ok: 'ok', up: '+', proj: '->', ell: '...', dash: '--' }
const glyphs = (o) => (o?.ascii ? ASCII : UNICODE)

// Default: five dots. Filled = round(percent / 20), but under 10% none, so 6% shows 0 and 10% shows 1.
export function dotCount(percent) {
  const p = Math.max(0, Math.min(100, Number(percent) || 0))
  return p < 10 ? 0 : Math.round(p / 20)
}

// Default: dots. ascii: [##---]. fine (JUNO_UI_FINE_BARS=1): eighth blocks for a finer edge; some fonts draw them wide or solid, so opt-in only.
export function bar(percent, ascii = false, fine = false) {
  const p = Math.max(0, Math.min(100, Number(percent) || 0))
  if (ascii) {
    const filled = Math.round((p / 100) * CELLS)
    return '[' + '#'.repeat(filled) + '-'.repeat(CELLS - filled) + ']'
  }
  if (!fine) {
    const on = dotCount(p)
    return DOT_ON.repeat(on) + DOT_OFF.repeat(CELLS - on)
  }
  const eighths = Math.round((p / 100) * CELLS * 8)
  let text = ''
  for (let i = 0; i < CELLS; i++) {
    const left = eighths - i * 8
    text += left >= 8 ? '█' : left > 0 ? EIGHTHS[left] : TRACK
  }
  return text
}

// The filled part of a bar in its colour, the empty part (the `empty` glyph) dimmed.
function barSegs(b, color, ascii, empty) {
  const cells = ascii ? b.slice(1, -1) : b
  const fill = [...cells].filter((c) => c !== empty).join('')
  const track = [...cells].filter((c) => c === empty).join('')
  const segs = [seg(fill, { c: color }), seg(track, { dim: true })]
  if (ascii) { segs.unshift(seg('[')); segs.push(seg(']')) }
  return segs.filter((s) => s.t !== '')
}

// Claude Code's model id as a short label; at the narrowest width the version goes.
export function modelLabel(id, level) {
  if (typeof id !== 'string' || id === '') return ''
  const is1m = /\[1m\]$/i.test(id)
  const base = id.replace(/\[1m\]$/i, '')
  const known = MODEL_RE.exec(base)
  const text = known
    ? (level === 'tiny' ? known[1] : `${known[1]} ${known[2]}.${known[3]}`)
    : [...base].slice(0, 20).join('')
  return is1m ? text + '·1M' : text
}

// Blue below 50, yellow 50-70, orange 70-90, red from 90.
export function levelColor(percent) {
  if (percent >= 90) return 'red'
  if (percent >= 70) return ORANGE
  if (percent >= 50) return 'yellow'
  return 'blue'
}

export function countdown(resetsAt, nowMs) {
  const ms = Date.parse(resetsAt) - nowMs
  if (!Number.isFinite(ms) || ms <= 0) return ''
  const mins = Math.floor(ms / 60000)
  if (mins < 1) return '<1m'
  const d = Math.floor(mins / 1440)
  const h = Math.floor((mins % 1440) / 60)
  const m = mins % 60
  if (d > 0) return `${d}d${h}h`
  return h > 0 ? `${h}h${String(m).padStart(2, '0')}` : `${m}m`
}

// Percent used minus percent of the 7-day window elapsed, rounded; null unless spending faster than even pace (> +2) or unknown.
export function pace(percentUsed, resetsAt, nowMs) {
  const reset = Date.parse(resetsAt)
  if (!Number.isFinite(reset) || typeof percentUsed !== 'number') return null
  const elapsed = Math.max(0, Math.min(100, (1 - (reset - nowMs) / WEEK_MS) * 100))
  const p = Math.round(percentUsed - elapsed)
  return p > 2 ? p : null
}

function elapsedPercent(resetsAt, nowMs) {
  const reset = Date.parse(resetsAt)
  if (!Number.isFinite(reset)) return null
  return Math.max(0, Math.min(100, (1 - (reset - nowMs) / WEEK_MS) * 100))
}

// Linear end-of-week projection of the 7-day usage; null until 5% of the window has passed or when unknown.
export function projectedPercent(percentUsed, resetsAt, nowMs) {
  const elapsed = elapsedPercent(resetsAt, nowMs)
  if (elapsed === null || elapsed < MIN_ELAPSED || typeof percentUsed !== 'number') return null
  return Math.min(999, Math.round((percentUsed / elapsed) * 100))
}

// True when the 7-day usage is at 70% or more, or runs more than 10 points ahead of the elapsed time.
export function weekWarning(percentUsed, resetsAt, nowMs) {
  if (typeof percentUsed !== 'number') return false
  if (percentUsed >= WARN_PERCENT) return true
  const elapsed = elapsedPercent(resetsAt, nowMs)
  return elapsed !== null && percentUsed - elapsed > WARN_PACE
}

export function costText(usd) {
  if (typeof usd !== 'number' || !Number.isFinite(usd) || usd < 0) return ''
  return '$' + (usd >= 100 ? usd.toFixed(0) : usd.toFixed(2))
}

// Share of the prompt tokens the cache served, as a whole percentage; null before any counted turn.
export function cacheRatio(totals) {
  if (!totals || !(totals.all > 0)) return null
  return Math.max(0, Math.min(100, Math.round((totals.read / totals.all) * 100)))
}

export function addTurnUsage(totals, usage) {
  const t = totals ?? { read: 0, all: 0 }
  if (!usage || typeof usage !== 'object') return t
  const n = (v) => (typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : 0)
  const read = n(usage.cache_read_input_tokens)
  return { read: t.read + read, all: t.all + n(usage.input_tokens) + read + n(usage.cache_creation_input_tokens) }
}

export function cacheColor(percent) {
  if (percent >= 80) return GREEN
  if (percent >= 50) return 'yellow'
  return ORANGE
}

export function labelFor(kind) {
  if (kind === 'five_hour') return '5h'
  if (kind === 'seven_day') return 'wk'
  return typeof kind === 'string' && kind ? kind : '?'
}

// five_hour, seven_day, then any other kind by raw name; never array order.
export function sortedWindows(limits) {
  const rank = (k) => (k === 'five_hour' ? 0 : k === 'seven_day' ? 1 : 2)
  return (Array.isArray(limits) ? limits : [])
    .filter((l) => l && typeof l.percentUsed === 'number')
    .map((l) => ({ ...l, label: labelFor(l.kind) }))
    .sort((a, b) =>
      rank(a.kind) - rank(b.kind) ||
      String(a.kind).localeCompare(String(b.kind)) ||
      b.percentUsed - a.percentUsed ||
      String(a.resetsAt ?? '').localeCompare(String(b.resetsAt ?? '')))
    .filter((w, i, all) => i === 0 || w.kind !== all[i - 1].kind)
}

export function debugLine(limits) {
  return 'rateLimits ' + JSON.stringify(
    (Array.isArray(limits) ? limits : []).map((l) => ({ kind: l?.kind, percentUsed: l?.percentUsed, resetsAt: l?.resetsAt })),
  )
}

// env: { NO_COLOR, TERM, LC_ALL, LC_CTYPE, LANG, JUNO_UI_ASCII, JUNO_UI_FINE_BARS } values (strings or undefined).
export function capsFrom(env) {
  const e = env ?? {}
  const loc = e.LC_ALL || e.LC_CTYPE || e.LANG || ''
  const ascii = e.JUNO_UI_ASCII === '1' || e.TERM === 'dumb' || (loc !== '' && !/utf-?8/i.test(loc))
  const color = !(typeof e.NO_COLOR === 'string' && e.NO_COLOR !== '') && e.TERM !== 'dumb'
  const fine = e.JUNO_UI_FINE_BARS === '1'
  return { ascii, color, fine }
}

const seg = (t, props) => ({ t, ...props })
export const plain = (segs) => segs.map((s) => s.t).join('')
const width = (segs) => [...plain(segs)].length

function levelFor(cols) {
  if (!(cols > 0)) return 'full'
  if (cols >= 110) return 'full'
  if (cols >= 80) return 'num'
  if (cols >= 60) return 'min'
  return 'tiny'
}

function meter(label, percent, o, level, extras, warn) {
  const g = glyphs(o)
  const out = [warn ? seg(label, { c: ORANGE, bold: true }) : seg(label, { dim: true }), seg(' ', { dim: true })]
  const color = levelColor(percent)
  const empty = o.ascii ? '-' : o.fine ? TRACK : DOT_OFF
  if (level === 'full') out.push(...barSegs(bar(percent, o.ascii, o.fine), color, o.ascii, empty), seg(' '))
  out.push(seg(`${Math.round(percent)}%`, { c: color }))
  if (percent >= 90) out.push(seg(g.warn, { c: 'red', bold: true }))
  if (level === 'full' || level === 'num') out.push(...(extras ?? []))
  return out
}

function windowExtras(w, o, nowMs) {
  const g = glyphs(o)
  const out = []
  if (w.kind === 'five_hour' && w.resetsAt) {
    const left = countdown(w.resetsAt, nowMs)
    if (left) out.push(seg(' ' + left, { dim: true }))
  }
  if (w.kind === 'seven_day' && w.resetsAt) {
    const p = pace(w.percentUsed, w.resetsAt, nowMs)
    if (p !== null) out.push(seg(` ${g.up}${p}`, { c: p > 15 ? 'red' : ORANGE }))
    const proj = weekWarning(w.percentUsed, w.resetsAt, nowMs) ? projectedPercent(w.percentUsed, w.resetsAt, nowMs) : null
    if (proj !== null) out.push(seg(` ${g.proj}${proj}%`, { c: proj >= 100 ? 'red' : ORANGE }))
  }
  return out
}

function spendGroups(cost, cache, level) {
  const groups = []
  const usd = costText(cost?.usd)
  if (usd && level !== 'tiny') groups.push([seg(usd, { dim: true })])
  const ratio = cacheRatio(cache)
  if (ratio !== null && (level === 'full' || level === 'num')) {
    groups.push([seg(level === 'full' ? 'cache ' : 'c ', { dim: true }), seg(`${ratio}%`, { c: cacheColor(ratio) })])
  }
  return groups
}

function rightSegments(usage, o, nowMs, level, cache) {
  const groups = []
  const pct = usage?.context?.percent
  if (typeof pct === 'number') groups.push(meter('ctx', pct, o, level))
  const wins = sortedWindows(usage?.rateLimits)
  const byKind = (k) => wins.find((w) => w.kind === k)
  const listed = level === 'tiny' ? ['seven_day'] : ['five_hour', 'seven_day']
  for (const k of ['five_hour', 'seven_day']) {
    if (!listed.includes(k)) continue
    const w = byKind(k)
    groups.push(w
      ? meter(w.label, w.percentUsed, o, level, windowExtras(w, o, nowMs), w.kind === 'seven_day' && weekWarning(w.percentUsed, w.resetsAt, nowMs))
      : [seg(labelFor(k) + ' n/a', { dim: true })])
  }
  if (level !== 'tiny') {
    for (const w of wins) {
      if (w.kind === 'five_hour' || w.kind === 'seven_day') continue
      groups.push(meter(w.label, w.percentUsed, o, level))
    }
  }
  groups.push(...spendGroups(usage?.cost, cache, level))
  return groups.flatMap((g, i) => (i ? [seg('  '), ...g] : g))
}

function cardMarker(status, g, level) {
  if (status.card_auto) return null
  if (level === 'tiny') return status.card_dirty ? seg(g.dot, { c: ORANGE, press: 'card-dirty' }) : seg(g.ok, { c: GREEN, dim: true })
  return status.card_dirty ? seg(`${g.dot} dirty`, { c: ORANGE, press: 'card-dirty' }) : seg(`card ${g.ok}`, { c: GREEN, dim: true })
}

function leftSegments(status, o, level, modelText, mine) {
  const g = glyphs(o)
  const head = [seg(' juno', { bold: true }), seg(` ${g.bar} `, { dim: true })]
  if (!status) {
    if (modelText) head.push(seg(modelText, { dim: true }), seg(' · ', { dim: true }))
    return [...head, seg('idle', { dim: true })]
  }
  const label = mine ? status.ticket || status.repo : null
  const items = [
    modelText && seg(modelText, { dim: true }),
    label && seg(String(label)),
    cardMarker(status, g, level),
  ].filter(Boolean)
  return [...head, ...items.flatMap((x, i) => (i ? [seg(' · ', { dim: true }), x] : [x]))]
}

// m: { status, mine, usage, now, error }; o: { cols, ascii, fine }. Returns styled segments.
export function formatLine1(m, o = {}) {
  const g = glyphs(o)
  if (m?.error) return [seg(` juno ${g.bar} ?`, { dim: true })]
  const level = levelFor(o.cols)
  const left = leftSegments(m?.status ?? null, o, level, modelLabel(m?.modelId, level), m?.mine === true)
  const right = rightSegments(m?.usage, o, m?.now ?? 0, level, m?.cache)
  if (!right.length) return left
  return fit([...left, seg(`  ${g.bar}  `, { dim: true }), ...right], o.cols, o)
}

function ruleText(rule, ascii) {
  const scope = rule && typeof rule === 'object' ? (rule.scope ?? '?') : '?'
  const raw = rule && typeof rule === 'object' ? rule.text : rule
  const text = typeof raw === 'string' ? [...raw].slice(0, 50).join('') : ''
  return `+ rule added (${scope}) ${text}`.trimEnd()
}

// seen: { ruleAt, reminderAt } (ms timestamps of the last increase, or null).
export function line2Items(status, usage, nowMs, seen, o = {}) {
  const g = glyphs(o)
  const items = []
  if (status?.errors_24h > 0) {
    items.push(seg(`${g.warn} ${status.errors_24h} hook errors in 24h ${g.dash} juno doctor`, { c: 'red', press: 'doctor' }))
  }
  if (status?.card_dirty && !status.card_auto) {
    items.push(seg(`${g.mark} handoff card out of date${status.card_path ? ': ' + status.card_path : ''}`, { c: ORANGE, press: 'card-stale' }))
  }
  const fresh = (at) => typeof at === 'number' && nowMs - at >= 0 && nowMs - at < FRESH_MS
  if (status && fresh(seen?.ruleAt)) items.push(seg(ruleText(status.last_rule), { c: GREEN }))
  if (status && fresh(seen?.reminderAt)) items.push(seg(`${g.mark} handoff reminder sent`))
  for (const w of sortedWindows(usage?.rateLimits)) {
    if (w.percentUsed >= 90) items.push(seg(`${g.warn} ${w.label} at ${Math.round(w.percentUsed)}%`, { c: 'red' }))
  }
  return items
}

// Returns segments for line 2, or [] when nothing needs attention.
export function formatLine2(m, o = {}) {
  if (m?.error) return []
  const g = glyphs(o)
  const items = line2Items(m?.status ?? null, m?.usage, m?.now ?? 0, m?.seen, o)
  if (!items.length) return []
  const joined = items.flatMap((s, i) => (i ? [seg('  ·  ', { dim: true }), s] : [s]))
  return fit([seg(` ${g.branch} `, { dim: true }), ...joined], o.cols, o)
}

// Cuts segments to max cells, ending with the ellipsis.
function fit(segs, max, o) {
  if (!(max > 0) || width(segs) <= max) return segs
  const ell = glyphs(o).ell
  let room = max - [...ell].length
  const out = []
  for (const s of segs) {
    const chars = [...s.t]
    if (chars.length <= room) { out.push(s); room -= chars.length; continue }
    if (room > 0) out.push({ ...s, t: chars.slice(0, room).join('') })
    break
  }
  out.push(seg(ell, { dim: true }))
  return out
}

// Full text of both lines (colour-free), for tests and debugging.
export function formatStatus(m, o = {}) {
  const l2 = formatLine2(m, o)
  return plain(formatLine1(m, o)) + (l2.length ? '\n' + plain(l2) : '')
}

// The seen-state after a read: counters plus when each last increased. First read stamps nothing.
export function advanceSeen(prev, status, nowMs) {
  const rules = status.rules_added_seq ?? 0
  const reminder = status.last_reminder_seq ?? 0
  if (!prev) return { rules, reminder, ruleAt: null, reminderAt: null }
  return {
    rules,
    reminder,
    ruleAt: rules > prev.rules ? nowMs : prev.ruleAt,
    reminderAt: reminder > prev.reminder ? nowMs : prev.reminderAt,
  }
}

// env: { HOME, JUNO_STATE, XDG_STATE_HOME } values. JUNO_STATE wins when set (an empty value counts as unset).
// Without it the macOS state dir comes first, then the XDG one; the first that holds a fresh file is used.
export function stateDirs(env) {
  const state = env?.JUNO_STATE
  if (typeof state === 'string' && state !== '') return [state.replace(/\/+$/, '')]
  if (!env?.HOME) return []
  const xdg = env.XDG_STATE_HOME
  const base = typeof xdg === 'string' && xdg.startsWith('/') ? xdg.replace(/\/+$/, '') + '/juno' : env.HOME + XDG_REL
  return [env.HOME + MAC_REL, base]
}

export function statusPaths(env) {
  return stateDirs(env).map((d) => d + '/status.json')
}

// This session's own files, written by the engine next to status.json from the same hook call.
export function sessionStatusPaths(env, sid) {
  if (typeof sid !== 'string' || !SAFE_ID.test(sid)) return []
  return stateDirs(env).map((d) => d + '/status/' + sid + '.json')
}

// own: this session's file, shared: status.json (parsed, or null). The shared file is another session's
// whenever its segment_id differs, so with a usable id it counts only when it names this session.
export function pickStatus(own, shared, sid) {
  const usable = typeof sid === 'string' && SAFE_ID.test(sid)
  if (usable && own && typeof own === 'object') return { status: own, mine: true }
  if (!shared || typeof shared !== 'object') return { status: null, mine: false }
  if (!usable) return { status: shared, mine: false }
  return shared.segment_id === sid ? { status: shared, mine: true } : { status: null, mine: false }
}

async function readOne($, path, nowMs) {
  let stat
  try {
    stat = await $.fs.stat(path)
  } catch (err) {
    // A missing file is the normal idle case; anything else is a real error.
    if (/ENOENT/.test(String(err?.message ?? err))) return null
    throw err
  }
  if (nowMs - stat.mtimeMs > STALE_MS) return null
  return JSON.parse(await $.fs.read(path))
}

async function readFresh($, paths, nowMs) {
  for (const path of paths) {
    const found = await readOne($, path, nowMs)
    if (found) return found
  }
  return null
}

export async function readStatus($, nowMs) {
  let home, state, xdg, sid, debug
  try { home = await $.env.get('HOME') } catch {}
  try { state = await $.env.get('JUNO_STATE') } catch {}
  try { xdg = await $.env.get('XDG_STATE_HOME') } catch {}
  try { sid = await $.session.id() } catch {}
  const env = { HOME: home, JUNO_STATE: state, XDG_STATE_HOME: xdg }
  const ownPaths = sessionStatusPaths(env, sid)
  const sharedPaths = statusPaths(env)
  if (!sharedPaths.length) return { ...pickStatus(null, null, sid), sid }
  try { debug = await $.env.get('JUNO_UI_DEBUG') } catch {}
  const paths = (ownPaths.join(',') || 'no session file') + ' | ' + sharedPaths.join(',')
  if (paths !== loggedPath && debug === '1') {
    loggedPath = paths
    try { $.ui.log('status: ' + paths) } catch {}
  }
  const own = await readFresh($, ownPaths, nowMs)
  return { ...pickStatus(own, own ? null : await readFresh($, sharedPaths, nowMs), sid), sid }
}

async function readCaps($) {
  const get = async (f) => { try { return await f() } catch { return undefined } }
  return capsFrom({
    NO_COLOR: await get(() => $.env.get('NO_COLOR')),
    TERM: await get(() => $.env.get('TERM')),
    LC_ALL: await get(() => $.env.get('LC_ALL')),
    LC_CTYPE: await get(() => $.env.get('LC_CTYPE')),
    LANG: await get(() => $.env.get('LANG')),
    JUNO_UI_ASCII: await get(() => $.env.get('JUNO_UI_ASCII')),
    JUNO_UI_FINE_BARS: await get(() => $.env.get('JUNO_UI_FINE_BARS')),
  })
}

async function refresh($, measured) {
  try {
    const nowMs = await $.clock.now()
    const { status, mine, sid } = await readStatus($, nowMs)
    await readModel($)
    if (measured) usageNow = measured
    const usage = usageNow ?? (await $.session.usage())
    const caps = model?.caps ?? (await readCaps($))
    // A new session id (/clear, resume) starts the counters over, so its earlier rules do not flash.
    let seen = sid === model?.sid ? model?.seen : undefined
    if (status) {
      seen = advanceSeen(seen, status, nowMs)
    }
    model = { status, mine, sid, usage, now: nowMs, error: false, seen, caps, modelId, cache: cacheTotals }
    if ((await $.env.get('JUNO_UI_DEBUG')) === '1') {
      const line = debugLine(usage?.rateLimits)
      if (line !== lastDebug) { lastDebug = line; $.ui.log(line) }
    }
  } catch {
    model = { ...(model ?? {}), error: true, caps: model?.caps ?? { ascii: false, color: true, fine: false } }
  }
  try { $.ui.invalidate('ui.render') } catch {}
}

async function readModel($) {
  try { modelId = await $.session.model() } catch {}
}

// Our rows, then the tree beneath (next(e)'s result, which keeps Claude Code's own band content).
export function bandChildren(rows, theirs) {
  return theirs ? [...rows, theirs] : rows
}

function toTextProps(s, color) {
  const props = { children: s.t }
  if (color && s.c) props.color = s.c
  if (color && s.dim) props.dimColor = true
  if (s.bold) props.bold = true
  return props
}

function toText(Text, s, color) {
  return Text(toTextProps(s, color))
}

function drawRow(Box, Text, segs, color, ctl) {
  return Box({ flexDirection: 'row', children: segs.map((s) => (ctl && s.press ? toButton(ctl, Text, s, color) : toText(Text, s, color))) })
}

// A pressable segment: the same Text (colour kept) underlined, inside a plain Button (no brackets; the focus inverts it).
function toButton(ctl, Text, s, color) {
  const label = Text({ ...toTextProps(s, color), underline: true })
  return ctl.Button({ key: s.press, plain: true, onPress: () => ctl.press(s.press), children: [label] })
}

// Pane content (pure). Each returns { dim, text }: a dim one-line message, or the text to show.
const PANE_MAX_CHARS = 20000
const clip = (t, rows) => {
  const lines = String(t).split('\n')
  const kept = lines.slice(0, Math.max(1, rows > 0 ? rows : 200)).join('\n')
  return [...kept].length > PANE_MAX_CHARS ? [...kept].slice(0, PANE_MAX_CHARS).join('') : kept
}

// text: the card file's text, null when it could not be read, undefined while it is being read.
export function cardPaneContent(text, path, rows) {
  if (text === undefined) return { dim: true, text: 'reading…' }
  if (typeof text !== 'string') return { dim: true, text: `Card file not found${path ? ': ' + path : ''}` }
  if (text.trim() === '') return { dim: true, text: 'Card file is empty' }
  return { dim: false, text: clip(text, rows) }
}

// view: { running } | { result: { exitCode, stdout, stderr } } | { error }.
export function doctorPaneContent(view, rows) {
  if (view?.running) return { dim: true, text: 'running\u2026' }
  if (view?.error) return { dim: true, text: `juno doctor could not run: ${String(view.error).split('\n')[0]}` }
  const r = view?.result
  if (!r) return { dim: true, text: 'no output' }
  const parts = [r.stdout]
  if (r.exitCode !== 0) parts.push(r.stderr)
  const text = parts.filter((x) => typeof x === 'string' && x.trim() !== '').join('\n').replace(/\s+$/, '')
  return text ? { dim: false, text: clip(text, rows) } : { dim: true, text: 'no output' }
}

let cardView = { text: null, path: '' } // set on press; the pane draws it
let doctorView = { running: false }
const PANES = { 'juno-card': 'Juno card', 'juno-doctor': 'Juno doctor' }

async function openCard($, path) {
  cardView = { text: undefined, path }
  try { await $.ui.open({ id: 'juno-card', title: PANES['juno-card'] }) } catch {}
  try { cardView = { text: await $.fs.read(path), path } } catch { cardView = { text: null, path } }
  try { $.ui.invalidate('ui.render') } catch {}
}

async function openDoctor($) {
  if (!doctorView.running) {
    doctorView = { running: true }
    try { await $.ui.open({ id: 'juno-doctor', title: PANES['juno-doctor'] }) } catch {}
    try { $.ui.invalidate('ui.render') } catch {}
    try { doctorView = { result: await $.process.run(['juno', 'doctor'], { timeoutMs: 60000 }) } } catch (err) { doctorView = { error: err?.message ?? err } }
    try { $.ui.invalidate('ui.render') } catch {}
  } else {
    try { await $.ui.open({ id: 'juno-doctor', title: PANES['juno-doctor'] }) } catch {}
  }
}

// What a press does; never throws.
function pressHandler($) {
  return (key) => {
    try {
      if (key === 'doctor') void openDoctor($)
      else if (key === 'card-dirty' || key === 'card-stale') {
        const path = model?.status?.card_path
        void openCard($, typeof path === 'string' ? path : '')
      }
    } catch {}
  }
}

function drawPane($, e, id, content) {
  const { Box, Text, Button } = $.ui.resolve(e)
  const rows = Math.max(1, (e.viewport?.rows ?? 24) - 4)
  const c = content(rows)
  return Box({
    flexDirection: 'column',
    children: [
      Button({ key: 'dismiss', role: 'dismiss', label: 'Dismiss', onPress: () => { try { void $.ui.close({ id }) } catch {} } }),
      Text({ children: c.text, ...(c.dim ? { dimColor: true } : {}) }),
    ],
  })
}

export function register(on) {
  on('session.start', async ($, e, next) => {
    try {
      timer?.cancel()
      cacheTotals = { read: 0, all: 0 }
      // The $ from session.start is reused in the timer callback.
      timer = $.clock.every(POLL_MS, () => refresh($))
      await refresh($)
    } catch {}
    return next(e)
  })

  on('session.measure', async ($, e, next) => {
    // The event carries the same figures as $.session.usage().
    await refresh($, { context: e.context, rateLimits: e.rateLimits, cost: e.cost })
    return next(e)
  })

  on('turn.complete', async ($, e, next) => {
    if (e.agentId === undefined) cacheTotals = addTurnUsage(cacheTotals, e.usage)
    return next(e)
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    try {
      const props = e.props ?? e
      if (props.hasSurvey || !model) return next(e)
      const { Box, Text, Button } = $.ui.resolve(e)
      const ctl = Button ? { Button, press: pressHandler($) } : undefined
      const o = { cols: props.bodyColumns, ascii: model.caps.ascii, fine: model.caps.fine }
      const color = model.caps.color
      const rows = [drawRow(Box, Text, formatLine1(model, o), color, ctl)]
      const l2 = formatLine2(model, o)
      if (l2.length) rows.push(drawRow(Box, Text, l2, color, ctl))
      const theirs = await next(e)
      return Box({ flexDirection: 'column', children: bandChildren(rows, theirs) })
    } catch {
      return next(e)
    }
  })

  // Panes opened by a press on a band item; each draws only what a press loaded.
  on('ui.render', { component: 'Pane', requestId: 'juno-card' }, async ($, e, next) => {
    try { return drawPane($, e, 'juno-card', (rows) => cardPaneContent(cardView.text, cardView.path, rows)) } catch { return next(e) }
  })
  on('ui.render', { component: 'Pane', requestId: 'juno-doctor' }, async ($, e, next) => {
    try { return drawPane($, e, 'juno-doctor', (rows) => doctorPaneContent(doctorView, rows)) } catch { return next(e) }
  })
}
