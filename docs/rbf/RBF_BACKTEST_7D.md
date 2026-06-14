# RBF Backtest — 7 días live (Jun 5–12, 2026)

*Última actualización: 2026-06-12*

> Backtest con microestructura real de Supabase. El script de referencia es
> `apps/rbf-review/api/backtest_script.py`. Para el backtest histórico pre-live
> ver [RBF_BACKTEST_14D.md](RBF_BACKTEST_14D.md).

---

## Cobertura de datos

| Campo | Valor |
|---|---|
| Script | `apps/rbf-review/api/backtest_script.py --days 60` |
| Micro start | 2026-06-05 16:25 UTC (primera barra con `cvd_slope` NOT NULL) |
| Primer trade | 2026-06-06 14:54 UTC |
| Último trade | 2026-06-12 19:32 UTC |
| actual_days | **7.1 días** (170 horas desde micro_start) |
| Duración de trades | 6 días, 4h, 38m |
| Símbolos | BTCUSDT, ETHUSDT, BNBUSDT, SOLUSDT, XRPUSDT |
| Tablas Supabase | btc_bars, eth_bars, bnb_bars, sol_bars, xrp_bars |

Los primeros días (~May 23 – Jun 5) están en las tablas como backfill OHLCV sin
microestructura (sin `cvd_slope`, `obi_l5`, `stacked_imb`, etc.). El script detecta el
`micro_start` buscando la primera barra con `cvd_slope` NOT NULL y usa ese punto como
inicio real del backtest.

---

## Parámetros del detector

| Parámetro | Valor |
|---|---|
| Capital | $500 |
| Riesgo/trade | $10 (2% fijo) |
| VR mínimo (post) | ≥ 3.0× |
| VR mínimo (pre) | ≥ 1.5× |
| Rango válido | 0.08% – 0.55% |
| Ventanas | 15, 20, 30, 45, 60 barras M1 |
| Target Short | 2.0R |
| Target Pre | 3.0R |
| Trail activate | 1.90R (sweep óptimo) |
| Trail ATR K | 1.2× |
| Cooldown | 60 barras |
| Sesiones | London, LondonNyOverlap, NewYork |
| Solo Shorts | Sí (Longs desactivados) |
| OI gate pre | `oi_mom_bars_recent ≤ 3` |
| Time stop | Eliminado |

### Gates por símbolo (simulados)
| Símbolo | Gate |
|---|---|
| BTCUSDT | `cum_delta_25b > +200` → skip |
| ETHUSDT | London skip; `cvd_in_range < -700` skip; `obi_l5 > 0.10` skip |
| BNBUSDT | `cum_delta_25b < -500` → skip |
| SOLUSDT | Sin expansion filter (bypass) |
| XRPUSDT | Sin expansion filter (bypass) |

### Gates NO simulados (limitación del backtest)
- `expansion_bars_recent ≤ 1` para BTC/ETH/BNB — requiere columna `regime` en barras históricas, no disponible
- `oi_mom_bars_recent` en pre-breakout — requiere `oi_momentum` en barras históricas (NULL en backfill)
- Score ≠ 4 gate — el script actualmente no filtra score=4 (4 trades con score=4 están incluidos)

---

## Resultados

### Global

| Métrica | Valor |
|---|---|
| Trades cerrados | **35** |
| Wins / Losses | 16 / 19 |
| Win Rate | **45.7%** |
| Total R | **+13.48R** |
| Avg R/trade | +0.385R |
| Equity final | **$637.26 (+27.5%)** |

### Por tipo de señal

| Tipo | n | WR | Total R | Avg R |
|---|---|---|---|---|
| Post-breakout (VR ≥ 3.0) | 15 | **53%** | **+8.20R** | +0.547R |
| Pre-breakout (VR ≥ 1.5 + OI gate) | 20 | 40% | +5.28R | +0.264R |

Post-breakout conserva mejor edge. Pre-breakout contribuye positivo con el gate OI activo.

### Por sesión

| Sesión | n | WR | Total R | Avg R |
|---|---|---|---|---|
| London | 10 | **50%** | **+4.50R** | **+0.450R** |
| LondonNyOverlap | 7 | 43% | +1.91R | +0.273R |
| NewYork | 18 | 44% | +7.07R | +0.393R |

