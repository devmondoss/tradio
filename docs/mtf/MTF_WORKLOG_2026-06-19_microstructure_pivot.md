# MTF Worklog — 2026-06-19 (sesión 3): exploración exhaustiva, 2º lookahead, y pivota a ticks+derivados

> Continuación tras el lookahead H1/H4. Objetivo del usuario: **explorar TODOS los setups posibles
> ICT+orderflow**. Resultado: el template está muerto sobre BTC-M1 causal; encontramos un **segundo
> lookahead (en el stop)**; y probamos que la **microestructura agregada a M1 no predice**. Pivota:
> recuperar la microestructura REAL (ticks sub-minuto + order book + derivados OI/funding).

---

## TL;DR

1. **24 setups ICT+orderflow, ambas direcciones, todas las familias → todos negativos causal** (IS≈0/+,
   OOS −0.1 a −0.3R). Patrón inequívoco de no-edge + sobreajuste.
2. **2º LOOKAHEAD encontrado (en el stop):** `mtf_system.py` colocaba el stop en el high TOTAL del
   bucket H1 EN-CURSO (`h1hi.get((ts//H1_MS)*H1_MS)`) → conoce el máximo futuro de la hora. Inflaba
   ~+0.2R IS. Con stop causal (swing reciente o bucket cerrado) el edge residual desaparece.
3. **Baseline nulo** (entradas aleatorias, mismo exit causal) OOS −0.04 ≈ breakeven; los setups dan
   **peor que aleatorio** OOS → anti-predictivos.
4. **M1 vs M5 vs M15:** cambiar el TF de entrada NO cambia el signo (todos negativos).
5. **Geometría swing** (stop ancho + target estructural + hold 2d): IS+ → OOS− en todos. Tampoco.
6. **Predictividad de microestructura M1** (15k+ instancias, fee-aware): exceso de retorno futuro
   ±1-2 bps vs **11 bps de fee** → **~0**. La micro agregada a M1 no anticipa 1/5/15 min.
7. **Causa raíz: granularidad.** Los ticks crudos estaban BORRADOS (`trades_raw/` vacío); solo quedaba
   el agregado M1, que licúa la microestructura (un sweep dura ~15s). **Pivota: re-bajar ticks + L2 +
   derivados** y medir en horizonte nativo (eventos sub-minuto).

---

## 1. Exploración de setups (`_setup_explorer.py`, `_stop_lookahead_check.py`, `_tf_explorer.py`, `_swing_explorer.py`)

Harness honesto: mismo exit causal para todos (stop H1 ±0.40·ATR, target 2.8R, fee 0.11%, timeout 4h),
solo cambia el disparador. Catálogo: fade en VAH/AH/PDH/WH (y soporte espejo), sweep primario,
continuación (displacement, H1 BOS), orderflow puro (absorción, CVD-div, big-trade, stacked imb),
exhaustion. **Ninguno supera el ruido causal IS/OOS.**

### El 2º lookahead (decisivo)
El motor canónico ancla el stop al **high del bucket H1 en-curso** (incluye minutos futuros de la hora).
Reproducido con 3 modelos de stop sobre rejection@VAH+confirmación:
```
A) H1 full-bucket  [LOOKAHEAD]  IS +0.129 / OOS −0.101
B) H1 prev-closed  [causal]     IS −0.084 / OOS −0.179
C) rolling-60 high [causal]     IS −0.128 / OOS −0.259
```
→ El "+0.16R causal residual" de la sesión 2 se apoyaba en este 2º lookahead. **Verdad causal: negativo.**
Nota: en **live** (Rust streaming) no existe este lookahead (solo conoce el high hasta "ahora"); o sea
el **backtest sobrestimaba lo que live haría** (la paridad 391/391 era contra un backtest inflado).

---

## 2. ¿Predice la microestructura M1? (`_micro_predict.py`) — NO

En vez de serializar en ~50 trades, medimos el retorno futuro (1/5/15 min) condicionado a cada señal,
sobre **miles** de instancias (escapa al cuello de muestra). Exceso sobre baseline de sesión, IS/OOS:

| Señal (n alto) | exceso 15m OOS |
|---|---|
| abs_ask / abs_bid / cvd_div | ±0.5 bps |
| obi extremos / vpin / fp_absorb | ≤ ±1.2 bps |
| dz extremo (delta) | +1.4 bps |

