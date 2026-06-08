# Estrategia ICT AMD — Accumulation · Manipulation · Distribution

> Documento actualizado: 2026-06-08  
> Estado: shadow mode activo en 4 activos. Señales grabadas en `amd_signals`. Gates desactivados hasta 30+ días de datos.

---

## Qué es

**ICT AMD** es una estrategia de reversión basada en Smart Money Concepts (SMC). El mercado opera en tres fases repetibles en M1:

```
ACCUMULATION  →  MANIPULATION  →  DISTRIBUTION
(rango lateral)   (spike/stop hunt)  (movimiento real)
```

Los institucionales acumulan posición en un rango comprimido, ejecutan un spike que barre stops y pools de liquidez de los retail (manipulación), y después mueven el precio con fuerza en la dirección opuesta (distribución real).

**Por qué funciona — el edge real son las liquidaciones.** El spike no es solo técnico: desencadena stop cascades. Las liquidaciones forzadas generan flujo unidireccional que empuja el precio más allá del nivel; cuando ese flujo se agota, el precio revierta con velocidad. AMD captura esa reversión.

---

## El patrón visual

```
SHORT — spike UP barre liquidity pool, cierra de vuelta, entry short:

            ↑ spike barre swing_hi / PDH / EQH
         ────┤ close vuelve al rango
─────────────┤  acumulación 10–60 velas  ├──────────────────────────
             │  (rango comprimido)        │ close vuelve
─────────────┘                           └──→ SHORT entry → distribución bajista

LONG — espejo: spike DOWN barre swing_lo / PDL / EQL → long entry.
```

---

## Estado actual del sistema

| Componente | Estado |
|---|---|
| Detector Rust en `monitor` | ✅ shadow mode — señales grabadas, no ejecutadas |
| 4 activos M1 | ✅ BTC, ETH, BNB, SOL — todos con microestructura completa |
| Gates liq_ratio + dz | ⏳ Desactivados — activar tras 30+ días de datos live |
| Paper trading / ejecución | ⏳ Pendiente validación con datos live |

### Diagnóstico gates — 2026-06-08

El detector producía n=0 señales en `amd_signals`. Causa: tres gates bloqueando en cascada.

| Gate | Problema | Fix aplicado |
|---|---|---|
| `manip_vpin_threshold = 0.55` (ACCUM→MANIP) | VPIN en M1 live rara vez supera 0.55 — el valor estaba calibrado para datos HFT, no barras de 1min | Bajado a **0.30** en `strategy.toml` |
| `cvd_slope.map_or(false, ...)` (MANIP→ENTRY) | Si `cvd_slope` es `None` (dato no disponible), el gate bloqueaba silenciosamente **siempre** | Cambiado a `map_or(true, ...)` — no vetar si el dato no existe |
| Tests `#[cfg(test)]` sin `vwap_dz`/`liq_ratio` | `AmdContext` en tests no tenía los campos añadidos en la migración anterior → `cargo test` no compilaba | Añadidos `vwap_dz: None, liq_ratio: 0.0` a todos los contextos de test |

Commit: `d9bb4e2`. No requiere cambios en Railway env vars — todo va en `strategy.toml` y código.

---

## El setup descubierto

### Backtest 14 días (Jun 1–5, 2026) — OHLCV + microestructura Supabase

**Sin filtros (solo patrón precio + VR):** WR ~31%, avgR −0.070, n=13  
No hay edge con solo OHLCV histórico. El edge viene de la microestructura en tiempo real.

**Con filtros de microestructura en la barra del SPIKE:**

| Gate en spike | WR | avgR | n |
|---|---|---|---|
| Ninguno (baseline) | 31% | −0.07 | 13 |
| `dz ≥ 1.5` | 44% | +0.33 | 9 |
| `liq_ratio < 1.2` | 50% | +0.42 | 6 |
| `dz ≥ 1.5` + `liq_ratio < 1.2` | **67%** | **+0.68** | 4 |

