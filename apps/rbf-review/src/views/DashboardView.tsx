import { useEffect, useState } from 'react'
import { supabase, type AmdSignal, type BeSignal } from '../lib/supabase'
import type { Trade } from '../lib/types'
import { ACCOUNT, RISK_USD } from '../lib/types'
import { fmtR, sesLabel } from '../lib/utils'

// ─── Shared trade type ────────────────────────────────────────────────────────

interface StratTrade {
  resultR: number | null
  pnlUsd:  number
  equity:  number
  session: string
  sym:     string
  dir:     string
  reason:  string
  tsMs:    number
  isOpen:  boolean
}

// ─── Dataset metadata ─────────────────────────────────────────────────────────

interface DatasetMeta {
  source:  string
  period?: string
  macro:   string[]
  micro:   string[]
}

const RBF_META: DatasetMeta = {
  source: 'rbf_signals · Supabase live',
  macro:  ['0.08 – 0.55% rango', 'score ≥ 4', 'London · Overlap · NY', 'expansion ≤ 1'],
  micro:  ['CVD acum en rango', 'VR ≥ 3× breakout', 'pre-CVD gate 5b', 'OBI intrabar 10s', 'spread gate', 'OI covering gate'],
}

const AMD_META: DatasetMeta = {
  source: 'amd_signals · Supabase live',
  macro:  ['acum 4–30% · 10–60 barras', 'London · Overlap · NY', 'Long + Short'],
  micro:  ['VR ≥ 1.5× en spike', 'CVD diverge del precio', 'liq_ratio ≤ 1.5', '|dz| ≥ 1.0'],
}

const BE_META: DatasetMeta = {
  source: 'be_backtest_script.py · Binance FAPI',
  period: '180d · Ene – Jun',
  macro:  ['BTC · ETH · BNB · SOL', 'London · Overlap', 'Solo Shorts'],
  micro:  ['CVD+ en rango ≥ 8b', 'flip ratio ≥ 0.40', 'VR ≥ 2.5×', 'close_loc ≤ 0.35', 'bear_body ≥ 0.35', 'upper_wick ≤ 0.30'],
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function dateRange(trades: StratTrade[]): string {
  const valid = trades.filter(t => t.tsMs > 0)
  if (!valid.length) return ''
  const min = Math.min(...valid.map(t => t.tsMs))
  const max = Math.max(...valid.map(t => t.tsMs))
  const fmt = (ms: number) => {
    const d = new Date(ms)
    return `${d.getUTCDate()} ${d.toLocaleString('en', { month: 'short', timeZone: 'UTC' })}`
  }
  const days = Math.max(1, Math.round((max - min) / 86400000) + 1)
  return `${fmt(min)} – ${fmt(max)} · ${days}d`
}

function fromLiveTrades(
  items: { result_r: number | null; session: string; symbol: string; direction?: string; exit_reason?: string | null; timestamp_ms: number }[],
  capital: number, riskUsd: number,
): StratTrade[] {
  let eq = capital
  return items.map(s => {
    const r = s.result_r ?? null
    const isOpen = r == null
    const pnl = r != null ? r * riskUsd : 0
    eq = Math.round((eq + pnl) * 100) / 100
    return { resultR: r, pnlUsd: Math.round(pnl * 100) / 100, equity: eq, session: s.session ?? '', sym: s.symbol ?? '', dir: s.direction ?? 'Short', reason: s.exit_reason ?? (isOpen ? 'OPEN' : '?'), tsMs: s.timestamp_ms, isOpen }
  })
}

function fromRbfTrades(trades: Trade[]): StratTrade[] {
  return trades.map(t => ({ resultR: t.resultR, pnlUsd: t.pnlUsd, equity: t.equity, session: t.session, sym: t.sym, dir: t.dir, reason: t.reason, tsMs: t.tsMs, isOpen: t.isOpen }))
}

// ─── Chip ─────────────────────────────────────────────────────────────────────

function Chip({ label, accent }: { label: string; accent: string }) {
  return (
    <span style={{
      display: 'inline-block', padding: '1px 6px', borderRadius: 3,
      border: `1px solid ${accent}33`,
      background: `${accent}0d`,
      color: 'var(--text2)', fontSize: 8.5, lineHeight: '1.7',
      whiteSpace: 'nowrap',
    }}>{label}</span>
  )
}

// ─── Dataset panel ────────────────────────────────────────────────────────────

function DatasetInfo({ meta, period }: { meta: DatasetMeta; period: string }) {
  const p = meta.period ?? period

  return (
    <div style={{ padding: '6px 10px 7px', background: 'rgba(0,0,0,0.18)', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 5 }}>
        <span style={{ fontSize: 9, color: 'var(--text2)', fontWeight: 500 }}>{meta.source}</span>
        {p && <span style={{ fontSize: 8, color: 'var(--text3)' }}>{p}</span>}
      </div>
      <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap', alignItems: 'center', marginBottom: 3 }}>
        <span style={{ fontSize: 7.5, color: 'var(--text3)', fontWeight: 700, letterSpacing: .6, textTransform: 'uppercase', marginRight: 1, flexShrink: 0 }}>macro</span>
        {meta.macro.map((f, i) => <Chip key={i} label={f} accent="var(--blue)" />)}
      </div>
      <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ fontSize: 7.5, color: 'var(--text3)', fontWeight: 700, letterSpacing: .6, textTransform: 'uppercase', marginRight: 1, flexShrink: 0 }}>micro</span>
        {meta.micro.map((f, i) => <Chip key={i} label={f} accent="var(--yellow)" />)}
      </div>
    </div>
  )
}

