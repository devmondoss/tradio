# Estrategia de Provisión de Liquidez — BTCUSDT Perp (hallazgos, 2026-06-20)

## ★ CONFIG FINAL CONGELADA (2026-06-20)
> Parámetros fijados tras agotar la exploración (TF, RR híbrido, filtros de entrada — ver §13).
> Cambiar algo de aquí = re-validar. Siguiente paso NO es backtest: es validar fills maker en vivo.

| Parámetro | Valor |
|---|---|
| Mercado | BTCUSDT perpetuo Bybit (linear) |
| **Datos** | **era tick VERIFICADA 2025-06-19 → 2026-06 (~365d)**. El OHLCV existe desde 2025-01 (klines, 532d) pero el pre-tick NO está verificado → NO se usa. IS<2026-03 / OOS≥2026-03 |
| Componentes | POC del order-block + POC defendido (long) + **mirror corto del POC defendido** (provisión de liquidez maker) |
| TF de decisión | **M15** (recalcula niveles cada cierre; fills/salidas en tiempo real / M1) |
| Entrada | orden LÍMITE maker en el nivel · selección adversa 2 bps |
| Filtro | **volatilidad: ATR > mediana móvil(500)** (clave) |
| Target | **ROTACIÓN al nivel de liquidez LEJANO** (~3.7%, no scalp) · parcial 50% en el nivel cercano → breakeven → resto corre al lejano · min_RR 1.2 |
| Salida | simulada/evaluada en **M1** (honesto) · timeout 24h |
| Fee | **honesto**: maker 2 bps/lado en entrada+tp1+target; **taker 5.5 bps/lado en stop/BE/timeout** (88% de salidas son a mercado) · Riesgo: **fijo $5/trade (1%, sin compounding)** · cap 2/día por nivel |
| **Resultado OOS** | **WR ~73% · avgR +0.89 · target mediana 3.7% (rotación real) · ~3-4 trades/día** (cartera con mirror, fee honesto) |

Reproducir: `python backtest/liquidity_app_backtest.py --days 540 --json` · Visual: `apps/rbf-review` (tab único).

---


> Documento vivo. Consolida el descubrimiento central del proyecto tras el reencuadre
> "estrategia ≠ predictor". **No se pierde aquí.** Código: `backtest/_listas.py`,
> `backtest/_listas2.py`, `backtest/_consolidated.py`, `backtest/_filters.py`,
> `backtest/_build_tickfeats.py`. Validación en vivo: `live/`.

---

## 1. Qué es la estrategia (en una frase)

**No predecimos dirección. Proveemos liquidez.** Reposamos órdenes **límite maker** en precios
donde ya se negoció mucho volumen (zonas de liquidez). Cuando el precio vuelve a esos niveles,
nos llenan barato (rebate maker + mejor entrada) y el precio tiende a reaccionar desde ahí.

**El edge es la provisión de liquidez, no la predicción.** A fee taker (perseguir al cierre) la
estrategia **pierde**; solo es positiva reposando como **maker** en el nivel. Esto encaja con
todo lo anterior del proyecto (`EDGE_VERDICT_2026-06-19.md`): no hay edge direccional; el único
edge real es estructural/de microestructura de ejecución.

---

## 2. De qué se alimenta — tres tipos de nivel de liquidez

Las tres convergen en la misma idea (descubiertas como hipótesis H1, H5, H21 del catálogo, pero
son **el mismo fenómeno**):

| Nivel de liquidez | Qué es | Cómo operamos |
|---|---|---|
| **Área de Valor del día anterior** | Rango con el 70% del volumen de ayer (borde alto/bajo + POC) | Compra en borde bajo / vende en borde alto → hacia POC y extremo opuesto |
| **POC del Order Block** | Precio de mayor volumen de la última vela de impulso | Límite en ese precio esperando el retest |
| **POC defendido** | Nivel de alto volumen tocado y respetado ≥2 veces | Compra en ese soporte de volumen probado |

