// Backtest RBF — CLI runner (mirrors backtest.ts logic)
import { createClient } from '@supabase/supabase-js'

const SUPABASE_URL = 'https://ztdhvmcisjjyhbqlgkzm.supabase.co'
const SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp0ZGh2bWNpc2pqeWhicWxna3ptIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODk0MTc1MiwiZXhwIjoyMDk0NTE3NzUyfQ.sqMh9Jcxrxyg-ZBYWPaNN8DB9kf-KkC7ARPLucItN1Y'

const supabase = createClient(SUPABASE_URL, SUPABASE_KEY)

// ── params ────────────────────────────────────────────────────────────────────
const CONS_MIN         = 15
const CONS_MAX         = 60
const RANGE_MIN        = 0.0008
const RANGE_MAX        = 0.0055
const VR_MIN           = 3.0
const DZ_MIN           = 0.5
const DZ_MAX           = 3.0
const TRAIL_ATR_K      = 1.2
const TRAIL_ACTIVATE_R = 1.5
const TIME_STOP_BARS   = 30
const RR               = 2.0
const POSITION         = 500      // $500 × 10x
const ACCOUNT          = 50
const SESSIONS_OK      = new Set(['London', 'LondonNyOverlap', 'NewYork'])

const SYM_TABLES = {
  BTCUSDT: 'btc_bars',
  ETHUSDT: 'eth_bars',
  BNBUSDT: 'bnb_bars',
  SOLUSDT: 'sol_bars',
  XRPUSDT: 'xrp_bars',
}

async function fetchBars(table, startMs) {
  const all = []
  const PAGE = 1000
  let from = 0
  process.stdout.write(`  ${table}... `)
  while (true) {
    const { data, error } = await supabase
      .from(table)
      .select('ts_ms,open,high,low,close,vr,atr,session,cvd_slope,dz,obi_l5,bar_delta,vwap')
      .gte('ts_ms', startMs)
      .order('ts_ms', { ascending: true })
      .range(from, from + PAGE - 1)
    if (error || !data || data.length === 0) break
    all.push(...data)
    if (data.length < PAGE) break
    from += PAGE
  }
  console.log(`${all.length} bars`)
  return all
}

function simulate(bars, entry, stop, target, atr, startMs) {
  let bestLow     = entry
  let trailActive = false
  let trailStop   = stop

  for (let k = 0; k < bars.length; k++) {
    const b = bars[k]
    const effectiveStop = trailActive ? trailStop : stop

    if (b.high >= effectiveStop) {
      const dMin   = Math.floor((b.ts_ms - startMs) / 60000)
      const reason = trailActive ? 'TRAILING_STOP' : 'STOP_LOSS'
      return { r: (entry - effectiveStop) / (stop - entry), exit: effectiveStop, reason, durationMin: dMin }
    }
    if (b.low <= target) {
      const dMin = Math.floor((b.ts_ms - startMs) / 60000)
      return { r: RR, exit: target, reason: 'TAKE_PROFIT', durationMin: dMin }
    }

    if (b.low < bestLow) bestLow = b.low
    const favR = (entry - bestLow) / (stop - entry)
    if (favR >= TRAIL_ACTIVATE_R) trailActive = true
    if (trailActive && atr > 0) {
      const candidate = bestLow + TRAIL_ATR_K * atr
      if (candidate < trailStop) trailStop = candidate
    }

    if (k + 1 >= TIME_STOP_BARS) {
      const pnl = entry - b.close
      if (pnl < 0) {
        const dMin = Math.floor((b.ts_ms - startMs) / 60000)
        return { r: pnl / (stop - entry), exit: b.close, reason: 'TIME_STOP', durationMin: dMin }
      }
    }
  }
  return { r: 0, exit: 0, reason: 'DATA_END', durationMin: 0 }
}

function runOnBars(sym, bars) {
  const trades = []

  for (let i = CONS_MAX; i < bars.length - TIME_STOP_BARS; i++) {
    const bar = bars[i]

    if (!SESSIONS_OK.has(bar.session ?? '')) continue
    if ((bar.vr ?? 0) < VR_MIN) continue
    if ((bar.atr ?? 0) <= 0) continue

    if (bar.dz != null) {
      const dzDir = Math.max(-bar.dz, 0)
      if (dzDir < DZ_MIN || dzDir > DZ_MAX) continue
    }

    // VSWAP proximity: no short si precio >0.3% bajo VWAP (backtest 30d: WR=6.5% si extendido)
    if (bar.vwap != null && bar.vwap > 0) {
      const pct = (bar.close - bar.vwap) / bar.vwap
      if (pct < -0.003) continue
    }

    let found = false
    for (let clen = CONS_MIN; clen <= CONS_MAX && !found; clen++) {
      const win = bars.slice(i - clen, i)
      const lo  = Math.min(...win.map(b => b.low))
      const hi  = Math.max(...win.map(b => b.high))
      const rangePct = (hi - lo) / lo
      if (rangePct < RANGE_MIN || rangePct > RANGE_MAX) continue
      if (bar.close >= lo) continue

      // cvd_slope gate eliminado: absorción (slope>=0) tiene mejor WR que continuación (slope<0)

      // Breakout extension: close debe romper >0.1% más allá del range_low
      const breakoutExt = (lo - bar.close) / lo
      if (breakoutExt < 0.001) continue

      // cvdInRange eliminado: vswap+ext ya garantizan calidad del setup

      // Stop = range HIGH (estructura real, no ATR ruido)
      const entry   = bar.close
      const atr     = bar.atr
      const stop    = hi
      const target  = entry - RR * (stop - entry)
      const stopPct = (stop - entry) / entry

      const sim = simulate(
        bars.slice(i + 1, i + 1 + TIME_STOP_BARS + 30),
        entry, stop, target, atr, bar.ts_ms
      )

      const riskUsd = POSITION * stopPct
      const pnlUsd  = sim.r * riskUsd
      const dzDir   = bar.dz != null ? Math.max(-bar.dz, 0) : null

      trades.push({
        sym, session: bar.session,
        entry, stop, target,
        stopPct: Math.round(stopPct * 100000) / 1000,
        riskUsd: Math.round(riskUsd * 100) / 100,
        r:       Math.round(sim.r * 1000) / 1000,
        pnlUsd:  Math.round(pnlUsd * 100) / 100,
        reason:  sim.reason,
        durationMin: sim.durationMin,
        tsMs:    bar.ts_ms,
        rangePct: Math.round(rangePct * 100000) / 1000,
        rangeBars: clen,
        dz: dzDir,
        vr: bar.vr,
      })
      found = true
      i++
    }
  }
  return trades
}

