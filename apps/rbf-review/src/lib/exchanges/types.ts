export interface Candle {
  time: number
  open: number
  high: number
  low: number
  close: number
}

export const TF_SECONDS: Record<string, number> = {
  '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400,
}

export type FetchKlines = (
  symbol: string,
  startMs: number,
  limit?: number,
  extraBackBars?: number,
  endMs?: number,
  interval?: string,
) => Promise<Candle[]>