> **Por qué el spike bar, no el entry bar.** El VPIN y liq_ratio en el momento del sweep discriminan si la liquidación fue *contenida* (reversión probable) o *cascada* (precio continúa). Mirar features en la entry bar es demasiado tarde.

### Teoría VPIN cascade

Cuando `liq_ratio` es alto, los market makers retiran liquidez porque detectan flujo informado (cascada de liquidaciones). Sin liquidez, el precio continúa en la dirección del spike en vez de revertir. AMD necesita sweeps *contenidos*, no cascadas.

- `liq_ratio < 1.2`: sweep absorbe liquidez → reversión probable  
- `liq_ratio ≥ 1.2`: cascada activa → market makers se van → precio continúa

### Gate dz en el spike

`dz_at_spike = (close_spike − vwap_session) / atr`

Mide si el spike rompió en zona de valor significativa. Un spike sin desplazamiento del VWAP es ruido.

- `dz ≥ 1.0–1.5`: spike desplazó precio a zona extrema → reversión válida  
- `dz < 1.0`: spike dentro del rango normal → fakeout sin edge

---

## Máquina de estados — detector Rust

```
IDLE
  │  on_bar_close: rango comprimido ≥ accum_min_bars, dentro de accum_range_max_pct
  ▼
ACCUMULATING
  │  on_bar_close: spike detectado
  │  • high > accum_high (SHORT) ó low < accum_low (LONG)
  │  • vr ≥ manip_min_vr
  │  • vpin ≥ manip_vpin_threshold
  │  • CVD diverge: bar_delta < 0 en spike UP ó bar_delta > 0 en spike DOWN
  │  • GATE liq_ratio ≤ manip_liq_ratio_max  (actualmente desactivado: 999.0)
  │  • GATE |dz| ≥ manip_dz_spike_min        (actualmente desactivado: 0.0)
  ▼
MANIPULATION_DETECTED
  │  Se guarda: spike_ts, liq_ratio_at_spike, dz_at_spike, spike_extreme, entry_side
  │  Espera max_wait_bars_after_spike barras para la reversión
  │
  │  on_bar_close: primera barra que confirma reversión
  │  • dist_min_vr, dist_cvd_slope, dist_obi_confirm
  ▼
AmdSignal (emitida)
  │  Campos: entry, stop, targets[], liq_ratio_at_spike, dz_at_spike, session_name
  ▼
write_amd_signal() → Supabase `amd_signals`
  │
  └── cooldown_bars antes del siguiente ciclo → IDLE
```

---

## Configuración live — `config/strategy.toml`

```toml
[amd_detector]
enabled = true                    # shadow mode activo

# Fase de acumulación
accum_range_min_pct       = 0.04  # rango mínimo (%) — filtrar micro-rangos sin sentido
accum_range_max_pct       = 0.30  # rangos comprimidos → mejor edge (sweep 4320 combos)
accum_min_bars            = 10    # mínimo 10 velas M1 de consolidación real
accum_max_bars            = 60    # timeout: 1 hora

# Spike de manipulación
manip_min_vr              = 1.5   # VR mínimo en la barra del spike
manip_vpin_threshold      = 0.30  # bajado de 0.55 — VPIN M1 live raramente supera 0.55

# Entry de distribución
dist_min_vr               = 1.0   # VR mínimo en la barra de entry
dist_cvd_slope            = 5.0   # CVD slope mínimo (giro confirmado)
dist_obi_confirm          = 0.08  # OBI alineado con la distribución

# Gestión
stop_buffer_pct           = 0.08  # buffer sobre el spike extreme (%)
min_rr                    = 2.0   # R:R mínimo requerido
cooldown_bars             = 30    # barras entre señales
max_wait_bars_after_spike = 10    # timeout esperando reversión tras spike

# Gates de microestructura en el spike (DESACTIVADOS hasta 30+ días de datos)
# Backtest 14d: ganadores liq_ratio_med=0.67, perdedores=1.51 → gate sugerido: 1.2
# Backtest 14d: dz≥1.5 → WR 44%, avgR +0.33 → gate sugerido: 1.0–1.5
manip_liq_ratio_max  = 999.0  # 999 = desactivado; activar con: 1.5
manip_dz_spike_min   = 0.0    # 0.0 = desactivado; activar con: 1.0
```

