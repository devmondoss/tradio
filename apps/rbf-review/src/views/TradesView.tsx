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
      <InfoRow label="Entry at"  value={(() => { const d = new Date(trade.tsMs); return `${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')} ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')} UTC` })()} />
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

      {trade.isLive && (
        <>
          <SectionHeader label="Live Order" />
          <div style={{
            padding: '4px 5px', borderRadius: 4, marginBottom: 4,
            background: 'rgba(88,166,255,0.08)', border: '1px solid rgba(88,166,255,0.2)',
          }}>
            <div style={{ fontSize: 8, color: 'var(--blue)', fontWeight: 700, marginBottom: 3, letterSpacing: 0.5 }}>
              ● BYBIT LIVE
            </div>
            {trade.liveEntryOrderId && (
              <InfoRow label="Entry ID" value={trade.liveEntryOrderId.slice(-8)} />
            )}
            {trade.liveFillPrice != null && (
              <InfoRow label="Fill" value={trade.liveFillPrice.toFixed(2)} color="var(--blue)" />
            )}
            {trade.liveFilledQty != null && (
              <InfoRow label="Qty" value={trade.liveFilledQty.toFixed(5) + ' BTC'} />
            )}
            {trade.liveTpOrderId && (
              <InfoRow label="TP ID" value={trade.liveTpOrderId.slice(-8)} color="var(--green)" />
            )}
            {trade.liveSlOrderId && (
              <InfoRow label="SL ID" value={trade.liveSlOrderId.slice(-8)} color="var(--red)" />
            )}
          </div>
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
        <>
          <SectionHeader label="Score" />
          <div style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            marginBottom: 5, padding: '4px 0',
            borderBottom: '1px solid var(--border)',
          }}>
            <span style={{ fontFamily: 'var(--mono)', fontWeight: 800, fontSize: 14, color: trade.score >= 3 ? 'var(--green)' : trade.score >= 2 ? 'var(--yellow)' : 'var(--red)' }}>
              {trade.score}/4
            </span>
            <span style={{ fontSize: 10, color: 'var(--text3)', fontFamily: 'var(--mono)' }}>
              {['0.20×', '0.50×', '1.00×', '1.50×', '2.00×'][trade.score]}
            </span>
            <span style={{ fontSize: 9, color: 'var(--text3)' }}>
              Risk ${trade.riskUsd.toFixed(2)}
            </span>
          </div>

          {trade.scoreBreakdown ? (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              {[
                { ok: trade.scoreBreakdown.sv,  label: 'sell_vol', val: trade.scoreBreakdown.svVal.toFixed(2),  thr: trade.scoreBreakdown.svThr.toFixed(2),  op: '>=' },
                { ok: trade.scoreBreakdown.bv,  label: 'buy_vol',  val: trade.scoreBreakdown.bvVal.toFixed(2),  thr: trade.scoreBreakdown.bvThr.toFixed(2),  op: '>=' },
                { ok: trade.scoreBreakdown.cvd, label: 'cvd_slope',val: trade.scoreBreakdown.cvdVal.toFixed(3), thr: '0',                                     op: '>'  },
                { ok: trade.scoreBreakdown.vr,  label: 'vr',       val: trade.scoreBreakdown.vrVal.toFixed(3),  thr: trade.scoreBreakdown.vrThr.toFixed(3),   op: '>=' },
              ].map(({ ok, label, val, thr, op }) => (
                <div key={label} style={{
                  display: 'flex', alignItems: 'center', gap: 5,
                  padding: '2px 3px', borderRadius: 3,
                  background: ok ? 'rgba(35,134,54,0.10)' : 'rgba(240,62,62,0.07)',
                }}>
                  <span style={{ fontSize: 11, color: ok ? 'var(--green)' : 'var(--red)', lineHeight: 1, flexShrink: 0 }}>
                    {ok ? '●' : '○'}
                  </span>
                  <span style={{ fontSize: 9, color: 'var(--text2)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {label}
                  </span>
                  <span style={{ fontFamily: 'var(--mono)', fontSize: 9, color: ok ? 'var(--green)' : 'var(--text3)', flexShrink: 0 }}>
                    {val}
                  </span>
                  <span style={{ fontSize: 8, color: 'var(--text3)', flexShrink: 0 }}>
                    {op}{thr}
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <InfoRow label="Score" value={`${trade.score}/4`} color={trade.score >= 3 ? 'var(--green)' : 'var(--yellow)'} />
          )}
        </>
      )}
    </div>
  )
}

export default function TradesView({ trades }: Props) {
  const [selected, setSelected] = useState<Trade | null>(trades[0] ?? null)
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
