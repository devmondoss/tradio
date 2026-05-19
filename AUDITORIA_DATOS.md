# Auditoria de datos para estrategias

Archivos leidos segun el pedido:

- `data/src/strategy/mod.rs`
- `data/src/strategy/detectors/vwap_value_pullback_continuation.rs`
- `data/src/strategy/detectors/value_area_failed_auction.rs`
- `data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs`
- `data/src/strategy/detectors/liquidation_hunt.rs`
- `data/src/context/mod.rs`: no existe en este repo. La definicion real esta en `data/src/strategy/types.rs`.
- `crates/monitor/src/main.rs`: en este repo el monitor esta bajo `crates/monitor`, no `monitor`.
- `monitor/src/kline.rs`: no existe. Hay un constructor de contexto en `src/chart/kline.rs` para la UI/chart, pero no dentro del crate monitor.

## 1. Campos exactos de `StrategyMarketContext`

Definicion real: `data/src/strategy/types.rs:164-209`.

| Campo | Tipo |
|---|---|
| `symbol` | `String` |
| `timestamp_ms` | `i64` |
| `price` | `f64` |
| `regime` | `Regime` |
| `atr` | `Option<f64>` |
| `volume_profile` | `VolumeProfileContext` |
| `vwap` | `VwapContext` |
| `flow` | `OrderFlowContext` |
| `orderbook` | `OrderBookContext` |
| `institutional` | `Option<crate::institutional::InstitutionalContext>` |
| `swing_high_20` | `Option<f64>` |
| `swing_low_20` | `Option<f64>` |
| `market_structure` | `Option<MarketStructureContext>` |
| `session` | `Option<SessionContext>` |
| `order_blocks` | `Option<OrderBlockContext>` |
| `fvg` | `Option<FvgContext>` |
| `leverage` | `f64` |

Campos que no existen en `StrategyMarketContext`: `footprint`, `footprint_by_price`, `depth`, `dom`, `heatmap`, `raw_trades`, `raw_liquidation_events`, `mark_price`.

Subcontextos relevantes:

- `VolumeProfileContext`: `poc`, `vah`, `val`, `hvn_nearby`, `lvn_nearby`, `value_location`, `quality` (`data/src/strategy/types.rs:89-98`).
- `VwapContext`: `vwap_session`, `avwap_bos`, `avwap_event`, `price_vs_vwap`, `price_vs_avwap_bos`, `price_vs_avwap_event`, `quality` (`data/src/strategy/types.rs:100-109`).
- `OrderFlowContext`: `cvd`, `cvd_slope`, `delta`, `taker_imbalance`, `buy_volume`, `sell_volume`, `vpin`, `cvd_divergence`, `footprint_absorption`, `stacked_imbalance`, `failed_acceptance`, `sweep_confirmed`, `mss_active`, `quality`, `funding_rate`, `basis`, `oi_delta`, `oi_momentum_aligned`, `bid_wall_nearby`, `ask_wall_nearby`, `price_action_clean`, `fast_slope` (`data/src/strategy/types.rs:111-144`).
- `OrderBookContext`: `obi_l5`, `obi_l10`, `obi_l20`, `microprice`, `spread_bps`, `walls_above`, `walls_below`, `thin_zone_above`, `thin_zone_below`, `quality`, `spoof` (`data/src/strategy/types.rs:146-161`).
- `InstitutionalContext`: `timestamp_ms`, `liquidations`, `ls_ratio`, `oi_trend`, `taker_ratio`, `funding`, `quality`, `smart_money_score`, `liq_map` (`data/src/institutional/types.rs:188-208`).

## 2. Footprint: llega al contexto o solo UI

El footprint real como delta por nivel de precio no llega a `StrategyMarketContext`.

Si existe para la UI/chart: `KlineDataPoint` contiene `footprint: KlineTrades` (`data/src/chart/kline.rs:11-15`), y `KlineTrades` agrupa por `Price` en `FxHashMap<Price, GroupedTrades>` (`data/src/chart/kline.rs:144-148`). Cada `GroupedTrades` tiene `buy_qty` y `sell_qty`, y `delta_qty()` devuelve `buy_qty - sell_qty` (`data/src/chart/kline.rs:86-133`).

