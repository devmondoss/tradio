import { useEffect, useRef, useState } from 'react'
import { supabase, type AmdSignal, type BeSignal } from '../lib/supabase'
import { buildAmdTrades, buildBeTrades, fmtR } from '../lib/utils'
import { ACCOUNT, RISK_USD } from '../lib/types'
import type { Trade } from '../lib/types'
import LiveView from './LiveView'
import StatsView from './StatsView'
import BacktestView from './BacktestView'
import FilterBar, { emptyFilters, applyFilters, type Filters } from '../components/FilterBar'

type SubTab = 'live' | 'stats' | 'backtest'

// ─── Strategy configs ─────────────────────────────────────────────────────────

const CONFIGS = {
  amd: {
    label: 'AMD', color: 'var(--green)',
    desc: 'Accumulation · Manipulation · Distribution · Long + Short',
    table: 'amd_signals' as const,
    btNote: 'Live trades cerrados — sin script Python (AMD no tiene backtest histórico)',
    btJsonKey: null as string | null,
    dynamicBt: false,
  },
  be: {
    label: 'BE', color: 'var(--yellow)',
    desc: 'Buyer Exhaustion · Short · London + Overlap',
    table: 'be_signals' as const,
    btNote: '',
    btJsonKey: null as string | null,
    dynamicBt: true,
  },
}

// ─── Backtest JSON → Trade converter ─────────────────────────────────────────

interface BtRow {
  fecha: string; symbol: string; direction?: string
  session: string; result_r: number; reason?: string
  [k: string]: unknown
}

function buildJsonTrades(rows: BtRow[]): Trade[] {
  let equity = ACCOUNT
  return rows.map((r, i) => {
    const pnl = r.result_r * RISK_USD
    equity = Math.round((equity + pnl) * 100) / 100
    const tsMs = r.fecha ? new Date(r.fecha.replace(' ', 'T') + 'Z').getTime() : i
    return {
      idx: i + 1, id: String(i), sym: r.symbol ?? '',
      dir: (r.direction ?? 'Short') as 'Short' | 'Long',
      session: r.session ?? '', score: null,
      entry: 0, stop: 0, target: 0, exit: 0,
      resultR: r.result_r,
      pnlUsd: Math.round(pnl * 100) / 100,
      riskUsd: RISK_USD, stopPct: 0, equity,
      reason: r.reason ?? '', tsMs, ts: Math.floor(tsMs / 1000),
      closedAt: null, regime: '', sessionPhase: '',
      evidence: [], confluenceFlags: [], vetoReason: '',
      cvdInRange: null, vr: null, priceVsVwap: null,
      funding: null, cvdSlope: null, obi: null, dz: null,
      rangePct: null, rangeBars: null, rangeTouch: null,
      durationMin: null, isOpen: false,
    }
  })
}

// ─── Backtest panel (equity curve + breakdown tables) ─────────────────────────

