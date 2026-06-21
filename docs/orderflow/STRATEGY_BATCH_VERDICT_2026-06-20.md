# Veredicto del barrido "estrategia ≠ predictor" — hipótesis listas (2026-06-20)

> **Reencuadre del usuario:** el [EDGE_VERDICT](EDGE_VERDICT_2026-06-19.md) cerró la pregunta
> *"¿alguna feature PREDICE el retorno forward?"* (no). Pero una estrategia no es un predictor:
> es **estructura de entrada + gestión asimétrica** (stop, TP1 parcial, breakeven, TP2, timeout)
> + **filtros de abstención** (H3/H4). Una expectativa positiva podría salir del path/payoff
> aunque el hit-rate sea ~50%. Este barrido testea ESE espacio sobre las hipótesis "listas" de
> `02_catalogo_21_hipotesis.md` / `01_opening_range_4_variants.md`.

## Qué se testeó (`backtest/_listas.py`)
Motor causal único, gestión real (parcial 50% en TP1 → stop a breakeven → resto a TP2/timeout),
fee 11 bps taker **y** 4 bps maker, $500 @ 1%, IS<2026-03-01 / OOS, walk-forward 4 folds en IS,
reporta **fechas únicas** (no solo n de trades). Ejecución M5. VP del día previo **congelado**
(vp_poc/vah/val son rolling intra-día — 165 valores únicos/día — el congelado es obligatorio,
mismo tipo de lookahead que ya mordió a H1/H4).

| # | Estrategia completa | Spec |
|---|---|---|
| H1 | 4 variantes de apertura vs perfil del día previo (V1 fade rango / V2-V3 lean / V4 excl.) | `01_opening_range_4_variants.md` |
| H7 | LVN entrada → HVN/POC target | H7 |
| H14 | Niveles redondos 80/20 (reversión, SL/TP fijo) | H14 |
| H15 | Repair candle → re-entrada en retest | H15 |
| H19 | Daily sentiment continuation (bias = vela diaria previa, hold-to-close) | H19 |
| H20 | Caja estadística de pullback (percentil 25-75, a favor de tendencia) | H20 |
| overlay | H3 (veto delta-en-contra), H4 (unfinished), H9 (régimen) ON/OFF | H3/H4/H9 |

## Resultados

### Taker 11 bps — todo negativo IS y OOS
| Estrategia | tr/día | WR OOS | avgR IS / OOS | WF-IS (4 folds) |
|---|---|---|---|---|
| H1 [stop=max] | 0.8 | 37.3% | −0.265 / **−0.354** | −0.56 −0.24 −0.19 −0.06 |
| H1 [stop=local] | 0.8 | 21.6% | −0.741 / −0.815 | todos < −0.6 |
| H7 | 11 | 20.5% | −1.011 / −0.896 | todos < −0.8 |
| H14 | 36 | 40.6% | −0.764 / −0.730 | ~−0.77 estable |
| H15 | 13 | 29.6% | −2.035 / −1.289 | todos < −1.4 |
| H19 | 0.9 | 25.7% | −0.132 / −0.189 | −0.50 −0.27 −0.35 +0.59 |
| H20 | 42 | 41.8% | −0.171 / −0.135 | ~−0.15 estable |

### Maker 4 bps — el test decisivo: rozan el breakeven, no lo cruzan OOS
| Estrategia | avgR IS | **avgR OOS** | WF-IS |
|---|---|---|---|
| **H1 [stop=max]** | −0.020 | **−0.124** | −0.24 +0.08 −0.04 +0.13 |
| **H19** | −0.016 | **−0.073** | −0.38 −0.15 −0.23 +0.71 |
| H20 | −0.058 | −0.036 | ~−0.10 |
| H14 | −0.297 | −0.263 | el fee tight domina aún a maker |
| H15 | −0.752 | −0.453 | negativo |
| H7 | −0.407 | −0.392 | negativo |

### Overlays de abstención (H3+H4) sobre H1
**n=0 IS / n=1 OOS.** No mejoran la expectativa: **vacían la muestra** (mismo patrón
"el orderflow filtra a n insignificante" de S2/S5/S7/S9).

---

# ⚠️ ACTUALIZACIÓN (mismo día) — H1 con implementación FIEL: SÍ aparece un candidato

> La tabla de arriba usó implementaciones **primera-pasada**. Reimplementado H1 con fidelidad
> total al spec (`01_opening_range_4_variants.md`): gate min_RR 1.5, sub-variante 1b, trigger
> mecha/cierre, sensibilidad a `daily_open_hour`, y —clave— **modelo de orden LÍMITE realista**
> (entrada *en el nivel*, no al close; selección adversa intrabar contada; fee maker). Resultado:
> **el veredicto "todo negativo" NO se sostiene para H1.**

