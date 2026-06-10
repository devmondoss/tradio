export interface Candle {
  time: number
  open: number
  high: number
  low: number
  close: number
}

const cache = new Map<string, Candle[]>()

export async function fetchKlines(symbol: string, startMs: number, limit = 300): Promise<Candle[]> {
  const key = `${symbol}-${startMs}`
  if (cache.has(key)) return cache.get(key)!

  const from = startMs - 100 * 60 * 1000
  const url = `https://fapi.binance.com/fapi/v1/klines?symbol=${symbol}&interval=1m&startTime=${from}&limit=${limit}`
  try {
    const res = await fetch(url)
    if (!res.ok) {
      console.warn(`[binance] ${symbol} HTTP ${res.status}`)
      return []
    }
    const raw = await res.json()
    if (!Array.isArray(raw) || raw.length === 0) {
      console.warn(`[binance] ${symbol} empty response`)
      return []
    }
    const candles: Candle[] = raw.map((k: unknown[]) => ({
      time:  Math.floor(Number(k[0]) / 1000),
      open:  Number(k[1]),
      high:  Number(k[2]),
      low:   Number(k[3]),
      close: Number(k[4]),
    }))
    // solo cachea si hay datos reales
    cache.set(key, candles)
    return candles
  } catch (e) {
    console.warn(`[binance] ${symbol} error:`, e)
    return []
  }
}
