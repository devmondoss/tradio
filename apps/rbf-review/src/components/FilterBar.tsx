import { useEffect, useRef, useState } from 'react'
import type { Trade } from '../lib/types'
import { sesLabel } from '../lib/utils'

export interface Filters {
  syms:     Set<string>
  sessions: Set<string>
  dirs:     Set<string>
  results:  Set<string>
}

export function emptyFilters(): Filters {
  return { syms: new Set(), sessions: new Set(), dirs: new Set(), results: new Set() }
}

export function countActive(f: Filters) {
  return f.syms.size + f.sessions.size + f.dirs.size + f.results.size
}

export function applyFilters(trades: Trade[], f: Filters): Trade[] {
  return trades.filter(t => {
    if (f.syms.size     && !f.syms.has(t.sym))        return false
    if (f.sessions.size && !f.sessions.has(t.session)) return false
    if (f.dirs.size     && !f.dirs.has(t.dir))         return false
    if (f.results.size) {
      const r = t.isOpen ? 'open' : (t.resultR ?? 0) > 0 ? 'win' : 'loss'
      if (!f.results.has(r)) return false
    }
    return true
  })
}

function toggle<T>(set: Set<T>, val: T): Set<T> {
  const next = new Set(set)
  next.has(val) ? next.delete(val) : next.add(val)
  return next
}

// ─── pill ────────────────────────────────────────────────────────────────────

function Pill({ label, active, color, onClick }: {
  label: string; active: boolean; color: string; onClick: () => void
}) {
  return (
    <button onClick={onClick} style={{
      padding: '3px 9px', borderRadius: 4, fontSize: 10, fontWeight: 600,
      cursor: 'pointer', fontFamily: 'inherit', lineHeight: 1.4,
      background: active ? color : 'var(--bg)',
      border: `1px solid ${active ? color : 'var(--border2)'}`,
      color: active ? '#fff' : 'var(--text)',
      opacity: active ? 1 : 0.7,
    }}>
      {label}
    </button>
  )
}

function Group({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <span style={{ fontSize: 9, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: 1 }}>
        {label}
      </span>
      <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
        {children}
      </div>
    </div>
  )
}

// ─── main ─────────────────────────────────────────────────────────────────────

interface Props {
  trades:   Trade[]
  filters:  Filters
  filtered: Trade[]
  onChange: (f: Filters) => void
}

const SES_ORDER  = ['London', 'LondonNyOverlap', 'NewYork', 'Asia', 'SessionEnd']
const SES_COLOR: Record<string, string> = {
  London:          '#388bfd',
  LondonNyOverlap: '#8b5cf6',
  NewYork:         '#3fb950',
  Asia:            '#d29922',
  SessionEnd:      '#6e7681',
}

export default function FilterBar({ trades, filters, filtered, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const panelRef = useRef<HTMLDivElement>(null)
  const btnRef   = useRef<HTMLButtonElement>(null)

  const active  = countActive(filters)
  const syms    = [...new Set(trades.map(t => t.sym))].sort()
  const sessions = SES_ORDER.filter(s => trades.some(t => t.session === s))
  const dirs    = (['Short', 'Long'] as const).filter(d => trades.some(t => t.dir === d))

  // close on outside click
  useEffect(() => {
    if (!open) return
    function onDown(e: MouseEvent) {
      if (
        panelRef.current && !panelRef.current.contains(e.target as Node) &&
        btnRef.current   && !btnRef.current.contains(e.target as Node)
      ) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  return (
    <div style={{ position: 'relative' }}>
      {/* trigger button — lives in header row */}
      <button
        ref={btnRef}
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'flex', alignItems: 'center', gap: 5,
          padding: '3px 8px', borderRadius: 4, fontSize: 10,
          cursor: 'pointer', fontFamily: 'inherit',
          background: open || active > 0 ? 'var(--bg3)' : 'transparent',
          border: `1px solid ${open || active > 0 ? 'var(--border2)' : 'transparent'}`,
          color: active > 0 ? 'var(--text)' : 'var(--text2)',
        }}
      >
        <span>⚙</span>
        <span>Filtros</span>
        {active > 0 && (
          <span style={{
            background: 'var(--blue)', color: '#fff',
            borderRadius: 8, padding: '0 5px', fontSize: 8, fontWeight: 700,
          }}>{active}</span>
        )}
        {active > 0 && (
          <span style={{ color: 'var(--text3)', fontSize: 9 }}>
            {filtered.length}/{trades.length}
          </span>
        )}
      </button>

      {/* dropdown panel */}
      {open && (
        <div
          ref={panelRef}
          style={{
            position: 'absolute', top: 'calc(100% + 6px)', right: 0,
            zIndex: 50,
            background: 'var(--bg2)',
            border: '1px solid var(--border2)',
            borderRadius: 6,
            padding: '12px 14px',
            display: 'flex', flexDirection: 'column', gap: 12,
            minWidth: 280,
            boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
          }}
        >
          <Group label="Simbolo">
            {syms.map(s => (
              <Pill key={s}
                label={s.replace('USDT', '')}
                active={filters.syms.has(s)}
                color="#388bfd"
                onClick={() => onChange({ ...filters, syms: toggle(filters.syms, s) })}
              />
            ))}
          </Group>

          <Group label="Sesion">
            {sessions.map(s => (
              <Pill key={s}
                label={sesLabel(s)}
                active={filters.sessions.has(s)}
                color={SES_COLOR[s]}
                onClick={() => onChange({ ...filters, sessions: toggle(filters.sessions, s) })}
              />
            ))}
          </Group>

          <Group label="Direccion">
            {dirs.map(d => (
              <Pill key={d}
                label={d}
                active={filters.dirs.has(d)}
                color={d === 'Short' ? '#f85149' : '#3fb950'}
                onClick={() => onChange({ ...filters, dirs: toggle(filters.dirs, d) })}
              />
            ))}
          </Group>

          <Group label="Resultado">
            {(['win', 'loss', 'open'] as const).map(r => (
              <Pill key={r}
                label={{ win: 'Win', loss: 'Loss', open: 'Open' }[r]}
                active={filters.results.has(r)}
                color={{ win: '#3fb950', loss: '#f85149', open: '#388bfd' }[r]}
                onClick={() => onChange({ ...filters, results: toggle(filters.results, r) })}
              />
            ))}
          </Group>

          {active > 0 && (
            <button
              onClick={() => { onChange(emptyFilters()); setOpen(false) }}
              style={{
                padding: '4px', borderRadius: 4, fontSize: 10, fontWeight: 600,
                cursor: 'pointer', fontFamily: 'inherit',
                background: 'transparent', border: '1px solid var(--border2)',
                color: 'var(--text2)', marginTop: 2,
              }}
            >
              Limpiar filtros
            </button>
          )}
        </div>
      )}
    </div>
  )
}
