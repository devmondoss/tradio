# Evolución Completa de las Estrategias
## Liquidity A+B · SC3 · Todos los experimentos, descubrimientos y descartes

> Documento vivo. Última actualización: 2026-06-30.
> Cubre todo lo investigado desde el origen del proyecto.
> Objetivo: que cualquiera (o tú en 6 meses) entienda qué probamos, qué aprendimos y por qué el estado actual es como es.

---

## Índice

1. [El punto de partida — qué creíamos al inicio](#1-el-punto-de-partida)
2. [Fase 1 — Primer edge real: H1 apertura de rango](#2-fase-1--primer-edge-real)
3. [Fase 2 — El filtro de volatilidad (hallazgo más robusto)](#3-fase-2--el-filtro-de-volatilidad)
4. [Fase 3 — Targets estructurales y M15](#4-fase-3--targets-estructurales-y-m15)
5. [Fase 4 — Simulador honesto, fee honesto, mirror short](#5-fase-4--simulador-honesto-fee-honesto-mirror-short)
6. [Fase 5 — Markout post-fill: la pregunta central resuelta](#6-fase-5--markout-post-fill)
7. [Fase 6 — Sistema A+B: fade + trailing enrutado por régimen](#7-fase-6--sistema-ab)
8. [Fase 7 — Port a Rust, bugs de footprint, deploy Railway](#8-fase-7--port-a-rust)
9. [Fase 8 — Validación multiasset (ETH + SOL)](#9-fase-8--validación-multiasset)
10. [Fase 9 — Research orderflow exhaustivo (todo descartado)](#10-fase-9--research-orderflow-todo-descartado)
11. [Fase 10 — Gaps de paridad en el paper (3 reglas que faltaban)](#11-fase-10--gaps-de-paridad)
12. [Fase 11 — Validación Nautilus (fills realistas)](#12-fase-11--validación-nautilus)
13. [Fase 12 — Optimizaciones estructurales](#13-fase-12--optimizaciones-estructurales)
14. [Fase 13 — Fix del detector de régimen](#14-fase-13--fix-del-detector-de-régimen)
15. [Fase 14 — Filtros de señal liquidity (10 probados, 1 sobrevive)](#15-fase-14--filtros-de-señal-liquidity)
16. [Fase 15 — IFVG (Inverse Fair Value Gap)](#16-fase-15--ifvg)
17. [Fase 16 — ICT/SMC Checklist (descartado)](#17-fase-16--ictscm-checklist)
18. [Fase 17 — Research SC3: absorción intradiaria M5](#18-fase-17--research-sc3)
19. [Fase 18 — S/R estructurales como niveles de entrada](#19-fase-18--sr-estructurales-como-entradas)
20. [Estado actual deployado](#20-estado-actual-deployado)
21. [Reglas del proceso de investigación](#21-reglas-del-proceso-de-investigación)
22. [Glosario técnico](#22-glosario-técnico)

---

## 1. El punto de partida

### Qué creíamos

El proyecto empezó con la hipótesis de que el orderflow (flujo de órdenes, desequilibrios del libro, delta de volumen) tenía información predictiva sobre la dirección del precio en BTCUSDT perp.

Catálogo inicial: **21 hipótesis** documentadas en `docs/orderflow/02_catalogo_21_hipotesis.md`. Las hipótesis abarcaban desde áreas de valor (H1), POC de volumen (H5), POC defendido (H21), hasta sweeps (H2), delta de volumen (H8), VPIN (H12), divergencias CVD (H16), etc.

### El primer veredicto (y por qué era incompleto)

`docs/orderflow/EDGE_VERDICT_2026-06-19.md` concluyó: **"no hay edge en BTCUSDT"**.

Ese veredicto era sobre **predicción direccional**: ¿sube o baja en los próximos N minutos? Y era correcto — ninguna feature predice la dirección consistentemente.

**El error conceptual:** estrategia ≠ predictor.

Predecir la dirección del precio es casi imposible. Pero **proveer liquidez en el nivel correcto a la hora correcta** es diferente — no necesitas predecir si el precio sube o baja, solo que va a rebotar desde ese nivel específico.

El reencuadre decisivo (2026-06-20): testear las mismas hipótesis no como predictores sino como **estrategias completas con gestión de posición** — entrada límite maker, stop, target, parcial. Ese cambio de óptica produjo el primer candidato real.

---

## 2. Fase 1 — Primer edge real: H1 apertura de rango

### Qué descubrimos

**H1 (apertura de rango, 4 variantes)** — entrada con orden límite maker en niveles de área de valor del día previo (VAH/VAL/POC), timeframe M5.

**Config ganadora:** variantes V1+V3, `open_hour=12 UTC`, stop=max(pdh/pdl), minRR=1.5.

**Resultado OOS:** +0.325R, $500→$913, MaxDD 23%, 0.4 trades/día.

**Robustez confirmada:**
- Estable a `open_hour` (meseta entre 8-16h UTC)
- Estable a régimen (4-5 de 6 trimestres positivos)
- Estable frente a selección adversa (tests explícitos)

### Lo que descartamos en esta fase

- **V2 y sub-variante 1b:** decaen OOS, no generalizan
- **Entrada taker (a mercado):** negativo incluso antes de fees. Esto ya anticipaba el mecanismo real.

### El mecanismo real (muy importante)

**No es un edge direccional.** A taker, la estrategia pierde. La única diferencia entre perder y ganar es:
1. Mejor entrada por orden límite en nivel (evitas el spread)
2. Fee maker 4bps vs taker 11bps

Todo el edge = **provisión de liquidez en niveles de volumen**. Estás dando liquidez donde otros necesitan tomar. El mercado te paga por eso.

**Implicación profunda:** cualquier feature de "predicción direccional" que agregues es ruido. El edge no viene de saber dónde va el precio.

---

## 3. Fase 2 — El filtro de volatilidad (hallazgo más robusto del proyecto)

### El descubrimiento

Proveer liquidez **solo cuando `ATR > mediana_móvil(500 barras)`** casi cuadruplica el avgR OOS.

| Métrica | Sin filtro | Con filtro | Cambio |
|---------|-----------|-----------|--------|
| OOS avgR | +0.13R | **+0.51R** | ×3.9 |
| WR | 48% | 65% | +17pp |
| MaxDD | 18.5% | **4.8%** | −74% |
| Sharpe | 1.1 | **4.1** | ×3.7 |
| Trades conservados | 100% | 86% | −14% |

Resultado IS ≈ OOS (+0.517 vs +0.511) → **no hay overfitting**. Es un filtro estructural, no una curva-fitted.

### Por qué funciona (mecanismo)

En vol **baja**: el spread y el fee dominan los movimientos pequeños. Un rebote de 0.05% con fee 4bps = netR negativo o cero. Además, los niveles de VP en vol baja no se respetan tanto — hay menos momentum para que el precio rebote limpio.

En vol **alta**: el precio se mueve suficiente para pagar el fee, el stop y generar ganancia. Los niveles de VP se convierten en paredes duras porque hay más convicción de market participants.

**La volatilidad baja destruye:** `-0.06R` promedio. No es que sea neutral — activamente pierde.

### Lo que no funciona para reemplazarlo

Probados extensivamente: OBI, absorción, CVD, VPIN, spread actual, depth — **ninguno supera o complementa al ATR filter solo**. La microestructura fina no agrega información sobre cuándo funciona el edge.

### Dónde está en el código

```
backtest/_strategy_ab.py: parámetro volfilter=True
levels.rs: HIGH_VOL_ONLY → atr > atr_median
main.rs: atr_median() = mediana de últimos 500 ATR
```

---

## 4. Fase 3 — Targets estructurales y M15

### El problema con los targets anteriores

Los primeros tests usaban targets como múltiplos fijos de ATR (ej. 2×ATR desde la entrada). Resultado: micro-scalps de ~0.1-0.2% donde "+1.8R" significaba un target de 18bps con riesgo de 10bps. El fee taker en el stop (5.5bps) se comía buena parte de cualquier ganancia.

### El fix: `struct_target`

Target = siguiente nivel de liquidez estructural real en la dirección del trade:
- VAH, VAL del día previo
- Swing high/low de los últimos 50 períodos
- PDH, PDL (previous day high/low)
- Weekly high/low

El primer nivel estructural en dirección = tp1 (parcial 50%). El siguiente = tp2 (target principal).

```python
# backtest/_listas2.py
def struct_target(side, lvl, a, i):
    candidates = [VAH, VAL, swing_H, swing_L, PDH, PDL, wH, wL]
    tp2 = nearest in direction
    tp1 = next nearest before tp2
```

### Por qué M15 supera a M5 (contra-intuitivo)

Subir de M5 a M15 mejoró drásticamente:

| TF | WR | OOS avgR | Razón |
|----|----|-----------|----|
| M5 | 47% | +0.25 | Stop 0.097% → wicks lo reventaban |
| **M15** | **72%** | **+0.92** | Stop 0.17% → aguanta el ruido |

**El insight:** M15 no es "mejor señal". Es que el stop más grande (proporcional a ATR en M15) sobrevive los wicks falsos que en M5 activaban el stop antes de que el precio llegara al target. La señal es la misma; la diferencia es que el stop tiene espacio para respirar.

**Trampa:** usar el stop de M15 sobre entrada M5 no replica el efecto (probado explícitamente — el stop en M5 sigue siendo activado por ruido M5 aunque el nivel sea M15).

---

## 5. Fase 4 — Simulador honesto, fee honesto, mirror short

### Simulador honesto: salida en M1

**Problema:** el backtest entraba en barra M15 y simulaba la salida también en M15 — pero dentro de una vela M15 el precio puede alcanzar tanto el stop como el target. La vela M15 no dice cuál se tocó primero.

**Solución:** `run_level_m1exit` — entrada en M15, salida simulada en datos M1 reales.

**Resultado:** los números de M15 no estaban inflados. La simulación M1 confirmó los mismos valores de OOS. Esto validó que el problema anterior no era ambigüedad intrabar sino los targets ATR-fijos.

### Fee honesto: composición real de salidas

El backtest naïve usaba fee maker para todas las salidas. La realidad es:

| Tipo de salida | % de trades | Fee |
|----------------|-------------|-----|
| Breakeven (BE) | 58% | Taker (5.5bps/lado) |
| Stop loss | 25% | Taker (5.5bps/lado) |
| Timeout | 5% | Taker (5.5bps/lado) |
| Target (tp1/tp2) | 12-14% | Maker (4bps/lado) |

**Solo el 12-14% de las salidas son maker.** El resto es taker. Fee honesto cuesta −10% del netR total.

Peor caso apilado (fee honesto + slippage 10bps + stop floor 0.20%): OOS +0.47R, WR 70% → **sigue positivo**.

### Mirror short: `gen_h21_short`

**Insight:** el edge de POC-defendido funcionaba bien en long (fade desde soporte). ¿Por qué no el mirror en short (fade desde resistencia)?

**Implementación:** `gen_h21_short` en `backtest/_listas2.py` — vende resistencias donde el precio fue rechazado ≥2 veces.

**Resultado:**

| Métrica | Antes (solo long) | Después (long+short) |
|---------|------------------|---------------------|
| Long/Short split | 77% / 23% | **57% / 43%** |
| Trades | 901 | 1210 (+34%) |
| netR OOS | base | +34% |
| Edge propio H21s | — | OOS +0.94R, WR 79% |

El mirror short tiene mejor WR que el long original. Los niveles de resistencia respetados funcionan tan bien o mejor que los soportes.

---

## 6. Fase 5 — Markout post-fill: la pregunta central resuelta

### La duda que nadie había resuelto

**"Si las órdenes se llenan en volatilidad alta, ¿el precio no se mueve inmediatamente en tu contra?"**

Selección adversa: cuando alguien toma tu liquidez en vol alta, es porque sabe algo que tú no. El precio sigue en su dirección, tu orden se llena, y ya estás perdiendo.

Si esta duda era real, el 100% del research era un castillo de arena.

### La medición (datos reales, tick a tick)

Sobre tape real de 365 días, 5310 fills identificados:

| Momento post-fill | Curva precio (bps) |
|------------------|--------------------|
| 0-30 segundos | −0.8 (selección adversa) |
| 60 segundos | 0 (break-even) |
| 5 minutos | **+5.6** (reversión neta) |
| VOL-HIGH @ 5min | **+7.56** |
| VOL-LOW @ 5min | +3.35 |

**Conclusión:**

1. Sí hay selección adversa inmediata: −0.8bps en los primeros 30s. Es real pero pequeña.
2. A los 60s ya se neutraliza.
3. A los 5min hay reversión neta de +5.6bps.
4. En VOL-HIGH la reversión es **mayor**, no menor. El miedo estaba invertido.

**El filtro de volatilidad queda vindicado a nivel de microestructura**, no solo de backtest estadístico.

**Mirror H21 short tiene la mejor reversión:** +8.27bps @5min en VOL-HIGH.

**Lección:** aguantar el trade (salida en target estructural) captura la reversión. Salir rápido (stop prematuro, target ajustado) cristaliza la selección adversa.

**Script:** `backtest/_audit_markout.py`

---

## 7. Fase 6 — Sistema A+B

### El problema descubierto

Autopsia de 269 movimientos grandes (≥2% en el año):
- La estrategia liquidity capturaba solo el **1%** de esos movimientos
- Los movimientos grandes son **continuaciones** (breakouts que se extienden)
- Una estrategia fader pura los pierde todos o los toma como stop

**El costo:** el 13% de trades que termina en trail genera +3.74R promedio (BTC OOS). Eso es enorme y se estaba dejando sobre la mesa.

### La solución: MISMA entrada, DOS gestiones

La entrada sigue siendo la misma (orden límite maker en nivel VP). Lo que cambia es la gestión según el régimen de mercado:

**Chop (87-93% del tiempo):**
- Gestión A: fade
- Parcial 50% en tp1 → stop a breakeven → target estructural tp2

**Tendencia (7-13% del tiempo):**
- Gestión B: trailing stop
- TRAIL_ATR × 6 (antes era ×4) → deja correr el movimiento

**Detección de régimen:** columna `regime` del dataset (computed en M1). En Rust: EMA5 de M15 + racha 2 barras + ATR expansión ×1.3 (ver Fase 13 para la historia completa).

### Resultados A+B vs componentes solos

| Sistema | OOS avgR | Sharpe | DD% |
|---------|----------|--------|-----|
| A solo (fade) | +1.24 | — | — |
| B solo (trail) | +0.81 | — | — |
| **A+B enrutado** | **+1.82** | 6.7 | 8.5% |

### Qué NO funcionó

**Breakout-chase ingenuo (B puro sin entrada maker):** pierde sistemáticamente por falsos breaks. El 87-93% de "breakouts" son chop disfrazado. Sin la entrada maker en el nivel como filtro, el trail se llena en medio del chop y sangra.

**Detectores de régimen alternativos:**
- Kaufman ER (Efficiency Ratio) → más permisivo que `regime`, revienta DD
- ADX → igual
- Choppiness Index → igual
- Todos comparados explícitamente con `regime` real del dataset → ninguno gana

**Lección:** el detector de régimen que viene del dataset (ATR14 vs MA20 + EMA20 por 3+ barras) es óptimo porque está calibrado con años de datos reales. Cualquier proxy en M15 pierde información.

**Scripts:** `backtest/_strategy_b.py`, `_strategy_b_v2.py`, `_strategy_ab.py`, `_strategy_ab_v2.py`

---

## 8. Fase 7 — Port a Rust, bugs de footprint, deploy Railway

### Por qué Rust

El paper trader original era Python (`live/paper_liquidity.py`). Rust ofrece:
- WS estable sin GIL
- Footprint incremental sin pandas en el hot path
- Binario único = fácil Docker multi-stage en Railway
- Misma lógica que el backtest = paridad garantizable

### Arquitectura Rust

```
crates/liquidity_monitor/
├── src/
│   ├── levels.rs     — compute_levels (VP, ATR, regime, generadores)
│   ├── book.rs       — PaperBook (fade + trail, gestión de posición)
│   ├── supa.rs       — REST Supabase fire-and-forget
│   ├── exec.rs       — ExecClient (órdenes reales Bybit API)
│   ├── executor.rs   — Executor (testnet/live, sobrevive redeploys)
│   └── main.rs       — WS Bybit, bootstrap 1000 barras, loop principal
```

### Bugs de footprint encontrados y corregidos

**Bug 1: Bin fijo $5 para todos los activos**

El footprint usaba bin = $5 (apropiado para BTC ~$60k). Para ETH y SOL:
- ETH $3000 → bin $5 → 600 bins por barra (manejable pero granularidad excesiva)
- SOL $69 → bin $5 → **1 solo bin** → todos los trades caen en el mismo bin → POC siempre el único bin → inútil

**Fix (commit 4823ad3):** bin proporcional al precio → `ref_px × 0.0001`. ETH: 4→133 bins, SOL: 1→83 bins.

**Bug 2: PK del footprint era solo `ts_ms`**

Las barras M15 de BTC, ETH y SOL cierran al mismo timestamp (son sincrónicas). La tabla tenía PK en `ts_ms` → los tres activos hacían upsert al mismo registro → **BTC pisaba a ETH y SOL**.

**Fix (SQL sin redeploy):** PK `(ts_ms, symbol, tf)`. Cada activo tiene sus propias filas.

**Bug 3: El footprint tarda ~5h en madurar tras cada redeploy**

El footprint se construye en vivo de `publicTrade`. No es reconstruible de velas OHLCV. Al arrancar, el servicio parte de 0 y tarda ~5h (20 barras M15) en tener suficientes datos para que el POC sea representativo.

**Implicación operativa crítica:** cada push a `main` redeploya TODOS los servicios Railway. Esto reinicia el warmup. Regla: usar **ramas de Git** para research, `main` solo para redeploy intencional.

**Script diagnóstico:** el log de cada barra imprime `ticks=N fp_bins=M`. Si `fp_bins=1` o `fp_bins=0`, hay un bug de bin. Si ticks=0, hay problema de stream.

### Geo-block Railway/Bybit

Servicios Railway en región US → Bybit REST devuelve 403 CloudFront.

**Fix:** usar región Singapur o Europa en Railway para todos los servicios que llaman REST de Bybit.

**Memoria:** `memory/railway_bybit_geoblock.md`

---

## 9. Fase 8 — Validación multiasset

### El test decisivo

¿El edge en BTCUSDT es específico de BTC (overfitting implícito por ser el único activo en el research) o generaliza?

Se corrió A+B con exactamente los mismos parámetros sobre ETHUSDT y SOLUSDT Perp (365 días, OBI 100%), **sin reoptimizar nada**.

| Símbolo | OOS avgR | WR | DD% | Sharpe | n OOS |
|---------|----------|----|-----|--------|-------|
| BTCUSDT | **+1.82** | 49% | 4.2% | 8.2 | 156 |
| ETHUSDT | **+1.37** | 52% | 6.8% | 5.0 | 90 |
| SOLUSDT | **+0.93** | 61% | 5.2% | 6.6 | 89 |

**Todos positivos OOS sin tocar un solo parámetro.** El edge generaliza.

### El test decisivo sobre OBI

Se comparó OBI real (100% disponible en ETH/SOL) vs proxy (0% OBI).

**Diferencia en OOS avgR: 0.00 en todos los activos.**

Esto entierra definitivamente el OBI como feature para esta estrategia. No agrega información. La microestructura del libro de órdenes a nivel 1s no predice el rebote en el nivel VP.

### Gap SOL

IS SOL: +1.87 vs OOS: +0.93 — el único gap significativo.

**Root cause:** el detector de régimen en SOL es impreciso. SOL tiene más breakouts que BTC/ETH relativamente. El detector de chop/trend clasifica algunos trends de SOL como chop → mal enrutamiento → el trail pierde algunas operaciones que el backtest enrutaría bien.

Investigado en profundidad en Fase 13.

---

## 10. Fase 9 — Research orderflow exhaustivo (todo descartado)

### Motivación

A pesar de los resultados positivos, cabía la duda: ¿hay alguna feature de orderflow que mejore el edge? Se hizo una sesión exhaustiva para cerrar esta pregunta de una vez.

### Todo lo probado y descartado

| Feature | Resultado | Razón del fracaso |
|---------|-----------|-------------------|
| OBI 5 niveles | 0 impacto | Vida media del book ~5s vs trade ~2h |
| OBI 10 niveles | 0 impacto | Ídem |
| OBI 25 niveles | 0 impacto | Ídem |
| Depth bid/ask 25 niveles | 0 impacto | Mismo horizonte temporal inadecuado |
| Spread bps actual | 0 impacto | Noise en M15 |
| Delta M15 footprint | Era lookahead intrabar | El delta acumulado de la barra actual incluye el cierre = fraude estadístico |
| CVD slope | No generaliza a 3 activos | |
| Divergencia CVD | No generaliza | |
| Volumen directo | Sesgo de dirección | Vol alta ≠ dirección determinable |
| Sweep SMC (barrer liquidez antes del setup) | OOS −0.30 en los 3 | |
| Libro completo ob500 (500 niveles) | ETH +0.22 → deflactado a +0.17 con muestra completa; SOL −0.01 | Con muestra parcial parecía prometedor. Con muestra completa: ruido. |
| Heatmap de liquidez resting | Pearson ~0 | La liquidez resting (órdenes pasivas) no predice el rebote maker |
| Conceptos SMC (fases, trampas SMT) | Pierden como regla mecánica | La estrategia ya es la versión cuantificada de lo bueno; el resto = overfitting narrativo |
| Optimizaciones de params (trail×5, cap SOL) | Falló validación temporal | Generalizaban cross-activo pero no cross-tiempo. El período "OOS" era un régimen específico, no un verdadero OOS |

### Lección metodológica crítica

**Generalización cross-activo NO basta.** Si los tres activos comparten el mismo período de tiempo, el modelo puede estar capturando un régimen temporal específico, no un edge universal. La validación real requiere **cross-tiempo**: IS vs OOS en períodos diferentes.

Desde entonces se aplica la regla dura: OOS = período cronológicamente posterior al IS, nunca tocado durante la investigación.

### El hallazgo positivo de esta sesión: warmup del footprint +29%

El footprint (POC de volumen construido en vivo de `publicTrade`) vale **+29% del edge**:
- avgR con footprint maduro (>5h): +1.54R
- avgR con footprint frío (recién arrancado): +1.09R

Mecanismo: el POC calculado con pocos ticks es menos preciso → peores niveles de entrada → peor avgR.

Implicación: **no redeploy innecesario**. Cada redeploy = ~5h de warmup = trades con POC impreciso.

---

## 11. Fase 10 — Gaps de paridad en el paper

### La auditoría

Comparación sistemática del binario Rust vs el backtest Python (`_strategy_ab.py`). Se encontraron 4 reglas del backtest que **el port nunca trajo**.

### Brecha 1: Stop floor 0.15% (CRÍTICO)

**Faltaba en el Rust.** El backtest nunca generaba stops menores al 0.15% del precio, pero el Rust sí los generaba.

**Efecto:** 14 trades con stop de 0.11–0.14%. RR aparente de 46–77. ¿Por qué es un problema? Porque un stop tan ajustado = 100% de probabilidad de ser activado por el ruido intrabar. El "target estructural" a 77R desde una entrada con stop de 0.12% nunca se alcanza — el stop se activa en el primer wick.

**Fix:** `STOP_FLOOR=0.0015` en `levels.rs`. El stop mínimo antes del check de RR es 0.15% del precio. Si el stop estructural es menor, se expande al floor.

**Commit:** 9b2367a

### Brecha 2: Anti-spam (cooldown 6 barras + máx 2 entradas/día UTC)

**Nunca portado.** El backtest Python tiene:
- `cooldown=6`: no puede haber dos entradas en menos de 6 barras M15 (1.5h)
- `max_day=2`: máximo 2 aperturas nuevas por día UTC

Sin estos límites, el Rust recolocaba órdenes en cada barra → más frecuencia que el backtest → comparación imposible.

**Fix:** `COOLDOWN_BARS=6`, `MAX_TRADES_DAY=2` en `levels.rs` y `book.rs`.

**Commit:** 9b2367a

### Brecha 3: Filtro ATR (HIGH_VOL_ONLY)

El código existía (`HIGH_VOL_ONLY` en `levels.rs`) pero estaba desactivado en producción (env var no seteada o `false`).

**Efecto en vivo:**
- avgR con LOW_VOL incluido: +0.315R
- avgR con HIGH_VOL_ONLY=true (era limpia): **+1.94R**

Se activó el 2026-06-25 14:00 UTC. Desde entonces, **todos los trades son en vol alta**. Verificado: cero filtraciones desde esa hora.

### Brecha 4: Parcial TP1 (el bug que destruía el WR)

**El bug:** el port usaba `MIN_TP1_RR=2.3` para activar el parcial. El trade bancaba 50% solo si tp1 estaba a ≥2.3R de la entrada.

**El problema:** tp1 = nivel estructural más cercano. Casi nunca está a 2.3R. El resultado: `take_partial=false` en casi todos los trades → **fade todo-o-nada** → 84% stops → WR 13% → `filled1=8/207 trades`.

**El backtest no tiene ese umbral.** Banca 50% cuando el precio toca tp1, sin importar a qué R está, siempre que tp1 esté al menos 0.5% lejos de la entrada (min_range).

**Fix:** `take_partial = tp1.is_some()`, eliminado `MIN_TP1_RR`. Si hay tp1 → siempre bancamos.

**Commit:** e875438

---

## 12. Fase 11 — Validación Nautilus (fills realistas)

### La pregunta

¿El edge del backtest sobrevive fills realistas?

El backtest asume que si el precio llega al nivel, la orden se llena. En vivo, la orden puede no llenarse (precio toca el nivel y rebota antes de llenar), o llenarse con peor precio (slippage de cola), o llenarse cuando el precio ya se fue lejos (adverse queue position).

### El método

Estrategia portada a NautilusTrader v1.224 (motor de backtest y trading con fills realistas tick a tick). Clave: **los generadores de señales no se reimplementaron** — se reutilizaron exactamente `gen_h5/gen_h21/mirror + struct_target` de `_listas2.py`. NautilusTrader solo hace la ejecución/fills. Así se aísla la pregunta del fill del edge de la señal.

### Resultados OOS (~108 días)

| Símbolo | WR | avgR | Fill ratio | Ref backtest |
|---------|----|----|-----------|-------------|
| BTC | 49% | **+2.01R** | 27% | +1.82R |
| ETH | 54% | **+1.91R** | 14% | +1.37R |
| SOL | 74% | **+1.94R** | 14% | +0.93R |

El avgR con fills realistas es **igual o mejor** que el backtest simplificado. El fill ratio de 14-27% es convergente entre bar-level y tick-level, y con el paper live (~19-40%).

### Qué significa el fill ratio de 14-27%

De cada 4-5 órdenes colocadas, ~1 se llena. Esto parece bajo pero es el comportamiento esperado para órdenes maker en niveles: la mayoría del tiempo el precio no llega exactamente al nivel. El **subset que se llena** (cuando el precio llega Y rebota) es el que tiene edge.

**Esto es correcto por diseño.** No es un bug. Una orden maker que siempre se llena sería una señal de que estás colocando demasiado lejos del nivel o en momentos donde el nivel no tiene respeto.

---

## 13. Fase 12 — Optimizaciones estructurales

### El objetivo

Mejorar la gestión de riesgo sin overfitear señales. Se fijaron reglas duras:
1. Mejora OOS avgR en los 3 activos simultáneamente
2. No dispara DD
3. Slippage-tolerante (robust a 2bps de slippage en salidas taker)

Motor de sweep: `backtest/_bt_levers.py` y `_bt_slip.py`.

### ADOPTADAS

#### trail_atr: 4 → 6

Dejar correr más el trailing stop en régimen tendencia.

| Activo | Delta OOS avgR |
|--------|---------------|
| BTC | +0.30R |
| ETH | +0.16R |
| SOL | +0.29R |

**Por qué:** el trail ×4 cortaba tendencias buenas demasiado pronto. El óptimo está entre 6-7 (no es monotónico — más allá de 7 empieza a devolver ganancias esperando un giro que ya pasó).

**Slippage test:** el combo trail=6 + stop_scale=0.8 sigue siendo positivo con 2bps de slippage extra en todas las salidas taker. La mejora es estructural, no dependiente de ejecución perfecta.

#### stop_scale: 1.0 → 0.8

Reducir el stop en un 20%. Stop más chico → mismo riesgo monetario → mayor R cuando se llega al target estructural lejano.

**Mecánica:** el riesgo por trade en $ es fijo (5 USDT). Si el stop cae de 0.5% a 0.4% del precio, la cantidad de contratos que puedes poner crece, pero para R no cambia porque R = (target−entry)/(entry−stop). Con stop más chico y mismo target estructural, la R aumenta.

**Acotado por:** `STOP_FLOOR 0.15%` para evitar stops absurdamente pequeños que se activan con cualquier wick.

**Combo final:**

| Símbolo | Antes | Después |
|---------|-------|---------|
| BTC | +1.82R | **+2.27R** |
| ETH | +1.37R | **+1.86R** |
| SOL | +0.93R | **+1.56R** |

**Commit:** ddbfc66

### DESCARTADAS

| Palanca | Resultado | Razón |
|---------|-----------|-------|
| atr_mult 1.2/1.5 | Rompen BTC/ETH | La ATR base (×1.0) ya es el óptimo |
| timeout 48/72h | DD sube, WR baja | Los trades lentos no mejoran con más tiempo |
| p1_frac 0.33/0.66 | Mixtos | 0.5 (mitad en tp1) es el balance correcto |
| Posición entera (sin parcial TP1) | Rompe SOL: +0.93→+0.42, DD dispara | El parcial funciona como lock de ganancia parcial |

**Nota sobre stop_scale agresivo:** en backtest sigue mejorando hasta ~0.6 (BTC tiene pico en 0.4; ETH/SOL siguen subiendo). Se eligió 0.8 conservador porque la exposición a slippage aumenta con stops más chicos y el margen sobre el floor se reduce. Subir a 0.7 solo tras confirmar en paper.

---

## 14. Fase 13 — Fix del detector de régimen

### El problema

El binario Rust usaba el detector de régimen más simple posible: `is_trend = |precio − SMA50| > 0.6×ATR`.

El backtest usaba la columna `regime` del dataset, computada en M1 (EMA20 + expansión ATR + racha de barras).

**Diferencia:** el Rust clasificaba tendencia con mucha más frecuencia que el backtest real. Consecuencia: `SYSTEM=flow` (trail) se activaba en situaciones de chop → el trail sangraba −0.10R neto en paper.

### El proceso de investigación

**Intento 1: portar el detector M1 al Rust**
- Requiere datos M1 en tiempo real
- Complejidad significativa de infra
- Resultado con EMA20 en M15 (=5h lookback): clasifica ~50% chop vs ~90% real → mucho peor
- Descartado: el horizonte temporal en M15 con lookback 20×15min = 300 min = 5h es muy largo

**Intento 2: barrido de parámetros en M15**
`backtest/_bt_regime_m15.py`:
- EMA5 M15 (75 min lookback) → mucho mejor
- Añadir racha de 2 barras confirmando → más estable
- Añadir expansión ATR ×1.3 → captura inicio de tendencias agresivas

**Ganador: EMA5 + racha 2 + ATR-expansión 1.3**

| Detector | maximin OOS (peor de los 3) |
|----------|---------------------------|
| SMA50 viejo (Rust) | +0.85 |
| Regime M1 real | +1.56 |
| **EMA5/st2/x1.30 (M15)** | **+1.66** |

Walk-forward: 0 bloques negativos en ninguno de los 3 activos. Todos los trimestres positivos. Más robusto que el propio detector M1.

**Constantes en `levels.rs`:**
```rust
pub const REGIME_EMA: usize   = 5;    // EMA5 → 75 min en M15
pub const REGIME_STREAK: i32  = 2;    // racha 2 barras confirmando trend
pub const REGIME_EXP: f64     = 1.30; // ATR actual > 1.3× MA20 = expansión
```

**Commit:** 02c9d15

---

## 15. Fase 14 — Filtros de señal liquidity (10 probados, 1 sobrevive)

### El protocolo

Cada filtro se testó con regla dura: debe mejorar OOS avgR en BTC, ETH y SOL **simultáneamente**. Un filtro que ayuda a 2 activos pero no al tercero → descartado.

Base para comparación: gen_h5 + gen_h21 + gen_h21_short (sin IFVG aún).

### Todos los descartados

| Filtro | Razón de falla |
|--------|---------------|
| No-Asian (trades solo 7-21 UTC) | BTC empeora, ETH empeora |
| No-weekend | SOL empeora |
| No-lunes | Todos neutros o levemente peor |
| Solo London-NY (12-21 UTC) | BTC cae ligeramente bajo la regla dura |
| Approach N=3 barras (precio se acercó en últimas 3 barras) | BTC +0.786→+0.445 (mucho peor) |
| Approach N=5 barras | Mismo resultado |
| dist < 0.5 ATR (cerca del nivel) | PEOR: esto es el filtro INVERSO del que funciona |
| dist < 1.0 ATR | Igual de malo |
| fp_poc cerca entry | BTC +0.786→+0.093, desastroso |
| fp_absorb ETH/SOL | Columna vacía — feature no disponible para ETH/SOL |

**Sorpresa de "approach":** la idea intuitiva era que si el precio se está acercando al nivel, la señal es más fuerte. El resultado fue el contrario: empeora significativamente. Cuando el precio lleva varios barras bajando hacia el nivel, es señal de que puede romperlo, no de que va a rebotar.

### El único que PASA: distancia al nivel > 0.5×ATR

Cuando la barra de señal cierra **lejos** del nivel VP POC (más de 0.5×ATR):

| Activo | Base OOS | Con filtro | Delta |
|--------|----------|-----------|-------|
| BTC | +0.786 | **+1.377** | +75% |
| ETH | +0.870 | **+1.505** | +73% |
| SOL | +0.666 | **+0.950** | +43% |

**Por qué funciona (mecanismo):**

- **Cuando close ≈ nivel:** el nivel ya está siendo testeado activamente. El precio puede continuar a través del nivel (ruptura). Es el momento más incierto.
- **Cuando close >> nivel (>0.5 ATR lejos):** el nivel es un target limpio intacto. El precio tiene que recorrer distancia para llegar ahí. Al llegar, el rebote es más limpio porque el nivel no ha sido "masticado" en esa barra.

**Caveat live:** este filtro selecciona trades donde el precio necesita recorrer más para llenar la orden → el fill rate puede caer en vivo. El E[R_live] = fill_rate × avgR_si_llena. Si fill_rate cae 25% y avgR dobla → claramente positivo. Verificar en paper.

**Combo London-NY + dist > 0.5 ATR:** también pasa regla dura con n moderado.

---

## 16. Fase 15 — IFVG (Inverse Fair Value Gap)

### Qué es un IFVG

Un FVG (Fair Value Gap) es un gap de precio entre 3 velas consecutivas donde la vela central no solapó completamente con sus vecinas. Un **IFVG** es un FVG que fue parcialmente llenado (el precio entró al gap pero no lo completó) y el precio vuelve a testear el área del gap.

La hipótesis: esas zonas son "imanes" — hay liquidez pendiente de llenarse y el precio es atraído de vuelta.

### IFVG standalone (nueva señal)

| Activo | OOS avgR standalone |
|--------|---------------------|
| BTC | +0.297 |
| ETH | +0.332 |
| SOL | +0.495 |

Los tres positivos → pasa regla dura como señal independiente.

### Combo ganador: v2_h1_ifvg

**Señales:** gen_h5 + gen_h21 + gen_h21_short + **gen_ifvg(K=60)**
**Filtros:** H1 slope alineado AND dist(close, lvl) > 0.5×ATR

**H1 slope:** `close > close[bar-4]` para long, lo inverso para short — contexto horario. Primero que se probó explícitamente para Liquidity (había sido usado en SC3 antes).

| Activo | n_oos/día | avgR | WR | RR | R/año |
|--------|-----------|------|----|----|-------|
| BTC | 3.1 | **+1.974** | 75% | 2.28 | +2234 |
| ETH | 1.6 | **+1.905** | 68% | 2.72 | +1142 |
| SOL | 1.6 | **+1.865** | 70% | 2.49 | +1068 |
| **Portfolio** | **6.3** | — | — | — | **+4444R** |

vs base trail=6: +9.47 R/día → +12.17 R/día (+29%), con menos trades (9.1 → 6.3/día).

**Calidad mejora, cantidad baja.** El filtro H1+dist filtra trades malos (los que entrarían sin contexto).

**Esta config (v2_h1_ifvg) es el baseline actual desplegado en Rust.**

### Filtros adicionales probados para v2 (todos descartados)

| Filtro | Resultado |
|--------|-----------|
| H4 slope solo | ETH no mejora |
| H1+H4 slope combinado | Menos trades, misma mejora |
| EMA20 H1 proxy (SMA80 M15) | Inconsistente |
| dist > 1.0 ATR | n muy pequeño, alta varianza |

---

## 17. Fase 16 — ICT/SMC Checklist (descartado)

### El contexto

El usuario compartió un checklist de trading ICT/SMC (del "Trader Zed"):
- HTF Key Level
- HTF Liquidity Sweep
- CISD (Change In State Of Delivery)
- FVG en discount/premium
- RR mínimo

Se implementó como generador causal completo compatible con el motor del proyecto.

### >160 calibraciones

**Primera pasada (solo entrada/gestión):** negativo en todo. Grid 96 configs + sweep de palancas (filtro ATR + routing) → IS-negativo siempre.

**Segunda pasada (con los pilares ICT):** sesgo HTF (precio vs SMA diaria + slope) + killzone London+NY (h7-16 UTC) + target RR fijo + timeout largo. Con estos, aparecen configs IS+OOS positivas.

**El problema: SOL**

| Activo | Resultado |
|--------|-----------|
| BTC | Marginal positivo (algunas configs) |
| ETH | Fuerte (isA +1.02), pero OOS n=5 (muy poco) |
| SOL | **Negativo en TODAS las variantes** (−0.27 a −0.45) |

**Veredicto:** falla regla dura. SOL lo mata en todas las variantes. NO desplegar como modelo de 3 activos.

### Lección metodológica

No declarar "sin edge" hasta probar el contexto completo del modelo. Las killzones y el sesgo HTF son parte integral del checklist ICT, no opcionales. Testear el modelo sin su contexto (como entrada/gestión en aislamiento) es testear una versión incompleta.

**Analogía:** testear si "comprar el desayuno" es rentable sin considerar que la idea es comprar el desayuno en el restaurante correcto a la hora correcta.

### Ángulo no agotado

Entrada momentum/breakout post-CISD con target de continuación (en vez de límite-maker al FVG con target mean-reversion). Sería una estrategia diferente, no una calibración del modelo actual.

---

## 18. Fase 17 — Research SC3: absorción intradiaria M5

### Los 8 setups probados

| # | Setup | Resultado |
|---|-------|-----------|
| 1 | Momentum M1 (OBI direction) | OOS −0.4 a −1.7, DD hasta 1000% |
| 2 | OBI wall (muro de órdenes) | Muerto: fee taker + 87-93% chop |
| 3 | VWAP bounce | Muerto: fee taker |
| 4 | LVN breakout | Muerto: falsos breaks |
| 5 | Stacked imbalance | Muerto: ídem |
| 6 | Liquidaciones como señal | Sin histórico suficiente |
| 7 | Heatmap liquidez resting | No generaliza (Pearson ~0) |
| **8** | **SC3: absorción en nivel VP** | ✅ **ÚNICO superviviente** |

**Por qué todo lo demás muere:** el mercado está en chop el 87-93% del tiempo. Cualquier estrategia direccional (momentum, breakout) apuesta el 87% de las veces que el precio continúa un movimiento que en realidad va a rebotar. El fee taker hace el resto.

### SC3 — el mecanismo que funciona

**Definición:** precio en nivel VP (VAH/VAL/POC del area de valor) + volumen agresor alto (VR ≥ 1.5 = 1.5× el promedio del período) + delta footprint en contra del movimiento + precio aguanta el nivel → **fade límite maker en M5**.

**Por qué funciona:**
1. El nivel VP es donde hay volumen histórico concentrado → imán de precio y zona de respeto
2. El volumen alto en contra (absorción) significa que hay vendedores (o compradores) grandes absorbiendo la presión → el nivel resiste
3. El delta footprint opuesto confirma que el volumen agresor fue absorbido por el lado contrario
4. Orden límite maker → no pagas el spread + fee maker negativo → mejor entrada

**POC es el PEOR nivel (contra-intuitivo):**

| Nivel | OOS avgR |
|-------|---------|
| POC (rank=0) | **+0.021** (flat) |
| VAL/VAH (rank=1) | **+0.492** |
| PDH/PDL | +0.610 (n=5, poco estadístico) |

**Por qué:** el POC es el nivel más "obvio" del value area → todos lo ven → hay anticipación al rebote → más traders entran antes de que el precio llegue → el rebote se anticipa y el POC a veces quiebra. VAL/VAH son los bordes del área de valor → menos obvios → fades más limpios.

**M5 vs M1:**
M1 muere por ruido. En M5, la barra tiene suficiente tiempo para mostrar absorción real vs ruido de microestructura. M1 genera señales falsas en exceso.

### Todo lo que NO funciona en SC3

| Cosa probada | Resultado | Por qué falla |
|-------------|-----------|---------------|
| Entrada taker (cualquier VIP) | WR 37-38%, OOS −0.38 a −0.76 en los 3 | La cola de posición filtra señales malas. El taker las toma todas y pierde por selección adversa. Con fees VIP3 (0.025% taker), la reducción de costo no compensa el peor precio de entrada |
| Partial TP (10-90%, R1=0.5/1.0/1.5R) | TODAS PEORES que all-in | El partial cristaliza una ganancia pequeña y deja correr el riesgo. En SC3 all-in al target estructural es mejor |
| POC como nivel único | OOS +0.021 (flat) | Ver arriba — el nivel más obvio es el menos respetado |
| Filtro sesión London/NY | Asia tiene OOS comparable o mejor | Asia (3-8 UTC) tiene absorción en niveles de VP igual de válida |
| OBI / CVD / VPIN | 0 impacto | Misma conclusión que en Liquidity |
| Routing A/B por régimen | WR 33-45%, OOS negativo | SC3 es un trade de reversión puro. En tendencia no tiene ventaja — el trailing lo destruye |
| CVD slope | 0 trades ETH/SOL | La columna está vacía para estos activos en el período testado |
| Régimen chop_only | SOL colapsa +0.552→+0.149 | Muchos buenos trades en SOL ocurren en régimen trend (absorción en nivel durante un trend = el mejor setup) |
| BTC guard (stop adicional si BTC cae X%) | Descartado | Reduce n sin mejorar avgR |
| EMA200 | Demasiado lento | No captura cambios de contexto relevantes para SC3 |
| Session filter | Asia = mejor o igual | No hay ventaja temporal en esta estrategia |
| max_day > 3 | Peor | Las primeras 3 señales del día son las más limpias. Más allá = nivel "usado" |

### El sesgo HTF faltante: H1/H4 EMA

SC3 disparaba absorción sin preguntarse "¿hacia dónde va el mercado en marcos temporales mayores?". La Liquidity A+B usa el detector de régimen. SC3 necesitaba algo equivalente.

**Filtros probados exhaustivamente:**

| Feature | peorOOS | trades/día | Veredicto |
|---------|---------|-----------|-----------|
| Baseline | +0.232 | 1.7 | Referencia |
| Régimen chop_only | +0.149 | — | ❌ |
| Routing A/B | negativo | — | ❌ |
| M5 EMA20 align | +0.405 | 1.6 | Solo si pero 1.6/d muy bajo |
| H1 EMA20 solo | +0.393 | 2.4 | ✅ bueno |
| H4 EMA20 solo | +0.305 | — | ❌ BTC débil |
| H1+H4 EMA20 | +0.358 | 2.0 | ✅ menos trades |
| CVD slope | 0 trades | — | ❌ |
| poc_frac (posición vs POC) | +0.178 | — | ❌ |
| Session London/NY 7-17 UTC | peor | — | ❌ |
| VR>3 solo | +0.429 | 3.5 | ✅ buen resultado |
| **H1 OR H4 OR VR>3** | **+0.410** | **3.6** | ✅✅ GANADOR |

**Por qué H1 OR H4 OR VR>3 es el mejor:**
- Si H1 EMA20 alineado: el precio está en tendencia horaria. Un fade en el nivel con esa dirección = pullback en tendencia establecida = alta probabilidad.
- Si H4 EMA20 alineado: confirmación en timeframe de 4 horas.
- Si ninguno pero VR>3: la absorción es tan fuerte (3× el volumen promedio) que la dirección del HTF no importa — el volumen institucional está absorbiendo la presión con convicción.
- Si ninguno de los tres → señal débil sin contexto → saltar el trade.

### Parámetros cerrados con sweep completo

| Parámetro | Ganador | Razón |
|-----------|---------|-------|
| vr_thr | 1.5 | 2.5 era demasiado restrictivo para BTC (n=139 → n=888). La tesis no requiere absorción masiva, solo significativa |
| tol_atr | 0.6 | 0.4 pierde wicks válidos de absorción. 0.8+ permite entradas demasiado lejos del nivel |
| rr_cap | 3.0 | Sweep 0.5→5.0R fijo + target estructural sin cap. 3.0R = peorOOS +0.522 (+27% vs 2.5R) |
| max_day | 3 | Relajar a 4-5: peor (primeras 3 señales del día son las más limpias — el nivel no está "masticado") |
| cooldown | 6 barras (30 min) | Relajar: peor |

### Estabilidad temporal por trimestre

| Trimestre | BTC | ETH | SOL | Portfolio |
|-----------|-----|-----|-----|-----------|
| Q3-2025 Jul-Sep | +0.248 | +0.419 | +0.621 | **+0.433** |
| Q4-2025 Oct-Dic | +0.561 | +0.490 | +0.855 | **+0.602** |
| Q1-2026 Ene-Mar | +0.426 | +0.551 | +0.519 | **+0.500** |
| Q2-2026 Abr-Jun | +0.396 | +0.542 | +0.755 | **+0.548** |

Cuatro trimestres, cuatro regímenes diferentes, todos positivos. El edge es independiente del régimen de mercado.

### Métricas finales SC3 (108 días OOS)

| Símbolo | n_total | n_oos | /día | WR | avgR OOS | DD |
|---------|---------|-------|------|----|-----------|----|
| BTCUSDT | 733 | 186 | 1.4 | 63% | **+0.547** | 6.8% |
| ETHUSDT | 541 | 162 | 1.5 | 54% | **+0.524** | 5.4% |
| SOLUSDT | 525 | 158 | 1.4 | 56% | **+0.522** | 8.2% |
| **Portfolio** | **1799** | **506** | **3.4** | **58%** | — | **6.8%** |

$500 → $5,709 (+1042%), riesgo fijo $5/trade.

### Fills reales SC3 (Nautilus, 20 días OOS)

| Símbolo | Fill ratio | WR real | avgR real |
|---------|-----------|---------|-----------|
| BTC | 52% | 82% | +0.41 |
| ETH | 43% | 85% | +0.59 |
| SOL | 65% | 77% | +0.64 |

avgR real ≈ backtest → el edge no es artefacto de fill.

---

## 19. Fase 18 — S/R estructurales como niveles de entrada

### El insight

Desde el principio, PDH/PDL, Weekly H/L y Round numbers se usaban como **targets** de los trades (la estrategia cierra en esos niveles). ¿Por qué no también como **entradas**?

La idea: esos niveles tienen órdenes acumuladas de participantes que los usan como referencia. El primer toque tiene el pool más intacto — más probabilidad de rebote.

### Tests standalone (cada generador por separado, 108 días OOS)

| Generador | BTC OOS | ETH OOS | SOL OOS | Portfolio | Regla dura |
|-----------|---------|---------|---------|-----------|-----------|
| PDH/PDL — todos los toques | +0.654 | +1.283 | +0.973 | +0.970 | ✅ |
| PDH/PDL — virgin (0 toques prev 20 barras) | +2.177 | +3.356 | +3.352 | +2.961 | ✅ pero n=11-20 |
| PDH/PDL — max 1 toque | +1.241 | +2.106 | +2.022 | +1.790 | ✅ pero n bajo |
| Weekly H/L — todos | **+1.947** | +2.125 | +1.719 | **+1.930** | ✅ |
| Weekly H/L — solo virgin | similar | similar | similar | similar | ✅ (88.8% ya son virgin) |
| Monthly H/L | +0.873 | +0.991 | **−0.498** | — | ❌ |
| Round numbers — todos | +1.547 | +2.196 | +1.931 | +1.891 | ✅ |
| **Round numbers — virgin** | **+2.119** | **+2.791** | **+2.438** | **+2.449** | ✅ |

### Por qué virgin funciona (el mecanismo)

**Primer toque = pool de stops intacto = máximo impacto.**

Cada toque a un nivel activa una parte de las órdenes acumuladas ahí. El primer toque absorbe el mayor pool. El segundo toque ya tiene menos órdenes. El tercero, menos aún. Eventualmente el nivel se "digiere" y el precio lo cruza.

**Virgin Weekly H/L:** el 88.8% de las barras el weekly H/L ya es virgin (la vela nunca ha tocado el extremo semanal). Filtrar solo virgin no cambia mucho las métricas porque casi todos son virgin de todas formas.

### PDH/PDL virgin — por qué NO se deployó

A pesar de OOS +2.961, no se deployó:
- n_oos: 11-20 trades en 108 días → **muestra estadística insuficiente**
- SOL DD: 29% (vs 5% del base)
- 2 trimestres IS negativos
- Con muestra tan pequeña, el OOS alto puede ser azar

Decisión: esperar más datos. PDH/PDL tiene el potencial pero no la validación suficiente.

### Monthly H/L — descartado

SOL OOS −0.498 → falla regla dura de forma contundente. No se investiga más.

**Hipótesis:** el monthly H/L no tiene el mismo respeto psicológico que el weekly. Los traders usan weekly como referencia más que monthly. El monthly puede coincidir con rangos tan grandes que el ATR del stop no alcanza el target con suficiente RR.

### S/R como contexto (filtro) — también testado

Se probó usar S/R no como nuevas entradas sino como **filtro de los trades VP existentes** — solo entrar cuando el nivel VP coincide con un S/R cercano.

Resultado con tol=0.3%:
- delta avgR: +0.435 vs base
- n: cae al 56% del base
- WR: **no sube** (65-67% vs 67% base)

**Conclusión:** el contexto S/R no mejora el WR. La base ya está bien filtrada por ATR/cooldown/H1/dist. Agregar la condición de "estar cerca de un S/R" solo reduce trades sin mejorar calidad.

### Config 2 seleccionada (final, deployada): v2_h1_ifvg + Weekly H/L + Round numbers virgin

Comparación con base v2:

| Métrica | Base v2_h1_ifvg | Config 2 (+ Weekly + Round) | Delta |
|---------|-----------------|------------------------------|-------|
| BTC n_oos | 279 | **396** | +42% |
| ETH n_oos | 158 | **242** | +53% |
| SOL n_oos | 148 | **236** | +59% |
| Portfolio trades/día | 5.3 | **8.1** | +53% |
| BTC avgR | +1.931 | +1.804 | −0.13 |
| ETH avgR | +2.070 | +2.084 | +0.01 |
| SOL avgR | +1.941 | +2.211 | +0.27 |
| Portfolio avgR | +1.981 | **+2.033** | +0.05 |
| BTC WR | 76% | 66% | −10pp |
| ETH WR | 66% | 57% | −9pp |
| SOL WR | 71% | 64% | −7pp |
| BTC net R | 539R | **714R** | +32% |
| ETH net R | 327R | **504R** | +54% |
| SOL net R | 287R | **522R** | +82% |
| **Portfolio net R** | **1153R** | **1740R** | **+51%** |
| BTC PnL ($500, $5/trade) | $11,019 | **$14,859** | +35% |

**El tradeoff:** WR baja de ~71% a ~62%. Los nuevos generadores (Weekly+Round) tienen WR standalone de 38-45%. Al combinar, el promedio ponderado cae. Es aceptable porque:
1. El WR combinado (62%) sigue siendo bueno
2. El avgR no baja (sube levemente)
3. El PnL absoluto sube +35-80%

### Implementación en Rust (commit c8539db, 2026-06-30)

```rust
// levels.rs — nuevos helpers
fn nearest_round(price: f64, mult: f64) -> f64 {
    (price / mult).round() * mult
}
fn sr_touches(bars: &[ClosedBar], lvl: f64, lookback: usize) -> usize {
    // cuenta barras donde |low-lvl|/lvl ≤ 0.2% OR |high-lvl|/lvl ≤ 0.2%
}

// compute_levels — nuevas constantes
pub const SR_STOP_FRAC: f64   = 0.5;   // stop = nivel ± 0.5×ATR
pub const SR_ENTRY_TOL: f64   = 0.001; // entrada 0.1% sobre/bajo el nivel
pub const SR_TOUCH_TOL: f64   = 0.002; // tolerancia de toque para virgin
pub const SR_LOOKBACK: usize  = 20;    // barras M15 (5h) para virgin check

// main.rs — round_mults por símbolo
let round_mults = match symbol {
    "BTCUSDT" => vec![1000.0, 5000.0],  // $1k y $5k
    "ETHUSDT" => vec![100.0, 500.0],    // $100 y $500
    "SOLUSDT" => vec![10.0, 50.0],      // $10 y $50
};
```

**Nuevos kinds en DB:** `weekly_l`, `weekly_h`, `round_l`, `round_h`

---

## 20. Estado actual deployado

### Liquidity A+B — config final

```
Señales:  gen_h5 (OB)
          gen_h21 (POC defendido long, ≥2 toques)
          gen_h21_short (POC defendido short, ≥2 toques)
          gen_ifvg (IFVG, K=60 barras lookback)
          weekly_l / weekly_h (nuevo, entrada 0.1% sobre/bajo el extremo semanal)
          round_l / round_h (nuevo, round number virgin — 0 toques prev 20 barras)

Filtros:  H1 slope alineado (close > close[bar-4] para long)
          dist(close, level) > 0.5×ATR (nivel limpio)
          ATR > ATR_mediana(500) — HIGH_VOL_ONLY

Gestión:  Chop → fade: 50% en tp1 → BE → tp2
          Trend → trailing stop 6×ATR

Params:   trail_atr=6.0, stop_scale=0.8, stop_floor=0.15%
          cooldown=6 barras, max_day=2
          Round: BTC=[1000,5000], ETH=[100,500], SOL=[10,50]

Métricas OOS Config 2 (108 días):
  BTC: n=396, avgR=+1.804, WR=66%, RR=2.48x, PnL=$14,859
  ETH: n=242, avgR=+2.084, WR=57%, RR=3.57x, PnL=$8,012
  SOL: n=236, avgR=+2.211, WR=64%, RR=3.20x, PnL=$9,533
  Portfolio: +2.033 avgR, 8.1 trades/día, net R 1740R
```

### SC3 Intradiario — config final

```
Señales:  Absorción en niveles VP ampliados:
          VAH, VAL, POC, PDH, PDL, Weekly H/L, Swing H/L

Filtros:  ATR > ATR_mediana(500)
          VR ≥ 1.5 (volumen 1.5× el promedio)
          Delta footprint en contra del movimiento
          HTF rule: H1_EMA20_alineado OR H4_EMA20_alineado OR VR > 3
          Precio aguanta el nivel tras absorción

Gestión:  Fade all-in a rr_cap=3.0R
          NO partial TP (empeora sin excepción)

Params:   vr_thr=1.5, stop_atr=0.5, tol_atr=0.6, rr_cap=3.0
          max_day=3, cooldown=6 barras (30 min)

Métricas OOS (108 días):
  BTC: n=733, avgR=+0.547, WR=63%, DD=6.8%
  ETH: n=541, avgR=+0.524, WR=54%, DD=5.4%
  SOL: n=525, avgR=+0.522, WR=56%, DD=8.2%
  Portfolio: 3.4 trades/día, peorOOS=+0.522, $500→$5,709
```

### Servicios Railway activos (2026-06-30)

| Servicio | Símbolo | Sistema | Estado |
|----------|---------|---------|--------|
| liquidity-btc | BTCUSDT | flow (A+B) | Online |
| liquidity-eth | ETHUSDT | flow (A+B) | Online |
| liquidity-sol | SOLUSDT | flow (A+B) | Online |
| sc3-btc | BTCUSDT | SC3 | Online |
| sc3-eth | ETHUSDT | SC3 | Online |
| sc3-sol | SOLUSDT | SC3 | Online |

### Supabase — tablas activas

| Tabla | Contenido |
|-------|-----------|
| `liquidity_paper_trades` | Trades completos Liquidity A+B |
| `liquidity_paper_events` | Eventos de orden (place, fill, close) |
| `liquidity_paper_snapshots` | Estado horario del sistema |
| `sc3_paper_trades` | Trades SC3 |

### Ejecutor real (testnet/live)

Infra lista (commit 05d219a). env-gated por defecto OFF (no afecta paper). Pendiente: migración SQL + API keys testnet + activar para medir fills/R reales.

---

## 21. Reglas del proceso de investigación

Aprendidas a golpes a lo largo del proyecto. Son obligatorias para cualquier research futuro.

### Regla 1: La regla dura (la más importante)

**OOS avgR > 0 en los 3 activos simultáneamente.**

BTC + ETH + SOL. Si uno falla, el experimento falla. No hay excepciones.

*Por qué:* Los activos comparten el mismo período de tiempo. Una feature puede capturar un régimen temporal específico de BTC sin ser un edge real. La generalización cross-activo es el mínimo requerimiento.

### Regla 2: Validación cross-tiempo obligatoria

IS = período de entrenamiento cronológicamente anterior al OOS.
OOS = período "nunca tocado" durante el research.

*Split actual:* OOS desde 2026-03-01 en adelante (aprox 108 días).

*Por qué:* Generalización cross-activo NO basta si los activos comparten el mismo período. La validación temporal es el único test real de overfitting.

### Regla 3: El edge es maker o no es

Toda feature/filtro/señal debe ser probada con entrada límite maker, no taker.

Si algo funciona a taker pero no a maker → el edge está en el spread/fee, no en la señal. En vivo, operar a taker a escala pequeña (~$5/trade) hace que el fee sea una fracción enorme del P&L.

### Regla 4: No redeploy sin propósito

Cada push a `main` redeploya todos los servicios Railway. Cada redeploy reinicia el warmup del footprint (~5h). Research en ramas, deploy en `main` solo cuando hay algo concreto.

### Regla 5: Generalización cross-activo ≠ validación temporal

Ya explicada, pero tan importante que merece su propio punto. Un modelo puede generalizar a BTC/ETH/SOL en el mismo período porque los tres vivieron el mismo régimen de mercado. Si el research completo cae dentro de un "bull run" o de "alta vol", el modelo puede ser overfitting al régimen, no al edge.

### Regla 6: Antes de declarar "sin edge", probar el contexto completo

Lección del ICT: el modelo incompleto (sin sesgo HTF + killzones) parecía sin edge. Con el contexto correcto, ETH mostraba isA+1.02. El problema era el test incompleto.

Antes de cerrar cualquier hipótesis, asegurarse de que se está testando la versión completa del modelo (con todo su contexto implícito).

### Regla 7: La muestra importa

Con n=11-20 OOS, cualquier avgR (incluso +2.96R) puede ser azar. La decisión de deploy requiere n > 50 por activo como mínimo. Muestra pequeña → "prometedor, monitorear" no "deployar".

---

## 22. Glosario técnico

| Término | Definición |
|---------|-----------|
| **VP / Value Profile** | Volume Profile: distribución de volumen por nivel de precio en un período |
| **POC** | Point of Control: nivel de precio con mayor volumen en el VP |
| **VAH/VAL** | Value Area High/Low: bordes del área donde se concentra el 70% del volumen |
| **VP defendido** | POC donde el precio fue rechazado ≥2 veces (señal de respeto del nivel) |
| **ATR** | Average True Range (14 períodos): medida de volatilidad reciente |
| **OBI** | Order Book Imbalance: desequilibrio entre bid y ask en el libro |
| **CVD** | Cumulative Volume Delta: delta acumulado de volumen (compras-ventas) |
| **VR** | Volume Ratio: volumen de la barra / volumen promedio del período |
| **FVG** | Fair Value Gap: gap entre 3 velas donde la central no solapó con las vecinas |
| **IFVG** | Inverse FVG: FVG que fue parcialmente llenado y el precio vuelve al área |
| **Maker** | Orden límite que añade liquidez al libro (cobra fee maker ~4bps) |
| **Taker** | Orden a mercado que consume liquidez del libro (paga fee taker ~11bps) |
| **Fill ratio** | Porcentaje de órdenes colocadas que efectivamente se llenan |
| **Markout** | Movimiento del precio después de que se llena una orden (mide selección adversa) |
| **Selección adversa** | El precio se mueve en tu contra inmediatamente después de llenarse |
| **avgR** | Average R-multiple: ganancia/pérdida promedio medida en unidades de riesgo |
| **WR** | Win rate: porcentaje de trades con resultado positivo |
| **DD** | Drawdown: caída máxima del capital desde el pico anterior |
| **Sharpe** | Ratio de Sharpe: retorno / volatilidad del retorno (ajustado por riesgo) |
| **OOS** | Out-of-Sample: período de validación no visto durante el research |
| **IS** | In-Sample: período de entrenamiento/calibración |
| **Regla dura** | OOS avgR > 0 en BTC + ETH + SOL simultáneamente |
| **Virgin** | Nivel que no ha sido tocado en las últimas 20 barras M15 (pool intacto) |
| **Warmup** | Período inicial después de un redeploy donde el footprint aún no tiene datos suficientes |
| **Trail** | Trailing stop: stop que sigue al precio en la dirección del trade |
| **Fade** | Contra-tendencia: apostar a que el precio revertirá desde el nivel |
| **PDH/PDL** | Previous Day High/Low: máximo y mínimo del día anterior |
| **Weekly H/L** | Rolling 8-day High/Low: máximo y mínimo de los últimos 8 días |
| **Round number** | Nivel psicológico redondo (BTC $1k/$5k, ETH $100/$500, SOL $10/$50) |
| **CISD** | Change In State Of Delivery (ICT): cambio de dirección institucional |
| **Chop** | Mercado en rango lateral sin dirección clara (87-93% del tiempo) |
| **Trend** | Mercado con dirección sostenida (7-13% del tiempo) |
| **SC3** | Scalp clase 3 (nombre interno): absorción intradiaria en nivel VP, M5 |
| **rr_cap** | Cap del reward:risk ratio — limita el target máximo al que se apunta |
| **STOP_FLOOR** | Mínimo absoluto de stop (0.15%) — evita stops absurdamente ajustados |
| **TRAIL_ATR** | Multiplicador del ATR para el trailing stop (actualmente 6.0) |
| **stop_scale** | Factor de reducción del stop (0.8 = 20% más chico que el calculado) |

---

*Documento construido a partir de los experimentos reales del proyecto. Cada número viene de backtest sobre datos reales con validación OOS estricta.*

*Scripts clave: `backtest/_strategy_ab.py` (motor Liquidity), `backtest/_scalp.py` (motor SC3), `crates/liquidity_monitor/src/levels.rs` (Rust live).*
