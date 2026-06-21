# Documentacion canonica

Ultima revision: 2026-06-12

Este indice existe para evitar leer documentacion historica como si fuera el estado actual del sistema. Para proximas revisiones de RBF, empezar siempre por los documentos canonicos de `docs/rbf`.

## Leer primero

| Documento | Uso |
| --- | --- |
| [rbf/RBF_REGLAS_ACTIVAS.md](rbf/RBF_REGLAS_ACTIVAS.md) | Fuente de verdad operativa de RBF: reglas live, config, score, vetos y dataset. |
| [rbf/RBF_FEATURE_MATRIX.md](rbf/RBF_FEATURE_MATRIX.md) | Contrato de features RBF para ciencia/ingenieria de datos. |
| [rbf/DATASET.md](rbf/DATASET.md) | Dataset M1 + microestructura, tablas y limites de backfill/live. |
| [mtf/MTF_SISTEMA.md](mtf/MTF_SISTEMA.md) | Fuente de verdad del sistema MTF (FUTUROS Bybit perp, BTCUSDT). Estado, params, infra y edge honesto. |
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

## Estado vigente (2026-06-12)

- **Motor principal:** RangeBreakoutFlow sobre M1. Es la linea prioritaria del proyecto.
- **Modo RBF:** Solo Shorts, sesiones London/LondonNyOverlap/NewYork, post-breakout + pre-breakout.
- **Dataset activo:** `*_bars` por simbolo, `rbf_signals`, `obi_10s`, `regime_history`.
- **Microestructura:** OBI L5/L10/L20, spread, CVD slope, DZ, VR, absorption, stacked imbalance, VPIN, OI, VWAP.
- **Scalping S1/S2/S3:** etapa previa/soporte historico, no foco operativo actual.
- **MomentumFlow v2:** paper hasta acumular muestra out-of-sample suficiente.
- **MTF (futuros):** Bybit perp BTCUSDT, M1, solo shorts. Infra verificada; edge causal fino y NO desplegable (ver [mtf/MTF_SISTEMA.md](mtf/MTF_SISTEMA.md)). Eras spot/multi-símbolo archivadas en [archive/mtf/](archive/mtf/).
- **DRR:** desactivado. Tablas `shadow_signals` y `signal_outcomes` eliminadas de Supabase.

## Archivo historico

Los documentos obsoletos o de investigacion previa estan en [archive/pre-drr-and-research/](archive/pre-drr-and-research/) y en varios planes RBF antiguos. No usarlos como fuente de verdad si contradicen [rbf/RBF_REGLAS_ACTIVAS.md](rbf/RBF_REGLAS_ACTIVAS.md).

## Regla para mantener docs

Cuando cambie RBF live, actualizar primero [rbf/RBF_REGLAS_ACTIVAS.md](rbf/RBF_REGLAS_ACTIVAS.md), despues [rbf/RBF_FEATURE_MATRIX.md](rbf/RBF_FEATURE_MATRIX.md) si cambia el contrato de datos, y por ultimo este indice. No crear otro documento de "estado actual".
