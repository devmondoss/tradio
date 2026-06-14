# RBF Feature Matrix

*Última actualización: 2026-06-12*

Contrato de features para análisis, backtesting y modelado de RBF. La regla principal: no mezclar filas de backfill OHLCV con filas live de microestructura sin segmentar por disponibilidad de datos.

---

## Entidades

| Entidad | Tabla | Grano | Uso |
|---------|-------|-------|-----|
| Barra M1 | `btc_bars`, `eth_bars`, `bnb_bars`, `sol_bars`, `xrp_bars` | 1 símbolo x 1 minuto | Feature store temporal |
| Señal RBF | `rbf_signals` | 1 señal | Decisión + outcome |
| OBI intrabar | `obi_10s` | 1 símbolo x ~10 segundos | Presión intrabar |

---

## Señales (`rbf_signals`)

| Feature | Tipo | Origen | Estado | Nota |
|---------|------|--------|--------|------|
| `symbol` | text | monitor | activa | Join con tabla `*_bars` |
| `timestamp_ms` | bigint | monitor | activa | Timestamp de entrada |
| `direction` | text | detector | activa | Live actual opera Shorts |
| `session` | text | session tracker | activa | London, LondonNyOverlap, NewYork |
| `is_pre_breakout` | bool | detector | activa | Segmentación obligatoria |
| `entry_price` | float | detector | activa | Close de señal |
| `stop_price` | float | detector | activa | Estructural por rango |
| `target_price` | float | detector | activa | 2R post, 3R pre |
| `rr` | float | detector | activa | Risk/reward teórico |
| `range_high` | float | detector | activa | Techo del rango |
| `range_low` | float | detector | activa | Piso del rango |
| `range_pct` | float | detector | activa | 0.08%-0.55% válido |
| `range_bars` | int | detector | activa | 15/20/30/45/60 |
| `cvd_in_range` | float | detector | activa | Hipótesis base |
| `vr_at_breakout` | float | detector | activa | En pre-breakout puede ser <3 |
| `macro_regime` | text | detector | activa | EMA macro interna |
| `session_phase` | text | session tracker | activa | Útil para segmentar |
| `price_vs_vwap_pct` | float | monitor/detector | shadow | Gate desactivado, feature útil |
| `funding_at_entry` | float | exchange REST/WS | shadow | Contexto |
| `liq_ratio_pre` | float | institutional context | shadow | Cascadas/fakeouts |
| `cvd_slope_at_entry` | float | bars/monitor | shadow | No usar como flag direccional sin validar |
| `dz_at_entry` | float | bars/monitor | activa | Gate hard: `dz_min`/`dz_max` |
| `obi_at_entry` | float | orderbook | activa/shadow | Usado por `ObiTrap`, no OBI alineado |
| `confluence_score` | smallint | detector | activa | 0-6 |
| `confluence_flags` | text[] | detector | activa | Flags activos |
| `veto_reason` | text | detector | activa | Filtrar tradeable vs vetada |
| `absorption_score` | float | detector | shadow | Score continuo |
| `bar_displacement` | float | detector | shadow | Calidad de vela |
| `oi_delta_pct` | float | OI history | activa | Veto `oi_covering` |
| `cvd_divergence_bars` | int | monitor | shadow | Persistencia divergente |
| `vr_tier` | int | detector | shadow | 1/2/3 |
| `range_touch_symmetry` | float | detector | shadow | Calidad de rango |
| `cvd_per_bar` | float | detector | shadow | Densidad de presión |
| `breakout_extension_pct` | float | detector | shadow | Gate desactivado |
| `htf_h1_trend` | text | EMA-60M1 | activa/shadow | Renombrado desde H4 |
| `htf_h1_aligned` | bool | EMA-60M1 | activa/shadow | Usar este nombre en scripts |
| `vp_open_bias` | text | VP tracker | shadow | Tipo de apertura |
| `signal_score_v2` | float | detector | experimental | 0-1, validación pendiente |
| `sizing_multiplier` | float | detector/paper | experimental | No usar para capital real sin validación |
| `result_r` | float | paper trader | label | Target/regresión |
| `exit_reason` | text | paper trader | label | TARGET/STOP/TRAILING_STOP/SESSION_END |
| `bars_held` | int | paper trader | label | Time-to-exit |

