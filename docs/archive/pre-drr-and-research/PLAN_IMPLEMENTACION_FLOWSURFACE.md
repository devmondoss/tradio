# FlowSurface — Plan de Implementación
## Strategy Lab + Core Engine v2

> **Documento historico.** Este plan fue superado por la fase DRR-only del 2026-05-29. No usarlo como backlog actual; ver [DRR_PRESENTE_Y_FUTURO.md](../../DRR_PRESENTE_Y_FUTURO.md).

> Documento para Claude Code. Actualizado: Mayo 2026.
> Capital: $300. Par: BTCUSDT Perp Binance. Modo: Shadow/Paper únicamente.

---

## Estado rápido por fase

| Fase | Descripción | Estado |
|------|-------------|--------|
| 0 | Tipos base + módulo Lab + tablas Supabase | ✅ COMPLETA |
| 1 | VVPC gates + VWAPRejection + DIB persistencia | ✅ COMPLETA |
| 2 | Lab detectors + runner + writer | ✅ COMPLETA |
| 3 | OI z-score + footprint por nivel + order blocks | ✅ COMPLETA |
| 3b | Bug FK lab_outcomes + limpieza LabStrategyId enum | ✅ COMPLETA |
| 4 | Análisis Python + primera evaluación de promoción | 🔶 PARCIAL |

---

## Fase 0 — Tipos base y módulo Lab ✅

Todo construido. No hay deuda pendiente.

| Entregable | Archivo | Estado |
|---|---|---|
| `StrategyMaturity` enum | `data/src/strategy/lab/types.rs` | ✅ |
| `StrategyRuntimeStatus` enum | `data/src/strategy/lab/types.rs` | ✅ |
| `LabStrategyId` enum | `data/src/strategy/lab/types.rs` | ✅ |
| `LabSignal` struct | `data/src/strategy/lab/types.rs` | ✅ |
| `LabFeatureSnapshot` con snapshot jsonb | `data/src/strategy/lab/types.rs` | ✅ |
| `LabOutcome` con horizontes 5m/15m/TTL/MFE/MAE | `data/src/strategy/lab/types.rs` | ✅ |
| `LabConfig` struct (enabled: false por defecto) | `data/src/strategy/lab/types.rs` | ✅ |
| Lab writer (write_lab_signal / write_lab_outcome) | `data/src/strategy/lab/writer.rs` | ✅ |
| Lab outcome tracker multi-horizonte | `data/src/strategy/lab/tracker.rs` | ✅ |
| `lab/mod.rs` runner paralelo | `data/src/strategy/lab/mod.rs` | ✅ |
| Tablas `lab_signals` + `lab_outcomes` en SQL | `supabase/reset.sql` + `supabase/schema.sql` | ✅ |

**Nota**: Las tablas están en los archivos SQL pero deben ejecutarse en Supabase antes de que el Lab pueda escribir. Ver sección de Deploy.

---

## Fase 1 — VVPC gates + VWAPRejection + DIB persistencia ✅

Todo construido. No hay deuda pendiente.

| Tarea | Archivo | Estado |
|---|---|---|
| `taker_imbalance > 0` como gate Long en VVPC | `vwap_value_pullback_continuation.rs` | ✅ |
| `obi_l5 >= 0` como gate Long en VVPC | mismo | ✅ |
| `taker_imbalance < 0` + `obi_l5 <= 0` gates Short | mismo | ✅ |
| `prev_obi_l5: f64` en `BarState` del monitor | `crates/monitor/src/main.rs` | ✅ |
| `prev_obi_l5` como condición de persistencia en DIB | `dom_imbalance_breakout.rs` | ✅ |
| VWAPRejection como detector Lab separado (ShadowLab) | `data/src/strategy/lab/detectors/vwap_rejection.rs` | ✅ |

---

## Fase 2 — Lab detectors + runner + writer ✅

Todo construido. El Lab corre en paralelo al Core sin bloquearlo.

