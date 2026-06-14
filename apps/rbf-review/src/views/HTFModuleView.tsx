import { useEffect, useState } from 'react'
import { supabase, type HtfTrade } from '../lib/supabase'
import { fmtR, sesLabel } from '../lib/utils'
import BacktestView from './BacktestView'

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

// ── Trade row ─────────────────────────────────────────────────────────────────

function TradeRow({ t, idx }: { t: HtfTrade; idx: number }) {
  const isOpen  = t.is_open
  const r       = t.result_r
  const gross   = t.gross_r
  const isWin   = !isOpen && r != null && r > 0
  const rowCol  = isOpen ? 'var(--blue)' : isWin ? 'var(--green)' : r != null ? 'var(--red)' : 'var(--text3)'

  const entryDate = new Date(t.entry_at)
  const dateFmt   = `${entryDate.getUTCDate()} ${entryDate.toLocaleString('en', { month: 'short', timeZone: 'UTC' })} ${String(entryDate.getUTCHours()).padStart(2,'0')}:${String(entryDate.getUTCMinutes()).padStart(2,'0')}`

  const sym = t.symbol.replace('USDT', '')
  const ses = sesLabel(t.session)

  return (
    <tr style={{ background: idx % 2 === 1 ? 'var(--bg3)' : 'transparent', borderLeft: `2px solid ${rowCol}` }}>
      <td style={{ padding: '4px 8px', color: 'var(--text3)', fontSize: 9 }}>{dateFmt}</td>
      <td style={{ padding: '4px 6px', fontWeight: 700, fontSize: 10 }}>{sym}</td>
      <td style={{ padding: '4px 6px', color: 'var(--text2)', fontSize: 9 }}>{t.sig}</td>
      <td style={{ padding: '4px 6px', color: 'var(--text3)', fontSize: 9 }}>{ses}</td>
      <td style={{ padding: '4px 6px', color: 'var(--text2)', fontSize: 9, textAlign: 'right' }}>{t.entry.toFixed(2)}</td>
      <td style={{ padding: '4px 6px', color: 'var(--text3)', fontSize: 9, textAlign: 'right' }}>{t.stop_pct.toFixed(2)}%</td>
      <td style={{ padding: '4px 6px', color: 'var(--text2)', fontSize: 9, textAlign: 'right' }}>
        {isOpen
          ? <span style={{ color: 'var(--blue)', fontWeight: 700 }}>OPEN</span>
          : t.reason}
      </td>
      <td style={{ padding: '4px 8px', fontWeight: 700, fontSize: 11, textAlign: 'right', color: rowCol }}>
        {isOpen ? '—' : r != null ? fmtR(r) : '—'}
      </td>
      {/* gross_r para comparar con backtest */}
      <td style={{ padding: '4px 8px', fontSize: 9, textAlign: 'right', color: 'var(--text3)' }}>
        {gross != null ? fmtR(gross) : '—'}
      </td>
    </tr>
  )
}

// ── Live view ─────────────────────────────────────────────────────────────────