**Mecánica (ciclo):** cada vela M5 cerrada → recalcula niveles → coloca límites maker del lado
correcto (compra debajo, venta encima) → si el precio toca, fill maker (4 bps) → gestiona
stop/TP (parcial en el área-valor) → si no se llena, cancela y repone.

---

## 3. Resultado de la cartera consolidada (las 3 juntas)

Riesgo fijo $500 @ 1%, maker 4 bps, IS<2026-03-01 / OOS, 1.5 años de datos.

| Tramo | n | WR | avgR | $500→ | MaxDD | Sharpe |
|---|---|---|---|---|---|---|
| TODO 2025-01+ | 2274 | 60% | +0.254 | $3384 | 12% | +0.36 |
| IS | 1802 | 61% | +0.281 | $3030 | 12% | +0.41 |
| **OOS** | 472 | 56% | +0.150 | $854 (+71%/3.5m) | 24% | +0.19 |

6/6 trimestres positivos. La diversificación de 3 niveles baja el MaxDD IS a 12%.

---

## 4. ⭐ HALLAZGO CLAVE — el filtro de volatilidad

**Proveer liquidez SOLO cuando la volatilidad está elevada (ATR > su mediana móvil de 500 velas,
causal)** mejora casi todas las métricas a la vez. Es el descubrimiento más robusto del proyecto.

### Tabla completa de métricas (OOS) — BASE vs +ATR alta

| # | Métrica | BASE | +ATR alta | Efecto |
|---|---|---|---|---|
| 1 | WR / precisión | 58% | **65%** | +7 pp |
| 2 | avgR (R/trade) | +0.134 | **+0.511** | ×3.8 |
| 3 | netR / PnL (R) | +57.7 | **+188.4** | ×3.3 |
| 4 | $500 → | $788 | **$1442** | +83% extra |
| 5 | Profit factor | 1.21 | **2.09** | casi ×2 |
| 6 | avg win | +1.34R | +1.51R | mejor |
| 7 | avg loss | −1.53R | **−1.34R** | pierde menos |
| 8 | **MaxDD** | 18.5% | **4.8%** | **−74%** |
| 9 | **Sharpe** | +0.22 | **+0.82** | ×3.7 |
| 10 | Consistencia IS↔OOS | decae (+0.26→+0.13) | **estable (+0.52→+0.51)** | quita el decaimiento |
| 11 | n trades | 429 | 369 | −14% (único coste) |
| 12 | trades/día | 3.97 | 3.40 | baja poco |
| 13 | Fill ratio maker | — | **sin medir** | ❓ validar en vivo |

### Por qué tiene sentido
El problema crónico del proyecto era *el fee domina los movimientos pequeños*. La solución:
operar solo cuando el rebote desde el nivel es **lo bastante grande** para pagar el fee y llegar
al TP. **Volatilidad baja → movimientos chicos → sangras (−0.06R). Volatilidad alta → rebotes
grandes → +0.51R.** El edge nace de la asimetría: el coste es fijo, el rebote escala con la vol.

### Validación anti-overfit
IS +0.517 ≈ OOS +0.511 (coincidencia casi perfecta) → **no es data-snooping**, es robusto.
Conserva el 86% de los trades (no es cherry-picking de pocos eventos).

---

## 5. Qué features mejoran y cuáles NO (ablación)

| Feature | Efecto sobre proveer liquidez | Veredicto |
|---|---|---|
| **Volatilidad alta (ATR>mediana)** | Rebotes grandes pagan el fee | ⭐ El mejor — usar |
| Spread estrecho | Mejores fills, menos DD (OOS +0.194) | ✅ útil, secundario |
| Régimen tendencia | OOS +0.82 pero n=92 (poca muestra) | 🔶 dudoso, vigilar |
| OBI a favor / en contra | Neutro (+0.19 / +0.24) | ➖ no aporta claro |
| Absorción en nivel (abs_bid/ask) | Neutro | ➖ |
| Divergencia CVD | Neutro | ➖ |
| VPIN bajo (flujo no tóxico) | Neutro | ➖ |
| Sesión London+NY | Neutro | ➖ |
| **Volatilidad baja** | Destruye (−0.06R, DD 37%) | ❌ vetar |

