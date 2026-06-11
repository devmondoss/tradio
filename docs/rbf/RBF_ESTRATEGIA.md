# Range Breakout Flow (RBF) — Documentación completa

Última revisión: 2026-06-11 (OBI intrabar 10s · spread gate · OI covering gate · multi-depth confluence)

---

## Qué es y por qué existe

RBF es el detector principal del sistema. Reemplaza el enfoque de scalping microestructural (S1/S2/S3 con targets de $35) por un detector de continuación de movimiento real ($300–$800 en BTC).

**Hipótesis central:**  
Cuando el precio consolida en un rango estrecho (0.08–0.55% del precio) y el CVD acumula presión en una dirección durante esa consolidación, el breakout del rango con volumen confirmado (VR ≥ 2×) produce un movimiento sostenido de 0.5–1%+ en la misma dirección.

**Validado en backtest:** 30 días M1, 43,200 barras, n=914 señales con CVD alineado.
VR mínimo: 3× (no 2×). Stop dinámico: 1.0×ATR.

**Live (2026-06-02 → 2026-06-10):** 72 trades en 5 símbolos (BTC/ETH/BNB/SOL/XRP).
Ver análisis completo en [RBF_CALIBRACION_POR_ACTIVO.md](RBF_CALIBRACION_POR_ACTIVO.md).

---

## Cómo funciona — paso a paso

### 1. Detección del rango

El detector mantiene un historial de las últimas 60 barras M1. En cada cierre de barra prueba 5 ventanas distintas: 15, 20, 30, 45 y 60 barras hacia atrás.

Para cada ventana calcula:
- `range_high` = máximo de los highs de la ventana
- `range_low`  = mínimo de los lows de la ventana
- `range_pct`  = (range_high - range_low) / precio × 100

**El rango es válido si:**
- `range_pct` entre **0.08%** y **0.55%** del precio
- Rango muy pequeño (< 0.08%) = ruido puro, no hay consolidación real
- Rango grande (> 0.55%) = mercado trending, no es un rango

A $70,000 BTC esto equivale a rangos de $56 a $385 de amplitud.

### 2. CVD acumulado en el rango

Dentro de la ventana identificada, suma el delta de cada barra:
```
cvd_in_range = Σ(bar_delta) para cada barra de la ventana
```

El `bar_delta` es la diferencia entre volumen comprador y vendedor de cada barra M1 (calculado como `2 × buy_vol - volume`).

**Interpretación:**
- `cvd_in_range < 0` = vendedores dominaron la consolidación (sesgo bajista)
- `cvd_in_range > 0` = compradores dominaron la consolidación (sesgo alcista)

### 3. Breakout confirmado

La señal dispara cuando **la barra actual cierra fuera del rango** con **VR ≥ 3×**:
- Cierre bajo `range_low` → **SHORT breakdown**
- Cierre sobre `range_high` → **LONG breakout**

**El CVD debe estar alineado con la dirección:**
- SHORT: `cvd_in_range < 0` (vendedores construyeron presión)
- LONG: `cvd_in_range > 0` (compradores construyeron presión)

Si el CVD no está alineado → señal rechazada (fakeout probable).

### 4. Régimen macro (EMA480)

El detector calcula una EMA de 480 barras M1 (~8 horas) del precio de cierre. Esto define el régimen macro:

| Condición | Régimen |
|-----------|---------|
| precio > EMA480 y EMA subiendo | `Bull` |
| precio < EMA480 y EMA bajando | `Bear` |
| precio > EMA480 y EMA bajando | `BullPullback` |
| precio < EMA480 y EMA subiendo | `BearPullback` |

**En backtest:**
- Shorts en régimen Bear: mayor probabilidad de continuación
- Longs contra-tendencia (Bear): edge a +30min por short squeeze

### 5. Precios de entrada

```
Entry:  cierre de la barra de breakout
Stop:   entry ± 1.0 × ATR(14)                    → distancia dinámica
Target: entry ∓ 2.0 × ATR   para SHORT  (RR 2:1)
        entry ± 1.8 × ATR   para LONG   (RR 1.8:1)
RR mínimo requerido: 1.5
```

A $70,000 BTC con ATR ≈ $50 (M1 típico):
- Stop ≈ $50 contra la entrada
- Target SHORT ≈ $100 a favor
- Target LONG ≈ $90 a favor