Pero el contexto de estrategia solo recibe agregados: `delta`, `buy_volume`, `sell_volume`, `cvd`, `cvd_slope`, y un enum `footprint_absorption`. En monitor, `on_bar_close` calcula `bar_delta = bar_buy - bar_sell` (`crates/monitor/src/main.rs:847-849`) y luego pasa solo `[bar_delta]` a `derive_failed_acceptance_and_absorption` (`crates/monitor/src/main.rs:895-898`). En `build_flow_context`, eso termina como `delta`, `buy_volume`, `sell_volume` y `footprint_absorption`, no como mapa por precio (`crates/monitor/src/main.rs:1035-1056`).

Conclusion: el footprint por nivel de precio es visual/UI; al contexto llega una version resumida, no el footprint.

## 3. Heatmap de liquidez: contexto o visual

El heatmap historico de liquidez no llega al contexto.

En la UI, `HeatmapChart` guarda `heatmap: HistoricalDepth` (`src/chart/heatmap.rs:149-155`), recibe `Depth` en `insert_depth`, y llama `self.heatmap.insert_latest_depth(depth, rounded_update)` (`src/chart/heatmap.rs:217-252`). `HistoricalDepth` guarda `price_levels: BTreeMap<Price, Vec<OrderRun>>` (`data/src/chart/heatmap.rs:136-143`).

En monitor, el stream de depth si llega (`Event::DepthReceived`) y se guarda como `self.depth = Some(depth)` (`crates/monitor/src/main.rs:2043-2048`, `crates/monitor/src/main.rs:803-807`). Pero al contexto solo entra `OrderBookContext`, construido con `build_orderbook_context(d)` (`crates/monitor/src/main.rs:1011-1026`). Ese builder calcula OBI, microprice, spread, paredes y thin zones; no pasa `HistoricalDepth` ni la matriz del heatmap (`data/src/strategy/adapter.rs:6-112`).

Conclusion: el heatmap como tal es visual. El contexto recibe derivados del order book actual, no el heatmap historico.

## 4. DOM completo o solo OBI calculado

No llega el DOM completo. Tampoco es solo OBI: llega un resumen del DOM.

`Depth` completo se mantiene en `BarState.depth` (`crates/monitor/src/main.rs:389`, `crates/monitor/src/main.rs:803-807`), pero `StrategyMarketContext` no tiene un campo `Depth`, `dom`, `bids` o `asks`. El resumen `OrderBookContext` contiene:

- `obi_l5`, `obi_l10`, `obi_l20`: calculados con sumas de los primeros niveles (`data/src/strategy/adapter.rs:35-55`, `data/src/strategy/adapter.rs:100-104`).
- `microprice` y `spread_bps`: calculados desde best bid/ask (`data/src/strategy/adapter.rs:10-33`).
- `walls_above`, `walls_below`: niveles detectados por cantidad relativa (`data/src/strategy/adapter.rs:57-78`).
- `thin_zone_above`, `thin_zone_below`: zonas finas por top 10 niveles (`data/src/strategy/adapter.rs:80-98`).
- `spoof`: existe en el struct, pero el builder lo fija en `None` (`data/src/strategy/adapter.rs:100-112`).

Conclusion: el DOM completo no llega. Llega un resumen: OBI + microprice + spread + paredes + thin zones.

## 5. Campos que lee cada estrategia

### VVPC (`VwapValuePullbackContinuation`)

Lee estos campos reales:

- `ctx.price`, `ctx.atr`, `ctx.volume_profile`, `ctx.vwap`, `ctx.flow`, `ctx.orderbook` (`data/src/strategy/detectors/vwap_value_pullback_continuation.rs:6-11`).
- `volume_profile.vah`, `volume_profile.val` (`data/src/strategy/detectors/vwap_value_pullback_continuation.rs:13-14`).
- `vwap.price_vs_avwap_bos`, `vwap.price_vs_vwap` (`data/src/strategy/detectors/vwap_value_pullback_continuation.rs:18-43`).
- `flow.fast_slope`, `ctx.regime`, `volume_profile.value_location` (`data/src/strategy/detectors/vwap_value_pullback_continuation.rs:52-56`, `data/src/strategy/detectors/vwap_value_pullback_continuation.rs:138-142`).
- `flow.cvd_slope`, `flow.delta`, `flow.cvd`, `flow.failed_acceptance` (`data/src/strategy/detectors/vwap_value_pullback_continuation.rs:61-64`, `data/src/strategy/detectors/vwap_value_pullback_continuation.rs:144-147`).
- `orderbook.spread_bps`, `orderbook.microprice` (`data/src/strategy/detectors/vwap_value_pullback_continuation.rs:66-67`, `data/src/strategy/detectors/vwap_value_pullback_continuation.rs:149-150`).
- `ctx.institutional.funding.regime` para bloquear longs con funding elevado/extremo; si `institutional` es `None`, bloquea long por `unwrap_or(false)` (`data/src/strategy/detectors/vwap_value_pullback_continuation.rs:71-83`).
- `flow.basis` via `adapter::basis_ok` (`data/src/strategy/detectors/vwap_value_pullback_continuation.rs:84`, `data/src/strategy/detectors/vwap_value_pullback_continuation.rs:152`).
- `ctx.swing_high_20`, `ctx.swing_low_20` para targets (`data/src/strategy/detectors/vwap_value_pullback_continuation.rs:95`, `data/src/strategy/detectors/vwap_value_pullback_continuation.rs:162`).
- `ctx.order_blocks.nearest_bullish/nearest_bearish` solo como evidencia (`data/src/strategy/detectors/vwap_value_pullback_continuation.rs:105-106`, `data/src/strategy/detectors/vwap_value_pullback_continuation.rs:172-173`).
- `ctx.fvg.nearest_bullish/nearest_bearish` solo como evidencia (`data/src/strategy/detectors/vwap_value_pullback_continuation.rs:108-109`, `data/src/strategy/detectors/vwap_value_pullback_continuation.rs:175-176`).
- `ctx.timestamp_ms` y `ctx.regime` para la senal emitida (`data/src/strategy/detectors/vwap_value_pullback_continuation.rs:115-128`, `data/src/strategy/detectors/vwap_value_pullback_continuation.rs:182-195`).
- En target helper: `volume_profile.hvn_nearby`, `volume_profile.vah`, `volume_profile.val`, `orderbook.walls_above`, `orderbook.walls_below` (`data/src/strategy/detectors/vwap_value_pullback_continuation.rs:227-269`).

No lee `volume_profile.poc`, `volume_profile.lvn_nearby`, OBI, thin zones, `flow.taker_imbalance`, `flow.vpin`, `flow.footprint_absorption`, `flow.stacked_imbalance`, `flow.sweep_confirmed`, `flow.mss_active`, `flow.oi_delta`, `flow.oi_momentum_aligned`, `market_structure`, `session`, ni `leverage`.

### VAFA (`ValueAreaFailedAuction`)

Lee estos campos reales:

- `ctx.price`, `ctx.atr`, `ctx.volume_profile`, `ctx.flow`, `ctx.orderbook` (`data/src/strategy/detectors/value_area_failed_auction.rs:6-10`).
- `volume_profile.vah`, `volume_profile.val`, `volume_profile.poc` (`data/src/strategy/detectors/value_area_failed_auction.rs:12-14`).
- `ctx.regime` para `Chop` estricto y para la senal (`data/src/strategy/detectors/value_area_failed_auction.rs:18`, `data/src/strategy/detectors/value_area_failed_auction.rs:67`, `data/src/strategy/detectors/value_area_failed_auction.rs:130`).
- `flow.failed_acceptance`, `flow.delta`, `flow.footprint_absorption` (`data/src/strategy/detectors/value_area_failed_auction.rs:25-27`, `data/src/strategy/detectors/value_area_failed_auction.rs:89-91`).
- `flow.cvd_slope`, `flow.taker_imbalance`, `flow.delta / atr` en modo Chop (`data/src/strategy/detectors/value_area_failed_auction.rs:31-36`, `data/src/strategy/detectors/value_area_failed_auction.rs:94-99`).
- `orderbook.spread_bps`, `orderbook.thin_zone_above`, `orderbook.thin_zone_below` (`data/src/strategy/detectors/value_area_failed_auction.rs:39`, `data/src/strategy/detectors/value_area_failed_auction.rs:102`).
- `flow.basis` via `adapter::basis_ok` (`data/src/strategy/detectors/value_area_failed_auction.rs:41`, `data/src/strategy/detectors/value_area_failed_auction.rs:104`).
- `ctx.order_blocks.nearest_bearish/nearest_bullish` solo como evidencia (`data/src/strategy/detectors/value_area_failed_auction.rs:58-61`, `data/src/strategy/detectors/value_area_failed_auction.rs:121-124`).
- `ctx.timestamp_ms` para la senal (`data/src/strategy/detectors/value_area_failed_auction.rs:79`, `data/src/strategy/detectors/value_area_failed_auction.rs:142`).

