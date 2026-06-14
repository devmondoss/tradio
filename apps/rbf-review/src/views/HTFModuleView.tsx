import { useEffect, useState } from 'react'
import { supabase, type HtfTrade } from '../lib/supabase'
import { fmtR } from '../lib/utils'
import BacktestView from './BacktestView'
import TradeChart from '../components/TradeChart'
import type { Trade } from '../lib/types'

function htfToTrade(t: HtfTrade, idx: number, dir: 'Short' | 'Long' = 'Short'): Trade {
  const tsMs = new Date(t.entry_at).getTime()
  return {
    idx,
    id: String(t.id),
    sym: t.symbol,
    dir,
    session: t.session ?? '',
    score: null,
    entry: t.entry,
    stop: t.stop,
    target: t.target,
    exit: t.exit_price ?? 0,
    resultR: t.result_r ?? null,
    pnlUsd: (t.result_r ?? 0) * 10,
    riskUsd: 10,
    stopPct: t.stop_pct,
    equity: 0,
    reason: t.reason ?? '',
    tsMs,
    ts: Math.floor(tsMs / 1000),
    closedAt: t.closed_at ?? null,
    regime: '',
    sessionPhase: '',
    evidence: [t.sig],
    confluenceFlags: [],
    vetoReason: '',
    cvdInRange: null, vr: null, priceVsVwap: null,
    funding: null, cvdSlope: null, obi: null, dz: null,
    rangePct: null, rangeBars: null, rangeTouch: null,
    durationMin: t.duration_bars ?? null,
    isOpen: t.is_open,
  }
}

type SubTab = 'live' | 'backtest'

const COLOR  = 'var(--red)'
const CAPITAL = 500
const RISK_USD = CAPITAL * 0.02

// ── Helpers ───────────────────────────────────────────────────────────────────

function rC(v: number) { return v >= 0 ? 'var(--green)' : 'var(--red)' }

function equity(trades: HtfTrade[]): number {
  let eq = CAPITAL
  for (const t of trades) {
    if (!t.is_open && t.result_r != null) eq += t.result_r * RISK_USD
  }
  return eq
}


// ── Live view ─────────────────────────────────────────────────────────────────

