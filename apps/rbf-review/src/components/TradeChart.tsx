import { useEffect, useRef, useState } from 'react'
import { createChart, CandlestickSeries, type IChartApi, type ISeriesApi, type Time } from 'lightweight-charts'
import type { Trade } from '../lib/types'
import { fetchKlines } from '../lib/binance'

interface Props { trade: Trade | null }
type CandleSeries = ISeriesApi<'Candlestick', Time>

// ─── Tool / Drawing types ──────────────────────────────────────────────────────
type Tool =
  | 'cursor'
  | 'shortpos' | 'longpos'
  | 'trendline' | 'ray' | 'extline' | 'hline' | 'vline'
  | 'rect' | 'arrow' | 'text' | 'measure' | 'eraser'

type Drawing =
  | { id: number; type: 'hline';   price: number; color: string }
  | { id: number; type: 'vline';   time: number;  color: string }
  | { id: number; type: 'trendline' | 'ray' | 'extline' | 'arrow'
      t1: number; p1: number; t2: number; p2: number; color: string }
  | { id: number; type: 'rect';   t1: number; p1: number; t2: number; p2: number; color: string }
  | { id: number; type: 'text';   time: number; price: number; label: string; color: string }
  | { id: number; type: 'measure'; t1: number; p1: number; t2: number; p2: number }
  | { id: number; type: 'shortpos' | 'longpos'
      time: number; t2: number; entry: number; stop: number; target: number }

interface HitResult { drawingId: number; handle: string; cursor: string }
interface DragOp    { drawingId: number; handle: string; startPrice: number; startTime: number; snapshot: Drawing }
interface Pending   { t1: number; p1: number; x1: number; y1: number; tool: Tool }
interface TextInput { x: number; y: number; time: number; price: number }

const COLORS = ['#f0c040','#58a6ff','#ff6b6b','#51cf66','#cc5de8','#ff922b','#20c997']

interface ToolDef { icon: string; tip: string }
const TOOL_DEF: Record<Tool, ToolDef> = {
  cursor:    { icon: '↖',   tip: 'Cursor / Seleccionar' },
  shortpos:  { icon: '↓$',  tip: 'Short Position' },
  longpos:   { icon: '↑$',  tip: 'Long Position' },
  trendline: { icon: '╱',   tip: 'Línea de tendencia' },
  ray:       { icon: '—›',  tip: 'Rayo' },
  extline:   { icon: '‹—›', tip: 'Línea extendida' },
  hline:     { icon: '—',   tip: 'Línea horizontal' },
  vline:     { icon: '│',   tip: 'Línea vertical' },
  rect:      { icon: '▭',   tip: 'Rectángulo' },
  arrow:     { icon: '↗',   tip: 'Flecha' },
  text:      { icon: 'T',   tip: 'Texto' },
  measure:   { icon: '⟺',  tip: 'Medida R / %' },
  eraser:    { icon: '◫',   tip: 'Borrador selectivo' },
}
const GROUPS: Tool[][] = [
  ['cursor'],
  ['shortpos','longpos'],
  ['trendline','ray','extline','hline','vline'],
  ['rect'],
  ['arrow','text'],
  ['measure'],
  ['eraser'],
]

let _uid = 0
function deepClone<T>(x: T): T { return JSON.parse(JSON.stringify(x)) }

