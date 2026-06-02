# Subdimi-only + Playbook Reasoning - Implementacion

> **Documento historico.** Registra el paso intermedio Subdimi-only del 2026-05-24. El estado live actual es DRR-only; ver [DRR_PRESENTE_Y_FUTURO.md](../../DRR_PRESENTE_Y_FUTURO.md).

Fecha de trabajo: 2026-05-24 (America/Lima)

Este documento registra lo que se cambio realmente en el codigo, que comportamiento queda activo, que queda solo como observacion para backtesting y que pasos operativos faltan para que produccion refleje el cambio.

## Contexto

Se detecto en Supabase que el sistema seguia emitiendo senales del router original:

- `VwapValuePullbackContinuation`
- `DomImbalanceBreakout`
- `LiquidationHunt`

Eso confirmo que el sistema no estaba restringido a una estrategia Subdimi-only. La capa de `playbook_reasoning` que se habia agregado antes solo observaba y etiquetaba, pero no bloqueaba detectores.

La correccion implementada tiene dos partes:

1. Mantener `playbook_reasoning` como observador para backtesting.
2. Restringir el router live a detectores considerados Subdimi.

## Resumen de cambios

Archivos cambiados:

- `data/src/strategy/playbook_reasoning.rs`
- `data/src/strategy/mod.rs`
- `data/src/strategy/router.rs`
- `data/src/strategy/logger.rs`
- `crates/monitor/src/main.rs`
- `crates/monitor/src/supabase_writer.rs`
- `src/chart/kline.rs`
- `supabase/reset.sql`
- `supabase/schema.sql`
- `docs/PLAYBOOK_REASONING_BACKTEST_PLAN.md`

Archivos/directorios no tocados intencionalmente:

- `orderflow/`
- Codigo de detectores individuales como `vwap_value_pullback_continuation.rs`, `dom_imbalance_breakout.rs`, etc.
- Codigo Mongo como fuente principal. Mongo queda como compatibilidad local historica, pero la persistencia real para esta fase es Supabase.

## 1. Playbook Reasoning Fase 1

Archivo nuevo:

- `data/src/strategy/playbook_reasoning.rs`

Se agrego un clasificador hardcodeado:

```rust
classify_playbook_reasoning(ctx, cfg, signal, detector_log) -> PlaybookReasoning
```

### Que hace

Lee:

- `StrategyMarketContext`
- `StrategyConfig`
- `StrategySignal`
- `detector_log`

Produce:

- `version`
- `primary_playbook`
- `secondary_playbooks`
- `market_state`
- `location_tags`
- `flow_tags`
- `liquidity_tags`
- `book_tags`
- `institutional_tags`
- `structure_tags`
- `trigger_tags`
- `risk_tags`
- `confirmation_tags`
- `contradiction_tags`
- `missing_tags`
- `detector_role_tags`
- `confidence`
- `completeness`

### Que NO hace

No cambia comportamiento live:

- No bloquea senales.
- No cambia `score`.
- No cambia `min_score`.
- No decide entrada, stop ni target.
- No reemplaza al router.
- No hace el sistema mas conservador por si solo.

Su objetivo es recolectar data para backtesting y entender bajo que razonamiento funciona o falla cada setup.

## 2. Vocabulario / playbooks contemplados

El clasificador contempla multiples tipos de razonamiento, incluyendo:

- `trapped_traders_reversal`
- `failed_auction_reversal`
- `footprint_absorption_reversal`
- `liquidity_sweep_reversal`
- `cvd_absorption_reversal`
- `funding_exhaustion_reversal`
- `smart_money_fade`
- `order_block_absorption_reversal`
- `value_extreme_rejection`
- `imbalance_continuation`
- `vwap_pullback_continuation`
- `lvn_vacuum_breakout`
- `dom_breakout`
- `session_open_breakout`
- `order_block_continuation`
- `fvg_rebalance`
- `trend_day_continuation`
- `thin_book_momentum`
- `value_rotation`
- `poc_magnet`
- `liquidity_magnet`
- `institutional_exhaustion`
- `oi_expansion_trend`
- `mixed_context`
- `data_insufficient`
- `no_clear_playbook`
- `possible_noise`
- `conflicting_signals`

Importante: aunque el vocabulario incluye playbooks no Subdimi puros, eso es para backtesting y diagnostico. No significa que todos puedan emitir senales live.

## 3. Integracion en monitor live

Archivo:

- `crates/monitor/src/main.rs`

Se cambio el flujo despues de `route_strategy`.

Antes:

```rust
let (signal, _) = route_strategy(&ctx, &cfg);
```

Ahora:

```rust
let (signal, detector_log) = route_strategy(&ctx, &cfg);
let reasoning = classify_playbook_reasoning(&ctx, &cfg, &signal, &detector_log);
```

Se integro en dos caminos:

- Evaluacion intrabar.
- Evaluacion al cierre de vela.

En ambos casos el reasoning se calcula despues del router, usando el resultado real y el log de detectores.