**Conclusión:** la microestructura fina (OBI, absorción, CVD, VPIN) **no** mejora nada —coherente
con todo el proyecto— pero **el contexto de volatilidad sí, y mucho.**

---

## 6. Validación en vivo (lo único que falta cerrar)

El backtest asume fill al tocar el nivel. **El fill ratio maker real con cola de órdenes es el
único riesgo que no se puede zanjar sin datos en vivo.** Harness en `live/`:
- **Dry-run** (`python live/paper_liquidity.py`): datos públicos reales de Bybit, órdenes
  virtuales, mide fill ratio + outcome + PnL forward. Sin claves, sin riesgo.
- **Live-testnet** (`--live-testnet` + claves): órdenes post-only reales = fill ratio con cola.

⚠️ **Caveat ATR + fills:** en alta volatilidad el mercado va más rápido → el fill ratio podría
**empeorar** justo cuando el edge es mayor (selección adversa). El harness debe medir el **fill
ratio condicionado a volatilidad** para confirmar que el +0.51R sobrevive a fills reales.

---

## 7. Filtro ATR cableado — cartera completa (OOS, BASE vs +filtro)

| | OOS sin filtro | OOS con filtro ATR |
|---|---|---|
| WR | 55.7% | **61.6%** |
| avgR | +0.150 | **+0.474** |
| $500 → | $854 | **$1457** |
| MaxDD | 23.8% | **5.2%** |
| Sharpe | +0.19 | **+0.65** |
| n trades | 472 | 404 (−14%) |

IS +0.409 ≈ OOS +0.474 (robusto). Reproducir: `python backtest/_consolidated.py --volfilter`.

**Matiz por componente (OOS, con filtro):** el filtro dispara los niveles POC
(orderblock +0.59 WR 67%, defendido +0.41 WR 62%) pero **empeora el fade de área-valor**
(+0.31 → +0.085): ese componente prefiere baja volatilidad. Posible mejora futura: aplicar el
filtro solo a los componentes POC.

## 7b. ⭐ Mirror corto del POC defendido (2026-06-21) — CABLEADO

`gen_h21` solo compraba soportes → cartera ~80% long. Añadido el **espejo**
(`gen_h21_short` en `_listas2.py`): vende en **resistencias de volumen defendidas ≥2 veces**
(máximos previos pegados al nivel + rechazo), stop `lvl+0.6·ATR`, target estructural abajo.
Cableado en `liquidity_app_backtest.py` (gens = h5 + h21 + h21_short).

| Cartera (M15, salida M1, vol ON) | n | OOS n | WR | OOS avgR | OOS netR | long/short |
|---|---|---|---|---|---|---|
| BASE (h5+h21) | 901 | 261 | 73.8% | +1.004 | +262.2 | 77/23 |
| **+mirror (h5+h21+h21s)** | **1210** | 355 | **75.2%** | +0.988 | **+350.8** | **57/43** |

El mirror tiene edge propio (OOS avgR **+0.94**, WR 79%, n=309 — *mejor* que el H21 long +0.49)
y aguanta fills 10 bps (OOS +0.68). +34% trades y +34% netR OOS sin diluir el avgR.
⚠️ Vigilar: el mirror rinde más en OOS que en IS → puede deberse a rotaciones bajistas del
tramo OOS (mar-2026+), no solo al edge. Confirmar cuando haya más OOS.
**✅ Portado a live (2026-06-21):** `live/levels.py` emite `poc_defendido_short` (resistencia
defendida por máximos ≥2 veces, stop `+0.6·ATR`, target estructural abajo). El harness
(`paper_liquidity.py`) es agnóstico al lado → el paper ya coloca shorts del mirror. **Requiere
restart del servicio Railway** para tomar el código nuevo (el proceso vivo corre el código viejo).

## 7c. ⭐ Re-auditoría del edge + fee honesto (2026-06-21)

