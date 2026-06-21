# Veredicto de Edge — BTCUSDT Perp Bybit, orderflow/microestructura (2026-06-19)

> Pregunta del encargo: ¿existe un edge **direccional, desplegable y robusto IS/OOS** sobre BTCUSDT
> perpetuo de Bybit, operando alta frecuencia (muchos trades/día) en largo y corto, a fees reales
> (taker 0.055%/lado → **11 bps RT**), partiendo de cero y libre de lookahead?
>
> **Respuesta: NO.** Tres frentes independientes —eventos univariados, derivados, y un modelo
> multivariante full-feature— convergen en lo mismo: la señal direccional residual es ~±0.5 bps,
> **1–2 órdenes de magnitud por debajo del fee**, y **no generaliza fuera de muestra**.

## Dataset (año completo, re-bajado sin lookahead)
- **365 días** 2025-06-19→2026-06-18: ticks tick-a-tick (`raw_trades/`, ~1.76M/día) + order book a 1s
  (`ob_1s/`: mid, microprice, spread, OBI L5/10/25, profundidad) + OI 5min + funding 8h.
- Split temporal estricto: **IS < 2026-03-01**, OOS ≥ 2026-03-01 (~3.5 meses OOS).
- Costo modelado desde el primer cálculo: **11 bps RT** (taker Bybit perp). Maker (≈4 bps RT) tampoco
  rescata señales de ~0.5 bps.

## Frente 1 — Eventos de microestructura sub-minuto (`build_events.py` + `_event_predict.py`)
2.76M instancias de 16 tipos de evento (sweep, absorción, big-trade, delta-burst, OBI extremo/flip,
micro-lean, vol/spread spike), etiquetadas con retorno fwd 1/5/15m + MFE/MAE, signadas por hipótesis.

| Evento | signed 15m OOS | hit15 OOS | MFE/MAE |
|---|---|---|---|
| sweep_down | +0.9 bps | 53% | +20/−22 |
| obi_extreme_neg | +0.6 bps | 52% | +15/−17 |
| micro_div / obi_flip / obi_extreme | +0.4–0.5 bps | 51% | ±15–17 |
| big_trade / delta_burst / absorb | −0.2 a +0.2 bps | ~50% | ±15 |

**Lo mejor = +0.9 bps vs 11 bps de fee.** Hit rates 50–55% (moneda al aire). MFE/MAE simétricos
±15–22 bps → el retorno futuro condicionado a cualquier evento es un random walk simétrico.
Los heurísticos de los traders (absorción, divergencia CVD, flush de stops, OBI extremo, big trades)
mapean 1:1 a estos eventos: **ninguno tiene alfa harvesteable**.

## Frente 2 — Derivados OI/funding, horizonte horas (`_deriv_predict.py`)
Panel horario (9.122 h), señales de posicionamiento (funding extremo, px×ΔOI, surge/drop OI), fwd 1/4/12/24h.
- Único flag positivo: `funding_pos_extreme` (OOS +25/+88/+147 bps a 4/12/24h) **pero es un artefacto**:
  n_oos=51, **IS≈0** (+0.0/+1.6 bps), su espejo `funding_neg_extreme` es **negativo en IS**, y solo
  "funciona" en el régimen bajista post-marzo. No es edge: es un puñado de episodios de un solo régimen.
- Además horizonte multi-hora ⇒ pocos trades/día, lo opuesto al objetivo de alta frecuencia.

## Frente 3 — Capstone multivariante (`_ml_build.py` + `_ml_eval.py`) — el límite superior honesto
LightGBM (400 árboles) sobre **2.07M filas** muestreadas del panel 1s, 23 features causales
(OBI multinivel, micro-lean, flujos delta/vol/momentum multi-ventana 10–300s, big-trade, tick-imb,
profundidad, ΔOI, funding). Entrena IS, evalúa OOS. Si ni un modelo con TODO bate el fee → no hay edge.

| label | IS Spearman | **OOS Spearman** | OOS sign-acc | OOS decil-top real | net long/short OOS |
|---|---|---|---|---|---|
| fwd_5m | +0.153 | **+0.008** | 50.3% | +0.49 bps | −10.5 / −11.0 bps |
| fwd_15m | +0.229 | **+0.0008** | 50.0% | +0.45 bps | −10.6 / −11.0 bps |

