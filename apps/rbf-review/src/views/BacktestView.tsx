import { useState, useEffect } from 'react'
import LiveView from './LiveView'
import StatsView from './StatsView'
import type { Trade } from '../lib/types'
import { supabase } from '../lib/supabase'

type Panel = 'trades' | 'stats'

const STRATEGY_META = {
  rbf: {
    apiPath:    '/api/backtest',
    label:      'Backtest RBF — Short post+pre · $500 capital · $10/trade',
    detail:     'VR≥3× · CVD rango < 0 · Stop = Range HIGH · Target 2R\nTrail ATR×1.2 activa 1.75R · Sin time stop · Cooldown 60 bars\nSessions: London · Overlap · NY',
    detail2:    'Pre-breakout: VR≥1.5 · OI mom ≤3 barras · Pre-CVD 5b ≤ 0\nETH: CVD≥−700 · OBI≤0.10 | BNB: cum_delta≥−500 | BTC: cum_delta≤+200',
    presets:    [1, 3, 7, 14, 30] as number[],
    maxDays:    null as number | null,   // limitado por Supabase (necesita cvd_slope/vwap)
    defaultDays: null as number | null,  // null = usar todos los disponibles en Supabase
  },
  sweep: {
    apiPath:    '/api/backtest/sweep',
    label:      'Backtest Sweep & Reclaim Long · BTC/BNB/SOL · $500 capital · $10/trade',
    detail:     'Wick < range_low · close > range_low · bar_delta < 0 · OBI > 0\nVR≥1.5 · Riesgo wick ≤0.3% · min USD: BTC $15 · BNB $0.50 · SOL $0.08\nTarget 2R · Trail 1.90R · Sessions: London · Overlap · NY',
    detail2:    'ETH (WR=22%) y XRP (WR=25%) excluidos · Cooldown 60 bars independiente de shorts',
    presets:    [1, 3, 7, 14, 30] as number[],
    maxDays:    null as number | null,
    defaultDays: null as number | null,
  },
  combined: {
    apiPath:    '/api/backtest',
    label:      'Backtest Combinado · Short + Sweep · $500 capital · $10/trade',
    detail:     'Short: VR≥3× · CVD < 0 · Stop = Range HIGH · Trail ATR 1.75R\nSweep Long: Wick < range_low · bar_delta < 0 · OBI > 0 · Trail 1.90R\nCooldowns independientes · Filtro USD mínimo por símbolo',
    detail2:    'Ambas estrategias con sus propias reglas y cooldowns · 5 símbolos (ETH/XRP excluidos de sweep)',
    presets:    [1, 3, 7, 14, 30] as number[],
    maxDays:    null as number | null,
    defaultDays: null as number | null,
  },
  absorption: {
    apiPath:    '/api/backtest/absorption',
    label:      'Backtest Absorption Long · 5 símbolos · Compounding 2% · Todas sesiones',
    detail:     'wick_atr < 0.5 · bar_delta < 0 · OBI > 0.2 · vwap_dev < -0.3% · cvd_slope < -5\nEntrada: open barra i+1 · Stop: wick_low - 0.15×ATR · Target 2R · Trail 1.90R\nCompounding: 2% del capital actual por trade (no fijo)',
    detail2:    'Minería retrospectiva 9d · BTC-dominante · n≥60 requerido para validar edge real',
    presets:    [1, 3, 7, 14, 30] as number[],
    maxDays:    null as number | null,
    defaultDays: null as number | null,
  },
  longs: {
    apiPath:    '/api/backtest/longs',
    label:      'Backtest Longs Minería · 5 símbolos · Patrones por símbolo · Compounding 2%',
    detail:     'BTC: decline>p75 + oi_momentum=False · ETH: vwap_dev<p25 + London\nBNB: cvd>p90 + Overlap · SOL: Overlap + vr<p25 · XRP: London + wick_hi>0.5ATR\nCooldown 15 min · Stop swing_low - 0.15ATR · Trail 1.90R',
    detail2:    'Thresholds adaptativos por símbolo (percentiles reales) · Detectores calibrados individualmente\nBTC +280%E / ETH +86%E en 8d · BNB/SOL marginal · XRP marginal',
    presets:    [1, 3, 7, 14, 30] as number[],
    maxDays:    null as number | null,
    defaultDays: null as number | null,
  },
  shorts: {
    apiPath:    '/api/backtest/shorts',
    label:      'Backtest Shorts HTF+M1 · BTC/ETH/SOL/BNB · Compounding 2% · D1 bear filter',
    detail:     'D1: precio < EMA20 (bear/neutral) · H1: equal_high + oi_momentum / stacked_imb / shooting star\nM1 entrada: rejection@high · shooting star M1 · ask absorption · Stop H1_high + 0.3×ATR_H1\nTrail activa 2.0R → best − 1.5×ATR_M1 · Fee 0.07% RT (maker+taker)',
    detail2:    'BTC: eq_hi+oi / oi+obi+ses / shoot+oi · ETH: oi+obi / eq_hi+bear\nSOL: stacked+vr+ses / oi+shoot · BNB: eq_hi+obi / stacked+obi (stop>0.40%)\n$500→$773 (+54.7%) · n=23 · WR=74% · AvgR=+0.97R · MaxDD=6.4%',
    presets:    [1, 3, 7, 14, 30] as number[],
    maxDays:    null as number | null,
    defaultDays: null as number | null,
  },
  mtf_shorts: {
    apiPath:    '/api/backtest/mtf_shorts',
    label:      'Backtest MTF Shorts · BTC/ETH/SOL/BNB/XRP · D1 bear/neutral · stop H1 <0.75%',
    detail:     'D1 EMA20 filter (no bull) · H1 high + 0.3×ATR stop · Cooldown 30 bars M1\nPatrones M1 mineados: shooting star · absorption Ask · equal_high + OI\nSesiones: London · NY (BNB/XRP solo NY)',
    detail2:    'Fee 0.07% RT · Target 2.5R · CVD_EXHAUSTION exit (5 bars + OBI flip + ≥1R)\nBTC: shoot+london/ask+obi · ETH: ask+london+exp / ny+oi+eq · SOL: ny+vr4+oi/eq',
    presets:    [7, 14, 30] as number[],
    maxDays:    null as number | null,
    defaultDays: null as number | null,
  },
  mtf_longs: {
    apiPath:    '/api/backtest/mtf_longs',
    label:      'Backtest MTF Longs · ETH/SOL · H4 bull/neutral · stop H1 <0.75%',
    detail:     'H4 EMA20 filter (no bear) · H1 low - 0.3×ATR stop · Cooldown 30 bars M1\nPatrones M1 mineados: hammer · stacked_bull · equal_low + OI\nSesiones: London · NY (ETH y SOL únicamente)',
    detail2:    'Fee 0.07% RT · Target 2.5R · CVD_EXHAUSTION exit (5 bars CVD neg + OBI neg + ≥1R)\nETH: stacked_bull+london/ny · hammer+dz · SOL: hammer+london · stacked_bull+london',
    presets:    [7, 14, 30] as number[],
    maxDays:    null as number | null,
    defaultDays: null as number | null,
  },
  mtf_combined: {
    apiPath:    '/api/backtest/mtf_combined',
    label:      'Backtest MTF Combinado · Shorts (5 sym) + Longs (ETH/SOL) · Sistema completo',
    detail:     'Shorts: D1 bear/neutral filter · H1 stop · M1 patrones mineados (5 símbolos)\nLongs: H4 bull/neutral filter · H1 stop · M1 patrones mineados (ETH/SOL)\nAmbos con Target 2.5R · Fee 0.07% RT · CVD exhaustion exit',
    detail2:    'Cooldowns independientes por símbolo · Trades ordenados cronológicamente\nEdge estadístico mineado sobre 8,700+ barras M1 reales',
    presets:    [7, 14, 30] as number[],
    maxDays:    null as number | null,
    defaultDays: null as number | null,
  },
  be: {
    apiPath:    '/api/backtest/be',
    label:      'Backtest BE — Python backend · $500 capital · $10/trade',
    detail:     'VR≥2.5× · CVD rango > 0 (compradores atrapados) · Giro CVD ≥40% · Stop = Range HIGH\nTime stop 30 bars · Cooldown 60 bars · close_location ≤0.35 · bear_body ≥0.35',
    detail2:    'Sessions: London · Overlap | bar_delta via Supabase (reciente) + Binance API (histórico)',
    presets:    [14, 30, 60, 90, 180] as number[],
    maxDays:    180 as number | null,    // Binance cubre hasta ~2 años; limitamos a 180d razonable
    defaultDays: 180 as number | null,  // auto-run con 180d
  },
}

