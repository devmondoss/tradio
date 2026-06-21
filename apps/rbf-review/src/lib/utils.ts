import type { Trade } from './types'

export function sesLabel(s: string) {
  if (s === 'LondonNyOverlap') return 'Overlap'
  if (s === 'NewYork') return 'NY'
  return s || '?'
}

export function fmtR(r: number | null) {
  if (r == null) return '—'
  return (r >= 0 ? '+' : '') + r.toFixed(3) + 'R'
}

export function fmtUsd(n: number) {
  return (n >= 0 ? '+$' : '-$') + Math.abs(n).toFixed(3)
}

export function fmtDur(m: number | null) {
  if (m == null) return '—'
  if (m < 60) return m + 'm'
  return Math.floor(m / 60) + 'h ' + (m % 60) + 'm'
}

export function fmtDate(ms: number) {
  const d = new Date(ms)
  return `${String(d.getUTCMonth()+1).padStart(2,'0')}-${String(d.getUTCDate()).padStart(2,'0')} ${String(d.getUTCHours()).padStart(2,'0')}:${String(d.getUTCMinutes()).padStart(2,'0')}`
}

export function winRate(trades: Trade[]) {
  const closed = trades.filter(t => !t.isOpen)
  if (!closed.length) return 0
  return closed.filter(t => (t.resultR ?? 0) > 0).length / closed.length * 100
}

export function avgR(trades: Trade[]) {
  const closed = trades.filter(t => !t.isOpen && t.resultR != null)
  if (!closed.length) return 0
  return closed.reduce((s, t) => s + (t.resultR ?? 0), 0) / closed.length
}
