# Estado presente y futuro — FlowSurface

Ultima revision: 2026-06-06

Este es el documento canonico para revisar el proyecto. Resume lo que esta vivo, lo que quedo obsoleto y que decisiones futuras dependen de datos.

---

## Estado presente

### Sistema activo: Range Breakout Flow (RBF)

Un solo motor activo en Railway corriendo sobre BTCUSDT M1.

**Hipotesis validada en backtest (30 dias M1, n=318 senales London+Overlap):**
Un rango de consolidacion (0.08–0.55% del precio, 15–60 barras M1) donde el CVD acumula presion en una direccion, seguido de un cierre fuera del rango con VR >= 2x, produce edge positivo en horizonte 30–60 min.

**Resultados reales acumulados (v1, 12 senales cerradas):**

| Segmento | n | Win rate | Avg R |
|----------|---|----------|-------|
| Total | 12 | 58% | +0.75R |
| Shorts | 8 | 87.5% | +1.5R |
| Longs | 4 | 0% | -1.0R |
| LondonNyOverlap | 4 | 100% | +2.0R |
| London | 7 | 43% | +0.3R |

**V2 activo (desde 2026-06-05):** sistema de confluencia con 7 flags y 4 vetos. En shadow mode (min_confluence_score=1). Primeras senales v2 registradas con confluence_score y flags.

---

## Arquitectura del detector RBF

### Gates base (requisitos duros — sin estos no hay senal)

| Gate | Valor | Razon |
|------|-------|-------|
| Sesion | London + LondonNyOverlap | NewYork: WR 24.7% excluido |
| Tamano rango | 0.08%–0.55% | Rango real, no ruido ni tendencia |
| Duracion rango | 15–60 barras M1 | Consolidacion genuina |
| CVD acumulado | Alineado con direccion | Presion en la direccion del breakout |
| VR en breakout | >= 2x promedio 50 barras | Volumen confirma la ruptura |
| dz | 0.5 <= dz <= 3.0 | Elimina planos y eventos climaticos |
| CVD slope | Confirma direccion | Pendiente del CVD alineada |
| Cooldown | 60 barras M1 | Una senal por hora maximo |

### Sistema de confluencia v2 (score 0–7)

Cada flag que se cumple suma +1. La senal dispara si score >= min_confluence_score.

| Flag | Condicion SHORT | Condicion LONG |
|------|----------------|----------------|
| cvd_slope | slope < -15 | slope > +15 |
| obi | OBI L5 < -0.15 | OBI L5 > +0.15 |
| stacked_imbalance | Imbalance bajista en ultimas 3 barras | Idem alcista |
| absorption | Absorcion footprint bajista | Idem alcista |
| lvn_thin | LVN cerca del nivel roto o thin zone en dir. | Idem |
| vwap_bias | Precio bajo VWAP de sesion | Precio sobre VWAP |
| oi_momentum | OI expandiendose + precio bajando | Idem subiendo |

### Vetos duros (bloquean independientemente del score)

| Veto | Condicion | Estado |
|------|-----------|--------|
| hvn_target | HVN en el 50% del camino al target | Activo |
| vpin_toxic | VPIN > 0.65 | Activo |
| long_bear_low_score | Long en Bear/BearPullback con score < 5 | Activo |
| wall_target | Pared entre entry y target | **DESACTIVADO** — 1xATR era demasiado agresivo (vetaba 100% de senales). Pendiente calibracion con datos reales. |

### Configuracion actual (strategy.toml)

```toml
[range_breakout]
enabled          = true
stop_pct         = 0.25        # 0.25% del precio
target_short_pct = 0.50        # SHORT target: RR 2:1
target_long_pct  = 0.45        # LONG target: RR 1.8:1
min_rr           = 1.5
cvd_slope_gate   = true
dz_min           = 0.5
dz_max           = 3.0
min_confluence_score = 1       # shadow mode
cvd_slope_threshold  = 15.0
bear_long_min_score  = 5
```

---

## Base de datos — Supabase

### Tablas activas