Tras la duda "no cuadra" (stops minúsculos → RR gigante). Tres pruebas independientes la
**refutan**: (1) `corr(stopPct, R) = −0.09` (≈0, no es stop-chico→R-grande); (2) los winners R>3
tienen stop medio 0.20% ≈ el resto 0.22% (no salen de stops menores); (3) sobrevive un piso de
stop forzado de 0.50% (OOS avgR +0.40, WR 83%). El top-5 es solo 11% del netR → no es lotería.

**Fee honesto cableado** (`_audit_fee.py`, y en `liquidity_app_backtest.py`): el 88% de las
salidas son a mercado (breakeven 58% + stop 25% + timeout 5%), solo 12-14% (target) son límite.
Cobrar taker (5.5bps/lado) en esas salidas cuesta **−10% de netR** (avgR +0.83→+0.72, OOS
+0.99→+0.89). El edge aguanta. **Peor caso apilado** (fee honesto + fills 10bps + piso 0.20%):
OOS avgR **+0.47**, WR 70%, n=199 → sigue claramente positivo. Reproducir: `python backtest/_audit_edge.py`,
`_audit_fee.py`, `_audit_mirror.py`. La única pregunta abierta sigue siendo el fill ratio maker real.

## 7d. ⭐⭐ Análisis de MARKOUT post-fill (2026-06-21) — la pregunta del fill ratio, RESPONDIDA con datos

Métrica de mesa de market-making: tras un fill límite en el nivel, ¿a dónde va el precio? Medido con
el **tape tick-a-tick real** (raw_trades, 365d, 5.310 fills), sin simular salida ni PnL → aísla la
**calidad del fill**, lo único que el backtest no ve. `backtest/_audit_markout.py`.

| Régimen | +1s | +5s | +30s | +60s | +300s | n |
|---|---|---|---|---|---|---|
| TODO | −0.77 | −0.91 | −0.79 | −0.21 | **+5.59** | 5310 |
| **VOL-HIGH** | −0.82 | −0.94 | −0.69 | +0.20 | **+7.56** | 2826 |
| VOL-LOW | −0.72 | −0.87 | −0.90 | −0.68 | **+3.35** | 2484 |

(bps; + = favorable. Spread BTC perp ≈ 0.5-1bp, fee maker 2bps/lado.)

**Tres conclusiones:**
1. **Selección adversa real pero pequeña** (~0.8 bps los primeros 30s): el flujo informado te llena y
   el precio continúa un pelín antes de revertir. Manejable.
2. **El edge es REAL** — confirmado por una lente independiente del backtest. A +300s ya es +5.6 bps
   (y los trades reales aguantan horas hasta la rotación ~3.7%=370bps). La reversión aplasta a la
   selección adversa Y al fee.
3. ⭐ **El miedo "VOL-HIGH = fills tóxicos" queda REFUTADO** (era LA pregunta abierta): en VOL-HIGH la
   selección adversa inmediata es igual de chica, pero la reversión es **mucho mayor** (+7.56 vs +3.35
   @5min). El filtro de volatilidad queda vindicado a nivel de microestructura. El mirror H21s es el
   mejor (+8.27 @5min VOL-HIGH).

**Implicación operativa:** como la reversión tarda minutos, **cualquier salida rápida destruiría el
edge** (cristaliza la selección adversa). Confirma target estructural + aguante. Lever destapado:
colocar el límite 1-2 bps más profundo para esquivar la continuación inmediata (ver §7e).

## 7e. Offset de profundidad + compounding (2026-06-21) — `_audit_offset_compound.py`

**Offset de entrada (raspar la selección adversa de 0.8bps del §7d): NO es palanca de dinero.**
Colocar el límite N bps más profundo dispara el avgR (+0.72→+3.22 @10bps) PERO es **artefacto**:
la entrada se acerca al stop → encoge el denominador del riesgo → R infla sin más dinero. Tell: el
**OOS netR queda plano** (+317→+379) mientras el avgR se triplica. El `n` casi no baja por el cap
2/día (engañoso; el fill ratio real caería). Veredicto: **entrada en el nivel (offset 0-2bps máx)**;
el edge está en el aguante, no en raspar la entrada. (Mismo patrón "stop-chico→R-gigante" del §7c.)