function LiveView({ trades, dir }: { trades: HtfTrade[]; dir: 'Short' | 'Long' }) {
  const [selId, setSelId] = useState<number | null>(trades.length ? trades[trades.length - 1].id : null)

  const sel = trades.find(t => t.id === selId) ?? trades[trades.length - 1] ?? null
  const selTrade: Trade | null = sel ? htfToTrade(sel, trades.indexOf(sel) + 1, dir) : null

  if (!trades.length) return (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text3)', fontSize: 11 }}>
      Sin trades paper aún — el monitor Rust emitirá señales en cuanto detecte un patrón
    </div>
  )

  return (
    <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>

      {/* ── Trade list (izquierda) ───────────────────────────────────── */}
      <div style={{ width: 340, flexShrink: 0, overflow: 'auto', borderRight: '1px solid var(--border)' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)', position: 'sticky', top: 0, background: 'var(--bg2)', zIndex: 1 }}>
              {['Fecha', 'Sym', 'Patrón', 'R'].map(h => (
                <th key={h} style={{ padding: '4px 6px', color: 'var(--text3)', fontWeight: 500, fontSize: 7.5, textTransform: 'uppercase', letterSpacing: .6, textAlign: h === 'R' ? 'right' : 'left' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {[...trades].reverse().map((t, i) => {
              const isOpen  = t.is_open
              const r       = t.result_r
              const isWin   = !isOpen && r != null && r > 0
              const rowCol  = isOpen ? 'var(--blue)' : isWin ? 'var(--green)' : r != null ? 'var(--red)' : 'var(--text3)'
              const isSel   = t.id === selId
              const d       = new Date(t.entry_at)
              const dateFmt = `${d.getUTCDate()} ${d.toLocaleString('en', { month: 'short', timeZone: 'UTC' })} ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`
              return (
                <tr key={t.id}
                  onClick={() => setSelId(t.id)}
                  style={{
                    cursor: 'pointer',
                    background: isSel ? 'var(--bg3)' : i % 2 === 1 ? 'rgba(255,255,255,0.02)' : 'transparent',
                    borderLeft: `2px solid ${isSel ? 'var(--blue)' : rowCol}`,
                    outline: isSel ? '1px solid var(--border2)' : 'none',
                  }}>
                  <td style={{ padding: '4px 8px', color: 'var(--text3)', fontSize: 8.5 }}>{dateFmt}</td>
                  <td style={{ padding: '4px 6px', fontWeight: 700, fontSize: 10 }}>{t.symbol.replace('USDT','')}</td>
                  <td style={{ padding: '4px 6px', color: 'var(--text2)', fontSize: 8.5 }}>{t.sig}</td>
                  <td style={{ padding: '4px 8px', fontWeight: 700, fontSize: 11, textAlign: 'right', color: rowCol }}>
                    {isOpen ? <span style={{ color: 'var(--blue)', fontSize: 9 }}>OPEN</span> : r != null ? fmtR(r) : '—'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* ── Gráfico (derecha) ────────────────────────────────────────── */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minWidth: 0 }}>
        <TradeChart trade={selTrade} />
      </div>

    </div>
  )
}

// ── Main ──────────────────────────────────────────────────────────────────────

interface BtStats { n: number; wins: number; totalR: number; avgR: number; equity: number }

export default function HTFModuleView() {
  const [sub,      setSub]      = useState<SubTab>('live')
  const [trades,   setTrades]   = useState<HtfTrade[]>([])
  const [loading,  setLoading]  = useState(true)
  const [btStats,  setBtStats]  = useState<BtStats | null>(null)

  useEffect(() => {
    const since = Date.now() - 30 * 86400000
    supabase
      .from('htf_trades')
      .select('id,symbol,sig,session,d1_trend,entry,stop,target,stop_pct,is_open,result_r,gross_r,fee_r,reason,exit_price,duration_bars,entry_at,closed_at')
      .gte('entry_at', new Date(since).toISOString())
      .order('entry_at', { ascending: true })
      .then(({ data }) => {
        if (data) setTrades(data as HtfTrade[])
        setLoading(false)
      })

    const ch = supabase.channel('htf-module')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'htf_trades' }, p => {
        if (p.eventType === 'INSERT') setTrades(prev => [...prev, p.new as HtfTrade])
        else if (p.eventType === 'UPDATE') setTrades(prev => prev.map(t => t.id === (p.new as HtfTrade).id ? p.new as HtfTrade : t))
      }).subscribe()

    return () => { supabase.removeChannel(ch) }
  }, [])

  // Deduplicar: si existe un trade cerrado con el mismo (symbol, entry_at),
  // descartar el registro OPEN huérfano (quedó abierto por restart del monitor)
  const dedupedTrades = (() => {
    const closedKeys = new Set(
      trades.filter(t => !t.is_open).map(t => `${t.symbol}|${t.entry_at}`)
    )
    return trades.filter(t => !t.is_open || !closedKeys.has(`${t.symbol}|${t.entry_at}`))
  })()

  const closed = dedupedTrades.filter(t => !t.is_open)
  const openCount = dedupedTrades.filter(t => t.is_open).length

  // Header stats — live cuando en live, backtest cuando en backtest
  const isLive  = sub === 'live'
  const n       = isLive ? closed.length       : (btStats?.n      ?? 0)
  const wins    = isLive ? closed.filter(t => (t.result_r ?? 0) > 0).length : (btStats?.wins   ?? 0)
  const totalR  = isLive ? closed.reduce((s, t) => s + (t.result_r ?? 0), 0) : (btStats?.totalR ?? 0)
  const avgR    = n ? totalR / n : 0
  const wr      = n ? wins / n * 100 : 0
  const eq      = isLive ? equity(trades) : (btStats?.equity ?? CAPITAL)

  const wrCol   = n === 0 ? 'var(--text3)' : wr >= 55 ? 'var(--green)' : wr >= 45 ? 'var(--yellow)' : 'var(--red)'

  return (
    <div className="mod-wrap">

      {/* ── Header ───────────────────────────────────────────────────── */}
      <div className="mod-header" style={{ borderTop: `2px solid ${COLOR}` }}>
        <div className="mod-name-row">
          <span className="mod-name" style={{ color: COLOR }}>HTF</span>
          <span className="mod-desc">
            Shorts · D1 bear · patrones M1 mineados · stop H1 &lt;0.75% · CVD exhaustion
          </span>
        </div>

        <div className="stats-row">
          {[
            { lbl: 'Trades',   val: n > 0 ? String(n)              : '—', col: 'var(--text)'  },
            { lbl: 'Win Rate', val: n > 0 ? `${wr.toFixed(0)}%`    : '—', col: wrCol          },
            { lbl: 'Avg R',    val: n > 0 ? fmtR(avgR)             : '—', col: n > 0 ? rC(avgR)   : 'var(--text3)' },
            { lbl: 'Total R',  val: n > 0 ? fmtR(totalR)           : '—', col: n > 0 ? rC(totalR) : 'var(--text3)' },
            { lbl: 'Equity',   val: `$${eq.toFixed(0)}`,             col: rC(eq - CAPITAL)    },
          ].map((s, i) => (
            <div key={i} className={`stat-col${i < 4 ? ' stat-sep' : ''}`}>
              <span className="stat-lbl">{s.lbl}</span>
              <span className="stat-val" style={{ color: s.col }}>{s.val}</span>
            </div>
          ))}

          <div className="spacer" />

          <div className="subtabs">
            {(['live', 'backtest'] as SubTab[]).map(t => (
              <button key={t} className={`subtab${sub === t ? ' active' : ''}`}
                onClick={() => setSub(t)}
                style={{ borderBottomColor: sub === t ? COLOR : 'transparent' }}>
                {t}
              </button>
            ))}
          </div>

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', paddingBottom: 8, marginLeft: 16 }}>
            {openCount > 0 && (
              <span className="open-badge" style={{ color: COLOR }}>{openCount} OPEN</span>
            )}
            {loading && <span style={{ fontSize: 9, color: 'var(--text3)' }}>cargando…</span>}
          </div>
        </div>
      </div>

      {/* ── Content ──────────────────────────────────────────────────── */}
      <div className="mod-body">
        {sub === 'live' ? (
          loading
            ? <div className="mod-center">Cargando…</div>
            : <LiveView trades={dedupedTrades} dir="Short" />
        ) : (
          <BacktestView strategy="shorts" onStats={setBtStats} />
        )}
      </div>
    </div>
  )
}
