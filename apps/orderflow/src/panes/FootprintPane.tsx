// FootprintPane — custom-canvas footprint (bid×ask volume per price level inside
// each candle) built live from Bybit trades.
//
// Bins are ACCUMULATED per candle: a closed candle is frozen and never recomputed
// (fixes "closed candle values move"). Raw trades are also kept so a timeframe
// change re-buckets without a full reset. Mouse wheel zooms, drag pans.
import { useEffect, useRef } from 'react'
import { BybitWS, type BybitTrade } from '../lib/bybitWs'
import { fetchLinearKlines, intervalSec } from '../lib/klines'
import { T } from '../theme'

interface Bin { buy: number; sell: number }
interface FC { start: number; open: number; high: number; low: number; close: number; bins: Map<number, Bin>; total: number }

export default function FootprintPane({ symbol, interval }: { symbol: string; interval: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const raw = useRef<BybitTrade[]>([])          // raw trades (for timeframe rebuild)
  const candles = useRef<Map<number, FC>>(new Map()) // accumulated, closed = frozen
  const binSize = useRef(0)
  const secs = useRef(intervalSec(interval))
  const view = useRef({ zoom: 1, panY: 0, panX: 0 })

  const add = (t: BybitTrade) => {
    if (!binSize.current) binSize.current = Math.max(t.price * 0.0002, 0.1)
    const start = Math.floor(t.ts / 1000 / secs.current) * secs.current
    let c = candles.current.get(start)
    if (!c) {
      c = { start, open: t.price, high: t.price, low: t.price, close: t.price, bins: new Map(), total: 0 }
      candles.current.set(start, c)
      if (candles.current.size > 400) candles.current.delete(Math.min(...candles.current.keys()))
    }
    c.high = Math.max(c.high, t.price); c.low = Math.min(c.low, t.price); c.close = t.price
    const k = Math.round(t.price / binSize.current)
    const b = c.bins.get(k) ?? { buy: 0, sell: 0 }
    if (t.side === 'Sell') b.sell += t.qty; else b.buy += t.qty
    c.bins.set(k, b); c.total += t.qty
  }

  // seed empty (no-footprint) OHLC shells from REST so the pane isn't blank
  // on load / timeframe change — bins fill in for real as live trades arrive.
  const seedHistorical = async (sym: string, intv: string) => {
    const bars = await fetchLinearKlines(sym, intv, 300)
    if (!binSize.current && bars.length) binSize.current = Math.max(bars[bars.length - 1].close * 0.0002, 0.1)
    for (const k of bars) {
      if (candles.current.has(k.time)) continue
      candles.current.set(k.time, { start: k.time, open: k.open, high: k.high, low: k.low, close: k.close, bins: new Map(), total: 0 })
    }
  }

  // interval change: re-bucket the raw buffer once (no reset), then backfill history
  useEffect(() => {
    secs.current = intervalSec(interval)
    candles.current = new Map()
    seedHistorical(symbol, interval)
    for (const t of raw.current) add(t)
    view.current.panX = 0
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [interval])

  // symbol change: full reset + reconnect + backfill history
  useEffect(() => {
    raw.current = []
    candles.current = new Map()
    binSize.current = 0
    view.current = { zoom: 1, panY: 0, panX: 0 }
    seedHistorical(symbol, interval)
    const ws = new BybitWS(symbol, '5', {
      onTrade: (t) => {
        raw.current.push(t)
        if (raw.current.length > 60000) raw.current.shift()
        add(t)
      },
    })
    ws.connect()
    let rafId = 0
    const loop = () => { draw(canvasRef.current, candles.current, binSize.current, view.current); rafId = requestAnimationFrame(loop) }
    rafId = requestAnimationFrame(loop)
    return () => { cancelAnimationFrame(rafId); ws.close() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol])

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault()
    const f = e.deltaY < 0 ? 1.12 : 1 / 1.12
    view.current.zoom = Math.max(0.4, Math.min(10, view.current.zoom * f))
  }
  const onPointerDown = (e: React.PointerEvent) => {
    const el = canvasRef.current!
    const rect = el.getBoundingClientRect()
    const onPriceAxis = e.clientX - rect.left >= rect.width - 60
    const sx = e.clientX, sy = e.clientY
    const p0 = { ...view.current }
    const move = (ev: PointerEvent) => {
      if (onPriceAxis) {
        // drag on the price axis scales price (zoom), like TradingView
        view.current.zoom = Math.max(0.4, Math.min(12, p0.zoom * Math.exp(-(ev.clientY - sy) / 220)))
      } else {
        const r = lastRange.current
        view.current.panX = p0.panX + (ev.clientX - sx)
        if (r) view.current.panY = p0.panY + ((ev.clientY - sy) / rect.height) * (r.max - r.min)
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
    el.style.cursor = e.clientX - rect.left >= rect.width - 60 ? 'ns-resize' : 'grab'
  }

  return (
    <div style={{ width: '100%', height: '100%', background: T.panel }}>
      <canvas ref={canvasRef} onWheel={onWheel} onPointerDown={onPointerDown} onMouseMove={onMouseMove}
        style={{ width: '100%', height: '100%', display: 'block', cursor: 'grab' }} />
    </div>
  )
}

const lastRange = { current: null as { min: number; max: number } | null }

function draw(canvas: HTMLCanvasElement | null, cmap: Map<number, FC>, binSize: number, view: { zoom: number; panY: number; panX: number }) {
  if (!canvas || !binSize) return
  const dpr = window.devicePixelRatio || 1
  const w = canvas.clientWidth, h = canvas.clientHeight
  if (canvas.width !== w * dpr || canvas.height !== h * dpr) { canvas.width = w * dpr; canvas.height = h * dpr }
  const ctx = canvas.getContext('2d')!
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.fillStyle = T.panel
  ctx.fillRect(0, 0, w, h)

  const all = [...cmap.values()].sort((a, b) => a.start - b.start)
  if (!all.length) return

  const priceAxis = 60
  const VOL_H = 34
  const plotW = w - priceAxis
  const plotH = h - VOL_H
  const colW = Math.min(230, Math.max(46, 90 * Math.sqrt(view.zoom)))
  const nFit = Math.max(1, Math.floor(plotW / colW))
  const shift = Math.round(-view.panX / colW)
  const end = Math.min(all.length, Math.max(nFit, all.length - Math.max(0, shift)))
  const vis = all.slice(Math.max(0, end - nFit), end)
  if (!vis.length) return

  let maxP = -Infinity, minP = Infinity
  for (const c of vis) { maxP = Math.max(maxP, c.high); minP = Math.min(minP, c.low) }
  const center0 = (maxP + minP) / 2
  const span0 = (maxP - minP) * 1.15 || center0 * 0.002
  const span = span0 / view.zoom
  const center = center0 - view.panY
  maxP = center + span / 2; minP = center - span / 2
  lastRange.current = { min: minP, max: maxP }
  const yFor = (p: number) => ((maxP - p) / (maxP - minP)) * plotH
  const cellH = Math.max(1, (binSize / (maxP - minP)) * plotH)
  const showText = cellH >= 8.5 && colW >= 60

  ctx.font = '9px "Azeret Mono", monospace'
  ctx.textBaseline = 'middle'

  const startX = plotW - vis.length * colW // right-align (newest hugs the right)
  vis.forEach((c, i) => {
    const x0 = startX + i * colW
    const cx = x0 + colW / 2
    ctx.strokeStyle = T.faint
    ctx.beginPath(); ctx.moveTo(cx, yFor(c.high)); ctx.lineTo(cx, yFor(c.low)); ctx.stroke()
    const up = c.close >= c.open
    ctx.fillStyle = up ? 'rgba(81,205,160,0.30)' : 'rgba(192,80,77,0.30)'
    const yo = yFor(c.open), yc = yFor(c.close)
    ctx.fillRect(cx - 2, Math.min(yo, yc), 4, Math.max(2, Math.abs(yc - yo)))
    let pocK = 0, pocV = -1
    for (const [k, b] of c.bins) { const v = b.buy + b.sell; if (v > pocV) { pocV = v; pocK = k } }
    let cellMax = 0
    for (const b of c.bins.values()) cellMax = Math.max(cellMax, b.buy + b.sell)
    for (const [k, b] of c.bins) {
      const y = yFor(k * binSize)
      if (y < -cellH || y > plotH + cellH) continue
      const delta = b.buy - b.sell
      const inten = cellMax > 0 ? Math.min(0.5, 0.08 + 0.42 * ((b.buy + b.sell) / cellMax)) : 0.1
      ctx.fillStyle = delta >= 0 ? `rgba(81,205,160,${inten})` : `rgba(192,80,77,${inten})`
      ctx.fillRect(x0 + 3, y - cellH / 2, colW - 6, cellH)
      if (k === pocK) { ctx.strokeStyle = 'rgba(230,237,243,0.35)'; ctx.strokeRect(x0 + 3, y - cellH / 2, colW - 6, cellH) }
      if (showText) {
        ctx.textAlign = 'left'; ctx.fillStyle = T.red
        ctx.fillText(b.sell >= 0.01 ? b.sell.toFixed(2) : '', x0 + 7, y)
        ctx.textAlign = 'right'; ctx.fillStyle = T.green
        ctx.fillText(b.buy >= 0.01 ? b.buy.toFixed(2) : '', x0 + colW - 7, y)
      }
    }
  })

  // ── volume histogram (bottom, buy vs sell per candle) ──────────────────────
  ctx.strokeStyle = T.border
  ctx.beginPath(); ctx.moveTo(0, plotH); ctx.lineTo(plotW, plotH); ctx.stroke()
  let maxVol = 0
  for (const c of vis) { let v = 0; for (const b of c.bins.values()) v += b.buy + b.sell; maxVol = Math.max(maxVol, v) }
  vis.forEach((c, i) => {
    const x0 = startX + i * colW
    let buy = 0, sell = 0
    for (const b of c.bins.values()) { buy += b.buy; sell += b.sell }
    const total = buy + sell
    if (maxVol <= 0 || total <= 0) return
    const bh = (total / maxVol) * (VOL_H - 6)
    ctx.fillStyle = buy >= sell ? 'rgba(81,205,160,0.55)' : 'rgba(192,80,77,0.55)'
    ctx.fillRect(x0 + colW * 0.2, h - bh, colW * 0.6, bh)
  })

  ctx.fillStyle = T.panel; ctx.fillRect(w - priceAxis, 0, priceAxis, plotH)
  ctx.strokeStyle = T.border; ctx.beginPath(); ctx.moveTo(w - priceAxis, 0); ctx.lineTo(w - priceAxis, plotH); ctx.stroke()
  ctx.fillStyle = T.dim; ctx.textAlign = 'left'
  for (let s = 0; s <= 9; s++) { const p = minP + (s / 9) * (maxP - minP); ctx.fillText(p.toFixed(1), w - priceAxis + 5, yFor(p)) }
}
