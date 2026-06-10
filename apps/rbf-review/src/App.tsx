import { useEffect, useRef, useState } from 'react'
import { supabase, type RbfSignal } from './lib/supabase'
import { buildTrades } from './lib/utils'
import type { Trade } from './lib/types'
import LiveView from './views/LiveView'
import StatsView from './views/StatsView'
import BacktestView from './views/BacktestView'
import FilterBar, { emptyFilters, applyFilters, type Filters } from './components/FilterBar'

type Tab = 'live' | 'stats' | 'backtest'

const SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT']

function TabBtn({ label, active, onClick, badge }: {
  label: string; active: boolean; onClick: () => void; badge?: number
}) {
  return (
    <button onClick={onClick} style={{
      padding: '0 14px', height: '100%',
      background: 'none', border: 'none',
      borderBottom: `2px solid ${active ? 'var(--blue)' : 'transparent'}`,
      color: active ? 'var(--text)' : 'var(--text2)',
      cursor: 'pointer', fontSize: 11,
      fontWeight: active ? 700 : 400,
      fontFamily: 'inherit',
    }}>
      {label}
      {badge != null && badge > 0 && (
        <span style={{
          marginLeft: 5, background: 'var(--red)', color: '#fff',
          borderRadius: 8, padding: '1px 5px', fontSize: 8, fontWeight: 700,
          verticalAlign: 'middle',
        }}>{badge}</span>
      )}
    </button>
  )
}

export default function App() {
  const [tab,      setTab]      = useState<Tab>('live')
  const [signals,  setSignals]  = useState<RbfSignal[]>([])
  const [loading,  setLoading]  = useState(true)
  const [error,    setError]    = useState<string | null>(null)
  const [newCount, setNewCount] = useState(0)
  const [filters,  setFilters]  = useState<Filters>(emptyFilters())
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
          setNewCount(n => n + 1)
        } else if (payload.eventType === 'UPDATE') {
          const sig     = payload.new as RbfSignal
          const updated = signalsRef.current.map(s => s.id === sig.id ? sig : s)
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
        .select('*')
        .in('symbol', SYMBOLS)
        .order('timestamp_ms', { ascending: true })
      if (err) throw err
      signalsRef.current = data as RbfSignal[]
      setSignals(data as RbfSignal[])
    } catch (e) { setError(String(e)) }
    finally     { setLoading(false) }
  }

  const trades:   Trade[] = buildTrades(signals)
  const filtered: Trade[] = applyFilters(trades, filters)
  const openCount = trades.filter(t => t.isOpen).length
  const showFilters = tab !== 'backtest' && !loading && trades.length > 0

  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
      {/* ── header ─────────────────────────────────────────────────────── */}
      <div style={{
        display: 'flex', alignItems: 'stretch',
        borderBottom: '1px solid var(--border)',
        background: 'var(--bg2)',
        padding: '0 8px',
        height: 36, flexShrink: 0,
        position: 'relative',
      }}>
        <span style={{ fontWeight: 700, fontSize: 11, color: 'var(--text)', marginRight: 8, alignSelf: 'center', letterSpacing: 1 }}>
          RBF
        </span>

        <TabBtn label="Live"     active={tab === 'live'}     onClick={() => { setTab('live'); setNewCount(0) }}
          badge={newCount > 0 && tab !== 'live' ? newCount : undefined} />
        <TabBtn label="Stats"    active={tab === 'stats'}    onClick={() => setTab('stats')} />
        <TabBtn label="Backtest" active={tab === 'backtest'} onClick={() => setTab('backtest')} />

        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          {loading && <span style={{ fontSize: 9, color: 'var(--text3)' }}>cargando…</span>}
          {error   && <span style={{ fontSize: 9, color: 'var(--red)' }}>error</span>}
          {openCount > 0 && <span style={{ fontSize: 10, color: 'var(--blue)', fontWeight: 700 }}>{openCount} OPEN</span>}
          {!loading && <span style={{ fontSize: 9, color: 'var(--text3)' }}>{trades.length} trades</span>}

          {showFilters && (
            <FilterBar
              trades={trades}
              filters={filters}
              filtered={filtered}
              onChange={setFilters}
            />
          )}

          <button onClick={loadAll} style={{
            background: 'transparent', border: 'none',
            color: 'var(--text3)', fontSize: 13,
            padding: '2px 4px', cursor: 'pointer', fontFamily: 'inherit',
            lineHeight: 1,
          }}>↺</button>
        </div>
      </div>

      {/* ── content ────────────────────────────────────────────────────── */}
      <div style={{ flex: 1, overflow: 'hidden', display: 'flex', minHeight: 0 }}>
        {tab === 'live' && (
          loading
            ? <Center>Cargando…</Center>
            : error
              ? <Center color="var(--red)">Error: {error}</Center>
              : trades.length === 0
                ? <Center>Sin trades aun</Center>
                : filtered.length === 0
                  ? <Center>Sin trades con esos filtros</Center>
                  : <LiveView trades={filtered} />
        )}
        {tab === 'stats' && (
          loading
            ? <Center>Cargando…</Center>
            : filtered.length === 0
              ? <Center>Sin trades con esos filtros</Center>
              : <StatsView trades={filtered} />
        )}
        {tab === 'backtest' && <BacktestView />}
      </div>
    </div>
  )
}

function Center({ children, color }: { children: React.ReactNode; color?: string }) {
  return (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: color ?? 'var(--text2)', fontSize: 11 }}>
      {children}
    </div>
  )
}
