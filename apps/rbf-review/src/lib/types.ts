export type Tab = 'live' | 'stats'

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
}

export const ACCOUNT  = 500
export const RISK_USD = ACCOUNT * 0.02   // $10 — riesgo fijo 2% por trade
