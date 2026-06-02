# Estado presente y futuro — FlowSurface

Ultima revision: 2026-06-02

Este es el documento canonico para revisar el proyecto. Resume lo que esta vivo, lo que quedo obsoleto y que decisiones futuras dependen de datos.

---

## Estado presente

El sistema tiene dos motores activos en Railway:

### 1. Scalping S1/S2/S3 (circuit breaker activo)

Motor de scalping M1 con tres estrategias basadas en microestructura del libro de ordenes:

- **S1 OBI Maker** — sesgo de OBI + micro-precio → entry post-only
- **S2 Absorption** — delta Z-score + VR en extremo de rango → reversal
- **S3 CVD Divergence** — divergencia CVD/precio en swings → fade

**Estado actual:** circuit breaker activo despues de 12 trades consecutivos con PnL neto negativo.

**Causa raiz identificada (sesion 2026-06-02):**
- El target de S1 con multiplicador x1.2 era estructuralmente menor que las fees ($34.9 vs $40.3 de break-even). El fix a x1.8 esta en el codigo pero no deployado a Railway aun.
- S1 entra en cierre de barra M5 pero el edge de OBI es de segundos — desajuste de timeframe.
- 6 de 12 trades en sesion Asia antes de que el gate se deployara.

**Backtest de conviction events (30 dias M1, n=559):** edge negativo — el spike aislado de DZ+VR no predice continuacion.

### 2. RangeBreakoutFlow (nuevo — pendiente deploy)

Detector validado en backtest de 30 dias M1 (43,200 barras):

> Un rango de consolidacion (0.08–0.55% de precio, 15–60 barras M1) donde el CVD acumula presion en una direccion, seguido de un cierre fuera del rango con VR >= 2x, produce edge positivo.

**Resultados del backtest (n=914 senales con CVD alineado):**

| Setup | n | Horizonte | Win rate | Exp/trade |
|-------|---|-----------|---------|----------|
| SHORT breakdown + CVD bajista + VR>=2 | 187 | +60min | 13.9% | +0.061% |
| LONG breakout + CVD alcista + VR>=2 + contra-tendencia | 196 | +30min | 12.8% | +0.093% |
| London SHORT + VR>=3 + CVD bajista | 32 | +60min | 31.3% | +0.274% |

Parametros del setup:
- Rango: 15–60 barras M1, tamano 0.08–0.55% del precio
- CVD: acumulado negativo (SHORT) o positivo (LONG) durante el rango
- Breakout: cierre fuera del rango con VR >= 2x
- Stop: 0.25% del precio
- Target SHORT: 0.50% | Target LONG: 0.45%
- Sesiones: London, LondonNyOverlap, NewYork

**Estado:** codigo implementado, pendiente de:
1. Correr `supabase/migration_rbf.sql` en Supabase SQL Editor
2. Deploy a Railway con `git push`

---

## DB — Supabase (limpiada 2026-06-02)

**Tablas activas:**

| Tabla | Filas | Descripcion |
|-------|-------|-------------|
| `scalping_bars` | ~1,500+ | Una fila por barra M1 — DZ, VR, CVD, OBI, signal_fired, blocked_by |
| `scalping_signals` | 16 | Senales S1/S2/S3 con contexto completo |
| `scalping_trades` | 12 | Trades cerrados con PnL, MFE, MAE |
| `regime_history` | 416 | Historial de cambios de regimen |
| `micro_windows` | 1,960 | Micro-dinamica M1 por vela (5 buckets x 15s) |
| `lab_signals` | 28 | Subdimi Parallel — referencia historica |
| `deployed_params` | — | Hot-reload de config desde Railway |
| `rbf_signals` | 0 | Tabla nueva para RangeBreakoutFlow (pendiente migracion) |

**Tablas eliminadas (2026-06-02):**
- `shadow_signals`, `signal_outcomes` — eran del sistema DRR, vacias
- `lab_outcomes`, `intrabar_outcomes`, `calibration_log`, `institutional_snapshots` — nunca recibieron datos
- Vistas: `v_signals_with_outcomes`, `v_micro_with_outcomes`

---

## Que no es presente

No usar como estado actual:

- DRR como estrategia live — esta desactivado (`drr_enabled = false` en strategy.toml).
- `shadow_signals` y `signal_outcomes` — eliminadas de Supabase.
- Documentos anteriores al 2026-06-02 sobre el "estado del sistema" — todo cambio en esta sesion.
- S1/S2/S3 como estrategia principal de largo plazo — son la base de datos mientras se valida RBF.

---

## Proximos pasos

1. **Deploy inmediato:** `git push` a Railway con el codigo nuevo (RBF detector + config).
2. **Migracion DB:** correr `supabase/migration_rbf.sql` para crear `rbf_signals`.
3. **Acumular senales RBF** durante 2–3 semanas en paper trading.
4. **Primera evaluacion:** con 50+ senales `rbf_signals` cerradas, comparar outcomes reales vs backtest.
5. **S1 fix pendiente:** si se quiere reactivar S1, primero deployar el fix de target x1.8 y verificar que el circuit breaker se resetea.

---

## Checklist para futuras revisiones

Antes de evaluar cualquier estrategia:

- Identificar de que tabla vienen los datos (`scalping_bars`, `rbf_signals`, etc).
- Excluir datos anteriores al reset de cada sistema.
- Medir por expectancy (wins x target + losses x stop) / n, no solo win rate.
- Buscar segmentos con n >= 30 antes de sacar conclusiones.
- No optimizar parametros con menos de 100 senales.

---

## Documentos relacionados

- [ESTADO_CHECKLIST.md](ESTADO_CHECKLIST.md): inventario tecnico por capas.
- [DRR_IMPLEMENTACION.md](DRR_IMPLEMENTACION.md): bitacora tecnica de RangeDetector y micro-ventana.
- [SESION_6_UI_DRR_DB.md](SESION_6_UI_DRR_DB.md): bitacora del deploy DRR (historico).
- [BUILD.md](BUILD.md): setup local de build en Windows.
