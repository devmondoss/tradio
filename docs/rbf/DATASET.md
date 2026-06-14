# Dataset — FlowSurface

*Última revisión: 2026-06-10*

---

## Qué estamos construyendo

Un dataset de microestructura de mercado en tiempo real para 4 activos (BTC, ETH, BNB, SOL) que combina:

1. **OHLCV por barra M1** — lo que cualquier exchange provee
2. **Microestructura del orderbook** — lo que Binance NO guarda históricamente (OBI, paredes, thin zones)
3. **Flow de órdenes** — CVD slope, delta z-score, absorción footprint, stacked imbalance
4. **Contexto institucional** — VPIN, OI momentum, VWAP de sesión

**Por qué importa:** Binance te da velas históricas. No te da el orderbook ni el flow de cada minuto pasado. Una vez que pasa, se perdió para siempre. Este dataset captura eso en tiempo real para usarlo en backtests futuros de RBF v2.

---

## Tablas activas

### `btc_bars` / `eth_bars` / `bnb_bars` / `sol_bars` / `xrp_bars`

Una fila por barra M1 por símbolo. Misma estructura en las 5 tablas.

**Frecuencia:** 1 fila/minuto = ~1,440 filas/día por tabla = ~7,200 filas/día total los 5 símbolos.

**Cobertura histórica:** backfill M1 desde 2026-05-11 para los 5 símbolos (ver sección Backfill).
Los campos de order book (`obi_l5`, `cvd_slope`, etc.) son NULL en el período de backfill —
solo están poblados desde que el monitor live arrancó (Jun 5–9 según símbolo).

#### Columnas completas

| Columna | Tipo | Descripción |
|---------|------|-------------|
| `id` | bigserial | PK autoincremental |
| `ts_ms` | bigint | Timestamp cierre de barra (Unix ms) |
| `symbol` | text | BTCUSDT / ETHUSDT / BNBUSDT / SOLUSDT |
| `session` | text | Asia / London / LondonNyOverlap / NewYork |
| `operative` | boolean | true = London o Overlap (sesiones donde corre RBF) |
| | | |
| **OHLCV** | | |
| `open` | float4 | Precio de apertura |
| `high` | float4 | Máximo de la barra |
| `low` | float4 | Mínimo de la barra |
| `close` | float4 | Precio de cierre |
| `volume` | float4 | Volumen total de la barra |
| `bar_delta` | float4 | buy_vol − sell_vol (delta neto comprador/vendedor) |
| | | |
| **Flow de órdenes** | | |
| `cvd_slope` | float4 | Slope OLS del CVD acumulado (USD/barra, ventana 20 barras). Negativo = presión vendedora sostenida. |
| `obi_l5` | float4 | Order Book Imbalance top 5 niveles. Rango [-1, +1]. Positivo = más bids. |
| `dz` | float4 | Delta z-score. Bar delta normalizado sobre últimas 50 barras. Extremos >3 tienden a revertir. |
| `vr` | float4 | Volume Ratio. Volumen barra actual / media 50 barras. VR ≥ 2 = breakout con volumen real. |
| `stacked_imb` | text | Stacked imbalance: Bullish / Bearish / None. 3+ barras consecutivas con mismo sesgo de delta = distribución institucional. |
| `absorption` | text | Absorción footprint: Bid / Ask / None. Detecta quién está absorbiendo presión contraria en VAH/VAL. |
| | | |
| **Orderbook snapshot** | | |
| `thin_above` | boolean | Thin zone por encima (poca liquidez → precio acelera si sube) |
| `thin_below` | boolean | Thin zone por debajo (poca liquidez → precio acelera si baja) |
| `bid_wall` | boolean | Muro de bids grande cerca del precio (soporte fuerte) |
| `ask_wall` | boolean | Muro de asks grande cerca del precio (resistencia fuerte) |
| | | |
| **Contexto institucional** | | |
| `vpin` | float4 | Volume-synchronized Probability of Informed trading. > 0.65 = flujo tóxico activo. |
| `oi_momentum` | boolean | true = OI expandiéndose en dirección del precio (posiciones nuevas, no cierres). |
| `vwap` | float4 | VWAP de sesión. Reset 00:00 UTC. Referencia de algoritmos institucionales. |
| `atr` | float4 | ATR(14) Wilder. Volatilidad normalizada de la barra. |
| `regime` | text | Régimen de mercado: TrendUp / TrendDown / Expansion / Chop. |
| | | |
| **ICT / AMD structural levels** | | |
| `asian_high` | float4 | Máximo de la sesión Asia (00:00–09:00 UTC) |
| `asian_low` | float4 | Mínimo de la sesión Asia |
| `prev_day_high` | float4 | PDH — máximo del día anterior (nivel clave institucional) |
| `prev_day_low` | float4 | PDL — mínimo del día anterior |
| `swing_high_50` | float4 | Máximo swing en las últimas 50 barras |
| `swing_low_50` | float4 | Mínimo swing en las últimas 50 barras |
| `equal_high` | boolean | true = high actual está a ≤0.03% de un swing previo (liquidity pool) |
| `equal_low` | boolean | true = low actual está a ≤0.03% de un swing previo |
| | | |
| **Microestructura adicional** *(añadidos 2026-06-10)* | | |
| `cvd_divergence` | text | `BearishAbsorption` cuando precio sube pero CVD cae — distribución silenciosa dentro del rango. `BullishAbsorption` cuando precio baja y CVD sube. NULL el resto del tiempo. Señal temprana de 5–10 barras antes del breakout. |
| `sweep_confirmed` | boolean | true = en las últimas 3 barras hubo un wick que superó un swing extremo pero el precio cerró de vuelta dentro. Liquidity grab clásico previo al movimiento real. |

