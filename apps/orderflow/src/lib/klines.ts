// klines.ts — historical linear-perp candles from Bybit REST + helpers.

export interface Candle { time: number; open: number; high: number; low: number; close: number; volume: number }

export async function fetchLinearKlines(symbol: string, interval: string, limit = 400): Promise<Candle[]> {
  const url = `https://api.bybit.com/v5/market/kline?category=linear&symbol=${symbol}&interval=${interval}&limit=${limit}`
  const res = await fetch(url)
  const json = await res.json()
  if (json.retCode !== 0 || !Array.isArray(json.result?.list)) return []
  return (json.result.list as string[][])
    .map((k) => ({ time: Math.floor(Number(k[0]) / 1000), open: +k[1], high: +k[2], low: +k[3], close: +k[4], volume: +k[5] }))
    .reverse()
}

/** Seconds per bar for a Bybit interval label (numeric = minutes). */
export function intervalSec(interval: string): number {
  const n = Number(interval)
  if (!Number.isNaN(n)) return n * 60
  if (interval === 'D') return 86400
  if (interval === 'W') return 604800
  return 300
}

export const INTERVALS = ['1', '5', '15', '60', '240'] as const
export const intervalLabel = (i: string) =>
  ({ '1': '1m', '5': '5m', '15': '15m', '60': '1h', '240': '4h' })[i] ?? `${i}m`