function LiveView({ trades }: { trades: HtfTrade[] }) {
  if (!trades.length) return (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text3)', fontSize: 11 }}>
      Sin trades paper aún — el monitor Rust emitirá señales en cuanto detecte un patrón
    </div>
  )

  const closed = trades.filter(t => !t.is_open)

  // Breakdown por símbolo
  const syms = ['BTC', 'ETH', 'SOL']
  const bySymRows = syms.map(s => {
    const g    = closed.filter(t => t.symbol.startsWith(s))
    const wins = g.filter(t => (t.result_r ?? 0) > 0).length
    const tot  = g.reduce((a, t) => a + (t.result_r ?? 0), 0)
    return { s, n: g.length, wr: g.length ? wins / g.length * 100 : 0, tot, ar: g.length ? tot / g.length : 0 }
  }).filter(r => r.n > 0)

  // Breakdown por patrón (sig)
  const sigMap = new Map<string, { wins: number; n: number; tot: number }>()
  for (const t of closed) {
    const e = sigMap.get(t.sig) ?? { wins: 0, n: 0, tot: 0 }
    e.n++
    e.tot += t.result_r ?? 0
    if ((t.result_r ?? 0) > 0) e.wins++
    sigMap.set(t.sig, e)
  }
  const sigRows = [...sigMap.entries()]
    .map(([sig, e]) => ({ sig, ...e, wr: e.n ? e.wins / e.n * 100 : 0, ar: e.n ? e.tot / e.n : 0 }))
    .sort((a, b) => b.tot - a.tot)

  return (
    <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>

      {/* ── Trade list ───────────────────────────────────────────────── */}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border)' }}>
            {['Fecha UTC', 'Sym', 'Patrón', 'Sesión', 'Entry', 'Stop%', 'Razón', 'Net R', 'Gross R'].map(h => (
              <th key={h} style={{ padding: '4px 6px', color: 'var(--text3)', fontWeight: 500, fontSize: 7.5, textTransform: 'uppercase', letterSpacing: .6, textAlign: h === 'Fecha UTC' || h === 'Sym' || h === 'Patrón' || h === 'Sesión' ? 'left' : 'right' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {[...trades].reverse().map((t, i) => <TradeRow key={t.id} t={t} idx={i} />)}
        </tbody>
      </table>

      {/* ── Breakdowns ───────────────────────────────────────────────── */}
      {closed.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 0, borderTop: '1px solid var(--border)', marginTop: 4 }}>

          {/* Por símbolo */}
          <div style={{ padding: '8px 10px', borderRight: '1px solid var(--border)' }}>
            <div style={{ fontSize: 7.5, textTransform: 'uppercase', letterSpacing: .8, color: 'var(--text3)', marginBottom: 6, fontWeight: 600 }}>Por símbolo</div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 9 }}>
              <thead><tr>{['', 'n', 'WR', 'Avg R', 'Tot R'].map(h => <th key={h} style={{ padding: '2px 4px', color: 'var(--text3)', fontWeight: 500, fontSize: 7.5, textAlign: h === '' ? 'left' : 'right' }}>{h}</th>)}</tr></thead>
              <tbody>
                {bySymRows.map(r => (
                  <tr key={r.s}>
                    <td style={{ padding: '3px 4px', fontWeight: 700 }}>{r.s}</td>
                    <td style={{ padding: '3px 4px', textAlign: 'right', color: 'var(--text3)' }}>{r.n}</td>
                    <td style={{ padding: '3px 4px', textAlign: 'right', color: r.wr >= 55 ? 'var(--green)' : r.wr >= 45 ? 'var(--yellow)' : 'var(--red)', fontWeight: 600 }}>{r.wr.toFixed(0)}%</td>
                    <td style={{ padding: '3px 4px', textAlign: 'right', color: rC(r.ar) }}>{fmtR(r.ar)}</td>
                    <td style={{ padding: '3px 4px', textAlign: 'right', color: rC(r.tot), fontWeight: 600 }}>{fmtR(r.tot)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Por patrón */}
          <div style={{ padding: '8px 10px' }}>
            <div style={{ fontSize: 7.5, textTransform: 'uppercase', letterSpacing: .8, color: 'var(--text3)', marginBottom: 6, fontWeight: 600 }}>Por patrón</div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 9 }}>
              <thead><tr>{['', 'n', 'WR', 'Avg R', 'Tot R'].map(h => <th key={h} style={{ padding: '2px 4px', color: 'var(--text3)', fontWeight: 500, fontSize: 7.5, textAlign: h === '' ? 'left' : 'right' }}>{h}</th>)}</tr></thead>
              <tbody>
                {sigRows.map(r => (
                  <tr key={r.sig}>
                    <td style={{ padding: '3px 4px', color: 'var(--text2)', fontSize: 8.5 }}>{r.sig}</td>
                    <td style={{ padding: '3px 4px', textAlign: 'right', color: 'var(--text3)' }}>{r.n}</td>
                    <td style={{ padding: '3px 4px', textAlign: 'right', color: r.wr >= 55 ? 'var(--green)' : r.wr >= 45 ? 'var(--yellow)' : 'var(--red)', fontWeight: 600 }}>{r.wr.toFixed(0)}%</td>
                    <td style={{ padding: '3px 4px', textAlign: 'right', color: rC(r.ar) }}>{fmtR(r.ar)}</td>
                    <td style={{ padding: '3px 4px', textAlign: 'right', color: rC(r.tot), fontWeight: 600 }}>{fmtR(r.tot)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
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

  const closed = trades.filter(t => !t.is_open)
  const openCount = trades.filter(t => t.is_open).length

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
            : <LiveView trades={trades} />
        ) : (
          <BacktestView strategy="shorts" onStats={setBtStats} />
        )}
      </div>
    </div>
  )
}
