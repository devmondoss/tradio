import { useEffect, useState } from 'react'
import { supabase, type MtfTrade, type MtfSpotTrade } from '../../lib/supabase'
import { fmtR } from '../../lib/utils'
import LocalResultsView from '../../views/LocalResultsView'
import TradesView from '../../views/TradesView'
import type { Trade } from '../../lib/types'

type LiveMtfTrade = MtfTrade | MtfSpotTrade
type LiveSource = 'spot' | 'futures'

function isFuturesTrade(t: LiveMtfTrade): t is MtfTrade {
  return !('market_type' in t)
}

function mtfToTrade(t: LiveMtfTrade, idx: number): Trade {
  const tsMs = new Date(t.entry_at).getTime()
  const level = 'level' in t ? (t.level ?? '') : ''
  return {
    idx,
    id: String(t.id),
    sym: t.symbol,
    dir: t.direction ?? 'Short',
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
    regime: isFuturesTrade(t) ? (t.d1_trend ?? '') : `${t.venue} ${t.market_type}`,
    sessionPhase: '',
    evidence: [t.sig],
    confluenceFlags: level ? [level] : [],
    vetoReason: '',
    cvdInRange: null,
    vr: null,
    priceVsVwap: null,
    funding: null,
    cvdSlope: t.cvd_slope_entry ?? null,
    obi: t.obi_entry ?? null,
    dz: isFuturesTrade(t) ? (t.dz_score ?? null) : null,
    rangePct: null,
    rangeBars: null,
    rangeTouch: null,
    durationMin: t.duration_bars ?? null,
    isOpen: t.is_open,
    isLive: ('is_live' in t && t.is_live) ? true : false,
    liveEntryOrderId: 'live_entry_order_id' in t ? t.live_entry_order_id : null,
    liveFillPrice:    'live_fill_price' in t ? t.live_fill_price : null,
    liveFilledQty:    'live_filled_qty' in t ? t.live_filled_qty : null,
    liveTpOrderId:    'live_tp_order_id' in t ? t.live_tp_order_id : null,
    liveSlOrderId:    'live_sl_order_id' in t ? t.live_sl_order_id : null,
  }
}

type SubTab  = 'live' | 'backtest'
type BtMode = 'Shorts' | 'Longs'

const COLOR   = 'var(--red)'
const CAPITAL = 500
const RISK_USD = CAPITAL * 0.02

function rC(v: number) { return v >= 0 ? 'var(--green)' : 'var(--red)' }

function equity(trades: LiveMtfTrade[]): number {
  let eq = CAPITAL
  for (const t of trades) {
    if (!t.is_open && t.result_r != null) eq += t.result_r * RISK_USD
  }
  return eq
}

interface BtStats { n: number; wins: number; totalR: number; avgR: number; equity: number }