El stop es **dinámico**: se usa `1.0 × ATR(14)` en vez de un porcentaje fijo.
Razón: el stop fijo (0.25% ≈ 2.6×ATR promedio) era demasiado ancho; el grid search
sobre M1 mostró que 1.0×ATR minimiza stops en barra 1 (era 67% con 0.7×ATR).

### 5b. Trailing stop

Una vez abierta la posición, el stop se gestiona de forma dinámica:

```
TRAIL_ACTIVATE_R_SHORT = 1.75R  → Shorts activan trailing al llegar a 1.75R (target 2R)
TRAIL_ACTIVATE_R_LONG  = 1.5R   → Longs activan trailing al llegar a 1.5R (target 1.8R)
TRAIL_ATR_K            = 1.2    → distancia del stop al extremo favorable = 1.2 × ATR
TIME_STOP_BARS         = 30     → si a los 30 min la posición está en pérdida, cierra al mercado
```

**Calibración 2026-06-10:** 4 trades Short salieron por trailing a +0.90–1.32R cuando el target
era 2R — cedieron ~0.97R/trade. Raising Short threshold 1.5→1.75 reduce exits prematuros sin
eliminar la protección ante reversales.

### 5c. Filtro expansion_bars_recent

Introducido en la calibración 2026-06-10. En las últimas 25 barras M1 previas a la señal,
cuenta cuántas estuvieron en régimen `Expansion`. Si ese conteo supera el umbral del símbolo,
la señal se descarta (el movimiento ya está maduro).

| Símbolo | `expansion_max_bars` | Razón |
|---------|---------------------|-------|
| BTCUSDT | **1** | Análisis 72 trades: ≤1 → WR=54.5% vs ≤3 → 48.8% |
| ETHUSDT | **1** | Losses 4.8 vs wins 1.3 |
| BNBUSDT | **1** | Losses 7.8 vs wins 3.6 (mayor diferencia de todos) |
| SOLUSDT | None (bypass) | Correlación invertida: wins tienen MÁS expansion (5.2 vs 4.2) |
| XRPUSDT | None (bypass) | Muestra insuficiente |

El conteo se pasa via `RbfGateContext.expansion_bars_recent`. SOL/XRP reciben 0 (bypass).
Ver [RBF_CALIBRACION_POR_ACTIVO.md](RBF_CALIBRACION_POR_ACTIVO.md) para el análisis completo.

### 5d. Gates adicionales (2026-06-11)

**Pre-CVD gate:** en el path Short, si la suma de `bar_delta` de las últimas 5 barras del rango
es positiva (compradores activos al final de la consolidación), se salta la señal sin activar cooldown.
Análisis live n=7: WR=0%, avg=−1.38R. El skip sin cooldown permite re-escanear el mismo rango en la barra siguiente.

**Score ≠ 4 gate:** score=4 es la zona "ambigua" — suficiente para pasar el min_score pero con más
factores en contra que a favor. Análisis live n=11: WR=9%, total=−10.68R → se registra en Supabase pero no opera.

**London CVD gate:** si `session == London` y `cvd_in_range > 200` → skip. Fakeout con compradores activos en el rango.

**Spread gate:** `spread_bps > 5` → veto `spread_wide`. Mercado ilíquido = breakout de noise.

**OI covering gate:** Short con `oi_delta_pct < −10%` → veto `oi_covering`. Longs cerrando = presión compradora.

### 5e. OBI multi-depth y OBI intrabar (2026-06-11)

**obi_multi_depth (+1 score):** requiere que tanto `obi_l10 < −0.05` como `obi_l20 < −0.03` para Shorts. El OBI L5 puede ser
spoofed con órdenes en los primeros niveles; L10 y L20 son más difíciles de manipular.

**obi_intrabar_mean (+1 score):** durante cada barra M1 se toman muestras de OBI cada 10 segundos (~6 muestras/barra).
La media de esas muestras se pasa al scorer. Si la media < −0.05 para Shorts, suma +1. Detecta presión sostenida
vs spike puntual al cierre de barra.

Ambas muestras se guardan en la tabla `obi_10s` de Supabase para análisis futuro.

**Score máximo actual: 11** (era 9 antes del 2026-06-11).

### 6. Cooldown

Una vez disparada una señal, el detector espera **60 barras M1 (60 minutos)** antes de buscar otra. Evita señales consecutivas en el mismo contexto.