function printStats(trades) {
  if (!trades.length) { console.log('  (sin trades)'); return }

  const winners  = trades.filter(t => t.r > 0)
  const losers   = trades.filter(t => t.r < 0)
  const wr       = Math.round(winners.length / trades.length * 1000) / 10
  const avgR     = Math.round(trades.reduce((s,t) => s+t.r, 0) / trades.length * 1000) / 1000
  const totalPnl = Math.round(trades.reduce((s,t) => s+t.pnlUsd, 0) * 100) / 100
  const avgStop  = Math.round(trades.reduce((s,t) => s+t.stopPct, 0) / trades.length * 1000) / 1000
  const avgRisk  = Math.round(trades.reduce((s,t) => s+t.riskUsd, 0) / trades.length * 100) / 100
  const avgDur   = Math.round(trades.reduce((s,t) => s+t.durationMin, 0) / trades.length)

  const byReason = {}
  trades.forEach(t => { byReason[t.reason] = (byReason[t.reason] || 0) + 1 })

  const bySess = {}
  trades.forEach(t => {
    if (!bySess[t.session]) bySess[t.session] = { n:0, wins:0, r:0 }
    bySess[t.session].n++
    if (t.r > 0) bySess[t.session].wins++
    bySess[t.session].r += t.r
  })

  console.log(`  Trades: ${trades.length}  WR: ${wr}%  AvgR: ${avgR}  PnL: $${totalPnl}`)
  console.log(`  AvgStop: ${avgStop}%  AvgRisk: $${avgRisk}/trade  AvgDur: ${avgDur}min`)
  console.log(`  Exits: ${Object.entries(byReason).map(([k,v])=>`${k}=${v}`).join('  ')}`)
  console.log(`  Sessions:`)
  Object.entries(bySess).forEach(([sess, s]) => {
    const swr = Math.round(s.wins/s.n*1000)/10
    const savg = Math.round(s.r/s.n*1000)/1000
    console.log(`    ${sess.padEnd(20)} n=${s.n}  WR=${swr}%  AvgR=${savg}`)
  })
}

// ── main ──────────────────────────────────────────────────────────────────────
const DAYS = 30
const startMs = Date.now() - DAYS * 24 * 60 * 60 * 1000

console.log(`\nBacktest RBF — ${DAYS}d — Stop=RangeHigh — TimeStop=${TIME_STOP_BARS}bars\n`)
console.log('Descargando bars...')

const syms = Object.keys(SYM_TABLES)
const allBars = []
for (const sym of syms) {
  const bars = await fetchBars(SYM_TABLES[sym], startMs)
  allBars.push({ sym, bars })
}

console.log('\nEjecutando backtest...')
let allTrades = []
for (const { sym, bars } of allBars) {
  const t = runOnBars(sym, bars)
  console.log(`  ${sym}: ${t.length} trades`)
  allTrades.push(...t)
}

allTrades.sort((a,b) => a.tsMs - b.tsMs)
let eq = ACCOUNT
allTrades.forEach((t, i) => {
  t.idx = i + 1
  eq = Math.round((eq + t.pnlUsd) * 100) / 100
  t.equity = eq
})

console.log('\n═══ RESULTADOS GLOBALES ═══════════════════════════════════════')
printStats(allTrades)

console.log('\n═══ POR SÍMBOLO ════════════════════════════════════════════════')
for (const sym of syms) {
  const st = allTrades.filter(t => t.sym === sym)
  if (!st.length) continue
  const wr  = Math.round(st.filter(t=>t.r>0).length/st.length*1000)/10
  const avg = Math.round(st.reduce((s,t)=>s+t.r,0)/st.length*1000)/1000
  console.log(`  ${sym.padEnd(10)} n=${String(st.length).padEnd(3)} WR=${wr}%  AvgR=${avg}`)
}

console.log('\n═══ EQUITY CURVE ═══════════════════════════════════════════════')
const eq_start = ACCOUNT
const eq_end   = allTrades.length ? allTrades[allTrades.length-1].equity : eq_start
console.log(`  Start: $${eq_start}  →  End: $${eq_end}  (${eq_end >= eq_start ? '+' : ''}${Math.round((eq_end-eq_start)*100)/100})`)

console.log('\n═══ ÚLTIMOS 10 TRADES ══════════════════════════════════════════')
allTrades.slice(-10).forEach(t => {
  const d = new Date(t.tsMs).toISOString().slice(5,16)
  const rStr = (t.r >= 0 ? '+' : '') + t.r.toFixed(3) + 'R'
  console.log(`  #${String(t.idx).padEnd(3)} ${t.sym.replace('USDT','').padEnd(4)} ${t.session?.padEnd(20)} ${d}  ${rStr.padEnd(8)} stop=${t.stopPct}%  dur=${t.durationMin}m  ${t.reason}`)
})
