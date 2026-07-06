// PaneLeaf — a configurable pane with a Flowsurface-style tab bar:
// venue icon + symbol + type + interval on the left; cog / split / maximize /
// close icons on the right. The body is routed by pane type.
import { useState, type CSSProperties } from 'react'
import type { LeafNode, PaneType } from './tree'
import { INTERVALS, intervalLabel } from '../lib/klines'
import { Icon, G } from '../ui/icons'
import CandlePane from '../panes/CandlePane'
import LinePane from '../panes/LinePane'
import DomPane from '../panes/DomPane'
import FootprintPane from '../panes/FootprintPane'
import HeatmapPane from '../panes/HeatmapPane'
import TimeSalesPane from '../panes/TimeSalesPane'
import ComparisonPane from '../panes/ComparisonPane'
import StarterPane from '../panes/StarterPane'

const SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
const TYPES: { type: PaneType; label: string }[] = [
  { type: 'candles', label: 'Velas' },
  { type: 'footprint', label: 'Footprint' },
  { type: 'heatmap', label: 'Heatmap' },
  { type: 'dom', label: 'DOM / Ladder' },
  { type: 'timeandsales', label: 'Time & Sales' },
  { type: 'comparison', label: 'Comparación' },
  { type: 'line', label: 'Línea' },
]

interface Props {
  node: LeafNode
  canClose: boolean
  isMax: boolean
  onUpdate: (patch: Partial<LeafNode>) => void
  onSplit: () => void
  onClose: () => void
  onToggleMax: () => void
}

export default function PaneLeaf({ node, canClose, isMax, onUpdate, onSplit, onClose, onToggleMax }: Props) {
  const [menu, setMenu] = useState(false)
  const showData = node.type !== 'empty'
  const typeLabel = node.type === 'empty' ? 'panel' : (TYPES.find((t) => t.type === node.type)?.label ?? node.type)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minWidth: 0, minHeight: 0, background: 'var(--panel)' }}>
      {/* tab bar */}
      <div style={bar}>
        <Icon g={G.bybit} size={13} color="#f7a600" />
        {/* type dropdown */}
        <span style={{ position: 'relative' }}>
          <span onClick={() => setMenu((m) => !m)} style={{ cursor: 'pointer', color: 'var(--text)', fontWeight: 600 }}>
            {typeLabel} <span style={{ color: 'var(--dim)', fontSize: 9 }}>▾</span>
          </span>
          {menu && (
            <div style={dropdown} onMouseLeave={() => setMenu(false)}>
              {TYPES.map((t) => (
                <div key={t.type} onClick={() => { onUpdate({ type: t.type }); setMenu(false) }} style={item(node.type === t.type)}>
                  {t.label}
                </div>
              ))}
            </div>
          )}
        </span>

        {showData && (
          <>
            <select value={node.symbol} onChange={(e) => onUpdate({ symbol: e.target.value })} style={sel}>
              {SYMBOLS.map((s) => <option key={s} value={s}>{s.replace('USDT', '')} · PERP</option>)}
            </select>
            {node.type !== 'dom' && (
              <div style={{ display: 'flex', gap: 2 }}>
                {INTERVALS.map((i) => (
                  <span
                    key={i}
                    onClick={() => onUpdate({ interval: i })}
                    style={pill(node.interval === i)}
                  >
                    {intervalLabel(i)}
                  </span>
                ))}
              </div>
            )}
          </>
        )}

        <div style={{ flex: 1 }} />
        <Icon g={G.clone} size={13} title="Dividir panel" onClick={onSplit} />
        <Icon g={isMax ? G.resizeSmall : G.resizeFull} size={13} title={isMax ? 'Restaurar' : 'Maximizar'} onClick={onToggleMax} />
        {canClose && <Icon g={G.close} size={13} color="#6e3a3a" title="Cerrar" onClick={onClose} />}
      </div>

      {/* body */}
      <div style={{ flex: 1, minHeight: 0, minWidth: 0 }}>
        {node.type === 'empty' && <StarterPane onPick={(t) => onUpdate({ type: t })} />}
        {node.type === 'candles' && <CandlePane symbol={node.symbol} interval={node.interval} />}
        {node.type === 'line' && <LinePane symbol={node.symbol} interval={node.interval} />}
        {node.type === 'dom' && <DomPane symbol={node.symbol} />}
        {node.type === 'footprint' && <FootprintPane symbol={node.symbol} interval={node.interval} />}
        {node.type === 'heatmap' && <HeatmapPane symbol={node.symbol} />}
        {node.type === 'timeandsales' && <TimeSalesPane symbol={node.symbol} />}
        {node.type === 'comparison' && <ComparisonPane interval={node.interval} />}
      </div>
    </div>
  )
}

const bar: CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 8, height: 30, padding: '0 10px',
  borderBottom: '1px solid var(--border)', fontSize: 11.5, flexShrink: 0, background: 'var(--tabbar)',
}
const sel: CSSProperties = {
  background: 'transparent', color: 'var(--muted)', border: '1px solid var(--border2)', borderRadius: 3,
  fontSize: 11, padding: '2px 4px', fontFamily: 'inherit', cursor: 'pointer',
}
const pill = (active: boolean): CSSProperties => ({
  cursor: 'pointer', fontSize: 10.5, padding: '2px 6px', borderRadius: 3,
  color: active ? 'var(--text)' : 'var(--dim)',
  background: active ? '#1c2530' : 'transparent',
})
const dropdown: CSSProperties = {
  position: 'absolute', top: 20, left: 0, zIndex: 30, background: 'var(--panel)',
  border: '1px solid var(--border2)', borderRadius: 5, minWidth: 110, padding: 4,
  boxShadow: '0 6px 20px rgba(0,0,0,0.5)',
}
const item = (active: boolean): CSSProperties => ({
  padding: '5px 9px', fontSize: 11.5, cursor: 'pointer', borderRadius: 3,
  color: active ? 'var(--green)' : 'var(--muted)',
})
