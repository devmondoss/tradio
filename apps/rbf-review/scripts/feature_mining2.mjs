// Feature Mining v2 — validación de combinaciones óptimas encontradas
import { createClient } from '@supabase/supabase-js'

const SUPABASE_URL = 'https://ztdhvmcisjjyhbqlgkzm.supabase.co'
const SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp0ZGh2bWNpc2pqeWhicWxna3ptIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODk0MTc1MiwiZXhwIjoyMDk0NTE3NzUyfQ.sqMh9Jcxrxyg-ZBYWPaNN8DB9kf-KkC7ARPLucItN1Y'
const supabase = createClient(SUPABASE_URL, SUPABASE_KEY)

const CONS_MIN = 15, CONS_MAX = 60, RANGE_MIN = 0.0008, RANGE_MAX = 0.0055
const VR_MIN = 3.0, TRAIL_ATR_K = 1.2, TRAIL_R = 1.5, TIME_STOP_BARS = 30, RR = 2.0
const SESSIONS_OK = new Set(['London', 'LondonNyOverlap', 'NewYork'])
const SYM_TABLES  = { BTCUSDT:'btc_bars', ETHUSDT:'eth_bars', BNBUSDT:'bnb_bars', SOLUSDT:'sol_bars', XRPUSDT:'xrp_bars' }

async function fetchBars(table, startMs) {
  const all = []; let from = 0
  while (true) {
    const { data, error } = await supabase.from(table)
      .select('ts_ms,open,high,low,close,vr,atr,session,cvd_slope,dz,obi_l5,bar_delta,vwap')
      .gte('ts_ms', startMs).order('ts_ms', { ascending: true }).range(from, from+999)
    if (error || !data || data.length === 0) break
    all.push(...data)
    if (data.length < 1000) break
    from += 1000
  }
  return all
}

function simulate(bars, entry, stop, target, atr) {
  let bestLow = entry, trailActive = false, trailStop = stop
  for (let k = 0; k < bars.length; k++) {
    const b = bars[k], eStop = trailActive ? trailStop : stop
    if (b.high >= eStop) return { r: (entry - eStop) / (stop - entry), reason: trailActive ? 'TRAIL' : 'SL' }
    if (b.low  <= target) return { r: RR, reason: 'TP' }
    if (b.low < bestLow) bestLow = b.low
    if ((entry - bestLow) / (stop - entry) >= TRAIL_R) trailActive = true
    if (trailActive && atr > 0) { const c = bestLow + TRAIL_ATR_K * atr; if (c < trailStop) trailStop = c }
    if (k + 1 >= TIME_STOP_BARS) { const pnl = entry - b.close; if (pnl < 0) return { r: pnl / (stop - entry), reason: 'TIME' } }
  }
  return { r: 0, reason: 'END' }
}

function collectTrades(sym, bars) {
  const trades = []
  for (let i = CONS_MAX; i < bars.length - TIME_STOP_BARS; i++) {
    const bar = bars[i]
    if (!SESSIONS_OK.has(bar.session ?? '')) continue
    if ((bar.vr ?? 0) < VR_MIN || (bar.atr ?? 0) <= 0) continue
    let found = false
    for (let clen = CONS_MIN; clen <= CONS_MAX && !found; clen++) {
      const win  = bars.slice(i - clen, i)
      const lo   = Math.min(...win.map(b => b.low))
      const hi   = Math.max(...win.map(b => b.high))
      const rangePct = (hi - lo) / lo
      if (rangePct < RANGE_MIN || rangePct > RANGE_MAX || bar.close >= lo) continue

      const entry = bar.close, stop = hi, target = entry - RR * (stop - entry)
      const sim   = simulate(bars.slice(i+1, i+1+TIME_STOP_BARS+30), entry, stop, target, bar.atr)

      const dzDir      = bar.dz != null ? Math.max(-bar.dz, 0) : null
      const cvdInRange = win.every(b => b.bar_delta != null) ? win.reduce((s,b) => s+b.bar_delta, 0) : null
      const touchesLow = win.filter(b => b.low <= lo*1.001 && b.close > lo).length
      const breakoutExt = (lo - entry) / lo
      const vswap      = bar.vwap != null && bar.vwap > 0 ? (entry - bar.vwap) / bar.vwap : null

      trades.push({
        sym, session: bar.session, tsMs: bar.ts_ms,
        r: sim.r, reason: sim.reason, win: sim.r > 0,
        dz: bar.dz, dzDir,
        obi: bar.obi_l5,
        cvdSlope: bar.cvd_slope,
        cvdInRange, touchesLow, breakoutExt, vswap, vr: bar.vr, rangePct, rangeBars: clen,
      })
      found = true; i++
    }
  }
  return trades
}

