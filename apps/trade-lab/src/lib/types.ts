export interface Trade {
  idx: number
  id: string
  sym: string
  dir: 'Short' | 'Long'
  session: string
  score: number | null
  entry: number
  stop: number
  target: number
  tp1?: number | null         // nivel del parcial (cercano) — liquidez
  targetName?: string | null  // qué nivel de liquidez ES el target lejano (weekly_high, etc.)
  exit: number
  resultR: number | null
  pnlUsd: number
  riskUsd: number
  stopPct: number
  equity: number
  reason: string
  tsMs: number
  ts: number
  closedAt: string | null
  regime: string
  sessionPhase: string
  evidence: string[]
  confluenceFlags: string[]
  vetoReason: string
  cvdInRange: number | null
  vr: number | null
  priceVsVwap: number | null
  funding: number | null
  cvdSlope: number | null
  obi: number | null
  dz: number | null
  rangePct: number | null
  rangeBars: number | null
  rangeTouch: number | null
  durationMin: number | null
  isOpen: boolean
  mscore?: number | null     // micro score 0-4 (OBI + VR + absorcion + DZ) al momento de entrada
  absorb?: boolean | null    // fp_absorb_buy/sell activo en la entrada
  // Live trading fields (null for paper trades)
  isLive?: boolean
  liveEntryOrderId?: string | null
  liveFillPrice?: number | null
  liveFilledQty?: number | null
  liveTpOrderId?: string | null
  liveSlOrderId?: string | null
  isPreBreakout?: boolean
  isSweepReclaim?: boolean
  htf?: unknown
  sellVol?: number | null
  buyVol?: number | null
  scoreBreakdown?: {
    sv: boolean; svVal: number; svThr: number
    bv: boolean; bvVal: number; bvThr: number
    cvd: boolean; cvdVal: number
    vr: boolean; vrVal: number; vrThr: number
  } | null
}

export const ACCOUNT = 500
