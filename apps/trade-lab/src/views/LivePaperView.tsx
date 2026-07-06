import { useEffect, useRef, useState } from 'react'
import { createChart, CandlestickSeries, type IChartApi, type ISeriesApi, type Time } from 'lightweight-charts'
import type { Trade } from '../lib/types'
import { fetchLiquidityTrades, type LiqOpts } from '../lib/liquidity'
import { fetchKlines } from '../lib/exchanges'

const ACC = 500

// Carga velas M1 cubriendo [fromMs, toMs] paginando (Bybit limita 1000/request).
async function loadRangeM1(symbol: string, fromMs: number, toMs: number) {
  const out: { time: number; open: number; high: number; low: number; close: number }[] = []
  let cursor = fromMs
  for (let i = 0; i < 6 && cursor < toMs; i++) {
    const cs = await fetchKlines(symbol, cursor, 1000, 0, undefined, '1m')
    if (!cs.length) break
    for (const c of cs) if (c.time * 1000 >= fromMs && c.time * 1000 <= toMs) out.push(c)
    const lastMs = cs[cs.length - 1].time * 1000
    if (cs.length < 1000 || lastMs <= cursor) break
    cursor = lastMs + 60000
  }
  const seen = new Set<number>()
  return out.filter(c => (seen.has(c.time) ? false : (seen.add(c.time), true))).sort((a, b) => a.time - b.time)
}