Firma de **sobreajuste puro**: el modelo aprende el ruido IS (net +2.84 bps a 15m) y la correlación
OOS con el retorno realizado es **cero** (0.0008). El decil más confiado realiza +0.45 bps → −10.6 net.
Features top (spread, funding, vol_300, ret_300): contexto, no predicción.

## Métricas solicitadas (capital base 500 USD)
No es posible reportar WR/avgR/PnL/DD/Sharpe positivos de "una estrategia" porque **ninguna hipótesis
supera el gate de predictividad** previo a construir la estrategia. Ilustración concreta con el mejor
candidato OOS honesto (`sweep_down`, +0.9 bps signed, ~12 eventos/día):
- Expectancy neta por trade = **+0.9 − 11 = −10.1 bps**. avgR < 0. PnL neto **negativo** por diseño.
- A ~12 trades/día sangra fees monótonamente; cualquier Sharpe es negativo. No desplegable.

## Frente 4 — Orderflow como CONFIRMACIÓN de un edge estructural (la tesis correcta)
> Corrección del usuario: *orderflow no es estrategia, es la capa que confirma el edge de una estrategia.*
> Estrategia base extraída de las transcripciones: **Delta Range Reversal con Absorción**
> (`transcripciones/Delta_Range_Reversal_v3_Unificado.md`) — fade de extremos de rango intradía hacia
> el mid/extremo opuesto; el orderflow (absorción/delta/CVD/VR) confirma. Test en M5, causal, IS/OOS.

**Magnitudes reales BTC perp M5** (clave): ATR mediana 14 bps, rango 65 bps, distancia extremo→mid 33 bps.
El stop del spec (0.25·ATR ≈ **3.6 bps**) es < fee (11 bps) ⇒ el fee vale >1R y mata el setup tal cual.
Único variante viable: TP al extremo opuesto (~65 bps) con stop fee-survivable (~25 bps).

**Backtest del fade** (`_range_fade.py`, TP=opp, stop 25 bps, fee 11 bps, $500@1%):
| Config | WR IS/OOS | avgR IS/OOS | $500→ |
|---|---|---|---|
| A base estructura | 35.2/36.6% | −0.46/−0.42 | $37 |
| B + veto ruptura | 35.5/36.4% | −0.45/−0.45 | $39 |
| C + absorción full | 36.5/34.2% | −0.37/−0.41 | $351 |
El orderflow recorta trades 355→52 pero **no sube el WR** (y OOS baja). Todas las configs pierden.

**Test decisivo fee-independiente** (`_fade_conditional.py`): en cada toque de extremo, ¿alcanza el
extremo opuesto antes de romper? P(WIN) base vs condicionada a orderflow, IS/OOS (sin fee, sin stop):
| condición | WR IS | WR OOS | Δ vs base (IS/OOS) |
|---|---|---|---|
| BASE (todos los toques) | 50.8% | 45.5% | — |
| absorción (DZ<−1,VR>1.5,reclaim) | 56.5% | 43.8% | **+5.7 / −1.7** |
| delta fuerte \|DZ\|≥1.5 | 43.5% | 38.5% | −7.2 / −7.0 |
| CVD divergencia | 40.7% | 29.1% | −10.0 / −16.4 |
| VR≥2 | 49.7% | 42.2% | −1.0 / −3.3 |
| \|DZ\|≥2 | 41.1% | 36.1% | −9.7 / −9.3 |

**El fade base ya es moneda al aire (50.8%→45.5% OOS, decae). Ningún orderflow lo mejora robustamente:**
la absorción parpadea +5.7 IS y se cae a −1.7 OOS; la agresión fuerte/CVD-div predicen *ruptura* (Δ muy
negativo). Confirma frentes 1-3 desde el ángulo estructural: el orderflow no separa fades buenos de malos OOS.

**Hueco de datos honesto:** el spec llama "señal estrella en crypto" a **clusters de liquidaciones +
heatmap DOM**, que NO están en el dataset (sí OB L2 1s + OI + funding, no el feed de liquidaciones ni
el DOM histórico). Es lo único de la estrategia sin evaluar. Bajar liquidaciones de Bybit sería el
próximo paso si se quiere cerrar también ese frente.