| Entregable | Archivo | Maturity | Estado |
|---|---|---|---|
| VwapRejection | `lab/detectors/vwap_rejection.rs` | ShadowLab | ✅ |
| AbsorptionTrapReversal | `lab/detectors/absorption_trap_reversal.rs` | ObserveOnly | ✅ (Asleep sin footprint_levels) |
| SessionImbalanceBreakout | `lab/detectors/session_imbalance_breakout.rs` | ObserveOnly | ✅ |
| LiquidityMagnet | `lab/detectors/liquidity_magnet.rs` | ObserveOnly | ✅ |
| OrderBlockFlowRetest | `lab/detectors/order_block_flow_retest.rs` | ObserveOnly | ✅ (Asleep sin order_blocks) |
| positioning_adjustment() | `lab/detectors/positioning_expansion.rs` | función auxiliar | ✅ |
| Lab runner (`run_strategy_lab`) conectado al monitor | `crates/monitor/src/main.rs` | — | ✅ |
| Lab writer llamado en cada barra (fire-and-forget) | mismo | — | ✅ |
| LabTracker llamado en cada barra | mismo | — | ✅ |

**Dos detectores en Asleep por datos faltantes** (correcto — `missing_data` documenta el motivo):
- `AbsorptionTrapReversal` → falta `footprint_levels` acumulado por precio (Fase 3)
- `OrderBlockFlowRetest` → falta `order_blocks` en el monitor (Fase 3)

---

## Fase 3 — Datos más finos ✅ COMPLETA

| Entregable | Impacto | Estado |
|---|---|---|
| OI z-score en `OiTracker` (`delta_zscore()`) | Scoring institucional más preciso | ✅ |
| `oi_delta_zscore` en `OrderFlowContext` + `LabFeatureSnapshot` | wired through adapter, types, monitor, lab | ✅ |
| Footprint por nivel en monitor | AbsorptionTrap + FAR usan datos reales | ✅ ya existía |
| OrderBlockDetector conectado en monitor | OBR usa datos reales | ✅ ya existía |
| Funding rate panel multi-exchange (UI) | FER mejora en el Lab | ❌ fuera de scope actual |

**Nota**: footprint_levels y order_blocks ya estaban completamente implementados.
- `bar_footprint.add_trade_to_nearest_bin()` acumula en cada trade WS (`main.rs:650`)
- `current_footprint_levels()` convierte a `Vec<FootprintLevel>` y se asigna en `main.rs:1343`
- `ob_detector.push_bar()` + `ob_detector.snapshot(c)` se llaman en `main.rs:1347-1348`
- `order_blocks: Some(order_blocks)` se pasa al contexto en `main.rs:1436`

## Fase 3b — Bug FK + limpieza enum ✅ COMPLETA

| Entregable | Estado |
|---|---|
| FK bug corregido: `"id"` incluido en `build_lab_signal_row` | ✅ |
| `LabStrategyId::PositioningExpansion` eliminado del enum | ✅ |
| `LabStrategyId` limpio: 5 variants activos (VwapRejection + 4 ObserveOnly) | ✅ |
| `positioning_adjustment()` permanece como función auxiliar en `positioning_expansion.rs` | ✅ |

**El bug**: `build_lab_signal_row` no enviaba `"id"` en el body → Supabase generaba su propio UUID → `lab_outcomes.signal_id` referenciaba un UUID inexistente → FK violation silenciosa → outcomes nunca linkados a sus señales.

---

## Fase 4 — Análisis Python + primera evaluación de promoción 🔶 PARCIAL

| Entregable | Estado |
|---|---|
| `scripts/analysis/analyze_lab_outcomes.py` | ✅ DONE |
| `scripts/analysis/compare_core_vs_lab.py` | ✅ DONE |
| `scripts/analysis/score_decay.py` | ✅ DONE |
| `scripts/analysis/session_filter_analysis.py` | ✅ DONE |
| Primera evaluación de promoción VwapRejection | ❌ PENDIENTE (necesita 100+ señales) |

Los scripts están listos para ejecutarse. Requieren que el Lab esté habilitado
y acumulando datos en Supabase. Sin datos, retornan vacío.

---

## Para activar el Lab en producción

