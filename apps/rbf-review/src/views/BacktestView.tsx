import { useState } from 'react'
import LiveView from './LiveView'
import StatsView from './StatsView'
import type { Trade } from '../lib/types'
import { runBacktest, type BtProgress } from '../lib/backtest'

type Panel = 'trades' | 'stats'

export default function BacktestView() {
  const [panel,    setPanel]    = useState<Panel>('trades')
  const [trades,   setTrades]   = useState<Trade[]>([])
  const [loading,  setLoading]  = useState(false)
  const [progress, setProgress] = useState<BtProgress | null>(null)
  const [error,    setError]    = useState<string | null>(null)
  const [days,     setDays]     = useState(7)
  const [ran,      setRan]      = useState(false)

  async function run() {
    setLoading(true)
    setError(null)
    setProgress(null)
    setTrades([])
    try {
      const result = await runBacktest(days, p => setProgress(p))
      setTrades(result)
      setRan(true)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
      setProgress(null)
    }
  }

  if (!ran && !loading) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 16 }}>
        <div style={{ color: 'var(--text2)', fontSize: 11, textAlign: 'center', lineHeight: 1.6 }}>
          Backtest RBF — Shorts solo<br />
          <span style={{ color: 'var(--text3)', fontSize: 10 }}>
            VR≥3× · CVD slope &lt; 0 · CVD rango &lt; 0 · DZ [0.5–3.0] · Stop = Range HIGH · Trail ATR 1.2× (activa a 1.5R) · Time stop 30 bars<br />
            Sessions: London · Overlap · NY · (DZ/CVD rango solo si datos disponibles)
          </span>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ color: 'var(--text3)', fontSize: 10 }}>Ultimos</span>
          {[3, 7, 14].map(d => (
            <button key={d} onClick={() => setDays(d)} style={{
              padding: '3px 10px', borderRadius: 4, fontSize: 10, cursor: 'pointer', fontFamily: 'inherit',
              background: days === d ? 'var(--blue)' : 'var(--bg3)',
              border: `1px solid ${days === d ? 'var(--blue)' : 'var(--border2)'}`,
              color: days === d ? '#fff' : 'var(--text2)',
            }}>{d}d</button>
          ))}
        </div>
        <button onClick={run} style={{
          padding: '6px 20px', borderRadius: 5, fontSize: 11, fontWeight: 700,
          cursor: 'pointer', fontFamily: 'inherit',
          background: 'var(--blue)', border: 'none', color: '#fff',
        }}>
          Ejecutar Backtest
        </button>
        <span style={{ color: 'var(--text3)', fontSize: 9 }}>Descarga datos de Supabase (~{days * 7.2}k bars × 5 simbolos)</span>
      </div>
    )
  }

  if (loading) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 10 }}>
        <div style={{ color: 'var(--text2)', fontSize: 11 }}>
          {progress
            ? `Cargando ${progress.sym.replace('USDT', '')}… ${progress.loaded} bars`
            : 'Iniciando…'}
        </div>
        <div style={{ width: 200, height: 3, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
          <div style={{
            height: '100%', background: 'var(--blue)', borderRadius: 2,
            width: progress ? `${Math.min((progress.loaded / progress.total) * 100, 95)}%` : '10%',
            transition: 'width 0.3s',
          }} />
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 12 }}>
        <div style={{ color: 'var(--red)', fontSize: 11 }}>Error: {error}</div>
        <button onClick={run} style={{
          padding: '4px 14px', borderRadius: 4, fontSize: 10, cursor: 'pointer',
          fontFamily: 'inherit', background: 'var(--bg3)', border: '1px solid var(--border2)', color: 'var(--text2)',
        }}>Reintentar</button>
      </div>
    )
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
      {/* sub-header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '0 8px', height: 30, flexShrink: 0,
        borderBottom: '1px solid var(--border)', background: 'var(--bg2)',
        fontSize: 10, color: 'var(--text3)',
      }}>
        <button onClick={() => setPanel('trades')} style={{
          padding: '2px 10px', borderRadius: 3, fontSize: 10, cursor: 'pointer', fontFamily: 'inherit',
          background: panel === 'trades' ? 'var(--bg3)' : 'none',
          border: `1px solid ${panel === 'trades' ? 'var(--border2)' : 'transparent'}`,
          color: panel === 'trades' ? 'var(--text)' : 'var(--text3)',
        }}>Trades</button>
        <button onClick={() => setPanel('stats')} style={{
          padding: '2px 10px', borderRadius: 3, fontSize: 10, cursor: 'pointer', fontFamily: 'inherit',
          background: panel === 'stats' ? 'var(--bg3)' : 'none',
          border: `1px solid ${panel === 'stats' ? 'var(--border2)' : 'transparent'}`,
          color: panel === 'stats' ? 'var(--text)' : 'var(--text3)',
        }}>Stats</button>
        <span style={{ color: 'var(--text2)' }}>{trades.length} trades · {days}d</span>
        <span style={{ marginLeft: 'auto' }}>
          <button onClick={run} style={{
            padding: '2px 8px', borderRadius: 3, fontSize: 9, cursor: 'pointer', fontFamily: 'inherit',
            background: 'var(--bg3)', border: '1px solid var(--border2)', color: 'var(--text3)',
          }}>Re-run</button>
        </span>
      </div>

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>
        {panel === 'trades' && <LiveView trades={trades} />}
        {panel === 'stats'  && <StatsView trades={trades} />}
      </div>
    </div>
  )
}