---

## Comportamiento por sesión

### London — 08:00 a 13:00 UTC

**Características:** Frankfurt abre a las 08:00, LSE a las 08:30. Liquidez moderada en aumento. Las primeras 2 horas suelen ser los movimientos más impulsivos del día europeo.

**Comportamiento del detector:**
- Los rangos más comunes son de 15–20 barras (15–20 minutos)
- El CVD alcanza sesgos fuertes en aperturas (OpeningRush 08:00–08:15)
- Breakouts con VR ≥ 3× en London tienen el mejor edge del backtest
- Si el CVD ya es bajista desde la sesión Asia, los Shorts en London tienen viento a favor

**Señal más rentable del backtest:** SHORT con CVD bajista + VR ≥ 3× en London → exp=+0.274% a +60min (n=32)

**Red flags en London:**
- Rangos que aparecen inmediatamente después de un dato macro (CPI, NFP) → VR extremo pero fakeout frecuente
- Primeros 15 minutos (OpeningRush): alta volatilidad, stops más ajustados pueden ser barridos

---

### LondonNyOverlap — 13:00 a 17:00 UTC

**Características:** La ventana de máxima liquidez del día. London sigue activo, NYSE abre a las 13:30. El volumen sube 2–4× respecto a London puro.

**Comportamiento del detector:**
- Los rangos típicos son 20–30 barras (formados en la primera mitad de London, rotos al abrir NY)
- El CVD acumulado desde London puede ser muy negativo o muy positivo al llegar al Overlap
- Los conviction events más frecuentes y con mayor VR ocurren aquí (14:00–15:00 UTC zona caliente)
- El edge de SHORT contra-tendencia (Bull macro) es más alto aquí que en London puro

**Señal más frecuente:** Breakdown SHORT del rango formado entre 11:00–12:30 UTC, que rompe al abrir NYSE (13:30 UTC). El mercado americano confirma o contradice el sesgo europeo.

**Ejemplo real (hoy 12:18):** Rango 0.42% × 15 min, CVD −476 (vendedores dominaron), VR 3.95× → TARGET en 79 min → +$346.

**Red flags en Overlap:**
- LONG breakout cuando el CVD sesión lleva varias horas muy negativo → alta probabilidad de fakeout
- Entre 15:00–16:00 UTC (afternoon lull de NY) el volumen cae, VR puede ser bajo

---

### NewYork — 17:00 a 22:00 UTC

> **Estado:** operativa desde 2026-06-09. Habilitada para acumular datos reales
> y evaluar el edge con n≥15. No hay suficientes señales live todavía para
> confirmar o descartar.

**Características:** London cerró. Solo participantes americanos. Menor liquidez que el Overlap pero mayor que London solo. NYSE cierra a las 20:00 UTC.

**Comportamiento del detector:**
- Rangos más cortos (15 barras) son más comunes porque el mercado está más calmado
- El CVD sesión suele ser bajo en valor absoluto (se resetea al inicio del día) si la sesión arrancó a medianoche UTC
- La hora más activa: 17:00–18:30 UTC (settlement americano)
- El edge de SHORT es bueno si el mercado cayó todo el día y hay CVD bajista acumulado

**Patrón más frecuente en NY:** Continuación del movimiento que inició en London/Overlap. Si London bajó 1% y Overlap siguió, NY confirma con otro impulso de 0.5%.

**Red flags en NY:**
- Después de las 20:00 UTC el volumen cae significativamente — señales con VR 2× pueden ser menos confiables porque el denominador (VR medio) es bajo
- Close de sesión NYSE puede generar spikes de volatilidad falsos sin seguimiento

---

## Variables grabadas por señal

### En `rbf_signals` (Supabase)