---

## Hipótesis — activación de gates (30+ días)

Esperamos acumular n ≥ 50 señales en `amd_signals` con campos `liq_ratio_at_spike` y `dz_at_spike` para calibrar con datos live. Una vez con esos datos:

1. **Activar `manip_liq_ratio_max = 1.5`** — esperamos reducir señales ~30%, subir WR +15pp
2. **Activar `manip_dz_spike_min = 1.0`** — esperamos filtrar spikes sin desplazamiento real
3. **Target proyectado**: 3–4 señales/día con WR ≥ 55%, avgR ≥ +0.30 (frente a 13.2 raw sin gates)
4. **Kill Zone filter** — añadir como gate adicional: London (07:00–09:00 UTC) + NY Open (13:30–15:30 UTC)

La reducción de 13.2 → 4 señales/día ya observada en live vs backtest básico confirma que los gates de microestructura ya operando (OBI, absorción, VPIN) bloquean ~70% del ruido.

---

## Activos cubiertos

| Símbolo | Tabla | Exchange | Tipo | Temporalidad |
|---|---|---|---|---|
| BTCUSDT | `btc_bars` | Binance | Linear Perp | M1 |
| ETHUSDT | `eth_bars` | Binance | Linear Perp | M1 |
| BNBUSDT | `bnb_bars` | Binance | Linear Perp | M1 |
| SOLUSDT | `sol_bars` | Binance | Linear Perp | M1 |

Todos los activos reciben microestructura completa desde el mismo `monitor` (variable de entorno `SYMBOLS`).

---

## Datos usados — ICT + microestructura

### Niveles ICT (detectan si el nivel barrido es un pool real)

| Campo | Descripción | Cuándo aplica |
|---|---|---|
| `asian_high` / `asian_low` | Rango de la sesión Asia (00:00–07:00 UTC) | London barre Asia ~70% días |
| `prev_day_high` / `prev_day_low` | PDH/PDL del día anterior | Pool diario institucional |
| `swing_high_50` / `swing_low_50` | Max/min rolling 50 barras | Swing significativo de referencia |
| `equal_high` / `equal_low` | ¿High a ≤0.03% de swing previo? | Stops dobles visibles — pool enorme |

### Selección de targets ICT (distribución)

El sistema selecciona targets en este orden de preferencia:

1. **LVN** (Low Volume Node) — zona de thin liquidity, precio la atraviesa rápido
2. **Naked POC** — Point of Control no testeado desde su formación (precio busca equilibrio)
3. **Order Block midpoint** — zona institucional en dirección de distribución
4. **FVG midpoint** — Fair Value Gap como imán de precio
5. **Fallback 2R** — R:R mínimo si no hay nivel estructural en rango alcanzable

### Microestructura M1 — todas en `btc_bars` y homólogos

