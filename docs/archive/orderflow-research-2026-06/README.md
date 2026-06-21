# Archivo — Research de estrategias Orderflow/ICT (2026-06)

> **Por qué existe este archivo:** este cuerpo de research llegó a un veredicto fuerte (negativo) sobre
> el edge direccional de orderflow en BTC perp. Vivía en la **memoria auto-cargada**, lo que **sesgaba
> cada conversación nueva** hacia "no hay edge" desde el primer mensaje. Se archivó acá para preservarlo
> íntegro pero **sacarlo del auto-load**, de modo que las sesiones futuras arranquen sin sesgo y se
> consulte este material **solo cuando se pida explícitamente**. Nada se borró.

Fecha de archivo: 2026-06-20. Rama: `htf-orderflow`.

---

## Veredicto en una línea (para contexto, no para sesgar)
Tras ~12 frentes (microestructura, derivados, ML, las 6 estrategias de las transcripciones, ICT MMM,
top-down 4H→1H→5m, minería anti-overfit, test maker), **no se encontró edge direccional desplegable
robusto IS/OOS en BTCUSDT-perp-solo a fees retail**. El único signal OOS real fue momentum-VWAP débil,
que vive justo en la línea del fee. **Esto NO significa que no haya nada que probar** — frentes vivos:
swing H1/H4 con stops anchos (donde el fee deja de dominar), multi-instrumento, fees institucionales.

---

## A. Snapshot de memoria (preservado) — `memory-snapshot/`
Copia exacta de los archivos de memoria de estrategia que se sacaron del auto-load:
- `MEMORY.md` (índice original completo)
- `project_orderflow_edge_verdict.md` — el veredicto de los ~12 frentes
- `project_mtf_strategy.md` — el "$74.9K era lookahead"
- `project_orderflow_funnel.md` — qué features sirven/no (IS/OOS)
- `project_htf_orderflow.md` — fade contrarian real pero sub-fee
- `project_bybit_fees.md` — fees reales 0.1%/0.1%, bug que infló el edge

> Para restaurar el contexto en una sesión: pedir explícitamente leer estos archivos.

## B. Documentos de research (registro permanente, viven en docs/)
| Doc | Contenido |
|---|---|
| [docs/orderflow/EDGE_VERDICT_2026-06-19.md](../../orderflow/EDGE_VERDICT_2026-06-19.md) | Los 6 frentes + minería anti-overfit + test maker |
| [docs/orderflow/STRATEGY_SET.md](../../orderflow/STRATEGY_SET.md) | S1–S9 + ICT MMM + top-down + research web |
| [docs/orderflow/FEATURE_INVENTORY.md](../../orderflow/FEATURE_INVENTORY.md) | Inventario completo de features (todas las capas) |
| [docs/orderflow/INSIGHTS_TRADERS.md](../../orderflow/INSIGHTS_TRADERS.md) | Síntesis de las transcripciones de traders |
| [docs/mtf/MTF_WORKLOG_2026-06-19_lookahead.md](../../mtf/MTF_WORKLOG_2026-06-19_lookahead.md) | Autopsia del lookahead ($74.9K → $3.3K) |
| [docs/mtf/MTF_WORKLOG_2026-06-19_microstructure_pivot.md](../../mtf/MTF_WORKLOG_2026-06-19_microstructure_pivot.md) | Pivote a ticks+derivados |

## C. Scripts de backtest (en git, `backtest/`)
Las 6 estrategias de las transcripciones y los motores de research:
`_range_fade.py` `_fade_conditional.py` `_s2_breakout.py` `_s3_sweep.py` `_s4_s5.py`
`_s6_level_scalp.py` `_s7_ict_mmm.py` `_topdown.py` `_smc_of.py`
`_ml_build.py` `_ml_eval.py` `_mine_build.py` `_mine.py` `_vwap_mom.py`
`build_events.py` `_event_predict.py` `_deriv_predict.py`
Builders de datos: `build_timeframes.py` (M5/M15/H1/H4).

## D. Dataset
`share_dataset/` (M1/M5/M15/H1/H4 + OI + funding + muestras + diccionario). Ver su `README.md`.
