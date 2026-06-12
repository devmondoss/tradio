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
        Equity Curve — ${ACCOUNT} → <span style={{ color, fontWeight: 700 }}>${final.toFixed(3)}</span>
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

// ─── Capital summary ──────────────────────────────────────────────────────────

function CapitalSummary({ trades }: { trades: Trade[] }) {
  const closed  = trades.filter(t => !t.isOpen)
  const final   = closed.length ? closed[closed.length - 1].equity : ACCOUNT
  const gain    = final - ACCOUNT
  const pct     = (gain / ACCOUNT) * 100
  const color   = gain >= 0 ? 'var(--green)' : 'var(--red)'
  const sign    = gain >= 0 ? '+' : ''

  return (
    <div style={{
      display: 'flex', gap: 0, flexShrink: 0,
      borderBottom: '1px solid var(--border)',
      background: 'var(--bg2)',
    }}>
      {[
        { label: 'Capital Inicial', value: `$${ACCOUNT.toFixed(2)}`,           color: 'var(--text)' },
        { label: 'Capital Final',   value: `$${final.toFixed(3)}`,              color },
        { label: 'Ganancia',        value: `${sign}$${Math.abs(gain).toFixed(3)}`, color },
        { label: 'Retorno',         value: `${sign}${pct.toFixed(2)}%`,         color },
      ].map((item, i) => (
        <div key={i} style={{
          flex: 1, padding: '8px 12px',
          borderRight: i < 3 ? '1px solid var(--border)' : 'none',
          display: 'flex', flexDirection: 'column', gap: 3,
        }}>
          <span style={{ fontSize: 9, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: 1 }}>
            {item.label}
          </span>
          <span style={{ fontSize: 15, fontWeight: 700, color: item.color, fontFamily: 'var(--mono)' }}>
            {item.value}
          </span>
        </div>
      ))}
    </div>
  )
}

// ─── Main ─────────────────────────────────────────────────────────────────────

const REASON_ORDER = ['TAKE_PROFIT', 'TRAILING_STOP', 'STOP_LOSS', 'TARGET', 'STOP', 'SESSION_END', 'TIME_STOP', 'DATA_END']
const REASON_LABEL: Record<string, string> = {
  TAKE_PROFIT:    'Take Profit',
  TRAILING_STOP:  'Trailing Stop',
  STOP_LOSS:      'Stop Loss',
  TARGET:         'Target TP',
  STOP:           'Stop Loss',
  SESSION_END:    'Sesión fin',
  TIME_STOP:      'Time Stop',
  DATA_END:       'Data End',
}
const REASON_COLOR: Record<string, string> = {
  TAKE_PROFIT:   '#3fb950',
  TRAILING_STOP: '#58a6ff',
  STOP_LOSS:     '#f85149',
  TARGET:        '#3fb950',
  STOP:          '#f85149',
}

// ─── R Distribution chart ─────────────────────────────────────────────────────

