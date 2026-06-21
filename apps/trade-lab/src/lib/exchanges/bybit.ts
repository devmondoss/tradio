import type { Candle } from './types'
import { TF_SECONDS } from './types'

const BASE = 'https://api.bybit.com/v5/market/kline'

// Bybit interval labels differ from our internal TF keys
const TF_BYBIT: Record<string, string> = {
  '1m': '1', '5m': '5', '15m': '15', '1h': '60', '4h': '240',
}

const cache = new Map<string, Candle[]>()

export async function fetchKlines(
  symbol: string,
  startMs: number,
  limit = 300,
  extraBackBars = 100,
  endMs?: number,
  interval = '1m',
): Promise<Candle[]> {
  const barSec    = TF_SECONDS[interval] ?? 60
  const backMs    = extraBackBars * barSec * 1000
  const from      = startMs - backMs
  const bybitInt  = TF_BYBIT[interval] ?? '1'
  const key       = `bybit-${symbol}-${bybitInt}-${from}-${limit}-${endMs ?? 0}`

  if (cache.has(key)) return cache.get(key)!

  const params = new URLSearchParams({
    category: 'spot',
    symbol,
    interval: bybitInt,
    start:  String(from),
    limit:  String(Math.min(limit, 1000)),
  })
  if (endMs) params.set('end', String(endMs))

  try {
    const res = await fetch(`${BASE}?${params}`)
    if (!res.ok) {
      console.warn(`[bybit] ${symbol} HTTP ${res.status}`)
      return []
    }
    const json = await res.json()
    if (json.retCode !== 0 || !Array.isArray(json.result?.list)) {
      console.warn(`[bybit] ${symbol} bad response`, json.retMsg)
      return []
    }
    // Bybit returns newest-first → reverse
    const candles: Candle[] = (json.result.list as string[][])
      .map(k => ({
        time:  Math.floor(Number(k[0]) / 1000),
        open:  Number(k[1]),
        high:  Number(k[2]),
        low:   Number(k[3]),
        close: Number(k[4]),
      }))
      .reverse()

    if (candles.length > 0) cache.set(key, candles)
    return candles
  } catch (e) {
    console.warn(`[bybit] ${symbol} error:`, e)
    return []
  }
}