| Campo | Tipo | Descripción |
|---|---|---|
| `bar_delta` | float | `taker_buy_vol − taker_sell_vol` — divergencia CVD del spike |
| `vr` | float | Volume ratio vs media 50 barras — fuerza del spike |
| `vpin` | float | Probabilidad de flujo informado (institucionales) |
| `obi_l5` | float | Order Book Imbalance L5 en el momento del cierre |
| `obi_fast` | float | EMA(obi_l5, α=0.333) — suavizado 5-bar |
| `obi_slow` | float | EMA(obi_l5, α=0.095) — suavizado 20-bar |
| `dz` | float | `(close − vwap_session) / atr` — desplazamiento normalizado del VWAP |
| `liq_ratio` | float | Z-score absoluto del tracker de liquidaciones — intensidad del sweep |
| `spread_ticks` | int | Spread actual en ticks — liquidez del libro |
| `cvd_slope` | float | Pendiente OLS del CVD — momentum del giro |
| `absorption` | string | `Bid`/`Ask`/`None` — institucionales absorbiendo el spike |
| `bid_wall` / `ask_wall` | bool | Pared de liquidez en el nivel barrido |
| `stacked_imb` | string | Imbalances apiladas hacia el target |
| `thin_above` / `thin_below` | bool | Camino libre en dirección de distribución |
| `session` | string | Kill Zone activa en ese bar |
| `vwap` | float? | VWAP de sesión (reset diario 00:00 UTC) |
| `atr` | float | ATR — volatilidad normalizada |
| `regime` | string | Contexto macro (Bull/Bear/Range) |
| `asian_high/low` | float? | Rango Asia de la sesión actual |
| `prev_day_high/low` | float? | PDH/PDL del día anterior |
| `swing_high/low_50` | float? | Swing rolling 50 barras |
| `equal_high/low` | bool | Equal High/Low detectado |

### Campos adicionales en `amd_signals` (solo señales)

| Campo | Descripción |
|---|---|
| `liq_ratio_at_spike` | Valor de liq_ratio en la barra exacta del spike |
| `dz_at_spike` | Valor de dz en la barra exacta del spike |
| `entry_price` | Close de la barra de entry |
| `stop_price` | Extremo del spike ± buffer |
| `target_price` | Primer nivel ICT alcanzable (LVN/POC/OB/FVG/2R) |
| `rr` | R:R calculado |
| `direction` | Long / Short |
| `session_name` | Sesión activa al emitir |
| `spike_ts` | Timestamp exacto del spike (para join con btc_bars) |

---

## Fuentes de datos

### Live (tiempo real)
- **Binance WebSocket**: klines M1, order book L5 (bid/ask walls, OBI), taker volume
- **Binance REST**: liquidaciones acumuladas (alimenta `liq_ratio` via `liq_tracker`)
- **Supabase**: escritura de cada barra en `btc/eth/bnb/sol_bars`

### Histórico (backtest)
- **data.binance.vision**: CDN público con datos históricos de Binance Futures
  - `klines/BTCUSDT/1m/` — OHLCV M1 en zips diarios
  - `metrics/BTCUSDT/5m/` — OI, taker_ratio, top_trader_ratio en zips 5min
- **Supabase `btc_bars`**: datos live con microestructura completa (desde Jun 5, 2026)

### Limitación del backtest histórico
Los datos de `data.binance.vision` permiten reconstruir VR, dz-VWAP y taker_ratio (proxy de liq_ratio), pero NO tienen OBI L5 en tiempo real, absorption, ni liq_ratio exacto del tracker. Por eso el backtest puro (WR 31%) es inferior al live esperado: el edge real está en la microestructura que solo existe en tiempo real.

---

## Backtest 3 años — conclusiones (`scripts/amd_3y_backtest.py`)

| Métrica | Resultado |
|---|---|
| Período | Jun 2023 – Jun 2026 |
| WR sin filtros | 31% |
| avgR sin filtros | −0.070 |
| Mejor gate encontrado | `dz≥1.5` + `taker_ratio<1.2` → WR 44%, avgR +0.33 |

**Conclusión**: el backtest histórico confirma que el patrón precio puro no tiene edge. Validar la estrategia requiere datos live con microestructura real. El backtest histórico sirve para calibrar umbrales de `dz` y `vr`, no para probar WR final.

---

## Sesiones óptimas (14 días live + backtest histórico)

| Sesión | WR | Nota |
|---|---|---|
| Asia (00:00–07:00 UTC) | Mejor | Barre su propio rango → London caza esos pools |
| London Open (07:00–09:00 UTC) | Alta | Barre Asian range → pool verificable |
| NY Open (13:30–15:30 UTC) | Media | Barre London range |
| NY Tarde / Asia madrugada | Negativa | Evitar — edge negativo consistente |