// ─── Equity curve ─────────────────────────────────────────────────────────────

function EquityCurve({ trades, capital, color, emptyMsg }: {
  trades: StratTrade[]; capital: number; color: string; emptyMsg: string
}) {
  const closed = trades.filter(t => !t.isOpen)
  const pts    = [capital, ...closed.map(t => t.equity)]

  if (pts.length < 2) return (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text3)', fontSize: 10 }}>
      {emptyMsg}
    </div>
  )

  const W = 1000, H = 160
  const min = Math.min(...pts), max = Math.max(...pts)
  const pad = (max - min) * 0.15 || 2
  const lo  = min - pad, hi = max + pad
  const xp  = (i: number) => (i / (pts.length - 1)) * W
  const yp  = (v: number) => H - ((v - lo) / (hi - lo)) * H
  const d   = pts.map((v, i) => `${i === 0 ? 'M' : 'L'}${xp(i).toFixed(1)},${yp(v).toFixed(1)}`).join(' ')
  const area = d + ` L${xp(pts.length - 1).toFixed(1)},${H} L0,${H} Z`
  const final = pts[pts.length - 1]
  const gain  = final - capital
  const gSign = gain >= 0 ? '+' : '-'
  const gColor = gain >= 0 ? 'var(--green)' : 'var(--red)'
  let peak = pts[0], maxDd = 0
  for (const v of pts) { peak = Math.max(peak, v); maxDd = Math.max(maxDd, (peak - v) / peak * 100) }
  const gradId = `grad-${color.replace(/[^a-z0-9]/gi, '')}`

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0, padding: '4px 8px 6px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3, fontSize: 9 }}>
        <span style={{ color: 'var(--text3)' }}>${capital}</span>
        <span style={{ color: 'var(--text3)' }}>→</span>
        <span style={{ color, fontWeight: 700 }}>${final.toFixed(2)}</span>
        <span style={{ color: gColor, fontWeight: 700 }}>{gSign}${Math.abs(gain).toFixed(2)}</span>
        <span style={{ color: gColor }}>({gSign}{Math.abs(gain / capital * 100).toFixed(1)}%)</span>
        <span style={{ marginLeft: 'auto', color: 'var(--text3)' }}>DD <span style={{ color: 'var(--red)' }}>-{maxDd.toFixed(1)}%</span></span>
        <span style={{ color: 'var(--text3)' }}>{closed.length}×</span>
      </div>
      <div style={{ flex: 1, minHeight: 0, borderRadius: 4, overflow: 'hidden', background: 'var(--bg2)' }}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="100%" preserveAspectRatio="none" style={{ display: 'block' }}>
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity="0.20" />
              <stop offset="100%" stopColor={color} stopOpacity="0.01" />
            </linearGradient>
          </defs>
          <line x1={0} y1={yp(capital)} x2={W} y2={yp(capital)} stroke="var(--border)" strokeWidth={1} strokeDasharray="6,5" />
          <path d={area} fill={`url(#${gradId})`} />
          <path d={d}    fill="none" stroke={color} strokeWidth={1.5} />
          {pts.map((v, i) => {
            const isLast = i === pts.length - 1
            if (!isLast && i % Math.max(1, Math.floor(pts.length / 60)) !== 0) return null
            return <circle key={i} cx={xp(i)} cy={yp(v)} r={isLast ? 4 : 1.5}
              fill={v >= capital ? 'var(--green)' : 'var(--red)'}
              stroke={isLast ? 'var(--bg2)' : 'none'} strokeWidth={1.5} />
          })}
        </svg>
      </div>
    </div>
  )
}

