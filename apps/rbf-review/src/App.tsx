import { useEffect, useRef, useState } from 'react'
import { supabase, type RbfSignal } from './lib/supabase'
import { buildTrades } from './lib/utils'
import type { Trade } from './lib/types'
import ChartView      from './views/ChartView'
import DashboardView  from './views/DashboardView'
import RBFModuleView  from './views/RBFModuleView'
import MTFModuleView  from './views/MTFModuleView'
import StrategyModuleView from './views/StrategyModuleView'

type Tab   = 'chart' | 'strategies' | 'rbf' | 'mtf' | 'amd' | 'dashboard'
type Theme = 'light' | 'dark'

const SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT']

// ── Nav icon definitions ──────────────────────────────────────────────────────
// SVG paths keep the bundle tiny and look crisp at any size
const ICON_CHART = (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="1,12 5,7 8,9 12,4 15,6" />
    <line x1="1" y1="15" x2="15" y2="15" />
  </svg>
)
const ICON_GRID = (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <rect x="1" y="1" width="6" height="6" rx="1"/>
    <rect x="9" y="1" width="6" height="6" rx="1"/>
    <rect x="1" y="9" width="6" height="6" rx="1"/>
    <rect x="9" y="9" width="6" height="6" rx="1"/>
  </svg>
)
const ICON_RBF = (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <rect x="1" y="5" width="14" height="6" rx="1"/>
    <line x1="5" y1="5" x2="5" y2="1"/>
    <line x1="11" y1="5" x2="11" y2="1"/>
    <line x1="5" y1="11" x2="5" y2="15"/>
    <line x1="11" y1="11" x2="11" y2="15"/>
  </svg>
)
const ICON_MTF = (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="1,13 4,8 7,10 10,5 13,7 15,3"/>
    <line x1="8" y1="3" x2="15" y2="3"/>
    <line x1="15" y1="3" x2="15" y2="9"/>
  </svg>
)
const ICON_AMD = (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <circle cx="8" cy="8" r="6"/>
    <polyline points="8,5 8,8 10,10"/>
  </svg>
)

const NAV_ITEMS: { id: Tab; icon: React.ReactNode; tip: string }[] = [
  { id: 'chart',      icon: ICON_CHART, tip: 'Chart en vivo'  },
  { id: 'strategies', icon: ICON_GRID,  tip: 'Estrategias'    },
]

// ── Logo ──────────────────────────────────────────────────────────────────────
function TradioLogo() {
  return (
    <div className="tradio-logo-wrap">
      <img src="/tradio-image.png" alt="Tradio" className="tradio-img" />
    </div>
  )
}

// ── Clock UTC ─────────────────────────────────────────────────────────────────
function ClockUTC() {
  const [time, setTime] = useState(() => {
    const n = new Date()
    return `${String(n.getUTCHours()).padStart(2,'0')}:${String(n.getUTCMinutes()).padStart(2,'0')}`
  })
  useEffect(() => {
    const id = setInterval(() => {
      const n = new Date()
      setTime(`${String(n.getUTCHours()).padStart(2,'0')}:${String(n.getUTCMinutes()).padStart(2,'0')}`)
    }, 10000)
    return () => clearInterval(id)
  }, [])
  return <span className="clock-utc">{time} <span className="clock-utc-label">UTC</span></span>
}

// ── Live dot ──────────────────────────────────────────────────────────────────
function LiveDot() {
  return (
    <div className="live-dot-wrap">
      <div className="live-dot" />
      <span className="live-label">LIVE</span>
    </div>
  )
}

// ── User avatar panel ─────────────────────────────────────────────────────────
function UserPanel({ theme, onToggleTheme }: { theme: Theme; onToggleTheme: () => void }) {
  const isDark = theme === 'dark'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      {/* Theme toggle — único botón */}
      <button onClick={onToggleTheme} title={isDark ? 'Modo claro' : 'Modo oscuro'} className="icon-btn">
        {isDark
          ? <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"><circle cx="7" cy="7" r="2.5"/><path d="M7 1v1.5M7 11.5V13M1 7h1.5M11.5 7H13M2.9 2.9l1 1M10.1 10.1l1 1M10.1 3.9l-1 1M3.9 10.1l-1 1"/></svg>
          : <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"><path d="M12 8.5A5 5 0 0 1 5.5 2 5 5 0 1 0 12 8.5z"/></svg>
        }
      </button>
      {/* Avatar */}
      <div className="avatar-btn">G</div>
    </div>
  )
}

