import { useState, useEffect } from 'react'
import LiveView from './LiveView'
import StatsView from './StatsView'
import type { Trade } from '../lib/types'
import { supabase } from '../lib/supabase'

type Panel = 'trades' | 'stats'

const STRATEGY_META = {
  rbf: {
    apiPath:    '/api/backtest',
    label:      'Backtest RBF — Python backend · $500 capital · $10/trade',
    detail:     'VR≥3× · CVD rango < 0 · Ext>0.1% · VWAP gate · Stop = Range HIGH\nTrail ATR 1.2× (activa 1.75R Short / 1.5R Long) · Time stop 30 bars · Cooldown 60 bars\nSessions: London · Overlap · NY',
    detail2:    'ETH: London skip · CVD≥−700 · OBI≤0.10 | BNB: cum_delta≥−500 | BTC: cum_delta≤+200\nExpansion gate: BTC/ETH/BNB ≤1 barra exp. últimas 25 · Pre-CVD 5b ≤ 0',
    presets:    [1, 3, 7, 14, 30] as number[],
    maxDays:    null as number | null,   // limitado por Supabase (necesita cvd_slope/vwap)
    defaultDays: null as number | null,  // null = usar todos los disponibles en Supabase
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

export default function BacktestView({ strategy = 'rbf' }: { strategy?: 'rbf' | 'be' }) {
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
      setTrades(data.trades as Trade[])
      setMeta({ n: data.n, wins: data.wins, equity: data.equity, actualDays: data.actual_days ?? d, microStart: data.micro_start ?? null })
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
            <span style={{ color: meta.wins / meta.n >= 0.4 ? 'var(--green)' : 'var(--red)' }}>
              WR {(meta.wins / meta.n * 100).toFixed(0)}%
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