function stats(trades, label = '') {
  if (!trades.length) return { n:0, wr:0, avgR:0, label }
  const wins = trades.filter(t => t.win).length
  const avgR = trades.reduce((s,t) => s+t.r, 0) / trades.length
  return { n: trades.length, wr: Math.round(wins/trades.length*1000)/10, avgR: Math.round(avgR*1000)/1000, label }
}

function row(s) {
  const marker = s.wr >= 55 ? '✓✓ OBJETIVO' : s.wr >= 45 ? '✓ cerca' : s.wr >= 35 ? '~' : '✗'
  console.log(`  ${(s.label||'').padEnd(55)} n=${String(s.n).padEnd(4)} WR=${String(s.wr).padEnd(6)}% AvgR=${String(s.avgR).padEnd(7)} ${marker}`)
}

// ── main ──────────────────────────────────────────────────────────────────────
const DAYS = 30
const startMs = Date.now() - DAYS * 24 * 60 * 60 * 1000

console.log(`\nFeature Mining v2 — ${DAYS}d\n`)
let allTrades = []
for (const [sym, table] of Object.entries(SYM_TABLES)) {
  process.stdout.write(`  ${sym}... `)
  const bars = await fetchBars(table, startMs)
  const t = collectTrades(sym, bars)
  process.stdout.write(`${bars.length} bars → ${t.length} trades\n`)
  allTrades.push(...t)
}

const noOverlap = allTrades.filter(t => t.session !== 'LondonNyOverlap')
const hasVswap  = allTrades.filter(t => t.vswap != null)

console.log(`\nBaseline total: n=${allTrades.length} WR=${stats(allTrades).wr}%`)
console.log(`Sin Overlap:    n=${noOverlap.length} WR=${stats(noOverlap).wr}%\n`)

console.log('═══ COMBINACIONES CLAVE ════════════════════════════════════════\n')

// Base: sin overlap
row(stats(noOverlap, 'London+NY (sin Overlap)'))

// VSWAP solo
row(stats(hasVswap.filter(t => t.vswap > -0.003),               'vswap > -0.3%'))
row(stats(hasVswap.filter(t => t.vswap > 0),                    'vswap > 0 (sobre VWAP)'))

// Breakout extension
row(stats(allTrades.filter(t => t.breakoutExt > 0.002),         'ext > 0.2%'))
row(stats(allTrades.filter(t => t.breakoutExt > 0.001),         'ext > 0.1%'))

// CVD slope invertido (absorción: buyers activos pero precio rompe abajo)
row(stats(allTrades.filter(t => t.cvdSlope != null && t.cvdSlope >= 0), 'cvd_slope >= 0 (absorción buyers)'))

// DZ >= 1.5
row(stats(allTrades.filter(t => t.dzDir != null && t.dzDir >= 1.5), 'dzDir >= 1.5'))

console.log('\n═══ COMBOS SIN OVERLAP ════════════════════════════════════════\n')

// Todas las combinaciones sin overlap + vswap
const base = noOverlap.filter(t => t.vswap != null)
row(stats(base.filter(t => t.vswap > -0.003),
  'noOverlap + vswap>-0.3%'))