function EquityCurve({ trades, color }: { trades: Trade[]; color: string }) {
  const closed = trades.filter(t => !t.isOpen)
  const pts    = [ACCOUNT, ...closed.map(t => t.equity)]
  if (pts.length < 2) return <div className="mod-center">Sin datos</div>

  const W = 1000, H = 400
  const min = Math.min(...pts), max = Math.max(...pts)
  const pad = (max - min) * 0.12 || 5
  const lo  = min - pad, hi = max + pad
  const xp  = (i: number) => (i / (pts.length - 1)) * W
  const yp  = (v: number) => H - ((v - lo) / (hi - lo)) * H
  const d   = pts.map((v, i) => `${i === 0 ? 'M' : 'L'}${xp(i).toFixed(1)},${yp(v).toFixed(1)}`).join(' ')
  const area = d + ` L${xp(pts.length - 1).toFixed(1)},${H} L0,${H} Z`
  const final = pts[pts.length - 1]
  const gain  = final - ACCOUNT
  const gSign = gain >= 0 ? '+' : '-'
  const gColor = gain >= 0 ? 'var(--green)' : 'var(--red)'
  let peak = pts[0], maxDd = 0
  for (const v of pts) { peak = Math.max(peak, v); maxDd = Math.max(maxDd, (peak - v) / peak * 100) }
  const gradId = `bt-grad-${color.replace(/[^a-z0-9]/gi, '')}`

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', padding: '16px', minHeight: 0 }}>
      <div style={{ display: 'flex', gap: 20, marginBottom: 10, fontSize: 10, alignItems: 'baseline' }}>
        <span style={{ color: 'var(--text3)' }}>
          ${ACCOUNT} → <strong style={{ color, fontSize: 14 }}>${final.toFixed(2)}</strong>
        </span>
        <span style={{ color: gColor, fontWeight: 700, fontSize: 13 }}>
          {gSign}${Math.abs(gain).toFixed(2)} ({gSign}{Math.abs(gain / ACCOUNT * 100).toFixed(1)}%)
        </span>
        <span style={{ color: 'var(--text3)', marginLeft: 'auto' }}>
          Max DD <span style={{ color: 'var(--red)', fontWeight: 600 }}>-{maxDd.toFixed(1)}%</span>
        </span>
        <span style={{ color: 'var(--text3)' }}>{closed.length} trades</span>
      </div>
      <div style={{ flex: 1, minHeight: 0, borderRadius: 6, overflow: 'hidden', background: 'var(--bg2)', border: '1px solid var(--border)' }}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="100%" preserveAspectRatio="none" style={{ display: 'block' }}>
          <defs>
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor={color} stopOpacity="0.22" />
              <stop offset="100%" stopColor={color} stopOpacity="0.01" />
            </linearGradient>
          </defs>
          <line x1={0} y1={yp(ACCOUNT)} x2={W} y2={yp(ACCOUNT)}
            stroke="var(--border)" strokeWidth={1.5} strokeDasharray="8,6" />
          <path d={area} fill={`url(#${gradId})`} />
          <path d={d} fill="none" stroke={color} strokeWidth={2} />
          {pts.map((v, i) => {
            const isLast = i === pts.length - 1
            if (!isLast && i % Math.max(1, Math.floor(pts.length / 80)) !== 0) return null
            return <circle key={i} cx={xp(i)} cy={yp(v)} r={isLast ? 5 : 2}
              fill={v >= ACCOUNT ? 'var(--green)' : 'var(--red)'}
              stroke={isLast ? 'var(--bg2)' : 'none'} strokeWidth={2} />
          })}
        </svg>
      </div>
    </div>
  )
}

