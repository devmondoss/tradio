# INVENTARIO COMPLETO DE FEATURES — BTCUSDT Perp (dataset rico, 365 días)

> Estado 2026-06-20. Esquemas **extraídos de los archivos reales**, no de memoria. Cobertura:
> 2025-06-19 → 2026-06-18 (365 días). Todo en `data/bybit-perp/`. Split de validación:
> **IS** = entrenamiento (< 2026-03-01), **OOS** = validación honesta (≥ 2026-03-01, ~3.5 meses).
>
> ⚠️ **Causalidad:** todas las features están corregidas a causales (sin lookahead) tras el fix del
> 2026-06-19. Dos features nacieron de un lookahead y hay que tratarlas con cuidado (ver §6).

---

## 0. Mapa de capas de datos

| Capa | Archivo | Granularidad | Filas/cobertura | Tamaño |
|---|---|---|---|---|
| **Tick-a-tick** | `raw_trades/YYYY-MM-DD.parquet` (365) | por trade | ~0.8-1.8M/día | 2.3 GB |
| **Orderbook 1s** | `ob_1s/YYYY-MM-DD.parquet` (365) | 1 segundo | 86.400/día | 1.8 GB |
| **Open Interest** | `oi_5m.parquet` | 5 min | 104.832 | 1.6 MB |
| **Funding** | `funding.parquet` | 8 h | 1.092 | 20 KB |
| **M1 fusionado** | `processed/btcusdt_perp_m1.parquet` | 1 min | 524.160 | 200 MB |
| **L2 crudo (ob500)** | `_ob_tmp/*_ob500.data.zip` | 100 ms (snapshot+delta) | **solo 6 días** (2025-07-20→25) | 1.6 GB |

Derivados de research: `events.parquet` (2.76M eventos micro), `_ml_dataset.parquet`,
`_mine_dataset.parquet` (triple-barrera), `backtest/htf/htf_{5m,15m,1h,4h,1d}_full.parquet`.

---

## 1. CAPA TICK-A-TICK — `raw_trades/` (5 columnas, materia prima)
| Columna | Tipo | Qué es |
|---|---|---|
| `ts_ms` | int64 | timestamp del trade (ms) |
| `price` | double | precio de ejecución |
| `size` | float | tamaño (BTC) |
| `side` | string | `Buy`/`Sell` = lado **agresor** (taker) → base del delta |
| `tick_dir` | string | `PlusTick`/`MinusTick`/`ZeroPlusTick`/`ZeroMinusTick` (uptick/downtick) |

De aquí se derivan TODOS los agregados de flujo: delta, CVD, big trades, footprint, plus/minus ticks.

---

## 2. CAPA ORDERBOOK 1s — `ob_1s/` (14 columnas)
| Columna | Tipo | Qué es |
|---|---|---|
| `ts_ms` | int64 | timestamp (1 por segundo) |
| `n_upd` | int64 | nº de updates del libro en ese segundo (pace) |
| `bb` / `ba` | double | best bid / best ask |
| `mid` | double | (bb+ba)/2 |
| `microprice` | double | precio ponderado por tamaño top-of-book (presión de tope de libro) |
| `spread_bps` | double | spread en bps |
| `bid_sz1` / `ask_sz1` | double | tamaño en el mejor bid / ask (L1) |
| `obi5` / `obi10` / `obi25` | double | **Order Book Imbalance** a 5/10/25 niveles: (bid−ask)/(bid+ask) ∈ [−1,+1] |
| `depth_bid25` / `depth_ask25` | double | profundidad total acumulada 25 niveles (bid / ask) |

⚠️ NO tenemos heatmap DOM completo del año (solo top-of-book + OBI/depth agregados). El L2 crudo
por-evento (ob500) solo existe para 6 días.

---

## 3. DERIVADOS DE MERCADO
| Archivo | Columna | Qué es |
|---|---|---|
| `oi_5m.parquet` | `open_interest` | OI total (posiciones abiertas), cada 5 min |
| `funding.parquet` | `funding_rate` | tasa de funding, cada 8 h |

⚠️ **NO tenemos** feed histórico de **liquidaciones** (Bybit no lo publica; v5 es realtime-only).
Proxy fiel = caída de OI + burst agresivo (ya usado en S3/S5).

---

## 4. CAPA M1 FUSIONADA — `processed/btcusdt_perp_m1.parquet` (114 columnas)
La capa de trabajo: 1 fila por minuto, fusiona ticks+OB+derivados+estructura+ICT. Por categoría:

### 4.1 OHLCV base (6)
`open` `high` `low` `close` `volume` `n_trades`

### 4.2 Flujo agresivo / Delta / CVD (15)
| Columna | Qué es |
|---|---|
| `buy_vol` / `sell_vol` | volumen agresor comprador / vendedor |
| `delta` | buy_vol − sell_vol (presión neta de la vela) |
| `cvd` | Cumulative Volume Delta (delta acumulado) |
| `dz` | **Delta Z-score** (delta normalizado vs media móvil) — intensidad |
| `vr` | **Volume Ratio** (volumen / SMA) — expansión de volumen |
| `cvd_slope` | pendiente del CVD (tendencia de presión) |
| `cvd_div` | flag de divergencia CVD-precio |
| `cvd_consec_neg` / `cvd_consec_pos` | nº de barras consecutivas de CVD en una dirección |
| `prev_bar_delta` | delta de la barra anterior |
| `max_trade` | mayor trade de la vela (proxy big-trade) |
| `plus_ticks` / `minus_ticks` | nº de upticks / downticks |
| `vpin` | Volume-synced Probability of Informed Trading ⚠️ (ver §6) |

