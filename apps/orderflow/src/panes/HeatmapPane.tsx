// HeatmapPane — order-book depth heatmap (Flowsurface-style): bids (green, below)
// vs asks (red, above) drawn as horizontal time-runs so the price path emerges at
// the bid/ask boundary; trade bubbles sized by qty; volume histogram at the bottom.
// Mouse wheel = zoom price, drag = pan. Live from Bybit.
import { useEffect, useRef } from 'react'
import { BybitWS, type BybitBook } from '../lib/bybitWs'
import { ConfluenceDetector } from '../lib/confluence'
import { T } from '../theme'

interface Snap { t: number; book: BybitBook; mid: number }
interface Bubble { t: number; price: number; qty: number; buy: boolean }

const MAX_SNAPS = 900
const VOL_H = 46

export default function HeatmapPane({ symbol }: { symbol: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const snaps = useRef<Snap[]>([])
  const bubbles = useRef<Bubble[]>([])
  const view = useRef({ zoom: 1, panY: 0 }) // zoom>1 = closer; panY in price units

  useEffect(() => {
    snaps.current = []
    bubbles.current = []
    view.current = { zoom: 1, panY: 0 }
    let lastMid = 0
    let lastPush = 0
    const detector = new ConfluenceDetector(symbol)

    const ws = new BybitWS(symbol, '5', {
      onTrade: (tr) => {
        lastMid = tr.price
        bubbles.current.push({ t: tr.ts, price: tr.price, qty: tr.qty, buy: tr.side === 'Buy' })
        if (bubbles.current.length > 1500) bubbles.current.shift()
        detector.onTrade(tr)
      },
      onBook: (b) => {
        const now = Date.now()
        detector.onBook(b, now)
        if (now - lastPush < 350) return
        lastPush = now
        const mid = b.bids[0] && b.asks[0] ? (b.bids[0].price + b.asks[0].price) / 2 : lastMid
        snaps.current.push({ t: now, book: b, mid })
        if (snaps.current.length > MAX_SNAPS) snaps.current.shift()
      },
    })
    ws.connect()

    let raf = 0
    const loop = () => {
      draw(canvasRef.current, snaps.current, bubbles.current, view.current, detector.currentWalls())
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => { cancelAnimationFrame(raf); ws.close() }
  }, [symbol])

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    const f = e.deltaY < 0 ? 1.12 : 1 / 1.12
    view.current.zoom = Math.max(0.3, Math.min(8, view.current.zoom * f))
  }
  const onPointerDown = (e: React.PointerEvent) => {
    const el = canvasRef.current!
    const rect = el.getBoundingClientRect()
    const onPriceAxis = e.clientX - rect.left >= rect.width - 64
    const sy = e.clientY
    const p0 = { ...view.current }
    const move = (ev: PointerEvent) => {
      if (onPriceAxis) {
        view.current.zoom = Math.max(0.3, Math.min(10, p0.zoom * Math.exp(-(ev.clientY - sy) / 220)))
      } else {
        const range = lastRange.current
        if (!range) return
        view.current.panY = p0.panY + ((ev.clientY - sy) / rect.height) * (range.max - range.min)
      }
    }
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }
  const onMouseMove = (e: React.MouseEvent) => {
    const el = canvasRef.current
    if (!el) return
    const rect = el.getBoundingClientRect()
    el.style.cursor = e.clientX - rect.left >= rect.width - 64 ? 'ns-resize' : 'grab'
  }

  return (
    <div style={{ width: '100%', height: '100%', background: T.panel }}>
      <canvas
        ref={canvasRef}
        onWheel={onWheel}
        onPointerDown={onPointerDown}
        onMouseMove={onMouseMove}
        style={{ width: '100%', height: '100%', display: 'block', cursor: 'grab' }}
      />
    </div>
  )
}

const lastRange = { current: null as { min: number; max: number } | null }

type Walls = { bid: { price: number; qty: number; since: number | null }; ask: { price: number; qty: number; since: number | null }; persist: number }

function draw(canvas: HTMLCanvasElement | null, snaps: Snap[], bubbles: Bubble[], view: { zoom: number; panY: number }, walls?: Walls) {
  if (!canvas || !snaps.length) return
  const dpr = window.devicePixelRatio || 1
  const w = canvas.clientWidth, h = canvas.clientHeight
  if (canvas.width !== w * dpr || canvas.height !== h * dpr) { canvas.width = w * dpr; canvas.height = h * dpr }
  const ctx = canvas.getContext('2d')!
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.fillStyle = T.panel
  ctx.fillRect(0, 0, w, h)

  const priceAxis = 64
  const plotW = w - priceAxis
  const plotH = h - VOL_H

  // ── price range from the mid path over the window (this is what makes the
  //    snaking price line visible), then apply user zoom/pan ────────────────
  let lo = Infinity, hi = -Infinity
  for (const s of snaps) { if (s.mid) { lo = Math.min(lo, s.mid); hi = Math.max(hi, s.mid) } }
  if (!isFinite(lo) || hi <= lo) { const m = snaps[snaps.length - 1].mid || 1; lo = m * 0.999; hi = m * 1.001 }
  const center0 = (lo + hi) / 2
  const span0 = (hi - lo) * 1.35 // padding
  const span = span0 / view.zoom
  const center = center0 - view.panY
  const maxP = center + span / 2, minP = center - span / 2
  lastRange.current = { min: minP, max: maxP }
  const yFor = (p: number) => ((maxP - p) / (maxP - minP)) * plotH
  const inRange = (p: number) => p >= minP && p <= maxP

  const nCols = Math.min(snaps.length, plotW)
  const vis = snaps.slice(-nCols)
  const colW = plotW / vis.length
  const t0 = vis[0].t, t1 = vis[vis.length - 1].t
  const xForT = (t: number) => (t1 > t0 ? ((t - t0) / (t1 - t0)) * plotW : plotW)

  // global max depth qty for brightness
  let maxQ = 0
  for (const s of vis) {
    for (const l of s.book.bids) if (inRange(l.price)) maxQ = Math.max(maxQ, l.qty)
    for (const l of s.book.asks) if (inRange(l.price)) maxQ = Math.max(maxQ, l.qty)
  }
  if (maxQ <= 0) maxQ = 1
  const cellH = Math.max(1.5, (span / 300) / (span) * plotH) // ~fixed pixel cell

  // ── depth heatmap ─────────────────────────────────────────────────────────
  vis.forEach((s, i) => {
    const x = i * colW
    const cw = Math.max(1, colW + 0.6)
    const put = (l: { price: number; qty: number }, bid: boolean) => {
      if (!inRange(l.price)) return
      const a = Math.min(0.92, 0.06 + 0.9 * (l.qty / maxQ))
      ctx.fillStyle = bid ? `rgba(81,205,160,${a})` : `rgba(192,80,77,${a})`
      ctx.fillRect(x, yFor(l.price) - cellH / 2, cw, cellH)
    }
    for (const l of s.book.bids) put(l, true)
    for (const l of s.book.asks) put(l, false)
  })

  // ── trade bubbles + connecting price path (the "snake") ───────────────────
  let maxTQ = 0
  const visT: Bubble[] = []
  for (const b of bubbles) { if (b.t >= t0 && inRange(b.price)) { visT.push(b); maxTQ = Math.max(maxTQ, b.qty) } }
  if (visT.length > 1) {
    ctx.beginPath()
    ctx.moveTo(xForT(visT[0].t), yFor(visT[0].price))
    for (let i = 1; i < visT.length; i++) ctx.lineTo(xForT(visT[i].t), yFor(visT[i].price))
    ctx.strokeStyle = 'rgba(197,201,197,0.35)'
    ctx.lineWidth = 1
    ctx.setLineDash([2, 2])
    ctx.stroke()
    ctx.setLineDash([])
  }
  if (maxTQ > 0) {
    for (const b of visT) {
      const r = 1.5 + Math.sqrt(b.qty / maxTQ) * 9
      ctx.beginPath()
      ctx.arc(xForT(b.t), yFor(b.price), r, 0, Math.PI * 2)
      ctx.fillStyle = b.buy ? 'rgba(81,205,160,0.55)' : 'rgba(192,80,77,0.55)'
      ctx.fill()
      ctx.lineWidth = 1
      ctx.strokeStyle = b.buy ? 'rgba(81,205,160,0.9)' : 'rgba(192,80,77,0.9)'
      ctx.stroke()
    }
  }

  // ── liquidity walls (from the confluence detector) ─────────────────────────
  if (walls) {
    const now = snaps[snaps.length - 1]?.t ?? 0
    const drawWall = (w: Walls['bid'], bid: boolean) => {
      if (w.qty <= 0 || !inRange(w.price)) return
      const persisted = w.since != null && now - w.since >= walls.persist
      const y = yFor(w.price)
      ctx.strokeStyle = bid ? (persisted ? 'rgba(81,205,160,0.9)' : 'rgba(81,205,160,0.4)') : (persisted ? 'rgba(192,80,77,0.9)' : 'rgba(192,80,77,0.4)')
      ctx.lineWidth = persisted ? 2 : 1
      ctx.setLineDash(persisted ? [] : [4, 3])
      ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(plotW, y); ctx.stroke()
      ctx.setLineDash([])
      ctx.font = '9px "Azeret Mono", monospace'
      ctx.fillStyle = bid ? T.green : T.red
      ctx.textAlign = 'left'
      ctx.fillText(`${bid ? 'muro bid' : 'muro ask'} ${w.qty.toFixed(1)}`, 4, y - 5)
    }
    drawWall(walls.bid, true)
    drawWall(walls.ask, false)
  }

  // ── volume histogram (bottom) ─────────────────────────────────────────────
  ctx.fillStyle = T.grid
  ctx.fillRect(0, plotH, plotW, 1)
  const buckets = new Map<number, { buy: number; sell: number }>()
  for (const b of bubbles) {
    if (b.t < t0) continue
    const bx = Math.floor(xForT(b.t) / 3)
    const v = buckets.get(bx) ?? { buy: 0, sell: 0 }
    if (b.buy) v.buy += b.qty; else v.sell += b.qty
    buckets.set(bx, v)
  }
  let maxV = 0
  for (const v of buckets.values()) maxV = Math.max(maxV, v.buy + v.sell)
  if (maxV > 0) {
    for (const [bx, v] of buckets) {
      const x = bx * 3
      const total = v.buy + v.sell
      const bh = (total / maxV) * (VOL_H - 4)
      ctx.fillStyle = v.buy >= v.sell ? 'rgba(81,205,160,0.6)' : 'rgba(192,80,77,0.6)'
      ctx.fillRect(x, h - bh, 2.4, bh)
    }
  }

  // ── price axis ────────────────────────────────────────────────────────────
  ctx.fillStyle = T.panel
  ctx.fillRect(w - priceAxis, 0, priceAxis, h)
  ctx.strokeStyle = T.border
  ctx.beginPath(); ctx.moveTo(w - priceAxis, 0); ctx.lineTo(w - priceAxis, h); ctx.stroke()
  ctx.font = '9px "Azeret Mono", monospace'
  ctx.textAlign = 'left'
  ctx.textBaseline = 'middle'
  const lastMid = vis[vis.length - 1].mid
  const lastY = inRange(lastMid) ? yFor(lastMid) : -1
  for (let s = 0; s <= 10; s++) {
    const p = minP + (s / 10) * (maxP - minP)
    const y = yFor(p)
    if (lastY >= 0 && Math.abs(y - lastY) < 10) continue // avoid overlap with last-price badge
    ctx.fillStyle = T.dim
    ctx.fillText(p.toFixed(1), w - priceAxis + 6, y)
  }
  // last price marker
  if (lastY >= 0) {
    ctx.fillStyle = T.green
    ctx.fillRect(w - priceAxis, lastY - 8, priceAxis, 16)
    ctx.fillStyle = '#181616'
    ctx.fillText(lastMid.toFixed(1), w - priceAxis + 6, lastY)
  }
}