**Pre-requisito 1** — Ejecutar SQL en Supabase:
```sql
-- En Supabase SQL Editor, ejecutar supabase/reset.sql completo
-- O solo las secciones de lab_signals y lab_outcomes de schema.sql
```

**Pre-requisito 2** — Habilitar el Lab en el monitor:
```rust
// En crates/monitor/src/main.rs, BarState::new():
lab_cfg: LabConfig { enabled: true, ..Default::default() }

// O cargarlo desde env:
lab_cfg: LabConfig {
    enabled: std::env::var("LAB_ENABLED").map(|v| v == "true").unwrap_or(false),
    ..Default::default()
}
```

Una vez habilitado, cada barra M5 cerrada genera hasta 5 filas en `lab_signals`.
Verificar con:
```sql
SELECT strategy_id, status, count(*)
FROM lab_signals
GROUP BY 1, 2
ORDER BY 1, 2;
```

---

## Criterios de promoción — no negociables

| Transición | Señales mínimas | Criterio cuantitativo |
|---|---|---|
| ObserveOnly → ShadowLab | N/A | Datos mínimos disponibles sin dummy fields |
| ShadowLab → PaperCandidate | 100+ | RR promedio ≥ 1.3, MFE/MAE ratio > 1.5 |
| PaperCandidate → PaperPromoted | 300+ | Expectancy positiva, profit factor > 1.2, thresholds robustos ±20% |
| PaperPromoted → CoreActive | 500+ | Drawdown controlado, baja correlación negativa con Core |

**VwapRejection** (ShadowLab) es el primer candidato a evaluar.
Con `enabled: true` en producción, acumula ~288 señales/día (1 por barra M5).
En ~2 semanas habrá suficientes datos para la primera evaluación de promoción.

---

## Reglas de separación Core / Lab — no negociables

| Acción | Core | Lab |
|---|---|---|
| Emitir ShadowSignal al router principal | ✓ | ✗ |
| Escribir en `paper_trades` / `shadow_signals` | ✓ | ✗ |
| Bloquear otras estrategias (cooldown) | ✓ | ✗ |
| Evaluar en paralelo sin bloqueo | ✗ | ✓ siempre |
| Modificar score del Core | ✗ | ✗ nunca |
| Escribir en `lab_signals` / `lab_outcomes` | ✗ | ✓ |

---

## Arquitectura — dos carriles

```
StrategyMarketContext (on_bar_close, cada 5 min)
        │
   ┌────┴────┐
   │         │
CORE      LAB (run_strategy_lab)
decide    observa
   │         │
shadow_signals    lab_signals
signal_outcomes   lab_outcomes
```

---

## Lo que NO hacer

| Prohibido | Motivo |
|---|---|
| Live trading | Modo investigación únicamente |
| Optimizar thresholds con < 100 señales | Sobreajuste garantizado |
| Declarar edge con < 200 señales | Sin significancia estadística |
| Lab escribir en `shadow_signals` | Mezcla distribuciones distintas |
| Promover sin evidencia cuantitativa | El mercado no premia la ansiedad |
| Habilitar Lab sin crear tablas en Supabase | Panics en el writer |

---

## Resumen — qué falta

```
COMPLETADO:
  ✅ Fases 0–3b completas
  ✅ FK bug corregido (signal_id ahora se linkea correctamente en lab_outcomes)
  ✅ LabStrategyId enum limpio (5 variants activos)
  ✅ Lab activo en Railway (LAB_ENABLED=true + redeploy)
  ✅ SQL ejecutado en Supabase

LO ÚNICO PENDIENTE (Fase 4):
  1. Redeploy en Railway para que el fix de FK entre en producción
  2. Esperar ~2 semanas de datos acumulados en lab_signals
  3. Primera evaluación de VwapRejection con 100+ señales:
     → python scripts/analysis/analyze_lab_outcomes.py --strategy VwapRejection --days 14
     → python scripts/analysis/score_decay.py --strategy VwapRejection
     → python scripts/analysis/session_filter_analysis.py --strategy VwapRejection
  4. Si RR >= 1.3, MFE/MAE > 1.5 y 100+ señales → promover a PaperCandidate
```