---

### `rbf_signals`

Una fila por señal RBF emitida — tanto las tradeadas como las vetadas.

| Columna | Tipo | Descripción |
|---------|------|-------------|
| `id` | bigserial | PK |
| `symbol` | text | Símbolo |
| `timestamp_ms` | bigint | Timestamp del breakout |
| `direction` | text | Short / Long |
| `session` | text | Sesión donde ocurrió |
| `macro_regime` | text | Bear / Bull / BearPullback / BullPullback |
| `entry_price` | float4 | Precio de entrada |
| `stop_price` | float4 | Stop loss |
| `target_price` | float4 | Target |
| `rr` | float4 | Risk/Reward |
| | | |
| **Rango detectado** | | |
| `range_high` | float4 | Máximo del rango de consolidación |
| `range_low` | float4 | Mínimo del rango |
| `range_pct` | float4 | Tamaño del rango en % del precio |
| `range_bars` | int | Duración en barras M1 (15–60) |
| `cvd_in_range` | float4 | CVD acumulado durante el rango (negativo = presión vendedora) |
| `vr_at_breakout` | float4 | Volume ratio en la barra de ruptura |
| `range_touch_count` | int | Veces que tocó el extremo antes de romper |
| | | |
| **Microestructura en entry** | | |
| `cvd_slope_at_entry` | float4 | CVD slope en la barra de breakout |
| `dz_at_entry` | float4 | Delta z-score en la barra de breakout |
| `obi_at_entry` | float4 | OBI en la barra de breakout |
| `price_vs_vwap_pct` | float4 | Distancia al VWAP en % |
| `liq_ratio_pre` | float4 | Ratio de liquidaciones antes de la señal |
| `funding_at_entry` | float4 | Funding rate al momento de la señal |
| | | |
| **Confluencia v2** | | |
| `confluence_score` | smallint | Score 0–6. Cuántos flags live se cumplieron. |
| `confluence_flags` | text[] | Flags activos: ["stacked_imbalance","absorption","lvn_thin","vwap_bias","oi_momentum","obi_trap"] |
| `veto_reason` | text | Si fue vetada: hvn_target / vpin_toxic / long_bear_low_score |
| | | |
| **Outcome** | | |
| `status` | text | OPEN / TARGET / STOP / EXPIRED |
| `exit_price` | float4 | Precio de salida |
| `exit_reason` | text | Razón de salida |
| `result_r` | float4 | Resultado en R (2.0 = target, -1.0 = stop) |
| `closed_at` | timestamptz | Timestamp de cierre |