**Todo 1-2 órdenes por debajo del fee (11 bps).** Único parpadeo: `big_trade` (tick-derivado) ±3-6 bps,
pero n chico y bajo fee. **La micro agregada a M1/10s no tiene alfa intradía harvesteable.**

---

## 3. Restricción de datos: los ticks estaban borrados

`data/bybit-perp/trades_raw/` y `orderbook/` VACÍOS. El pipeline viejo (`build_futures_dataset.py`)
agregaba a M1 y borraba el crudo (`gz.unlink()`). Lo más fino que quedaba era M1 (footprint) + OBI 10s —
ya probado, sin edge. **La microestructura real (sub-minuto, por evento) no existía en disco.**

Clave: el crudo es **re-descargable** (archivos históricos permanentes de Bybit). Solo había que
re-bajarlo SIN borrarlo y construir la capa sub-minuto.

---

## 4. Pipeline nuevo (construido y validado esta sesión)

| Script | Qué hace |
|---|---|
| `backtest/download_raw.py` | Descarga idempotente/resumible/paralela. Conserva **ticks full-res** (`raw_trades/`) + **order book a 1s** (`ob_1s/`: mid, microprice, spread, OBI L5/10/25, profundidad). Zips OB de 368MB en streaming (scratch local C:, se borran). Manifest + validación (Content-Length, gaps, dedup, monotonicidad). Path portátil vía `TRADIO_PERP`. |
| `backtest/download_derivatives.py` | **OI 5min + funding 8h** vía API v5 Bybit (la capa ortogonal que faltaba). |
| `backtest/build_events.py` | Panel 1s (ticks⨝OB) → 16 tipos de **evento** (sweep, absorción, big-trade, delta-burst, OBI flip/extreme, micro-lean…) etiquetados con retorno futuro 1/5/15m + MFE/MAE. |
| `backtest/_event_predict.py` | Predictividad por evento, fee-aware (11 bps), IS/OOS. |

**Fuentes:** trades `public.bybit.com/trading/BTCUSDT/`, OB `quote-saver.bycsi.com/orderbook/linear/BTCUSDT/`
(ob500 ≤2025-08-20, ob200 después), OI/funding `api.bybit.com/v5/market/`.

**Calidad (smoke 3d):** cobertura 100% seg/min, monótono, 1.76M ticks/día, OB ~10 updates/seg.
Tamaño: **~4 GB el año** en zstd (los 134 GB de zips OB eran transitorios, no se guardan).

---

## 5. Almacenamiento portátil — Tonnio (`E:`)

Todo el dataset vive en `E:\tradio-data\bybit-perp\` (pendrive 1TB, Healthy) → portátil:
```
raw_trades/  ob_1s/  oi_5m.parquet  funding.parquet  events.parquet  _manifest.parquet
```
(OJO: `D:` "Anthony" es un disco FALLADO — no usar. El bueno es `E:` "Tonnio".)
Scratch transitorio de zips OB queda en `C:` (SSD), no desgasta el USB.

---

## 6. Estado al cierre de la sesión

- ✅ **Derivados año completo en Tonnio**: OI 104.832 pts (5min) + funding 1.092 pts (8h), 2025-06-19→2026-06-18.
- ⏳ **Tick + OB 1s del año bajando** (background, ~1.5h, idempotente) → Tonnio.
- ⏳ **Pendiente**: mergear OI/funding a eventos y correr `build_events`+`_event_predict` sobre el año.

## 7. La pregunta abierta (próxima sesión)
¿Algún **evento de microestructura sub-minuto** y/o el **contexto de derivados (OI/funding)** predice
el retorno intradía **por encima del fee (11 bps)**, robusto IS/OOS? Es el último frente honesto antes
de concluir si BTC-solo tiene edge desplegable. Si tampoco → multi-instrumento o aceptar que no hay.

## 8. NO repetir
- No anclar stops al bucket HTF en-curso en backtest (= lookahead). Usar swing causal.
- No agregar la microestructura a M1 y tirar el crudo: se licúa el evento. Conservar ticks.
- No validar sobre ~50 trades serializados cuando se puede medir predictividad sobre miles de instancias.
