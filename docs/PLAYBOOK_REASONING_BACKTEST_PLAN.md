# Playbook Reasoning para Backtesting

Plan para agregar una capa de razonamiento hardcodeado sobre las senales del
motor de estrategias, sin bloquear ni modificar el comportamiento actual en la
fase inicial.

## Objetivo

El sistema actual corre detectores y el router elige la mejor senal por score.
Eso sirve para acumular datos, pero no explica suficientemente en que contexto
funciona cada senal.

La nueva capa debe responder:

- Que historia de mercado parece estar ocurriendo.
- Que elementos la confirman.
- Que elementos la contradicen.
- Que datos faltan.
- Que detector disparo dentro de esa historia.
- En que condiciones ese detector gana, pierde o se degrada.

En Fase 1 esta capa no debe bloquear, penalizar, promover ni cambiar ninguna
senal. Solo etiqueta y persiste razonamiento para backtesting posterior.

## Principio rector

No sesgar el sistema prematuramente.

La capa de razonamiento no debe asumir que solo los setups "bonitos" tienen
edge. Debe permitir:

- Playbooks completos.
- Playbooks parciales.
- Contextos mixtos.
- Senales contradictorias.
- Senales sin playbook claro.
- Senales que parecen ruido pero podrian tener edge empirico.
- Condiciones con datos incompletos.
- Casos donde varios playbooks compiten.

El resultado de Fase 1 es una taxonomia extensa para explicar y medir, no para
filtrar.

## Arquitectura propuesta

Flujo actual:

```text
StrategyMarketContext
  -> route_strategy()
  -> detector ganador
  -> ShadowSignal
  -> Paper/outcomes
```

Flujo con razonamiento Fase 1:

```text
StrategyMarketContext
  -> route_strategy()
  -> detector ganador
  -> classify_playbook_reasoning(ctx, signal, detector_log)
  -> ShadowSignal + reasoning labels
  -> Paper/outcomes sin cambios
```

Para near misses:

```text
StrategyMarketContext
  -> collect_near_misses()
  -> classify_playbook_reasoning_for_near_miss(ctx, candidate)
  -> near_miss log con reasoning labels
```

## Fases

### Fase 1 - Observador hardcodeado

No cambia comportamiento.

Implementar un `PlaybookReasoning` que:

- Corre despues del router.
- Lee `StrategyMarketContext`, `StrategySignal` y `detector_log`.
- Produce etiquetas hardcodeadas.
- Produce scores de pertenencia a varios playbooks.
- Guarda evidencia, faltantes y contradicciones.
- Persiste el resultado en Supabase `shadow_signals` y logs locales.
- Permite analizar outcomes por detector + playbook + condicion.

Prohibido en Fase 1:

- Bloquear senales.
- Cambiar `score`.
- Cambiar `min_score`.
- Cambiar `min_rr`.
- Cambiar cooldown.
- Cambiar `StrategyAction`.
- Hacer early returns que oculten razonamientos alternativos.

### Fase 2 - Modificador de score, solo despues de datos

Cuando haya muestra suficiente:

- Usar outcomes para medir cada detector dentro de cada playbook.
- Definir factores suaves, por ejemplo `playbook_factor`.
- Aplicar solo multiplicadores pequenos y auditables.

Ejemplo futuro:

```text
score_final = detector_score * playbook_factor
```

No deberia existir un gate duro en Fase 2 salvo bugs obvios.

### Fase 3 - Gates conservadores con evidencia

Solo despues de backtesting:

- Convertir una condicion en bloqueo si estadisticamente destruye el edge.
- Mantener excepciones cuando exista otro playbook fuerte.
- Versionar cada gate.

Ejemplo futuro:

```text
Bloquear VVPC long solo si:
  - no hay imbalance_continuation,
  - VP Open Bias = InsideValue,
  - precio dentro de value,
  - y el backtest muestra expectancy negativa persistente.
```

## Contrato de salida Fase 1

Estructura sugerida:

```rust
pub struct PlaybookReasoning {
    pub version: String,
    pub primary_playbook: PlaybookId,
    pub secondary_playbooks: Vec<PlaybookScore>,
    pub market_state: Vec<ReasonTag>,
    pub location_tags: Vec<ReasonTag>,
    pub flow_tags: Vec<ReasonTag>,
    pub liquidity_tags: Vec<ReasonTag>,
    pub book_tags: Vec<ReasonTag>,
    pub institutional_tags: Vec<ReasonTag>,
    pub structure_tags: Vec<ReasonTag>,
    pub trigger_tags: Vec<ReasonTag>,
    pub risk_tags: Vec<ReasonTag>,
    pub confirmation_tags: Vec<ReasonTag>,
    pub contradiction_tags: Vec<ReasonTag>,
    pub missing_tags: Vec<ReasonTag>,
    pub detector_role_tags: Vec<ReasonTag>,
    pub confidence: f64,
    pub completeness: f64,
}
```

Persistencia JSON sugerida:

```json
{
  "reasoning_version": "playbook_reasoning_v1",
  "primary_playbook": "trapped_traders_reversal",
  "secondary_playbooks": [
    {"id": "failed_auction_reversal", "score": 0.68},
    {"id": "liquidity_sweep_reversal", "score": 0.55}
  ],
  "location_tags": ["near_val", "near_naked_poc"],
  "flow_tags": ["seller_aggression_absorbed", "delta_drain_bullish"],
  "confirmation_tags": ["footprint_absorption_bid", "reclaim_value_area"],
  "contradiction_tags": ["funding_long_crowded"],
  "missing_tags": ["liq_feed_quiet"],
  "detector_role_tags": ["far_primary_confirmation", "vafa_secondary_confirmation"],
  "confidence": 0.72,
  "completeness": 0.81
}
```

## Playbook IDs hardcodeados

Todos deben poder existir simultaneamente con distintos scores. El sistema no
debe forzar una unica narrativa si el contexto es mixto.

### Reversal / traps

- `trapped_traders_reversal`
- `failed_auction_reversal`
- `footprint_absorption_reversal`
- `liquidity_sweep_reversal`
- `cvd_absorption_reversal`
- `funding_exhaustion_reversal`
- `smart_money_fade_reversal`
- `order_block_absorption_reversal`
- `value_extreme_rejection`

### Continuation / imbalance

- `imbalance_continuation`
- `vwap_pullback_continuation`
- `lvn_vacuum_breakout`
- `dom_imbalance_breakout`
- `session_open_expansion`
- `order_block_retest_continuation`
- `fvg_rebalance_continuation`
- `trend_day_continuation`
- `thin_book_momentum_continuation`

### Rotation / balance

- `value_area_rotation`
- `inside_value_mean_reversion`
- `poc_magnet_rotation`
- `vah_to_val_rotation`
- `val_to_vah_rotation`
- `range_extreme_fade`
- `auction_balance_chop`

### Liquidity magnet / target seeking

- `naked_poc_magnet`
- `hvn_liquidity_magnet`
- `single_print_fill`
- `liq_pool_magnet`
- `weekly_monthly_vp_magnet`
- `fvg_fill_magnet`

### Institutional / positioning

- `institutional_exhaustion`
- `retail_trapped_positioning`
- `top_trader_accumulation`
- `top_trader_distribution`
- `oi_expansion_trend`
- `oi_collapse_liquidation`
- `funding_carry_unwind`

### Defensive / unclear

- `mixed_context`
- `conflicting_signals`
- `data_insufficient`
- `data_stale`
- `late_move_after_cascade`
- `toxic_flow_environment`
- `spread_untradable_environment`
- `no_clear_playbook`
- `possible_noise_signal`

## Vocabulario por elemento

Las etiquetas deben ser acumulativas. Una senal puede tener tags de varios
grupos.

### 1. Market state

- `market_balance`
- `market_imbalance_up`
- `market_imbalance_down`
- `market_accumulation`
- `market_distribution`
- `market_transition_to_balance`
- `market_transition_to_imbalance`
- `market_chop`
- `market_compression`
- `market_expansion`
- `market_stress`
- `market_aftermath`
- `market_unknown`

Fuentes:

- `ctx.regime`
- `ctx.auction_state`
- `ctx.slow_slope`
- `ctx.flow.fast_slope`
- `ctx.vp_open_bias`

### 2. Volume profile location

- `inside_value`
- `above_vah`
- `below_val`
- `near_vah`
- `near_val`
- `near_poc`
- `near_hvn`
- `near_lvn`
- `between_poc_and_vah`
- `between_poc_and_val`
- `far_from_value`
- `at_value_extreme`
- `at_value_mid`
- `accepted_inside_value`
- `accepted_outside_value`
- `failed_acceptance_above_vah`
- `failed_acceptance_below_val`
- `value_location_unknown`

