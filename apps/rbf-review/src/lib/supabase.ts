import { createClient } from '@supabase/supabase-js'

const SUPABASE_URL = 'https://ztdhvmcisjjyhbqlgkzm.supabase.co'
const SUPABASE_KEY =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9' +
  '.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp0ZGh2bWNpc2pqeWhicWxna3ptIiwicm9sZSI6' +
  'InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODk0MTc1MiwiZXhwIjoyMDk0NTE3NzUyfQ' +
  '.sqMh9Jcxrxyg-ZBYWPaNN8DB9kf-KkC7ARPLucItN1Y'

export const supabase = createClient(SUPABASE_URL, SUPABASE_KEY)

export interface AmdSignal {
  id: string
  timestamp_ms: number
  symbol: string
  direction: 'Long' | 'Short'
  session: string
  entry_price: number
  stop_price: number
  target_price: number
  rr: number | null
  result_r: number | null
  exit_reason: string | null
  closed_at_ms: number | null
  is_active: boolean
}

export interface BeSignal {
  id: string
  timestamp_ms: number
  symbol: string
  session: string
  entry_price: number
  stop_price: number
  target_price: number
  rr: number
  range_pct: number | null
  range_bars: number | null
  range_cvd: number | null
  cvd_flip_ratio: number | null
  vr_at_breakout: number | null
  result_r: number | null
  exit_reason: string | null
  closed_at: string | null
  active: boolean
}

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
  // microestructura snapshot en entrada
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
}

export interface RbfSignal {
  id: string
  timestamp_ms: number
  symbol: string
  direction: 'Short' | 'Long'
  session: string
  session_phase: string
  macro_regime: string
  entry_price: number
  stop_price: number
  target_price: number
  exit_price: number | null
  result_r: number | null
  exit_reason: string | null
  closed_at: string | null
  vr_at_breakout: number | null
  cvd_in_range: number | null
  range_pct: number | null
  range_bars: number | null
  confluence_score: number | null
  confluence_flags: string[] | null
  evidence: string[] | null
  cvd_slope_at_entry: number | null
  obi_at_entry: number | null
  dz_at_entry: number | null
  price_vs_vwap_pct: number | null
  funding_at_entry: number | null
  range_touch_count: number | null
  veto_reason: string | null
}
