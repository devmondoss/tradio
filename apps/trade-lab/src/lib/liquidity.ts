// Loader de los paper trades de provisión de liquidez (proyecto Supabase de liquidity,
// distinto del de lib/supabase.ts). Mapea liquidity_paper_trades -> Trade[] con
// equity acumulada cronológica ($5/trade = 1% de $500), para reutilizar StatsView/TradesView.
import type { Trade } from './types'
import { ACCOUNT } from './types'

const LIQ_URL = 'https://jubpovmsfvaqfnidozfh.supabase.co'
const LIQ_KEY =
  'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9' +
  '.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imp1YnBvdm1zZnZhcWZuaWRvemZoIiwicm9sZSI6' +
  'InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MTk4OTY1NywiZXhwIjoyMDk3NTY1NjU3fQ' +
  '.X_UMT7FypCvmPX1WxmI_Sg29FVRG2IliVBOwkWMh3ZI'

// reasons del binario Rust -> claves que StatsView colorea/agrupa
const REASON_MAP: Record<string, string> = {
  target: 'TARGET',
  trail: 'TRAILING_STOP',
  stop: 'STOP',
  breakeven: 'STOP',
  timeout: 'TIME_STOP',
}

export interface LiqRow {
  id: number; symbol: string; tf: string; kind: string; side: string
  vol_regime: string; regime: string; gestion: string
  entry: number; stop: number; target: number; exit_price: number
  result_r: number; win: boolean; reason: string; system: string
  opened_at: string; closed_at: string; reconstructed?: boolean
}

export interface LiqOpts {
  symbol?: 'BTCUSDT' | 'ETHUSDT' | 'SOLUSDT'
  source?: 'all' | 'reconstructed' | 'native'
}

export async function fetchLiquidityTrades(opts: LiqOpts = {}): Promise<{ trades: Trade[]; rows: LiqRow[] }> {
  const params = new URLSearchParams({ select: '*', order: 'closed_at.asc', limit: '5000' })
  if (opts.symbol) params.set('symbol', `eq.${opts.symbol}`)
  if (opts.source === 'reconstructed') params.set('reconstructed', 'eq.true')
  if (opts.source === 'native') params.set('reconstructed', 'eq.false')

  const res = await fetch(`${LIQ_URL}/rest/v1/liquidity_paper_trades?${params.toString()}`, {
    headers: { apikey: LIQ_KEY, Authorization: `Bearer ${LIQ_KEY}` },
  })
  if (!res.ok) throw new Error(`Supabase ${res.status}: ${(await res.text()).slice(0, 160)}`)
  const rows = (await res.json()) as LiqRow[]

  const risk = ACCOUNT * 0.01 // $5/trade
  let equity = ACCOUNT
  const trades: Trade[] = rows.map((r, i) => {
    const resultR = r.result_r ?? 0
    const pnlUsd = resultR * risk
    equity += pnlUsd
    const tsMs = Date.parse(r.opened_at)
    const closeMs = Date.parse(r.closed_at)
    const stopPct = r.entry ? Math.abs(r.entry - r.stop) / r.entry * 100 : 0
    return {
      idx: i + 1,
      id: String(r.id),
      sym: r.symbol,
      dir: r.side === 'long' ? 'Long' : 'Short',
      session: '',
      score: null,
      entry: r.entry,
      stop: r.stop,
      target: r.target,
      exit: r.exit_price,
      resultR,
      pnlUsd,
      riskUsd: risk,
      stopPct,
      equity,
      reason: REASON_MAP[r.reason] ?? r.reason?.toUpperCase() ?? '',
      tsMs,
      ts: tsMs,
      closedAt: r.closed_at,
      regime: r.regime ?? '',
      sessionPhase: '',
      evidence: [],
      confluenceFlags: [],
      vetoReason: '',
      cvdInRange: null,
      vr: null,
      priceVsVwap: null,
      funding: null,
      cvdSlope: null,
      obi: null,
      dz: null,
      rangePct: null,
      rangeBars: null,
      rangeTouch: null,
      durationMin: Number.isFinite(closeMs - tsMs) ? Math.round((closeMs - tsMs) / 60000) : null,
      isOpen: false,
      // metadatos extra de liquidez en htf para inspección
      htf: { kind: r.kind, gestion: r.gestion, system: r.system, reconstructed: !!r.reconstructed, volRegime: r.vol_regime },
    } as Trade
  })
  return { trades, rows }
}