Niveles multi-timeframe:

- `near_daily_poc`
- `near_weekly_poc`
- `near_monthly_poc`
- `near_weekly_vah`
- `near_weekly_val`
- `near_monthly_vah`
- `near_monthly_val`
- `near_naked_poc`
- `near_single_print`
- `near_prior_session_high`
- `near_prior_session_low`

### 3. VP Open Bias

- `vp_bias_inside_value`
- `vp_bias_outside_va_inside_pa_long`
- `vp_bias_outside_va_inside_pa_short`
- `vp_bias_trend_day_up`
- `vp_bias_trend_day_down`
- `vp_bias_fade_gap_long`
- `vp_bias_fade_gap_short`
- `vp_bias_unknown`

Interpretacion:

- `InsideValue`: favorece rotacion y fades en extremos.
- `OutsideVaInsidePa`: favorece aceptacion hacia POC previo.
- `TrendDay`: favorece continuacion y penaliza reversals contrarios.
- `FadeGap`: favorece fade del gap hacia value.

En Fase 1 esto solo etiqueta. No bloquea.

### 4. Flow / delta / CVD

Direccion:

- `delta_positive`
- `delta_negative`
- `delta_neutral`
- `cvd_slope_positive`
- `cvd_slope_negative`
- `cvd_slope_flat`
- `taker_imbalance_buy`
- `taker_imbalance_sell`
- `taker_imbalance_neutral`

Absorcion:

- `seller_aggression_absorbed`
- `buyer_aggression_absorbed`
- `footprint_absorption_bid`
- `footprint_absorption_ask`
- `absorption_unknown`
- `delta_extreme_at_low`
- `delta_extreme_at_high`
- `large_negative_delta_no_continuation`
- `large_positive_delta_no_continuation`

Divergencia:

- `cvd_bullish_divergence`
- `cvd_bearish_divergence`
- `cvd_divergence_persistent`
- `cvd_confirms_long`
- `cvd_confirms_short`
- `cvd_contradicts_long`
- `cvd_contradicts_short`

Delta drain:

- `delta_drain_bullish`
- `delta_drain_bearish`
- `delta_acceleration_bullish`
- `delta_acceleration_bearish`
- `delta_velocity_missing`

Finish / unfinish:

- `finish_action_bullish`
- `finish_action_bearish`
- `unfinish_action_bullish`
- `unfinish_action_bearish`
- `finish_action_missing`

Big trades:

- `big_trade_bullish`
- `big_trade_bearish`
- `big_trade_absorption_low`
- `big_trade_absorption_high`
- `big_trade_missing`

Stacked imbalance:

- `stacked_imbalance_bullish`
- `stacked_imbalance_bearish`
- `fbg_bullish`
- `fbg_bearish`
- `stacked_imbalance_none`
- `stacked_imbalance_unknown`

VPIN:

- `vpin_clean`
- `vpin_normal`
- `vpin_toxic`
- `vpin_missing`

### 5. Liquidity and liquidations

Sweep:

- `sweep_high`
- `sweep_low`
- `sweep_confirmed`
- `sweep_failed_to_reclaim`
- `sweep_without_volume`
- `no_sweep`

Liquidations:

- `short_liquidations_present`
- `long_liquidations_present`
- `liquidation_cluster_above`
- `liquidation_cluster_below`
- `liquidation_outlier`
- `liquidation_cascade_active`
- `liquidation_cascade_exhausted`
- `liq_feed_quiet`
- `liq_feed_missing`

Liquidity map:

- `liq_target_above`
- `liq_target_below`
- `high_liq_density_above`
- `high_liq_density_below`
- `liq_map_missing`

### 6. Order book / DOM

Book imbalance:

- `obi_bid_dominant`
- `obi_ask_dominant`
- `obi_neutral`
- `obi_persistent_bid`
- `obi_persistent_ask`
- `obi_missing`

Thin zones:

- `thin_zone_above`
- `thin_zone_below`
- `no_thin_zone_above`
- `no_thin_zone_below`

Walls:

- `bid_wall_nearby`
- `ask_wall_nearby`
- `wall_support_below`
- `wall_resistance_above`
- `path_clear_above`
- `path_clear_below`

