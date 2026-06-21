# Set de Estrategias a Probar — extraído de `transcripciones/` (2026-06-19)

> Análisis de las 22 transcripciones + `revisar/` + `CATALOGO_ORDERFLOW_COMPLETO.md` (65 conceptos).
> Principio unánime de los traders: **el orderflow NO es la estrategia — confirma el edge de una
> estructura.** Por eso cada estrategia = un EDGE BASE estructural + una CAPA DE CONFIRMACIÓN de orderflow.
> Las transcripciones se reducen a **7 edges base distintos** (lo demás son confluencias/contexto que
> filtran, no disparan). Todas se prueban: causal, fee real 11 bps (taker) o 4 bps (maker), IS/OOS
> 2026-03-01, capital $500, sobre el dataset rico de 365 días.

## Datos disponibles vs. faltantes (define qué confirmaciones podemos medir)
| Tenemos | Fuente |
|---|---|
| OHLCV M1 + delta + CVD + footprint (fp_*) | `processed/btcusdt_perp_m1.parquet` |
| Volume Profile (vp_poc/vah/val), swings, ATR, sesión, niveles ICT | idem |
| Ticks tick-a-tick + OB 1s (OBI L5/10/25, microprice, spread, depth25) | `raw_trades/`, `ob_1s/` |
| Open Interest 5m + funding 8h | `oi_5m.parquet`, `funding.parquet` |
| **FALTA: feed de liquidaciones + heatmap DOM completo** | (Bybit API/archivo — re-descargable) |

→ El `liq_ratio` (señal estrella crypto en S3/S5) y el `LI` del heatmap NO son medibles aún.
Tenemos un proxy parcial del heatmap (depth25/OBI). **Bajar liquidaciones = tarea habilitante pendiente.**

---

## Las 7 estrategias