**Compounding sin retiros (riesgo 1% del capital/trade, $500 inicial):** $500→**$2.1M** en 363d
(×4189); OOS ×21.2 en 109d. Matemáticamente order-invariant (producto de (1+0.01·r); el orden solo
afecta el DD, no el destino). PERO es **fantasía de capacidad**: a $2M no hay fills maker en estos
niveles de nicho. Real a tamaño chico (miles de $), se rompe al crecer. Por eso el app usa riesgo
FIJO. Para el edge → avgR/WR; para equity desplegable → falta techo de capacidad (lo da el paper).
Riesgo de ruina nulo: peor trade −2.5R = −2.5% capital con 1%, sin apalancamiento.

## 7f. Funding (descartado) + Capacidad (el techo del compounding) — 2026-06-21

**Funding EV — descartado, demasiado chico.** `_audit_funding.py`: funding BTC perp +0.33bps/8h,
positivo 73% (longs pagan, shorts cobran). Pero el hold medio es 3.9h (mediana 1.2h) → solo 0.45
eventos/trade. Impacto real: longs −1.3%, shorts +1.4% del avgR, **neto −$14 en todo el año**.
Direccionalmente correcto (viento de cola del mirror) pero irrelevante en magnitud. No es lever.

**Capacidad — el techo realista que le faltaba al compounding.** `_audit_capacity.py`: volumen taker
que cruza el nivel por fill (= tope de fill maker) = **mediana 82 BTC ($7.3M)**, VOL-HIGH 107 BTC.
Con stops 0.19% (notional = capital·5.3), capital desplegable: **realista (25% del flujo) ≈ $346k**
(nivel mediano) / $149k (p25); conservador (10%) ≈ $138k. **El compounding a $2.1M es inalcanzable**
(requeriría ~$11M notional/trade); pero el techo real son **bajos-medios cientos de miles de $**, no
miles → estrategia seriamente desplegable. Compounding real hasta ~$300k, ahí se aplana.

## 7g. Sizing por componente (2026-06-21) — `_audit_sizing.py`

Tilt de riesgo por rendimiento IS (pesos derivados SOLO de IS → OOS, honesto). Sube retorno pero
baja Sharpe: equal-weight OOS netR +317 Sharpe +6.97 ; tilt-avgR +374 (+18%) Sharpe +6.57 (−6%).
H5 fue el mejor IS y OOS (+1.195) → el tilt no es puro overfit, pero concentrar pierde
diversificación. **Default: equal-weight; tilt SUAVE hacia H5 defendible si priorizas retorno.**

## 8. Estado y siguiente paso

- ✅ Estrategia definida y backtesteada (3 niveles + cartera).
- ✅ Filtro de volatilidad descubierto, validado IS/OOS y **cableado** en cartera (`--volfilter`)
  y en el harness (tag `vol_regime` + `--high-vol-only`).
- ✅ Harness mide **fill ratio y PnL separados por régimen de volatilidad** (VOL-HIGH/VOL-LOW)
  para validar si en alta vol los fills empeoran (selección adversa).
- ⏳ **Pendiente:** dejar correr el dry-run días/semanas; leer el fill ratio por régimen;
  si en alta vol el fill ratio aguanta y el avgR de fills se sostiene → testnet post-only.

**Config recomendada:** maker-límite, filtro ATR>mediana(500), riesgo fijo 1%, cap ~2-3 trades/día
por nivel, margen de selección adversa 2 bps en backtest. Ver `backtest/_consolidated.py`,
`backtest/_filters.py`, `live/` para reproducir.

---

## 9. Visualización en la app (rbf-review) — solo liquidez

La app `apps/rbf-review` quedó **enfocada solo en esta estrategia** (se quitaron los tabs MTF/Chart).
- Backtest: `backtest/liquidity_app_backtest.py` (lo invoca `vite.config.ts` vía `/api/backtest/liquidity`).
- Emite los trades en el shape `Trade` → lista clicable + chart de velas (entry/stop/target/exit) + stats.
- Riesgo **FIJO $5/trade (sin compounding)**, a propósito: el compounding 2% infla (ej. MTF Spot Longs
  mostraba $500→$26k = +5113%, artefacto exponencial). Para comparar estrategias mirar **avgR y WR**,
  no el equity compuesto.