export default function TradeChart({ trade }: Props) {
  const wrapRef   = useRef<HTMLDivElement>(null)
  const chartRef  = useRef<IChartApi | null>(null)
  const serRef    = useRef<CandleSeries | null>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const tradeRef  = useRef<Trade | null>(null)
  const draws     = useRef<Drawing[]>([])
  const pending   = useRef<Pending | null>(null)
  const dragOp    = useRef<DragOp | null>(null)
  const dragRect  = useRef(false)  // rect tool drag
  const mouse     = useRef({ x: 0, y: 0 })
  const magnetRef = useRef(false)
  const colorIdx  = useRef(0)
  const candlesRef= useRef<{ time: number; open: number; high: number; low: number; close: number }[]>([])
  const toolRef   = useRef<Tool>('cursor')
  const hovHit    = useRef<HitResult | null>(null)  // hover hit in cursor mode

  const [activeTool, _setTool] = useState<Tool>('cursor')
  const [magnet,     setMagnet] = useState(false)
  const [textInput,  setTextInput] = useState<TextInput | null>(null)
  const [, bump] = useState(0)

  function setTool(t: Tool) {
    toolRef.current = t; _setTool(t)
    pending.current = null; hovHit.current = null
    setTextInput(null)
    const c = canvasRef.current
    if (c) c.style.pointerEvents = t === 'cursor' ? 'none' : 'auto'
  }
  function nextColor() { return COLORS[(colorIdx.current++) % COLORS.length] }
  function forceRedraw() { bump(v => v + 1) }

  // ── chart init ──────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!wrapRef.current) return
    const chart = createChart(wrapRef.current, {
      width: wrapRef.current.clientWidth, height: wrapRef.current.clientHeight,
      layout: { background: { color: '#0d1117' }, textColor: '#8b949e' },
      grid: { vertLines: { color: '#21262d' }, horzLines: { color: '#21262d' } },
      crosshair: { mode: 1 },
      timeScale: { borderColor: '#30363d', timeVisible: true, secondsVisible: false },
      rightPriceScale: { borderColor: '#30363d' },
    })
    const series = chart.addSeries(CandlestickSeries, {
      upColor: '#3fb950', downColor: '#f85149',
      borderUpColor: '#3fb950', borderDownColor: '#f85149',
      wickUpColor: '#3fb950', wickDownColor: '#f85149',
    })
    chartRef.current = chart; serRef.current = series

    const ro = new ResizeObserver(() => {
      if (!wrapRef.current) return
      chart.applyOptions({ width: wrapRef.current.clientWidth, height: wrapRef.current.clientHeight })
      syncCanvas(); draw()
    })
    ro.observe(wrapRef.current)
    chart.timeScale().subscribeVisibleLogicalRangeChange(draw)
    chart.subscribeCrosshairMove(draw)

    // ── Dynamic pointer-events toggle in cursor mode ───────────────────────
    // Listens on document so it fires even when canvas is pointer-events:none
    const onDocMove = (e: MouseEvent) => {
      const canvas = canvasRef.current
      if (!canvas || toolRef.current !== 'cursor' || dragOp.current) return
      const rect = canvas.getBoundingClientRect()
      const mx = e.clientX - rect.left, my = e.clientY - rect.top
      const hit = findHit(mx, my)
      const prev = hovHit.current
      hovHit.current = hit
      // Toggle pointer-events: only capture when over a drawing
      canvas.style.pointerEvents = hit ? 'auto' : 'none'
      if ((hit?.drawingId ?? null) !== (prev?.drawingId ?? null) || hit?.handle !== prev?.handle) {
        draw()
      }
    }
    document.addEventListener('mousemove', onDocMove)

    return () => {
      chart.remove(); ro.disconnect()
      document.removeEventListener('mousemove', onDocMove)
    }
  }, [])

  // ── load candles ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!trade) return
    tradeRef.current = trade
    draws.current = []; pending.current = null
    colorIdx.current = 0; hovHit.current = null
    dragOp.current = null
    setTextInput(null); forceRedraw()

    const series = serRef.current, chart = chartRef.current
    if (!series || !chart) return
    fetchKlines(trade.sym, trade.tsMs).then(cs => {
      if (tradeRef.current?.id !== trade.id) return
      candlesRef.current = cs
      series.setData(cs.map(c => ({ ...c, time: c.time as Time })))
      const ei = cs.findIndex(c => c.time >= trade.ts)
      const idx = ei >= 0 ? ei : Math.floor(cs.length / 3)
      chart.timeScale().setVisibleLogicalRange({ from: idx - 35, to: idx + 80 })
      draw()
    })
  }, [trade?.id])

  // ─── Helpers ────────────────────────────────────────────────────────────────
  function syncCanvas() {
    const c = canvasRef.current, w = wrapRef.current
    if (c && w) { c.width = w.clientWidth; c.height = w.clientHeight }
  }
  function cv(v: unknown): number | null {
    return v == null ? null : v as unknown as number
  }
  function snapPoint(x: number, y: number) {
    const chart = chartRef.current, series = serRef.current
    if (!chart || !series) return null
    const rt = cv(chart.timeScale().coordinateToTime(x))
    const rp = cv(series.coordinateToPrice(y))
    if (rt == null || rp == null) return null
    const time = rt as unknown as number, price = rp as unknown as number
    if (!magnetRef.current) return { x, y, time, price }
    const cs = candlesRef.current
    if (!cs.length) return { x, y, time, price }
    const ni = cs.reduce((b, c, i) => Math.abs(c.time-time) < Math.abs(cs[b].time-time) ? i : b, 0)
    const c = cs[ni]
    const sp = [c.open,c.high,c.low,c.close].reduce((a,b)=>Math.abs(a-price)<Math.abs(b-price)?a:b)
    const sy = cv(series.priceToCoordinate(sp)), sx = cv(chart.timeScale().timeToCoordinate(c.time as Time))
    if (sx==null||sy==null) return { x, y, time, price }
    return { x: sx as unknown as number, y: sy as unknown as number, time: c.time, price: sp }
  }
  function getPoint(e: React.MouseEvent<HTMLCanvasElement>) {
    const rect = canvasRef.current!.getBoundingClientRect()
    const x = e.clientX-rect.left, y = e.clientY-rect.top
    return snapPoint(x, y) ?? { x, y, time: 0, price: 0 }
  }

  // ─── Hit testing (for eraser + cursor editing) ───────────────────────────────
  const THRESH = 7
  function findHit(mx: number, my: number): HitResult | null {
    const chart = chartRef.current, series = serRef.current, canvas = canvasRef.current
    if (!chart || !series || !canvas) return null

    for (let i = draws.current.length-1; i >= 0; i--) {
      const d = draws.current[i]

      if (d.type === 'shortpos' || d.type === 'longpos') {
        const entY = cv(series.priceToCoordinate(d.entry))
        const stoY = cv(series.priceToCoordinate(d.stop))
        const tarY = cv(series.priceToCoordinate(d.target))
        const tx   = cv(chart.timeScale().timeToCoordinate(d.time as Time))
        if (entY==null||stoY==null||tarY==null||tx==null) continue
        if (mx < tx - THRESH || mx > canvas.width) continue
        const boxTop = Math.min(stoY,tarY), boxBot = Math.max(stoY,tarY)
        const tx2 = cv(chart.timeScale().timeToCoordinate(d.t2 as Time))
        if (Math.abs(my-entY) < THRESH && mx > tx) return { drawingId: d.id, handle: 'entry',  cursor: 'ns-resize' }
        if (Math.abs(my-stoY) < THRESH && mx > tx) return { drawingId: d.id, handle: 'stop',   cursor: 'ns-resize' }
        if (Math.abs(my-tarY) < THRESH && mx > tx) return { drawingId: d.id, handle: 'target', cursor: 'ns-resize' }
        if (Math.abs(mx-tx) < THRESH && my > boxTop-THRESH && my < boxBot+THRESH) return { drawingId: d.id, handle: 'time',       cursor: 'ew-resize' }
        if (tx2 != null && Math.abs(mx-tx2) < THRESH && my > boxTop-THRESH && my < boxBot+THRESH) return { drawingId: d.id, handle: 'right_edge', cursor: 'ew-resize' }
        if (mx > tx && my > boxTop && my < boxBot) return { drawingId: d.id, handle: 'body', cursor: 'move' }
        continue
      }
      if (d.type === 'hline') {
        const y = cv(series.priceToCoordinate(d.price))
        if (y!=null && Math.abs(my-y)<THRESH) return { drawingId: d.id, handle: 'line', cursor: 'ns-resize' }
        continue
      }
      if (d.type === 'vline') {
        const x = cv(chart.timeScale().timeToCoordinate(d.time as Time))
        if (x!=null && Math.abs(mx-x)<THRESH) return { drawingId: d.id, handle: 'line', cursor: 'ew-resize' }
        continue
      }
      if (d.type === 'text') {
        const x = cv(chart.timeScale().timeToCoordinate(d.time as Time))
        const y = cv(series.priceToCoordinate(d.price))
        if (x!=null&&y!=null && Math.abs(mx-x)<60 && Math.abs(my-y)<16) return { drawingId: d.id, handle: 'body', cursor: 'move' }
        continue
      }
      if (d.type === 'trendline'||d.type==='ray'||d.type==='extline'||d.type==='arrow') {
        const x1=cv(chart.timeScale().timeToCoordinate(d.t1 as Time))
        const y1=cv(series.priceToCoordinate(d.p1))
        const x2=cv(chart.timeScale().timeToCoordinate(d.t2 as Time))
        const y2=cv(series.priceToCoordinate(d.p2))
        if (x1==null||y1==null||x2==null||y2==null) continue
        if (Math.hypot(mx-x1,my-y1)<10) return { drawingId: d.id, handle: 'p1', cursor: 'move' }
        if (Math.hypot(mx-x2,my-y2)<10) return { drawingId: d.id, handle: 'p2', cursor: 'move' }
        const dx=x2-x1,dy=y2-y1,l2=dx*dx+dy*dy
        if (l2>1) {
          const t=Math.max(0,Math.min(1,((mx-x1)*dx+(my-y1)*dy)/l2))
          if (Math.hypot(mx-(x1+t*dx),my-(y1+t*dy))<THRESH) return { drawingId: d.id, handle: 'body', cursor: 'move' }
        }
        continue
      }
      if (d.type==='rect'||d.type==='measure') {
        const x1=cv(chart.timeScale().timeToCoordinate(d.t1 as Time))
        const y1=cv(series.priceToCoordinate(d.p1))
        const x2=cv(chart.timeScale().timeToCoordinate(d.t2 as Time))
        const y2=cv(series.priceToCoordinate(d.p2))
        if (x1==null||y1==null||x2==null||y2==null) continue
        const inside = mx>Math.min(x1,x2)-THRESH&&mx<Math.max(x1,x2)+THRESH&&my>Math.min(y1,y2)-THRESH&&my<Math.max(y1,y2)+THRESH
        if (!inside) continue
        // corner handles
        if (Math.hypot(mx-x1,my-y1)<10) return { drawingId: d.id, handle: 'c_t1p1', cursor: 'nwse-resize' }
        if (Math.hypot(mx-x2,my-y2)<10) return { drawingId: d.id, handle: 'c_t2p2', cursor: 'nwse-resize' }
        if (Math.hypot(mx-x1,my-y2)<10) return { drawingId: d.id, handle: 'c_t1p2', cursor: 'nesw-resize' }
        if (Math.hypot(mx-x2,my-y1)<10) return { drawingId: d.id, handle: 'c_t2p1', cursor: 'nesw-resize' }
        return { drawingId: d.id, handle: 'body', cursor: 'move' }
      }
    }
    return null
  }

  // ─── Drag logic ──────────────────────────────────────────────────────────────
  function applyDrag(op: DragOp, currentX: number, currentY: number) {
    const chart = chartRef.current, series = serRef.current
    if (!chart || !series) return
    const curPrice = cv(series.coordinateToPrice(currentY)) as unknown as number
    const curTime  = cv(chart.timeScale().coordinateToTime(currentX)) as unknown as number
    if (curPrice == null || curTime == null) return
    const dP = curPrice - op.startPrice
    const dT = curTime  - op.startTime

    const d = draws.current.find(x => x.id === op.drawingId)
    if (!d) return
    const s = op.snapshot as typeof d

    if (d.type === 'shortpos' || d.type === 'longpos') {
      const snap = s as Extract<Drawing, { type: 'shortpos'|'longpos' }>
      if (op.handle === 'entry')       { d.entry  = snap.entry  + dP }
      else if (op.handle === 'stop')   { d.stop   = snap.stop   + dP }
      else if (op.handle === 'target') { d.target = snap.target + dP }
      else if (op.handle === 'time')   { d.time   = snap.time   + dT; d.t2 = snap.t2 + dT }
      else if (op.handle === 'right_edge') { d.t2 = Math.max(snap.t2 + dT, snap.time + 60) }
      else if (op.handle === 'body') {
        d.entry  = snap.entry  + dP; d.stop = snap.stop + dP
        d.target = snap.target + dP; d.time = snap.time + dT; d.t2 = snap.t2 + dT
      }
    } else if (d.type === 'hline') {
      const snap = s as Extract<Drawing, { type: 'hline' }>
      d.price = snap.price + dP
    } else if (d.type === 'vline') {
      const snap = s as Extract<Drawing, { type: 'vline' }>
      d.time = snap.time + dT
    } else if (d.type === 'trendline'||d.type==='ray'||d.type==='extline'||d.type==='arrow') {
      const snap = s as Extract<Drawing, { type: 'trendline' }>
      if (op.handle === 'p1')        { d.t1 = snap.t1 + dT; d.p1 = snap.p1 + dP }
      else if (op.handle === 'p2')   { d.t2 = snap.t2 + dT; d.p2 = snap.p2 + dP }
      else { d.t1=snap.t1+dT; d.p1=snap.p1+dP; d.t2=snap.t2+dT; d.p2=snap.p2+dP }
    } else if (d.type === 'rect' || d.type === 'measure') {
      const snap = s as Extract<Drawing, { type: 'rect' }>
      if (op.handle === 'c_t1p1')      { d.t1=snap.t1+dT; d.p1=snap.p1+dP }
      else if (op.handle === 'c_t2p2') { d.t2=snap.t2+dT; d.p2=snap.p2+dP }
      else if (op.handle === 'c_t1p2') { d.t1=snap.t1+dT; d.p2=snap.p2+dP }
      else if (op.handle === 'c_t2p1') { d.t2=snap.t2+dT; d.p1=snap.p1+dP }
      else { d.t1=snap.t1+dT; d.p1=snap.p1+dP; d.t2=snap.t2+dT; d.p2=snap.p2+dP }
    } else if (d.type === 'text') {
      const snap = s as Extract<Drawing, { type: 'text' }>
      d.time = snap.time + dT; d.price = snap.price + dP
    }
  }

  // ─── Main draw ───────────────────────────────────────────────────────────────
  function draw() {
    const t=tradeRef.current,chart=chartRef.current,series=serRef.current
    const canvas=canvasRef.current,wrap=wrapRef.current
    if (!t||!chart||!series||!canvas||!wrap) return
    syncCanvas()
    const ctx=canvas.getContext('2d')!
    ctx.clearRect(0,0,canvas.width,canvas.height)
    const prec=t.entry>100?1:t.entry>1?4:6

    // ── Trade overlay ─────────────────────────────────────────────────────────
    const eX=cv(chart.timeScale().timeToCoordinate(t.ts as Time))
    const eY=cv(series.priceToCoordinate(t.entry))
    const sY=cv(series.priceToCoordinate(t.stop))
    const tY=cv(series.priceToCoordinate(t.target))
    if (eX==null||eY==null||sY==null||tY==null) return
    let exitTs=t.ts+90*60
    if (t.closedAt) { const ex=Math.floor(new Date(t.closedAt).getTime()/1000); if(ex>t.ts&&ex<t.ts+180*60) exitTs=ex }
    const xXraw=cv(chart.timeScale().timeToCoordinate(exitTs as Time))
    const x1=(xXraw==null||xXraw<=eX)?eX+Math.max(canvas.width*0.25,120):xXraw
    const x0=eX,w2=Math.max(x1-x0,4)
    const sy0=Math.min(eY,sY),sh=Math.abs(sY-eY)
    ctx.fillStyle='rgba(248,81,73,0.25)';ctx.fillRect(x0,sy0,w2,sh)
    ctx.strokeStyle='rgba(248,81,73,0.8)';ctx.lineWidth=1.5;ctx.setLineDash([]);ctx.strokeRect(x0,sy0,w2,sh)
    const ty0=Math.min(eY,tY),th=Math.abs(tY-eY)
    ctx.fillStyle='rgba(63,185,80,0.25)';ctx.fillRect(x0,ty0,w2,th)
    ctx.strokeStyle='rgba(63,185,80,0.8)';ctx.lineWidth=1.5;ctx.strokeRect(x0,ty0,w2,th)
    ctx.strokeStyle='rgba(230,237,243,0.5)';ctx.lineWidth=1;ctx.setLineDash([5,4])
    ctx.beginPath();ctx.moveTo(x0,eY);ctx.lineTo(x1,eY);ctx.stroke();ctx.setLineDash([])
    ctx.strokeStyle='rgba(88,166,255,0.2)';ctx.lineWidth=1;ctx.setLineDash([3,5])
    ctx.beginPath();ctx.moveTo(x0,0);ctx.lineTo(x0,canvas.height);ctx.stroke();ctx.setLineDash([])
    if (t.exit&&t.exit>0) {
      const xpY=cv(series.priceToCoordinate(t.exit))
      if (xpY!=null) { ctx.strokeStyle=(t.resultR??0)>0?'rgba(63,185,80,0.7)':'rgba(248,81,73,0.7)';ctx.lineWidth=1.5;ctx.setLineDash([3,3]);ctx.beginPath();ctx.moveTo(x0,xpY);ctx.lineTo(x1,xpY);ctx.stroke();ctx.setLineDash([]) }
    }
    ctx.font='bold 11px monospace';ctx.textAlign='left'
    ctx.fillStyle='rgba(230,237,243,0.9)';ctx.fillText('ENTRY  '+t.entry.toFixed(prec),x1+6,eY-3)
    ctx.fillStyle='rgba(248,81,73,0.95)';ctx.fillText('SL  '+t.stop.toFixed(prec),x1+6,sY+4)
    ctx.fillStyle='rgba(63,185,80,0.95)';ctx.fillText('TP  '+t.target.toFixed(prec),x1+6,tY+4)
    ctx.font='bold 10px monospace';ctx.textAlign='center'
    if(sh>14){ctx.fillStyle='rgba(248,81,73,0.6)';ctx.fillText('STOP LOSS',x0+w2/2,sy0+sh/2+4)}
    if(th>14){ctx.fillStyle='rgba(63,185,80,0.6)';ctx.fillText('TAKE PROFIT',x0+w2/2,ty0+th/2+4)}
    ctx.textAlign='left'
    const rStr=t.isOpen?'OPEN':((t.resultR??0)>=0?'+':'')+(t.resultR??0).toFixed(3)+'R'
    const uStr=t.isOpen?'':` ${t.pnlUsd>=0?'+$':'-$'}${Math.abs(t.pnlUsd).toFixed(3)}`
    const rCol=t.isOpen?'rgba(56,139,253,0.9)':(t.resultR??0)>0?'rgba(63,185,80,0.95)':'rgba(248,81,73,0.95)'
    ctx.font='bold 13px monospace';ctx.fillStyle=rCol
    const badgeY=Math.min(sy0,ty0)-8;ctx.fillText(rStr+uStr,x0+6,badgeY>16?badgeY:16)
    ctx.font='bold 11px monospace';ctx.fillStyle=t.dir==='Short'?'rgba(248,81,73,0.8)':'rgba(63,185,80,0.8)'
    ctx.fillText(t.dir.toUpperCase(),x0+6,eY+(t.dir==='Short'?-16:14))

    // ── User drawings ─────────────────────────────────────────────────────────
    const curHit = hovHit.current
    for (const d of draws.current) {
      const hov = curHit?.drawingId===d.id
      drawOne(ctx, canvas, d, prec, t, hov, curHit?.handle)
    }

    // ── Pending preview ───────────────────────────────────────────────────────
    const pd=pending.current, {x:mx,y:my}=mouse.current
    if (pd) {
      const snap=snapPoint(mx,my), ex=snap?.x??mx, ey=snap?.y??my
      ctx.strokeStyle='rgba(240,192,64,0.55)';ctx.lineWidth=1.2;ctx.setLineDash([5,4])
      if (pd.tool==='shortpos'||pd.tool==='longpos') {
        ctx.setLineDash([])
        const ep=cv(series.coordinateToPrice(pd.y1)) as unknown as number
        const cp=cv(series.coordinateToPrice(ey)) as unknown as number
        if (ep!=null&&cp!=null) {
          const isShort=pd.tool==='shortpos', risk=isShort?(cp-ep):(ep-cp)
          if (risk>0) {
            const tgt=isShort?ep-2*risk:ep+2*risk
            const tarY2=cv(series.priceToCoordinate(tgt))
            // preview: right edge = entry + 30 bars (30 min for M1 preview)
            const previewTx2=pd.x1+Math.max(canvas.width*0.3,120)
            drawPos(ctx,canvas,series,pd.tool,pd.x1,previewTx2,pd.y1,ey,tarY2??ey-(ey-pd.y1)*2,ep,cp,tgt,prec)
          }
        }
      } else if (pd.tool==='rect') {
        ctx.fillStyle='rgba(88,166,255,0.06)';ctx.fillRect(pd.x1,pd.y1,ex-pd.x1,ey-pd.y1)
        ctx.strokeRect(pd.x1,pd.y1,ex-pd.x1,ey-pd.y1)
      } else {
        drawLineShape(ctx,canvas,pd.tool,pd.x1,pd.y1,ex,ey)
      }
      ctx.setLineDash([])
    }

    // magnet dot
    if (magnetRef.current&&toolRef.current!=='cursor') {
      const snap=snapPoint(mx,my)
      if (snap) { ctx.fillStyle='rgba(240,192,64,0.9)';ctx.beginPath();ctx.arc(snap.x,snap.y,5,0,Math.PI*2);ctx.fill() }
    }
  }

  // ─── Position box renderer ───────────────────────────────────────────────────
  function drawPos(ctx: CanvasRenderingContext2D, canvas: HTMLCanvasElement, series: CandleSeries,
    type: 'shortpos'|'longpos', tx: number, tx2: number, entY: number, stoY: number, tarY: number,
    entry: number, stop: number, target: number, prec: number,
    hovHandle?: string
  ) {
    const risk=Math.abs(entry-stop), profit=Math.abs(entry-target)
    const rr=risk>0?(profit/risk).toFixed(2):'—'
    const pctR=(risk/entry*100).toFixed(2), pctP=(profit/entry*100).toFixed(2)
    const w=tx2-tx

    const lossTop=Math.min(entY,stoY),lossH=Math.abs(stoY-entY)
    ctx.globalAlpha=hovHandle==='stop'?0.35:0.2;ctx.fillStyle='#f85149';ctx.fillRect(tx,lossTop,w,lossH)
    ctx.globalAlpha=1;ctx.strokeStyle=hovHandle==='stop'?'#ff8070':'rgba(248,81,73,0.8)';ctx.lineWidth=1.2;ctx.setLineDash([]);ctx.strokeRect(tx,lossTop,w,lossH)

    const profTop=Math.min(entY,tarY),profH=Math.abs(tarY-entY)
    ctx.globalAlpha=hovHandle==='target'?0.35:0.2;ctx.fillStyle='#3fb950';ctx.fillRect(tx,profTop,w,profH)
    ctx.globalAlpha=1;ctx.strokeStyle=hovHandle==='target'?'#6fe08a':'rgba(63,185,80,0.8)';ctx.lineWidth=1.2;ctx.strokeRect(tx,profTop,w,profH)

    // entry line
    ctx.strokeStyle=hovHandle==='entry'?'#fff':'rgba(230,237,243,0.7)';ctx.lineWidth=hovHandle==='entry'?2:1.5;ctx.setLineDash([4,3])
    ctx.beginPath();ctx.moveTo(tx,entY);ctx.lineTo(canvas.width,entY);ctx.stroke();ctx.setLineDash([])

    // left anchor handle
    const anchorCol=hovHandle==='time'?'#f0c040':'rgba(255,255,255,0.25)'
    ctx.fillStyle=anchorCol;ctx.fillRect(tx-4,lossTop,4,Math.abs(lossTop-(profTop+profH))+lossH)

    // right edge handle
    const rEdgeCol=hovHandle==='right_edge'?'#f0c040':'rgba(255,255,255,0.2)'
    ctx.fillStyle=rEdgeCol;ctx.fillRect(tx2,lossTop,4,Math.abs(lossTop-(profTop+profH))+lossH)

    // handle circles on left edge
    const handleY=[entY,stoY,tarY]
    const handleCols=['rgba(230,237,243,0.8)','rgba(248,81,73,0.9)','rgba(63,185,80,0.9)']
    const handleHov=['entry','stop','target']
    handleY.forEach((hy,i)=>{
      ctx.fillStyle=hovHandle===handleHov[i]?'#fff':handleCols[i]
      ctx.beginPath();ctx.arc(tx,hy,5,0,Math.PI*2);ctx.fill()
      ctx.strokeStyle='rgba(0,0,0,0.4)';ctx.lineWidth=1;ctx.stroke()
    })

    // labels just outside right edge
    ctx.font='bold 10px monospace';ctx.textAlign='left'
    ctx.fillStyle=hovHandle==='entry'?'#fff':'rgba(230,237,243,0.9)'
    ctx.fillText(entry.toFixed(prec),tx2+8,entY+4)
    ctx.fillStyle=hovHandle==='stop'?'#ff8070':'rgba(248,81,73,0.95)'
    ctx.fillText(`${stop.toFixed(prec)}  -${pctR}%`,tx2+8,stoY+4)
    ctx.fillStyle=hovHandle==='target'?'#6fe08a':'rgba(63,185,80,0.95)'
    ctx.fillText(`${target.toFixed(prec)}  +${pctP}%`,tx2+8,tarY+4)

    // center badges
    if(lossH>18){ctx.font='bold 11px monospace';ctx.textAlign='center';ctx.fillStyle='rgba(248,81,73,0.8)';ctx.fillText(`-${pctR}%`,tx+w/2,lossTop+lossH/2+4)}
    if(profH>18){ctx.font='bold 11px monospace';ctx.textAlign='center';ctx.fillStyle='rgba(63,185,80,0.9)';ctx.fillText(`+${pctP}% · ${rr}R`,tx+w/2,profTop+profH/2+4)}
    ctx.textAlign='left'
  }

  // ─── Generic drawing renderer ─────────────────────────────────────────────────
  function drawOne(ctx: CanvasRenderingContext2D, canvas: HTMLCanvasElement, d: Drawing,
    prec: number, t: Trade, hov: boolean, hovHandle?: string
  ) {
    const chart=chartRef.current,series=serRef.current
    if (!chart||!series) return

    if (d.type==='shortpos'||d.type==='longpos') {
      const entY=cv(series.priceToCoordinate(d.entry)),stoY=cv(series.priceToCoordinate(d.stop))
      const tarY=cv(series.priceToCoordinate(d.target)),tx=cv(chart.timeScale().timeToCoordinate(d.time as Time))
      const tx2raw=cv(chart.timeScale().timeToCoordinate(d.t2 as Time))
      // if t2 is off-screen to the right, clamp to canvas width - 40 (still shows right handle)
      const tx2 = tx2raw ?? canvas.width - 40
      if(entY==null||stoY==null||tarY==null||tx==null) return
      drawPos(ctx,canvas,series,d.type,tx,tx2,entY,stoY,tarY,d.entry,d.stop,d.target,prec,hov?hovHandle:undefined)
      return
    }

    ctx.globalAlpha=hov?1:0.9
    const C='color' in d ? d.color : '#fff'
    const lw=hov?2:1.3, col=hov?'#fff':C

    if (d.type==='hline') {
      const y=cv(series.priceToCoordinate(d.price));if(y==null){ctx.globalAlpha=1;return}
      ctx.strokeStyle=col;ctx.lineWidth=lw;ctx.setLineDash([6,3])
      ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(canvas.width,y);ctx.stroke();ctx.setLineDash([])
      ctx.font='10px monospace';ctx.fillStyle=col;ctx.textAlign='right';ctx.fillText(d.price.toFixed(prec),canvas.width-4,y-3);ctx.textAlign='left'
    } else if (d.type==='vline') {
      const x=cv(chart.timeScale().timeToCoordinate(d.time as Time));if(x==null){ctx.globalAlpha=1;return}
      ctx.strokeStyle=col;ctx.lineWidth=lw;ctx.setLineDash([6,3])
      ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,canvas.height);ctx.stroke();ctx.setLineDash([])
    } else if (d.type==='trendline'||d.type==='ray'||d.type==='extline'||d.type==='arrow') {
      const x1=cv(chart.timeScale().timeToCoordinate(d.t1 as Time)),y1=cv(series.priceToCoordinate(d.p1))
      const x2=cv(chart.timeScale().timeToCoordinate(d.t2 as Time)),y2=cv(series.priceToCoordinate(d.p2))
      if(x1==null||y1==null||x2==null||y2==null){ctx.globalAlpha=1;return}
      ctx.strokeStyle=col;ctx.lineWidth=lw;ctx.setLineDash([])
      drawLineShape(ctx,canvas,d.type,x1,y1,x2,y2)
      ctx.fillStyle=col
      ctx.beginPath();ctx.arc(x1,y1,hov?5:3.5,0,Math.PI*2);ctx.fill()
      if(d.type==='trendline'){ctx.beginPath();ctx.arc(x2,y2,hov?5:3.5,0,Math.PI*2);ctx.fill()}
    } else if (d.type==='rect') {
      const x1=cv(chart.timeScale().timeToCoordinate(d.t1 as Time)),y1=cv(series.priceToCoordinate(d.p1))
      const x2=cv(chart.timeScale().timeToCoordinate(d.t2 as Time)),y2=cv(series.priceToCoordinate(d.p2))
      if(x1==null||y1==null||x2==null||y2==null){ctx.globalAlpha=1;return}
      ctx.globalAlpha=hov?0.2:0.1;ctx.fillStyle=C;ctx.fillRect(x1,y1,x2-x1,y2-y1)
      ctx.globalAlpha=1;ctx.strokeStyle=col;ctx.lineWidth=lw;ctx.setLineDash([]);ctx.strokeRect(x1,y1,x2-x1,y2-y1)
      if(hov){[[x1,y1],[x2,y2],[x1,y2],[x2,y1]].forEach(([cx,cy])=>{ctx.fillStyle='#fff';ctx.beginPath();ctx.arc(cx as number,cy as number,5,0,Math.PI*2);ctx.fill()})}
    } else if (d.type==='text') {
      const x=cv(chart.timeScale().timeToCoordinate(d.time as Time)),y=cv(series.priceToCoordinate(d.price))
      if(x==null||y==null){ctx.globalAlpha=1;return}
      if(hov){ctx.fillStyle='rgba(255,255,255,0.1)';ctx.fillRect(x-2,y-14,d.label.length*7.5,18)}
      ctx.font='bold 12px monospace';ctx.fillStyle=col;ctx.fillText(d.label,x+4,y-3)
      ctx.fillStyle=col;ctx.beginPath();ctx.arc(x,y,3,0,Math.PI*2);ctx.fill()
    } else if (d.type==='measure') {
      const x1=cv(chart.timeScale().timeToCoordinate(d.t1 as Time)),y1=cv(series.priceToCoordinate(d.p1))
      const x2=cv(chart.timeScale().timeToCoordinate(d.t2 as Time)),y2=cv(series.priceToCoordinate(d.p2))
      if(x1==null||y1==null||x2==null||y2==null){ctx.globalAlpha=1;return}
      const pd2=d.p2-d.p1,pct=((pd2/d.p1)*100).toFixed(3)
      const risk=Math.abs(t.stop-t.entry),rMult=risk>0?(pd2/risk).toFixed(2):null
      const label=`${pd2>=0?'+':''}${pd2.toFixed(prec)}  ${pct}%${rMult?`  ${rMult}R`:''}`
      ctx.globalAlpha=hov?0.25:0.14;ctx.fillStyle=pd2<0?'#f85149':'#3fb950'
      ctx.fillRect(Math.min(x1,x2),Math.min(y1,y2),Math.abs(x2-x1),Math.abs(y2-y1))
      ctx.globalAlpha=1;ctx.strokeStyle=hov?'#fff':(pd2<0?'rgba(248,81,73,0.7)':'rgba(63,185,80,0.7)')
      ctx.lineWidth=hov?2:1;ctx.setLineDash([4,3]);ctx.strokeRect(Math.min(x1,x2),Math.min(y1,y2),Math.abs(x2-x1),Math.abs(y2-y1));ctx.setLineDash([])
      ctx.font='bold 11px monospace';ctx.fillStyle=pd2<0?'#f85149':'#3fb950';ctx.textAlign='center'
      ctx.fillText(label,Math.min(x1,x2)+Math.abs(x2-x1)/2,Math.min(y1,y2)+Math.abs(y2-y1)/2+5);ctx.textAlign='left'
    }
    ctx.globalAlpha=1
  }

  function drawLineShape(ctx: CanvasRenderingContext2D, canvas: HTMLCanvasElement, tool: Tool, x1: number, y1: number, x2: number, y2: number) {
    ctx.beginPath()
    if (tool==='ray') {
      const dx=x2-x1,dy=y2-y1,l=Math.sqrt(dx*dx+dy*dy)||1
      ctx.moveTo(x1,y1);ctx.lineTo(x1+(dx/l)*canvas.width*2,y1+(dy/l)*canvas.width*2)
    } else if (tool==='extline') {
      const dx=x2-x1,dy=y2-y1,l=Math.sqrt(dx*dx+dy*dy)||1
      ctx.moveTo(x1-(dx/l)*canvas.width*2,y1-(dy/l)*canvas.width*2);ctx.lineTo(x1+(dx/l)*canvas.width*2,y1+(dy/l)*canvas.width*2)
    } else if (tool==='rect') {
      ctx.strokeRect(x1,y1,x2-x1,y2-y1);return
    } else if (tool==='arrow') {
      ctx.moveTo(x1,y1);ctx.lineTo(x2,y2)
      const a=Math.atan2(y2-y1,x2-x1)
      ctx.moveTo(x2,y2);ctx.lineTo(x2-11*Math.cos(a-0.4),y2-11*Math.sin(a-0.4))
      ctx.moveTo(x2,y2);ctx.lineTo(x2-11*Math.cos(a+0.4),y2-11*Math.sin(a+0.4))
    } else { ctx.moveTo(x1,y1);ctx.lineTo(x2,y2) }
    ctx.stroke()
  }

  // ─── Mouse handlers ──────────────────────────────────────────────────────────
  function handleMouseDown(e: React.MouseEvent<HTMLCanvasElement>) {
    const tool=toolRef.current
    if (tool==='cursor') {
      // start drag on a drawing
      const hit=hovHit.current
      if (!hit) return
      const chart=chartRef.current,series=serRef.current,canvas=canvasRef.current
      if (!chart||!series||!canvas) return
      const rect=canvas.getBoundingClientRect()
      const mx=e.clientX-rect.left,my=e.clientY-rect.top
      const sp=cv(series.coordinateToPrice(my)) as unknown as number
      const st=cv(chart.timeScale().coordinateToTime(mx)) as unknown as number
      const d=draws.current.find(x=>x.id===hit.drawingId)
      if (!d||sp==null||st==null) return
      dragOp.current={ drawingId: hit.drawingId, handle: hit.handle, startPrice: sp, startTime: st, snapshot: deepClone(d) }
      // keep canvas active during drag
      canvas.style.pointerEvents='auto'
      canvas.style.cursor=hit.cursor
      return
    }
    if (tool==='eraser') return
    const pt=getPoint(e)
    if (tool==='rect') {
      dragRect.current=true
      pending.current={ t1: pt.time, p1: pt.price, x1: pt.x, y1: pt.y, tool }
    }
  }

  function handleMouseMove(e: React.MouseEvent<HTMLCanvasElement>) {
    const canvas=canvasRef.current!
    const rect=canvas.getBoundingClientRect()
    const mx=e.clientX-rect.left,my=e.clientY-rect.top
    mouse.current={x:mx,y:my}

    if (dragOp.current) {
      applyDrag(dragOp.current,mx,my); draw(); return
    }
    if (toolRef.current==='eraser') {
      let found: HitResult|null=null
      for (let i=draws.current.length-1;i>=0;i--) {
        const h=findHit(mx,my); if(h){found=h;break}
      }
      if ((found?.drawingId??null)!==(hovHit.current?.drawingId??null)) {
        hovHit.current=found; draw()
      }
      canvas.style.cursor=found?'pointer':'crosshair'
      return
    }
    if (pending.current||dragRect.current||magnetRef.current) draw()
  }

  function handleMouseUp(e: React.MouseEvent<HTMLCanvasElement>) {
    if (dragOp.current) {
      dragOp.current=null; forceRedraw(); draw()
      // restore pointer-events
      const canvas=canvasRef.current
      if (canvas&&toolRef.current==='cursor') {
        const rect=canvas.getBoundingClientRect()
        const mx=e.clientX-rect.left,my=e.clientY-rect.top
        const hit=findHit(mx,my)
        canvas.style.pointerEvents=hit?'auto':'none'
        canvas.style.cursor=hit?.cursor??'default'
        hovHit.current=hit
      }
      return
    }
    if (!dragRect.current) return
    dragRect.current=false
    const pd=pending.current; if(!pd||pd.tool!=='rect') return
    const pt=getPoint(e)
    if (Math.abs(pt.x-pd.x1)<5||Math.abs(pt.y-pd.y1)<5){pending.current=null;return}
    draws.current.push({id:++_uid,type:'rect',t1:pd.t1,p1:pd.p1,t2:pt.time,p2:pt.price,color:nextColor()})
    pending.current=null; forceRedraw(); draw()
  }

  function handleClick(e: React.MouseEvent<HTMLCanvasElement>) {
    if (dragOp.current||dragRect.current) return
    const tool=toolRef.current
    if (tool==='cursor'||tool==='rect') return
    const pt=getPoint(e)

    if (tool==='eraser') {
      const rect=canvasRef.current!.getBoundingClientRect()
      const mx=e.clientX-rect.left,my=e.clientY-rect.top
      for (let i=draws.current.length-1;i>=0;i--) {
        if (hitTest_eraser(draws.current[i],mx,my)) { draws.current.splice(i,1); hovHit.current=null; forceRedraw(); draw(); return }
      }; return
    }
    if (tool==='hline'){draws.current.push({id:++_uid,type:'hline',price:pt.price,color:nextColor()});forceRedraw();draw();return}
    if (tool==='vline'){draws.current.push({id:++_uid,type:'vline',time:pt.time,color:nextColor()});forceRedraw();draw();return}
    if (tool==='text'){setTextInput({x:pt.x,y:pt.y,time:pt.time,price:pt.price});return}

    const pd=pending.current
    if (!pd){pending.current={t1:pt.time,p1:pt.price,x1:pt.x,y1:pt.y,tool};draw();return}

    if (tool==='shortpos'||tool==='longpos') {
      const isShort=tool==='shortpos',risk=isShort?(pt.price-pd.p1):(pd.p1-pt.price)
      if (risk>0) draws.current.push({id:++_uid,type:tool,time:pd.t1,t2:pd.t1+30*60,entry:pd.p1,stop:pt.price,target:isShort?pd.p1-2*risk:pd.p1+2*risk})
    } else if (tool==='measure') {
      draws.current.push({id:++_uid,type:'measure',t1:pd.t1,p1:pd.p1,t2:pt.time,p2:pt.price})
    } else {
      draws.current.push({id:++_uid,type:tool as 'trendline'|'ray'|'extline'|'arrow',t1:pd.t1,p1:pd.p1,t2:pt.time,p2:pt.price,color:nextColor()})
    }
    pending.current=null; forceRedraw(); draw()
  }

  function hitTest_eraser(d: Drawing, mx: number, my: number) {
    return findHit(mx, my)?.drawingId === d.id
  }

  function handleContextMenu(e: React.MouseEvent<HTMLCanvasElement>) {
    e.preventDefault()
    if (pending.current){pending.current=null;draw();return}
    if (draws.current.length){draws.current.pop();forceRedraw();draw()}
  }

  function commitText(label: string) {
    if (!textInput) return
    if (label.trim()) {
      draws.current.push({id:++_uid,type:'text',time:textInput.time,price:textInput.price,label:label.trim(),color:nextColor()})
      forceRedraw()
    }
    setTextInput(null); draw()
  }

  const isDrawing = activeTool !== 'cursor'

  if (!trade) return (
    <div style={{flex:1,display:'flex',alignItems:'center',justifyContent:'center',color:'var(--text3)',fontSize:13}}>
      Selecciona un trade
    </div>
  )

  return (
    <div style={{flex:1,position:'relative'}}>
      <div ref={wrapRef} style={{width:'100%',height:'100%'}} />

      <canvas ref={canvasRef}
        style={{position:'absolute',top:0,left:0,zIndex:5,
          pointerEvents: isDrawing ? 'auto' : 'none',
          cursor: isDrawing ? 'crosshair' : 'default'}}
        onMouseDown={handleMouseDown}
        onMouseUp={handleMouseUp}
        onMouseMove={handleMouseMove}
        onClick={handleClick}
        onContextMenu={handleContextMenu}
      />

      {textInput && (
        <input autoFocus
          onKeyDown={e=>{if(e.key==='Enter')commitText((e.target as HTMLInputElement).value);if(e.key==='Escape')setTextInput(null)}}
          onBlur={e=>commitText(e.target.value)}
          style={{position:'absolute',zIndex:20,left:textInput.x,top:textInput.y-14,
            background:'rgba(22,27,34,0.95)',border:'1px solid #388bfd',
            color:'#e6edf3',fontSize:12,fontFamily:'monospace',padding:'2px 6px',borderRadius:3,outline:'none',minWidth:120}}
          placeholder="Enter para confirmar"
        />
      )}

      {/* Toolbar */}
      <div style={{position:'absolute',top:8,left:8,zIndex:10,display:'flex',flexDirection:'column',gap:2}}>
        {GROUPS.map((group,gi)=>(
          <div key={gi} style={{display:'flex',flexDirection:'column',gap:2,marginBottom:gi<GROUPS.length-1?3:0}}>
            {gi>0&&<div style={{height:1,background:'rgba(48,54,61,0.6)',margin:'1px 3px'}}/>}
            {group.map(t=>{
              const def=TOOL_DEF[t],isActive=activeTool===t
              return (
                <button key={t} title={def.tip} onClick={()=>setTool(t)} style={{
                  width:30,height:30,display:'flex',alignItems:'center',justifyContent:'center',
                  background:isActive?'rgba(56,139,253,0.2)':'rgba(13,17,23,0.88)',
                  border:isActive?'1px solid rgba(56,139,253,0.65)':'1px solid rgba(33,38,45,0.8)',
                  borderRadius:5,
                  color:t==='shortpos'&&isActive?'#f85149':t==='longpos'&&isActive?'#3fb950'
                       :t==='shortpos'?'rgba(248,81,73,0.7)':t==='longpos'?'rgba(63,185,80,0.7)'
                       :t==='eraser'?(isActive?'#f0c040':'#8b949e'):isActive?'#58a6ff':'#8b949e',
                  fontSize:t==='text'?14:12,fontWeight:t==='text'?700:400,
                  cursor:'pointer',backdropFilter:'blur(6px)',fontFamily:'monospace',
                  letterSpacing:t==='extline'?-1:0,
                }}>
                  {def.icon}
                </button>
              )
            })}
          </div>
        ))}
        <div style={{height:1,background:'rgba(48,54,61,0.6)',margin:'1px 3px'}}/>
        <button title="Imán — snap a OHLC" onClick={()=>{magnetRef.current=!magnet;setMagnet(m=>!m)}} style={{
          width:30,height:30,display:'flex',alignItems:'center',justifyContent:'center',
          background:magnet?'rgba(240,192,64,0.18)':'rgba(13,17,23,0.88)',
          border:magnet?'1px solid rgba(240,192,64,0.65)':'1px solid rgba(33,38,45,0.8)',
          borderRadius:5,color:magnet?'#f0c040':'#8b949e',fontSize:16,cursor:'pointer',backdropFilter:'blur(6px)',
        }}>⌖</button>
        {draws.current.length>0&&(
          <button title="Borrar todos" onClick={()=>{draws.current=[];pending.current=null;hovHit.current=null;forceRedraw();draw()}} style={{
            width:30,height:30,display:'flex',alignItems:'center',justifyContent:'center',
            background:'rgba(13,17,23,0.88)',border:'1px solid rgba(248,81,73,0.4)',
            borderRadius:5,color:'rgba(248,81,73,0.8)',fontSize:14,cursor:'pointer',backdropFilter:'blur(6px)',
          }}>✕</button>
        )}
      </div>

      {/* Auto-center button — top-right */}
      <button
        title="Centrar trade (reset Y)"
        onClick={() => {
          chartRef.current?.timeScale().fitContent()
          serRef.current?.priceScale().applyOptions({ autoScale: true })
        }}
        style={{
          position:'absolute',top:8,right:8,zIndex:10,
          width:30,height:30,display:'flex',alignItems:'center',justifyContent:'center',
          background:'rgba(13,17,23,0.88)',border:'1px solid rgba(33,38,45,0.8)',
          borderRadius:5,color:'#8b949e',fontSize:15,cursor:'pointer',
          backdropFilter:'blur(6px)',fontFamily:'monospace',
        }}
      >⊕</button>
    </div>
  )
}
