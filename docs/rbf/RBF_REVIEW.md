# RBF Review — App de revisión y backtest

*Última revisión: 2026-06-10*

---

## Qué es

`apps/rbf-review/` es una app React/TypeScript/Vite para revisar señales RBF — tanto el paper trading
live como backtests históricos. Corre localmente con `npm run dev`.

---

## Cómo correr

```bash
cd apps/rbf-review
npm install     # primera vez
npm run dev     # inicia Vite + middleware Python en http://localhost:5173
```

El servidor Vite intercepta `/api/backtest` y ejecuta `api/backtest_script.py` como subprocess.
No hace falta uvicorn ni ningún proceso separado.

---

## Vistas

### Live

Muestra los trades del paper trader RBF cargados desde Supabase (`rbf_signals`).

- **TradeList** — panel izquierdo: lista de todos los trades con color verde/rojo, sesión, R obtenido
  - Cabecera con capital: `$500.00 → $607.050 (+21.41%)` en badge con contraste
- **TradeChart** — panel central: gráfico TradingView Lightweight con velas M5, cajas SL/TP, punto de entrada
  - Botón `⊕` (esquina superior derecha) para resetear el zoom Y cuando el precio de un activo
    difiere mucho del anterior (BTC ~70k vs BNB ~590)
- **TradeDetail** — panel derecho: métricas del trade seleccionado (score, confluencia, vr, dz, etc.)

### Backtest

Corre el detector RBF sobre los datos históricos de Supabase (`*_bars`).

- **Pre-run:** muestra "Datos en BD desde: 5 jun (~5d)" consultando el primer bar de `btc_bars`
- **Selector de días:** botones dinámicos — solo muestra días que realmente existen en la BD
  (si hay 5 días de datos, muestra `1d | 3d | 5d`, no `7d | 14d | 30d`)
- **Post-run:** cabecera con `{n} trades · {actualDays}d datos` donde `actualDays` viene del
  script Python (primer bar real encontrado entre los 5 símbolos)

### Stats

Tabla de rendimiento desglosada por sesión, símbolo y dirección.

- **Capital summary** — 4 celdas: Capital Inicial / Capital Final / Ganancia / Retorno %
- **Equity curve** — SVG escalado al contenedor, puntos por trade, línea base VWAP-style
- **3 tablas:** Por Sesión / Por Símbolo / Long vs Short con WR%, AvgR, TotR, PnL

---

## Parámetros del backtest (backtest_script.py)

```
Capital:          $500    Riesgo/trade:  $10 (2% fijo)
VR mínimo:        3.0×    Sesiones OK:   London, LondonNyOverlap, NewYork
Breakout ext min: 0.1%    VWAP gate:     max 0.3% bajo VWAP
Trail ATR:        1.2×    Activa trail Short: 1.75R  (calibrado 2026-06-10)
                          Activa trail Long:  1.5R
Time stop:        off     Cooldown:      60 barras
RR Short:         2.0     Solo Shorts    (Longs desactivados — WR=14%)
```

**Calibración trailing (2026-06-10):** 4 Short trailing cases salieron a +0.90–1.32R cuando el
target era 2R. TRAIL_ACTIVATE_R_SHORT subido de 1.5 a 1.75 para reducir exits prematuros.

**Live (2026-06-02 → 2026-06-10):** 72 trades en BTC/ETH/BNB/SOL/XRP.
Ver [RBF_CALIBRACION_POR_ACTIVO.md](RBF_CALIBRACION_POR_ACTIVO.md) para análisis por símbolo.

El script no replica fielmente todos los filtros del monitor live (le faltan `obi_l5`, `cvd_slope`,
`stacked_imb` que son NULL en el backfill histórico). Es una aproximación válida para OHLCV + flow.

---

## Arquitectura

```
apps/rbf-review/
├── src/
│   ├── views/
│   │   ├── LiveView.tsx       — layout 3 paneles (TradeList + TradeChart + TradeDetail)
│   │   ├── BacktestView.tsx   — pre-run / loading / post-run, llama a /api/backtest
│   │   └── StatsView.tsx      — CapitalSummary + EquityChart + 3 StatTables
│   ├── components/
│   │   ├── TradeList.tsx      — lista scrollable con header de capital
│   │   ├── TradeChart.tsx     — gráfico Lightweight Charts + botón ⊕
│   │   └── TradeDetail.tsx    — métricas del trade seleccionado
│   └── lib/
│       ├── types.ts           — Trade, ACCOUNT ($500)
│       ├── supabase.ts        — cliente Supabase
│       └── utils.ts           — fmtR, fmtUsd, sesLabel, winRate, avgR...
└── api/
    ├── server.ts              — middleware Vite que intercepta /api/backtest
    └── backtest_script.py     — detector RBF en Python puro (stdlib + urllib)
```

---

## Cómo interpreta los datos

- `ACCOUNT = 500` — capital inicial fijo en `lib/types.ts`
- `RISK_USD = 10` — riesgo por trade (2% de 500)
- `equity` — acumulado de `ACCOUNT + sum(pnlUsd)` recalculado al cargar
- `isOpen` — trades con `result_r = null` (posición viva en el monitor)
- `resultR` — R realizado: `2.0` = target alcanzado, `-1.0` = stop

---

## Notas de UX

- El selector de días consulta Supabase al montar para detectar cuántos días hay realmente.
  Si la BD tiene 5 días, no tiene sentido mostrar el botón "30d".
- El botón `⊕` en TradeChart existe porque BTC cotiza ~70k y BNB ~590 — al cambiar de activo
  el eje Y queda desescalado. El botón llama a `fitContent()` + `autoScale: true`.
- `actualDays` vs `days`: el script Python retorna cuántos días reales encontró. Si pediste
  30d pero solo hay 5d de barras, la cabecera muestra "5d datos (pedido 30d)" en amarillo.
