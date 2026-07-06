// icons.tsx — Flowsurface icon glyphs (from icons.ttf) + a tiny Icon component.
import type { CSSProperties } from 'react'

const c = (code: number) => String.fromCharCode(code)

// Codepoints straight from Flowsurface's fontello.json (icons.ttf).
export const G = {
  search: c(0xe802),
  resizeFull: c(0xe803),
  resizeSmall: c(0xe804),
  close: c(0xe805),
  layout: c(0xe806),
  link: c(0xe807),
  bybit: c(0xe808),
  starEmpty: c(0xe80a),
  star: c(0xe80b),
  popup: c(0xe80d),
  chart: c(0xe80e),
  cog: c(0xe810),
  pencil: c(0xe811),
  volumeOff: c(0xe814),
  volumeHigh: c(0xe815),
  volumeMid: c(0xe816),
  drag: c(0xe817),
  clone: c(0xf0c5),
  folder: c(0xf114),
}

export function Icon({
  g, size = 13, color = 'var(--dim)', title, onClick, style,
}: {
  g: string
  size?: number
  color?: string
  title?: string
  onClick?: () => void
  style?: CSSProperties
}) {
  return (
    <span
      className="icon"
      title={title}
      onClick={onClick}
      onMouseEnter={onClick ? (e) => (e.currentTarget.style.color = 'var(--text)') : undefined}
      onMouseLeave={onClick ? (e) => (e.currentTarget.style.color = color) : undefined}
      style={{ fontSize: size, color, cursor: onClick ? 'pointer' : 'default', transition: 'color .1s', ...style }}
    >
      {g}
    </span>
  )
}