function BkTable({ trades, groupFn, keys, title }: {
  trades: Trade[]; groupFn: (t: Trade) => string; keys: string[]; title: string
}) {
  const closed = trades.filter(t => !t.isOpen)
  const rC = (v: number) => v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--text3)'
  const groups = keys.map(k => {
    const g    = closed.filter(t => groupFn(t) === k)
    const wins = g.filter(t => (t.resultR ?? 0) > 0).length
    const tot  = g.reduce((s, t) => s + (t.resultR ?? 0), 0)
    return { k, n: g.length, wr: g.length ? wins / g.length * 100 : 0, tot, ar: g.length ? tot / g.length : 0 }
  }).filter(g => g.n > 0)
  if (!groups.length) return null

  const allClosed   = closed
  const atot        = allClosed.reduce((s, t) => s + (t.resultR ?? 0), 0)
  const aar         = allClosed.length ? atot / allClosed.length : 0
  const awr         = allClosed.length ? allClosed.filter(t => (t.resultR ?? 0) > 0).length / allClosed.length * 100 : 0

  return (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 8, textTransform: 'uppercase', letterSpacing: 1, color: 'var(--text3)', fontWeight: 700, marginBottom: 6 }}>{title}</div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
        <thead>
          <tr>
            {['', 'n', 'WR', 'Avg R', 'Tot R'].map(h => (
              <th key={h} style={{ padding: '3px 0 4px', color: 'var(--text3)', fontWeight: 500, fontSize: 8, textTransform: 'uppercase', letterSpacing: .6, borderBottom: '1px solid var(--border)', textAlign: h === '' ? 'left' : 'right' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {groups.map(g => (
            <tr key={g.k} style={{ borderBottom: '1px solid var(--border)' }}>
              <td style={{ padding: '5px 0', color: 'var(--text2)' }}>{g.k}</td>
              <td style={{ padding: '5px 0', color: 'var(--text3)', textAlign: 'right' }}>{g.n}</td>
              <td style={{ padding: '5px 0', textAlign: 'right', fontWeight: 600, color: g.wr >= 55 ? 'var(--green)' : g.wr >= 45 ? 'var(--yellow)' : 'var(--red)' }}>{g.wr.toFixed(0)}%</td>
              <td style={{ padding: '5px 0', textAlign: 'right', color: rC(g.ar) }}>{fmtR(g.ar)}</td>
              <td style={{ padding: '5px 0', textAlign: 'right', fontWeight: 600, color: rC(g.tot) }}>{fmtR(g.tot)}</td>
            </tr>
          ))}
          {groups.length > 1 && (
            <tr style={{ borderTop: '1px solid var(--border)' }}>
              <td style={{ padding: '5px 0', color: 'var(--text3)', fontSize: 8.5, fontWeight: 700 }}>TOTAL</td>
              <td style={{ padding: '5px 0', color: 'var(--text3)', textAlign: 'right' }}>{allClosed.length}</td>
              <td style={{ padding: '5px 0', textAlign: 'right', fontWeight: 700, color: awr >= 55 ? 'var(--green)' : awr >= 45 ? 'var(--yellow)' : 'var(--red)' }}>{allClosed.length ? `${awr.toFixed(0)}%` : '—'}</td>
              <td style={{ padding: '5px 0', textAlign: 'right', fontWeight: 700, color: rC(aar) }}>{allClosed.length ? fmtR(aar) : '—'}</td>
              <td style={{ padding: '5px 0', textAlign: 'right', fontWeight: 700, color: rC(atot) }}>{allClosed.length ? fmtR(atot) : '—'}</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

function sesLabel(s: string) {
  if (s === 'LondonNyOverlap') return 'Overlap'
  if (s === 'NewYork') return 'NY'
  return s || '?'
}

function BacktestPanel({ trades, note, color }: { trades: Trade[]; note: string; color: string }) {
  const sessions = [...new Set(trades.map(t => sesLabel(t.session)))].filter(Boolean)
  const symbols  = [...new Set(trades.map(t => t.sym.replace('USDT', '')))].filter(Boolean)
  const dirs     = [...new Set(trades.map(t => t.dir))].filter(Boolean)

  return (
    <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>
      {/* Left: breakdowns */}
      <div style={{ width: 300, flexShrink: 0, borderRight: '1px solid var(--border)', overflow: 'auto', padding: '16px' }}>
        <div style={{ fontSize: 9, color: 'var(--text3)', marginBottom: 14 }}>{note}</div>
        <BkTable trades={trades} groupFn={t => sesLabel(t.session)} keys={sessions} title="Por sesión" />
        <div className="sep-h" style={{ margin: '14px 0' }} />
        <BkTable trades={trades} groupFn={t => t.sym.replace('USDT', '')} keys={symbols} title="Por símbolo" />
        {dirs.length > 1 && (
          <>
            <div className="sep-h" style={{ margin: '14px 0' }} />
            <BkTable trades={trades} groupFn={t => t.dir} keys={dirs} title="Por dirección" />
          </>
        )}
      </div>
      {/* Right: equity curve */}
      <EquityCurve trades={trades} color={color} />
    </div>
  )
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function StrategyModuleView({ strategy }: { strategy: 'amd' | 'be' }) {
  const cfg = CONFIGS[strategy]
  const color = cfg.color

  const [amdSigs, setAmdSigs] = useState<AmdSignal[]>([])
  const [beSigs,  setBeSigs]  = useState<BeSignal[]>([])
  const [loading, setLoading]  = useState(true)
  const [sub,     setSub]      = useState<SubTab>('live')
  const [filters, setFilters]  = useState<Filters>(emptyFilters())
  const sigsRef = useRef<AmdSignal[] | BeSignal[]>([])

  useEffect(() => {
    setLoading(true)
    const since = Date.now() - 90 * 86400000
    const sigFetch = (strategy === 'amd'
      ? supabase.from('amd_signals').select('id,timestamp_ms,symbol,direction,session,entry_price,stop_price,target_price,rr,result_r,exit_reason,closed_at_ms,is_active').gte('timestamp_ms', since).order('timestamp_ms', { ascending: true })
      : supabase.from('be_signals').select('id,timestamp_ms,symbol,session,entry_price,stop_price,target_price,rr,range_pct,range_bars,range_cvd,cvd_flip_ratio,vr_at_breakout,result_r,exit_reason,closed_at,active').gte('timestamp_ms', since).order('timestamp_ms', { ascending: true })
    ) as unknown as Promise<{ data: any[] | null }>

    sigFetch.then((res: { data: any[] | null }) => {
      if (strategy === 'amd') {
        const data = (res.data ?? []) as AmdSignal[]
        sigsRef.current = data; setAmdSigs(data)
      } else {
        const data = (res.data ?? []) as BeSignal[]
        sigsRef.current = data; setBeSigs(data)
      }
      setLoading(false)
    })

    const ch = supabase.channel(`mod-${strategy}`)
      .on('postgres_changes', { event: '*', schema: 'public', table: cfg.table }, p => {
        if (strategy === 'amd') {
          if (p.eventType === 'INSERT')
            setAmdSigs(prev => [...prev, p.new as AmdSignal])
          else if (p.eventType === 'UPDATE')
            setAmdSigs(prev => prev.map(s => s.id === (p.new as AmdSignal).id ? p.new as AmdSignal : s))
        } else {
          if (p.eventType === 'INSERT')
            setBeSigs(prev => [...prev, p.new as BeSignal])
          else if (p.eventType === 'UPDATE')
            setBeSigs(prev => prev.map(s => s.id === (p.new as BeSignal).id ? p.new as BeSignal : s))
        }
      }).subscribe()
    return () => { supabase.removeChannel(ch) }
  }, [strategy])

  const trades: Trade[] = strategy === 'amd' ? buildAmdTrades(amdSigs) : buildBeTrades(beSigs)
  const filtered  = applyFilters(trades, filters)
  const closed    = trades.filter(t => !t.isOpen)
  const n         = closed.length
  const wins      = closed.filter(t => (t.resultR ?? 0) > 0).length
  const wr        = n ? wins / n * 100 : 0
  const totalR    = closed.reduce((s, t) => s + (t.resultR ?? 0), 0)
  const avgR      = n ? totalR / n : 0
  const openCount = trades.filter(t => t.isOpen).length
  const wrCol     = n === 0 ? 'var(--text3)' : wr >= 55 ? 'var(--green)' : wr >= 45 ? 'var(--yellow)' : 'var(--red)'
  const rC        = (v: number) => v >= 0 ? 'var(--green)' : 'var(--red)'

  // AMD backtest = live closed trades; BE backtest = BacktestView dinámico (Python)
  const bkTrades = closed

  return (
    <div className="mod-wrap">

      {/* ── Top bar ─────────────────────────────────────────────────── */}
      <div className="mod-header" style={{ borderTop: `2px solid ${color}` }}>
        {/* Name row */}
        <div className="mod-name-row">
          <span className="mod-name" style={{ color }}>{cfg.label}</span>
          <span className="mod-desc">{cfg.desc}</span>
        </div>

        {/* Stats + sub-tabs + actions */}
        <div className="stats-row">
          {[
            { lbl: 'Trades',   val: n > 0 ? String(n)           : '—', col: 'var(--text)'  },
            { lbl: 'Win Rate', val: n > 0 ? `${wr.toFixed(0)}%` : '—', col: wrCol          },
            { lbl: 'Avg R',    val: n > 0 ? fmtR(avgR)          : '—', col: n > 0 ? rC(avgR)   : 'var(--text3)' },
            { lbl: 'Total R',  val: n > 0 ? fmtR(totalR)        : '—', col: n > 0 ? rC(totalR) : 'var(--text3)' },
          ].map((s, i) => (
            <div key={i} className={`stat-col${i < 3 ? ' stat-sep' : ''}`}>
              <span className="stat-lbl">{s.lbl}</span>
              <span className="stat-val" style={{ color: s.col }}>{s.val}</span>
            </div>
          ))}

          <div className="spacer" />

          {/* Sub-tabs */}
          <div className="subtabs">
            {(['live', 'stats', 'backtest'] as SubTab[]).map(t => (
              <button key={t} className={`subtab${sub === t ? ' active' : ''}`}
                onClick={() => setSub(t)}
                style={{ borderBottomColor: sub === t ? color : 'transparent' }}>
                {t}
              </button>
            ))}
          </div>

          {/* Open + FilterBar */}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', paddingBottom: 8, marginLeft: 16 }}>
            {openCount > 0 && <span className="open-badge" style={{ color }}>{openCount} OPEN</span>}
            {loading && <span style={{ fontSize: 9, color: 'var(--text3)' }}>cargando…</span>}
            {!loading && trades.length > 0 && sub !== 'backtest' && (
              <FilterBar trades={trades} filters={filters} filtered={filtered} onChange={setFilters} />
            )}
          </div>
        </div>
      </div>

      {/* ── Content ─────────────────────────────────────────────────── */}
      <div className="mod-body">
        {sub === 'backtest' && cfg.dynamicBt ? (
          <BacktestView strategy="be" />
        ) : loading ? (
          <div className="mod-center">Cargando…</div>
        ) : sub === 'backtest' ? (
          <BacktestPanel trades={bkTrades} note={cfg.btNote} color={color} />
        ) : trades.length === 0 ? (
          <div className="mod-center">Sin trades aún</div>
        ) : filtered.length === 0 ? (
          <div className="mod-center">Sin trades con esos filtros</div>
        ) : sub === 'live' ? (
          <LiveView trades={filtered} />
        ) : (
          <StatsView trades={filtered} />
        )}
      </div>
    </div>
  )
}
