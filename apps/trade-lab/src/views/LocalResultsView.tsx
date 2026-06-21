import { useState, useEffect } from 'react'
import TradesView from './TradesView'
import StatsView from './StatsView'
import type { Trade } from '../lib/types'
import { supabase } from '../lib/supabase'

type Panel = 'trades' | 'stats' | 'diagnostics'

type StrategyMeta = {
  apiPath: string
  infoPath?: string
  label: string
  detail: string
  detail2: string
  presets: number[]
  maxDays: number | null
  defaultDays: number | null
}

const STRATEGY_META: Record<string, StrategyMeta> = {
  liquidity: {
    apiPath:    '/api/backtest/liquidity',
    infoPath:   '/api/backtest/liquidity_info',
    label:      'A · Liquidity (fader de rangos) · BTCUSDT Perp · M15 · maker · rango mín 0.5% · fee honesto',
    detail:     'QUÉ ES: estrategia A del sistema — FADER de niveles (gana en RANGOS). No predice dirección:\nPROVEE liquidez con límites maker en niveles de volumen (POC). Cuando el precio vuelve, te llenan\nbarato y rebota. Edge = mejor entrada + rebate maker.\n\nNIVELES (3 componentes, mismo principio):\n  • POC del Order Block previo (largo y corto)\n  • POC defendido ≥2 veces (soporte de volumen probado, largo)\n  • MIRROR: resistencia defendida ≥2 veces (corto) → cartera balanceada 57/43\n\nGESTIÓN BINARIA: entrada en nivel → STOP estructural ó TARGET estructural. Sin parciales.\nLa salida es tan clara como la entrada: un nivel real, nada más. Salida en M1 (honesto).',
    detail2:    'CONFIG (live-honesta):\n  Timeframe: M15 · Entrada: LÍMITE maker en el nivel · selección adversa 2 bps\n  Filtro VOLATILIDAD: solo opera con ATR > su mediana móvil(500)\n  RANGO MÍNIMO 0.5% al nivel intermedio (calidad de setup, no de salida)\n  PISO DE STOP 0.15% (elimina stops minúsculos irreales) · Timeout 24h\n  FEE HONESTO: maker 2bps/lado en entrada+target; TAKER 5.5bps/lado en stop/timeout\n  Riesgo: FIJO $5/trade (1% de $500, SIN compounding) · cap 2 trades/día por nivel\n\nDATOS: era tick VERIFICADA Bybit perp 2025-06-19 → 2026-06 (~365d). IS<2026-03 / OOS≥2026-03.\nRESULTADOS 365d A:  ~440 trades · WR ~28% · avgR +1.34 · avg_win +8.25R · wins<1R: 2 (timeouts)\nRESULTADOS 365d C:  ~506 trades · WR ~29% · avgR +1.31 · avg_win +7.59R (175 trails +2.2R cada uno)\n\n⚠️ WR bajo (28-29%) es normal en sistema binario de alta expectativa: pocas ganancias grandes.\n   Riesgo abierto: ratio de fills maker reales → validar en paper (live/paper_liquidity.py).',
    presets:    [30, 90, 180],
    maxDays:    null,
    defaultDays: null,
  },
  rbf: {
    apiPath:    '/api/backtest',
    label:      'Backtest RBF — Short post+pre · $500 capital · $10/trade',
    detail:     'VR≥3× · CVD rango < 0 · Stop = Range HIGH · Target 2R\nTrail ATR×1.2 activa 1.75R · Sin time stop · Cooldown 60 bars\nSessions: London · Overlap · NY',
    detail2:    'Pre-breakout: VR≥1.5 · OI mom ≤3 barras · Pre-CVD 5b ≤ 0\nETH: CVD≥−700 · OBI≤0.10 | BNB: cum_delta≥−500 | BTC: cum_delta≤+200',
    presets:    [1, 3, 7, 14, 30],
    maxDays:    null,
    defaultDays: null,
  },
  sweep: {
    apiPath:    '/api/backtest/sweep',
    label:      'Backtest Sweep & Reclaim Long · BTC/BNB/SOL · $500 capital · $10/trade',
    detail:     'Wick < range_low · close > range_low · bar_delta < 0 · OBI > 0\nVR≥1.5 · Riesgo wick ≤0.3% · min USD: BTC $15 · BNB $0.50 · SOL $0.08\nTarget 2R · Trail 1.90R · Sessions: London · Overlap · NY',
    detail2:    'ETH (WR=22%) y XRP (WR=25%) excluidos · Cooldown 60 bars independiente de shorts',
    presets:    [1, 3, 7, 14, 30],
    maxDays:    null,
    defaultDays: null,
  },
  combined: {
    apiPath:    '/api/backtest',
    label:      'Backtest Combinado · Short + Sweep · $500 capital · $10/trade',
    detail:     'Short: VR≥3× · CVD < 0 · Stop = Range HIGH · Trail ATR 1.75R\nSweep Long: Wick < range_low · bar_delta < 0 · OBI > 0 · Trail 1.90R\nCooldowns independientes · Filtro USD mínimo por símbolo',
    detail2:    'Ambas estrategias con sus propias reglas y cooldowns · 5 símbolos (ETH/XRP excluidos de sweep)',
    presets:    [1, 3, 7, 14, 30],
    maxDays:    null,
    defaultDays: null,
  },
  absorption: {
    apiPath:    '/api/backtest/absorption',
    label:      'Backtest Absorption Long · 5 símbolos · Compounding 2% · Todas sesiones',
    detail:     'wick_atr < 0.5 · bar_delta < 0 · OBI > 0.2 · vwap_dev < -0.3% · cvd_slope < -5\nEntrada: open barra i+1 · Stop: wick_low - 0.15×ATR · Target 2R · Trail 1.90R\nCompounding: 2% del capital actual por trade (no fijo)',
    detail2:    'Minería retrospectiva 9d · BTC-dominante · n≥60 requerido para validar edge real',
    presets:    [1, 3, 7, 14, 30],
    maxDays:    null,
    defaultDays: null,
  },
  longs: {
    apiPath:    '/api/backtest/longs',
    label:      'Backtest Longs Minería · 5 símbolos · Patrones por símbolo · Compounding 2%',
    detail:     'BTC: decline>p75 + oi_momentum=False · ETH: vwap_dev<p25 + London\nBNB: cvd>p90 + Overlap · SOL: Overlap + vr<p25 · XRP: London + wick_hi>0.5ATR\nCooldown 15 min · Stop swing_low - 0.15ATR · Trail 1.90R',
    detail2:    'Thresholds adaptativos por símbolo (percentiles reales) · Detectores calibrados individualmente\nBTC +280%E / ETH +86%E en 8d · BNB/SOL marginal · XRP marginal',
    presets:    [1, 3, 7, 14, 30],
    maxDays:    null,
    defaultDays: null,
  },
  shorts: {
    apiPath:    '/api/backtest/shorts',
    label:      'Backtest Shorts HTF+M1 · BTC/ETH/SOL/BNB · Compounding 2% · D1 bear filter',
    detail:     'D1: precio < EMA20 (bear/neutral) · H1: equal_high + oi_momentum / stacked_imb / shooting star\nM1 entrada: rejection@high · shooting star M1 · ask absorption · Stop H1_high + 0.3×ATR_H1\nTrail activa 2.0R → best − 1.5×ATR_M1 · Fee 0.07% RT (maker+taker)',
    detail2:    'BTC: eq_hi+oi / oi+obi+ses / shoot+oi · ETH: oi+obi / eq_hi+bear\nSOL: stacked+vr+ses / oi+shoot · BNB: eq_hi+obi / stacked+obi (stop>0.40%)\n$500→$773 (+54.7%) · n=23 · WR=74% · AvgR=+0.97R · MaxDD=6.4%',
    presets:    [1, 3, 7, 14, 30],
    maxDays:    null,
    defaultDays: null,
  },
  mtf_shorts: {
    apiPath:    '/api/backtest/mtf_shorts',
    label:      'Backtest MTF Shorts · BTC/ETH/SOL/BNB/XRP · D1 bear/neutral · stop H1 <0.75%',
    detail:     'D1 EMA20 filter (no bull) · H1 high + 0.3×ATR stop · Cooldown 30 bars M1\nPatrones M1 mineados: shooting star · absorption Ask · equal_high + OI\nSesiones: London · NY (BNB/XRP solo NY)',
    detail2:    'Fee 0.07% RT · Target 2.5R · CVD_EXHAUSTION exit (5 bars + OBI flip + ≥1R)\nBTC: shoot+london/ask+obi · ETH: ask+london+exp / ny+oi+eq · SOL: ny+vr4+oi/eq',
    presets:    [7, 14, 30],
    maxDays:    null,
    defaultDays: null,
  },
  mtf_longs: {
    apiPath:    '/api/backtest/mtf_longs',
    label:      'Backtest MTF Longs · ETH/SOL · H4 bull/neutral · stop H1 <0.75%',
    detail:     'H4 EMA20 filter (no bear) · H1 low - 0.3×ATR stop · Cooldown 30 bars M1\nPatrones M1 mineados: hammer · stacked_bull · equal_low + OI\nSesiones: London · NY (ETH y SOL únicamente)',
    detail2:    'Fee 0.07% RT · Target 2.5R · CVD_EXHAUSTION exit (5 bars CVD neg + OBI neg + ≥1R)\nETH: stacked_bull+london/ny · hammer+dz · SOL: hammer+london · stacked_bull+london',
    presets:    [7, 14, 30],
    maxDays:    null,
    defaultDays: null,
  },
  mtf_combined: {
    apiPath:    '/api/backtest/mtf_combined',
    label:      'Backtest MTF Combinado · Shorts (5 sym) + Longs (ETH/SOL) · Sistema completo',
    detail:     'Shorts: D1 bear/neutral filter · H1 stop · M1 patrones mineados (5 símbolos)\nLongs: H4 bull/neutral filter · H1 stop · M1 patrones mineados (ETH/SOL)\nAmbos con Target 2.5R · Fee 0.07% RT · CVD exhaustion exit',
    detail2:    'Cooldowns independientes por símbolo · Trades ordenados cronológicamente\nEdge estadístico mineado sobre 8,700+ barras M1 reales',
    presets:    [7, 14, 30],
    maxDays:    null,
    defaultDays: null,
  },
  mtf_local_btc: {
    apiPath:    '/api/backtest/mtf_local?symbol=BTCUSDT',
    infoPath:   '/api/backtest/mtf_local_info?symbol=BTCUSDT',
    label:      'Backtest MTF Spot v6 · BTCUSDT Bybit SPOT · $500 → $115K · Score v3 BOOST a',
    detail:     'Shorts: NIVEL + RECHAZO + FLUJO · LEVEL_TOL 0.70% · VAH requerido · London+Overlap+NY\nBloquea PDH+AH+VAH, PDH+VAH falso, WH @ 15h UTC y AH+VAH con OBI<-0.15',
    detail2:    'Stop H1_high + 0.40×ATR · 0.30%-0.75% · Target Chop=1.5R / Exp=3R / else=2R\nScore v3: sell_vol + buy_vol + cvd_slope>0 + vr · BOOST [0.20×,0.50×,1×,1.50×,2×]\nCVD exit: 3 barras CVD+ + OBI>0.15 + profit≥1R',
    presets:    [30, 90, 180],
    maxDays:    null,
    defaultDays: null,
  },
  mtf_spot_longs_btc: {
    apiPath:    '/api/backtest/mtf_local_longs?symbol=BTCUSDT',
    infoPath:   '/api/backtest/mtf_local_longs_info?symbol=BTCUSDT',
    label:      'Backtest MTF Spot Longs v1 · BTCUSDT Bybit SPOT · $500 · riesgo 2% compounding',
    detail:     'Longs: VAL + RECHAZO + FLUJO · LEVEL_TOL 0.70% · VAL requerido\nSesiones: 14:00-20:00 UTC · bloquea PDL+AL+VAL · una posición abierta',
    detail2:    'Stop H1_low - 0.40×ATR14 · stop 0.30%-0.75% · Target 2.0R\nCVD exit: 5 barras CVD- + OBI<-0.15 + profit≥1R · exports/mtf_btcusdt_longs_backtest.json',
    presets:    [30, 90, 180],
    maxDays:    null,
    defaultDays: null,
  },
  mtf_local_eth: {
    apiPath:    '/api/backtest/mtf_local?symbol=ETHUSDT',
    infoPath:   '/api/backtest/mtf_local_info?symbol=ETHUSDT',
    label:      'Backtest MTF Local · ETHUSDT · datos Bybit SPOT parquet · $500 · 2% riesgo',
    detail:     'Shorts: D1 EMA20 bear/neutral · H1 shoot_star / equal_high / sell_climax · M1 shoot+OBI\nLongs:  H4 EMA20 bull/neutral · H1 hammer / equal_low / buy_climax · M1 hammer+OBI\nStop H1 estructural + 0.25×ATR | Target 2.5R · Sesiones: London · Overlap · NY',
    detail2:    'Cooldown 30 barras por dirección · Forward 1200 barras (20h) · CVD exhaustion exit\nResultados guardados en exports/mtf_ethusdt_backtest.json',
    presets:    [30, 90, 180],
    maxDays:    null,
    defaultDays: null,
  },
  be: {
    apiPath:    '/api/backtest/be',
    label:      'Backtest BE — Python backend · $500 capital · $10/trade',
    detail:     'VR≥2.5× · CVD rango > 0 (compradores atrapados) · Giro CVD ≥40% · Stop = Range HIGH\nTime stop 30 bars · Cooldown 60 bars · close_location ≤0.35 · bear_body ≥0.35',
    detail2:    'Sessions: London · Overlap | bar_delta via Supabase (reciente) + API histórico',
    presets:    [14, 30, 60, 90, 180],
    maxDays:    180,
    defaultDays: 180,
  },
}

