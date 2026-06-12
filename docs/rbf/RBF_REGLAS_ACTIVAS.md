# RBF — Fuente de verdad operativa

*Última actualización: 2026-06-12*

Este documento es la referencia humana canónica de RBF. La jerarquía real es:

1. Código live: `data/src/strategy/detectors/range_breakout_flow.rs`
2. Wiring live: `crates/monitor/src/main.rs`
3. Config base: `config/strategy.toml` cargado por `data/src/strategy/config_file.rs`
4. Schema: migraciones `supabase/` y `migrations/`
5. Docs: resumen de lo anterior, no fuente primaria de ejecución

Si hay conflicto entre docs y código/config, gana el código/config y este documento debe corregirse.

---

## Estado actual

| Área | Estado vigente |
|------|----------------|
| Detector principal | Range Breakout Flow (`RangeBreakoutState`) |
| Temporalidad | M1 |
| Símbolos live | BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT |
| Dirección operada | Solo Shorts (`allow_long=false`, `allow_short=true`) |
| Sesiones operativas | London, LondonNyOverlap, NewYork |
| Rango válido | 0.08% - 0.55%, ventanas 15/20/30/45/60 barras M1 |
| Breakout post-confirmado | Close fuera del rango + VR >= 3.0 |
| Pre-breakout | Desactivado operativo; pendiente recalibración |
| Cooldown RBF | 60 barras M1 desde `config/strategy.toml` |
| Score de confluencia | 0-6 puntos |
| Score operable | `confluence_score >= 2`; score 1 se registra para dataset |
| Score ambiguo | `confluence_score == 4` se registra pero no opera |

---

## Config efectiva

### `config/strategy.toml`

| Parámetro | Valor vigente | Nota |
|-----------|---------------|------|
| `enabled` | true | RBF activo |
| `allow_long` | false | Longs desactivados por performance live |
| `allow_short` | true | Path principal |
| `min_rr` | 1.5 | Mínimo para emitir |
| `cvd_slope_gate` | false | CVD slope direccional quedó invertido/no confiable |
| `dz_min` / `dz_max` | 0.5 / 3.0 | Exige presión, evita extremos que revierten |
| `obi_gate` | false | OBI direccional quedó invertido en datos live |
| `obi_threshold` | 0.15 | Usado por `ObiTrap`, no por gate direccional |
| `vswap_gate` | false | Shadow/calibración |
| `breakout_ext_gate` | false | Shadow/calibración |
| `min_confluence_score` | 1 | Shadow mode operativo |
| `pre_breakout_enabled` | false | Apagado: backtest app mostró pre casi plano vs post fuerte |
| `pre_breakout_zone_pct` | 0.001 | Entrada a <=0.1% sobre `range_low` |
| `pre_breakout_rr` | 3.0 | Target más profundo que post-breakout |
| `pre_breakout_vr_min` | 1.5 | En Overlap el monitor lo sube a 2.0 |
| `pre_breakout_oi_max` | 3 | Gate de madurez del movimiento |
| `cooldown_bars` | 60 | Alineado con backtest app calibrado |

### Override en monitor

En `crates/monitor/src/main.rs`, antes de llamar al detector:

| Símbolo | `expansion_max_bars` |
|---------|----------------------|
| BTCUSDT | Some(1) |
| ETHUSDT | Some(1) |
| BNBUSDT | Some(1) |
| SOLUSDT | None |
| XRPUSDT | None |

Razón: se promovió la configuración calibrada de la app para evitar moves ya maduros. SOL/XRP mantienen bypass por correlación invertida o muestra insuficiente.

---

## Lógica de señal

### Post-breakout

1. Mantiene historial de barras M1.
2. Prueba rangos de 15/20/30/45/60 barras.
3. Requiere rango 0.08% - 0.55%.
4. Requiere potencial mínimo: rango >= 1.5 x ATR.
5. Requiere close fuera del rango.
6. Requiere VR >= 3.0.
7. Requiere CVD en rango alineado:
   - Short: `cvd_in_range < 0`
   - Long: `cvd_in_range > 0`, pero Long está desactivado por config.
8. Aplica gates duros activos: DZ, spread, OI covering, HVN, VPIN, expansión.
9. Calcula score de confluencia.
10. Registra si no hay veto y score >= `min_confluence_score`.
11. Opera si no hay veto, score >= 2 y score != 4.

### Pre-breakout

Actualmente está desactivado en `strategy.toml`. Cuando se reactive, entraría antes del close fuera del rango con estas reglas:

| Regla | Valor |
|-------|-------|
| Dirección | Short |
| Precio | `close <= range_low * (1 + pre_breakout_zone_pct)` |
| CVD rango | Negativo |
| VR | >= 1.5, o >= 2.0 en LondonNyOverlap por override |
| OI momentum reciente | `oi_mom_bars_recent <= 3` |
| Stop | `range_high` |
| Target | `entry - 3R` |

Motivo del apagado: el backtest app con microestructura disponible mostró pre-breakout casi plano (`16` tradeables, `-0.19R`) contra post-breakout fuerte (`13` tradeables, `+10.2R`). Reabrirlo requiere recalibración específica y evitar que consuma cooldown de post-breakout.

---

## Score de confluencia activo

Score máximo actual: **6 puntos**.

| Flag | Condición conceptual | Código |
|------|----------------------|--------|
| `stacked_imbalance` | Imbalance apilado en dirección del breakout | `StackedImbalance` |
| `absorption` | Absorción footprint en dirección bajista/alcista | `AbsorcionFootprint` |
| `lvn_thin` | LVN cercano o thin zone hacia target | `LvnOThinZone` |
| `vwap_bias` | Precio del lado correcto del VWAP | `VwapBias` |
| `oi_momentum` | OI expandiendo en el contexto del breakout | `OiMomentum` |
| `obi_trap` | OBI contra el breakout: participantes atrapados | `ObiTrap` |

