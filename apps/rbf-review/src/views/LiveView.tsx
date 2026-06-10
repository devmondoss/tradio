import { useState } from 'react'
import TradeList from '../components/TradeList'
import TradeChart from '../components/TradeChart'
import type { Trade } from '../lib/types'
import { sesLabel, fmtR, fmtUsd, fmtDur } from '../lib/utils'

interface Props { trades: Trade[] }

function InfoRow({ label, value, color }: { label: string; value: React.ReactNode; color?: string }) {
  return (
    <div style={{
      display: 'flex', justifyContent: 'space-between',
      padding: '2px 0', borderBottom: '1px solid rgba(33,38,45,0.5)',
      minWidth: 0,
    }}>
      <span style={{ color: 'var(--text3)', fontSize: 9, textTransform: 'uppercase', flexShrink: 0, marginRight: 6 }}>{label}</span>
      <span style={{ color: color ?? 'var(--text)', fontSize: 10, fontWeight: 600, textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{value}</span>
    </div>
  )
}

function SectionHeader({ label }: { label: string }) {
  return (
    <div style={{ marginTop: 8, marginBottom: 3, color: 'var(--text3)', fontSize: 9, textTransform: 'uppercase', letterSpacing: 1 }}>
      {label}
    </div>
  )
}

function DetailPanel({ trade }: { trade: Trade }) {
  const r      = trade.resultR ?? null
  const rColor = trade.isOpen ? 'var(--blue)' : r != null && r > 0 ? 'var(--green)' : 'var(--red)'

  return (
    <div style={{
      width: 'clamp(140px, 17%, 220px)',
      flexShrink: 0, minWidth: 0,
      borderLeft: '1px solid var(--border)',
      background: 'var(--bg)',
      padding: '8px',
      overflowY: 'auto',
      fontSize: 10,
    }}>
      <div style={{ fontWeight: 700, marginBottom: 6, color: 'var(--text2)', fontSize: 9, textTransform: 'uppercase', letterSpacing: 1 }}>
        Trade #{trade.idx}
      </div>

      <InfoRow label="Symbol"    value={trade.sym.replace('USDT', '')} />
      <InfoRow label="Dir"       value={trade.dir} color={trade.dir === 'Short' ? 'var(--red)' : 'var(--green)'} />
      <InfoRow label="Sesion"    value={sesLabel(trade.session)} />
      <InfoRow label="Resultado" value={trade.isOpen ? 'OPEN' : fmtR(r)} color={rColor} />
      {!trade.isOpen && <InfoRow label="PnL" value={fmtUsd(trade.pnlUsd)} color={rColor} />}
      {trade.durationMin != null && <InfoRow label="Dur" value={fmtDur(trade.durationMin)} />}
      {trade.reason && <InfoRow label="Exit" value={trade.reason} />}

      <SectionHeader label="Precios" />
      <InfoRow label="Entry"  value={trade.entry} />
      <InfoRow label="Stop"   value={trade.stop}   color="var(--red)" />
      <InfoRow label="Target" value={trade.target}  color="var(--green)" />
      {trade.exit > 0 && <InfoRow label="Exit" value={trade.exit} />}
      <InfoRow label="Stop%"  value={trade.stopPct + '%'} />
      <InfoRow label="Risk$"  value={'$' + trade.riskUsd.toFixed(3)} />

      <SectionHeader label="Micro" />
      {trade.vr       != null && <InfoRow label="VR"       value={trade.vr.toFixed(2) + 'x'}       color={trade.vr >= 3 ? 'var(--green)' : 'var(--yellow)'} />}
      {trade.cvdSlope != null && <InfoRow label="CVD"      value={trade.cvdSlope.toFixed(4)} />}
      {trade.obi      != null && <InfoRow label="OBI"      value={trade.obi.toFixed(3)} />}
      {trade.dz       != null && <InfoRow label="DZ"       value={trade.dz.toFixed(0)} />}
      {trade.funding  != null && <InfoRow label="Funding"  value={(trade.funding * 100).toFixed(4) + '%'} />}
      {trade.priceVsVwap != null && <InfoRow label="vsVWAP" value={(trade.priceVsVwap * 100).toFixed(3) + '%'} />}

      {trade.rangePct != null && (
        <>
          <SectionHeader label="Rango" />
          <InfoRow label="Range%"  value={trade.rangePct.toFixed(3) + '%'} />
          {trade.rangeBars  != null && <InfoRow label="Bars"    value={trade.rangeBars} />}
          {trade.rangeTouch != null && <InfoRow label="Touches" value={trade.rangeTouch} />}
        </>
      )}

      {trade.confluenceFlags && trade.confluenceFlags.length > 0 && (
        <>
          <SectionHeader label="Flags" />
          {trade.confluenceFlags.map((f, i) => (
            <div key={i} style={{ fontSize: 9, color: 'var(--blue)', paddingBottom: 2 }}>{f}</div>
          ))}
        </>
      )}

      {trade.score != null && (
        <InfoRow label="Score" value={trade.score.toFixed(2)} color={trade.score >= 3 ? 'var(--green)' : 'var(--yellow)'} />
      )}
    </div>
  )
}

export default function LiveView({ trades }: Props) {
  const [selected, setSelected] = useState<Trade | null>(trades[0] ?? null)

  // keep selected valid if trades change
  const sel = selected ? (trades.find(t => t.id === selected.id) ?? trades[0] ?? null) : (trades[0] ?? null)

  return (
    <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minWidth: 0, minHeight: 0 }}>
      <TradeList trades={trades} selected={sel} onSelect={setSelected} />

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minWidth: 0 }}>
        <TradeChart trade={sel} />
      </div>

      {sel && <DetailPanel trade={sel} />}
    </div>
  )
}
