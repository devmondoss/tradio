import { supabase } from './supabase'
import type { Trade } from './types'
import { POSITION, ACCOUNT } from './types'

// ─── params (mirrors config/strategy.toml + range_breakout_flow.rs) ───────────
const CONS_MIN          = 15
const CONS_MAX          = 60
const RANGE_MIN         = 0.0008
const RANGE_MAX         = 0.0055
const VR_MIN            = 3.0
const DZ_MIN            = 0.5    // dz_dir mínimo (0 = sin presión)
const DZ_MAX            = 3.0    // dz_dir máximo (>3 = fakeout)
// Stop = consolidation HIGH (natural structure), not ATR — ATR M1 es ruido puro
// (se mantiene ATR solo para trailing una vez activo)
const TRAIL_ATR_K       = 1.2
const TRAIL_ACTIVATE_R  = 1.5
const TIME_STOP_BARS    = 30   // más tiempo para target proporcional al range stop
const RR                = 2.0
const SESSIONS_OK       = new Set(['London', 'LondonNyOverlap', 'NewYork'])

interface Bar {
  ts_ms:     number
  open:      number
  high:      number
  low:       number
  close:     number
  vr:        number | null
  atr:       number | null
  session:   string | null
  cvd_slope: number | null
  dz:        number | null   // Z-score del delta de la barra
  obi_l5:    number | null   // Order Book Imbalance ventana L5
  bar_delta: number | null   // delta neto de la barra (taker_buy - taker_sell vol)
  vwap:      number | null   // VWAP de sesión
}

const SYM_TABLES: Record<string, string> = {
  BTCUSDT: 'btc_bars',
  ETHUSDT: 'eth_bars',
  BNBUSDT: 'bnb_bars',
  SOLUSDT: 'sol_bars',
  XRPUSDT: 'xrp_bars',
}

async function fetchBars(table: string, startMs: number, onProgress?: (n: number) => void): Promise<Bar[]> {
  const all: Bar[] = []
  const PAGE = 1000
  let from = 0
  while (true) {
    const { data, error } = await supabase
      .from(table)
      .select('ts_ms,open,high,low,close,vr,atr,session,cvd_slope,dz,obi_l5,bar_delta,vwap')
      .gte('ts_ms', startMs)
      .order('ts_ms', { ascending: true })
      .range(from, from + PAGE - 1)
    if (error || !data || data.length === 0) break
    all.push(...(data as Bar[]))
    onProgress?.(all.length)
    if (data.length < PAGE) break
    from += PAGE
  }
  return all
}

// Simula el manejo de posición barra a barra (mirrors rbf_paper.rs on_bar_close).
// Solo Short. stop > entry (stop por encima), target < entry.
function simulate(
  bars: Bar[], entry: number, stop: number, target: number, atr: number, startMs: number
): { r: number; exit: number; reason: string; durationMin: number; closedAt: string } {
  let bestLow     = entry    // extremo más favorable (Short: mínimo alcanzado)
  let trailActive = false
  let trailStop   = stop     // se mueve hacia abajo cuando trailing activo

  for (let k = 0; k < bars.length; k++) {
    const b = bars[k]
    const effectiveStop = trailActive ? trailStop : stop

    // 1. Stop check (tiene prioridad sobre target si ambos tocan en misma barra)
    if (b.high >= effectiveStop) {
      const dMin   = Math.floor((b.ts_ms - startMs) / 60000)
      const reason = trailActive ? 'TRAILING_STOP' : 'STOP_LOSS'
      return { r: (entry - effectiveStop) / (stop - entry), exit: effectiveStop, reason, durationMin: dMin, closedAt: new Date(b.ts_ms).toISOString() }
    }

    // 2. Target check
    if (b.low <= target) {
      const dMin = Math.floor((b.ts_ms - startMs) / 60000)
      return { r: (entry - target) / (stop - entry), exit: target, reason: 'TAKE_PROFIT', durationMin: dMin, closedAt: new Date(b.ts_ms).toISOString() }
    }

    // 3. Actualizar extremo favorable y trailing stop
    if (b.low < bestLow) bestLow = b.low
    const favR = (entry - bestLow) / (stop - entry)
    if (favR >= TRAIL_ACTIVATE_R && !trailActive) {
      trailActive = true
    }
    if (trailActive && atr > 0) {
      const candidate = bestLow + TRAIL_ATR_K * atr
      if (candidate < trailStop) trailStop = candidate
    }

    // 4. Time stop: barra 15+ en pérdida → cierra al close (mirrors TIME_STOP_BARS check)
    if (k + 1 >= TIME_STOP_BARS) {
      const pnl = entry - b.close  // Short: positivo = ganancia
      if (pnl < 0) {
        const dMin = Math.floor((b.ts_ms - startMs) / 60000)
        return { r: pnl / (stop - entry), exit: b.close, reason: 'TIME_STOP', durationMin: dMin, closedAt: new Date(b.ts_ms).toISOString() }
      }
    }
  }

  return { r: 0, exit: 0, reason: 'DATA_END', durationMin: 0, closedAt: '' }
}

