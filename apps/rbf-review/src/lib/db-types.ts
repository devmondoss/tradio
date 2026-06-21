// Supabase table row types for MTF Spot trading system.

export interface MtfTrade {
  id: string
  symbol: string
  sig: string
  session: string
  direction: 'Short' | 'Long'
  d1_trend: string
  entry: number
  stop: number
  target: number
  stop_pct: number
  is_open: boolean
  result_r: number | null
  gross_r: number | null
  fee_r: number | null
  reason: string | null
  exit_price: number | null
  duration_bars: number | null
  entry_at: string
  closed_at: string | null
  obi_entry: number | null
  cvd_slope_entry: number | null
  dz_score: number | null
  stacked_imb: string | null
  equal_low: boolean | null
}

export interface MtfSpotTrade {
  id: string
  symbol: string
  venue: string
  market_type: 'spot'
  strategy: 'mtf_spot_shorts_v4' | 'mtf_spot_longs_v1' | string
  sig: string
  session: string | null
  direction: 'Short' | 'Long'
  level: string | null
  entry: number
  stop: number
  target: number
  stop_pct: number
  is_open: boolean
  result_r: number | null
  gross_r: number | null
  fee_r: number | null
  reason: string | null
  exit_price: number | null
  duration_bars: number | null
  mfe_r: number | null
  mae_r: number | null
  entry_at: string
  closed_at: string | null
  wick_pct: number | null
  obi_entry: number | null
  delta_entry: number | null
  cvd_slope_entry: number | null
  is_live: boolean | null
  live_entry_order_id: string | null
  live_fill_price: number | null
  live_filled_qty: number | null
  live_tp_order_id: string | null
  live_sl_order_id: string | null
}