- Correr: `cd apps/rbf-review && npm run dev` → "Ejecutar Backtest".

## 10. ⭐ Fix de targets ESTRUCTURALES (no scalp de ATR fijo)

**Problema detectado visualmente:** los componentes POC usaban target = múltiplo fijo de ATR
(0.6 ATR stop, ~1.8R), lo que producía **micro-scalps de ~0.2%** que dejaban toda la rotación real
sobre la mesa. "+1.8R" engañaba porque R era diminuta (0.1%).

**Fix (`struct_target` en `backtest/_listas2.py`):** el target pasa a ser el **siguiente nivel de
liquidez REAL** — el más cercano por encima (long) / debajo (short) entre VAH/VAL, swing_high/low_50,
máx/mín del día previo — con **parcial 50% en el POC + breakeven**. Captura el movimiento real, no un scalp.

## 11. Análisis de timeframe (M5 vs M15 vs H1) — con targets estructurales

| TF | stop% med | target% med | n candidatos | OOS WR | OOS avgR |
|---|---|---|---|---|---|
| M5 | 0.097% | 0.283% | 30.565 | 47% | +0.25 |
| **M15** | 0.169% | 0.363% | 6.756 | **72%** | **+0.92** |
| H1 | 0.329% | 0.609% | 816 | 88% | +1.24 |

**Por qué M15 > M5 (la razón real):** NO es que los targets de M15 sean "mejores" — son el mismo tipo
de nivel estructural, solo que **todo escala con el TF** (stop y target ~2× más grandes). La mejora
viene de que **el stop más grande sobrevive al ruido**: en M5 el stop (0.097%) lo barre cualquier
wiggle/spread → WR 47%; en M15 (0.17%) aguanta → WR 72%. Y el trade pasa a ser un movimiento real,
no un scalp de 0.1%.

⚠️ **Caveat anti-snooping:** subir de TF reduce muchísimo la muestra (M5: 30k candidatos → H1: 816).
El WR 88% de H1 es en parte **optimismo de muestra chica**. M15 se eligió por equilibrio (WR alto +
~2 trades/día + muestra decente), no porque sea óptimo garantizado. El visual usa **M15 por defecto**.

## 12. Parámetros HARDCODEADOS (revisar antes de desplegar)

En `backtest/liquidity_app_backtest.py` / `_listas2.py` (los que más mueven el resultado marcados ⭐):

| Parámetro | Valor fijo | Afecta |
|---|---|---|
| Datos | `data/bybit-perp/processed/btcusdt_perp_m1.parquet` (real Bybit perp, 2025-01-01→2026-06-17, 767k velas M1) | — |
| Inicio backtest | **2025-06-19 (era tick verificada, ~365d)** — el pre-tick OHLCV existe pero NO se usa | rango |
| Capital / riesgo | $500 / $5 fijo (1%, sin compounding) | equity |
| Fee | maker 4 bps RT | rentabilidad |
| **Timeframe** | **M15** | ⭐ |
| **Filtro volatilidad** | ATR > mediana móvil(500) | ⭐ |
| **Stop H21** | 0.6 × ATR | ⭐ tamaño stop |
| Stop H5 | OB high/low ± 0.25 ATR | tamaño stop |
| Margen selección adversa | 2 bps | fills |
| Cap trades/día | 2 por componente | frecuencia |
| Cooldown | 6 barras | frecuencia |
| min_RR | 1.2 | filtra trades |
| Timeout | 8h | salidas |
| Tolerancia nivel | 0.2% | fills |
| Parcial / breakeven | 50% en POC, stop→BE | WR/avgR |

**Datos confirmados:** parquet real, periodo completo, columnas (fp_poc, swing, vp_vah) pobladas
pre y post era-tick (fp_poc 1% NaN pre-tick). Sin datos sintéticos ni gaps. IS<2026-03 / OOS≥2026-03.

