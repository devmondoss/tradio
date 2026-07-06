// CandlePane — live candlestick chart (Lightweight Charts) for one symbol/interval.
import { useEffect, useRef } from 'react'
import { createChart, CandlestickSeries, type Time } from 'lightweight-charts'
import { BybitWS } from '../lib/bybitWs'
import { fetchLinearKlines, intervalSec, type Candle } from '../lib/klines'
import { T } from '../theme'

export default function CandlePane({ symbol, interval }: { symbol: string; interval: string }) {
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
    const series = chart.addSeries(CandlestickSeries, {
      upColor: T.green, downColor: T.red,
      wickUpColor: T.green, wickDownColor: T.red, borderVisible: false,
    })

    let live = true
    const secs = intervalSec(interval)
    let cur: Candle | null = null

    fetchLinearKlines(symbol, interval).then((cs) => {
      if (!live || !cs.length) return
      series.setData(cs.map((c) => ({ ...c, time: c.time as Time })))
      cur = cs[cs.length - 1]
      chart.timeScale().fitContent()
    })

    const ws = new BybitWS(symbol, interval, {
      onKline: (k) => {
        series.update({ time: (k.start / 1000) as Time, open: k.open, high: k.high, low: k.low, close: k.close })
        cur = { time: k.start / 1000, open: k.open, high: k.high, low: k.low, close: k.close, volume: 0 }
      },
      onTrade: (t) => {
        if (cur && t.ts / 1000 >= cur.time && t.ts / 1000 < cur.time + secs) {
          cur.high = Math.max(cur.high, t.price)
          cur.low = Math.min(cur.low, t.price)
          cur.close = t.price
          series.update({ time: cur.time as Time, open: cur.open, high: cur.high, low: cur.low, close: cur.close })
        }
      },
    })
    ws.connect()

    return () => { live = false; ws.close(); chart.remove() }
  }, [symbol, interval])

  return <div ref={wrapRef} style={{ width: '100%', height: '100%' }} />
}
