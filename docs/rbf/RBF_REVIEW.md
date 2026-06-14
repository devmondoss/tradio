# RBF Trade Reviewer — Documentación

**Última actualización:** 2026-06-13  
**Ruta:** `apps/rbf-review/`  
**Stack:** React + Vite + TypeScript + Python backend

---

## 1. Arquitectura general

```
App.tsx
├── Tab: Overview  → DashboardView
├── Tab: RBF       → RBFModuleView (live + stats)
├── Tab: HTF       → HTFModuleView (backtest HTF shorts) — ver docs/htf/
├── Tab: AMD       → StrategyModuleView strategy="amd"
└── Tab: BE        → StrategyModuleView strategy="be"
```

El backend Python corre como subprocess vía proxy Vite (`vite.config.ts`):
- `/api/backtest` → `backtest_script.py`
- `/api/backtest/shorts` → `shorts_htf_backtest.py`

---

## 2. Vistas

### RBFModuleView
Subtabs: `live` | `stats`

- **live**: lista de trades en tiempo real desde Supabase `rbf_signals`. Click en trade → TradeChart.
- **stats**: StatsView con equity curve SVG + R Distribution + tablas por sesión/símbolo/exit reason.

BacktestView fue eliminado de RBF — solo existe en HTFModuleView.

### HTFModuleView
Módulo independiente para el sistema HTF shorts. Renderiza `<BacktestView strategy="shorts" />`.  
Ver documentación completa en `docs/htf/`.

### BacktestView
Strategies: `'rbf'` | `'sweep'` | `'shorts'`  
Auto-run al cargar. Presets de días configurables por strategy.

---

## 3. TradeChart — overlay visual

**Archivo:** `apps/rbf-review/src/components/TradeChart.tsx`

### 3.1 Fetch de velas

```typescript
// binance.ts — fetchKlines con cache y endMs opcional
fetchKlines(symbol, tsMs, totalLimit, extraBars, endMs?)

// Cálculo de límites:
closedAtSec   = new Date(trade.closedAt).getTime() / 1000
durationBars  = ceil((closedAtSec - trade.ts) / 60) + 30
extraBars     = rangeBars + 220
totalLimit    = min(extraBars + durationBars, 1500)
```

Trades largos (ej. 16h) fetchan hasta 1500 velas para cubrir el SL/TP real.

### 3.2 Vista inicial

```
from: trade.ts - (rangeBars + 100) * 60   ← 100 velas de contexto previo
to:   closedAtSec + 120 * 60              ← 120 velas post-exit para ver reacción
```

### 3.3 Borde derecho de la caja (exitTs)

Busca la **primera vela real** que tocó el nivel de exit, según dirección y tipo de cierre:

| Dirección | Exit type  | Condición de toque      |
|-----------|------------|-------------------------|
| Short     | STOP_LOSS  | `c.high >= t.stop`      |
| Short     | TP / CVD   | `c.low  <= t.exit`      |
| Long      | STOP_LOSS  | `c.low  <= t.stop`      |
| Long      | TP / CVD   | `c.high >= t.exit`      |

Reglas:
- Excluye la vela de entry (`c.time > t.ts`, no `>=`)
- Sin tolerancia de precio (exacto, no `* 1.001`)
- `exitTs = firstTouch.time + 60` (+1 vela para ver la mecha completa)
- Fallback: `closedAt` → `ts + durationMin * 60` → `ts + 90min`

### 3.4 Caja de riesgo (roja)

- Cubre zona `entry → stop`
- Alpha 7% en wins (apenas visible), 25% en losses
- Texto "STOP LOSS" centrado, 20% opacity wins / 60% losses

### 3.5 Caja de profit (verde)

**`exitedEarly`** = `t.exit > 0 && reason !== 'TAKE_PROFIT' && reason !== 'STOP_LOSS'`  
No requiere `isWin` — aplica a cualquier exit intermedio (CVD_EXHAUSTION, EXPIRED, etc.)

