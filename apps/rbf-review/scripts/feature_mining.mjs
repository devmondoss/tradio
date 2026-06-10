// Feature Mining — encuentra qué combinaciones de orderflow suben el WR a 55%+
// Basado en insights de: absorción, CLC, nivel de touches, extensión de breakout, VWAP
import { createClient } from '@supabase/supabase-js'

const SUPABASE_URL = 'https://ztdhvmcisjjyhbqlgkzm.supabase.co'
const SUPABASE_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp0ZGh2bWNpc2pqeWhicWxna3ptIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODk0MTc1MiwiZXhwIjoyMDk0NTE3NzUyfQ.sqMh9Jcxrxyg-ZBYWPaNN8DB9kf-KkC7ARPLucItN1Y'
const supabase = createClient(SUPABASE_URL, SUPABASE_KEY)

// ── params base (sin filtros de microestructura — queremos ver TODOS los trades) ──
const CONS_MIN       = 15
const CONS_MAX       = 60
const RANGE_MIN      = 0.0008
const RANGE_MAX      = 0.0055
const VR_MIN         = 3.0
const TRAIL_ATR_K    = 1.2
const TRAIL_R        = 1.5
const TIME_STOP_BARS = 30
const RR             = 2.0
const SESSIONS_OK    = new Set(['London', 'LondonNyOverlap', 'NewYork'])
const SYM_TABLES     = { BTCUSDT:'btc_bars', ETHUSDT:'eth_bars', BNBUSDT:'bnb_bars', SOLUSDT:'sol_bars', XRPUSDT:'xrp_bars' }

async function fetchBars(table, startMs) {
  const all = []
  let from = 0
  while (true) {
    const { data, error } = await supabase
      .from(table)
      .select('ts_ms,open,high,low,close,vr,atr,session,cvd_slope,dz,obi_l5,bar_delta,vwap')
      .gte('ts_ms', startMs)
      .order('ts_ms', { ascending: true })
      .range(from, from + 999)
    if (error || !data || data.length === 0) break
    all.push(...data)
    if (data.length < 1000) break
    from += 1000
  }
  return all
}

function simulate(bars, entry, stop, target, atr, startMs) {
  let bestLow = entry, trailActive = false, trailStop = stop
  for (let k = 0; k < bars.length; k++) {
    const b = bars[k]
    const eStop = trailActive ? trailStop : stop
    if (b.high >= eStop) return { r: (entry - eStop) / (stop - entry), reason: trailActive ? 'TRAIL' : 'SL' }
    if (b.low  <= target) return { r: RR, reason: 'TP' }
    if (b.low < bestLow) bestLow = b.low
    if ((entry - bestLow) / (stop - entry) >= TRAIL_R) trailActive = true
    if (trailActive && atr > 0) {
      const c = bestLow + TRAIL_ATR_K * atr
      if (c < trailStop) trailStop = c
    }
    if (k + 1 >= TIME_STOP_BARS) {
      const pnl = entry - b.close
      if (pnl < 0) return { r: pnl / (stop - entry), reason: 'TIME' }
    }
  }
  return { r: 0, reason: 'END' }
}