interface BtStats { n: number; wins: number; totalR: number; avgR: number; equity: number }

type DiagnosticRow = {
  label: string
  n: number
  wr: number
  avgR: number
  totalR: number
  stops: number
}

function htfNum(t: Trade, key: string): number | null {
  const htf = t.htf as Record<string, unknown> | undefined
  const raw = htf?.[key]
  return typeof raw === 'number' && Number.isFinite(raw) ? raw : null
}

function htfText(t: Trade, key: string): string {
  const htf = t.htf as Record<string, unknown> | undefined
  const raw = htf?.[key]
  return typeof raw === 'string' ? raw : ''
}

function tradeHour(t: Trade): number {
  return new Date(t.tsMs).getUTCHours()
}

function diagMetrics(label: string, rows: Trade[]): DiagnosticRow {
  const closed = rows.filter(t => !t.isOpen && t.resultR != null)
  const wins = closed.filter(t => (t.resultR ?? 0) > 0).length
  const totalR = closed.reduce((s, t) => s + (t.resultR ?? 0), 0)
  return {
    label,
    n: closed.length,
    wr: closed.length ? wins / closed.length * 100 : 0,
    avgR: closed.length ? totalR / closed.length : 0,
    totalR,
    stops: closed.filter(t => String(t.reason).toLowerCase() === 'stop').length,
  }
}