---

### `regime_history`

Historial de cambios de régimen del mercado.

| Columna | Descripción |
|---------|-------------|
| `timestamp_ms` | Cuándo cambió el régimen |
| `regime_combined` | Régimen nuevo (TrendUp / TrendDown / Expansion / Chop) |
| `price_at_change` | Precio en el momento del cambio |
| `duration_ms` | Cuánto duró el régimen anterior |

---

## Para qué sirve este dataset

### 1. Backtest completo de RBF v2 (objetivo principal)

Con las tablas `*_bars` podés reproducir exactamente lo que habría detectado el detector en el pasado:

```
Para cada barra M1:
  1. Detectar rangos usando high/low de las últimas 15–60 barras
  2. Verificar breakout: close < range_low o close > range_high
  3. Verificar vr >= 2 (ya está guardado)
  4. Calcular score v2 con los 7 flags (todos guardados)
  5. Simular outcome: buscar target/stop en las barras siguientes
```

**Lo que NO podés reconstruir retroactivamente sin este dataset:**
- `obi_l5` — requiere el orderbook en vivo en cada minuto
- `stacked_imb` — requiere los deltas barra a barra
- `absorption` — requiere footprint de trades en tiempo real
- `thin_above/below`, `bid_wall/ask_wall` — requiere el libro en tiempo real
- `vpin` — requiere el flujo de trades en tiempo real

### 2. Calibración de parámetros de confluencia

Con `rbf_signals` (cuando haya 50+ señales cerradas con `result_r`):

```sql
-- ¿El score predice el resultado?
SELECT confluence_score, COUNT(*) AS n, AVG(result_r) AS avg_r
FROM rbf_signals
WHERE result_r IS NOT NULL
GROUP BY 1 ORDER BY 1;

-- ¿Qué flags correlacionan con winners?
SELECT UNNEST(confluence_flags) AS flag, AVG(result_r) AS avg_r, COUNT(*) AS n
FROM rbf_signals
WHERE result_r IS NOT NULL
GROUP BY 1 ORDER BY avg_r DESC;
```

### 3. Análisis multi-símbolo

```sql
-- ¿En qué símbolo funciona mejor RBF?
SELECT symbol, COUNT(*) AS n,
       AVG(result_r) AS avg_r,
       COUNT(*) FILTER (WHERE result_r > 0) * 100 / COUNT(*) AS win_pct
FROM rbf_signals
WHERE result_r IS NOT NULL
GROUP BY symbol;
```

---

## Volumen de datos

| Tabla | Filas/día | Tamaño/mes | Tamaño/año |
|-------|-----------|------------|------------|
| btc_bars | ~1,440 | ~6 MB | ~72 MB |
| eth_bars | ~1,440 | ~6 MB | ~72 MB |
| bnb_bars | ~1,440 | ~6 MB | ~72 MB |
| sol_bars | ~1,440 | ~6 MB | ~72 MB |
| xrp_bars | ~1,440 | ~6 MB | ~72 MB |
| rbf_signals | ~2–5 | < 1 MB | < 5 MB |
| regime_history | ~10–20 | < 1 MB | < 5 MB |
| **Total** | ~7,200 | **~31 MB** | **~365 MB** |

Supabase free tier: 500 MB. Sin problema por más de 1 año.

---

## Backfill histórico

El script `scripts/backfill_bars.py` rellena los `*_bars` con datos de Binance Futures
(`/fapi/v1/klines`) para el período antes de que el monitor live arrancara.

### Qué calcula del OHLCV histórico

| Campo | Cómo | Exactitud |
|-------|------|-----------|
| `bar_delta` | `2 × takerBuyVol − totalVol` (campo 9 de klines Binance) | Exacto |
| `vr` | `volume / avg_30bars` | Exacto |
| `atr` | EMA14 del True Range | Exacto |
| `vwap` | VWAP acumulado con reset UTC 00:00 | Exacto |
| `session` | Clasificación por minutos UTC (espejo de session_tracker.rs) | Exacto |