// Recoge trades SIN filtros de microestructura para analizar features después
function collectTrades(sym, bars) {
  const trades = []
  for (let i = CONS_MAX; i < bars.length - TIME_STOP_BARS; i++) {
    const bar = bars[i]
    if (!SESSIONS_OK.has(bar.session ?? '')) continue
    if ((bar.vr ?? 0) < VR_MIN) continue
    if ((bar.atr ?? 0) <= 0) continue

    let found = false
    for (let clen = CONS_MIN; clen <= CONS_MAX && !found; clen++) {
      const win = bars.slice(i - clen, i)
      const lo  = Math.min(...win.map(b => b.low))
      const hi  = Math.max(...win.map(b => b.high))
      const rangePct = (hi - lo) / lo
      if (rangePct < RANGE_MIN || rangePct > RANGE_MAX) continue
      if (bar.close >= lo) continue  // solo shorts (close < range_low)

      const entry   = bar.close
      const stop    = hi
      const target  = entry - RR * (stop - entry)

      const sim = simulate(
        bars.slice(i + 1, i + 1 + TIME_STOP_BARS + 30),
        entry, stop, target, bar.atr, bar.ts_ms
      )

      // Features de microestructura en la barra de breakout
      const dz        = bar.dz
      const dzDir     = dz != null ? Math.max(-dz, 0) : null  // para Short: presión vendedora
      const obi       = bar.obi_l5
      const cvdSlope  = bar.cvd_slope
      const cvdInRange = win.every(b => b.bar_delta != null)
        ? win.reduce((s, b) => s + b.bar_delta, 0) : null
      const cvdPerBar = cvdInRange != null ? cvdInRange / clen : null

      // Touches del nivel que se rompe (cuántas veces rebotó en ese piso antes de romper)
      const touchesLow = win.filter(b => b.low <= lo * 1.001 && b.close > lo).length

      // Extensión del breakout: qué tan lejos del range_low cerró
      const breakoutExt = (lo - entry) / lo  // positivo = más extensión

      // VWAP: precio relativo al VWAP en la barra de entrada
      const vswap = bar.vwap != null && bar.vwap > 0
        ? (entry - bar.vwap) / bar.vwap : null  // negativo = por debajo del vwap

      const isWin = sim.r > 0

      trades.push({
        sym, session: bar.session, tsMs: bar.ts_ms,
        r: sim.r, reason: sim.reason, win: isWin,
        // features
        dz, dzDir,
        obi,
        cvdSlope,
        cvdInRange,
        cvdPerBar,
        touchesLow,
        breakoutExt,
        vswap,
        vr: bar.vr,
        rangePct,
        rangeBars: clen,
      })
      found = true
      i++
    }
  }
  return trades
}

// ── stats helpers ─────────────────────────────────────────────────────────────
function stats(trades) {
  if (!trades.length) return { n: 0, wr: 0, avgR: 0 }
  const wins = trades.filter(t => t.win).length
  const avgR = trades.reduce((s, t) => s + t.r, 0) / trades.length
  return {
    n:    trades.length,
    wr:   Math.round(wins / trades.length * 1000) / 10,
    avgR: Math.round(avgR * 1000) / 1000,
  }
}

function printSplit(label, a, b) {
  const sa = stats(a), sb = stats(b)
  const marker = sa.wr >= 55 ? ' ✓✓' : sa.wr >= 40 ? ' ✓' : ''
  const markerb = sb.wr >= 55 ? ' ✓✓' : sb.wr >= 40 ? ' ✓' : ''
  console.log(`  ${label}`)
  console.log(`    YES: n=${sa.n}  WR=${sa.wr}%  AvgR=${sa.avgR}${marker}`)
  console.log(`    NO:  n=${sb.n}  WR=${sb.wr}%  AvgR=${sb.avgR}${markerb}`)
}

// ── main ──────────────────────────────────────────────────────────────────────
const DAYS = 30  // más datos para mejor significancia estadística
const startMs = Date.now() - DAYS * 24 * 60 * 60 * 1000

console.log(`\nFeature Mining RBF — ${DAYS}d — Stop=RangeHigh\n`)
console.log('Descargando bars...')

let allTrades = []
for (const [sym, table] of Object.entries(SYM_TABLES)) {
  process.stdout.write(`  ${sym}... `)
  const bars = await fetchBars(table, startMs)
  console.log(`${bars.length} bars`)
  const t = collectTrades(sym, bars)
  console.log(`    → ${t.length} trades (sin filtros micro)`)
  allTrades.push(...t)
}

const base = stats(allTrades)
console.log(`\nBaseline (sin filtros micro): n=${base.n}  WR=${base.wr}%  AvgR=${base.avgR}\n`)

// ══════════════════════════════════════════════════════════════════════════════
console.log('═══ ANÁLISIS POR FEATURE ════════════════════════════════════════\n')