| Tabla | Descripcion | Filas aprox |
|-------|-------------|-------------|
| `btc_bars` | OHLCV + microestructura BTC M1 (desde 2026-06-05) | creciendo |
| `eth_bars` | Idem ETH (pendiente deploy) | 0 |
| `bnb_bars` | Idem BNB (pendiente deploy) | 0 |
| `sol_bars` | Idem SOL (pendiente deploy) | 0 |
| `rbf_signals` | Senales RBF de todos los simbolos, campo symbol | 19 |
| `regime_history` | Historial de cambios de regimen | varios |

### Estructura de btc_bars (y sus equivalentes por simbolo)

Cada fila = 1 barra M1. Captura todo lo necesario para backtest de RBF v2.

**OHLCV:** open, high, low, close, volume, bar_delta
**Microestructura:** cvd_slope, obi_l5, dz, vr, stacked_imb, absorption, thin_above, thin_below, bid_wall, ask_wall, vpin, oi_momentum, vwap
**Contexto:** session, regime, atr, operative (London/Overlap = true)

### Tablas eliminadas (2026-06-06)

- scalping_signals, scalping_trades, scalping_bars — sistema S1/S2/S3 desactivado
- micro_windows — sistema DRR obsoleto
- lab_signals — Subdimi parallel obsoleto

### Datos historicos

scalping_bars (Jun 1-5, 5,599 filas) tiene microestructura parcial sin OHLCV. Candidata a backfill: fetchear OHLCV de Binance + merge por ts_ms para insertar en btc_bars e extender el historico.

---

## Sistemas desactivados

### S1/S2/S3 Scalping (desactivado 2026-06-05)

- **S1 OBI Maker:** 45 trades, 49% WR, avg_r=+0.07R — edge marginal
- **S2 Absorption:** nunca disparo (umbrales muy estrictos o condiciones no presentadas)
- **S3 CVD Divergence:** nunca disparo
- Desactivado: `scalping.enabled = false` en strategy.toml

### DRR — Delta Range Reversal (desactivado antes de Jun 2026)

Sistema previo basado en M5. Reemplazado por RBF.

---

## Multi-simbolo (pendiente deploy)

Arquitectura: una instancia Railway por simbolo, misma imagen Docker, distinto env var SYMBOL.

| Servicio Railway | SYMBOL | Tabla destino |
|-----------------|--------|---------------|
| monitor-btc (activo) | BTCUSDT | btc_bars |
| monitor-eth (pendiente) | ETHUSDT | eth_bars |
| monitor-bnb (pendiente) | BNBUSDT | bnb_bars |
| monitor-sol (pendiente) | SOLUSDT | sol_bars |

rbf_signals es compartida — todas las instancias escriben ahi con su campo symbol.

---

## Proximos pasos

| Prioridad | Accion | Condicion |
|-----------|--------|-----------|
| 1 | Correr migration_cleanup_and_multisymbol.sql en Supabase | Inmediato |
| 2 | Backfill btc_bars con datos Jun 1-5 (Binance OHLCV + scalping_bars merge) | Inmediato |
| 3 | Deploy ETH/BNB/SOL en Railway | Despues de migracion |
| 4 | Primera evaluacion de RBF v2 | Con 50+ senales cerradas con confluence_score |
| 5 | Calibrar wall_target veto con datos reales | Con 50+ senales |
| 6 | Subir min_confluence_score a 3 | Cuando datos confirmen gradiente score->avg_r |

---

## Checklist para futuras revisiones

- Los datos de microestructura que Binance no provee (obi_l5, cvd_slope, stacked_imb, absorption, vpin) solo estan disponibles desde el momento en que el monitor corre.
- Para backtests usar siempre btc_bars (o equivalente por simbolo) — tiene OHLCV + microestructura juntos.
- No evaluar estrategia con menos de 30 senales cerradas.
- Medir por expectancy (wins x target + losses x stop) / n, no solo win rate.

---

*Actualizado 2026-06-06 — sistema RBF v2 activo, multi-simbolo pendiente deploy*
