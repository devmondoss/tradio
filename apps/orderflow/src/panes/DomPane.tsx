// DomPane — live DOM ladder (order book) with wall highlighting, configurable depth.
import { useEffect, useState } from 'react'
import { BybitWS, type BybitBook } from '../lib/bybitWs'
import { T } from '../theme'

export default function DomPane({ symbol, depth = 18 }: { symbol: string; depth?: number }) {
  const [book, setBook] = useState<BybitBook>({ bids: [], asks: [] })

  useEffect(() => {
    setBook({ bids: [], asks: [] })
    const ws = new BybitWS(symbol, '5', { onBook: setBook })
    ws.connect()
    return () => ws.close()
  }, [symbol])

  const asks = book.asks.slice(0, depth)
  const bids = book.bids.slice(0, depth)
  const qtys = [...asks, ...bids].map((l) => l.qty).sort((a, b) => a - b)
  const median = qtys.length ? qtys[Math.floor(qtys.length / 2)] : 0
  const maxQ = qtys.length ? qtys[qtys.length - 1] : 1
  const thr = median * 3

  const Row = (l: { price: number; qty: number }, side: 'bid' | 'ask') => {
    const wall = thr > 0 && l.qty >= thr
    const color = side === 'bid' ? T.green : T.red
    const w = Math.min(100, (l.qty / maxQ) * 100)
    return (
      <div key={side + l.price} style={{ position: 'relative', display: 'flex', justifyContent: 'space-between', padding: '1px 6px' }}>
        <div style={{ position: 'absolute', top: 0, bottom: 0, [side === 'bid' ? 'left' : 'right']: 0, width: `${w}%`, background: side === 'bid' ? 'rgba(81,205,160,0.10)' : 'rgba(192,80,77,0.10)' }} />
        <span style={{ position: 'relative', color: T.dim }}>{l.price.toFixed(1)}</span>
        <span style={{ position: 'relative', color, fontWeight: wall ? 800 : 400 }}>{l.qty.toFixed(2)}</span>
      </div>
    )
  }

  return (
    <div style={{ fontFamily: 'monospace', fontSize: 11, height: '100%', overflow: 'hidden', padding: '4px 0' }}>
      {asks.slice().reverse().map((l) => Row(l, 'ask'))}
      <div style={{ borderTop: '1px solid #1c232c', margin: '2px 6px' }} />
      {bids.map((l) => Row(l, 'bid'))}
    </div>
  )
}