No lee VWAP, HVN/LVN, OBI, microprice, walls, `flow.cvd`, `flow.vpin`, `flow.stacked_imbalance`, `flow.sweep_confirmed`, `flow.mss_active`, institucional, `market_structure`, `session`, `fvg`, ni `leverage`.

### LVN (`LvnLiquidityVacuumBreakout`)

Lee estos campos reales:

- `ctx.price`, `ctx.atr`, `ctx.volume_profile`, `ctx.vwap`, `ctx.flow`, `ctx.orderbook` (`data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:25-30`).
- `orderbook.thin_zone_above`, `orderbook.thin_zone_below` (`data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:33`, `data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:103`).
- `vwap.price_vs_vwap`, `vwap.vwap_session` (`data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:34`, `data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:61`, `data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:104`, `data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:131`).
- `volume_profile.value_location`, `volume_profile.hvn_nearby`, `volume_profile.vah`, `volume_profile.val` (`data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:35-38`, `data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:54-56`, `data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:105-108`, `data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:124-126`).
- `flow.delta`, `flow.cvd_slope`, `flow.stacked_imbalance`, `flow.taker_imbalance` (`data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:40-46`, `data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:110-116`).
- `orderbook.spread_bps`, `orderbook.microprice`, `flow.ask_wall_nearby`, `flow.bid_wall_nearby` (`data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:49-51`, `data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:119-121`).
- `flow.basis` via `adapter::basis_ok` (`data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:53`, `data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:123`).
- `ctx.regime`, `ctx.timestamp_ms` para la senal (`data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:77-96`, `data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:147-166`).

No lee `volume_profile.lvn_nearby`. Esto es importante: la estrategia se llama LVN, el contexto tiene `lvn_nearby`, pero el detector no lo usa. Tampoco lee OBI, `flow.cvd`, `flow.vpin`, `flow.footprint_absorption`, `flow.failed_acceptance`, institucional, `market_structure`, `session`, `order_blocks`, `fvg`, ni `leverage`.

### LiqHunt (`LiquidationHunt`)

Lee estos campos reales:

- `ctx.price`, `ctx.atr`, `ctx.flow`, `ctx.volume_profile`, `ctx.orderbook` (`data/src/strategy/detectors/liquidation_hunt.rs:10-14`).
- Long: `inst.liquidations.short_liq_usd_5m`, `inst.liquidations.dominant_side` (`data/src/strategy/detectors/liquidation_hunt.rs:17-18`).
- Long: `inst.oi_trend.slope_5bar`, `inst.taker_ratio.taker_imbalance`, `flow.cvd_slope` (`data/src/strategy/detectors/liquidation_hunt.rs:20-26`).
- Long: `orderbook.thin_zone_above` (`data/src/strategy/detectors/liquidation_hunt.rs:28`).
- Long: `inst.funding.regime`, `inst.ls_ratio.divergence_signal` (`data/src/strategy/detectors/liquidation_hunt.rs:31-38`).
- Long: `inst.liquidations.long_liq_usd_5m` para evitar cascada opuesta (`data/src/strategy/detectors/liquidation_hunt.rs:40`).
- Long target: `volume_profile.hvn_nearby`, `volume_profile.vah`, `orderbook.walls_above` (`data/src/strategy/detectors/liquidation_hunt.rs:46-50`).
- Long evidencia: `inst.liq_map.primary_target_above`, `inst.liq_map.density_above` (`data/src/strategy/detectors/liquidation_hunt.rs:67-72`).
- Short: `inst.liquidations.long_liq_usd_5m`, `inst.liquidations.dominant_side` (`data/src/strategy/detectors/liquidation_hunt.rs:95-96`).
- Short: `inst.oi_trend.slope_5bar`, `inst.taker_ratio.taker_imbalance`, `flow.cvd_slope` (`data/src/strategy/detectors/liquidation_hunt.rs:98-104`).
- Short: `orderbook.thin_zone_below` (`data/src/strategy/detectors/liquidation_hunt.rs:106`).
- Short: `inst.funding.regime`, `inst.ls_ratio.divergence_signal` (`data/src/strategy/detectors/liquidation_hunt.rs:108-115`).
- Short: `inst.liquidations.short_liq_usd_5m` para evitar cascada opuesta (`data/src/strategy/detectors/liquidation_hunt.rs:116`).
- Short target: `volume_profile.hvn_nearby`, `volume_profile.val`, `orderbook.walls_below` (`data/src/strategy/detectors/liquidation_hunt.rs:122-126`).
- Short evidencia: `inst.liq_map.primary_target_below`, `inst.liq_map.density_below` (`data/src/strategy/detectors/liquidation_hunt.rs:143-148`).
- `ctx.regime`, `ctx.timestamp_ms` para la senal (`data/src/strategy/detectors/liquidation_hunt.rs:77-89`, `data/src/strategy/detectors/liquidation_hunt.rs:153-165`).

