// LinePane — live line chart (close price) for one symbol/interval.
import { useEffect, useRef } from 'react'
import { createChart, LineSeries, type Time } from 'lightweight-charts'
import { BybitWS } from '../lib/bybitWs'
import { fetchLinearKlines, type Candle } from '../lib/klines'
import { T } from '../theme'

export default function LinePane({ symbol, interval }: { symbol: string; interval: string }) {
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
    const series = chart.addSeries(LineSeries, { color: T.green, lineWidth: 2 })

    let live = true
    let curTime = 0
    fetchLinearKlines(symbol, interval).then((cs: Candle[]) => {
      if (!live || !cs.length) return
      series.setData(cs.map((c) => ({ time: c.time as Time, value: c.close })))
      curTime = cs[cs.length - 1].time
      chart.timeScale().fitContent()
    })

    const ws = new BybitWS(symbol, interval, {
      onKline: (k) => {
        curTime = k.start / 1000
        series.update({ time: (k.start / 1000) as Time, value: k.close })
      },
      onTrade: (t) => {
        if (curTime) series.update({ time: curTime as Time, value: t.price })
      },
    })
    ws.connect()

    return () => { live = false; ws.close(); chart.remove() }
  }, [symbol, interval])

  return <div ref={wrapRef} style={{ width: '100%', height: '100%' }} />
}
