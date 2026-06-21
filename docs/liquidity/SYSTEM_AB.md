# Sistema A+B — Liquidity (fader) + Momentum (trailing), enrutado por régimen

> 2026-06-21. Evolución del proyecto: de UNA estrategia de liquidez a un SISTEMA de dos gestiones
> sobre el mismo motor de entrada, enrutadas por régimen. Doc maestro de A: `LIQUIDITY_STRATEGY.md`.

## TL;DR

El proyecto tenía **una** estrategia (liquidity = fader de niveles). Una autopsia mostró que **captura
solo el 1% de los movimientos grandes** del mercado — porque es un *fader* (apuesta a reversión) y los
movimientos grandes son *continuaciones*. La solución no es otra estrategia con otra entrada: es la
**misma entrada (límite en nivel de volumen) con DOS gestiones de salida**, elegidas por el régimen.

| | **A · Liquidity (fader)** | **B · Momentum (trailing)** |
|---|---|---|
| Tesis | el nivel **aguanta** → rebote | el nivel **se rompe** → continúa |
| Salida | parcial 50% + breakeven + target estructural | **trailing stop** (deja correr) |
| Gana en | rangos (Chop) | tendencias (Expansion/Trend) |
| Perfil | muchos aciertos chicos | pocos, grandes (+6.5R medio, máx +40R) |

**Misma entrada. La gestión la decide el régimen.** Fade en Chop, trailing en tendencia.

## La autopsia que lo motivó (`_audit_missed.py`)

De **269 movimientos limpios ≥2%** (mediana 3.9%) en 365d, la estrategia A:
- capturó completos: **4 (1%)**
- entró y soltó temprano (migaja): 44 (16%)
- no se llenó / bloqueó vol / RR / cupo / sin setup: **83%**

A es un fader: estructuralmente **no ve** las tendencias. Confirmado con las variables del motor.

## Mejora de A primero (`--min-range`, `_audit_edge.py`)

Las "migajas" (parcial de 0.19% que el fee se come en vivo) se eliminan con un **RANGO MÍNIMO 0.5%**
al primer objetivo. A pasa de 1210 trades migaja (avgR +0.72) a **440 trades reales (avgR +1.12)**.
Cada trade apunta a ≥0.5% → limpia los fees con margen. (Más piso de stop 0.15% + fee honesto.)

## B: lo que NO funcionó y lo que SÍ

- **Breakout-chase ingenuo** (`_strategy_b.py`): perseguir la ruptura **PIERDE** (avgR −0.5, captura
  0% de movimientos). Falsos breakouts + fee taker. Re-confirma: no hay edge persiguiendo dirección.
- **A-entrada + trailing** (`_strategy_b_v2.py`): la entrada de A (límite en el nivel) te mete en el
  pivote del movimiento; cambiar la gestión a trailing **captura 10% de los movimientos grandes (10×)**.
  Perfil trend-follower: WR 25%, ganancias medias +6.5R, máx +40R.

## El sistema A+B enrutado (`_strategy_ab.py`) — backtest 365d

| Sistema | n | WR | avgR | netR | **OOS avgR** | maxR | DD | Sharpe |
|---|---|---|---|---|---|---|---|---|
| A sola (todo fade) | 440 | 61% | +1.12 | +493 | +1.24 | +17 | **3.2%** | **8.0** |
| B sola (todo trail) | 1210 | 25% | +0.64 | **+779** | +0.81 | **+40** | 20.3% | 4.9 |
| **A+B (enrutado)** | 506 | 49% | **+1.27** | +641 | **+1.82** | +28 | 8.5% | 6.7 |

El enrutado tiene el **mejor edge por trade OOS (+1.82)** y captura **9× más movimientos grandes** que
A sola. Reparto: 331 fades (avgR +0.75) + 175 trails (avgR **+2.24**). El régimen marca tendencia solo
el 6% del tiempo → es selectivo, y por eso los pocos trails son de alta calidad.

## Detectores de régimen — investigación (`_regime_detectors.py`, `_strategy_ab_v2.py`)

Literatura (Kaufman ER, ADX, Choppiness Index): en TREND → continuación, en RANGE → fade (= nuestro
diseño). Implementados causales y testeados. **Resultado: NINGUNO le gana a la columna `regime`
tosca.** Son demasiado permisivos (marcan tendencia muy seguido) → enrutan falsos breakouts a trailing
→ revientan el DD (ADX 31-52%) o bajan la calidad. La columna `regime` es **selectiva** (6% trend) y
por eso enruta mejor. **Lección:** nuestra `regime` ya es buen filtro; no hace falta algo más fino.
Hay un **trade-off duro selectividad↔cobertura**: perseguir más capturas degrada el riesgo-ajustado.

## En el trade-lab

`apps/trade-lab` tiene un selector **"A solo" / "A+B"** (barra superior, solo en la estrategia
liquidity). Backend: `liquidity_app_backtest.py --system A|AB`. Los trades de trailing salen con
`reason="trail"`, `gestion="trail"` y `targetName="trailing"` (target = precio de salida del trailing).

## Caveats honestos

- Los regímenes de tendencia son raros (6% de barras) → el subset de trails es chico; el +1.82 OOS es
  fuerte pero sobre menos muestra. Vigilar.
- B-sola hace más netR total (+779) montando todo, pero con 20% DD y trades mediocres. El enrutado
  cambia netR por calidad y menos DD.
- Todo sigue dependiendo del **fill ratio maker real** (la entrada es maker) → validar en paper.

## Reproducir

```
python backtest/_audit_missed.py        # autopsia: A captura 1% de los movimientos grandes
python backtest/_strategy_b.py          # breakout ingenuo (pierde)
python backtest/_strategy_b_v2.py       # A-entrada + trailing (captura 10%)
python backtest/_strategy_ab.py         # sistema A+B enrutado (365d)
python backtest/_strategy_ab_v2.py      # detectores de régimen probados (ninguno gana al tosco)
python backtest/liquidity_app_backtest.py --days 540 --system AB --json
```