export default function MTFModule() {
  const [sub,        setSub]        = useState<SubTab>(
    () => (localStorage.getItem('tradio-mtf-sub') as SubTab) ?? 'live'
  )
  const [liveSource, setLiveSource] = useState<LiveSource>(
    () => (localStorage.getItem('tradio-mtf-src') as LiveSource) ?? 'spot'
  )
  const [btMode,     setBtMode]     = useState<BtMode>(
    () => (localStorage.getItem('tradio-mtf-bt') as BtMode) ?? 'Shorts'
  )

  function setSub2(v: SubTab)            { setSub(v);        localStorage.setItem('tradio-mtf-sub', v) }
  function setLiveSource2(v: LiveSource) { setLiveSource(v); localStorage.setItem('tradio-mtf-src', v) }
  function setBtMode2(v: BtMode)         { setBtMode(v);     localStorage.setItem('tradio-mtf-bt',  v) }
  const [trades,   setTrades]   = useState<LiveMtfTrade[]>([])
  const [loading,  setLoading]  = useState(true)
  const [btStats,  setBtStats]  = useState<BtStats | null>(null)
  const [btTrades, setBtTrades] = useState<Trade[]>([])

  useEffect(() => {
    let cancelled = false
    const since = Date.now() - 30 * 86400000
    const table = liveSource === 'spot' ? 'mtf_spot_trades' : 'mtf_trades'
    const select = liveSource === 'spot'
      ? 'id,symbol,venue,market_type,strategy,sig,session,direction,level,entry,stop,target,stop_pct,is_open,result_r,gross_r,fee_r,reason,exit_price,duration_bars,mfe_r,mae_r,entry_at,closed_at,wick_pct,obi_entry,delta_entry,cvd_slope_entry'
      : 'id,symbol,sig,session,direction,d1_trend,entry,stop,target,stop_pct,is_open,result_r,gross_r,fee_r,reason,exit_price,duration_bars,entry_at,closed_at,obi_entry,cvd_slope_entry,dz_score,stacked_imb,equal_low'
    const db = supabase as unknown as { from: (name: string) => any }

    async function refresh(showLoading = false) {
      if (showLoading) setLoading(true)
      const { data } = await db
        .from(table)
        .select(select)
        .gte('entry_at', new Date(since).toISOString())
        .order('entry_at', { ascending: true })
      if (cancelled) return
      setTrades((data ?? []) as LiveMtfTrade[])
      setLoading(false)
    }

    refresh(true)
    const refreshTimer = window.setInterval(() => refresh(false), 10_000)

    const ch = supabase.channel(`htf-module-${liveSource}`)
      .on('postgres_changes', { event: '*', schema: 'public', table }, p => {
        const row = p.new as LiveMtfTrade
        if (p.eventType === 'INSERT') setTrades(prev => [...prev, row])
        else if (p.eventType === 'UPDATE') setTrades(prev => prev.map(t => String(t.id) === String(row.id) ? row : t))
      }).subscribe()

    return () => {
      cancelled = true
      window.clearInterval(refreshTimer)
      supabase.removeChannel(ch)
    }
  }, [liveSource])

  const MAX_OPEN_MS = 20 * 60 * 60 * 1000
  const now = Date.now()
  const visibleTrades = trades.filter(t =>
    !t.is_open || (now - new Date(t.entry_at).getTime()) < MAX_OPEN_MS
  )

  const converted = visibleTrades.map((t, i) => mtfToTrade(t, i + 1))

  const closed  = visibleTrades.filter(t => !t.is_open)
  const openCnt = visibleTrades.filter(t => t.is_open).length

  const isLive    = sub === 'live'
  const srcShorts = isLive
    ? closed.filter(t => (t.direction ?? 'Short') === 'Short')
    : btTrades.filter(t => t.dir === 'Short' && !t.isOpen)
  const srcLongs  = isLive
    ? closed.filter(t => t.direction === 'Long')
    : btTrades.filter(t => t.dir === 'Long' && !t.isOpen)

  function calcStats(src: { result_r?: number | null; resultR?: number | null }[]) {
    const rs  = src.map(t => ('result_r' in t ? (t.result_r ?? 0) : (t.resultR ?? 0)))
    const n   = rs.length
    const wins = rs.filter(r => r > 0).length
    const tot = rs.reduce((a, b) => a + b, 0)
    return { n, wins, wr: n ? wins / n * 100 : 0, avgR: n ? tot / n : 0, totalR: tot }
  }

  const sS   = calcStats(srcShorts)
  const sL   = calcStats(srcLongs)
  const eq   = isLive ? equity(trades) : (btStats?.equity ?? CAPITAL)
  const wrC  = (wr: number, n: number) =>
    n === 0 ? 'var(--text3)' : wr >= 55 ? 'var(--green)' : wr >= 45 ? 'var(--yellow)' : 'var(--red)'

  return (
    <div className="mod-wrap">

      <div className="mod-header" style={{ borderTop: `2px solid ${COLOR}` }}>
        <div className="mod-name-row">
          <span className="mod-name" style={{ color: COLOR }}>HTF</span>
          <span className="mod-desc">
            {sub === 'live' && liveSource === 'spot'
              ? 'Bybit spot paper - MTF Spot Shorts v4 + Longs v1 - mtf_spot_trades'
              : 'Shorts+Longs - D1/H4 filter - patrones M1 mineados - stop H1 <0.75% - CVD exhaustion'}
          </span>
        </div>

        <div className="stats-row">

          <div className="stat-col" style={{ minWidth: 36 }}>
            <span className="stat-lbl" style={{ color: 'var(--red)' }}>Short</span>
            <span className="stat-val" style={{ color: 'var(--text3)' }}>{sS.n || '—'}</span>
          </div>
          <div className="stat-col stat-sep">
            <span className="stat-lbl">WR</span>
            <span className="stat-val" style={{ color: wrC(sS.wr, sS.n) }}>{sS.n ? `${sS.wr.toFixed(0)}%` : '—'}</span>
          </div>
          <div className="stat-col stat-sep">
            <span className="stat-lbl">Avg R</span>
            <span className="stat-val" style={{ color: sS.n ? rC(sS.avgR) : 'var(--text3)' }}>{sS.n ? fmtR(sS.avgR) : '—'}</span>
          </div>
          <div className="stat-col stat-sep" style={{ marginRight: 10 }}>
            <span className="stat-lbl">Total</span>
            <span className="stat-val" style={{ color: sS.n ? rC(sS.totalR) : 'var(--text3)' }}>{sS.n ? fmtR(sS.totalR) : '—'}</span>
          </div>

          <div style={{ width: 1, background: 'var(--border2)', alignSelf: 'stretch', margin: '4px 10px 4px 0' }} />

          <div className="stat-col" style={{ minWidth: 36 }}>
            <span className="stat-lbl" style={{ color: 'var(--green)' }}>Long</span>
            <span className="stat-val" style={{ color: 'var(--text3)' }}>{sL.n || '—'}</span>
          </div>
          <div className="stat-col stat-sep">
            <span className="stat-lbl">WR</span>
            <span className="stat-val" style={{ color: wrC(sL.wr, sL.n) }}>{sL.n ? `${sL.wr.toFixed(0)}%` : '—'}</span>
          </div>
          <div className="stat-col stat-sep">
            <span className="stat-lbl">Avg R</span>
            <span className="stat-val" style={{ color: sL.n ? rC(sL.avgR) : 'var(--text3)' }}>{sL.n ? fmtR(sL.avgR) : '—'}</span>
          </div>
          <div className="stat-col stat-sep" style={{ marginRight: 10 }}>
            <span className="stat-lbl">Total</span>
            <span className="stat-val" style={{ color: sL.n ? rC(sL.totalR) : 'var(--text3)' }}>{sL.n ? fmtR(sL.totalR) : '—'}</span>
          </div>

          <div style={{ width: 1, background: 'var(--border2)', alignSelf: 'stretch', margin: '4px 10px 4px 0' }} />

          <div className="stat-col">
            <span className="stat-lbl">Equity</span>
            <span className="stat-val" style={{ color: rC(eq - CAPITAL) }}>${eq.toFixed(0)}</span>
          </div>

          <div className="spacer" />

          <div className="subtabs">
            {(['live', 'backtest'] as SubTab[]).map(t => (
              <button key={t} className={`subtab${sub === t ? ' active' : ''}`}
                onClick={() => setSub2(t)}
                style={{ borderBottomColor: sub === t ? COLOR : 'transparent' }}>
                {t}
              </button>
            ))}
          </div>

          {sub === 'live' && (
            <div className="subtabs" style={{ marginLeft: 10 }}>
              {(['spot', 'futures'] as LiveSource[]).map(t => (
                <button key={t} className={`subtab${liveSource === t ? ' active' : ''}`}
                  onClick={() => setLiveSource2(t)}
                  style={{ borderBottomColor: liveSource === t ? (t === 'spot' ? 'var(--green)' : COLOR) : 'transparent' }}>
                  {t === 'spot' ? 'Spot paper' : 'Futures'}
                </button>
              ))}
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', paddingBottom: 8, marginLeft: 16 }}>
            {openCnt > 0 && <span className="open-badge" style={{ color: COLOR }}>{openCnt} OPEN</span>}
            {loading   && <span style={{ fontSize: 9, color: 'var(--text3)' }}>cargando…</span>}
          </div>
        </div>
      </div>

      <div className="mod-body">
        {sub === 'live' ? (
          loading
            ? <div className="mod-center">Cargando…</div>
            : <TradesView trades={converted} />
        ) : (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
            <div style={{
              display: 'flex', gap: 4, padding: '4px 8px', flexShrink: 0,
              borderBottom: '1px solid var(--border)', background: 'var(--bg2)',
              alignItems: 'center',
            }}>
              <span style={{ fontSize: 9, color: 'var(--text3)', marginRight: 4 }}>Modo</span>
              {(['Shorts', 'Longs'] as BtMode[]).map(s => (
                <button key={s}
                  onClick={() => setBtMode2(s)}
                  style={{
                    padding: '2px 14px', borderRadius: 3, fontSize: 10,
                    cursor: 'pointer', fontFamily: 'inherit',
                    background: btMode === s ? (s === 'Longs' ? 'var(--green)' : COLOR) : 'var(--bg3)',
                    border: `1px solid ${btMode === s ? (s === 'Longs' ? 'var(--green)' : COLOR) : 'var(--border2)'}`,
                    color: btMode === s ? '#fff' : 'var(--text3)',
                    fontWeight: btMode === s ? 700 : 400,
                  }}>
                  {s}
                </button>
              ))}
              <span style={{ fontSize: 9, color: 'var(--text3)', marginLeft: 8 }}>
                Bybit spot parquet · MTF Spot {btMode === 'Longs' ? 'Longs v2' : 'Shorts v6'} · resultados en exports/
              </span>
            </div>
            <LocalResultsView
              key={btMode}
              strategy={btMode === 'Longs' ? 'mtf_spot_longs_btc' : 'mtf_local_btc'}
              onStats={setBtStats}
              onTrades={setBtTrades}
            />
          </div>
        )}
      </div>

    </div>
  )
}