Microstructure:

- `microprice_above_mid`
- `microprice_below_mid`
- `spread_clean`
- `spread_wide`
- `spread_untradable`
- `spoof_detected`
- `spoof_unknown`

### 7. Institutional positioning

Funding:

- `funding_neutral`
- `funding_elevated_long`
- `funding_extreme_long`
- `funding_elevated_short`
- `funding_extreme_short`
- `funding_velocity_rising`
- `funding_velocity_retreating`
- `funding_peak_confirmed`
- `funding_missing`

Open interest:

- `oi_expanding_with_price_up`
- `oi_expanding_with_price_down`
- `oi_declining_with_price_up`
- `oi_declining_with_price_down`
- `oi_accumulation_confirmed`
- `oi_distribution_confirmed`
- `oi_short_covering`
- `oi_long_liquidation`
- `oi_delta_zscore_high`
- `oi_delta_zscore_low`
- `oi_missing`

Long/short ratios:

- `top_traders_long`
- `top_traders_short`
- `retail_long_crowded`
- `retail_short_crowded`
- `smart_money_bullish_divergence`
- `smart_money_bearish_divergence`
- `ls_ratio_neutral`
- `ls_ratio_missing`

Smart money score:

- `smart_money_score_long`
- `smart_money_score_short`
- `smart_money_score_neutral`
- `smart_money_score_missing`

### 8. Structure / SMC

Market structure:

- `bos_bullish`
- `bos_bearish`
- `choch_bullish`
- `choch_bearish`
- `structure_range_high`
- `structure_range_low`
- `premium_zone`
- `discount_zone`
- `structure_neutral`
- `structure_missing`

Order blocks:

- `inside_bullish_order_block`
- `inside_bearish_order_block`
- `near_bullish_order_block`
- `near_bearish_order_block`
- `bullish_ob_active`
- `bearish_ob_active`
- `ob_volume_ratio_gt_1_5x`
- `ob_volume_ratio_gt_3x`
- `ob_swings_broken_ge_2`
- `order_blocks_missing`

FVG:

- `inside_bullish_fvg`
- `inside_bearish_fvg`
- `near_bullish_fvg`
- `near_bearish_fvg`
- `fvg_fill_target`
- `fvg_missing`

### 9. Session and timing

Session:

- `session_asia`
- `session_london`
- `session_london_ny_overlap`
- `session_new_york`
- `session_off`
- `session_unknown`

Phase:

- `phase_opening_rush`
- `phase_open`
- `phase_mid`
- `phase_close`
- `phase_unknown`

Timing quality:

- `early_session_opportunity`
- `mid_session_continuation`
- `late_session_risk`
- `after_large_move_late_entry`
- `time_context_missing`

### 10. Trigger / execution logic

Triggers:

- `reclaim_value_area`
- `lose_value_area`
- `close_back_inside_value`
- `close_accepts_outside_value`
- `breakout_acceptance`
- `breakout_failure`
- `retest_hold`
- `order_block_reclaim`
- `vwap_reclaim`
- `vwap_loss`
- `avwap_bos_reclaim`
- `avwap_bos_loss`
- `mss_active`
- `engulfing_reversal`
- `volume_candle_confirmation`
- `time_candle_confirmation`
- `trigger_missing`

Execution stage:

- `early_entry`
- `confirmed_entry`
- `late_entry`
- `post_cascade_entry`
- `no_entry_quality`

### 11. Risk and target quality

Risk:

- `rr_ge_min`
- `rr_below_min`
- `rr_high_but_reachable`
- `rr_too_large_fantasy`
- `risk_degenerate`
- `stop_inside_noise`
- `stop_beyond_structure`
- `stop_aligned_with_sweep`
- `stop_aligned_with_ob`
- `stop_aligned_with_value_extreme`

Targets:

- `target_poc`
- `target_vah`
- `target_val`
- `target_hvn`
- `target_lvn`
- `target_naked_poc`
- `target_single_print`
- `target_liq_pool`
- `target_swing_high`
- `target_swing_low`
- `target_wall`
- `target_missing`

### 12. Data quality

- `data_live`
- `data_degraded`
- `data_fallback`
- `data_stale`
- `data_missing`
- `atr_not_ready`
- `volume_profile_missing`
- `vwap_missing`
- `footprint_missing`
- `orderbook_missing`
- `institutional_missing`
- `session_missing`
- `market_structure_missing`
- `historical_warmup`

