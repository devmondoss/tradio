# Cierre completo del catálogo de hipótesis — veredicto consolidado (2026-06-20)

> Las 21 hipótesis de `02_catalogo_21_hipotesis.md` + las 4 variantes de `01_opening_range_4_variants.md`,
> probadas como **estrategias completas** (entrada + gestión) en **dos modos de ejecución**: taker-market
> (mercado al cierre, 11 bps) y **maker-límite** (orden límite reposando en el nivel, 4 bps). El hallazgo
> de H1 obligó a probarlo todo en maker-límite. Disciplina: causal, IS<2026-03-01/OOS, walk-forward,
> fechas únicas, $500@1%. Motores: `backtest/_listas.py` (H1, H14-20), `backtest/_listas2.py` (H2-H21),
> `backtest/_build_tickfeats.py` (columnas derivadas de ticks para H8/H10/H12).

## TL;DR
El único edge real en este dataset es **provisión de liquidez maker en niveles de volumen/valor**
(POC, área de valor). Aparece en **3 hipótesis independientes** —H1, H5, H21— todas con la misma firma:
positivo a maker, negativo a taker, sobrevive selección adversa y cap de frecuencia. **Predicción
direccional, momentum, estructura y orderflow-como-señal: todo negativo** (confirma EDGE_VERDICT).

## Ganadores (edge maker liquidity-provision, OOS positivo robusto)
| H | Estrategia | nivel del límite | OOS avgR | WR | notas |
|---|---|---|---|---|---|
| **H1** | Fade de área-valor (V1+V3) | VAH/VAL/POC día previo | **+0.33** (M5) / **+0.55** (M15) | 35-40% | MaxDD 15-23%, robusto a hora/régimen/TF |
| **H5** | Order Block — entrada en POC del OB | fp_poc de la vela OB previa | **+0.10 a +0.22** (estresado) | 60% | sobrevive margen adverso 0-4bps, cap 3/d |
| **H21** | Nivel POC defendido (B-shape proxy) | fp_poc mediano de ventana | **+0.12 a +0.25** (estresado) | 60% | ídem |
| H17 | Salida dinámica por tape (overlay) | — | +0.02/+0.03 sobre H5/H21 | — | mejora leve, no transformadora |

**Hilo común:** los tres son **órdenes límite maker en niveles de volumen pre-conocidos**. A taker
(perseguir al cierre) los tres son negativos. El edge = mejor precio por reposar en el nivel + rebate maker.

## Negativos (incluso a maker)
| H | Estrategia | Veredicto |
|---|---|---|
| H2 | Delta Range Reversal (fade rango intradía) | ❌ taker −1.7 / maker −0.94 |
| H6 | Big trades fade/continuación | ❌ maker −0.50 |
| H8 | Merge de perfiles de volumen (VP diario de ticks) | ❌ maker −1.03 OOS |
| H9 | POC desplazándose (régimen) | ❌ maker −0.49 |
| H10 | Velas de volumen (reversión estructura) | ❌ maker −0.33 |
| H11 | Confluencia multi-nivel | ❌ maker −0.57 (peor, alto overfit) |
| H12 | Delta absorbido intrabar | ❌ taker −1.16 (el +0.89 inicial era **look-ahead**, ver nota) |
| H13 | LVN + defensa pasiva (proxy abs_bid) | 🔶 maker +0.09 OOS marginal, IS negativo → no robusto |
| H16 | Bid refill creciente (proxy max_trade) | ❌ maker −0.49 |
| H18 | Jerarquía macro/secundaria/micro (H4/H1) | ❌ maker −0.37 |

## Notas metodológicas (bugs cazados y corregidos)
1. **Lado del límite:** un límite maker solo es válido si reposa del lado correcto del mercado
   (sell-limit por encima, buy-limit por debajo). Sin ese guard, H5 daba 54 trades/día ficticios.
2. **Path intrabar:** los fills límite simulan salida desde la barra **siguiente** (i+1), no la de
   llenado, para no fabricar wins con el rango de la propia vela. H13 pasó de +0.56 (falso) a +0.09.
3. **Look-ahead de nivel:** el nivel del límite debe ser **pre-conocido** (día/vela previa, ventana).
   H12 usaba `low/high` *realizado* de la barra → +0.89 falso; corregido a entrada a mercado → −1.16.
4. **Selección adversa:** stress con fill solo al *atravesar* el nivel (0-4 bps margen). H1/H5/H21
   sobreviven; el resto ya era negativo.

## Variantes de H1 (01_opening_range)
- **V1 (fade rango) + V3 (lean bajista):** ganadoras (forman el candidato H1).
- **V2 (lean alcista) y sub-variante 1b (reacción POC):** decaen OOS, descartadas.
- **V4 (apertura fuera de rango):** ❌ excluida — la fuente no define regla de entrada (spec §4/§11.2).
- TF: M5 (+0.33 OOS) y **M15 (+0.55 OOS, MaxDD 15%)** ambos positivos. Stop=max gana a local.

## Bloqueadas por datos (no cerrables con el dataset)
Ya se construyeron las derivables de ticks (H8/H10/H12 → negativas). Siguen sin datos fieles:
H13/H16/H21 usan **proxies** del heatmap/L2 (solo 6 días de ob500), no el dato original. H21 da
positivo con proxy fp_poc; **validar con L2 real sería el cierre fino**.

## Conclusión y siguiente paso
El catálogo está cerrado. De 21+4 hipótesis, **3 convergen en el mismo edge real: liquidity
provision maker en niveles de volumen** (H1/H5/H21). No es predicción — es dar liquidez donde el
mercado ya negoció. **Riesgo load-bearing común: el ratio de fills maker reales**, que ningún
backtest puede zanjar. El siguiente paso es **paper/testnet** para validar fills de H1 (el más
robusto y de menor frecuencia), no más backtest.