### S1 — Range Reversal (fade de extremos) · REVERSIÓN · ✅ PROBADA
- **Edge base:** rango intradía (≥20 velas, 0.8–3.5 ATR); fade del extremo hacia mid/extremo opuesto.
- **Confirmación OF:** absorción (AS, DZ contra el nivel, VR≥2, POC en mecha), CVD divergencia, delta flip.
- **Fuente:** Delta_Range_Reversal_v3, Delta Ranges (#56/57), VWAP+BigTrades (#60), AMT responsivo (#53).
- **Resultado:** fade base = moneda al aire (WR 50.8% IS → 45.5% OOS); **ningún OF lo mejora robusto OOS**
  (absorción +5.7 IS → −1.7 OOS). Stop spec 0.25·ATR (3.6 bps) < fee. **No desplegable.** Ver EDGE_VERDICT frente 4.

### S2 — Real Breakout Continuation · MOMENTUM · ✅ PROBADA (negativa)
> `_s2_breakout.py`: base (toda ruptura) WR 28%IS/42%OOS, avgR −0.64/−0.17. El gate OF "ruptura real"
> (VR≥4,|DZ|≥2,CVD) deja 14 IS/3 OOS trades → estadísticamente vacío. Sin edge.
- **Edge base:** ruptura REAL del rango/nivel (lo opuesto a S1). Entrada a favor del break hacia el siguiente
  HVN / naked POC.
- **Confirmación OF (gate del spec #11/#61):** VR≥4 + |DZ|≥2 + CVD confirma en la dirección + OI sube +
  aceptación fuera (2 velas). Sin esto = no es ruptura real.
- **Fuente:** rompimiento real (#61), ICT "volumen confirma intención + cierre fuera" (Pass Prop/Abraham).
- **Hipótesis a refutar:** ¿el orderflow distingue rupturas reales de fakeouts mejor que el azar, neto de fee?

### S3 — Liquidity Sweep Reversal (SFP / stop-run) · REVERSIÓN · ✅ PROBADA (negativa, incl. proxy-liq)
> `_s3_sweep.py` (K=24, RR2.0): base SFP fade WR 38%IS/35%OOS, avgR −0.37/−0.44, 6.5 tr/día. Con OF
> completo (VR≥2 + delta + **OI-drop como proxy de liquidación**) → negativo. Test fee-indep: WR base
> 38.2/35.0% → con OF+liq 38.7/33.3% = **Δ +0.5 IS / −1.7 OOS**. El proxy de liquidación NO rescata OOS.
> **Dato:** Bybit NO publica liquidaciones históricas (v5 realtime-only, sin archivo). El proxy OI-drop
> es la definición fiel del spec ("OI baja en el sweep = stops saltando"). Frente cubierto.
- **Edge base:** barrido de swing high/low previo o equal highs/lows (toma de BSL/SSL), falla en aceptar,
  revierte. Stop en la invalidación del swing; target al nivel opuesto / FVG.
- **Confirmación OF:** spike de volumen en la barrida (validación obligatoria — sin volumen = fake),
  big-trade atrapado en la mecha, delta absorción/flip, OI cae en el sweep.
- **Fuente:** SFP (#40), BSL/SSL (#46), EQH/EQL (#47), Toma de liquidez (#51), ICT paso 2.
- **Nota datos:** liq_ratio no disponible; usamos volumen+delta+OI+big-trade como confirmación.

### S4 — VWAP Band Reversion · REVERSIÓN · ✅ PROBADA (negativa)
> `_s4_s5.py`: WR 32%IS/27.5%OOS, avgR −0.44/−0.50, 6.9 tr/día. OF (delta rechazo) no mejora. Negativo.
- **Edge base:** precio alcanza VWAP ±2σ (sobreextensión) → retorno al VWAP. Zona muerta cerca del VWAP.
- **Confirmación OF:** rechazo en la banda (delta contra la extensión, big-trade fallido, absorción).
- **Fuente:** VWAP bandas (#34/35), VWAP+BigTrades zona alta/baja (#60).

### S5 — AMD / Power-of-3 (sweep de sesión) · MOMENTUM/REVERSIÓN · ✅ PROBADA (negativa)
> `_s4_s5.py`: base avgR −0.05 IS (cerca de breakeven) → colapsa OOS (WR 23.7%, avgR −0.40) = decay
> IS→OOS, no edge. Con OF (OI-drop+delta) quedan 1 IS/3 OOS trades → vacío.
- **Edge base:** Asia acumula (rango) → London barre un extremo de Asia (manipulación) → NY distribuye
  (movimiento real). Entrada tras el sweep de London en dirección de la distribución.
- **Confirmación OF:** manipulación = sweep + OI cae + CVD divergencia; distribución = delta/CVD a favor.
- **Fuente:** AMD/Power of 3 (#44), sesiones (#58). (Distinto de MTF: anclado a sesión Asia→London→NY.)

### S6 — Level Reaction Scalp (Okala 80/20) · REVERSIÓN · ✅ PROBADA (negativa) · ★ ALTA FRECUENCIA
> `_s6_level_scalp.py` (step $250): **28 tr/día** (cumple frecuencia) pero WR 38–42%, avgR −0.4 a −1.35,
> Sharpe −0.4 a −0.7. Stop wide mejora avgR pero sigue negativo; OF (delta flip) NO sube WR (OOS baja).
- **Edge base:** niveles redondos/cuartos (p.ej. múltiplos de $250/$500 en BTC, o .00/.20/.50/.80). Precio
  toca el nivel + cierra de vuelta + reacción → entrada contraria. Stop fijo chico, TP1 fijo, escala.
- **Confirmación OF:** reacción real en el nivel (delta flip, absorción, big-trade fallido).
- **Fuente:** Okala "80/20 levels / repair entry" (Prop Firm 70% WR), level-to-level (#55).
- **Por qué importa:** es el setup de **mayor frecuencia** (muchos trades/día) → alinea con el objetivo
  de maximizar trades viables. Riesgo conocido: stop chico vs fee (mismo problema que S1 — clave medirlo).

### S7b — ICT Market Maker Model + OTE (Omar/MBB) · DIRECCIONAL · ✅ PROBADA (negativa)
> `_s7_ict_mmm.py` (M15): bias daily + sweep PDH/PDL (manipulación) + displacement/CISD + entrada OTE
> fib 0.62 (RR~2.2) o a mercado. **Negativa en ambos modos:** market WR 77–85% pero avgR −0.25/−0.35
> (RR<1), OTE WR 28%/15%OOS avgR −0.85/−1.05. Que ambos extremos del trade-off den avgR<0 ⇒ no hay
> edge direccional, no es calibración. OF (delta a favor estilo-forex Y delta-en-contra/absorción
> estilo-cripto) no rescata (filtra a n=3–9). **ICT es baja frecuencia (~30–50 tr/año)** → opuesto al
> objetivo de máx trades/día. NOTA: modelo discretizado de uno discrecional ("intuición/grading de
> displacement"); un backtest mecánico no lo captura 100%, pero ningún componente da expectativa +.

### S8 — TOP-DOWN multi-timeframe 4H→1H→5m (la "imagen completa" SMC/ICT) · ✅ PROBADA (negativa)
> `_topdown.py`: la escalera top-down fiel — 4H DIRECCIÓN (EMA20) → 1H ESTRUCTURA+LIQUIDEZ (zona FVG/OB
> alineada con 4H) → 5m ENTRADA (engulfing/rechazo + orderflow: VR + delta). Regla: 4H↔1H deben alinear;
> sin confirmación 5m no se entra. **Negativa:** A solo-estructura WR 36%OOS avgR −0.99 (4.9 tr/día);
> B +orderflow 5m WR 31%OOS avgR −1.78 — el OF **empeora** OOS (recorta 1178→444, WR 36→31%). El
> framework anidado completo tampoco produce edge; el orderflow como confirmación 5m no separa OOS.

### S9 — SMC + Orderflow confluence (investigación INTERNET) · ✅ PROBADA (negativa)
> Fuentes web (MarketTrace CVD-perp, Buildix SMC+orderflow, TradingView ICT BTC.P, TradingFinder):
> framework unánime = zona SMC (OB/FVG/swing) alineada con Volume Profile (POC/VAL) + **≥2
> confirmaciones de orderflow** (CVD divergencia, OBI flip ≥±0.30, funding extremo, delta). Stop bajo
> swing/VAL, target swing opuesto. `_smc_of.py` (M5, K=12): estructura-sola WR 31.5%OOS avgR −1.28;
> +1 OF WR 25%OOS −1.62; **+≥2 OF (la regla "obligatoria") → solo 2-3 trades = vacío**. avgR<−1 por
> stop tight (fee 11bps vale >1R). Negativa. NOTA: las fuentes son educativas/marketing (Udemy "90%
> winrate", blogs de prop firms) SIN backtest auditado fee-aware OOS — ninguna publica métricas
> verificables en BTC perp. Nuestro test fiel y honesto da negativo.

### Investigación ICT-para-CRYPTO (conceptos específicos BTC perp)
Las transcripciones ICT son mayormente forex/índices (UJ, EUR, NQ). Adaptaciones cripto-nativas (research):
- **Midnight Open (00:00 UTC) / True Day Open** como PD array y "draw on liquidity" (clave en ICT-crypto).
- Sesiones UTC 24/7: Asia 00-07 (acumulación) → London 07-10 (manipulación/sweep) → NY 12-16 (distribución).
- **Delta invertido en cripto** (catálogo #2/#56): en BTC el delta va EN CONTRA del precio → la confirmación
  de un short tras barrer un high es *compra agresiva absorbida* (delta>0 que falla), no delta<0. Probado
  (of_mode=absorb) → no mejora.
- Crypto-nativo: OI/funding/liquidaciones como confirmación de la manipulación (ya en S3/S5, sin lift OOS).
- Pendiente testeable (expectativa baja por el patrón): midnight-open + NWOG/NDOG (new week/day opening gap)
  como key levels del sweep; macros 08:30/09:30 NY.

### S7 — OB / FVG Retest Continuation · MOMENTUM · ⏳ PENDIENTE (baja prioridad)
- **Edge base:** OB/FVG validado; precio retesta y continúa. Entrada en el retest, no en el break.
- **Confirmación OF (validación del OB):** volumen>media + delta significativo + stacked imbalances en el OB.
- **Fuente:** OB (#41), FVG (#42), Carmine FVG retest, ICT entry models.
- **Nota:** los templates ICT/OB sobre M1 causal ya salieron negativos (lookahead corregido); se reprueba
  solo con confirmación OF nativa y como continuación pura. Prioridad baja.

---

## Capas de contexto (filtran S1–S7, no disparan solas)
- **Régimen rango vs tendencia** (#64, voto 2/3: VA se desplaza / lado del VWAP / pendiente CVD). S1,S4,S6
  solo en rango; S2,S5,S7 en tendencia/transición.
- **VP Open day-type** (#29): apertura dentro/fuera del VA define día de rango vs tendencial.
- **Mamushka top-down** (#54) / **Level-to-level** (#55): jerarquía de niveles para targets.
- **Sesiones líquidas:** London 07–10, NY 13–17 UTC.

## RESULTADO DEL SET COMPLETO (todas probadas, 365 días, causal, fee 11 bps, IS/OOS)
| # | Estrategia | tr/día | WR OOS | avgR OOS | ¿OF sube WR OOS? | Veredicto |
|---|---|---|---|---|---|---|
| S1 | Range Reversal | 1.2 | 45.5% | −0.42 | No (abs +5.7IS→−1.7OOS) | ❌ |
| S2 | Breakout Continuation | 0.3 | 42% | −0.17 | Filtra a n=3 (vacío) | ❌ |
| S3 | Sweep Reversal (SFP)+proxy-liq | 5.1 | 35% | −0.44 | No (+0.5IS/−1.7OOS) | ❌ |
| S4 | VWAP Band Reversion | 6.4 | 27.5% | −0.50 | No | ❌ |
| S5 | AMD sesión | 0.5 | 23.7% | −0.40 | Filtra a n=3 (vacío) | ❌ |
| S6 | Level Scalp (Okala) | **28** | 40% | −0.44 | No | ❌ |

**Patrón unánime:** el edge estructural base es breakeven-a-negativo y decae OOS; la confirmación de
orderflow (absorción, delta, CVD, VR, OI-drop/proxy-liq) **nunca produce un lift robusto fuera de
muestra** — parpadea positiva IS y se cae o filtra a n insignificante OOS. El fee (11 bps taker) domina:
con stop fee-survivable de 25 bps el coste vale ~0.44R/trade, y con stop tight estilo Okala (3-5 bps)
vale >1R. S6 confirma que se puede operar 28 trades/día — perdiendo. **Ninguna es desplegable.**

## Orden de testeo (prioridad)
1. **S6** (alta frecuencia, simple, mide el objetivo "max trades/día" + el problema stop-vs-fee de frente).
2. **S2** (ortogonal a S1: momentum, no fade — el otro lado de la moneda del rango).
3. **S3** (núcleo ICT: sweep reversal; el setup más citado entre traders).
4. **S4** (VWAP bands, independiente y simple de medir).
5. **S5** (AMD sesión).
6. **S7** (OB/FVG, baja prioridad).
7. **Habilitar liquidaciones** (descarga Bybit) → re-correr S3/S5 con liq_ratio.

## Metodología común (todas)
Engine causal único: detección estructural con info ≤ t; simulación de salida path-dependiente; fee 11 bps
(taker) y variante 4 bps (maker/limit); IS<2026-03-01/OOS; métricas WR, avgR, PnL neto, nº trades,
trades/día, MaxDD, Sharpe/Sortino, sobre $500 @ 1% riesgo. **Comparar siempre base vs base+OF** para aislar
el aporte del orderflow (la pregunta central). Test fee-independiente de la confirmación cuando aplique
(estilo `_fade_conditional.py`).