## Detector role vocabulary

Cada detector puede ser clasificado como:

- `primary_setup`
- `secondary_confirmation`
- `context_confirmation`
- `timing_confirmation`
- `risk_target_provider`
- `contradiction_detector`
- `inactive_due_to_missing_data`
- `inactive_due_to_context`
- `inactive_due_to_low_score`
- `noise_candidate`

### VAFA - ValueAreaFailedAuction

Playbooks compatibles:

- `failed_auction_reversal`
- `value_area_rotation`
- `trapped_traders_reversal`
- `value_extreme_rejection`

Confirmation tags:

- `failed_acceptance_above_vah`
- `failed_acceptance_below_val`
- `close_back_inside_value`
- `delta_drain_bullish`
- `delta_drain_bearish`
- `seller_aggression_absorbed`
- `buyer_aggression_absorbed`
- `target_poc`

Contradiction tags:

- `thin_zone_in_breakout_direction`
- `cvd_confirms_breakout`
- `oi_expanding_with_breakout`
- `vp_bias_trend_day_against_reversal`

### FAR - FootprintAbsorptionReversal

Playbooks compatibles:

- `footprint_absorption_reversal`
- `trapped_traders_reversal`
- `order_block_absorption_reversal`
- `value_extreme_rejection`

Confirmation tags:

- `footprint_absorption_bid`
- `footprint_absorption_ask`
- `three_delta_levels_absorbed`
- `finish_action_bullish`
- `finish_action_bearish`
- `big_trade_bullish`
- `big_trade_bearish`

Contradiction tags:

- `footprint_missing`
- `unfinish_action_against_signal`
- `strong_trend_against_reversal`
- `no_reclaim_after_absorption`

### LIQ - LiquidationHunt

Playbooks compatibles:

- `liquidity_sweep_reversal`
- `trapped_traders_reversal`
- `oi_collapse_liquidation`
- `liq_pool_magnet`

Confirmation tags:

- `short_liquidations_present`
- `long_liquidations_present`
- `liquidation_outlier`
- `sweep_confirmed`
- `liq_target_above`
- `liq_target_below`
- `path_clear_after_sweep`

Contradiction tags:

- `liquidation_cascade_active`
- `late_move_after_cascade`
- `liq_feed_missing`
- `funding_crowded_against_signal`
- `smart_money_against_signal`

### CDR - CvdDivergenceReversal

Playbooks compatibles:

- `cvd_absorption_reversal`
- `trapped_traders_reversal`
- `value_extreme_rejection`

Confirmation tags:

- `cvd_bullish_divergence`
- `cvd_bearish_divergence`
- `cvd_divergence_persistent`
- `near_vah`
- `near_val`
- `target_poc`

Contradiction tags:

- `trend_day_continuation_against_reversal`
- `price_far_from_value_extreme`
- `divergence_not_persistent`

### LVN - LvnLiquidityVacuumBreakout

Playbooks compatibles:

- `lvn_vacuum_breakout`
- `imbalance_continuation`
- `thin_book_momentum_continuation`
- `hvn_liquidity_magnet`

Confirmation tags:

- `near_lvn`
- `thin_zone_above`
- `thin_zone_below`
- `path_clear_above`
- `path_clear_below`
- `cvd_confirms_long`
- `cvd_confirms_short`
- `target_hvn`

Contradiction tags:

- `no_lvn_nearby`
- `wall_against_breakout`
- `inside_value_range_day_against_breakout`
- `failed_acceptance_against_breakout`

### DIB - DomImbalanceBreakout

Playbooks compatibles:

- `dom_imbalance_breakout`
- `imbalance_continuation`
- `thin_book_momentum_continuation`

Confirmation tags:

- `obi_bid_dominant`
- `obi_ask_dominant`
- `obi_persistent_bid`
- `obi_persistent_ask`
- `microprice_above_mid`
- `microprice_below_mid`
- `thin_zone_above`
- `thin_zone_below`

Contradiction tags:

- `obi_neutral`
- `wall_against_breakout`
- `spread_wide`
- `vpin_toxic`

### SOB - SessionOpenBreakout

Playbooks compatibles:

- `session_open_expansion`
- `imbalance_continuation`
- `trend_day_continuation`
- `liquidity_sweep_reversal` when breakout follows a sweep