| Caso | Altura de la caja |
|------|-------------------|
| `TAKE_PROFIT` | entry → target completo |
| `STOP_LOSS` | entry → target (potencial, se ve como zona) |
| `CVD_EXHAUSTION` / `EXPIRED` | entry → exit real |

Cuando `exitedEarly`:
- Caja verde termina en `t.exit`
- Línea fantasma tenue (25% opacity, dashed) muestra dónde estaba el TP completo
- Texto dentro = razón de exit (`"CVD EXHAUSTION"`)

### 3.6 Línea de exit

Solo cuando `exitedEarly`. Para TP/SL el borde de la caja es el indicador visual.  
Color: verde si win, rojo si loss.  
Label: `"CVD EXHAUSTION  60517.3"` a la derecha del borde.

### 3.7 Labels de precio (posición por dirección)

| Label | Short | Long |
|-------|-------|------|
| `ENTRY` | `eY - 3` | `eY - 3` |
| `SL` | `sY - 3` (encima) | `sY + 12` (debajo) |
| `TP` | `tY + 12` (debajo) | `tY - 3` (encima) |
| `SHORT`/`LONG` | `eY - 16` siempre | `eY - 16` siempre |
| Badge R/PnL | `max(boxTop - 8, 16)` — clampea para no salir del canvas |

### 3.8 Herramientas de dibujo (toolbar izquierda)

| Herramienta | Función |
|-------------|---------|
| cursor | selección y arrastre de dibujos existentes |
| shortpos / longpos | posición con SL/TP/time draggable |
| trendline / ray / extline | líneas de tendencia |
| hline / vline | niveles horizontales y verticales |
| rect | rectángulos de zonas |
| arrow / text | anotaciones |
| measure | medida R/% entre dos puntos |
| eraser | borrador selectivo |
| ⌖ imán | snap a OHLC de la vela más cercana |
| ⊕ (top-right) | reset zoom / fitContent |

---

## 4. StatsView — R Distribution

Dots por cada trade posicionados en su R exacto:
- Rojo = STOP_LOSS
- Azul = TRAILING_STOP / CVD_EXHAUSTION
- Verde = TAKE_PROFIT

Leyenda: n, %, avg R, total R por tipo de exit.

---

## 5. Supabase realtime

`App.tsx` subscribe a `rbf_signals` con `postgres_changes`:
- `INSERT` → append al state
- `UPDATE` → replace por id

90 días de historia en el query inicial. Símbolos: BTC, ETH, BNB, SOL, XRP.

---

## 6. Archivos clave

| Archivo | Descripción |
|---------|-------------|
| `apps/rbf-review/src/App.tsx` | Tabs + Supabase realtime |
| `apps/rbf-review/src/views/RBFModuleView.tsx` | live + stats subtabs |
| `apps/rbf-review/src/views/HTFModuleView.tsx` | módulo HTF independiente |
| `apps/rbf-review/src/views/BacktestView.tsx` | runner backtest UI |
| `apps/rbf-review/src/views/StatsView.tsx` | equity curve + R distribution |
| `apps/rbf-review/src/views/LiveView.tsx` | lista trades + chart |
| `apps/rbf-review/src/components/TradeChart.tsx` | chart individual + overlay |
| `apps/rbf-review/src/components/FilterBar.tsx` | filtros por sesión/símbolo/dirección |
| `apps/rbf-review/src/lib/binance.ts` | fetchKlines con cache + endMs |
| `apps/rbf-review/src/lib/supabase.ts` | cliente + tipos RbfSignal |
| `apps/rbf-review/src/lib/utils.ts` | buildTrades, fmtR, applyFilters |
| `apps/rbf-review/vite.config.ts` | proxy /api/backtest/* → scripts Python |
| `docs/htf/HTF_SHORTS_SISTEMA.md` | documentación completa del sistema HTF |
