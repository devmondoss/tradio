// bybitWs.ts — Live Bybit v5 public WebSocket (linear perps).
// Subscribes to trades + orderbook + kline for one symbol and emits parsed events.
// Keeps a running local orderbook (snapshot + deltas). Self-reconnecting.

export interface BybitTrade {
  ts: number
  side: 'Buy' | 'Sell'
  price: number
  qty: number
}

export interface BookLevel {
  price: number
  qty: number
}

export interface BybitBook {
  bids: BookLevel[] // sorted desc (best first)
  asks: BookLevel[] // sorted asc (best first)
}

export interface BybitKline {
  start: number
  open: number
  high: number
  low: number
  close: number
  volume: number
  confirm: boolean
}

interface Handlers {
  onTrade?: (t: BybitTrade) => void
  onBook?: (b: BybitBook) => void
  onKline?: (k: BybitKline) => void
  onStatus?: (s: 'connected' | 'disconnected') => void
}

const WS_URL = 'wss://stream.bybit.com/v5/public/linear'

export class BybitWS {
  private ws?: WebSocket
  private bids = new Map<number, number>()
  private asks = new Map<number, number>()
  private closed = false
  private symbol: string
  private interval: string // e.g. '5' for 5m
  private h: Handlers

  constructor(symbol: string, interval: string, h: Handlers) {
    this.symbol = symbol
    this.interval = interval
    this.h = h
  }

  connect() {
    this.closed = false
    const ws = new WebSocket(WS_URL)
    this.ws = ws
    ws.onopen = () => {
      ws.send(
        JSON.stringify({
          op: 'subscribe',
          args: [
            `publicTrade.${this.symbol}`,
            `orderbook.50.${this.symbol}`,
            `kline.${this.interval}.${this.symbol}`,
          ],
        }),
      )
      this.h.onStatus?.('connected')
    }
    ws.onclose = () => {
      this.h.onStatus?.('disconnected')
      if (!this.closed) setTimeout(() => this.connect(), 2000)
    }
    ws.onerror = () => ws.close()
    ws.onmessage = (ev) => {
      try {
        this.handle(JSON.parse(ev.data as string))
      } catch {
        /* ignore malformed frames */
      }
    }
  }

  private handle(msg: {
    topic?: string
    type?: string
    data?: unknown
  }) {
    const topic = msg.topic ?? ''
    if (topic.startsWith('publicTrade')) {
      for (const d of msg.data as Array<{ T: number; S: string; p: string; v: string }>) {
        this.h.onTrade?.({ ts: d.T, side: d.S as 'Buy' | 'Sell', price: +d.p, qty: +d.v })
      }
    } else if (topic.startsWith('orderbook')) {
      const d = msg.data as { b: [string, string][]; a: [string, string][] }
      if (msg.type === 'snapshot') {
        this.bids.clear()
        this.asks.clear()
      }
      for (const [p, s] of d.b) {
        const pr = +p, sz = +s
        if (sz === 0) this.bids.delete(pr)
        else this.bids.set(pr, sz)
      }
      for (const [p, s] of d.a) {
        const pr = +p, sz = +s
        if (sz === 0) this.asks.delete(pr)
        else this.asks.set(pr, sz)
      }
      this.h.onBook?.(this.snapshot())
    } else if (topic.startsWith('kline')) {
      for (const d of msg.data as Array<{
        start: number; open: string; high: string; low: string
        close: string; volume: string; confirm: boolean
      }>) {
        this.h.onKline?.({
          start: d.start,
          open: +d.open,
          high: +d.high,
          low: +d.low,
          close: +d.close,
          volume: +d.volume,
          confirm: d.confirm,
        })
      }
    }
  }

  private snapshot(): BybitBook {
    const bids = [...this.bids.entries()]
      .map(([price, qty]) => ({ price, qty }))
      .sort((a, b) => b.price - a.price)
    const asks = [...this.asks.entries()]
      .map(([price, qty]) => ({ price, qty }))
      .sort((a, b) => a.price - b.price)
    return { bids, asks }
  }

  close() {
    this.closed = true
    this.ws?.close()
  }
}