row(stats(base.filter(t => t.vswap > -0.003 && t.breakoutExt > 0.001),
  'noOverlap + vswap>-0.3% + ext>0.1%'))
row(stats(base.filter(t => t.vswap > -0.003 && t.breakoutExt > 0.002),
  'noOverlap + vswap>-0.3% + ext>0.2%'))
row(stats(base.filter(t => t.vswap > 0),
  'noOverlap + vswap>0 (sobre VWAP)'))
row(stats(base.filter(t => t.vswap > 0 && t.breakoutExt > 0.001),
  'noOverlap + vswap>0 + ext>0.1%'))
row(stats(base.filter(t => t.vswap > 0 && t.breakoutExt > 0.002),
  'noOverlap + vswap>0 + ext>0.2%'))

console.log('\n═══ COMBOS CON ABSORCIÓN (cvdSlope >= 0) ══════════════════════\n')
const absorp = noOverlap.filter(t => t.cvdSlope != null && t.cvdSlope >= 0 && t.vswap != null)
row(stats(absorp, 'noOverlap + cvdSlope>=0 (absorción)'))
row(stats(absorp.filter(t => t.vswap > -0.003),
  '+ vswap>-0.3%'))
row(stats(absorp.filter(t => t.vswap > -0.003 && t.breakoutExt > 0.001),
  '+ vswap>-0.3% + ext>0.1%'))
row(stats(absorp.filter(t => t.vswap > -0.003 && t.breakoutExt > 0.002),
  '+ vswap>-0.3% + ext>0.2%'))
row(stats(absorp.filter(t => t.dzDir != null && t.dzDir >= 1.5 && t.vswap > -0.003),
  '+ dz>=1.5 + vswap>-0.3%'))

console.log('\n═══ MEJOR COMBO POR SESIÓN ════════════════════════════════════\n')
// La mejor combo que encontremos, desglosada por sesión
const bestCombo = noOverlap.filter(t =>
  t.vswap != null && t.vswap > -0.003 &&
  t.breakoutExt > 0.001
)
console.log(`Combo: noOverlap + vswap>-0.3% + ext>0.1%  →  n=${bestCombo.length}`)
for (const sess of ['London', 'NewYork']) {
  const s = stats(bestCombo.filter(t => t.session === sess), sess)
  row(s)
}

// Segunda mejor
const bestCombo2 = noOverlap.filter(t =>
  t.vswap != null && t.vswap > 0 &&
  t.cvdSlope != null && t.cvdSlope >= 0
)
console.log(`\nCombo: noOverlap + vswap>0 + cvdSlope>=0  →  n=${bestCombo2.length}`)
for (const sess of ['London', 'NewYork']) {
  const s = stats(bestCombo2.filter(t => t.session === sess), sess)
  row(s)
}

console.log('\n═══ RESUMEN FINAL POR ACTIVO (mejor combo encontrada) ══════════\n')
// La combo con mejor WR global que tenga n >= 5
const combos = [
  { label: 'vswap>-0.3%+ext>0.2%',   f: t => t.vswap != null && t.vswap > -0.003 && t.breakoutExt > 0.002 },
  { label: 'vswap>0+absorp',          f: t => t.vswap != null && t.vswap > 0 && t.cvdSlope != null && t.cvdSlope >= 0 },
  { label: 'vswap>-0.3%+ext>0.1%',   f: t => t.vswap != null && t.vswap > -0.003 && t.breakoutExt > 0.001 },
]
for (const { label, f } of combos) {
  const filtered = noOverlap.filter(f)
  const s = stats(filtered, label)
  console.log(`\n${label}: n=${s.n} WR=${s.wr}% AvgR=${s.avgR}`)
  for (const sym of Object.keys(SYM_TABLES)) {
    const ss = stats(filtered.filter(t => t.sym === sym))
    if (ss.n > 0) console.log(`  ${sym.replace('USDT','').padEnd(6)} n=${ss.n}  WR=${ss.wr}%  AvgR=${ss.avgR}`)
  }
}
