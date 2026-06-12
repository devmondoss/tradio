import { useState } from 'react'
import type { Trade } from '../lib/types'
import { fmtR } from '../lib/utils'
import LiveView from './LiveView'
import StatsView from './StatsView'
import BacktestView from './BacktestView'
import FilterBar, { emptyFilters, applyFilters, type Filters } from '../components/FilterBar'

type SubTab = 'live' | 'stats' | 'backtest'

interface Props {
  trades:   Trade[]
  loading:  boolean
  error:    string | null
  onReload: () => void
}

interface BtStats { n: number; wins: number; totalR: number; avgR: number; equity: number }

export default function RBFModuleView({ trades, loading, error, onReload }: Props) {
  const [sub,     setSub]     = useState<SubTab>('live')
  const [filters, setFilters] = useState<Filters>(emptyFilters())
  const [btStats, setBtStats] = useState<BtStats | null>(null)

  const filtered  = applyFilters(trades, filters)
  const closed    = trades.filter(t => !t.isOpen)

  // Header muestra stats del backtest cuando está en esa pestaña, si no las live
  const n      = sub === 'backtest' && btStats ? btStats.n      : closed.length
  const wins   = sub === 'backtest' && btStats ? btStats.wins   : closed.filter(t => (t.resultR ?? 0) > 0).length
  const totalR = sub === 'backtest' && btStats ? btStats.totalR : closed.reduce((s, t) => s + (t.resultR ?? 0), 0)
  const avgR   = sub === 'backtest' && btStats ? btStats.avgR   : (n ? totalR / n : 0)
  const wr     = n ? wins / n * 100 : 0

  const openCount = trades.filter(t => t.isOpen).length
  const wrCol  = n === 0 ? 'var(--text3)' : wr >= 55 ? 'var(--green)' : wr >= 45 ? 'var(--yellow)' : 'var(--red)'
  const rC     = (v: number) => v >= 0 ? 'var(--green)' : 'var(--red)'
  const color  = 'var(--blue)'

  return (
    <div className="mod-wrap">

      {/* ── Top bar ─────────────────────────────────────────────────── */}
      <div className="mod-header" style={{ borderTop: `2px solid ${color}` }}>
        {/* Name row */}
        <div className="mod-name-row">
          <span className="mod-name" style={{ color }}>RBF</span>
          <span className="mod-desc">
            Range Breakout Flow · Short · {sub === 'backtest' ? 'Backtest' : 'Live'}
            {sub === 'backtest' && btStats ? ` · score≥1 · BE +1R · timeout 45b` : ''}
          </span>
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

          {/* Open + FilterBar + reload */}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', paddingBottom: 8, marginLeft: 16 }}>
            {openCount > 0 && <span className="open-badge" style={{ color }}>{openCount} OPEN</span>}
            {loading && <span style={{ fontSize: 9, color: 'var(--text3)' }}>cargando…</span>}
            {error   && <span style={{ fontSize: 9, color: 'var(--red)'   }}>error</span>}
            {!loading && trades.length > 0 && sub !== 'backtest' && (
              <FilterBar trades={trades} filters={filters} filtered={filtered} onChange={setFilters} />
            )}
            {sub !== 'backtest' && (
              <button className="reload-btn" onClick={onReload}>↺</button>
            )}
          </div>
        </div>
      </div>

      {/* ── Content ─────────────────────────────────────────────────── */}
      <div className="mod-body">
        {sub === 'backtest' ? (
          <BacktestView onStats={setBtStats} />
        ) : loading ? (
          <div className="mod-center">Cargando…</div>
        ) : error ? (
          <div className="mod-center" style={{ color: 'var(--red)' }}>Error: {error}</div>
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