// ─── Stat header ──────────────────────────────────────────────────────────────

function StatHeader({ trades, capital, color, label, note, isBt, onToggle }: {
  trades: StratTrade[]; capital: number; color: string
  label: string; note: string; isBt: boolean; onToggle: () => void
}) {
  const closed  = trades.filter(t => !t.isOpen)
  const n       = closed.length
  const wins    = closed.filter(t => (t.resultR ?? 0) > 0).length
  const wr      = n ? wins / n * 100 : 0
  const totalR  = closed.reduce((s, t) => s + (t.resultR ?? 0), 0)
  const avgR    = n ? totalR / n : 0
  const final   = closed.length ? closed[closed.length - 1].equity : capital
  const gain    = final - capital
  const gSign   = gain >= 0 ? '+' : '-'
  const gColor  = gain >= 0 ? 'var(--green)' : 'var(--red)'
  const open    = trades.filter(t => t.isOpen).length
  const wrColor = n === 0 ? 'var(--text3)' : wr >= 55 ? 'var(--green)' : wr >= 45 ? 'var(--yellow)' : 'var(--red)'

  return (
    <div style={{ borderTop: `2px solid ${color}`, padding: '8px 10px', flexShrink: 0, background: 'var(--bg2)', borderBottom: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
        <span style={{ fontSize: 11, fontWeight: 800, color, letterSpacing: 1.5 }}>{label}</span>
        <span style={{ fontSize: 8.5, color: 'var(--text3)', marginLeft: 8 }}>{note}</span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 6, alignItems: 'center' }}>
          {!isBt && open > 0 && (
            <span style={{ fontSize: 8, color: 'var(--blue)', fontWeight: 700, letterSpacing: .5 }}>{open} OPEN</span>
          )}
          <div style={{ display: 'flex', borderRadius: 4, overflow: 'hidden', border: '1px solid var(--border)' }}>
            {(['LIVE', 'BT'] as const).map(m => {
              const active = (m === 'BT') === isBt
              return (
                <button key={m} onClick={() => { if (!active) onToggle() }} style={{
                  padding: '2px 8px', fontSize: 8.5, fontWeight: 700, letterSpacing: .5,
                  border: 'none', cursor: 'pointer', fontFamily: 'inherit',
                  background: active ? color : 'transparent',
                  color: active ? '#000' : 'var(--text3)',
                }}>{m}</button>
              )
            })}
          </div>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 0 }}>
        {[
          { lbl: 'trades', val: n > 0 ? String(n) : '—', col: 'var(--text)' },
          { lbl: 'win rate', val: n > 0 ? `${wr.toFixed(0)}%` : '—', col: wrColor },
          { lbl: 'avg r', val: n > 0 ? fmtR(avgR) : '—', col: n > 0 ? (avgR >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--text3)' },
          { lbl: 'total r', val: n > 0 ? fmtR(totalR) : '—', col: n > 0 ? (totalR >= 0 ? 'var(--green)' : 'var(--red)') : 'var(--text3)' },
          { lbl: 'p&l', val: n > 0 ? `${gSign}$${Math.abs(gain).toFixed(2)}` : '—', col: n > 0 ? gColor : 'var(--text3)' },
        ].map((s, i) => (
          <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 2, borderLeft: i > 0 ? '1px solid var(--border)' : 'none', paddingLeft: i > 0 ? 8 : 0 }}>
            <span style={{ fontSize: 7.5, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: .8 }}>{s.lbl}</span>
            <span style={{ fontSize: 14, fontWeight: 700, color: s.col, lineHeight: 1 }}>{s.val}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Breakdown table ──────────────────────────────────────────────────────────

function BreakdownTable({ trades, groupFn, keys, title }: {
  trades: StratTrade[]; groupFn: (t: StratTrade) => string; keys: string[]; title: string
}) {
  const closed = trades.filter(t => !t.isOpen)
  const rC = (v: number) => v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--text3)'

  const groups = keys.map(k => {
    const g    = closed.filter(t => groupFn(t) === k)
    const wins = g.filter(t => (t.resultR ?? 0) > 0).length
    const tot  = g.reduce((s, t) => s + (t.resultR ?? 0), 0)
    const ar   = g.length ? tot / g.length : 0
    return { k, n: g.length, wr: g.length ? wins / g.length * 100 : 0, tot, ar }
  }).filter(g => g.n > 0)

  if (!groups.length) return null

  const atot = closed.reduce((s, t) => s + (t.resultR ?? 0), 0)
  const aar  = closed.length ? atot / closed.length : 0
  const awr  = closed.length ? closed.filter(t => (t.resultR ?? 0) > 0).length / closed.length * 100 : 0
  const showTotal = groups.length > 1

  return (
    <div style={{ padding: '6px 8px 4px' }}>
      <div style={{ fontSize: 7.5, textTransform: 'uppercase', letterSpacing: .8, color: 'var(--text3)', marginBottom: 4, fontWeight: 600 }}>{title}</div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 9 }}>
        <thead>
          <tr>
            {['', 'n', 'WR', 'Avg R', 'Tot R'].map(h => (
              <th key={h} style={{ padding: '2px 6px 3px', color: 'var(--text3)', fontWeight: 500, fontSize: 7.5, textTransform: 'uppercase', letterSpacing: .6, borderBottom: '1px solid var(--border)', textAlign: h === '' ? 'left' : 'right' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {groups.map((g, i) => (
            <tr key={g.k} style={{ background: i % 2 === 1 ? 'rgba(255,255,255,0.02)' : 'transparent' }}>
              <td style={{ padding: '3px 6px', color: 'var(--text2)' }}>{g.k}</td>
              <td style={{ padding: '3px 6px', color: 'var(--text3)', textAlign: 'right' }}>{g.n}</td>
              <td style={{ padding: '3px 6px', textAlign: 'right', color: g.wr >= 55 ? 'var(--green)' : g.wr >= 45 ? 'var(--yellow)' : 'var(--red)', fontWeight: 600 }}>{g.wr.toFixed(0)}%</td>
              <td style={{ padding: '3px 6px', textAlign: 'right', color: rC(g.ar) }}>{fmtR(g.ar)}</td>
              <td style={{ padding: '3px 6px', textAlign: 'right', color: rC(g.tot), fontWeight: 600 }}>{fmtR(g.tot)}</td>
            </tr>
          ))}
          {showTotal && (
            <tr style={{ borderTop: '1px solid var(--border)' }}>
              <td style={{ padding: '3px 6px', color: 'var(--text3)', fontSize: 8, fontWeight: 700 }}>TOTAL</td>
              <td style={{ padding: '3px 6px', color: 'var(--text3)', textAlign: 'right' }}>{closed.length}</td>
              <td style={{ padding: '3px 6px', textAlign: 'right', color: awr >= 55 ? 'var(--green)' : awr >= 45 ? 'var(--yellow)' : 'var(--red)', fontWeight: 700 }}>{closed.length ? `${awr.toFixed(0)}%` : '—'}</td>
              <td style={{ padding: '3px 6px', textAlign: 'right', color: rC(aar), fontWeight: 700 }}>{closed.length ? fmtR(aar) : '—'}</td>
              <td style={{ padding: '3px 6px', textAlign: 'right', color: rC(atot), fontWeight: 700 }}>{closed.length ? fmtR(atot) : '—'}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

// ─── Column ───────────────────────────────────────────────────────────────────

function StrategyCol({ live, bt, btLoading, btError, meta, capital, color, label, note, sessions, symbols }: {
  live: StratTrade[]; bt: StratTrade[]; btLoading: boolean; btError: boolean
  meta: DatasetMeta; capital: number; color: string; label: string; note: string
  sessions: string[]; symbols: string[]
}) {
  const [isBt, setIsBt] = useState(false)
  const trades   = isBt ? bt : live
  const btPeriod = meta.period ?? (bt.length ? dateRange(bt) : '')
  const showBtLoading = isBt && btLoading
  const showBtError   = isBt && btError && !btLoading && bt.length === 0

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden', borderRight: '1px solid var(--border)' }}>
      <StatHeader trades={trades} capital={capital} color={color} label={label} note={note} isBt={isBt} onToggle={() => setIsBt(v => !v)} />
      {isBt && !showBtLoading && !showBtError && <DatasetInfo meta={meta} period={btPeriod} />}

      <div style={{ height: 186, flexShrink: 0, borderBottom: '1px solid var(--border)', display: 'flex', flexDirection: 'column' }}>
        {showBtLoading ? (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 6 }}>
            <div style={{ color: 'var(--text3)', fontSize: 10 }}>cargando backtest…</div>
            <div style={{ width: 100, height: 2, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
              <div style={{ height: '100%', background: color, borderRadius: 2, width: '60%', animation: 'pulse 1s ease-in-out infinite alternate' }} />
            </div>
          </div>
        ) : showBtError ? (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--red)', fontSize: 9 }}>
            Error al cargar backtest
          </div>
        ) : (
          <EquityCurve trades={trades} capital={capital} color={color}
            emptyMsg={isBt ? 'Sin datos' : 'Sin señales live aún'} />
        )}
      </div>

      {!showBtLoading && !showBtError && (
        <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
          <BreakdownTable trades={trades} groupFn={t => sesLabel(t.session)} keys={sessions.map(sesLabel)} title="Por sesión" />
          <div style={{ height: 1, background: 'var(--border)', margin: '0 8px' }} />
          <BreakdownTable trades={trades} groupFn={t => t.sym.replace('USDT', '')} keys={symbols} title="Por símbolo" />
        </div>
      )}
    </div>
  )
}

// ─── Main ─────────────────────────────────────────────────────────────────────

export default function DashboardView({ rbfTrades }: { rbfTrades: Trade[] }) {
  const [amdSignals, setAmdSignals] = useState<AmdSignal[]>([])
  const [beSignals,  setBeSignals]  = useState<BeSignal[]>([])
  const [loading,    setLoading]    = useState(true)

  // BT data — fetched lazily from Python API in the background
  const [rbfBtTrades, setRbfBtTrades] = useState<Trade[]>([])
  const [beBtTrades,  setBeBtTrades]  = useState<Trade[]>([])
  const [rbfBtLoading, setRbfBtLoading] = useState(true)
  const [beBtLoading,  setBeBtLoading]  = useState(true)
  const [rbfBtError,   setRbfBtError]   = useState(false)
  const [beBtError,    setBeBtError]    = useState(false)

  useEffect(() => {
    // ── Live signals (Supabase, fast) ────────────────────────────────────────
    const since = Date.now() - 90 * 86400000
    Promise.all([
      supabase.from('amd_signals').select('id,timestamp_ms,symbol,direction,session,entry_price,stop_price,target_price,rr,result_r,exit_reason,closed_at_ms,is_active').gte('timestamp_ms', since).order('timestamp_ms', { ascending: true }),
      supabase.from('be_signals').select('id,timestamp_ms,symbol,session,entry_price,stop_price,target_price,rr,range_pct,range_bars,range_cvd,cvd_flip_ratio,vr_at_breakout,result_r,exit_reason,closed_at,active').gte('timestamp_ms', since).order('timestamp_ms', { ascending: true }),
    ]).then(([amdRes, beRes]) => {
      if (amdRes.data) setAmdSignals(amdRes.data as AmdSignal[])
      if (beRes.data)  setBeSignals(beRes.data  as BeSignal[])
      setLoading(false)
    })

    // ── Python backtests (subprocess, lento 1ª vez / cache rápido después) ──
    // RBF: usa todos los días con microestructura disponibles
    fetch('/api/backtest?days=90')
      .then(r => r.json())
      .then(d => { setRbfBtTrades((d.trades ?? []) as Trade[]); setRbfBtLoading(false) })
      .catch(() => { setRbfBtLoading(false); setRbfBtError(true) })

    // BE: 180d desde Binance FAPI con caché en disco
    fetch('/api/backtest/be?days=180')
      .then(r => r.json())
      .then(d => { setBeBtTrades((d.trades ?? []) as Trade[]); setBeBtLoading(false) })
      .catch(() => { setBeBtLoading(false); setBeBtError(true) })

    // ── Realtime ─────────────────────────────────────────────────────────────
    const beCh = supabase.channel('be-live-dash')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'be_signals' }, p => {
        if (p.eventType === 'INSERT') setBeSignals(prev => [...prev, p.new as BeSignal])
        else if (p.eventType === 'UPDATE') setBeSignals(prev => prev.map(s => s.id === (p.new as BeSignal).id ? p.new as BeSignal : s))
      }).subscribe()

    const amdCh = supabase.channel('amd-live-dash')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'amd_signals' }, p => {
        if (p.eventType === 'INSERT') setAmdSignals(prev => [...prev, p.new as AmdSignal])
        else if (p.eventType === 'UPDATE') setAmdSignals(prev => prev.map(s => s.id === (p.new as AmdSignal).id ? p.new as AmdSignal : s))
      }).subscribe()

    return () => { supabase.removeChannel(beCh); supabase.removeChannel(amdCh) }
  }, [])

  const rbfLive = fromRbfTrades(rbfTrades)
  const amdLive = fromLiveTrades(
    amdSignals.map(s => ({ result_r: s.result_r, session: s.session, symbol: s.symbol, direction: s.direction, exit_reason: s.exit_reason, timestamp_ms: s.timestamp_ms })),
    ACCOUNT, RISK_USD,
  )
  const beLive = fromLiveTrades(
    beSignals.map(s => ({ result_r: s.result_r, session: s.session, symbol: s.symbol, direction: 'Short', exit_reason: s.exit_reason, timestamp_ms: s.timestamp_ms })),
    ACCOUNT, RISK_USD,
  )

  // BT: RBF y BE desde Python API (Trade[] → StratTrade[])
  // AMD: sin backtest histórico propio → usa live cerrados
  const rbfBt = fromRbfTrades(rbfBtTrades)
  const amdBt = amdLive.filter(t => !t.isOpen)
  const beBt  = fromRbfTrades(beBtTrades)

  if (loading) return (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text2)', fontSize: 11 }}>cargando…</div>
  )

  return (
    <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', overflow: 'hidden', minHeight: 0 }}>
      <StrategyCol live={rbfLive} bt={rbfBt} btLoading={rbfBtLoading} btError={rbfBtError}
        meta={RBF_META} capital={ACCOUNT} color="var(--blue)"
        label="RBF" note="Short · London + Overlap · score ≥ 4"
        sessions={['London', 'LondonNyOverlap', 'NewYork']} symbols={['BTC', 'ETH', 'BNB', 'SOL', 'XRP']} />
      <StrategyCol live={amdLive} bt={amdBt} btLoading={false} btError={false}
        meta={AMD_META} capital={ACCOUNT} color="var(--green)"
        label="AMD" note="Long + Short · Acum · Manip · Dist"
        sessions={['London', 'LondonNyOverlap', 'NewYork']} symbols={['BTC', 'ETH', 'BNB', 'SOL']} />
      <StrategyCol live={beLive} bt={beBt} btLoading={beBtLoading} btError={beBtError}
        meta={BE_META} capital={ACCOUNT} color="var(--yellow)"
        label="BE" note="Short · BTC / ETH / BNB / SOL · London + Overlap"
        sessions={['London', 'LondonNyOverlap']} symbols={['BTC', 'ETH', 'BNB', 'SOL']} />
    </div>
  )
}