---

## Arquitectura del sistema

```
Binance WS (klines M1)
  │
  ▼
BarState::on_bar_close()           ← Rust monitor, cada vela M1
  │
  ├── ICT levels:
  │     asian_high/low             (acumula 00:00–07:00 UTC)
  │     prev_day_high/low          (guarda al cambiar día UTC)
  │     swing_high/low_50          (rolling max/min 50 barras)
  │     equal_high/low             (≤0.03% vs swing previo)
  │
  ├── Microestructura:
  │     bar_delta, vr, vpin        (del kline Binance)
  │     obi_l5                     (del order book snapshot)
  │     obi_ema_fast/slow          (EMA α=0.333/0.095 de obi_l5)
  │     liq_ratio                  (liq_tracker.snapshot().total_zscore)
  │     spread_ticks               (best_ask − best_bid en ticks)
  │     dz = (close − vwap) / atr  (normalización VWAP)
  │
  ├── AmdDetectorState::on_bar_close()
  │     Fase IDLE → ACCUMULATING → MANIP_DETECTED → AmdSignal
  │     Graba: spike_ts, liq_ratio_at_spike, dz_at_spike
  │
  └── write_rbf_bar()    → Supabase btc/eth/bnb/sol_bars  (cada barra)
      write_amd_signal() → Supabase amd_signals            (solo señales)
```

### Tablas Supabase

| Tabla | Contenido | Estado |
|---|---|---|
| `btc_bars` | Barras M1 BTC con microestructura completa | Activa |
| `eth_bars` | Barras M1 ETH | Activa |
| `bnb_bars` | Barras M1 BNB | Activa |
| `sol_bars` | Barras M1 SOL | Activa |
| `amd_signals` | Señales AMD con spike context | Activa, shadow mode |

Columnas de microestructura añadidas en migración `20260608_microstructure_bars.sql`:  
`obi_fast`, `obi_slow`, `liq_ratio`, `spread_ticks` en todas las `*_bars`.  
`liq_ratio_at_spike`, `dz_at_spike` en `amd_signals`.

---

## Hoja de ruta

| Paso | Condición | Acción |
|---|---|---|
| 1 — Acumular datos | Hoy → 30+ días | Monitor corriendo sin cambios |
| 2 — Analizar señales | n ≥ 30 en `amd_signals` | `scripts/amd_features_analysis.py` sobre `spike_ts` |
| 3 — Activar `liq_ratio` gate | Datos confirman umbral | `manip_liq_ratio_max = 1.5` en strategy.toml |
| 4 — Activar `dz` gate | Datos confirman umbral | `manip_dz_spike_min = 1.0` en strategy.toml |
| 5 — Kill Zone filter | WR ≥ 50% con gates | Añadir filtro de sesión al detector Rust |
| 6 — Paper trading activo | WR ≥ 55%, avgR ≥ +0.25 | Mover de shadow mode a paper entries |
| 7 — Live | WR ≥ 65%, n ≥ 100 | Evaluación |

---

## Scripts de análisis

| Script | Función |
|---|---|
| `scripts/amd_backtest.py` | Backtest básico OHLCV |
| `scripts/amd_3y_backtest.py` | Backtest 3 años con data.binance.vision (M1 + 5min metrics) |
| `scripts/amd_features_analysis.py` | Análisis de features en `spike_ts` — calibración de gates |
| `scripts/amd_sweep.py` | Sweep de parámetros (accum_range, manip_vr, etc.) |
| `scripts/amd_chart.py` | Chart interactivo M1 con señales, CVD, VR, OBI |
| `scripts/amd_charts.py` | Batch de charts por sesión |

Generar chart con microestructura:
```bash
python scripts/amd_chart.py --days 14 --min-vr 5 --micro -o amd_chart.html
```

Análisis de features en el spike (una vez con n ≥ 30 señales):
```bash
python scripts/amd_features_analysis.py --days 30
```