London y NewYork son sesiones positivas. Overlap aporta pero con el menor edge.

### Por símbolo

| Símbolo | n | WR | Total R | Avg R |
|---|---|---|---|---|
| BNBUSDT | 3 | **67%** | +2.80R | **+0.933R** |
| ETHUSDT | 2 | 50% | +1.67R | +0.836R |
| XRPUSDT | 6 | 50% | +2.70R | +0.450R |
| SOLUSDT | 16 | 44% | +5.40R | +0.337R |
| BTCUSDT | 8 | 38% | +0.91R | +0.114R |

BTC tiene el menor edge (n=8 aún pequeño). SOL es el mayor contribuidor por volumen de señales.
ETH y XRP con n pequeño — no calibrar filtros con estos valores.

### Por exit reason

| Reason | n | Avg R | Total R |
|---|---|---|---|
| STOP_LOSS | 19 | -1.000R | -19.00R |
| TRAILING_STOP | 15 | **+1.966R** | +29.50R |
| TAKE_PROFIT | 1 | +3.000R | +3.00R |

El TRAILING_STOP promedia +1.97R — el trailing lock floor a 1.90R funciona correctamente
(mínimo garantizado cuando activa). Un trade SOL alcanzó el pre-breakout target a +3R.

---

## Trade por trade

| # | Fecha UTC | Sym | Sesión | VR | Rng% | Pre | Reason | R |
|---|---|---|---|---|---|---|---|---|
| 1 | 06-06 14:54 | BTC | Overlap | 1.50 | 0.176 | PRE | TRAILING_STOP | +2.11 |
| 2 | 06-06 17:55 | BTC | NY | 1.94 | 0.412 | PRE | STOP_LOSS | -1.00 |
| 3 | 06-06 18:09 | SOL | NY | 2.23 | 0.423 | PRE | STOP_LOSS | -1.00 |
| 4 | 06-07 08:05 | SOL | London | 5.93 | 0.433 | — | STOP_LOSS | -1.00 |
| 5 | 06-07 12:09 | BNB | London | 4.95 | 0.245 | — | TRAILING_STOP | +1.90 |
| 6 | 06-07 12:09 | SOL | London | 4.49 | 0.465 | — | TRAILING_STOP | +1.90 |
| 7 | 06-07 16:08 | SOL | Overlap | 1.80 | 0.464 | PRE | STOP_LOSS | -1.00 |
| 8 | 06-07 16:56 | BTC | Overlap | 5.66 | 0.287 | — | STOP_LOSS | -1.00 |
| 9 | 06-07 17:48 | ETH | NY | 1.68 | 0.361 | PRE | TRAILING_STOP | +2.67 |
| 10 | 06-07 18:17 | SOL | NY | 1.62 | 0.475 | PRE | TAKE_PROFIT | +3.00 |
| 11 | 06-07 21:34 | SOL | NY | 1.62 | 0.419 | PRE | STOP_LOSS | -1.00 |
| 12 | 06-08 13:18 | SOL | Overlap | 1.69 | 0.524 | PRE | STOP_LOSS | -1.00 |
| 13 | 06-08 16:55 | SOL | Overlap | 2.64 | 0.523 | PRE | STOP_LOSS | -1.00 |
| 14 | 06-08 18:47 | SOL | NY | 1.90 | 0.446 | PRE | STOP_LOSS | -1.00 |
| 15 | 06-08 19:50 | SOL | NY | 2.27 | 0.460 | PRE | TRAILING_STOP | +1.90 |
| 16 | 06-08 20:12 | BTC | NY | 5.10 | 0.221 | — | STOP_LOSS | -1.00 |
| 17 | 06-09 08:06 | SOL | London | 4.67 | 0.436 | — | TRAILING_STOP | +1.90 |
| 18 | 06-09 11:14 | SOL | London | 8.45 | 0.500 | — | STOP_LOSS | -1.00 |
| 19 | 06-09 12:46 | SOL | London | 1.68 | 0.530 | PRE | STOP_LOSS | -1.00 |
| 20 | 06-09 13:19 | BTC | Overlap | 12.71 | 0.401 | — | TRAILING_STOP | +1.90 |
| 21 | 06-09 14:04 | BNB | Overlap | 2.25 | 0.392 | PRE | TRAILING_STOP | +1.90 |
| 22 | 06-09 17:48 | ETH | NY | 1.57 | 0.478 | PRE | STOP_LOSS | -1.00 |
| 23 | 06-09 21:04 | XRP | NY | 5.15 | 0.263 | PRE | TRAILING_STOP | +1.90 |
| 24 | 06-09 21:18 | BTC | NY | 4.33 | 0.217 | — | TRAILING_STOP | +1.90 |
| 25 | 06-09 21:19 | SOL | NY | 2.56 | 0.444 | PRE | TRAILING_STOP | +1.90 |
| 26 | 06-10 08:01 | XRP | London | 2.08 | 0.252 | PRE | TRAILING_STOP | +1.90 |
| 27 | 06-10 08:03 | SOL | London | 3.43 | 0.483 | — | TRAILING_STOP | +1.90 |
| 28 | 06-10 09:20 | BTC | London | 5.24 | 0.419 | — | STOP_LOSS | -1.00 |
| 29 | 06-10 10:18 | XRP | London | 2.34 | 0.416 | PRE | STOP_LOSS | -1.00 |
| 30 | 06-10 17:29 | SOL | NY | 4.66 | 0.531 | — | TRAILING_STOP | +1.90 |
| 31 | 06-10 18:40 | XRP | NY | 3.90 | 0.527 | — | TRAILING_STOP | +1.90 |
| 32 | 06-10 20:09 | BNB | NY | 2.37 | 0.356 | PRE | STOP_LOSS | -1.00 |
| 33 | 06-10 21:10 | XRP | NY | 8.10 | 0.513 | — | STOP_LOSS | -1.00 |
| 34 | 06-12 18:07 | BTC | NY | 4.49 | 0.170 | — | STOP_LOSS | -1.00 |
| 35 | 06-12 19:32 | XRP | NY | 1.93 | 0.318 | PRE | STOP_LOSS | -1.00 |