// 1. CVD SLOPE (presión bajista sostenida)
const hasCvdSlope = allTrades.filter(t => t.cvdSlope != null)
console.log('1. CVD SLOPE (slope negativo = presión vendedora sostenida)')
printSplit('cvd_slope < 0 (alineado Short)',
  hasCvdSlope.filter(t => t.cvdSlope < 0),
  hasCvdSlope.filter(t => t.cvdSlope >= 0))
printSplit('cvd_slope < -50',
  hasCvdSlope.filter(t => t.cvdSlope < -50),
  hasCvdSlope.filter(t => t.cvdSlope >= -50))
printSplit('cvd_slope < -100',
  hasCvdSlope.filter(t => t.cvdSlope < -100),
  hasCvdSlope.filter(t => t.cvdSlope >= -100))

// 2. DZ (delta z-score — absorción de compradores)
const hasDz = allTrades.filter(t => t.dzDir != null)
console.log('\n2. DZ_DIR (absorción — vendedores agresivos en breakout)')
printSplit('dzDir ∈ [0.5, 3.0] (zona óptima)',
  hasDz.filter(t => t.dzDir >= 0.5 && t.dzDir <= 3.0),
  hasDz.filter(t => !(t.dzDir >= 0.5 && t.dzDir <= 3.0)))
printSplit('dzDir ∈ [1.0, 2.5] (sweet spot)',
  hasDz.filter(t => t.dzDir >= 1.0 && t.dzDir <= 2.5),
  hasDz.filter(t => !(t.dzDir >= 1.0 && t.dzDir <= 2.5)))
printSplit('dzDir ∈ [0.5, 2.0]',
  hasDz.filter(t => t.dzDir >= 0.5 && t.dzDir <= 2.0),
  hasDz.filter(t => !(t.dzDir >= 0.5 && t.dzDir <= 2.0)))
printSplit('dzDir >= 1.5 (presión fuerte)',
  hasDz.filter(t => t.dzDir >= 1.5),
  hasDz.filter(t => t.dzDir < 1.5))

// 3. OBI (order book imbalance)
const hasObi = allTrades.filter(t => t.obi != null)
console.log('\n3. OBI (order book imbalance — asks dominan para Short)')
printSplit('obi < 0 (asks > bids)',
  hasObi.filter(t => t.obi < 0),
  hasObi.filter(t => t.obi >= 0))
printSplit('obi < -0.1',
  hasObi.filter(t => t.obi < -0.1),
  hasObi.filter(t => t.obi >= -0.1))
printSplit('obi < -0.3',
  hasObi.filter(t => t.obi < -0.3),
  hasObi.filter(t => t.obi >= -0.3))

// 4. CVD EN RANGO (presión neta durante consolidación)
const hasCvdRange = allTrades.filter(t => t.cvdInRange != null)
console.log('\n4. CVD EN RANGO (acumulación de vendedores en consolidación)')
printSplit('cvdInRange < 0 (neto vendedor)',
  hasCvdRange.filter(t => t.cvdInRange < 0),
  hasCvdRange.filter(t => t.cvdInRange >= 0))
const cvdPerBarVals = hasCvdRange.filter(t => t.cvdPerBar != null)
const p25 = cvdPerBarVals.map(t => t.cvdPerBar).sort((a,b)=>a-b)[Math.floor(cvdPerBarVals.length*0.25)]
const p50 = cvdPerBarVals.map(t => t.cvdPerBar).sort((a,b)=>a-b)[Math.floor(cvdPerBarVals.length*0.5)]
console.log(`  (p25 cvdPerBar=${Math.round(p25*10)/10}, p50=${Math.round(p50*10)/10})`)
printSplit(`cvdPerBar < ${Math.round(p50*10)/10} (mitad inferior)`,
  cvdPerBarVals.filter(t => t.cvdPerBar < p50),
  cvdPerBarVals.filter(t => t.cvdPerBar >= p50))

