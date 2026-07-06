// Sidebar — Flowsurface-style vertical icon strip on the far left.
// Every icon here does something real: quick global symbol switch, sound-alert
// toggle (big-print beep in Time & Sales), reset layout to default.
import { useEffect, useState } from 'react'
import { Icon, G } from '../ui/icons'
import { isSoundEnabled, onSoundChange, toggleSound } from '../lib/sound'

const SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']

export default function Sidebar({
  onAddPane, onGlobalSymbol, onResetLayout,
}: {
  onAddPane: () => void
  onGlobalSymbol: (symbol: string) => void
  onResetLayout: () => void
}) {
  const [search, setSearch] = useState(false)
  const [sound, setSound] = useState(isSoundEnabled())
  useEffect(() => {
    return onSoundChange(setSound)
  }, [])

  return (
    <div
      style={{
        width: 42, flexShrink: 0, background: 'var(--panel)', borderRight: '1px solid var(--border)',
        display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '10px 0', gap: 16,
        position: 'relative',
      }}
    >
      <span style={{ position: 'relative' }}>
        <Icon g={G.search} size={16} title="Cambiar símbolo en todos los paneles" onClick={() => setSearch((s) => !s)} />
        {search && (
          <div
            onMouseLeave={() => setSearch(false)}
            style={{
              position: 'absolute', top: 0, left: 34, zIndex: 40, background: 'var(--panel)',
              border: '1px solid var(--border2)', borderRadius: 5, minWidth: 110, padding: 4,
              boxShadow: '0 6px 20px rgba(0,0,0,0.5)',
            }}
          >
            {SYMBOLS.map((s) => (
              <div
                key={s}
                onClick={() => { onGlobalSymbol(s); setSearch(false) }}
                style={{ padding: '5px 9px', fontSize: 11.5, cursor: 'pointer', borderRadius: 3, color: 'var(--muted)' }}
              >
                {s.replace('USDT', '')} · PERP
              </div>
            ))}
          </div>
        )}
      </span>
      <Icon g={G.layout} size={16} title="Agregar panel" onClick={onAddPane} />
      <Icon g={G.volumeHigh} size={16} title={sound ? 'Sonido: activado (click para silenciar)' : 'Sonido: silenciado'} color={sound ? 'var(--green)' : undefined} onClick={toggleSound} />
      <div style={{ flex: 1 }} />
      <Icon g={G.cog} size={16} title="Restaurar layout por defecto" onClick={onResetLayout} />
    </div>
  )
}