**Pendiente de decisión:** exponer TF / filtro vol / riesgo como selectores en la app (en vez de
hardcodear), y decidir si aplicar el filtro de volatilidad solo a los componentes POC.

---

## 13. Simulador honesto (salidas en M1) + RR híbrido + filtros de entrada (2026-06-20)

### 13.1 Fix del simulador: entrada en TF, SALIDA en M1
Evaluar stop/target en velas grandes (M15/H1) tiene **ambigüedad intrabar**: una vela de 15min
puede contener stop y target a la vez y el backtest no sabe cuál se tocó primero. Fix
(`run_level_m1exit` y el `run()` del app): la entrada se decide en el TF, pero **stop/target se
simulan barra-a-barra en M1** (`load_m1_exit`). Resultado — los números reales **NO estaban
inflados** por esto (la alarma previa era un caso patológico con stop forzado diminuto):

| TF | OOS n | WR | avgR | PnL (salidas M1) |
|---|---|---|---|---|
| M5 | 363 | 56% | +0.609 | +$1.105 |
| **M15** | 225 | **74%** | **+1.055** | **+$1.186** |
| H1 | 73 | 95% | +1.515 | +$553 |

M15 confirmado genuinamente mejor (señal más limpia en velas grandes + stop que aguanta el ruido,
NO artefacto de salida). H1 mejor por trade pero pocos (optimismo de muestra). El app usa **M15 + salidas M1**.

### 13.2 ¿M15 es el timeframe o solo la temporalidad de entrada?
Es (debería ser) solo la **temporalidad de ENTRADA**. Mover el TF cambia 3 cosas: tamaño de stop
(ATR), granularidad de fills, y antes también la de salida (ya arreglado → M1). Forzar el stop de
M15 sobre M5 **no** replica M15 (WR sigue ~53%, no 72%) → la mejora NO es solo el stop: es también
la **señal más limpia** decidida sobre velas de 15min (menos ruido que 5min).

### 13.3 RR híbrido (piso/techo) — NO mejora
| Variante (M15, salidas M1) | n | WR | avgR | PnL |
|---|---|---|---|---|
| **estructural puro** | 225 | 74% | +1.055 | **+$1.186** |
| estr. piso 1.5R | 261 | 67% | +0.833 | +$1.087 |
| estr. techo 4R | 225 | 76% | +1.039 | +$1.169 |
| estr. piso2 techo5 | 261 | 65% | +0.859 | +$1.121 |

El estructural puro (gate min_RR 1.2) gana: poner piso añade trades peores, poner techo recorta
winners. **No usar híbrido.**

### 13.4 Filtros de entrada / velas pre-entry / orderflow — ablación
| Filtro de entry (sobre BASE=vol) | n | WR | avgR | PnL |
|---|---|---|---|---|
| **BASE (solo volatilidad)** | 225 | 74% | +1.055 | **+$1.186** |
| +approach 3 velas (precio bajó al nivel) | 187 | 73% | +1.129 | +$1.056 |
| +OBI a favor | 147 | 73% | +1.129 | +$830 |
| +big trade absorbido en nivel | 12 | 83% | +1.984 | +$119 |
| +spread estrecho | 136 | 68% | +0.745 | +$507 |
| +cvd_slope / +dz exhaustión | 113 / 26 | 68% / 50% | +0.77 / +1.11 | +$437 / +$144 |

**Ningún filtro bate la BASE en PnL.** "Approach 3 velas" y "OBI a favor" suben el avgR (calidad)
pero cortan volumen → menos PnL: son filtros de calidad, no de dinero. Las confirmaciones clásicas
de orderflow (delta, CVD, spread) **empeoran** — re-confirma que el orderflow fino no aporta. El
único de alta calidad es "big trade absorbido en el nivel" (WR 83%, +1.98R) pero n=12, insuficiente.
"¿Cuántas velas antes?": approach 3 > 5 > 10, pero ninguna supera la base.

**Conclusión:** el entry ya está bien filtrado por **el nivel estructural + el régimen de
volatilidad**. La gestión óptima es **target estructural puro + salida M1**. Más filtros = menos PnL.
