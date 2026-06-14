import type { RbfSignal, AmdSignal, BeSignal } from './supabase'
import type { Trade } from './types'
import { RISK_USD, ACCOUNT } from './types'

export function signalToTrade(sig: RbfSignal, idx: number, equity: number): Trade {
  const entry   = sig.entry_price ?? 1
  const stop    = sig.stop_price  ?? entry
  const stopPct = Math.abs(entry - stop) / entry
  const riskUsd = RISK_USD
  const r       = sig.result_r ?? null
  const pnlUsd  = r != null ? r * riskUsd : 0
  const isOpen  = sig.result_r == null

  const durationMin = (() => {
    if (!sig.closed_at || !sig.timestamp_ms) return null
    try {
      const ms = new Date(sig.closed_at).getTime() - sig.timestamp_ms
      return Math.floor(ms / 60000)
    } catch { return null }
  })()

  return {
    idx,
    id:             sig.id,
    sym:            sig.symbol,
    dir:            sig.direction,
    session:        sig.session,
    score:          sig.confluence_score,
    entry,
    stop,
    target:         sig.target_price ?? entry,
    exit:           sig.exit_price ?? 0,
    resultR:        r,
    pnlUsd:         Math.round(pnlUsd * 100) / 100,
    riskUsd:        Math.round(riskUsd * 10000) / 10000,
    stopPct:        Math.round(stopPct * 100000) / 1000,
    equity,
    reason:         sig.exit_reason ?? (isOpen ? 'OPEN' : '?'),
    tsMs:           sig.timestamp_ms,
    ts:             Math.floor(sig.timestamp_ms / 1000),
    closedAt:       sig.closed_at,
    regime:         sig.macro_regime ?? '',
    sessionPhase:   sig.session_phase ?? '',
    evidence:       sig.evidence ?? [],
    confluenceFlags:sig.confluence_flags ?? [],
    vetoReason:     sig.veto_reason ?? '',
    cvdInRange:     sig.cvd_in_range,
    vr:             sig.vr_at_breakout,
    priceVsVwap:    sig.price_vs_vwap_pct,
    funding:        sig.funding_at_entry,
    cvdSlope:       sig.cvd_slope_at_entry,
    obi:            sig.obi_at_entry,
    dz:             sig.dz_at_entry,
    rangePct:       sig.range_pct,
    rangeBars:      sig.range_bars,
    rangeTouch:     sig.range_touch_count,
    durationMin,
    isOpen,
  }
}

export function buildTrades(signals: RbfSignal[]): Trade[] {
  let equity = ACCOUNT
  return signals.map((sig, i) => {
    const t = signalToTrade(sig, i + 1, equity)
    equity = Math.round((equity + t.pnlUsd) * 100) / 100
    return t
  })
}

export function sesLabel(s: string) {
  if (s === 'LondonNyOverlap') return 'Overlap'
  if (s === 'NewYork') return 'NY'
  return s || '?'
}

export function fmtR(r: number | null) {
  if (r == null) return '—'
  return (r >= 0 ? '+' : '') + r.toFixed(3) + 'R'
}

export function fmtUsd(n: number) {
  return (n >= 0 ? '+$' : '-$') + Math.abs(n).toFixed(3)
}

export function fmtDur(m: number | null) {
  if (m == null) return '—'
  if (m < 60) return m + 'm'
  return Math.floor(m / 60) + 'h ' + (m % 60) + 'm'
}

export function fmtDate(ms: number) {
  const d = new Date(ms)
  return `${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')} ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`
}

// ─── AMD converter ────────────────────────────────────────────────────────────