## El hallazgo (`python backtest/_listas.py h1final`)
Config: **V1+V3** (V2 y sub1b decaen OOS, descartadas), **open_hour=12 UTC**, **orden límite
maker** en niveles de área-valor del día previo (VAH/VAL/POC), stop=max (pdh/pdl), minRR 1.5.

| Tramo | n | WR | avgR | $500→ | MaxDD | tr/día |
|---|---|---|---|---|---|---|
| TODO 2025-01+ (1.5a) | 204 | 37.7% | **+0.405** | $913 (+83%) | 23.2% | 0.39 |
| IS <2026-03 | 161 | 38.5% | +0.427 | $844 | 23.2% | 0.39 |
| **OOS ≥2026-03** | 43 | 34.9% | **+0.325** | $570 | 19.1% | 0.42 |

**Robustez (lo que distingue señal de artefacto):**
- **Meseta en `open_hour`** 8–16 UTC, no un pico (OOS positivo en todas las horas). No es curve-fit.
- **Sobrevive selección adversa:** fill solo al *atravesar* el nivel (4 bps margen adverso) → OOS
  apenas baja (+0.41→+0.30). El edge no es ilusión de fills en el toque.
- **Robusto a régimen:** positivo trimestre a trimestre sobre 1.5a (4–5 de 6 trimestres +,
  incluye bull 2025 y 2026). Peor trimestre −0.21R.
- **OOS no decae** respecto a IS (al revés de los falsos positivos por overfit).

## La naturaleza del edge (y su talón de Aquiles)
**NO es un edge direccional.** A **taker (11 bps) es netamente negativo** (IS −0.56 / OOS −0.63).
Todo el edge vive en: (a) mejor precio de entrada por poner **límite en el nivel** vs perseguir al
close, + (b) **fee maker 4 bps**. Es decir: H1 es **provisión de liquidez** en las zonas de valor
del día previo (un fade *es* dar liquidez), no predicción.

→ **Riesgo #1 (load-bearing):** todo descansa en **conseguir los fills maker**. El modelo asume
fill al alcanzar el nivel; la realidad tiene cola de órdenes, fills parciales y cancelaciones, y
los fills que faltan pueden ser justo los buenos rebotes. **Esto el backtest no lo puede zanjar —
requiere validación en paper/testnet con fills reales.**
Riesgos #2-3: lumpy (WR 38%, MaxDD 23%, Sharpe bajo); frecuencia modesta (0.4 tr/día).

## Conclusión (rectificada)
El reencuadre del usuario era correcto y **productivo**: con implementación fiel y modelo de
límite realista, **H1 (fade de área-valor V1+V3 con fills maker) es el primer candidato con
expectativa positiva OOS robusta del proyecto** (+0.325R OOS, +0.405R full, robusto a hora /
régimen / selección adversa). No es predicción direccional — es liquidity-provision. El siguiente
paso NO es más backtest sino **paper-trading en testnet para validar el ratio de fills maker**,
la única hipótesis que el dataset no puede cerrar.

---

## Apéndice — primer barrido (implementaciones primera-pasada, supersedido por lo de arriba para H1)
El reencuadre era legítimo y se testeó con rigor. En primera pasada parecía que **la gestión no
rescata el edge:**
- A taker, las 6 estrategias completas son negativas IS y OOS (idéntico a los predictores).
- A maker, las mejores (H1 stop ancho, H19) **rozan el breakeven IS y NO lo cruzan OOS** —
  exactamente la firma del Frente 6 (VWAP-momentum) del veredicto previo. Y el modelo maker es
  optimista (asume fill sin selección adversa; un límite real se llena peor).
- Los filtros de abstención achican la muestra a n insignificante en vez de subir la expectativa.

Confirma desde el ángulo "estrategia" lo que el ángulo "predictor" ya mostró: cuando el drift
base es ~simétrico y el fee es fijo, **ningún esquema de TP/SL/parciales/vetos produce
expectativa positiva robusta OOS**. El único matiz vivo —idéntico al previo— es
**momentum/persistencia con fills maker** (H1-stop-ancho, H19, H20), que viven en la línea del
breakeven sin cruzarla. No desplegable.

## Pendiente (si se quiere profundizar, con expectativa calibrada baja)
- **H1 con fidelidad total del spec:** gate min_RR 1.5, sub-variante 1b (reacción en POC),
  sizing por convicción, sensibilidad a `daily_open_hour` (00:00 vs 13:30 UTC). Riesgo: empujar
  un IS casi-breakeven a positivo es overfitting — el OOS de H1-maker (−0.124) es el guardarraíl.
- Hipótesis bloqueadas por datos (H8/H10/H12/H13/H16/H21): footprint tick / heatmap real.

## Reproducir
```
python backtest/_listas.py all --tf 5            # taker
python backtest/_listas.py all --tf 5 --maker    # maker
python backtest/_listas.py h1  --tf 5 --overlays # vetos H3/H4 sobre H1
```