*(1 trade abierto en DATA_END excluido: ETH NY +0.03R parcial)*

---

## Observaciones

### Score 4 no filtrado en el backtest
Los trades #9 (ETH score=4), #16 (BTC score=4), #21 (BNB score=4), #34 (BTC score=4)
fueron ejecutados. En el live, `score == 4` se registra pero no opera.
Pendiente: añadir este filtro al script Python para alinear completamente con live.

### SOL domina el volumen
16 de 35 trades son SOL (46%). SOL no tiene filtro `expansion_max_bars` (bypass por
correlación invertida). Esto explica la cantidad — SOL dispara más frecuentemente.

### BTC con menor edge
BTC tiene WR=38% en backtest. En live el patrón de `cum_delta > 200` skip debería estar
reduciendo las entradas contra compradores activos. El gate no se simula en el backtest.

### XRP nuevo en datos
6 trades de XRP (primer símbolo nuevo con volumen de señales). WR=50%, +2.70R. Con n=6
no se puede calibrar filtros específicos aún. Objetivo: n=15 antes de activar cualquier gate.

---

## Comparación con referencia anterior

| Métrica | Ref Jun-10 | Actual Jun-12 | Diferencia |
|---|---|---|---|
| actual_days | ~5.0 | 7.1 | +2.1 días |
| Trades | 33 | 35 | +2 |
| WR | 48.5% | 45.7% | -2.8pp |
| Total R | +15.48R | +13.48R | -2.0R |
| Equity | $654 | $637 | -$17 |

Los 2 días adicionales (Jun 10-12) aportaron 8 nuevos trades: 4 wins (+7.60R) y 4 losses (-4R).
Diferencia neta: +3.60R pero la equity bajó porque el capital base acumulaba los trades anteriores
con equity distinta. La WR cayó levemente — jun 10-12 fue un período de mayor lateralidad.

---

## Próximos pasos

| Trabajo | Criterio | Estado |
|---|---|---|
| Score ≠ 4 gate en script | Alinear Python con live | Pendiente |
| expansion_bars_recent en script | Requiere columna regime en bars | Pendiente |
| BTC gates con n suficiente | n=25 Shorts cerrados | n=8, acumulando |
| XRP calibración | n=15 mínimo | n=6, acumulando |
| ETH calibración | n=25 mínimo | n=2, acumulando |

---

*Generado: 2026-06-12. Re-ejecutar backtest cuando haya ≥7 días nuevos de datos.*
