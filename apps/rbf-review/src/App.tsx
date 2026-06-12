import { useEffect, useRef, useState } from 'react'
import { supabase, type RbfSignal } from './lib/supabase'
import { buildTrades } from './lib/utils'
import type { Trade } from './lib/types'
import DashboardView from './views/DashboardView'
import RBFModuleView from './views/RBFModuleView'
import StrategyModuleView from './views/StrategyModuleView'

type Tab = 'dashboard' | 'rbf' | 'amd' | 'be'

const SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT']

const TABS: { id: Tab; label: string; color: string }[] = [
  { id: 'dashboard', label: 'Overview', color: 'var(--text)' },
  { id: 'rbf',       label: 'RBF',      color: 'var(--blue)'   },
  { id: 'amd',       label: 'AMD',      color: 'var(--green)'  },
  { id: 'be',        label: 'BE',       color: 'var(--yellow)' },
]

export default function App() {
  const [tab,     setTab]     = useState<Tab>('dashboard')
  const [signals, setSignals] = useState<RbfSignal[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)
  const signalsRef = useRef<RbfSignal[]>([])

  useEffect(() => {
    loadAll()
    const ch = supabase
      .channel('rbf-live')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'rbf_signals' }, payload => {
        if (payload.eventType === 'INSERT') {
          const sig = payload.new as RbfSignal
          signalsRef.current = [...signalsRef.current, sig]
          setSignals([...signalsRef.current])
        } else if (payload.eventType === 'UPDATE') {
          const updated = signalsRef.current.map(s => s.id === (payload.new as RbfSignal).id ? payload.new as RbfSignal : s)
          signalsRef.current = updated
          setSignals([...updated])
        }
      })
      .subscribe()
    return () => { supabase.removeChannel(ch) }
  }, [])

  async function loadAll() {
    setLoading(true); setError(null)
    try {
      const { data, error: err } = await supabase
        .from('rbf_signals')
        .select('id,timestamp_ms,symbol,direction,session,session_phase,macro_regime,entry_price,stop_price,target_price,exit_price,result_r,exit_reason,closed_at,vr_at_breakout,cvd_in_range,range_pct,range_bars,confluence_score,confluence_flags,evidence,cvd_slope_at_entry,obi_at_entry,dz_at_entry,price_vs_vwap_pct,funding_at_entry,range_touch_count,veto_reason')
        .in('symbol', SYMBOLS)
        .gte('timestamp_ms', Date.now() - 90 * 86400000)
        .order('timestamp_ms', { ascending: true })
      if (err) throw err
      signalsRef.current = data as RbfSignal[]
      setSignals(data as RbfSignal[])
    } catch (e) { setError(String(e)) }
    finally     { setLoading(false) }
  }

  const trades: Trade[] = buildTrades(signals)

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

      {/* ── Header ──────────────────────────────────────────────────── */}
      <div style={{
        display: 'flex', alignItems: 'stretch', height: 36, flexShrink: 0,
        background: 'var(--bg2)', borderBottom: '1px solid var(--border)', padding: '0 12px',
      }}>
        <span style={{ fontWeight: 800, fontSize: 12, color: 'var(--text)', alignSelf: 'center', letterSpacing: 1.5, marginRight: 16 }}>FS</span>

        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} style={{
            padding: '0 14px', height: '100%', background: 'none', border: 'none',
            borderBottom: `2px solid ${tab === t.id ? t.color : 'transparent'}`,
            color: tab === t.id ? 'var(--text)' : 'var(--text2)',
            fontWeight: tab === t.id ? 700 : 400, fontSize: 11,
            cursor: 'pointer', fontFamily: 'inherit',
          }}>{t.label}</button>
        ))}
      </div>

      {/* ── Content ─────────────────────────────────────────────────── */}
      <div style={{ flex: 1, overflow: 'hidden', display: 'flex', minHeight: 0 }}>
        {tab === 'dashboard' && <DashboardView rbfTrades={trades} />}
        {tab === 'rbf'       && <RBFModuleView trades={trades} loading={loading} error={error} onReload={loadAll} />}
        {tab === 'amd'       && <StrategyModuleView strategy="amd" />}
        {tab === 'be'        && <StrategyModuleView strategy="be"  />}
      </div>
    </div>
  )
}
