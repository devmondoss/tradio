// StarterPane — empty pane; pick a view type (like Flowsurface's starter).
import type { PaneType } from '../layout/tree'

const OPTIONS: { type: PaneType; label: string }[] = [
  { type: 'candles', label: 'Candlestick' },
  { type: 'footprint', label: 'Footprint' },
  { type: 'heatmap', label: 'Heatmap' },
  { type: 'dom', label: 'DOM / Ladder' },
  { type: 'timeandsales', label: 'Time & Sales' },
  { type: 'comparison', label: 'Comparación' },
  { type: 'line', label: 'Línea' },
]

export default function StarterPane({ onPick }: { onPick: (t: PaneType) => void }) {
  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
      <div style={{ color: '#454d58', fontSize: 12, marginBottom: 4 }}>Elegí una vista</div>
      {OPTIONS.map((o) => (
        <button
          key={o.type}
          onClick={() => onPick(o.type)}
          style={{
            width: 190, padding: '7px 12px', borderRadius: 5, fontSize: 12,
            cursor: 'pointer', fontFamily: 'inherit',
            background: 'var(--tabbar)', border: '1px solid var(--border2)', color: 'var(--muted)',
          }}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}