function RDistribution({ trades }: { trades: Trade[] }) {
  const closed = trades.filter(t => !t.isOpen && t.resultR != null)
  if (closed.length === 0) return null

  const rs     = closed.map(t => t.resultR ?? 0)
  const minR   = Math.min(...rs, -1.1)
  const maxR   = Math.max(...rs,  2.1)
  const W = 1000, H = 80
  const pad = 40
  const xOf = (r: number) => pad + ((r - minR) / (maxR - minR)) * (W - pad * 2)

  const colorOf = (t: Trade) => REASON_COLOR[t.reason ?? ''] ?? (( t.resultR ?? 0) > 0 ? '#3fb950' : '#f85149')

  // Group by reason for legend
  const byReason = REASON_ORDER
    .map(r => ({ r, trades: closed.filter(t => t.reason === r) }))
    .filter(g => g.trades.length > 0)

  return (
    <div style={{ padding: '0 8px 6px' }}>
      <div style={{ fontSize: 9, textTransform: 'uppercase', letterSpacing: 1, color: 'var(--text3)', padding: '6px 0 4px' }}>
        Distribución R — {closed.length} trades
      </div>
      <div style={{ background: 'var(--bg2)', border: '1px solid var(--border)', borderRadius: 5, padding: '8px 4px 4px' }}>
        <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H * 0.6} style={{ display: 'block', overflow: 'visible' }}>
          {/* zero line */}
          <line x1={xOf(0)} y1={0} x2={xOf(0)} y2={H - 20} stroke="var(--text3)" strokeWidth={1} strokeDasharray="4,3" strokeOpacity={0.5} />
          <text x={xOf(0)} y={H - 6} textAnchor="middle" fontSize={18} fill="var(--text3)">0</text>

          {/* axis ticks */}
          {[-1, 1, 1.5, 1.9, 2, 2.5, 3].filter(v => v >= minR && v <= maxR).map(v => (
            <g key={v}>
              <line x1={xOf(v)} y1={H - 22} x2={xOf(v)} y2={H - 18} stroke="var(--border2)" strokeWidth={1} />
              <text x={xOf(v)} y={H - 6} textAnchor="middle" fontSize={16} fill="var(--text3)">{v > 0 ? `+${v}R` : `${v}R`}</text>
            </g>
          ))}

          {/* dots — jitter by index within same reason */}
          {closed.map((t, i) => {
            const r = t.resultR ?? 0
            const sameR = closed.filter(u => Math.abs((u.resultR ?? 0) - r) < 0.05)
            const pos   = sameR.indexOf(t)
            const yPos  = 12 + (pos % 4) * 12
            return (
              <circle key={i} cx={xOf(r)} cy={yPos} r={7}
                fill={colorOf(t)} fillOpacity={0.85}
                stroke="#0d1117" strokeWidth={1}
              />
            )
          })}
        </svg>

        {/* Legend */}
        <div style={{ display: 'flex', gap: 16, paddingLeft: 8, paddingTop: 2 }}>
          {byReason.map(({ r, trades: gt }) => {
            const col  = REASON_COLOR[r] ?? 'var(--text3)'
            const pct  = (gt.length / closed.length * 100).toFixed(0)
            const totR = gt.reduce((s, t) => s + (t.resultR ?? 0), 0)
            const avg  = totR / gt.length
            return (
              <div key={r} style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 9 }}>
                <div style={{ width: 8, height: 8, borderRadius: '50%', background: col, flexShrink: 0 }} />
                <span style={{ color: 'var(--text2)' }}>{REASON_LABEL[r] ?? r}</span>
                <span style={{ color: 'var(--text3)' }}>n={gt.length} ({pct}%)</span>
                <span style={{ color: col, fontFamily: 'var(--mono)' }}>avg {avg > 0 ? '+' : ''}{avg.toFixed(2)}R</span>
                <span style={{ color: 'var(--text3)', fontFamily: 'var(--mono)' }}>tot {totR > 0 ? '+' : ''}{totR.toFixed(2)}R</span>
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

export default function StatsView({ trades }: Props) {
  const sessions = ['London', 'LondonNyOverlap', 'NewYork', 'Asia', 'SessionEnd']
  const symbols  = [...new Set(trades.map(t => t.sym))].sort()
  const dirs     = ['Short', 'Long'] as const

  const bySes = sessions
    .map(s => ({ label: sesLabel(s), trades: trades.filter(t => t.session === s) }))
    .filter(g => g.trades.length)

  const bySym    = symbols.map(s => ({ label: s.replace('USDT', ''), trades: trades.filter(t => t.sym === s) }))
  const byDir    = dirs.map(d => ({ label: d, trades: trades.filter(t => t.dir === d) })).filter(g => g.trades.length)
  const byReason = REASON_ORDER
    .map(r => ({ label: REASON_LABEL[r] ?? r, trades: trades.filter(t => t.reason === r) }))
    .filter(g => g.trades.length)

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
      <CapitalSummary trades={trades} />
    <div style={{
      flex: 1,
      display: 'grid',
      gridTemplateRows: '38% 20% 42%',
      gridTemplateColumns: '1fr 1fr 1fr 1fr',
      overflow: 'hidden',
      minHeight: 0,
    }}>
      {/* Equity chart — spans all 4 columns */}
      <div style={{ gridColumn: '1 / 5', gridRow: '1', display: 'flex', flexDirection: 'column', borderBottom: '1px solid var(--border)', minHeight: 0 }}>
        <EquityChart trades={trades} />
      </div>

      {/* R Distribution — spans all 4 columns */}
      <div style={{ gridColumn: '1 / 5', gridRow: '2', borderBottom: '1px solid var(--border)', overflow: 'hidden' }}>
        <RDistribution trades={trades} />
      </div>

      {/* Session table */}
      <div style={{ gridColumn: '1', gridRow: '3', display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--border)', minHeight: 0, overflow: 'hidden' }}>
        <StatTable title="Por Sesion" groups={bySes} showTotal />
      </div>

      {/* Symbol table */}
      <div style={{ gridColumn: '2', gridRow: '3', display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--border)', minHeight: 0, overflow: 'hidden' }}>
        <StatTable title="Por Simbolo" groups={bySym} />
      </div>

      {/* Exit reason table */}
      <div style={{ gridColumn: '3', gridRow: '3', display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--border)', minHeight: 0, overflow: 'hidden' }}>
        <StatTable title="Por Salida" groups={byReason} showTotal />
      </div>

      {/* Direction table */}
      <div style={{ gridColumn: '4', gridRow: '3', display: 'flex', flexDirection: 'column', minHeight: 0, overflow: 'hidden' }}>
        <StatTable title="Long vs Short" groups={byDir} />
      </div>
    </div>
    </div>
  )
}