### Qué NO puede reconstruirse (queda NULL en backfill)

`cvd_slope`, `obi_l5`, `obi_fast`, `obi_slow`, `dz`, `liq_ratio`, `spread_ticks`,
`stacked_imb`, `absorption`, `thin_above`, `thin_below`, `bid_wall`, `ask_wall`,
`vpin`, `oi_momentum`, `cvd_divergence`, `sweep_confirmed`, todos los ICT levels.

Estos requieren snapshots del orderbook o flujo de trades en tiempo real — una vez que pasa,
se pierde para siempre.

### Uso

```bash
# Backfill normal (detecta qué falta y lo rellena)
python scripts/backfill_bars.py

# Forzar re-backfill (sobreescribe el período histórico con merge-duplicates)
# Útil si cambiaste el intervalo (ej: de M5 a M1)
python scripts/backfill_bars.py --force

# Solo un símbolo
python scripts/backfill_bars.py --symbol BTCUSDT

# Ver cuánto traería sin insertar nada
python scripts/backfill_bars.py --dry-run
```

El script detecta automáticamente el "primer bar live" (el primero con `obi_l5 NOT NULL`)
para no sobreescribir datos live que ya tienen microestructura real.

### Limitación importante

El backfill OHLCV sirve para el detector básico de RBF (range + VR + session + VWAP).
Para analizar los filtros de microestructura (`obi_l5 gate`, `stacked_imb`, etc.) solo
sirven los datos live. Cada día adicional del monitor en Railway vale más que cualquier
cantidad de backfill histórico.

---

## Milestones de calibración con estos datos

| Condición | Acción | Estado |
|-----------|--------|--------|
| 30 señales cerradas | Primera lectura de distribución de scores | ✅ Completado |
| 50 señales cerradas | Verificar gradiente score → avg_r. Decidir si los flags discriminan. | ✅ 72 trades live (2026-06-10) |
| 100 señales cerradas | Fijar `min_confluence_score` real (subir de 1 a 3) en strategy.toml | En progreso |
| n=25 Shorts por símbolo | Calibración per-símbolo completa (cum_delta threshold, OBI gate ETH) | En progreso |
| 200 señales cerradas | Validar vetos: ¿bloquearon principalmente losers? Calibrar wall_target. | Pendiente |
| 200+ señales, WR > 55% | Considerar escalar tamaño de posición | Pendiente |

### Calibraciones implementadas (2026-06-10)

**Expansion filter** (`expansion_bars_recent` / `expansion_max_bars`):
- Campo `expansion_bars_recent: u8` en `RbfGateContext` — conteo de barras Expansion en últimas 25 M1
- Campo `expansion_max_bars: Option<u8>` en `RangeBreakoutConfig` — filtro por símbolo
- BTC/ETH/BNB: max=3. SOL/XRP: bypass (correlación invertida / muestra insuficiente)

**Trailing direction-aware:**
- `TRAIL_ACTIVATE_R_SHORT = 1.75` (antes 1.5). 4 Shorts trailing cedieron avg 0.97R/trade.
- `TRAIL_ACTIVATE_R_LONG  = 1.5` — sin cambio

Ver análisis completo en `docs/rbf/RBF_CALIBRACION_POR_ACTIVO.md`.

---

---

## Historial de cambios

| Fecha | Cambio |
|-------|--------|
| 2026-06-06 | Dataset arranca. BTC, ETH, BNB, SOL. Monitor M1 en Railway. |
| 2026-06-09 | XRP añadido. 5 símbolos activos. |
| 2026-06-10 | Backfill M1 desde 2026-05-11 para los 5 símbolos (~188k filas). Script `backfill_bars.py`. |
| 2026-06-10 | Nuevas columnas `cvd_divergence` y `sweep_confirmed` en todas las tablas `*_bars`. |

*Dataset activo desde 2026-06-06. 5 símbolos (BTC/ETH/BNB/SOL/XRP) en Railway M1.*