// 5. TOUCHES (consolidación con múltiples rechazos — "nivel probado")
console.log('\n5. TOUCHES DEL NIVEL (CLC: nivel probado ≥ N veces antes del breakout)')
for (const minT of [1, 2, 3, 4, 5]) {
  const yes = allTrades.filter(t => t.touchesLow >= minT)
  const no  = allTrades.filter(t => t.touchesLow < minT)
  const s = stats(yes)
  const marker = s.wr >= 55 ? ' ✓✓' : s.wr >= 40 ? ' ✓' : ''
  console.log(`  touches >= ${minT}: n=${s.n}  WR=${s.wr}%  AvgR=${s.avgR}${marker}`)
}

// 6. VWAP PROXIMITY (no short cuando precio está muy abajo del VWAP)
const hasVswap = allTrades.filter(t => t.vswap != null)
console.log('\n6. VSWAP (posición relativa al VWAP — no short si ya extendido)')
printSplit('vswap > -0.003 (dentro 0.3% del VWAP)',
  hasVswap.filter(t => t.vswap > -0.003),
  hasVswap.filter(t => t.vswap <= -0.003))
printSplit('vswap > -0.005 (dentro 0.5%)',
  hasVswap.filter(t => t.vswap > -0.005),
  hasVswap.filter(t => t.vswap <= -0.005))
printSplit('vswap > 0 (precio sobre VWAP)',
  hasVswap.filter(t => t.vswap > 0),
  hasVswap.filter(t => t.vswap <= 0))

// 7. BREAKOUT EXTENSION (qué tan lejos rompió del range_low)
console.log('\n7. BREAKOUT EXTENSION (distancia del close desde range_low)')
const extVals = allTrades.map(t => t.breakoutExt).sort((a,b)=>a-b)
const ep25 = extVals[Math.floor(extVals.length*0.25)]
const ep75 = extVals[Math.floor(extVals.length*0.75)]
console.log(`  (p25=${(ep25*100).toFixed(3)}%, p75=${(ep75*100).toFixed(3)}%)`)
printSplit(`ext > ${(ep25*100).toFixed(3)}% (mayor extensión del breakout)`,
  allTrades.filter(t => t.breakoutExt > ep25),
  allTrades.filter(t => t.breakoutExt <= ep25))
printSplit('ext > 0.001 (>0.1% extensión)',
  allTrades.filter(t => t.breakoutExt > 0.001),
  allTrades.filter(t => t.breakoutExt <= 0.001))
printSplit('ext > 0.002 (>0.2% extensión)',
  allTrades.filter(t => t.breakoutExt > 0.002),
  allTrades.filter(t => t.breakoutExt <= 0.002))

// 8. VR TIER (volumen extraordinario = breakout real)
console.log('\n8. VR TIER (volumen relativo en el breakout)')
printSplit('VR >= 4× (tier 3 — institucional)',
  allTrades.filter(t => t.vr >= 4),
  allTrades.filter(t => t.vr < 4))
printSplit('VR >= 5×',
  allTrades.filter(t => t.vr >= 5),
  allTrades.filter(t => t.vr < 5))

// 9. SESION
console.log('\n9. POR SESIÓN')
for (const sess of ['London', 'LondonNyOverlap', 'NewYork']) {
  const s = stats(allTrades.filter(t => t.session === sess))
  const marker = s.wr >= 55 ? ' ✓✓' : s.wr >= 40 ? ' ✓' : ''
  console.log(`  ${sess.padEnd(22)} n=${String(s.n).padEnd(4)} WR=${s.wr}%  AvgR=${s.avgR}${marker}`)
}

// ══════════════════════════════════════════════════════════════════════════════
console.log('\n═══ COMBINACIONES ÓPTIMAS (≥3 filtros) ════════════════════════\n')

// Combinación A: cvdSlope < 0 + dzDir [0.5,3] + touches >= 2
const combA = allTrades.filter(t =>
  t.cvdSlope != null && t.cvdSlope < 0 &&
  t.dzDir != null && t.dzDir >= 0.5 && t.dzDir <= 3.0 &&
  t.touchesLow >= 2
)
const sA = stats(combA)
console.log(`A. cvd_slope<0 + dz[0.5-3] + touches≥2:  n=${sA.n}  WR=${sA.wr}%  AvgR=${sA.avgR}`)