function DiagTable({ title, rows }: { title: string; rows: DiagnosticRow[] }) {
  const usable = rows.filter(r => r.n > 0)
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, border: '1px solid var(--border)', background: 'var(--bg)', overflow: 'hidden' }}>
      <div style={{ padding: '6px 10px', fontSize: 8, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 1.2, color: 'var(--text3)', borderBottom: '1px solid var(--border)', background: 'var(--bg2)' }}>
        {title}
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', tableLayout: 'fixed' }}>
        <colgroup>
          <col style={{ width: '36%' }} />
          <col style={{ width: '12%' }} />
          <col style={{ width: '16%' }} />
          <col style={{ width: '18%' }} />
          <col style={{ width: '18%' }} />
        </colgroup>
        <thead>
          <tr>
            {['Corte', 'N', 'WR', 'AvgR', 'Stops'].map((h, i) => (
              <th key={h} style={{ padding: '5px 8px', fontSize: 8, color: 'var(--text3)', textAlign: i === 0 ? 'left' : 'right', borderBottom: '1px solid var(--border)' }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {usable.map(r => {
            const wrCol = r.wr >= 52 ? 'var(--green)' : r.wr >= 45 ? 'var(--yellow)' : 'var(--red)'
            const avgCol = r.avgR > 0.3 ? 'var(--green)' : r.avgR > 0 ? 'var(--yellow)' : 'var(--red)'
            return (
              <tr key={r.label} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '5px 8px', fontSize: 10, color: 'var(--text2)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.label}</td>
                <td style={{ padding: '5px 8px', fontSize: 10, color: 'var(--text3)', textAlign: 'right', fontFamily: 'var(--mono)' }}>{r.n}</td>
                <td style={{ padding: '5px 8px', fontSize: 10, color: wrCol, textAlign: 'right', fontFamily: 'var(--mono)', fontWeight: 700 }}>{r.wr.toFixed(1)}%</td>
                <td style={{ padding: '5px 8px', fontSize: 10, color: avgCol, textAlign: 'right', fontFamily: 'var(--mono)', fontWeight: 700 }}>{r.avgR > 0 ? '+' : ''}{r.avgR.toFixed(3)}</td>
                <td style={{ padding: '5px 8px', fontSize: 10, color: 'var(--text3)', textAlign: 'right', fontFamily: 'var(--mono)' }}>{r.stops}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function LossDiagnostics({ trades }: { trades: Trade[] }) {
  const closed = trades.filter(t => !t.isOpen && t.resultR != null)
  const losses = closed.filter(t => (t.resultR ?? 0) < 0)
  const oosStart = Date.UTC(2026, 2, 1)
  const metric = (label: string, pred: (t: Trade) => boolean) => diagMetrics(label, closed.filter(pred))
  const oosMetric = (label: string, pred: (t: Trade) => boolean) => diagMetrics(label, closed.filter(t => t.tsMs >= oosStart && pred(t)))
  const base = diagMetrics('Base', closed)
  const oosBase = diagMetrics('Base OOS', closed.filter(t => t.tsMs >= oosStart))
  const lossShare = (pred: (t: Trade) => boolean) => losses.length ? losses.filter(pred).length / losses.length * 100 : 0

  const flowRows = [
    metric('OBI <= 0.10', t => (t.obi ?? 0) <= 0.10),
    metric('OBI > 0.10', t => (t.obi ?? 0) > 0.10),
    metric('CVD -10 a -3', t => (t.cvdSlope ?? 0) >= -10 && (t.cvdSlope ?? 0) < -3),
    metric('CVD fuera zona mala', t => !((t.cvdSlope ?? 0) >= -10 && (t.cvdSlope ?? 0) < -3)),
    metric('OBI bearish real', t => (t.obi ?? 0) < -0.05),
  ]
  const contextRows = [
    metric('VAH solo', t => htfText(t, 'level') === 'VAH'),
    metric('AH+VAH', t => htfText(t, 'level') === 'AH+VAH'),
    metric('PDH+VAH bloqueado', t => htfText(t, 'level') === 'PDH+VAH'),
    metric('Stop <= 0.70%', t => t.stopPct <= 0.70),
    metric('Wick <.35 o >=.55', t => {
      const w = htfNum(t, 'wickPct') ?? 0
      return w < 0.35 || w >= 0.55
    }),
  ]
  const timeRows = [12, 13, 14, 15, 16, 17, 18, 19].map(h => metric(`${String(h).padStart(2, '0')}:00 UTC`, t => tradeHour(t) === h))
  const filterRows = [
    metric('OBI<=.10 + no CVD mala', t => (t.obi ?? 0) <= 0.10 && !((t.cvdSlope ?? 0) >= -10 && (t.cvdSlope ?? 0) < -3)),
    oosMetric('OOS: mismo filtro', t => (t.obi ?? 0) <= 0.10 && !((t.cvdSlope ?? 0) >= -10 && (t.cvdSlope ?? 0) < -3)),
    metric('OBI<=.05 + no CVD mala', t => (t.obi ?? 0) <= 0.05 && !((t.cvdSlope ?? 0) >= -10 && (t.cvdSlope ?? 0) < -3)),
    oosMetric('OOS: filtro estricto', t => (t.obi ?? 0) <= 0.05 && !((t.cvdSlope ?? 0) >= -10 && (t.cvdSlope ?? 0) < -3)),
  ]

  return (
    <div style={{ flex: 1, overflow: 'auto', background: 'var(--bg)', padding: 12 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 8, marginBottom: 10 }}>
        {[
          { k: 'Base', v: `${base.n} trades`, s: `${base.wr.toFixed(1)}% WR · ${base.avgR.toFixed(3)}R`, c: 'var(--text)' },
          { k: 'OOS', v: `${oosBase.n} trades`, s: `${oosBase.wr.toFixed(1)}% WR · ${oosBase.avgR.toFixed(3)}R`, c: 'var(--green)' },
          { k: 'Losses', v: `${losses.length}`, s: `${lossShare(t => (t.obi ?? 0) > 0.10).toFixed(1)}% con OBI>0.10`, c: 'var(--red)' },
          { k: 'Hipotesis', v: 'Book + nivel', s: 'evitar OBI alto y CVD zona mala; PDH+VAH ya bloqueado', c: 'var(--blue)' },
        ].map(x => (
          <div key={x.k} style={{ border: '1px solid var(--border)', background: 'var(--bg2)', padding: '9px 11px', display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: 8, color: 'var(--text3)', textTransform: 'uppercase', letterSpacing: 1.2, fontWeight: 700 }}>{x.k}</span>
            <span style={{ fontSize: 18, color: x.c, fontFamily: 'var(--mono)', fontWeight: 800, lineHeight: 1 }}>{x.v}</span>
            <span style={{ fontSize: 10, color: 'var(--text3)' }}>{x.s}</span>
          </div>
        ))}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 10 }}>
        <DiagTable title="Flujo en entrada" rows={flowRows} />
        <DiagTable title="Nivel / stop / vela" rows={contextRows} />
        <DiagTable title="Hora UTC" rows={timeRows} />
        <DiagTable title="Filtros candidatos" rows={filterRows} />
      </div>
      <div style={{ marginTop: 10, padding: '8px 10px', border: '1px solid var(--border)', background: 'var(--bg2)', color: 'var(--text3)', fontSize: 10, lineHeight: 1.55 }}>
        Lectura: los perdedores no se explican por sesion; se concentran mas en entradas donde el book ya no esta claramente vendedor.
        Los cortes mas utiles son OBI alto y CVD slope en zona intermedia negativa. PDH+VAH queda bloqueado por la spec v2; AH+VAH se mantiene como nivel debil, no como veto base.
      </div>
    </div>
  )
}

export default function LocalResultsView({
  strategy = 'rbf',
  onStats,
  onTrades,
}: {
  strategy?: 'liquidity' | 'rbf' | 'sweep' | 'be' | 'combined' | 'absorption' | 'longs' | 'shorts' | 'mtf_shorts' | 'mtf_longs' | 'mtf_combined' | 'mtf_local_btc' | 'mtf_spot_longs_btc' | 'mtf_local_eth'
  onStats?: (s: BtStats | null) => void
  onTrades?: (trades: Trade[]) => void
}) {
  const meta_cfg = STRATEGY_META[strategy]
  const [panel,    setPanel]   = useState<Panel>('trades')
  const [trades,   setTrades]  = useState<Trade[]>([])
  const [loading,  setLoading] = useState(false)
  const [error,    setError]   = useState<string | null>(null)
  const [days,     setDays]    = useState(meta_cfg.defaultDays ?? 14)
  const [ran,      setRan]     = useState(false)
  const [meta,     setMeta]    = useState<{ n: number; wins: number; equity: number; actualDays: number; microStart: string | null; longsEnabled?: boolean; nShorts?: number; nLongs?: number } | null>(null)
  const [dataFrom,      setDataFrom]      = useState<string | null>(null)
  const [availableDays, setAvailableDays] = useState<number | null>(meta_cfg.maxDays)
  const [system,        setSystem]        = useState<'A' | 'C'>('A')   // liquidity: A solo vs C (sistema A+B enrutado)

  useEffect(() => {
    if (meta_cfg.maxDays !== null) {
      const d = meta_cfg.defaultDays ?? meta_cfg.maxDays
      setDays(d)
      triggerRun(d)
      return
    }

    if (meta_cfg.infoPath) {
      fetch(meta_cfg.infoPath)
        .then(r => r.json())
        .then((info: any) => {
          if (info.error) return
          const nDays = info.available_days as number
          const label = info.start_label as string
          setDataFrom(`${label} (~${nDays}d)`)
          setAvailableDays(nDays)
          setDays(nDays)
          triggerRun(nDays)   // auto-corre el RANGO COMPLETO disponible (sin race del default 14d)
        })
        .catch(() => { /* parquet no disponible */ })
      return
    }

    supabase
      .from('btc_bars')
      .select('ts_ms')
      .not('cvd_slope', 'is', null)
      .order('ts_ms', { ascending: true })
      .limit(1)
      .then(({ data }) => {
        if (!data?.length) return
        const ms    = data[0].ts_ms as number
        const nDays = Math.round((Date.now() - ms) / 86_400_000)
        const label = new Date(ms).toLocaleDateString('es', { day: 'numeric', month: 'short' })
        setDataFrom(`${label} (~${nDays}d)`)
        setAvailableDays(nDays)
        setDays(nDays)
        triggerRun(nDays)
      })
  }, [])

  async function triggerRun(d: number, sys: 'A' | 'C' = system) {
    setLoading(true)
    setError(null)
    setTrades([])
    setMeta(null)
    try {
      const sep  = meta_cfg.apiPath.includes('?') ? '&' : '?'
      const sysQ = strategy === 'liquidity' ? `&system=${sys}` : ''
      const res  = await fetch(`${meta_cfg.apiPath}${sep}days=${d}${sysQ}`)
      const text = await res.text()
      let data: any
      try { data = JSON.parse(text) } catch {
        throw new Error(text.slice(0, 200))
      }
      if (!res.ok || data.error) throw new Error(data.error ?? `HTTP ${res.status}`)
      const ts = data.trades as Trade[]
      const totalR = ts.reduce((s: number, t: Trade) => s + (t.resultR ?? 0), 0)
      const avgR   = data.n > 0 ? totalR / data.n : 0
      setTrades(ts)
      setMeta({ n: data.n, wins: data.wins, equity: data.equity, actualDays: data.actual_days ?? d, microStart: data.micro_start ?? null, longsEnabled: data.longs_enabled ?? true, nShorts: data.n_shorts, nLongs: data.n_longs })
      onStats?.({ n: data.n, wins: data.wins, totalR, avgR, equity: data.equity })
      onTrades?.(ts)
      setRan(true)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  // "Ejecutar Backtest" corre el rango COMPLETO disponible (evita la race del default 14d)
  function run() { const d = availableDays ?? days; setDays(d); triggerRun(d) }

  if (!ran && !loading) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 16 }}>
        <div style={{ color: 'var(--text2)', fontSize: 11, textAlign: 'center', lineHeight: 1.8 }}>
          {meta_cfg.label}<br />
          <span style={{ color: 'var(--text3)', fontSize: 10, whiteSpace: 'pre-line' }}>{meta_cfg.detail}</span>
          <span style={{ display: 'block', color: 'var(--text3)', fontSize: 9, marginTop: 3, whiteSpace: 'pre-line' }}>{meta_cfg.detail2}</span>
          {dataFrom && <div style={{ marginTop: 6, color: 'var(--yellow)', fontSize: 10 }}>Datos desde: {dataFrom}</div>}
        </div>

        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ color: 'var(--text3)', fontSize: 10 }}>Ultimos</span>
          {meta_cfg.maxDays === null && availableDays != null && (
            <button onClick={() => { setDays(availableDays); triggerRun(availableDays) }} style={{
              padding: '3px 10px', borderRadius: 4, fontSize: 10, cursor: 'pointer', fontFamily: 'inherit',
              background: days === availableDays ? 'var(--blue)' : 'var(--bg3)',
              border: `1px solid ${days === availableDays ? 'var(--blue)' : 'var(--border2)'}`,
              color: days === availableDays ? '#fff' : 'var(--text2)', fontWeight: 700,
            }}>Todo ({availableDays}d)</button>
          )}
          {meta_cfg.presets
            .filter(d => availableDays == null || d <= availableDays)
            .map(d => (
              <button key={d} onClick={() => { setDays(d); triggerRun(d) }} style={{
                padding: '3px 10px', borderRadius: 4, fontSize: 10, cursor: 'pointer', fontFamily: 'inherit',
                background: days === d ? 'var(--blue)' : 'var(--bg3)',
                border: `1px solid ${days === d ? 'var(--blue)' : 'var(--border2)'}`,
                color: days === d ? '#fff' : 'var(--text2)',
                fontWeight: days === d ? 700 : 400,
              }}>{d}d</button>
            ))
          }
        </div>

        <button onClick={run} style={{
          padding: '6px 20px', borderRadius: 5, fontSize: 11, fontWeight: 700,
          cursor: 'pointer', fontFamily: 'inherit',
          background: 'var(--blue)', border: 'none', color: '#fff',
        }}>Ejecutar Backtest</button>

        <div style={{ color: 'var(--text3)', fontSize: 9 }}>
          {meta_cfg.infoPath
            ? 'Datos locales parquet · resultados en exports/'
            : <>Python corre como subprocess dentro de Vite — solo necesitas <code>npm run dev</code></>}
        </div>

        {error && (
          <div style={{ color: 'var(--red)', fontSize: 10, maxWidth: 360, textAlign: 'center' }}>
            {error.includes('Failed to fetch')
              ? 'No se puede conectar al servidor Python. ¿Está corriendo uvicorn en :8787?'
              : error}
          </div>
        )}
      </div>
    )
  }

  if (loading) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 10 }}>
        <div style={{ color: 'var(--text2)', fontSize: 11 }}>Corriendo backtest Python ({days}d)…</div>
        <div style={{ width: 200, height: 3, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
          <div style={{ height: '100%', background: 'var(--blue)', borderRadius: 2, width: '60%', animation: 'pulse 1s ease-in-out infinite alternate' }} />
        </div>
        <div style={{ color: 'var(--text3)', fontSize: 9 }}>
          {meta_cfg.infoPath
            ? `Leyendo parquet local · ${days}d de datos…`
            : `Descargando ~${(days * 7.2).toFixed(0)}k bars × 5 símbolos…`}
        </div>
      </div>
    )
  }

  if (error && !ran) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 12 }}>
        <div style={{ color: 'var(--red)', fontSize: 11 }}>
          {error.includes('Failed to fetch') ? 'Servidor Python no disponible en :8787' : `Error: ${error}`}
        </div>
        <button onClick={() => { setError(null); setRan(false) }} style={{
          padding: '4px 14px', borderRadius: 4, fontSize: 10, cursor: 'pointer',
          fontFamily: 'inherit', background: 'var(--bg3)', border: '1px solid var(--border2)', color: 'var(--text2)',
        }}>Volver</button>
      </div>
    )
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '0 8px', height: 30, flexShrink: 0,
        borderBottom: '1px solid var(--border)', background: 'var(--bg2)',
        fontSize: 10, color: 'var(--text3)',
      }}>
        {((strategy === 'liquidity' ? ['trades', 'stats'] : ['trades', 'stats', 'diagnostics']) as Panel[]).map(p => (
          <button key={p} onClick={() => setPanel(p)} style={{
            padding: '2px 10px', borderRadius: 3, fontSize: 10, cursor: 'pointer', fontFamily: 'inherit',
            background: panel === p ? 'var(--bg3)' : 'none',
            border: `1px solid ${panel === p ? 'var(--border2)' : 'transparent'}`,
            color: panel === p ? 'var(--text)' : 'var(--text3)',
          }}>{p.charAt(0).toUpperCase() + p.slice(1)}</button>
        ))}

        {strategy === 'liquidity' && (
          <span style={{ display: 'flex', gap: 3, marginLeft: 6, borderLeft: '1px solid var(--border)', paddingLeft: 8 }}>
            {([['A', 'A'], ['C', 'C']] as const).map(([s, lbl]) => (
              <button key={s} title={s === 'A' ? 'A · Fader (fade en rangos, parcial+BE)' : 'C · Sistema completo: fade en rango, trailing en tendencia (A+B enrutado)'}
                onClick={() => { setSystem(s); triggerRun(days, s) }} style={{
                  padding: '2px 10px', borderRadius: 3, fontSize: 10, cursor: 'pointer', fontFamily: 'inherit', fontWeight: 700,
                  background: system === s ? 'var(--blue)' : 'var(--bg3)',
                  border: `1px solid ${system === s ? 'var(--blue)' : 'var(--border2)'}`,
                  color: system === s ? '#fff' : 'var(--text3)',
                }}>{lbl}</button>
            ))}
          </span>
        )}

        {meta && (
          <span style={{ color: 'var(--text2)' }}>
            {meta.longsEnabled === false && <span style={{ color: 'var(--yellow)', fontSize: 9, marginRight: 6 }}>sin longs · </span>}
            {meta.nShorts != null ? `S:${meta.nShorts} L:${meta.nLongs ?? 0}` : meta.n} trades ·{' '}
            <span title={`Pedido: ${days}d · Datos reales: ${meta.actualDays}d`}>
              {meta.actualDays}d datos
              {meta.actualDays < days * 0.9 && <span style={{ color: 'var(--yellow)', marginLeft: 3 }}>(pedido {days}d)</span>}
            </span>
            {' · '}
            <span style={{ color: meta.n > 0 && meta.wins / meta.n >= 0.4 ? 'var(--green)' : 'var(--red)' }}>
              WR {meta.n > 0 ? (meta.wins / meta.n * 100).toFixed(0) + '%' : '—'}
            </span>
            {' · '}
            <span style={{ color: meta.equity >= 500 ? 'var(--green)' : 'var(--red)' }}>
              ${meta.equity.toFixed(2)}
            </span>
          </span>
        )}

        <span style={{ marginLeft: 'auto', display: 'flex', gap: 4, alignItems: 'center' }}>
          {meta_cfg.maxDays === null && availableDays != null && (
            <button onClick={() => { setDays(availableDays); triggerRun(availableDays) }} style={{
              padding: '2px 8px', borderRadius: 3, fontSize: 9, cursor: 'pointer', fontFamily: 'inherit',
              background: days === availableDays ? 'var(--blue)' : 'var(--bg3)',
              border: `1px solid ${days === availableDays ? 'var(--blue)' : 'var(--border2)'}`,
              color: days === availableDays ? '#fff' : 'var(--text3)', fontWeight: 700,
            }}>Todo ({availableDays}d)</button>
          )}
          {meta_cfg.presets
            .filter(d => availableDays == null || d <= availableDays)
            .map(d => (
              <button key={d} onClick={() => { setDays(d); triggerRun(d) }} style={{
                padding: '2px 8px', borderRadius: 3, fontSize: 9, cursor: 'pointer', fontFamily: 'inherit',
                background: days === d ? 'var(--blue)' : 'var(--bg3)',
                border: `1px solid ${days === d ? 'var(--blue)' : 'var(--border2)'}`,
                color: days === d ? '#fff' : 'var(--text3)',
                fontWeight: days === d ? 700 : 400,
              }}>{d}d</button>
            ))
          }
          <button onClick={() => triggerRun(days)} style={{
            padding: '2px 8px', borderRadius: 3, fontSize: 9, cursor: 'pointer', fontFamily: 'inherit',
            background: 'var(--bg3)', border: '1px solid var(--border2)', color: 'var(--text3)',
          }}>↺</button>
        </span>
      </div>

      <div style={{ flex: 1, display: 'flex', overflow: 'hidden', minHeight: 0 }}>
        {panel === 'trades'      && <TradesView trades={trades} />}
        {panel === 'stats'       && <StatsView trades={trades} />}
        {panel === 'diagnostics' && <LossDiagnostics trades={trades} />}
      </div>
    </div>
  )
}