// ── Chart de velas + cajas de proyección (riesgo/profit) — con diagnóstico ────
function LiveCandleChart({ trade }: { trade: Trade | null }) {
  const boxRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const serRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const tradeRef = useRef<Trade | null>(null)
  const closeMsRef = useRef<number>(0)
  const [status, setStatus] = useState('iniciando…')

  function drawOverlay() {
    const cv = canvasRef.current, box = boxRef.current, ser = serRef.current, chart = chartRef.current
    const t = tradeRef.current
    if (!cv || !box || !ser || !chart || !t) return
    cv.width = box.clientWidth; cv.height = box.clientHeight
    const ctx = cv.getContext('2d')!; ctx.clearRect(0, 0, cv.width, cv.height)
    const num = (c: unknown): number | null => (c == null ? null : (c as number))
    const yE = num(ser.priceToCoordinate(t.entry)), yS = num(ser.priceToCoordinate(t.stop)), yT = num(ser.priceToCoordinate(t.target))
    const minT = (ms: number) => Math.floor(ms / 60000) * 60   // alinear a la vela M1
    const xE = num(chart.timeScale().timeToCoordinate(minT(t.tsMs) as Time))
    const xXraw = num(chart.timeScale().timeToCoordinate(minT(closeMsRef.current) as Time))
    if (yE == null || yS == null || yT == null || xE == null) return
    const xX: number = (xXraw == null || xXraw <= xE) ? Math.min(xE + Math.max(cv.width * 0.3, 120), cv.width) : xXraw
    const w = xX - xE
    const isWin = (t.resultR ?? 0) > 0
    // caja riesgo (entry→stop) roja
    ctx.fillStyle = `rgba(220,38,38,${isWin ? 0.08 : 0.2})`; ctx.fillRect(xE, Math.min(yE, yS), w, Math.abs(yS - yE))
    ctx.strokeStyle = 'rgba(220,38,38,0.8)'; ctx.lineWidth = 1; ctx.strokeRect(xE, Math.min(yE, yS), w, Math.abs(yS - yE))
    // caja profit (entry→target) verde
    ctx.fillStyle = `rgba(22,163,74,${isWin ? 0.26 : 0.1})`; ctx.fillRect(xE, Math.min(yE, yT), w, Math.abs(yT - yE))
    ctx.strokeStyle = 'rgba(22,163,74,0.85)'; ctx.lineWidth = 1; ctx.strokeRect(xE, Math.min(yE, yT), w, Math.abs(yT - yE))
    // entry line + labels
    ctx.strokeStyle = 'rgba(232,232,240,0.6)'; ctx.setLineDash([4, 3]); ctx.beginPath(); ctx.moveTo(xE, yE); ctx.lineTo(xX, yE); ctx.stroke(); ctx.setLineDash([])
    const prec = t.entry > 100 ? 1 : t.entry > 1 ? 4 : 6
    ctx.font = 'bold 11px monospace'; ctx.textAlign = 'left'
    ctx.fillStyle = 'rgba(232,232,240,0.9)'; ctx.fillText(`${t.dir.toUpperCase()}  ${t.entry.toFixed(prec)}`, xE + 4, yE - 4)
    ctx.fillStyle = 'rgba(220,38,38,0.95)'; ctx.fillText(`SL ${t.stop.toFixed(prec)}`, xX + 4, yS + 4)
    ctx.fillStyle = 'rgba(22,163,74,0.95)'; ctx.fillText(`TP ${t.target.toFixed(prec)}`, xX + 4, yT + 4)
    const r = t.resultR ?? 0
    ctx.fillStyle = r > 0 ? 'rgba(22,163,74,0.95)' : 'rgba(220,38,38,0.95)'
    ctx.font = 'bold 13px monospace'; ctx.fillText(`${r >= 0 ? '+' : ''}${r.toFixed(2)}R  ${t.pnlUsd >= 0 ? '+$' : '-$'}${Math.abs(t.pnlUsd).toFixed(1)}`, xE + 4, Math.min(yE, yT) - 8)
  }

  useEffect(() => {
    const box = boxRef.current
    if (!box) return
    const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim()
    const chart = createChart(box, {
      width: box.clientWidth || 600, height: box.clientHeight || 400,
      layout: { background: { color: css('--bg') || '#0d0d12' }, textColor: css('--text2') || '#999' },
      grid: { vertLines: { color: css('--border') || '#222' }, horzLines: { color: css('--border') || '#222' } },
      timeScale: { timeVisible: true, secondsVisible: false }, crosshair: { mode: 1 },
    })
    const ser = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e', downColor: '#ef4444', borderUpColor: '#22c55e', borderDownColor: '#ef4444',
      wickUpColor: '#22c55e', wickDownColor: '#ef4444',
      // extender el autoescalado para que entry/stop/target SIEMPRE estén en rango
      autoscaleInfoProvider: (orig: () => { priceRange: { minValue: number; maxValue: number } } | null) => {
        const r = orig()
        const t = tradeRef.current
        if (!r || !t) return r
        const lv = [t.entry, t.stop, t.target].filter(Number.isFinite)
        return { priceRange: { minValue: Math.min(r.priceRange.minValue, ...lv), maxValue: Math.max(r.priceRange.maxValue, ...lv) } }
      },
    } as Parameters<typeof chart.addSeries>[1])
    chartRef.current = chart; serRef.current = ser
    const ro = new ResizeObserver(() => { if (boxRef.current) { chart.applyOptions({ width: boxRef.current.clientWidth, height: boxRef.current.clientHeight }); drawOverlay() } })
    ro.observe(box)
    chart.timeScale().subscribeVisibleLogicalRangeChange(drawOverlay)
    chart.subscribeCrosshairMove(drawOverlay)
    setStatus(`chart montado (${box.clientWidth}×${box.clientHeight}px)`)
    return () => { ro.disconnect(); chart.remove(); chartRef.current = null; serRef.current = null }
  }, [])

  useEffect(() => {
    const ser = serRef.current, chart = chartRef.current
    if (!trade || !ser || !chart) return
    tradeRef.current = trade
    closeMsRef.current = trade.closedAt ? new Date(trade.closedAt).getTime() : trade.tsMs + (trade.durationMin ?? 90) * 60000
    let alive = true
    setStatus(`pidiendo velas ${trade.sym}…`)
    const fromMs = trade.tsMs - 24 * 3600000          // 24h antes del entry
    const toMs = closeMsRef.current + 24 * 3600000     // 24h después del cierre
    loadRangeM1(trade.sym, fromMs, toMs).then(cs => {
      if (!alive || serRef.current !== ser) return
      if (!cs.length) { setStatus(`⚠ Bybit devolvió 0 velas para ${trade.sym} @ ${new Date(trade.tsMs).toISOString().slice(0, 16)}`); return }
      setStatus('')
      ser.setData(cs.map(c => ({ time: c.time as Time, open: c.open, high: c.high, low: c.low, close: c.close })))
      // centrar por ÍNDICE de vela (robusto: siempre válido aunque falten datos en los bordes)
      const entrySec = Math.floor(trade.tsMs / 60000) * 60
      const closeSec = Math.floor(closeMsRef.current / 60000) * 60
      let ei = cs.findIndex(c => c.time >= entrySec); if (ei < 0) ei = 0
      let ci = cs.findIndex(c => c.time >= closeSec); if (ci < 0) ci = cs.length - 1
      const span = Math.max(ci - ei, 1)
      const margin = Math.max(span * 1.2, 360)   // ~6h de contexto mínimo a cada lado
      requestAnimationFrame(() => {
        if (serRef.current !== ser) return
        chart.timeScale().setVisibleLogicalRange({ from: ei - margin, to: ci + margin })
        requestAnimationFrame(drawOverlay)
      })
    }).catch(e => { if (alive) setStatus('✕ error fetch: ' + String(e).slice(0, 90)) })
    return () => { alive = false }
  }, [trade?.id])

  return (
    <div style={{ flex: 1, position: 'relative', minWidth: 0, minHeight: 0, background: 'var(--bg)' }}>
      <div ref={boxRef} style={{ position: 'absolute', inset: 0 }} />
      <canvas ref={canvasRef} style={{ position: 'absolute', inset: 0, pointerEvents: 'none', zIndex: 5 }} />
      {status && <div style={{ position: 'absolute', top: 8, left: 10, fontSize: 11, color: status.startsWith('⚠') || status.startsWith('✕') ? 'var(--red)' : 'var(--text3)', zIndex: 6, fontFamily: 'var(--mono)', background: 'rgba(0,0,0,0.4)', padding: '2px 6px', borderRadius: 3 }}>{status}</div>}
    </div>
  )
}

