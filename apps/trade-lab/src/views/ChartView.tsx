import { useEffect, useRef, useState } from 'react'

declare global {
  interface Window {
    TradingView?: { widget: new (config: Record<string, unknown>) => unknown }
  }
}

const SYMBOLS = [
  { id: 'BTCUSDT', tv: 'BINANCE:BTCUSDT.P', label: 'BTC' },
  { id: 'ETHUSDT', tv: 'BINANCE:ETHUSDT.P', label: 'ETH' },
  { id: 'SOLUSDT', tv: 'BINANCE:SOLUSDT.P', label: 'SOL' },
  { id: 'BNBUSDT', tv: 'BINANCE:BNBUSDT.P', label: 'BNB' },
  { id: 'XRPUSDT', tv: 'BINANCE:XRPUSDT.P', label: 'XRP' },
]

const INTERVALS = [
  { id: '1',   label: '1m'  },
  { id: '5',   label: '5m'  },
  { id: '15',  label: '15m' },
  { id: '60',  label: '1h'  },
  { id: '240', label: '4h'  },
  { id: 'D',   label: '1D'  },
]

// Read a CSS custom property from <html>
function cssVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

export default function ChartView({ theme }: { theme: 'light' | 'dark' }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const scriptLoaded = useRef(false)

  const [sym,      setSym]      = useState('BTCUSDT')
  const [interval, setInterval] = useState('240')

  const tvSym = SYMBOLS.find(s => s.id === sym)?.tv ?? 'BINANCE:BTCUSDT.P'

  function initWidget() {
    if (!containerRef.current || !window.TradingView) return
    containerRef.current.innerHTML = ''

    const isDark = theme === 'dark'

    // Pull colors from CSS vars so the widget matches the app theme
    const bg        = cssVar('--chart-bg')
    const grid      = cssVar('--chart-grid')
    const border    = cssVar('--chart-border')
    const textColor = cssVar('--chart-text')
    const upColor   = cssVar('--candle-up')   || (isDark ? '#e8e8f0' : '#0a0a0f')
    const downColor = cssVar('--candle-down') || (isDark ? '#3a3a48' : '#d0d0d8')
    const upBorder  = cssVar('--candle-up-b') || (isDark ? '#e8e8f0' : '#0a0a0f')
    const dnBorder  = cssVar('--candle-down-b') || (isDark ? '#5a5a6e' : '#b0b0b8')

    new window.TradingView.widget({
      container_id:        'tv-chart-container',
      autosize:            true,
      symbol:              tvSym,
      interval:            interval,
      timezone:            'UTC',
      theme:               isDark ? 'dark' : 'light',
      style:               '1',
      locale:              'en',
      toolbar_bg:          bg,

      hide_top_toolbar:    true,
      hide_legend:         false,
      hide_side_toolbar:   false,

      allow_symbol_change: false,
      save_image:          false,
      show_popup_button:   false,
      withdateranges:      false,

      disabled_features: [
        'header_widget',
        'header_symbol_search',
        'header_resolutions',
        'header_chart_type',
        'header_settings',
        'header_indicators',
        'header_compare',
        'header_undo_redo',
        'header_screenshot',
        'header_fullscreen_button',
        'timeframes_toolbar',
        'legend_context_menu',
      ],
      enabled_features: [
        'use_localstorage_for_settings',
        'side_toolbar_in_fullscreen_mode',
      ],

      overrides: {
        'paneProperties.background':               bg,
        'paneProperties.backgroundType':           'solid',
        'paneProperties.vertGridProperties.color': grid,
        'paneProperties.horzGridProperties.color': grid,
        'scalesProperties.textColor':              textColor,
        'scalesProperties.lineColor':              border,
        'scalesProperties.fontSize':               11,

        // dark: relleno sólido — blanco up, gris oscuro down
        // light: monocromático — negro up, gris claro down
        'mainSeriesProperties.candleStyle.upColor': upColor,
        'mainSeriesProperties.candleStyle.downColor': downColor,
        'mainSeriesProperties.candleStyle.borderUpColor': upBorder,
        'mainSeriesProperties.candleStyle.borderDownColor': dnBorder,
        'mainSeriesProperties.candleStyle.wickUpColor':
          isDark ? '#e8e8f0'  : '#0a0a0f',
        'mainSeriesProperties.candleStyle.wickDownColor':
          isDark ? '#5a5a6e'  : '#c0c0c8',

        'mainSeriesProperties.priceLineColor': isDark ? '#e8e8f0' : '#0a0a0f',
      },

      loading_screen: { backgroundColor: bg, foregroundColor: border },
    })
  }

  useEffect(() => {
    if (window.TradingView) {
      initWidget()
      return
    }
    if (scriptLoaded.current) return
    scriptLoaded.current = true
    const script = document.createElement('script')
    script.src   = 'https://s3.tradingview.com/tv.js'
    script.async = true
    script.onload = () => initWidget()
    document.head.appendChild(script)
  }, [tvSym, interval, theme])

  return (
    <div className="chart-view">

      {/* ── Sub-bar: símbolo + temporalidad ───────────────────────────── */}
      <div className="chart-subbar">
        {SYMBOLS.map(s => (
          <button
            key={s.id}
            onClick={() => setSym(s.id)}
            className={`chart-sym-btn${sym === s.id ? ' active' : ''}`}
          >
            {s.label}
          </button>
        ))}

        <div className="chart-subbar-sep" />

        {INTERVALS.map(iv => (
          <button
            key={iv.id}
            onClick={() => setInterval(iv.id)}
            className={`chart-iv-btn${interval === iv.id ? ' active' : ''}`}
          >
            {iv.label}
          </button>
        ))}
      </div>

      {/* ── TradingView widget ────────────────────────────────────────── */}
      <div
        id="tv-chart-container"
        ref={containerRef}
        style={{ flex: 1, overflow: 'hidden' }}
      />
    </div>
  )
}