// ── Nav icon button ───────────────────────────────────────────────────────────
function NavIcon({ item, active, onClick }: {
  item: typeof NAV_ITEMS[0]; active: boolean; onClick: () => void
}) {
  const [hov, setHov] = useState(false)
  return (
    <div style={{ position: 'relative' }}>
      <button
        onClick={onClick}
        onMouseEnter={() => setHov(true)}
        onMouseLeave={() => setHov(false)}
        className={`nav-icon-btn${active ? ' nav-icon-active' : hov ? ' nav-icon-hov' : ''}`}
      >
        {item.icon}
      </button>
      {hov && !active && (
        <div className="nav-icon-tooltip">{item.tip}</div>
      )}
    </div>
  )
}

// ── Strategies hub ───────────────────────────────────────────────────────────
const STRATEGY_CARDS: { id: Tab; icon: React.ReactNode; name: string; desc: string; color: string }[] = [
  { id: 'rbf',       icon: ICON_RBF, name: 'RBF',        desc: 'Range Breakout Flow · Shorts post-breakout · 5 símbolos',   color: 'var(--red)'   },
  { id: 'mtf',       icon: ICON_MTF, name: 'MTF',        desc: 'D1/H4 EMA20 · H1 stop estructural · M1 patrones mineados · CVD exhaustion', color: 'var(--blue)'  },
  { id: 'amd',       icon: ICON_AMD, name: 'AMD',        desc: 'Accumulation · Manipulation · Distribution · paper trader', color: 'var(--yellow)'},
  { id: 'dashboard', icon: ICON_GRID,name: 'Dashboard',  desc: 'Overview global · equity · estadísticas combinadas',        color: 'var(--accent)'},
]

function StrategiesHub({ onSelect }: { onSelect: (t: Tab) => void }) {
  return (
    <div className="hub-wrap">
      <div className="hub-title">Estrategias</div>
      <div className="hub-grid">
        {STRATEGY_CARDS.map(c => (
          <button key={c.id} className="hub-card" onClick={() => onSelect(c.id)}>
            <div className="hub-card-icon" style={{ color: c.color }}>{c.icon}</div>
            <div className="hub-card-name" style={{ color: c.color }}>{c.name}</div>
            <div className="hub-card-desc">{c.desc}</div>
          </button>
        ))}
      </div>
    </div>
  )
}

// ── App ───────────────────────────────────────────────────────────────────────
export default function App() {
  const [tab,     setTab]     = useState<Tab>('chart')
  const [theme,   setTheme]   = useState<Theme>(() =>
    (localStorage.getItem('tradio-theme') as Theme) ?? 'light'
  )
  const [signals, setSignals] = useState<RbfSignal[]>([])
  const [loading, setLoading] = useState(true)
  const [error,   setError]   = useState<string | null>(null)
  const signalsRef = useRef<RbfSignal[]>([])

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('tradio-theme', theme)
  }, [theme])

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
          const updated = signalsRef.current.map(s =>
            s.id === (payload.new as RbfSignal).id ? payload.new as RbfSignal : s
          )
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
    <div className="app-root">

      {/* ── Top nav — limpio ────────────────────────────────────────────── */}
      <header className="topnav">
        <TradioLogo />
        <div className="topnav-divider" />
        <nav className="topnav-icons">
          {NAV_ITEMS.map(item => (
            <NavIcon
              key={item.id}
              item={item}
              active={tab === item.id || (item.id === 'strategies' && ['rbf','mtf','amd','dashboard'].includes(tab))}
              onClick={() => setTab(item.id)}
            />
          ))}
        </nav>
        <div className="topnav-spacer" />
        <div className="topnav-right">
          <ClockUTC />
          <LiveDot />
          <div className="topnav-divider" />
          <UserPanel theme={theme} onToggleTheme={() => setTheme(t => t === 'light' ? 'dark' : 'light')} />
        </div>
      </header>

      {/* ── Content ─────────────────────────────────────────────────────── */}
      <main className="app-content">
        {tab === 'chart'      && <ChartView theme={theme} />}
        {tab === 'strategies' && <StrategiesHub onSelect={setTab} />}
        {(tab === 'rbf' || tab === 'mtf' || tab === 'amd' || tab === 'dashboard') && (
          <div className="module-shell">
            <button className="module-back" onClick={() => setTab('strategies')}>
              ← Estrategias
            </button>
            {tab === 'dashboard' && <DashboardView rbfTrades={trades} />}
            {tab === 'rbf'       && <RBFModuleView trades={trades} loading={loading} error={error} onReload={loadAll} />}
            {tab === 'mtf'       && <MTFModuleView />}
            {tab === 'amd'       && <StrategyModuleView strategy="amd" />}
          </div>
        )}
      </main>

    </div>
  )
}