| Campo | Descripción |
|-------|-------------|
| `direction` | Short o Long |
| `session` | London / LondonNyOverlap / NewYork |
| `session_phase` | OpeningRush / Open / Mid / Close |
| `entry_price` | Precio de cierre de la barra de breakout |
| `stop_price` | Stop (0.25% contra la entrada) |
| `target_price` | Target (0.5% SHORT / 0.45% LONG) |
| `rr` | Risk:Reward ≈ 2:1 |
| `range_high` | Techo del rango roto |
| `range_low` | Piso del rango roto |
| `range_pct` | Tamaño del rango como % del precio |
| `range_bars` | Duración del rango en barras M1 |
| `cvd_in_range` | CVD acumulado durante el rango |
| `vr_at_breakout` | Volumen relativo en la barra de ruptura |
| `macro_regime` | Bull / Bear / BullPullback / BearPullback |
| `range_touch_count` | Veces que el precio tocó el nivel roto antes de romper |
| `price_vs_vwap_pct` | Distancia precio-VWAP en % |
| `funding_at_entry` | Funding rate en la entrada |
| `liq_ratio_pre` | Ratio de liquidaciones previo al breakout |
| `result_r` | R múltiple al cerrar (se rellena después) |
| `exit_reason` | TARGET / STOP / TRAILING_STOP / TIME_STOP / SESSION_END |
| `is_pre_breakout` | true si la señal fue modo entrada anticipada |
| `bars_held` | barras M1 que duró el trade (para calibrar time stop pre-breakout) |

### En `scalping_bars` (contexto por barra M1)

Cada minuto se graba: `close`, `regime`, `dz`, `vr`, `cvd_session`, `cvd_slope`, `obi_fast`, `obi_slow`, `absorption_long`, `absorption_short`, `liq_ratio`, `blocked_by`, etc.

Esto permite reconstruir el contexto completo de cualquier señal juntando `rbf_signals` con las barras alrededor del `timestamp_ms`.

---

## Resultados live (2026-06-02 → 2026-06-11)

**72 trades en 5 símbolos** (pre-calibración v3) — resumen por sesión:

| Sesión | n | WR | PnL |
|--------|---|----|-----|
| London | 45 | 33% | +0.65R |
| LondonNyOverlap | 22 | 50% | +6.47R |
| NewYork | 5 | 60% | +2.57R |

**Por símbolo:**

| Símbolo | n | WR | PnL |
|---------|----|-----|-----|
| BTCUSDT | 32 | 41% | +6.37R |
| SOLUSDT | 11 | 55% | +5.80R |
| BNBUSDT | 17 | 41% | +1.40R |
| ETHUSDT | 11 | 27% | −2.88R |
| XRPUSDT | 1  | 0%  | −1.00R |

**Insight clave (entry lag):** el detector dispara cuando el movimiento ya consumió 32–54% del
desplazamiento total. Las señales bearish de microestructura aparecen ~24 barras antes. Si se
entrara al inicio del move, el target potencial sería 4.30R vs 2.00R actual.

**Modo pre-breakout (activo desde 2026-06-10):** entra en `range_low` antes del VR≥3×.
Entry en `pct_done≈0%`, target=3R, gate=`oi_mom_bars_recent≤3`. Ver [RBF_REGLAS_ACTIVAS.md](RBF_REGLAS_ACTIVAS.md).

**Curva OBI vs WR (53 trades con resultado, 2026-06-11):**

| OBI en entrada | n | WR | avgR |
|---|---|---|---|
| < −0.50 | 13 | 54% | +0.43 |
| −0.50 a −0.20 | 3 | 67% | +0.93 |
| −0.20 a −0.05 | 4 | 50% | +0.33 |
| −0.05 a 0 | 4 | 25% | −0.58 |
| 0 a +0.20 | 6 | 25% | −0.49 |
| +0.20 a +0.60 | 8 | 25% | −0.19 |
| > +0.60 | 10 | 40% | +0.24 |

Zona óptima actual: OBI < −0.20 (WR=60%+, avgR=+0.6R+). Los flags `obi_multi_depth` y `obi_intrabar_mean` acumulan evidencia para refinar este gate.

**Primer día (2026-06-02):**

| Señal | Dir | Contexto | VR | Resultado | R |
|-------|-----|----------|----|-----------|---|
| 08:48 | Short | TrendDown, 1 conviction event | 3.22× | ✅ Target | +2.1R |
| 10:11 | Long | Rebote contra Bear macro | 2.85× | ❌ Stop | −1.1R |
| 12:18 | Short | CVD slope −38 sostenido | 3.95× | ✅ Target | +2.2R |
| 14:01 | Short | CVD sesión −854, VR 3.29× | 3.29× | ✅ Target | +2.0R |

---

## Umbrales de decisión

| Decisión | Condición |
|----------|-----------|
| Primera lectura de edge real | 30+ señales con resultado cerrado |
| Análisis por sesión | 15+ señales por sesión |
| Análisis por régimen | 20+ señales por régimen |
| Ajuste de parámetros | 100+ señales |
| Decisión de escalar | Win rate sostenido > 55% en 50+ señales |