### Removidos del score

Estos campos se siguen recolectando porque son útiles para análisis, pero no suman puntos en el score live:

| Campo/flag anterior | Estado | Motivo |
|---------------------|--------|--------|
| `cvd_slope` como flag direccional | Deprecated | Puede indicar move consumido |
| `obi_aligned` | Deprecated | OBI en dirección del trade fue peor en datos live |
| `obi_multi_depth` | Deprecated como score | L10/L20 alineados repetían el problema de OBI direccional |
| `obi_intrabar_mean` | Dataset/shadow | Se guarda en `obi_10s`; falta calibración robusta |
| `session_cvd` / `big_trade_cvd` alineados | Deprecated como score | Acumulados pueden llegar tarde |

---

## Vetos activos

| Veto | Condición | Motivo |
|------|-----------|--------|
| `spread_wide` | `spread_bps > 5` | Mercado ilíquido/fakeout |
| `oi_covering` | Short + `oi_delta_pct < -10` | Longs cerrando, no shorts abriendo |
| `hvn_target` | HVN entre entry y primera mitad del target | Obstáculo estructural |
| `vpin_toxic` | `vpin > 0.65` | Flujo tóxico/cascada |
| `long_bear_low_score` | Long contra Bear sin score suficiente | Longs están apagados de todos modos |
| `score_1` | Score menor al umbral operable | Se registra para dataset, no opera |
| `score_4` | Score exactamente 4 | Ambiguo; se registra, no opera |

---

## Datos que se graban

### `rbf_signals`

| Campo | Uso |
|-------|-----|
| `symbol`, `timestamp_ms`, `direction`, `session` | Identidad de la señal |
| `entry_price`, `stop_price`, `target_price`, `rr` | Gestión |
| `range_high`, `range_low`, `range_pct`, `range_bars` | Estructura del rango |
| `cvd_in_range`, `vr_at_breakout` | Hipótesis base |
| `cvd_slope_at_entry`, `dz_at_entry`, `obi_at_entry` | Microestructura en entry |
| `confluence_score`, `confluence_flags`, `veto_reason` | Decisión |
| `signal_score_v2`, `sizing_multiplier` | Score experimental continuo |
| `is_pre_breakout`, `bars_held`, `exit_reason`, `result_r` | Outcome y calibración |
| `htf_h1_trend`, `htf_h1_aligned` | Contexto HTF actual |

### `*_bars`

Una fila por símbolo y barra M1. Campos clave para ciencia de datos:

| Campo | Uso |
|-------|-----|
| OHLCV, `bar_delta` | Base M1 |
| `cvd_slope`, `dz`, `vr` | Flow por barra |
| `obi_l5`, `obi_l10`, `obi_l20`, `obi_fast`, `obi_slow` | Orderbook |
| `spread_ticks`, `liq_ratio`, `vpin`, `oi_momentum` | Calidad/institucional |
| `stacked_imb`, `absorption`, `thin_above`, `thin_below` | Microestructura |
| `regime`, `atr`, `vwap` | Contexto |

### `obi_10s`

Muestras intrabar cada ~10s:

| Campo | Uso |
|-------|-----|
| `ts_ms`, `symbol` | Identidad |
| `obi_l5`, `obi_l10`, `obi_l20` | Presión sostenida vs spike puntual |
| `spread_bps` | Calidad del libro |

---

## Reglas por símbolo

| Símbolo | Activo | Estado |
|---------|--------|--------|
| BTCUSDT | `expansion_max_bars=1`, `cum_delta_25b <= 200` | Evita compradores agresivos/fakeout |
| ETHUSDT | `expansion_max_bars=1`, London off, `cvd_in_range >= -700`, `obi_l5 <= 0.10` | Evita London débil, selling consumido y OBI comprador resistente |
| BNBUSDT | `expansion_max_bars=1`, `cum_delta_25b >= -500` | Evita selling masivo ya consumido |
| SOLUSDT | expansion bypass | Expansión invertida; no aplicar gate global |
| XRPUSDT | expansion bypass | Muestra insuficiente |

Los gates `cum_delta_min_short`, `cum_delta_max_short`, `cvd_in_range_min_short` y `min_confluence_score_override` existen en el código/config loader, pero no están activos en `strategy.toml` al 2026-06-12.

---

## Próximas decisiones de DS/DE

| Trabajo | Objetivo |
|---------|----------|
| Feature matrix RBF | Una tabla canónica feature -> columna -> origen -> live/backfill -> estado |
| Walk-forward | Evitar calibrar y evaluar sobre la misma ventana |
| Split backfill/live | No mezclar OHLCV histórico con microestructura real sin flags |
| Null audit | Ningún script DS debe aceptar silenciosamente features con >50% NULL |
| Score calibration | Evaluar `confluence_score`, `signal_score_v2`, `score==4` y vetos con n suficiente |
| Pre-breakout audit | Calibrar `bars_held`, MAE/MFE y time stop específico |

---

## Docs históricos

Los siguientes documentos son útiles como investigación, pero no son fuente de verdad operativa:

| Documento | Estado |
|-----------|--------|
| `RBF_V2_PLAN_COMPLETO.md` | Histórico/diseño; contiene flags ya removidos |
| `RBF_BACKTEST_14D.md` | Histórico; útil para contexto de backtest |
| `RBF_CALIBRACION_POR_ACTIVO.md` | Snapshot de calibración; algunas conclusiones ya cambiaron |
| `ORDERFLOW_FEATURES_V2.md` | Histórico parcial; H4 fue renombrado a H1 |