export function amdSignalToTrade(sig: AmdSignal, idx: number, equity: number): Trade {
  const entry   = sig.entry_price ?? 1
  const stop    = sig.stop_price  ?? entry
  const stopPct = Math.abs(entry - stop) / entry
  const r       = sig.result_r ?? null
  const pnlUsd  = r != null ? r * RISK_USD : 0
  const isOpen  = sig.result_r == null

  const durationMin = (() => {
    if (!sig.closed_at_ms || !sig.timestamp_ms) return null
    return Math.floor((sig.closed_at_ms - sig.timestamp_ms) / 60000)
  })()

  return {
    idx, id: sig.id, sym: sig.symbol, dir: sig.direction,
    session: sig.session, score: null,
    entry, stop, target: sig.target_price ?? entry, exit: 0,
    resultR: r, pnlUsd: Math.round(pnlUsd * 100) / 100,
    riskUsd: Math.round(RISK_USD * 10000) / 10000,
    stopPct: Math.round(stopPct * 100000) / 1000,
    equity,
    reason: sig.exit_reason ?? (isOpen ? 'OPEN' : '?'),
    tsMs: sig.timestamp_ms, ts: Math.floor(sig.timestamp_ms / 1000),
    closedAt: sig.closed_at_ms ? new Date(sig.closed_at_ms).toISOString() : null,
    regime: '', sessionPhase: '', evidence: [], confluenceFlags: [], vetoReason: '',
    cvdInRange: null, vr: null, priceVsVwap: null, funding: null,
    cvdSlope: null, obi: null, dz: null,
    rangePct: null, rangeBars: null, rangeTouch: null, durationMin, isOpen,
  }
}

export function buildAmdTrades(signals: AmdSignal[]): Trade[] {
  let equity = ACCOUNT
  return signals.map((sig, i) => {
    const t = amdSignalToTrade(sig, i + 1, equity)
    equity = Math.round((equity + t.pnlUsd) * 100) / 100
    return t
  })
}

// ─── BE converter ─────────────────────────────────────────────────────────────

export function beSignalToTrade(sig: BeSignal, idx: number, equity: number): Trade {
  const entry   = sig.entry_price ?? 1
  const stop    = sig.stop_price  ?? entry
  const stopPct = Math.abs(entry - stop) / entry
  const r       = sig.result_r ?? null
  const pnlUsd  = r != null ? r * RISK_USD : 0
  const isOpen  = sig.result_r == null

  const durationMin = (() => {
    if (!sig.closed_at || !sig.timestamp_ms) return null
    try { return Math.floor((new Date(sig.closed_at).getTime() - sig.timestamp_ms) / 60000) }
    catch { return null }
  })()

  return {
    idx, id: sig.id, sym: sig.symbol, dir: 'Short',
    session: sig.session, score: null,
    entry, stop, target: sig.target_price ?? entry, exit: 0,
    resultR: r, pnlUsd: Math.round(pnlUsd * 100) / 100,
    riskUsd: Math.round(RISK_USD * 10000) / 10000,
    stopPct: Math.round(stopPct * 100000) / 1000,
    equity,
    reason: sig.exit_reason ?? (isOpen ? 'OPEN' : '?'),
    tsMs: sig.timestamp_ms, ts: Math.floor(sig.timestamp_ms / 1000),
    closedAt: sig.closed_at,
    regime: '', sessionPhase: '', evidence: [], confluenceFlags: [], vetoReason: '',
    cvdInRange: sig.range_cvd, vr: sig.vr_at_breakout, priceVsVwap: null,
    funding: null, cvdSlope: null, obi: null, dz: null,
    rangePct: sig.range_pct, rangeBars: sig.range_bars, rangeTouch: null,
    durationMin, isOpen,
  }
}

export function buildBeTrades(signals: BeSignal[]): Trade[] {
  let equity = ACCOUNT
  return signals.map((sig, i) => {
    const t = beSignalToTrade(sig, i + 1, equity)
    equity = Math.round((equity + t.pnlUsd) * 100) / 100
    return t
  })
}

// ─── Stats helpers ────────────────────────────────────────────────────────────

export function winRate(trades: Trade[]) {
  const closed = trades.filter(t => !t.isOpen)
  if (!closed.length) return 0
  return closed.filter(t => (t.resultR ?? 0) > 0).length / closed.length * 100
}

export function avgR(trades: Trade[]) {
  const closed = trades.filter(t => !t.isOpen && t.resultR != null)
  if (!closed.length) return 0
  return closed.reduce((s, t) => s + (t.resultR ?? 0), 0) / closed.length
}