---

## Archivos del sistema

| Archivo | Descripción |
|---------|-------------|
| `data/src/strategy/detectors/range_breakout_flow.rs` | Detector: RbfGateContext (11 campos), ConfluenceFlag (11 variantes), score_confluence, gates |
| `data/src/strategy/detectors/rbf_paper.rs` | Paper trader: trailing direction-aware (1.75/1.5), time stop 30 barras |
| `config/strategy.toml` → `[range_breakout]` | Parámetros: stop_pct, target_short_pct, target_long_pct, min_rr |
| `data/src/strategy/config_file.rs` | Parser del toml — RangeBreakoutSection |
| `crates/monitor/src/main.rs` | Wiring: regime_hist_25, obi_intrabar Vec (10s), expansion_bars_recent, per-symbol config |
| `crates/monitor/src/supabase_writer.rs` | write_rbf_signal(), write_rbf_bar() (con obi_l10/l20), write_obi_batch() |
| `src/chart/kline.rs` | Overlay visual en la UI local |
| `supabase/migration_rbf.sql` | Tabla rbf_signals + índices + vista v_rbf_summary |
| `supabase/migration_rbf_v2.sql` | ALTER TABLE: 5 campos de contexto adicionales |
| `docs/rbf/RBF_CALIBRACION_POR_ACTIVO.md` | Análisis per-símbolo: 72 trades, microestructura, filtros por símbolo |
| `docs/rbf/RBF_REGLAS_ACTIVAS.md` | Referencia única: todas las reglas vigentes (post + pre breakout + OBI gates) |
| `scripts/rbf_entry_autopsy.py` | Análisis barra-a-barra de trades live: MAE, MFE, pre_cvd_5b, death classification |
| `scripts/rbf_microstructure_analysis.py` | Análisis microestructura por símbolo: OBI, cum_delta, expansion segmentado |

**SQL para Supabase (ejecutar en Dashboard si no está hecho):**
```sql
-- obi_l10/l20 en tablas de barras
ALTER TABLE btc_bars ADD COLUMN IF NOT EXISTS obi_l10 FLOAT;
ALTER TABLE btc_bars ADD COLUMN IF NOT EXISTS obi_l20 FLOAT;
-- (igual para eth_bars, bnb_bars, sol_bars, xrp_bars)

-- Tabla nueva muestras OBI 10s
CREATE TABLE IF NOT EXISTS obi_10s (
    ts_ms BIGINT NOT NULL, symbol TEXT NOT NULL,
    obi_l5 FLOAT, obi_l10 FLOAT, obi_l20 FLOAT, spread_bps FLOAT,
    PRIMARY KEY (ts_ms, symbol)
);
```

---

## Queries útiles de Supabase

```sql
-- Resumen por sesión y régimen
SELECT * FROM v_rbf_summary;

-- Señales de las últimas 24h con todos los campos
SELECT timestamp_ms, direction, session, session_phase, macro_regime,
       range_pct, range_bars, cvd_in_range, vr_at_breakout,
       range_touch_count, price_vs_vwap_pct, funding_at_entry,
       result_r, exit_reason
FROM rbf_signals
WHERE timestamp_ms > extract(epoch from now()-interval '24 hours')*1000
ORDER BY timestamp_ms DESC;

-- Win rate por sesión
SELECT session, direction,
       COUNT(*) FILTER (WHERE result_r >= 1.0) AS wins,
       COUNT(*) FILTER (WHERE result_r < 0)    AS losses,
       COUNT(*)                                 AS total,
       ROUND(AVG(result_r)::NUMERIC, 3)         AS avg_r
FROM rbf_signals
WHERE exit_reason IS NOT NULL
GROUP BY session, direction
ORDER BY session;

-- Mejores setups: touches altos vs bajos
SELECT range_touch_count,
       COUNT(*) AS n,
       ROUND(AVG(result_r)::NUMERIC, 3) AS avg_r,
       COUNT(*) FILTER (WHERE result_r >= 1.0) * 100 / COUNT(*) AS win_pct
FROM rbf_signals
WHERE exit_reason IS NOT NULL AND range_touch_count IS NOT NULL
GROUP BY range_touch_count
ORDER BY range_touch_count;
```