### 4.3 Orderbook agregado a M1 (16)
`obi5_mean` `obi10_mean` `obi20_mean` `obi10_min` `obi10_max` `obi_range` `spread_mean`
`mid_open` `mid_close` `n_snapshots` `near5_ask` `near5_bid` `max_ask5` `max_bid5`
`thin_above` `thin_below` — y muros: `ask_wall` `bid_wall`

### 4.4 Footprint (intra-vela por nivel de precio) (14)
| Columna | Qué es |
|---|---|
| `fp_poc` | Point of Control de la vela (nivel de mayor volumen) |
| `fp_n_levels` | nº de niveles de precio en la vela |
| `fp_buy_imb` / `fp_sell_imb` | nº de imbalances diagonales compradores / vendedores |
| `fp_stack_buy` / `fp_stack_sell` | imbalances apilados (≥3 consecutivos) |
| `fp_unfinished_hi` / `fp_unfinished_lo` | unfinished auction en extremo alto / bajo |
| `fp_delta_top` / `fp_delta_bot` | delta en el tercio superior / inferior de la vela |
| `fp_sell_dom` | dominancia vendedora |
| `fp_absorb_sell` / `fp_absorb_buy` | absorción de venta / compra detectada |
| `fp_result_sell` | resultado post-absorción venta ⚠️ (ver §6) |

### 4.5 Absorción / Big trades (4)
`abs_ask` `abs_bid` `big_trade_bearish` `big_trade_bullish`

### 4.6 Volume Profile (7)
`vp_poc` `vp_vah` `vp_val` (POC / Value Area High / Low) · `vp_lvn_below` (Low Volume Node abajo)
· `above_poc` `val_near` `body_below_poc`

### 4.7 Estructura, niveles y liquidez (18)
`asian_high` `asian_low` `prev_day_high` `prev_day_low` `weekly_high` `weekly_low`
`swing_high_50` `swing_low_50` `equal_high` `equal_low`
`near_weekly_high` `near_asian_high` `near_pdh` `pdh_sweep` `equal_high_sweep`
`sweep_confirmed` `london_sweep_h` `tight_range`

### 4.8 Patrones ICT (11)
`fib_ote` `fib_ote_london` `ote_62` `ote_79` `ote_rejection`
`bearish_fvg_active` `near_bearish_fvg` `near_bearish_ob` `displacement_bear`
`body_below_vwap` `stacked_imb`

### 4.9 Indicadores / contexto (5)
`vwap` `ema20` `atr14` `session` (Asia/London/NY/…) `regime` (Chop/Expansion/TrendUp/TrendDown)
· `bars_since_low_vr`

### 4.10 Multi-timeframe H1/H4 (14) ⚠️ corregidas de lookahead (§6)
**H4:** `h4_ema20` `h4_bearish` `h4_ob_zone` `h4_fvg_zone` `h4_bos_bear`
**H1:** `h1_ema20` `h1_bearish` `h1_bos_bear` `h1_choch_bear` `h1_ob_bear` `h1_fvg_bear`
`h1_bos_bull` `h1_choch_bull` `h1_ob_bull`

---

## 5. DATASETS DERIVADOS (research)
| Archivo | Qué es | Builder |
|---|---|---|
| `events.parquet` | 2.76M eventos micro sub-minuto (sweep, absorción, big-trade, obi-flip…) + retorno fwd 1/5/15m | `build_events.py` |
| `_ml_dataset.parquet` | 2.07M filas, 23 features causales 1s + retorno fwd 5/15m | `_ml_build.py` |
| `_mine_dataset.parquet` | etiqueta **triple-barrera** (target/stop) + 28 features causales | `_mine_build.py` |
| `backtest/htf/htf_*.parquet` | features HTF causales por marco (5m/15m/1h/4h/1d): obi_twa, depth_imb, micro_prem_bps, delta, taker_buy_frac… | `build_htf_multi.py` |

---

## 6. ⚠️ CAVEATS (leer antes de usar)
- **`vpin` está MUERTO causal:** era artefacto del lookahead H1/H4. Flipea de signo IS↔OOS. No usar como señal.
- **`fp_result_sell` / `fp_sell_dom`:** parecían robustos pero estaban **overfit a otro motor** (mtf_v2);
  en el motor de despliegue dan OOS negativo. Tratar con escepticismo.
- **Features H1/H4:** ANTES tenían lookahead (mapeaban el cierre de la hora en curso a cada minuto →
  inflaban el edge a $74.9K falso). **Ya corregidas a causal** (`compute_spot_features.py` ~líneas 887/1010);
  parquet regenerado, backup en `...parquet.lookahead.bak`. Ahora son honestas pero el edge desapareció.
- **OBI/microprice agregados a M1 licúan la microestructura** (un sweep dura ~15s). Para señal micro real,
  usar la capa `ob_1s` / `raw_trades` directamente, no el agregado M1.
- **Liquidaciones y heatmap DOM completo: NO existen** en el dataset (ver §2, §3).

---

## 7. Qué se probó con estas features (resumen de veredictos)
Ver [EDGE_VERDICT_2026-06-19.md](EDGE_VERDICT_2026-06-19.md) y [STRATEGY_SET.md](STRATEGY_SET.md).
TL;DR: ninguna feature/combinación de orderflow da predicción direccional robusta OOS por encima del
fee (11 bps taker). El único signal OOS real es momentum-VWAP débil (`dist_vwap`), sub-fee.
