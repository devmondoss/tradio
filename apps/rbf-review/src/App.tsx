import { useEffect, useState } from 'react'
import LocalResultsView from './views/LocalResultsView'

type Theme = 'light' | 'dark'

function TradioLogo() {
  return (
    <div className="tradio-logo-wrap">
      <img src="/tradio-image.png" alt="Tradio" className="tradio-img" />
    </div>
  )
}

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

function UserPanel({ theme, onToggleTheme }: { theme: Theme; onToggleTheme: () => void }) {
  const isDark = theme === 'dark'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
      <button onClick={onToggleTheme} title={isDark ? 'Modo claro' : 'Modo oscuro'} className="icon-btn">
        {isDark
          ? <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"><circle cx="7" cy="7" r="2.5"/><path d="M7 1v1.5M7 11.5V13M1 7h1.5M11.5 7H13M2.9 2.9l1 1M10.1 10.1l1 1M10.1 3.9l-1 1M3.9 10.1l-1 1"/></svg>
          : <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"><path d="M12 8.5A5 5 0 0 1 5.5 2 5 5 0 1 0 12 8.5z"/></svg>
        }
      </button>
      <div className="avatar-btn">G</div>
    </div>
  )
}

export default function App() {
  const [theme, setTheme] = useState<Theme>(() =>
    (localStorage.getItem('tradio-theme') as Theme) ?? 'dark'
  )

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('tradio-theme', theme)
  }, [theme])

  return (
    <div className="app-root">
      <header className="topnav">
        <TradioLogo />
        <div className="topnav-divider" />
        <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text)', letterSpacing: 0.3 }}>
          Provisión de Liquidez
          <span style={{ fontSize: 10, fontWeight: 400, color: 'var(--text3)', marginLeft: 8 }}>
            BTCUSDT Perp · POC order-block + POC defendido · maker
          </span>
        </span>
        <div className="topnav-spacer" />
        <div className="topnav-right">
          <ClockUTC />
          <div className="topnav-divider" />
          <UserPanel theme={theme} onToggleTheme={() => setTheme(t => t === 'light' ? 'dark' : 'light')} />
        </div>
      </header>

      <main className="app-content">
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
          <LocalResultsView strategy="liquidity" />
        </div>
      </main>
    </div>
  )
}