No lee `flow.delta`, `flow.taker_imbalance`, OBI, microprice, spread, `volume_profile.lvn_nearby`, `inst.liquidations.total_usd_5m`, `inst.liquidations.cascade_detected`, `inst.liquidations.last_event_ms`, `inst.oi_trend.current`, `inst.oi_trend.change_30m`, `inst.oi_trend.trend`, `inst.funding.current`, `inst.funding.avg`, `inst.smart_money_score`, `market_structure`, `session`, `order_blocks`, `fvg`, ni `leverage`.

## 6. Datos por WebSocket que no pasan al contexto

Streams creados en monitor: kline, depth, trades (`crates/monitor/src/main.rs:1871-1894`) y liquidaciones `@forceOrder` (`crates/monitor/src/main.rs:1954-1957`).

No pasan al contexto como datos crudos:

- Kline WS crudo: `KlineReceived` se usa para decidir cierre de barra y llamar `on_bar_close` (`crates/monitor/src/main.rs:2005-2028`). Al contexto no llegan `open`, `high`, `low`, `close`, `volume`, `is_closed` como campos crudos; llegan derivados como `price`, `atr`, `regime`, `volume_profile`, `vwap`.
- Depth WS crudo: `DepthReceived` guarda `Depth` completo en `self.depth` (`crates/monitor/src/main.rs:2043-2048`), pero el contexto recibe solo `OrderBookContext` (`crates/monitor/src/main.rs:1011-1026`). Se omiten bids/asks completos, cantidades por nivel y el historial tipo heatmap.
- Trades WS crudo: cada trade trae `is_sell`, `qty`, `price`; `on_trade` solo actualiza `current_price`, `bar_buy_vol`, `bar_sell_vol`, `cvd` y contadores (`crates/monitor/src/main.rs:496-510`, `crates/monitor/src/main.rs:2058-2071`). Se omite la lista de trades y el footprint por precio.
- Liquidation WS crudo: `parse_force_order_event` convierte el mensaje a `LiquidationEvent { timestamp_ms, side, quantity_usd }` (`crates/monitor/src/main.rs:1638-1652`) y `on_liquidation` lo mete al tracker (`crates/monitor/src/main.rs:490-494`). El contexto recibe solo el `LiquidationSnapshot` agregado de 5 minutos (`crates/monitor/src/main.rs:1058-1060`, `crates/monitor/src/main.rs:1076-1086`), no los eventos individuales.
- Estado/frescura de streams: `streams` y `freshness` se loguean (`crates/monitor/src/main.rs:1237-1275`), pero no existen en `StrategyMarketContext`.

## 7. Datos por REST que no se usan en ningun detector

REST en monitor:

- `premiumIndex` devuelve `(funding_rate, mark_price)` (`crates/monitor/src/main.rs:1495-1524`). `mark_price` se descarta explicitamente con `let _ = mark_price` (`crates/monitor/src/main.rs:2074-2084`). No existe `mark_price` en `StrategyMarketContext` y ningun detector lo lee.
- Klines historicas de warmup parsean solo indices `0`, `2`, `3`, `4`, `5`, `9` (`crates/monitor/src/main.rs:1762-1767`). El `open` real del REST no se parsea; el `Kline` sintetico pone `open` igual a `close` (`crates/monitor/src/main.rs:1793-1799`). Los campos REST no parseados tampoco llegan a detectores.
- L/S REST parsea `longAccount`, `shortAccount`, `longShortRatio`, `timestamp` (`crates/monitor/src/main.rs:1550-1562`, `crates/monitor/src/main.rs:1565-1578`). Pero `LsRatioTracker::snapshot()` usa solo `long_ratio` y `source`; `short_ratio`, `ls_ratio` y `timestamp_ms` no llegan al contexto de detector (`data/src/institutional/ls_ratio_tracker.rs:20-56`).
- Taker REST parsea `buySellRatio`, `buyVol`, `sellVol`, `timestamp` (`crates/monitor/src/main.rs:1581-1597`). Los volumenes `buyVol`/`sellVol` solo se usan para calcular `taker_imbalance`; no llegan como volumenes crudos al contexto institucional. `timestamp_ms` tampoco lo lee ningun detector de los cuatro auditados.

Datos REST que si se usan indirectamente:

- `spot_price` se usa para calcular `basis` (`crates/monitor/src/main.rs:900-907`), y los cuatro detectores auditados usan `flow.basis` via `adapter::basis_ok` en sus gates.
- `openInterest` alimenta `oi_history` y `OiTracker` (`crates/monitor/src/main.rs:2093-2105`); LiqHunt usa `inst.oi_trend.slope_5bar`.
- Funding rate e historial alimentan `FundingTracker` (`crates/monitor/src/main.rs:2074-2083`, `crates/monitor/src/main.rs:2149-2153`); VVPC usa `inst.funding.regime` y LiqHunt tambien usa `inst.funding.regime`.

## 8. Por que LiqHunt puede mostrar `liq=0$` todo el dia

No es el umbral de `$500k`.

El log de barra imprime `liq={:.0}$` desde `inst_ref.map(|i| i.liquidations.total_usd_5m).unwrap_or(0.0)` (`crates/monitor/src/main.rs:1259-1271`). Ese valor sale de `liq_tracker.snapshot(bar_ms)` (`crates/monitor/src/main.rs:1058-1060`). El tracker calcula `total = long_usd + short_usd`; si no hay eventos dentro de la ventana de 5 minutos, `total` queda `0.0` (`data/src/institutional/liquidation_tracker.rs:32-75`).

Los eventos solo entran si el WS `@forceOrder` parsea un evento y lo manda al canal (`crates/monitor/src/main.rs:1690-1691`), y luego `on_liquidation` llama `self.liq_tracker.push(event)` (`crates/monitor/src/main.rs:490-494`). Por tanto, `liq=0$` todo el dia significa una de estas condiciones reales:

- feed `@forceOrder` sin eventos durante cada ventana de 5 minutos,
- feed desconectado o fallando,
- parseo sin `Some(LiquidationEvent)`.

El umbral de `$500k` esta en el detector, no en el log. LiqHunt exige `short_liq_usd_5m > cfg.liq_hunt_min_usd` y lado dominante `Shorts` para long (`data/src/strategy/detectors/liquidation_hunt.rs:17-18`), o `long_liq_usd_5m > cfg.liq_hunt_min_usd` y lado dominante `Longs` para short (`data/src/strategy/detectors/liquidation_hunt.rs:95-96`). Si hay `$100k`, el log deberia mostrar `liq=100000$`, pero el detector no dispara.

Nota: el propio codigo dice que el endpoint REST viejo `/fapi/v1/forceOrders` requeria auth y "caused liq=0$ on every bar"; el codigo actual lo reemplaza por el WS publico `@forceOrder` (`crates/monitor/src/main.rs:1599-1602`).

## 9. Donde se construye el contexto y que se omite

En el monitor headless se construye en `crates/monitor/src/main.rs`, no en `monitor/src/kline.rs`.

Constructores reales:

- `BarState::build_intrabar_ctx(...)` construye un `StrategyMarketContext` intrabar (`crates/monitor/src/main.rs:640-750`).
- `BarState::on_bar_close(...)` construye el contexto de barra cerrada (`crates/monitor/src/main.rs:809-1119`).
- Luego se llama `route_strategy(&ctx, &cfg)` (`crates/monitor/src/main.rs:1119`; intrabar en `crates/monitor/src/main.rs:753-757`).

Omisiones en `on_bar_close`:

- `vpin` se pasa como `None` a `build_flow_context` (`crates/monitor/src/main.rs:1035-1042`).
- `stacked_imbalance` se pasa como `ImbalanceSide::Unknown` (`crates/monitor/src/main.rs:1051-1053`).
- `institutional.liq_map` se fija en `None` (`crates/monitor/src/main.rs:1076-1086`).
- `market_structure`, `session`, `order_blocks`, `fvg` se fijan en `None` (`crates/monitor/src/main.rs:1099-1117`).
- `OrderBookContext.spoof` queda `None` porque el builder lo fija asi (`data/src/strategy/adapter.rs:100-112`).
- No se pasa footprint por nivel de precio, DOM completo, heatmap, raw trades, raw liquidations, ni `mark_price` porque no hay campos para eso.

Omisiones intrabar:

- `vpin` tambien `None` (`crates/monitor/src/main.rs:685-692`).
- `stacked_imbalance` tambien `ImbalanceSide::Unknown` (`crates/monitor/src/main.rs:701-703`).
- `institutional.liq_map` tambien `None` (`crates/monitor/src/main.rs:720-730`).
- `market_structure`, `session`, `order_blocks`, `fvg` tambien `None` (`crates/monitor/src/main.rs:732-749`).

En la UI/chart si hay otro constructor en `src/chart/kline.rs`: crea `market_structure`, `session`, `order_blocks`, `fvg` y `liq_map` (`src/chart/kline.rs:1368-1443`) y luego arma `StrategyMarketContext` (`src/chart/kline.rs:1448-1466`). Ese no es `monitor/src/kline.rs`.

## 10. Cambios de menor esfuerzo para conectar datos existentes

1. Usar `volume_profile.lvn_nearby` en LVN. El campo existe y se construye (`data/src/strategy/types.rs:94-95`, `data/src/strategy/adapter.rs:524-540`), pero el detector LVN no lo lee; usa `hvn_nearby`, `vah`, `val` como targets (`data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:54-56`, `data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:124-126`). Es el cambio mas obvio para una estrategia LVN.

2. Usar OBI en LVN y LiqHunt. `obi_l5/l10/l20` ya llegan al contexto (`data/src/strategy/adapter.rs:35-55`, `data/src/strategy/adapter.rs:100-104`), pero ninguno de los cuatro detectores auditados los lee. Gate simple: long requiere OBI no fuertemente negativo; short requiere OBI no fuertemente positivo.

3. Calcular `stacked_imbalance` real en monitor. LVN ya lee `flow.stacked_imbalance` (`data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:40-46`, `data/src/strategy/detectors/lvn_liquidity_vacuum_breakout.rs:110-116`), pero monitor le pasa `ImbalanceSide::Unknown` (`crates/monitor/src/main.rs:1051-1053`, `crates/monitor/src/main.rs:701-703`). Conservar un `bar_delta_history` y llamar `derive_stacked_imbalance` ya existente (`data/src/strategy/adapter.rs:593-614`).

4. Conectar `LiqMapTracker` en monitor. `InstitutionalContext` tiene `liq_map` (`data/src/institutional/types.rs:204-207`) y LiqHunt ya lo lee como evidencia (`data/src/strategy/detectors/liquidation_hunt.rs:67-72`, `data/src/strategy/detectors/liquidation_hunt.rs:143-148`), pero monitor siempre lo pone `None` (`crates/monitor/src/main.rs:1085`, `crates/monitor/src/main.rs:729`). La UI ya muestra el patron de actualizacion con precio, swings y OI (`src/chart/kline.rs:1400-1411`).

5. Construir footprint por precio desde trades WS en monitor para VAFA. El trade stream ya trae `price`, `qty`, `is_sell` (`crates/monitor/src/main.rs:2058-2064`), pero monitor solo acumula buy/sell total (`crates/monitor/src/main.rs:496-510`). Reusar `KlineTrades` permitiria derivar absorcion/imbalance por nivel en vez de pasar solo `[bar_delta]` a `derive_failed_acceptance_and_absorption` (`crates/monitor/src/main.rs:895-898`). El detector VAFA es el que mas lo necesita porque gatea por `flow.footprint_absorption` (`data/src/strategy/detectors/value_area_failed_auction.rs:25-27`, `data/src/strategy/detectors/value_area_failed_auction.rs:89-91`).