// Combinación B: cvdSlope < 0 + dzDir [1.0,2.5] + touches >= 3
const combB = allTrades.filter(t =>
  t.cvdSlope != null && t.cvdSlope < 0 &&
  t.dzDir != null && t.dzDir >= 1.0 && t.dzDir <= 2.5 &&
  t.touchesLow >= 3
)
const sB = stats(combB)
console.log(`B. cvd_slope<0 + dz[1.0-2.5] + touches≥3: n=${sB.n}  WR=${sB.wr}%  AvgR=${sB.avgR}`)

// Combinación C: cvdInRange<0 + cvdSlope<0 + dzDir[0.5,3] + vswap>-0.005
const combC = allTrades.filter(t =>
  t.cvdInRange != null && t.cvdInRange < 0 &&
  t.cvdSlope != null && t.cvdSlope < 0 &&
  t.dzDir != null && t.dzDir >= 0.5 && t.dzDir <= 3.0 &&
  t.vswap != null && t.vswap > -0.005
)
const sC = stats(combC)
console.log(`C. cvdRange<0 + slope<0 + dz[0.5-3] + vswap>-0.5%: n=${sC.n}  WR=${sC.wr}%  AvgR=${sC.avgR}`)

// Combinación D: touches>=3 + cvdInRange<0 + dzDir>=1.0 (absorción clara)
const combD = allTrades.filter(t =>
  t.touchesLow >= 3 &&
  t.cvdInRange != null && t.cvdInRange < 0 &&
  t.dzDir != null && t.dzDir >= 1.0
)
const sD = stats(combD)
console.log(`D. touches≥3 + cvdRange<0 + dzDir≥1.0:  n=${sD.n}  WR=${sD.wr}%  AvgR=${sD.avgR}`)

// Combinación E: todo junto
const combE = allTrades.filter(t =>
  t.cvdSlope != null && t.cvdSlope < 0 &&
  t.cvdInRange != null && t.cvdInRange < 0 &&
  t.dzDir != null && t.dzDir >= 0.5 && t.dzDir <= 3.0 &&
  t.touchesLow >= 2 &&
  t.vswap != null && t.vswap > -0.005
)
const sE = stats(combE)
console.log(`E. ALL (slope+range+dz+touches≥2+vswap): n=${sE.n}  WR=${sE.wr}%  AvgR=${sE.avgR}`)

// Combinación F: breakdown + obi alineado
const combF = allTrades.filter(t =>
  t.cvdSlope != null && t.cvdSlope < 0 &&
  t.dzDir != null && t.dzDir >= 0.5 && t.dzDir <= 3.0 &&
  t.obi != null && t.obi < -0.1 &&
  t.touchesLow >= 2
)
const sF = stats(combF)
console.log(`F. slope<0 + dz[0.5-3] + obi<-0.1 + touches≥2: n=${sF.n}  WR=${sF.wr}%  AvgR=${sF.avgR}`)

// Por sesión x combinacion A
console.log('\nComb A por sesión:')
for (const sess of ['London', 'LondonNyOverlap', 'NewYork']) {
  const s = stats(combA.filter(t => t.session === sess))
  const marker = s.wr >= 55 ? ' ✓✓' : s.wr >= 40 ? ' ✓' : ''
  if (s.n > 0) console.log(`  ${sess.padEnd(22)} n=${s.n}  WR=${s.wr}%  AvgR=${s.avgR}${marker}`)
}

console.log('\nComb D por sesión:')
for (const sess of ['London', 'LondonNyOverlap', 'NewYork']) {
  const s = stats(combD.filter(t => t.session === sess))
  const marker = s.wr >= 55 ? ' ✓✓' : s.wr >= 40 ? ' ✓' : ''
  if (s.n > 0) console.log(`  ${sess.padEnd(22)} n=${s.n}  WR=${s.wr}%  AvgR=${s.avgR}${marker}`)
}
