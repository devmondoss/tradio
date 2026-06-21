# HTF Orderflow Fade — Worklog 2026-06-19

Estrategia HTF construida **desde cero** explotando el orderbook historico crudo
(`data/bybit-perp/ob_1s` + `raw_trades`), sin heredar nada del dataset procesado
ni de la estrategia MTF previa. Rama: `htf-orderflow`.

## Pipeline (reutilizable)
- `backtest/htf/build_htf_features.py` — builder causal 1-marco + EDA de IC.
- `backtest/htf/build_htf_multi.py` — builder multi-marco (5m/15m/1h/4h/1d) en
  una sola lectura/dia. Genera `htf_{tf}_full.parquet`.
- `backtest/htf/backtest_htf_fade.py` — backtest causal por deciles, fee real.
- `backtest/htf/sweep_htf_fade.py` — barrido selectividad(colas) x holding.
- `backtest/htf/validate_htf_fade.py` — validacion robusta: trades NO solapados,
  desglose mensual, walk-forward, t-stats.

Features HTF causales: `obi5/10/25_twa`, `depth_imb`, `depth_ratio`,
`micro_prem_bps`, `obi25_sign_stab`, `delta`, `taker_buy_frac`, `spread_bps`.
Todo time-weighted dentro de la barra; el sesgo se calcula con barras cerradas.

## Hallazgos

1. **El orderbook a HTF en BTC perp es CONTRARIAN, no de seguimiento** (lo
   opuesto al manual de HTF). Libro cargado de bids + compra agresiva de takers
   -> precio baja despues. IC negativo estable IS y OOS para `depth_imb`,
   `obi25`, `delta`, `taker_buy_frac`. `spread_bps` parecia predictivo pero NO
   sobrevive OOS (descartado).

2. **La señal es estadisticamente REAL pero economicamente sub-fee.**
   - 15m: IC(depth_imb) ~ -0.03..-0.05, N~35k -> t ~ 3-4 (significativa). Pero
     el movimiento capturable a ese horizonte es ~3-4 bps < **11 bps fee taker**.
   - 1h: IC se debilita a -0.019 (t=-1.77); a 16h ya es ~0 (t=-0.14).
   - Firma clasica de microestructura: la señal vive en horizontes de minutos
     (no bate el fee) y se desvanece antes de horizontes con movimiento grande.

3. **El "edge" de 15-38 bps del barrido por colas era artefacto de SOLAPAMIENTO.**
   Con trades NO solapados (cooldown=hold), la mejor config (1h, hold16, cola2%)
   da gross **+6.6 bps/trade, t=0.55**; neto taker **-4.4 bps**; neto maker
   **+2.6 bps (t=0.22)**. Walk-forward: 1 de 5 folds en -25 bps. Todas las demas
   configs no solapadas: gross en [-2.8, +6.6] bps, **t-stats en [-0.73, +0.74]**
   -> indistinguible de cero.

## Veredicto
**No hay edge desplegable** desde esta construccion. La direccionalidad
contrarian es real pero su magnitud por trade (~0-6 bps en trades independientes)
esta por debajo del fee taker (11 bps) y no es estadisticamente significativa.
Con fees maker queda en ~breakeven/+2.6 bps, dentro del ruido. Coincide con la
conclusion previa del proyecto ("sin edge desplegable en BTC"), ahora probada de
forma **independiente y rigurosa desde el orderbook crudo**.

## ADENDA — Microestructura L2 por-evento (ob500 crudo, 6 dias)

Probado sobre el stream ob500 crudo (100ms) que SI tiene la dinamica L2 que el
agregado 1s pierde. Pipeline: `backtest/htf/probe_l2_events.py` (OFI L1 +
cancel/add asym) y `backtest/htf/probe_ofi_strategy.py` (persistencia + niveles).

1. **OFI (Order Flow Imbalance) es 3-5x mas fuerte que el agregado**: IC +0.134
   a 1s (vs depth_imb -0.02..-0.05). Pero vive en SEGUNDOS (decae a la mitad en
   15s) -> señal HFT, no harvesteable a tarifas/latencia retail.

2. **OFI NO persiste a minutos**: IC del OFI acumulado cae a +0.02..+0.035 a
   1-15min; backtest no-solap neto maker -3.7 bps. Falla.

3. **OFI x NIVEL CLAVE (PDH/PDL/POC/VWAP) concentra la señal** (la union
   microestructura+estructura de precio): cuando el precio esta a <=5 bps de un
   nivel, cofi180 a 5min da IC ~0.07 (vs 0.006 lejos) y backtest no-solap gross
   **+2.85 bps, t=+3.1, hit 62%**. ESTADISTICAMENTE REAL. Pero net maker -1.15
   bps: sigue bajo el fee maker retail (4 bps RT). Break-even ~2.85 bps RT
   (~1.4 bps/lado) -> solo alcanzable a maker MM/VIP o rebate.

**Veredicto global:** el orderbook de BTC perp lleva señal direccional real y
significativa en TODOS los niveles (agregado, OFI, OFI x nivel), pero su magnitud
SIEMPRE queda bajo el costo de transaccion, incluso maker. El unico lead vivo
(OFI x nivel clave) solo seria rentable a fees maker MM/VIP, y aun asi el +2.85
bps gross es OPTIMISTA para ejecucion maker (sesgo de seleccion de fills: tu orden
pasiva llena cuando el movimiento va en contra). Solo 6 dias -> feasibility, no
desplegable.

## Backtest de SISTEMA (metricas trading, capital $500)

`backtest/htf/backtest_system.py` — stop fijo (=1R), target=RR*stop, 1 posicion,
sizing por riesgo 1%/trade, fee 5.5bps/lado, resolucion intrabar (mid_high/low),
stop-primero conservador. Barrido thr x stop x RR sobre el AÑO completo.

Mejor config por marco (rankeada por equity final):
- **1h** (thr1.5, stop60bps, RR3): WR 34.8%, avgR_win +2.10, avgR_loss -1.12,
  RR_real 1.88, **expectancy +0.002R (~0)**, PF 0.98, $500 -> **$458 (-8.5%)**,
  **MaxDD -40.9%**, Sharpe 0.04. = breakeven, no rentable.
- **15m** (thr2.0, stop60bps, RR3): WR 35.7%, **expectancy -0.125R**, PF 0.77,
  $500 -> **$86 (-82.7%)**, MaxDD -85.2%.

Todas las demas combinaciones: expectancy negativa, DD -50%..-96%. El fee hace
que avgR_loss > 1R (-1.12..-1.25R) y recorta los ganadores -> expectancy <= 0.
Confirma con metricas de sistema que NO hay configuracion desplegable.

## Limitacion clave / siguiente frente honesto
La profundidad usada es **L25 agregada a 1Hz**. Pierde la dinamica L2 real
(colocacion/cancelacion, refills de iceberg, queue position, spoofing) donde
suele estar el alfa de orderbook. Un test legitimo distinto requeriria el stream
L2 por-evento, no snapshots de profundidad acumulada. Alternativa: reencuadrar
el +2.6 bps maker como skew de inventario en un bot market-maker (clase de
estrategia e infra distinta), no como señal direccional.