function runOnBars(sym: string, bars: Bar[], idxOffset: number, equityStart: number): Trade[] {
  const trades: Trade[] = []
  let equity = equityStart

  for (let i = CONS_MAX; i < bars.length - TIME_STOP_BARS; i++) {
    const bar = bars[i]

    // ── Filtros previos al scan de consolidación ──────────────────────────────
    if (!SESSIONS_OK.has(bar.session ?? '')) continue
    if ((bar.vr ?? 0) < VR_MIN)             continue
    if ((bar.atr ?? 0) <= 0)                continue

    // DZ gate
    if (bar.dz != null) {
      const dzDir = Math.max(-bar.dz, 0)
      if (dzDir < DZ_MIN || dzDir > DZ_MAX) continue
    }

    // VSWAP proximity gate: no short si precio >0.3% bajo el VWAP
    // Backtest 30d: precio <-0.3% VWAP → WR=6.5%; dentro → WR=37.3%
    if (bar.vwap != null && bar.vwap > 0) {
      const pct = (bar.close - bar.vwap) / bar.vwap
      if (pct < -0.003) continue
    }

    let found = false
    for (let clen = CONS_MIN; clen <= CONS_MAX && !found; clen++) {
      const win     = bars.slice(i - clen, i)
      const lo      = Math.min(...win.map(b => b.low))
      const hi      = Math.max(...win.map(b => b.high))
      const rangePct = (hi - lo) / lo
      if (rangePct < RANGE_MIN || rangePct > RANGE_MAX) continue

      // Short only (LONGS_ENABLED = false)
      if (bar.close >= lo) continue

      // cvd_slope gate ELIMINADO: backtest 30d muestra que slope>=0 (absorción)
      // tiene mejor WR que slope<0. El filtro estaba inversamente correlacionado.

      // Breakout extension gate: close debe romper >0.1% más allá del range_low
      // Backtest 30d: ext>0.1% → WR=44.4%; ext<0.1% → WR=18.8%
      const breakoutExt = (lo - bar.close) / lo
      if (breakoutExt < 0.001) continue

      // CVD in range: si todos los bars del rango tienen bar_delta, la suma debe
      // ser negativa (presión vendedora neta durante consolidación → breakout real)
      if (win.every(b => b.bar_delta != null)) {
        const cvdInRange = win.reduce((acc, b) => acc + b.bar_delta!, 0)
        if (cvdInRange >= 0) continue
      }

      // ── Construir trade ────────────────────────────────────────────────────
      // Stop = range HIGH: si el precio regresa al rango el breakout falló.
      // Esto evita stops dentro del ruido M1 (ATR ~0.1% vs rango ~0.4%).
      const entry    = bar.close
      const atr      = bar.atr!
      const stop     = hi                        // techo del rango de consolidación
      const target   = entry - RR * (stop - entry)
      const stopPct  = (stop - entry) / entry
      const riskUsd  = POSITION * stopPct

      const sim = simulate(bars.slice(i + 1, i + 1 + TIME_STOP_BARS + 30), entry, stop, target, atr, bar.ts_ms)

      const pnlUsd = sim.r * riskUsd
      equity = Math.round((equity + pnlUsd) * 100) / 100
      const idx = idxOffset + trades.length + 1

      // Campos de microestructura disponibles en la barra de entrada
      const dzDir        = bar.dz      != null ? Math.max(-bar.dz, 0)                  : null
      const priceVsVwap  = bar.vwap    != null && bar.vwap > 0
                           ? (entry - bar.vwap) / bar.vwap
                           : null

      const t: Trade = {
        idx,
        id:             `bt-${sym}-${bar.ts_ms}`,
        sym,
        dir:            'Short',
        session:        bar.session ?? '',
        score:          null,
        entry,
        stop,
        target,
        exit:           sim.exit,
        resultR:        Math.round(sim.r * 1000) / 1000,
        pnlUsd:         Math.round(pnlUsd * 100) / 100,
        riskUsd:        Math.round(riskUsd * 10000) / 10000,
        stopPct:        Math.round(stopPct * 100000) / 1000,
        equity,
        reason:         sim.reason,
        tsMs:           bar.ts_ms,
        ts:             Math.floor(bar.ts_ms / 1000),
        closedAt:       sim.closedAt || null,
        regime:         '',
        sessionPhase:   '',
        evidence:       [],
        confluenceFlags:[],
        vetoReason:     '',
        cvdInRange:     null,
        vr:             bar.vr,
        priceVsVwap,
        funding:        null,
        cvdSlope:       bar.cvd_slope,
        obi:            bar.obi_l5,
        dz:             dzDir,
        rangePct:       Math.round(rangePct * 100000) / 1000,
        rangeBars:      clen,
        rangeTouch:     null,
        durationMin:    sim.durationMin,
        isOpen:         sim.reason === 'DATA_END',
      }
      trades.push(t)
      found = true
      i++ // evitar señales solapadas
    }
  }
  return trades
}

export interface BtProgress { loaded: number; total: number; sym: string }

export async function runBacktest(
  days = 14,
  onProgress?: (p: BtProgress) => void
): Promise<Trade[]> {
  const startMs = Date.now() - days * 24 * 60 * 60 * 1000
  const syms = Object.keys(SYM_TABLES)

  const allBars = await Promise.all(
    syms.map(sym =>
      fetchBars(SYM_TABLES[sym], startMs, n => onProgress?.({ loaded: n, total: days * 1440, sym }))
    )
  )

  let equity = ACCOUNT
  let idxOff = 0
  const allTrades: Trade[] = []

  for (let si = 0; si < syms.length; si++) {
    const ts = runOnBars(syms[si], allBars[si], idxOff, equity)
    allTrades.push(...ts)
    if (ts.length) equity = ts[ts.length - 1].equity
    idxOff += ts.length
  }

  // Ordenar por tiempo de entrada, re-numerar y recalcular equity acumulada
  allTrades.sort((a, b) => a.tsMs - b.tsMs)
  let eq = ACCOUNT
  allTrades.forEach((t, i) => {
    t.idx    = i + 1
    eq       = Math.round((eq + t.pnlUsd) * 100) / 100
    t.equity = eq
  })

  return allTrades
}