### Persistencia live

En monitor live, el reasoning se envia a Supabase con:

```rust
write_signal_with_reasoning(&signal, &ctx, &reasoning)
```

El objetivo es que cada row nuevo de `shadow_signals` tenga campos de reasoning.

## 4. Integracion en UI local

Archivo:

- `src/chart/kline.rs`

Se agrego calculo de reasoning despues del cooldown local de UI:

```rust
let reasoning = classify_playbook_reasoning(&ctx, &cfg, &signal, &detector_log);
logger::log_signal_with_reasoning(&ctx, &signal, &reasoning);
```

Esto afecta logs locales JSONL.

Nota: la UI local mantiene su escritura Mongo existente para compatibilidad, pero el reasoning no se adapto como fuente principal en Mongo. El sistema operativo actual usa Supabase para analisis.

## 5. Logger local

Archivo:

- `data/src/strategy/logger.rs`

Se agrego:

```rust
log_signal_with_reasoning(ctx, signal, reasoning)
```

El logger anterior sigue existiendo:

```rust
log_signal(ctx, signal)
```

Esto mantiene compatibilidad. La nueva version agrega:

```rust
playbook_reasoning: Option<PlaybookReasoning>
```

en los JSONL locales.

## 6. Supabase writer

Archivo:

- `crates/monitor/src/supabase_writer.rs`

Se agrego:

```rust
write_signal_with_reasoning(...)
```

El writer ahora envia reasoning a Supabase en dos niveles.

### Columnas directas en `shadow_signals`

Se envian:

- `reasoning_version`
- `primary_playbook`
- `secondary_playbooks`
- `reasoning_tags`
- `reasoning_confidence`
- `reasoning_completeness`

### Blob flexible en `subdomi_ctx`

Tambien se guarda:

- `subdomi_ctx.playbook_reasoning`
- `subdomi_ctx.primary_playbook`
- `subdomi_ctx.reasoning_confidence`
- `subdomi_ctx.reasoning_completeness`

Esto sirve como respaldo para analisis flexible si mas adelante aparecen tags nuevos y no se quiere migrar schema inmediatamente.

## 7. Supabase schema y reset

Archivos:

- `supabase/reset.sql`
- `supabase/schema.sql`

Se agregaron columnas en `shadow_signals`:

```sql
reasoning_version       TEXT,
primary_playbook        TEXT,
secondary_playbooks     JSONB,
reasoning_tags          JSONB,
reasoning_confidence    DOUBLE PRECISION,
reasoning_completeness  DOUBLE PRECISION,
```

Se agrego indice:

```sql
CREATE INDEX idx_signals_playbook
ON shadow_signals(primary_playbook, reasoning_confidence, timestamp_ms DESC);
```

La vista `v_signals_with_outcomes` ahora expone:

- `reasoning_version`
- `primary_playbook`
- `secondary_playbooks`
- `reasoning_tags`
- `reasoning_confidence`
- `reasoning_completeness`

### Importante

`supabase/reset.sql` fue actualizado, pero no fue ejecutado contra Supabase.

Ejecutarlo borra todos los datos, porque es un reset completo. Si solo se quiere migrar sin borrar, hay que crear un `ALTER TABLE` separado.

## 8. Router Subdimi-only

Archivo:

- `data/src/strategy/router.rs`

Se agrego:

```rust
fn subdimi_detector_allowed(id: StrategyId) -> bool
```

### Detectores permitidos live

Solo estos pueden competir en el router:

- `ValueAreaFailedAuction` / `VAFA`
- `FootprintAbsorptionReversal` / `FAR`
- `LvnLiquidityVacuumBreakout` / `LVN`
- `CvdDivergenceReversal` / `CDR`
- `OrderBlockRetest` / `OBR`
- `LiquidationHunt` / `LIQ`

### Detectores bloqueados live

Estos ya no pueden emitir senal:

- `VwapValuePullbackContinuation` / `VWAP`
- `DomImbalanceBreakout` / `DIB`
- `SessionOpenBreakout` / `SOB`
- `FundingExhaustionReversal` / `FER`
- `SmartMoneyDivergence` / `SMD`

### Como se bloquean

El macro `try_detect!` ahora revisa primero:

```rust
if !subdimi_detector_allowed($id) { ... }
```

Si el detector no esta permitido:

- No ejecuta el detector.
- No calcula score.
- No entra a `candidates`.
- No puede ganar el winner-takes-all.
- Se registra en `detector_log` como:

```text
SUBDIMI_ONLY_DISABLED
```

Esto permite diagnosticar que el detector fue bloqueado por modo Subdimi-only y no por falta de condiciones.

## 9. Cambio especial para institucionales

Antes, si `ctx.institutional` era `None`, el router marcaba:

- `LIQ`
- `FER`
- `SMD`

como `GlobalBlocked` por `INST:NULL`.

Ahora:

- `LIQ` sigue marcado como `GlobalBlocked` con `INST:NULL`, porque es Subdimi permitido pero necesita datos institucionales.
- `FER` queda `Skip` con `SUBDIMI_ONLY_DISABLED`.
- `SMD` queda `Skip` con `SUBDIMI_ONLY_DISABLED`.

Esto evita que parezca que `FER` y `SMD` solo esperan datos institucionales. En realidad estan apagados por decision de modo Subdimi-only.

## 10. Que se observo en Supabase antes del cambio

Consulta ejecutada usando `.env` local:

Ventana revisada: ultimas 6 horas.

Conteos:

- `shadow_signals`: 4
- `signal_outcomes`: 5
- `lab_signals`: 0
- `intrabar_outcomes`: 0
- `regime_history`: 6
- `institutional_snapshots`: 0

Senales observadas:

- `VwapValuePullbackContinuation` short, `20:00`
- `DomImbalanceBreakout` long, `20:15`
- `LiquidationHunt` short, `20:55`
- `LiquidationHunt` short, `21:00`

Resultado agregado:

- Aproximadamente `-4.172 R`
- PnL neto aproximado `-17.87`
- 4 filas `STOP_HIT`
- 1 fila `TP1_PARTIAL` con R negativo

Interpretacion:

- El router viejo seguia activo.
- Habia senales que no debian existir si el sistema era Subdimi-only.
- Los campos `primary_playbook` y `reasoning_*` estaban `null` porque esas filas fueron emitidas antes de correr el binario/schema actualizado.

## 11. Verificaciones ejecutadas

Se ejecuto:

```bash
cargo check
```

Resultado:

- Paso correctamente.

Se ejecuto:

```bash
cargo check -p monitor
```

Resultado:

- Paso correctamente.
- Warnings existentes/no bloqueantes:
  - campos no usados en `FrozenBarCtx`
  - metodos wrapper no usados en `SupabaseWriter`

No se ejecutaron tests de backtesting ni simulacion historica completa.

## 12. Que falta para que produccion refleje el cambio

### 1. Migrar Supabase

Opciones:

1. Ejecutar `supabase/reset.sql`
   - Borra todos los datos.
   - Recrea tablas, indices y vista.

2. Crear migracion no destructiva con `ALTER TABLE`
   - Recomendado si se quieren conservar datos.
   - Debe agregar las columnas nuevas y el indice.

### 2. Redeploy / restart del monitor

El monitor live que ya estaba corriendo usa el binario anterior. Para que deje de emitir `VWAP`, `DIB`, `SOB`, `FER`, `SMD`, hay que redeployar o reiniciar con el nuevo codigo.

### 3. Validar Supabase despues del deploy

Consulta esperada:

- `strategy` en `shadow_signals` solo debe contener:
  - `ValueAreaFailedAuction`
  - `FootprintAbsorptionReversal`
  - `LvnLiquidityVacuumBreakout`
  - `CvdDivergenceReversal`
  - `OrderBlockRetest`
  - `LiquidationHunt`

No deberia aparecer:

- `VwapValuePullbackContinuation`
- `DomImbalanceBreakout`
- `SessionOpenBreakout`
- `FundingExhaustionReversal`
- `SmartMoneyDivergence`

### 4. Validar reasoning

Rows nuevos de `shadow_signals` deberian tener:

- `primary_playbook` no null
- `reasoning_confidence` no null
- `reasoning_completeness` no null
- `secondary_playbooks` como JSONB
- `reasoning_tags` como JSONB

## 13. Riesgos / decisiones tomadas

### VWAP

`VWAP` fue bloqueado aunque podia tener confirmaciones order-flow, porque se habia definido como semi-Subdimi, no Subdimi puro.

### FER y SMD

`FER` y `SMD` se bloquearon aunque usan datos institucionales/order-flow, porque en la comparacion previa fueron clasificados como order-flow puros, no Subdimi puro.

### DIB y SOB

`DIB` y `SOB` se bloquearon porque son mas de DOM/session breakout que lectura Subdimi central.

### LIQ

`LIQ` queda permitido porque se decidio tratar la caza de stops/liquidaciones como parte del marco Subdimi operativo actual.

## 14. Estado final del comportamiento

Antes:

```text
Router original winner-takes-all
VAFA, LVN, DIB, SOB, OBR, FAR, VWAP, CDR, LIQ, FER, SMD compiten
```

Ahora:

```text
Router winner-takes-all restringido a Subdimi-only
VAFA, LVN, OBR, FAR, CDR, LIQ compiten
VWAP, DIB, SOB, FER, SMD quedan bloqueados con SUBDIMI_ONLY_DISABLED
PlaybookReasoning observa y persiste metadata para backtesting
```

## 15. Nota sobre docs previos

El archivo `docs/PLAYBOOK_REASONING_BACKTEST_PLAN.md` es el plan conceptual.

Este archivo es la bitacora de implementacion real: que se cambio, que quedo activo y que falta operar.