---

## Barras M1 (`*_bars`)

| Feature | Tipo | Origen | Backfill | Live | Estado |
|---------|------|--------|----------|------|--------|
| `ts_ms` | bigint | exchange kline | sí | sí | activa |
| `symbol` | text | monitor/backfill | sí | sí | activa |
| `session` | text | session tracker | sí | sí | activa |
| `operative` | bool | session tracker | sí | sí | activa |
| `open/high/low/close` | float | kline | sí | sí | activa |
| `volume` | float | kline | sí | sí | activa |
| `bar_delta` | float | taker buy volume | sí | sí | activa |
| `vr` | float | rolling volume | sí | sí | activa |
| `atr` | float | OHLCV | sí | sí | activa |
| `vwap` | float | OHLCV/session | sí | sí | activa |
| `regime` | text | monitor | parcial | sí | activa |
| `cvd_slope` | float | CVD history | no/parcial | sí | shadow |
| `dz` | float | delta z-score | no/parcial | sí | activa |
| `obi_l5` | float | orderbook | no | sí | activa |
| `obi_l10` | float | orderbook | no | sí | shadow |
| `obi_l20` | float | orderbook | no | sí | shadow |
| `obi_fast` | float | OBI EMA | no | sí | shadow |
| `obi_slow` | float | OBI EMA | no | sí | shadow |
| `spread_ticks` | int | orderbook | no | sí | activa |
| `stacked_imb` | text | agg trades | no | sí | activa |
| `absorption` | text | footprint proxy | no | sí | activa |
| `thin_above` / `thin_below` | bool | orderbook | no | sí | activa |
| `bid_wall` / `ask_wall` | bool | orderbook | no | sí | shadow |
| `vpin` | float | trade flow | no | sí | activa |
| `oi_momentum` | bool | OI history | no | sí | activa |
| `liq_ratio` | float | liquidations | no | sí | activa |
| `cvd_divergence` | text | monitor | no | sí | shadow |
| `sweep_confirmed` | bool | monitor | no | sí | shadow |

---

## OBI intrabar (`obi_10s`)

| Feature | Tipo | Estado | Uso |
|---------|------|--------|-----|
| `ts_ms` | bigint | activa | Timestamp de muestra |
| `symbol` | text | activa | Join por símbolo |
| `obi_l5` | float | shadow | Media/min/max intrabar |
| `obi_l10` | float | shadow | Robustez multi-depth |
| `obi_l20` | float | shadow | Robustez multi-depth |
| `spread_bps` | float | activa | Calidad de libro |

Para usar `obi_10s` en un modelo, agregar features agregadas por minuto:

| Feature derivada | Fórmula |
|------------------|---------|
| `obi_l5_mean_1m` | media de muestras dentro de la barra |
| `obi_l5_min_1m` | mínimo intrabar |
| `obi_l5_max_1m` | máximo intrabar |
| `obi_l5_last_1m` | última muestra antes del cierre |
| `spread_bps_mean_1m` | media de spread |
| `obi_sample_count` | número de muestras válidas |

---

## Labels

| Label | Tabla | Tipo | Uso |
|-------|-------|------|-----|
| `result_r` | `rbf_signals` | continuo | Regresión / expectancy |
| `win` | derivada | bool | Clasificación (`result_r > 0`) |
| `exit_reason` | `rbf_signals` | categórica | Diagnóstico |
| `bars_held` | `rbf_signals` | entero | Time-to-event |
| `mae_r` | derivada desde bars | continuo | Calidad de entrada |
| `mfe_r` | derivada desde bars | continuo | Potencial capturado |

---

## Reglas para scripts DS

1. Usar `htf_h1_aligned`, no `htf_h4_aligned`.
2. Alertar si una feature esperada no existe.
3. Alertar si una feature tiene más de 50% NULL.
4. Separar `is_pre_breakout=true` y `false` antes de comparar expectancy.
5. Separar backfill vs live para cualquier feature de orderbook/footprint.
6. Reportar `n` por símbolo, sesión y dirección en cada métrica.
7. No activar filtros por símbolo con `n < 25` señales comparables.
8. No usar `signal_score_v2` para sizing real hasta validación out-of-sample.
