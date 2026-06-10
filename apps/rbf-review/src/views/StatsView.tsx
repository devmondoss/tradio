import type { Trade } from '../lib/types'
import { sesLabel, fmtR, fmtUsd, winRate, avgR } from '../lib/utils'
import { ACCOUNT } from '../lib/types'

interface Props { trades: Trade[] }

// ─── Compact stat table ───────────────────────────────────────────────────────

function StatTable({ title, groups, showTotal }: {
  title: string
  groups: { label: string; trades: Trade[] }[]
  showTotal?: boolean
}) {
  const all = groups.flatMap(g => g.trades)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, padding: '0 8px' }}>
      <div style={{
        fontSize: 9, textTransform: 'uppercase', letterSpacing: 1,
        color: 'var(--text3)', padding: '6px 0 4px',
      }}>{title}</div>
      <div style={{ flex: 1, minHeight: 0, background: 'var(--bg2)', border: '1px solid var(--border)', borderRadius: 5, overflow: 'hidden' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
          <thead>
            <tr>
              {['', 'n', 'WR%', 'AvgR', 'TotR', 'PnL'].map(h => (
                <th key={h} style={{
                  padding: '4px 6px', color: 'var(--text3)', fontWeight: 600,
                  fontSize: 9, textTransform: 'uppercase',
                  borderBottom: '1px solid var(--border)',
                  textAlign: h === '' ? 'left' : 'center',
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {groups.map(g => <TRow key={g.label} label={g.label} trades={g.trades} />)}
            {showTotal && <TRow label="TOTAL" trades={all} isTotal />}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function TRow({ label, trades, isTotal }: { label: string; trades: Trade[]; isTotal?: boolean }) {
  const closed = trades.filter(t => !t.isOpen)
  const wr     = winRate(trades)
  const ar     = avgR(trades)
  const totalR = closed.reduce((s, t) => s + (t.resultR ?? 0), 0)
  const pnl    = closed.reduce((s, t) => s + t.pnlUsd, 0)
  const nc     = closed.length

  const bg = isTotal ? 'rgba(30,38,46,0.6)' : 'transparent'
  const rBg = (v: number) => v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--text3)'

  return (
    <tr style={{ borderBottom: '1px solid var(--border)', background: bg }}>
      <td style={{ padding: '3px 6px', color: isTotal ? 'var(--text)' : 'var(--text2)', fontWeight: isTotal ? 700 : 400, fontSize: 10 }}>{label}</td>
      <td style={{ padding: '3px 6px', color: 'var(--text3)', textAlign: 'center', fontSize: 9 }}>{trades.length}</td>
      <td style={{ padding: '3px 6px', textAlign: 'center', color: nc ? (wr >= 50 ? 'var(--green)' : wr > 0 ? 'var(--yellow)' : 'var(--text3)') : 'var(--text3)' }}>
        {nc ? wr.toFixed(0) + '%' : '—'}
      </td>
      <td style={{ padding: '3px 6px', textAlign: 'center', color: nc ? rBg(ar) : 'var(--text3)' }}>{nc ? fmtR(ar) : '—'}</td>
      <td style={{ padding: '3px 6px', textAlign: 'center', color: nc ? rBg(totalR) : 'var(--text3)' }}>{nc ? fmtR(totalR) : '—'}</td>
      <td style={{ padding: '3px 6px', textAlign: 'right',  color: nc ? rBg(pnl)    : 'var(--text3)' }}>{nc ? fmtUsd(pnl) : '—'}</td>
    </tr>
  )
}

// ─── Equity curve — SVG with viewBox, scales to its container ────────────────

function EquityChart({ trades }: { trades: Trade[] }) {
  const closed = trades.filter(t => !t.isOpen)
  const pts    = [ACCOUNT, ...closed.map(t => t.equity)]
  if (pts.length < 2) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text3)', fontSize: 11 }}>
        Sin trades cerrados
      </div>
    )
  }

  const W = 1000, H = 200
  const min  = Math.min(...pts)
  const max  = Math.max(...pts)
  const pad  = (max - min) * 0.12 || 2
  const lo   = min - pad, hi = max + pad
  const x    = (i: number) => (i / (pts.length - 1)) * W
  const y    = (v: number) => H - ((v - lo) / (hi - lo)) * H
  const d    = pts.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  const area = d + ` L${x(pts.length - 1).toFixed(1)},${H} L0,${H} Z`
  const final = pts[pts.length - 1]
  const color = final >= ACCOUNT ? '#3fb950' : '#f85149'

  // grid lines
  const range  = hi - lo
  const step   = range > 20 ? 10 : range > 5 ? 2 : 1
  const gridYs = Array.from({ length: Math.ceil(range / step) + 1 }, (_, i) => lo + i * step).filter(v => v >= lo && v <= hi)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', padding: '6px 8px 0', minHeight: 0, flex: 1 }}>
      <div style={{ fontSize: 9, textTransform: 'uppercase', letterSpacing: 1, color: 'var(--text3)', marginBottom: 4 }}>
        Equity Curve — ${ACCOUNT} → <span style={{ color, fontWeight: 700 }}>${final.toFixed(2)}</span>
        <span style={{ marginLeft: 8, color: 'var(--text3)' }}>({closed.length} closed)</span>
      </div>
      <div style={{ flex: 1, minHeight: 0, background: 'var(--bg2)', border: '1px solid var(--border)', borderRadius: 5, padding: '6px 4px' }}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height="100%" preserveAspectRatio="none" style={{ display: 'block' }}>
          <defs>
            <linearGradient id="eq-grad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%"   stopColor={color} stopOpacity="0.3" />
              <stop offset="100%" stopColor={color} stopOpacity="0.02" />
            </linearGradient>
          </defs>
          {/* grid */}
          {gridYs.map((v, i) => (
            <line key={i} x1={0} y1={y(v)} x2={W} y2={y(v)} stroke="#21262d" strokeWidth={1} />
          ))}
          {/* baseline */}
          <line x1={0} y1={y(ACCOUNT)} x2={W} y2={y(ACCOUNT)} stroke="#388bfd" strokeWidth={1} strokeDasharray="8,6" strokeOpacity="0.4" />
          {/* area + line */}
          <path d={area} fill="url(#eq-grad)" />
          <path d={d}    fill="none" stroke={color} strokeWidth={2} />
          {/* trade dots */}
          {pts.map((v, i) => (
            <circle key={i} cx={x(i)} cy={y(v)} r={i === pts.length - 1 ? 5 : 3}
              fill={v >= ACCOUNT ? '#3fb950' : '#f85149'}
              stroke={i === pts.length - 1 ? '#fff' : 'none'} strokeWidth={1} />
          ))}
        </svg>
      </div>
    </div>
  )
}

// ─── Main ─────────────────────────────────────────────────────────────────────

export default function StatsView({ trades }: Props) {
  const sessions = ['London', 'LondonNyOverlap', 'NewYork', 'Asia', 'SessionEnd']
  const symbols  = [...new Set(trades.map(t => t.sym))].sort()
  const dirs     = ['Short', 'Long'] as const

  const bySes = sessions
    .map(s => ({ label: sesLabel(s), trades: trades.filter(t => t.session === s) }))
    .filter(g => g.trades.length)

  const bySym = symbols.map(s => ({ label: s.replace('USDT', ''), trades: trades.filter(t => t.sym === s) }))
  const byDir = dirs.map(d => ({ label: d, trades: trades.filter(t => t.dir === d) })).filter(g => g.trades.length)

  return (
    <div style={{
      flex: 1,
      display: 'grid',
      gridTemplateRows: '45% 55%',
      gridTemplateColumns: '1fr 1fr 1fr',
      overflow: 'hidden',
      minHeight: 0,
    }}>
      {/* Equity chart — spans all 3 columns */}
      <div style={{ gridColumn: '1 / 4', gridRow: '1', display: 'flex', flexDirection: 'column', borderBottom: '1px solid var(--border)', minHeight: 0 }}>
        <EquityChart trades={trades} />
      </div>

      {/* Session table */}
      <div style={{ gridColumn: '1', gridRow: '2', display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--border)', minHeight: 0, overflow: 'hidden' }}>
        <StatTable title="Por Sesion" groups={bySes} showTotal />
      </div>

      {/* Symbol table */}
      <div style={{ gridColumn: '2', gridRow: '2', display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--border)', minHeight: 0, overflow: 'hidden' }}>
        <StatTable title="Por Simbolo" groups={bySym} />
      </div>

      {/* Direction table */}
      <div style={{ gridColumn: '3', gridRow: '2', display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
        <StatTable title="Long vs Short" groups={byDir} />
      </div>
    </div>
  )
}