// ── Tabla de trades clara ─────────────────────────────────────────────────────
function TradeTable({ trades, sel, onSel }: { trades: Trade[]; sel: Trade | null; onSel: (t: Trade) => void }) {
  return (
    <div style={{ width: 360, flexShrink: 0, overflow: 'auto', borderRight: '1px solid var(--border)', background: 'var(--bg2)' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--mono)' }}>
        <thead>
          <tr style={{ position: 'sticky', top: 0, background: 'var(--bg2)' }}>
            {['#', 'Sym', 'Dir', 'Entry', 'Exit', 'R', '$', 'Salida'].map((h, i) => (
              <th key={h} style={{ padding: '5px 6px', fontSize: 8, color: 'var(--text3)', textAlign: i < 3 ? 'left' : 'right', borderBottom: '1px solid var(--border)', textTransform: 'uppercase' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => {
            const r = t.resultR ?? 0
            const on = sel?.id === t.id
            return (
              <tr key={t.id} onClick={() => onSel(t)} style={{ cursor: 'pointer', background: on ? 'var(--bg3)' : 'transparent', borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '4px 6px', fontSize: 9, color: 'var(--text3)' }}>{i + 1}</td>
                <td style={{ padding: '4px 6px', fontSize: 10, color: 'var(--text2)', fontWeight: 700 }}>{t.sym.replace('USDT', '')}</td>
                <td style={{ padding: '4px 6px', fontSize: 10, color: t.dir === 'Long' ? 'var(--green)' : 'var(--red)', fontWeight: 700 }}>{t.dir === 'Long' ? 'L' : 'S'}</td>
                <td style={{ padding: '4px 6px', fontSize: 9.5, color: 'var(--text3)', textAlign: 'right' }}>{t.entry}</td>
                <td style={{ padding: '4px 6px', fontSize: 9.5, color: 'var(--text3)', textAlign: 'right' }}>{t.exit || '—'}</td>
                <td style={{ padding: '4px 6px', fontSize: 10, textAlign: 'right', fontWeight: 700, color: r > 0 ? 'var(--green)' : r < 0 ? 'var(--red)' : 'var(--text3)' }}>{r >= 0 ? '+' : ''}{r.toFixed(2)}</td>
                <td style={{ padding: '4px 6px', fontSize: 9.5, textAlign: 'right', color: t.pnlUsd >= 0 ? 'var(--green)' : 'var(--red)' }}>{t.pnlUsd >= 0 ? '+' : ''}{t.pnlUsd.toFixed(1)}</td>
                <td style={{ padding: '4px 6px', fontSize: 9, textAlign: 'right', color: 'var(--text3)' }}>{t.reason}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

const KIND_LABEL: Record<string, string> = {
  poc_ob: 'POC Order Block', poc_def: 'POC Defendido (long)', poc_def_short: 'POC Defendido (short)',
}

function Row({ k, v, c }: { k: string; v: React.ReactNode; c?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', padding: '3px 0', borderBottom: '1px solid rgba(120,120,140,0.12)', gap: 6 }}>
      <span style={{ color: 'var(--text3)', fontSize: 9, textTransform: 'uppercase', flexShrink: 0 }}>{k}</span>
      <span style={{ color: c ?? 'var(--text)', fontSize: 10, fontWeight: 600, textAlign: 'right', fontFamily: 'var(--mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{v}</span>
    </div>
  )
}
function Sec({ label }: { label: string }) {
  return <div style={{ marginTop: 10, marginBottom: 3, color: 'var(--text3)', fontSize: 8.5, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1.2 }}>{label}</div>
}

function DetailPanel({ t }: { t: Trade | null }) {
  if (!t) return null
  const h = (t.htf ?? {}) as Record<string, unknown>
  const r = t.resultR ?? 0
  const rCol = r > 0 ? 'var(--green)' : r < 0 ? 'var(--red)' : 'var(--text3)'
  const risk = Math.abs(t.entry - t.stop), rr = risk > 0 ? Math.abs(t.target - t.entry) / risk : 0
  const d = new Date(t.tsMs)
  const fdate = `${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')} ${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')}`
  const kind = String(h.kind ?? '')
  return (
    <div style={{ width: 218, flexShrink: 0, borderLeft: '1px solid var(--border)', background: 'var(--bg)', padding: 9, overflowY: 'auto' }}>
      <div style={{ fontWeight: 700, marginBottom: 5, color: 'var(--text2)', fontSize: 9, textTransform: 'uppercase', letterSpacing: 1 }}>Trade #{t.idx}</div>
      <Row k="Symbol" v={t.sym.replace('USDT', '')} />
      <Row k="Dir" v={t.dir} c={t.dir === 'Long' ? 'var(--green)' : 'var(--red)'} />
      <Row k="Entry at" v={`${fdate} UTC`} />
      <Row k="Resultado" v={`${r >= 0 ? '+' : ''}${r.toFixed(3)}R`} c={rCol} />
      <Row k="PnL" v={`${t.pnlUsd >= 0 ? '+$' : '-$'}${Math.abs(t.pnlUsd).toFixed(2)}`} c={rCol} />
      <Row k="Salida" v={t.reason} c={t.reason === 'target' ? 'var(--green)' : t.reason === 'stop' ? 'var(--red)' : undefined} />
      {t.durationMin != null && <Row k="Duración" v={t.durationMin >= 60 ? `${(t.durationMin / 60).toFixed(1)}h` : `${t.durationMin}m`} />}

      <Sec label="Estrategia" />
      <Row k="Setup" v={KIND_LABEL[kind] ?? kind} c="var(--blue)" />
      <Row k="Gestión" v={String(h.gestion ?? '')} c={h.gestion === 'trail' ? 'var(--blue)' : 'var(--yellow)'} />
      <Row k="Sistema" v={String(h.system ?? '')} />
      <Row k="Régimen" v={t.regime} c={t.regime === 'trend' ? 'var(--blue)' : 'var(--text2)'} />
      <Row k="Vol regime" v={String(h.volRegime ?? '')} c={h.volRegime === 'high' ? 'var(--green)' : 'var(--text3)'} />

      <Sec label="Precios" />
      <Row k="Entry" v={t.entry} />
      <Row k="Stop" v={t.stop} c="var(--red)" />
      <Row k="Target" v={t.target} c="var(--green)" />
      {t.exit > 0 && <Row k="Exit" v={t.exit} c="var(--yellow)" />}
      <Row k="Stop %" v={`${t.stopPct.toFixed(3)}%`} />
      <Row k="R:R" v={`${rr.toFixed(2)} : 1`} />
      <Row k="Risk $" v={`$${t.riskUsd.toFixed(2)}`} />

      <Sec label="Fuente" />
      <Row k="Tipo" v={h.reconstructed ? 'reconstruido' : 'nativo (real)'} c={h.reconstructed ? 'var(--yellow)' : 'var(--green)'} />
    </div>
  )
}

function TradesPanel({ trades }: { trades: Trade[] }) {
  const [sel, setSel] = useState<Trade | null>(trades[0] ?? null)
  const cur = sel ? (trades.find(t => t.id === sel.id) ?? trades[0]) : trades[0]
  return (
    <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0, minWidth: 0 }}>
      <TradeTable trades={trades} sel={cur} onSel={setSel} />
      <LiveCandleChart trade={cur} />
      <DetailPanel t={cur} />
    </div>
  )
}

// ── Equity curve con ALTURA FIJA (no depende de flex height) ──────────────────
function EquityChartFixed({ trades }: { trades: Trade[] }) {
  const pts = [ACC, ...trades.filter(t => !t.isOpen).map(t => t.equity)]
  const W = 1000, H = 260, RPad = 56, TPad = 14, BPad = 14
  if (pts.length < 2) return <div style={{ height: H, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text3)' }}>Sin trades</div>
  const cW = W - RPad, cH = H - TPad - BPad
  const min = Math.min(...pts), max = Math.max(...pts)
  const pad = (max - min) * 0.12 || 10
  const lo = min - pad, hi = max + pad
  const xp = (i: number) => (i / (pts.length - 1)) * cW
  const yp = (v: number) => TPad + cH - ((v - lo) / (hi - lo)) * cH
  const d = pts.map((v, i) => `${i === 0 ? 'M' : 'L'}${xp(i).toFixed(1)},${yp(v).toFixed(1)}`).join(' ')
  const area = d + ` L${xp(pts.length - 1).toFixed(1)},${TPad + cH} L0,${TPad + cH} Z`
  const up = pts[pts.length - 1] >= ACC
  const stroke = up ? '#22c55e' : '#ef4444'
  const range = hi - lo
  const step = range > 500 ? 200 : range > 100 ? 50 : range > 20 ? 10 : 5
  const grid = Array.from({ length: Math.ceil(range / step) + 2 }, (_, i) => Math.floor(lo / step) * step + i * step).filter(v => v > lo && v < hi)
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" style={{ display: 'block' }}>
      <defs>
        <linearGradient id="liq-eq-g" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={stroke} stopOpacity="0.18" />
          <stop offset="100%" stopColor={stroke} stopOpacity="0" />
        </linearGradient>
      </defs>
      {grid.map(v => (
        <g key={v}>
          <line x1={0} y1={yp(v)} x2={cW} y2={yp(v)} stroke="var(--border)" strokeWidth={1} />
          <text x={cW + 6} y={yp(v) + 4} fontSize={12} fill="var(--text3)" fontFamily="var(--mono)">${v.toFixed(0)}</text>
        </g>
      ))}
      <line x1={0} y1={yp(ACC)} x2={cW} y2={yp(ACC)} stroke="var(--blue)" strokeWidth={1} strokeDasharray="6,4" strokeOpacity="0.5" />
      <text x={cW + 6} y={yp(ACC) + 4} fontSize={12} fill="var(--blue)" fontFamily="var(--mono)" fillOpacity="0.7">${ACC}</text>
      <path d={area} fill="url(#liq-eq-g)" />
      <path d={d} fill="none" stroke={stroke} strokeWidth={2.5} strokeLinejoin="round" />
      {pts.map((v, i) => (i === pts.length - 1
        ? <circle key={i} cx={xp(i)} cy={yp(v)} r={5} fill={stroke} stroke="var(--bg2)" strokeWidth={2} />
        : null))}
    </svg>
  )
}

function rcol(v: number) { return v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--text3)' }

function StatsPanel({ trades }: { trades: Trade[] }) {
  const closed = trades.filter(t => !t.isOpen)
  const final = closed.length ? closed[closed.length - 1].equity : ACC
  const pct = (final - ACC) / ACC * 100
  const totalR = closed.reduce((s, t) => s + (t.resultR ?? 0), 0)
  const wins = closed.filter(t => (t.resultR ?? 0) > 0).length
  const wr = closed.length ? wins / closed.length * 100 : 0
  let peak = ACC, maxDD = 0
  for (const t of closed) { if (t.equity > peak) peak = t.equity; const dd = (peak - t.equity) / peak * 100; if (dd > maxDD) maxDD = dd }
  const kpis = [
    { k: 'Equity Final', v: `$${final.toFixed(2)}`, s: `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`, c: rcol(final - ACC) },
    { k: 'Total R', v: `${totalR >= 0 ? '+' : ''}${totalR.toFixed(1)}R`, s: `${closed.length} trades`, c: rcol(totalR) },
    { k: 'Win Rate', v: `${wr.toFixed(0)}%`, s: `${wins}W / ${closed.length - wins}L`, c: wr >= 45 ? 'var(--green)' : 'var(--red)' },
    { k: 'Max Drawdown', v: maxDD > 0 ? `-${maxDD.toFixed(1)}%` : '—', s: `peak $${peak.toFixed(0)}`, c: maxDD > 15 ? 'var(--red)' : 'var(--yellow)' },
  ]
  const syms = [...new Set(closed.map(t => t.sym))].sort()
  const byGroup = (key: (t: Trade) => string, labels?: string[]) => {
    const keys = labels ?? [...new Set(closed.map(key))]
    return keys.map(k => {
      const g = closed.filter(t => key(t) === k)
      const tr = g.reduce((s, t) => s + (t.resultR ?? 0), 0)
      const w = g.filter(t => (t.resultR ?? 0) > 0).length
      return { k, n: g.length, wr: g.length ? w / g.length * 100 : 0, tr }
    }).filter(r => r.n > 0)
  }
  const rows = [
    { title: 'Por Símbolo', data: byGroup(t => t.sym.replace('USDT', ''), syms.map(s => s.replace('USDT', ''))) },
    { title: 'Long vs Short', data: byGroup(t => t.dir) },
    { title: 'Por Salida', data: byGroup(t => t.reason) },
  ]
  return (
    <div style={{ flex: 1, overflow: 'auto', background: 'var(--bg)' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', borderBottom: '1px solid var(--border)', background: 'var(--bg2)' }}>
        {kpis.map((k, i) => (
          <div key={i} style={{ borderRight: i < 3 ? '1px solid var(--border)' : 'none', padding: '12px 18px', display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 9, fontWeight: 600, textTransform: 'uppercase', letterSpacing: 1.5, color: 'var(--text3)' }}>{k.k}</span>
            <span style={{ fontSize: 24, fontWeight: 700, fontFamily: 'var(--mono)', color: k.c, lineHeight: 1 }}>{k.v}</span>
            <span style={{ fontSize: 10, color: 'var(--text3)' }}>{k.s}</span>
          </div>
        ))}
      </div>
      <div style={{ padding: '10px 14px' }}>
        <div style={{ fontSize: 9, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1.5, color: 'var(--text3)', marginBottom: 4 }}>Equity Curve · ${ACC} @ 1%/trade</div>
        <div style={{ border: '1px solid var(--border)', borderRadius: 8, background: 'var(--bg2)', overflow: 'hidden' }}>
          <EquityChartFixed trades={trades} />
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10, padding: '0 14px 14px' }}>
        {rows.map(r => (
          <div key={r.title} style={{ border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
            <div style={{ padding: '5px 10px', background: 'var(--bg2)', fontSize: 8, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1.2, color: 'var(--text3)', borderBottom: '1px solid var(--border)' }}>{r.title}</div>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <tbody>
                {r.data.map(d => (
                  <tr key={d.k} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '4px 10px', fontSize: 11, color: 'var(--text2)' }}>{d.k}</td>
                    <td style={{ padding: '4px 8px', fontSize: 10, color: 'var(--text3)', textAlign: 'right', fontFamily: 'var(--mono)' }}>{d.n}</td>
                    <td style={{ padding: '4px 8px', fontSize: 10, textAlign: 'right', fontFamily: 'var(--mono)', color: d.wr >= 45 ? 'var(--green)' : 'var(--red)' }}>{d.wr.toFixed(0)}%</td>
                    <td style={{ padding: '4px 10px', fontSize: 10, textAlign: 'right', fontFamily: 'var(--mono)', fontWeight: 700, color: rcol(d.tr) }}>{d.tr >= 0 ? '+' : ''}{d.tr.toFixed(1)}R</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    </div>
  )
}

type Panel = 'stats' | 'trades'
type Sym = '' | 'BTCUSDT' | 'ETHUSDT' | 'SOLUSDT'
type Source = 'all' | 'reconstructed' | 'native'

export default function LivePaperView() {
  const [panel, setPanel] = useState<Panel>('stats')
  const [sym, setSym] = useState<Sym>('')
  const [source, setSource] = useState<Source>('all')
  const [trades, setTrades] = useState<Trade[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setLoading(true); setError(null)
    const opts: LiqOpts = { source }
    if (sym) opts.symbol = sym
    fetchLiquidityTrades(opts)
      .then(({ trades }) => { if (alive) setTrades(trades) })
      .catch(e => { if (alive) setError(String(e)) })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [sym, source])

  const n = trades.length
  const netR = trades.reduce((s, t) => s + (t.resultR ?? 0), 0)
  const wins = trades.filter(t => (t.resultR ?? 0) > 0).length
  const finalEq = trades.length ? trades[trades.length - 1].equity : 500
  const recon = trades.filter(t => (t.htf as any)?.reconstructed).length

  const Btn = <T,>(val: T, cur: T, set: (v: T) => void, label: string) => (
    <button key={String(val)} onClick={() => set(val)} style={{
      padding: '2px 9px', borderRadius: 3, fontSize: 10, cursor: 'pointer', fontFamily: 'inherit', fontWeight: 700,
      background: cur === val ? 'var(--blue)' : 'var(--bg3)',
      border: `1px solid ${cur === val ? 'var(--blue)' : 'var(--border2)'}`,
      color: cur === val ? '#fff' : 'var(--text3)',
    }}>{label}</button>
  )

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, padding: '0 8px', height: 30, flexShrink: 0,
        borderBottom: '1px solid var(--border)', background: 'var(--bg2)', fontSize: 10, color: 'var(--text3)',
      }}>
        {(['stats', 'trades'] as Panel[]).map(p => (
          <button key={p} onClick={() => setPanel(p)} style={{
            padding: '2px 10px', borderRadius: 3, fontSize: 10, cursor: 'pointer', fontFamily: 'inherit',
            background: panel === p ? 'var(--bg3)' : 'none',
            border: `1px solid ${panel === p ? 'var(--border2)' : 'transparent'}`,
            color: panel === p ? 'var(--text)' : 'var(--text3)',
          }}>{p === 'stats' ? 'Equity & Stats' : 'Trades'}</button>
        ))}

        <span style={{ display: 'flex', gap: 3, marginLeft: 6, borderLeft: '1px solid var(--border)', paddingLeft: 8 }}>
          {Btn<Sym>('', sym, setSym, 'Todos')}
          {Btn<Sym>('BTCUSDT', sym, setSym, 'BTC')}
          {Btn<Sym>('ETHUSDT', sym, setSym, 'ETH')}
          {Btn<Sym>('SOLUSDT', sym, setSym, 'SOL')}
        </span>
        <span style={{ display: 'flex', gap: 3, marginLeft: 4, borderLeft: '1px solid var(--border)', paddingLeft: 8 }}>
          {Btn<Source>('all', source, setSource, 'Todos')}
          {Btn<Source>('reconstructed', source, setSource, 'Reconstr.')}
          {Btn<Source>('native', source, setSource, 'Nativos')}
        </span>

        {!loading && !error && (
          <span style={{ color: 'var(--text2)', marginLeft: 8 }}>
            {n} trades{recon > 0 && <span style={{ color: 'var(--yellow)' }}> ({recon} reconstr.)</span>} ·{' '}
            <span style={{ color: netR >= 0 ? 'var(--green)' : 'var(--red)' }}>{netR >= 0 ? '+' : ''}{netR.toFixed(1)}R</span> ·{' '}
            <span style={{ color: n && wins / n >= 0.4 ? 'var(--green)' : 'var(--red)' }}>WR {n ? (wins / n * 100).toFixed(0) : '—'}%</span> ·{' '}
            <span style={{ color: finalEq >= 500 ? 'var(--green)' : 'var(--red)' }}>${finalEq.toFixed(2)}</span>
          </span>
        )}
        <span style={{ marginLeft: 'auto', color: 'var(--text3)', fontSize: 9 }}>paper live · Supabase · $500 @ 1%/trade</span>
      </div>

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>
        {loading && <Centered>Cargando paper trades de Supabase…</Centered>}
        {error && <Centered color="var(--red)">{error}</Centered>}
        {!loading && !error && n === 0 && <Centered>Sin trades para este filtro.</Centered>}
        {!loading && !error && n > 0 && (panel === 'stats' ? <StatsPanel trades={trades} /> : <TradesPanel trades={trades} />)}
      </div>
    </div>
  )
}

function Centered({ children, color }: { children: React.ReactNode; color?: string }) {
  return (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: color ?? 'var(--text3)', fontSize: 11, textAlign: 'center', padding: 20 }}>
      {children}
    </div>
  )
}