Confirmation tags:

- `phase_opening_rush`
- `session_london`
- `session_new_york`
- `session_london_ny_overlap`
- `bos_bullish`
- `bos_bearish`
- `thin_zone_above`
- `thin_zone_below`

Contradiction tags:

- `session_asia`
- `session_off`
- `late_session_risk`
- `breakout_without_flow`
- `inside_value_range_day_against_breakout`

### OBR - OrderBlockRetest

Playbooks compatibles:

- `order_block_retest_continuation`
- `order_block_absorption_reversal`
- `trapped_traders_reversal`
- `vwap_pullback_continuation` when OB overlaps VWAP pullback

Confirmation tags:

- `inside_bullish_order_block`
- `inside_bearish_order_block`
- `bullish_ob_active`
- `bearish_ob_active`
- `ob_volume_ratio_gt_3x`
- `ob_swings_broken_ge_2`
- `footprint_absorption_bid`
- `footprint_absorption_ask`

Contradiction tags:

- `order_blocks_missing`
- `ob_invalidated`
- `flow_against_ob`
- `retest_failed`

### VWAP - VwapValuePullbackContinuation

Playbooks compatibles:

- `vwap_pullback_continuation`
- `imbalance_continuation`
- `trend_day_continuation`
- `order_block_retest_continuation` when OB overlaps pullback

Confirmation tags:

- `vwap_reclaim`
- `avwap_bos_reclaim`
- `accepted_inside_value_pullback`
- `cvd_confirms_long`
- `cvd_confirms_short`
- `slow_slope_aligned`
- `fast_slope_not_exhausted`

Contradiction tags:

- `inside_value_range_day_against_continuation`
- `funding_crowded_against_signal`
- `cvd_contradicts_signal`
- `fast_slope_against_signal`
- `strong_absorption_against_signal`

### FER - FundingExhaustionReversal

Playbooks compatibles:

- `funding_exhaustion_reversal`
- `institutional_exhaustion`
- `funding_carry_unwind`

Confirmation tags:

- `funding_extreme_long`
- `funding_extreme_short`
- `funding_peak_confirmed`
- `funding_velocity_retreating`
- `retail_long_crowded`
- `retail_short_crowded`
- `oi_declining_with_price_up`
- `oi_declining_with_price_down`

Contradiction tags:

- `funding_neutral`
- `funding_not_peaked`
- `oi_expansion_trend_against_reversal`
- `smart_money_against_signal`

### SMD - SmartMoneyDivergence

Playbooks compatibles:

- `smart_money_fade_reversal`
- `retail_trapped_positioning`
- `top_trader_accumulation`
- `top_trader_distribution`
- `institutional_exhaustion`

Confirmation tags:

- `smart_money_bullish_divergence`
- `smart_money_bearish_divergence`
- `top_traders_long`
- `top_traders_short`
- `retail_long_crowded`
- `retail_short_crowded`
- `funding_elevated_long`
- `funding_elevated_short`

Contradiction tags:

- `ls_ratio_neutral`
- `smart_money_score_neutral`
- `flow_against_smart_money`
- `no_positioning_divergence`

## Playbook scoring hardcodeado Fase 1

Fase 1 no usa estos scores para operar. Solo mide pertenencia.

### Score de playbook

Cada playbook puede calcular:

```text
playbook_score =
  context_score * 0.30
  + location_score * 0.20
  + flow_score * 0.20
  + trigger_score * 0.15
  + target_risk_score * 0.10
  + data_quality_score * 0.05
```

Pero debe guardar tambien componentes separados.

### Completeness

`completeness` mide cuantas piezas tiene la historia, no si es buena.

Ejemplo:

```text
trapped_traders_reversal completeness:
  level_context present
  liquidity_event present
  aggressive_delta present
  absorption present
  reclaim trigger present
  target present
```

### Confidence

`confidence` mide claridad de la clasificacion.

Un caso con muchos tags contradictorios puede tener:

```text
primary_playbook = mixed_context
confidence = 0.35
completeness = 0.70
```

Esto es valido y util para backtesting.

## Backtesting que habilita

Preguntas que debe poder responder:

