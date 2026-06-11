import type { Trade } from '../lib/types'
import { sesLabel, fmtR, fmtUsd, fmtDate, fmtDur } from '../lib/utils'
import { ACCOUNT } from '../lib/types'

interface Props {
  trades: Trade[]
  selected: Trade | null
  onSelect: (t: Trade) => void
}

export default function TradeList({ trades, selected, onSelect }: Props) {
  const closed = trades.filter(t => !t.isOpen)
  const wins   = closed.filter(t => (t.resultR ?? 0) > 0).length
  const wr     = closed.length ? Math.round(wins / closed.length * 100) : 0
  const totalR = closed.reduce((s, t) => s + (t.resultR ?? 0), 0)
  const equity = trades.length ? trades[trades.length - 1].equity : ACCOUNT

  return (
    <div style={{
      display: 'flex', flexDirection: 'column',
      width: 'clamp(160px, 20%, 280px)',
      flexShrink: 0,
      borderRight: '1px solid var(--border)',
      background: 'var(--bg)',
      minWidth: 0,
    }}>
      {/* header */}
      <div style={{
        padding: '5px 8px', borderBottom: '1px solid var(--border)',
        display: 'flex', flexDirection: 'column', gap: 3,
        fontSize: 10, color: 'var(--text2)', flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span><b style={{ color: 'var(--text)' }}>{trades.length}</b> trades</span>
          <span>WR=<b style={{ color: 'var(--text)' }}>{wr}%</b></span>
          <span style={{ marginLeft: 'auto', color: totalR >= 0 ? 'var(--green)' : 'var(--red)', fontSize: 10 }}>
            {fmtR(totalR)}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11 }}>
          <span style={{ color: 'var(--text2)', fontWeight: 600 }}>${ACCOUNT.toFixed(2)}</span>
          <span style={{ color: 'var(--text2)', fontWeight: 700 }}>→</span>
          <span style={{ fontWeight: 800, fontSize: 13, color: equity >= ACCOUNT ? 'var(--green)' : 'var(--red)' }}>
            ${equity.toFixed(3)}
          </span>
          <span style={{
            fontWeight: 700, fontSize: 10,
            color: '#fff',
            background: equity >= ACCOUNT ? 'rgba(63,185,80,0.25)' : 'rgba(248,81,73,0.25)',
            border: `1px solid ${equity >= ACCOUNT ? 'rgba(63,185,80,0.5)' : 'rgba(248,81,73,0.5)'}`,
            borderRadius: 4, padding: '1px 5px',
          }}>
            {equity >= ACCOUNT ? '+' : ''}{((equity - ACCOUNT) / ACCOUNT * 100).toFixed(2)}%
          </span>
        </div>
      </div>

      {/* list — only this inner div scrolls */}
      <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
        {trades.map(t => {
          const isWin  = (t.resultR ?? 0) > 0
          const bdrClr = t.isOpen ? 'var(--blue)' : isWin ? 'var(--green)' : 'var(--red)'
          const isSel  = selected?.id === t.id
          return (
            <div
              key={t.id}
              onClick={() => onSelect(t)}
              style={{
                padding: '5px 8px',
                cursor: 'pointer',
                borderLeft: `3px solid ${bdrClr}`,
                borderBottom: '1px solid var(--border)',
                background: isSel ? 'var(--bg3)' : 'transparent',
              }}
            >
              <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                <span style={{ color: 'var(--text3)', fontSize: 9, width: 18, flexShrink: 0 }}>
                  #{String(t.idx).padStart(2, '0')}
                </span>
                <span style={{
                  fontSize: 9, fontWeight: 700, padding: '1px 4px', borderRadius: 3,
                  background: t.dir === 'Short' ? 'rgba(248,81,73,0.2)' : 'rgba(63,185,80,0.2)',
                  color: t.dir === 'Short' ? 'var(--red)' : 'var(--green)',
                  flexShrink: 0,
                }}>{t.dir.toUpperCase()}</span>
                <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--text)', flexShrink: 0 }}>
                  {t.sym.replace('USDT', '')}
                </span>
                <span style={{ color: 'var(--text2)', fontSize: 9, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {sesLabel(t.session)}
                </span>
                <span style={{ fontSize: 10, fontWeight: 700, flexShrink: 0, color: t.isOpen ? 'var(--blue)' : isWin ? 'var(--green)' : 'var(--red)' }}>
                  {t.isOpen ? 'OPEN' : fmtR(t.resultR)}
                </span>
              </div>
              <div style={{ display: 'flex', gap: 5, color: 'var(--text3)', fontSize: 9, paddingLeft: 22, marginTop: 2, flexWrap: 'nowrap', overflow: 'hidden' }}>
                <span style={{ flexShrink: 0 }}>{fmtDate(t.tsMs)}</span>
                {!t.isOpen && (
                  <span style={{ fontWeight: 700, flexShrink: 0, color: isWin ? 'var(--green)' : 'var(--red)' }}>
                    {fmtUsd(t.pnlUsd)}
                  </span>
                )}
                {t.durationMin != null && (
                  <span style={{ color: 'var(--blue)', flexShrink: 0 }}>{fmtDur(t.durationMin)}</span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
