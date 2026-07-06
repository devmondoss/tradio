// TimeSalesPane — live trade tape; big prints highlighted, coloured by aggressor.
import { useEffect, useRef, useState } from 'react'
import { BybitWS, type BybitTrade } from '../lib/bybitWs'
import { beepBigTrade } from '../lib/sound'
import { T } from '../theme'

export default function TimeSalesPane({ symbol }: { symbol: string }) {
  const [rows, setRows] = useState<BybitTrade[]>([])
  const buf = useRef<BybitTrade[]>([])

  useEffect(() => {
    buf.current = []
    setRows([])
    const ws = new BybitWS(symbol, '5', {
      onTrade: (t) => {
        buf.current = [t, ...buf.current].slice(0, 60)
        const sizes = buf.current.map((r) => r.qty).sort((a, b) => a - b)
        const thr = sizes[Math.floor(sizes.length * 0.95)] ?? Infinity
        if (buf.current.length >= 20 && t.qty >= thr && t.qty > 0) beepBigTrade(t.side === 'Buy')
      },
    })
    ws.connect()
    const id = setInterval(() => setRows(buf.current.slice()), 250)
    return () => { clearInterval(id); ws.close() }
  }, [symbol])

  // "big" = size in the top decile of what's on screen
  const sizes = rows.map((r) => r.qty).sort((a, b) => a - b)
  const bigThr = sizes.length ? sizes[Math.floor(sizes.length * 0.9)] : Infinity

  return (
    <div style={{ height: '100%', overflow: 'hidden', fontSize: 11, fontFamily: '"Azeret Mono", monospace' }}>
      <div style={{ display: 'flex', color: T.dim, padding: '4px 10px', borderBottom: `1px solid ${T.border}` }}>
        <span style={{ flex: 1 }}>hora</span>
        <span style={{ width: 80, textAlign: 'right' }}>precio</span>
        <span style={{ width: 70, textAlign: 'right' }}>tamaño</span>
      </div>
      <div style={{ overflow: 'hidden' }}>
        {rows.map((t, i) => {
          const buy = t.side === 'Buy'
          const big = t.qty >= bigThr
          const time = new Date(t.ts)
          const hh = `${String(time.getHours()).padStart(2, '0')}:${String(time.getMinutes()).padStart(2, '0')}:${String(time.getSeconds()).padStart(2, '0')}`
          return (
            <div
              key={t.ts + '' + i}
              style={{
                display: 'flex', padding: '1.5px 10px',
                background: big ? (buy ? 'rgba(81,205,160,0.12)' : 'rgba(192,80,77,0.12)') : 'transparent',
                color: buy ? T.green : T.red, fontWeight: big ? 700 : 400,
              }}
            >
              <span style={{ flex: 1, color: T.dim }}>{hh}</span>
              <span style={{ width: 80, textAlign: 'right' }}>{t.price.toFixed(1)}</span>
              <span style={{ width: 70, textAlign: 'right' }}>{t.qty.toFixed(3)}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