interface BtStats { n: number; wins: number; totalR: number; avgR: number; equity: number }

export default function BacktestView({
  strategy = 'rbf',
  onStats,
  onTrades,
}: {
  strategy?: 'rbf' | 'sweep' | 'be' | 'combined' | 'absorption' | 'longs' | 'shorts' | 'mtf_shorts' | 'mtf_longs' | 'mtf_combined'
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
  const [meta,     setMeta]    = useState<{ n: number; wins: number; equity: number; actualDays: number; microStart: string | null } | null>(null)
  const [dataFrom,     setDataFrom]     = useState<string | null>(null)
  const [availableDays, setAvailableDays] = useState<number | null>(meta_cfg.maxDays)

  useEffect(() => {
    if (meta_cfg.maxDays !== null) {
      // BE y otras estrategias con fuente histórica (Binance): no necesitamos
      // consultar Supabase para saber cuántos días hay disponibles.
      const d = meta_cfg.defaultDays ?? meta_cfg.maxDays
      setDays(d)
      triggerRun(d)
      return
    }
    // RBF: limitado por datos de microestructura en Supabase (cvd_slope/vwap)
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
        // Auto-run con todos los datos disponibles — crece 1 día automáticamente cada día
        triggerRun(nDays)
      })
  }, [])

  async function triggerRun(d: number) {
    setLoading(true)
    setError(null)
    setTrades([])
    setMeta(null)
    try {
      const res  = await fetch(`${meta_cfg.apiPath}?days=${d}`)
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
      setMeta({ n: data.n, wins: data.wins, equity: data.equity, actualDays: data.actual_days ?? d, microStart: data.micro_start ?? null })
      onStats?.({ n: data.n, wins: data.wins, totalR, avgR, equity: data.equity })
      onTrades?.(ts)
      setRan(true)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  function run() { triggerRun(days) }

  if (!ran && !loading) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 16 }}>
        <div style={{ color: 'var(--text2)', fontSize: 11, textAlign: 'center', lineHeight: 1.8 }}>
          {meta_cfg.label}<br />
          <span style={{ color: 'var(--text3)', fontSize: 10, whiteSpace: 'pre-line' }}>
            {meta_cfg.detail}
          </span>
          <span style={{ display: 'block', color: 'var(--text3)', fontSize: 9, marginTop: 3, whiteSpace: 'pre-line' }}>
            {meta_cfg.detail2}
          </span>
          {dataFrom && (
            <div style={{ marginTop: 6, color: 'var(--yellow)', fontSize: 10 }}>
              Datos en BD desde: {dataFrom}
            </div>
          )}
        </div>

        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ color: 'var(--text3)', fontSize: 10 }}>Ultimos</span>
          {/* Botón "Todo" para RBF (limitado por Supabase) */}
          {meta_cfg.maxDays === null && availableDays != null && (
            <button onClick={() => { setDays(availableDays); triggerRun(availableDays) }} style={{
              padding: '3px 10px', borderRadius: 4, fontSize: 10, cursor: 'pointer', fontFamily: 'inherit',
              background: days === availableDays ? 'var(--blue)' : 'var(--bg3)',
              border: `1px solid ${days === availableDays ? 'var(--blue)' : 'var(--border2)'}`,
              color: days === availableDays ? '#fff' : 'var(--text2)',
              fontWeight: 700,
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
        }}>
          Ejecutar Backtest
        </button>

        <div style={{ color: 'var(--text3)', fontSize: 9 }}>
          Python corre como subprocess dentro de Vite — solo necesitas <code>npm run dev</code>
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
        <div style={{ color: 'var(--text2)', fontSize: 11 }}>
          Corriendo backtest Python ({days}d)…
        </div>
        <div style={{ width: 200, height: 3, background: 'var(--border)', borderRadius: 2, overflow: 'hidden' }}>
          <div style={{ height: '100%', background: 'var(--blue)', borderRadius: 2, width: '60%', animation: 'pulse 1s ease-in-out infinite alternate' }} />
        </div>
        <div style={{ color: 'var(--text3)', fontSize: 9 }}>Descargando ~{days * 7.2}k bars × 5 símbolos…</div>
      </div>
    )
  }

  if (error && !ran) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 12 }}>
        <div style={{ color: 'var(--red)', fontSize: 11 }}>
          {error.includes('Failed to fetch')
            ? 'Servidor Python no disponible en :8787'
            : `Error: ${error}`}
        </div>
        <code style={{ color: 'var(--text3)', fontSize: 9 }}>
          Asegúrate de tener Python en el PATH con las librerías urllib/json (stdlib)
        </code>
        <button onClick={() => { setError(null); setRan(false) }} style={{
          padding: '4px 14px', borderRadius: 4, fontSize: 10, cursor: 'pointer',
          fontFamily: 'inherit', background: 'var(--bg3)', border: '1px solid var(--border2)', color: 'var(--text2)',
        }}>Volver</button>
      </div>
    )
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minHeight: 0 }}>
      {/* sub-header */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '0 8px', height: 30, flexShrink: 0,
        borderBottom: '1px solid var(--border)', background: 'var(--bg2)',
        fontSize: 10, color: 'var(--text3)',
      }}>
        <button onClick={() => setPanel('trades')} style={{
          padding: '2px 10px', borderRadius: 3, fontSize: 10, cursor: 'pointer', fontFamily: 'inherit',
          background: panel === 'trades' ? 'var(--bg3)' : 'none',
          border: `1px solid ${panel === 'trades' ? 'var(--border2)' : 'transparent'}`,
          color: panel === 'trades' ? 'var(--text)' : 'var(--text3)',
        }}>Trades</button>
        <button onClick={() => setPanel('stats')} style={{
          padding: '2px 10px', borderRadius: 3, fontSize: 10, cursor: 'pointer', fontFamily: 'inherit',
          background: panel === 'stats' ? 'var(--bg3)' : 'none',
          border: `1px solid ${panel === 'stats' ? 'var(--border2)' : 'transparent'}`,
          color: panel === 'stats' ? 'var(--text)' : 'var(--text3)',
        }}>Stats</button>

        {meta && (
          <span style={{ color: 'var(--text2)' }}>
            {meta.n} trades ·{' '}
            <span title={`Pedido: ${days}d · Datos reales: ${meta.actualDays}d${meta.microStart ? ` · Micro desde: ${meta.microStart}` : ''}`}>
              {meta.actualDays}d datos
              {meta.actualDays < days * 0.9 && (
                <span style={{ color: 'var(--yellow)', marginLeft: 3 }}>
                  (pedido {days}d)
                </span>
              )}
            </span>
            {' · '}
            <span style={{ color: meta.n > 0 && meta.wins / meta.n >= 0.4 ? 'var(--green)' : 'var(--red)' }}>
              WR {meta.n > 0 ? (meta.wins / meta.n * 100).toFixed(0) + '%' : '—'}
            </span>
            {' · '}
            <span style={{ color: meta.equity >= 500 ? 'var(--green)' : 'var(--red)' }}>
              ${meta.equity.toFixed(2)}
            </span>
            {meta.microStart && (
              <span style={{ color: 'var(--text3)', marginLeft: 6 }}>
                · micro desde {meta.microStart.slice(0, 10)}
              </span>
            )}
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
        {panel === 'trades' && <LiveView trades={trades} />}
        {panel === 'stats'  && <StatsView trades={trades} />}
      </div>
    </div>
  )
}
