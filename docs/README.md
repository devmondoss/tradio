# Documentacion canonica

Ultima revision: 2026-06-02

Este indice existe para evitar leer documentacion historica como si fuera el estado actual del sistema. Para proximas revisiones, empezar siempre por los documentos canonicos.

## Leer primero

| Documento | Uso |
| --- | --- |
| [DRR_PRESENTE_Y_FUTURO.md](DRR_PRESENTE_Y_FUTURO.md) | Estado presente y futuro del sistema. Puerta de entrada para nuevas sesiones. |
| [ESTADO_CHECKLIST.md](ESTADO_CHECKLIST.md) | Checklist tecnico por capas. Inventario detallado del sistema. |
| [BUILD.md](BUILD.md) | Setup local de build en Windows. |

## Referencias oficiales

| Documento | Uso |
| --- | --- |
| [ARQUITECTURA.md](ARQUITECTURA.md) | Arquitectura general de la app. |
| [DATOS.md](DATOS.md) | Flujo de datos de exchange, WebSocket, REST y agregacion. |
| [INGESTA_DATOS.md](INGESTA_DATOS.md) | Mapa amplio de ingesta y campos disponibles. |
| [CHARTS.md](CHARTS.md) | Charts disponibles y notas de UI/rendering. |
| [RENDERING.md](RENDERING.md) | Pipeline de rendering. |
| [INDICATORS.md](INDICATORS.md) | Indicadores y formulas. |
| [HEALTH_MONITORING_BAR_LOG.md](HEALTH_MONITORING_BAR_LOG.md) | Observabilidad de streams y bar logs. |
| [BUGS_Y_FIXES.md](BUGS_Y_FIXES.md) | Historial oficial de bugs corregidos. |

## Estado vigente (2026-06-02)

- **Motor activo:** Scalping S1/S2/S3 (circuit breaker activo, pendiente fix de target) + RangeBreakoutFlow (pendiente deploy).
- **DRR:** desactivado. Tablas `shadow_signals` y `signal_outcomes` eliminadas de Supabase.
- **DB activa:** `scalping_bars`, `scalping_signals`, `scalping_trades`, `regime_history`, `micro_windows`.
- **Pendiente:** correr `supabase/migration_rbf.sql` y hacer `git push` para activar RangeBreakoutFlow en Railway.
- **Fase actual:** validacion en vivo del detector RBF validado en backtest (30 dias M1).

## Archivo historico

Los documentos obsoletos o de investigacion previa estan en [archive/pre-drr-and-research/](archive/pre-drr-and-research/). No usarlos como fuente de verdad.

## Regla para mantener docs

Cuando cambie el sistema live, actualizar primero [DRR_PRESENTE_Y_FUTURO.md](DRR_PRESENTE_Y_FUTURO.md) y despues este indice. No crear otro documento de "estado actual".
