// ComparisonPane — overlaid % change of BTC / ETH / SOL from the range start.
import { useEffect, useRef } from 'react'
import { createChart, LineSeries, type ISeriesApi, type Time } from 'lightweight-charts'
import { BybitWS } from '../lib/bybitWs'
import { fetchLinearKlines } from '../lib/klines'
import { T } from '../theme'

const PEERS = [
  { sym: 'BTCUSDT', color: '#2fe06a' },
  { sym: 'ETHUSDT', color: '#4493f8' },
  { sym: 'SOLUSDT', color: '#c084fc' },
]

export default function ComparisonPane({ interval }: { interval: string }) {
  const wrapRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!wrapRef.current) return
    const chart = createChart(wrapRef.current, {
      autoSize: true,
      layout: { background: { color: T.panel }, textColor: T.muted, fontSize: 10, attributionLogo: false },
      grid: { vertLines: { color: T.grid }, horzLines: { color: T.grid } },
      timeScale: { timeVisible: true, secondsVisible: false, borderColor: T.border },
      rightPriceScale: { borderColor: T.border },
      crosshair: { mode: 0 },
    })

    let live = true
    const wss: BybitWS[] = []
    const bases: Record<string, number> = {}
    const series: Record<string, ISeriesApi<'Line', Time>> = {}

    PEERS.forEach((p) => {
      const s = chart.addSeries(LineSeries, { color: p.color, lineWidth: 2, priceFormat: { type: 'percent' } })
      series[p.sym] = s
      fetchLinearKlines(p.sym, interval, 300).then((cs) => {
        if (!live || !cs.length) return
        const base = cs[0].close
        bases[p.sym] = base
        s.setData(cs.map((c) => ({ time: c.time as Time, value: ((c.close - base) / base) * 100 })))
        chart.timeScale().fitContent()
      })
      const ws = new BybitWS(p.sym, interval, {
        onKline: (k) => {
          const base = bases[p.sym]
          if (base) s.update({ time: (k.start / 1000) as Time, value: ((k.close - base) / base) * 100 })
        },
      })
      ws.connect()
      wss.push(ws)
    })

    return () => { live = false; wss.forEach((w) => w.close()); chart.remove() }
  }, [interval])

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative' }}>
      <div style={{ position: 'absolute', top: 6, left: 10, zIndex: 4, display: 'flex', gap: 10, fontSize: 11 }}>
        {PEERS.map((p) => (
          <span key={p.sym} style={{ color: p.color }}>● {p.sym.replace('USDT', '')}</span>
        ))}
      </div>
      <div ref={wrapRef} style={{ width: '100%', height: '100%' }} />
    </div>
  )
}