## Frente 5 — Minería de patrones anti-overfitting (triple-barrera + null por permutación)
`_mine_build.py` + `_mine.py`. Etiqueta TRADE-LIKE (triple-barrera ±B bps, target vs stop, no retorno a
horizonte fijo). 28 features causales (orderflow+footprint+estructura+OI/funding+hora). Disciplina:
IS-selecciona → OOS-valida, + control de data-snooping con **null de etiquetas barajadas** (el edge OOS
de las reglas IS-seleccionadas debe superar el p95 del null).

**B=80bps, T=180m (fee 0.137R, breakeven P(up)=0.569):**
- Modelo flexible (LightGBM, 28 feats): AUC IS 0.868 → **OOS 0.519**; decil top P(up) IS 0.96 → **OOS 0.50**.
- Reglas (1.326 candidatas): edge CRUDO (sin fee) tiene UNA señal real que sobrevive OOS — familia
  **`dist_vwap<lo`** (precio bajo VWAP → continúa abajo, ~58-62% direccional, edge OOS +0.12 a +0.23):
  es **persistencia de tendencia/momentum-VWAP, NO microestructura**. Pero edge OOS crudo medio +0.13
  queda **bajo el fee 0.138**. Con fee: 40 reglas pasan IS, OOS medio **−0.007R**, 20% positivas.
- **NULL (etiqueta barajada): OOS p95 = +0.0036. Edge real (−0.007) NO lo supera → indistinguible de
  ruido de búsqueda.** (B=35bps: 0 de 1.326 reglas baten fee ni siquiera IS.)

**Conclusión de la minería:** no hay patrón desplegable bajo el control más estricto. El único signal OOS
real es persistencia de tendencia (VWAP/ret/atr), que vive justo en la línea del fee y es regime-dependiente;
las features de **orderflow/microestructura no aportan predicción OOS**. Coherente con frentes 1-4.

## Frente 6 — La única señal viva (VWAP-momentum) con fills MAKER (`_vwap_mom.py`, M5)
La minería dejó UN signal OOS real: posición extrema vs VWAP de sesión → continuación. Operada explícita
en M5 (M1→M5), barrera ±80bps/3h, ¿la rescata el maker (~4 bps RT) vs taker (11 bps)?

| variante | WR OOS | taker 11bps | maker 4bps | maker VIP 2bps |
|---|---|---|---|---|
| quantile, sesión | 51.1% | −0.126 | −0.038 | −0.013 |
| th=2.0 ATR, sesión | 50.5% | — | −0.026 | **−0.001** |
| th=2.5, 24h | 48.0% | — | −0.044 | −0.019 |

El maker convierte el taker-perdedor en casi-breakeven, pero en el MEJOR caso (2bps VIP, sesión) aterriza
**exactamente en la línea de breakeven OOS (−0.001R), sin cruzarla**. 24h o umbral más fuerte → WR<50%,
peor. Y el modelo de maker es OPTIMISTA (asume fill gratis sin selección adversa; un límite de momentum
real se llena peor). → **Ni con maker hay edge desplegable robusto OOS.** Es la última hipótesis, cerrada.

## Conclusión honesta
A fees retail de Bybit, **BTCUSDT-solo no presenta un edge direccional desplegable** en
orderflow/microestructura/derivados, robusto IS/OOS, en horizonte de minutos a horas. El retorno
forward condicionado a cualquier feature o combinación es un cuasi-martingala simétrica; los ~±0.5 bps
de señal viven muy por debajo del coste de transacción. Forzar una estrategia aquí sería sobreajuste.

## Frentes NO cerrados (si se quiere seguir, con expectativa calibrada)
1. **Cross-asset lead-lag** (ETH/SOL→BTC, ~30s de adelanto que mencionan los traders): único ortogonal
   sin testear. Pero las señales BTC propias son sub-bps y el fee es el mismo ⇒ expectativa baja.
2. **Market-making pasivo** (ganar el spread, no predecir dirección): juego distinto, latency-sensitive
   y con selección adversa; poco realista para una cuenta retail de 500 USD en Bybit.
3. **Reducir el fee** (tier VIP / rebate maker): cambia el umbral, no crea señal donde hay 0.5 bps.

## Reproducir
```
python backtest/build_events.py        # eventos del año → events.parquet
python backtest/_event_predict.py      # Frente 1
python backtest/_deriv_predict.py      # Frente 2  (TRADIO_PERP si el dato vive en E:)
python backtest/_ml_build.py --step 15 # dataset capstone → _ml_dataset.parquet (~2M filas)
python backtest/_ml_eval.py            # Frente 3
```