- Que detector gana mas dentro de `trapped_traders_reversal`.
- Que detector pierde cuando `no_clear_playbook`.
- Si `VVPC` funciona solo con `imbalance_continuation`.
- Si `FAR` necesita `liq_event` o basta con absorcion + reclaim.
- Si `LVN` requiere `DIB` como confirmacion o puede operar solo.
- Si `SOB` funciona fuera de `phase_opening_rush`.
- Si `SMD` mejora cuando tambien hay `funding_extreme`.
- Si `VP Open Bias` debe convertirse en gate duro.
- Si `vpin_toxic` solo degrada o debe bloquear.
- Si `unfinish_action_against_signal` mata el edge.
- Si `big_trade_bullish/bearish` realmente aumenta expectancy.
- Si `near_naked_poc` cambia el MFE/MAE.
- Si `single_print_fill` sirve como target.
- Si `mixed_context` tiene edge o es ruido.

## Metricas por corte

Guardar y analizar por:

- `detector_id`
- `primary_playbook`
- `secondary_playbooks`
- `side`
- `regime`
- `auction_state`
- `vp_open_bias`
- `session`
- `phase`
- `location_tags`
- `flow_tags`
- `liquidity_tags`
- `institutional_tags`
- `contradiction_tags`
- `missing_tags`
- `rr_bucket`
- `score_bucket`
- `confidence_bucket`
- `completeness_bucket`

Metricas:

- N
- win rate
- expectancy neta
- R multiple promedio
- MFE-R
- MAE-R
- MFE/MAE
- target hit rate
- stop hit rate
- TTL rate
- average time to MFE
- average time to MAE
- profit factor
- max adverse excursion antes de target

## Implementacion sugerida

### Archivos nuevos

- `data/src/strategy/playbook_reasoning.rs`
- `data/src/strategy/playbook_types.rs` si crece demasiado
- `scripts/analysis/analyze_playbook_reasoning.py`

### Integracion monitor

En `crates/monitor/src/main.rs`:

```text
let (signal, detector_log) = route_strategy(&ctx, &cfg);
let reasoning = classify_playbook_reasoning(&ctx, &signal, &detector_log);
persist signal + reasoning
```

### Integracion UI

En `src/chart/kline.rs`:

```text
let (signal, detector_log) = route_strategy(&ctx, &cfg);
let reasoning = classify_playbook_reasoning(&ctx, &signal, &detector_log);
mostrar solo resumen, persistir completo si overlay activo
```

### Persistencia

Agregar campos en Supabase `shadow_signals`:

- `reasoning_version`
- `primary_playbook`
- `secondary_playbooks`
- `reasoning_tags`
- `reasoning_confidence`
- `reasoning_completeness`

Mantener tambien el objeto completo en `subdomi_ctx.playbook_reasoning` como
respaldo flexible para tags nuevos sin migracion inmediata.

## Reglas de seguridad para Fase 1

- Si no hay senal, clasificar near misses tambien.
- Si hay datos faltantes, emitir tags `missing`, no ocultar el caso.
- Si hay contradicciones, emitir `conflicting_signals`, no forzar playbook.
- Si varios playbooks empatan, mantener todos en `secondary_playbooks`.
- No usar solo el detector ganador para clasificar; mirar contexto completo.
- No usar frases humanas libres como fuente principal; usar tags estables.
- Versionar la taxonomia desde el primer dia.

## Definicion de "no sesgado"

La Fase 1 se considera no sesgada si:

- Registra `no_clear_playbook` sin penalizar.
- Registra `possible_noise_signal` sin bloquear.
- Registra `mixed_context` sin elegir a la fuerza.
- Permite que `VVPC` gane aunque no haya `imbalance_continuation`.
- Permite que `FAR` gane sin liquidaciones.
- Permite que `LVN` gane sin DIB.
- Permite que una senal contradicha quede viva para medir outcome.
- Guarda contradicciones como datos, no como decision.

## Decision futura esperada

Despues de acumular datos, el sistema deberia poder responder con evidencia:

```text
Mantener libre:
  - detector/playbook con expectancy positiva incluso en contexto parcial.

Hacer mas conservador:
  - detector/playbook con expectancy positiva solo cuando completeness >= X.

Bloquear:
  - detector/playbook con expectancy negativa persistente bajo condicion Y.

Promover:
  - combinacion detector + playbook + tags con edge robusto.
```

La meta no es imponer playbooks. La meta es aprender cuales razonamientos
explican el edge real del sistema.
