# Sesión 2026-06-24 — Bugs de paper, investigación orderflow, optimizaciones y diagnóstico footprint

Resumen de una sesión larga de research + fixes sobre la estrategia de provisión de
liquidez (paper trading en Railway). **Conclusión de fondo: la estrategia ya era buena;
lo que fallaba era ejecución (bugs) e infraestructura (footprint). No se optimizó ningún
parámetro porque nada sobrevivió la validación temporal.**

---

## 1. Bugs de ejecución arreglados (lo más importante en lo práctico)

### Bug A — posiciones no sobrevivían redeploys
- `PaperBook.open_pos` vivía solo en RAM. Cada redeploy de Railway reiniciaba el proceso
  con `open_pos` vacío → las posiciones abiertas se perdían, nunca tocaban stop/target en
  el nuevo proceso, nunca se escribía su `ClosedTrade`.
- **Síntoma**: 22 fills en `events` pero solo 2 trades cerrados.
- **Fix** (commit `31fba37`, en `main`): tabla `liquidity_paper_open_pos` + persistencia
  en cada `bar_close` + restauración al arrancar. `book.rs`/`supa.rs`/`main.rs`.

### Bug B — trades no se escribían (`size_mult`)
- `write_trades` mandaba la columna `size_mult` que no existía en la tabla → insert fallaba
  (fire-and-forget, en silencio).
- **Fix**: `ALTER TABLE liquidity_paper_trades ADD COLUMN size_mult ...` (en Supabase).

**Resultado**: el paper ahora registra trades reales y persiste posiciones (verificado:
posiciones vivas en `liquidity_paper_open_pos`, trades nativos nuevos cerrándose solos).

---

## 2. El edge: validado y NO sesgado

- BTC backtest OOS: **avgR +1.54, +297% en 107d, DD 4.7%, Sharpe 9.5, cada mes verde**.
- **No está sesgado**: shorts ganan igual que longs (BTC +1.57 short vs +1.52 long).
- **Preparado para ambos regímenes**: mes alcista → cargan los longs; mes bajista → cargan
  los shorts. Ningún mes OOS fue negativo.
- El paper rendía mal por los bugs + footprint warmup (abajo), no por la estrategia.

---

## 3. El footprint warmup — el hallazgo de mayor impacto (+29%)

- El footprint (POC de volumen ejecutado) se construye **en vivo** escuchando `publicTrade`
  por WebSocket (tarda ~5h en madurar). NO se puede reconstruir de velas (las velas no
  tienen el volumen desglosado por precio) ni re-fetchear (Bybit `recent-trade` da solo
  ~1 min; los archivos históricos tienen retraso de 1 día).
- Por eso se **guarda agregado por barra** en Supabase (`liquidity_paper_footprint`,
  ~1 KB/barra) y se **restaura al arrancar** (`load_footprint`, 200 barras).
- **Sin footprint** (POC = midpoint de la vela), el sistema pierde **29% del edge**:
  `avgR +1.54 (con footprint) → +1.09 (midpoint)`.
- **Implicación operativa: cada redeploy reinicia el warmup ~5h.** Hay que evitar redeploys
  innecesarios. (Railway redeploya TODOS los servicios en CUALQUIER push a `main`.)

---

## 4. Orderflow — descartado exhaustivamente (no mejora el edge)

Probado con backtest (n grande) y, donde se pudo, con tick real:

| Señal | corr con R / veredicto |
|-------|------------------------|
| OBI / depth / spread (libro a 1s) | −0.04 — muerto por horizonte (vida media 5s vs trade 2h) |
| delta / CVD / footprint puntual | ~0 sin lookahead (el +0.2 inicial era lookahead intrabar) |
| volumen | sesgo de dirección, no generaliza (BTC vol>3× malo, ETH bueno) |
| divergencia CVD (multi-barra) | no generaliza a los 3 activos |
| Sweep (barrido SMC) | **−0.30 en los 3** (pierde como regla mecánica) |
| **Libro completo (heatmap ob500)** | +0.22 ETH (46 trades) → +0.17 (90) y SOL −0.01. **No generaliza.** |

- Infra Rust nueva: **crate `ob_heatmap`** reconstruye el libro completo (ob500/ob200) y
  extrae features de muros en el fill — procesa 365d ETH/SOL en ~2.5 min (vs horas en
  Python). Quedó listo por si se quiere re-explorar.
- **Conceptos SMC** de las imágenes del usuario: Order Block = ya es `poc_ob`; niveles =
  ya son los niveles; fases/manipulación = ya es el régimen; Sweep/Trampa SMT = pierde.
  Nada nuevo que agregar — la estrategia es la versión cuantificada de lo bueno del SMC.

---

## 5. Optimizaciones — NINGUNA sobrevivió (todas overfitting)

Barrido en los 3 activos:

| Parámetro | Mejor | ¿Generaliza cross-activo? | ¿Generaliza cross-TIEMPO (IS vs OOS)? |
|-----------|-------|--------------------------|---------------------------------------|
| **Trail ATR** | ×5-6 | ✅ sí | ❌ **NO** — mejora OOS, empeora ETH/SOL en IS |
| tp2_cap | per-activo | ❌ (ETH no, SOL sí) | — |
| Filtro ATR ×mult | per-activo | ❌ | — |
| Ventana ATR | per-activo | ❌ | — |
| Parcial TP1 | per-activo OPUESTO (ETH 0.0, SOL 1.0) | ❌ | — |
| mejoras BTC-only (vol>3×, corte 30min/−0.5R) | +0.28/+0.15R | solo BTC | overfit de umbrales |

- El **trail ×5** parecía robusto (generalizaba a los 3 activos) pero la **validación
  temporal** (período in-sample vs out-of-sample) lo desinfló: mejora en OOS, neutro/peor
  en IS. Era el régimen del período OOS, no un edge atemporal.
- **Lección**: la generalización cross-activo NO basta si los activos comparten período.
  La validación cross-tiempo es la prueba dura. **El sistema ORIGINAL es lo más robusto.**
- **Conclusión: no se modificó ningún parámetro.** Mismos valores que el original.

---

## 6. El footprint de ETH/SOL — diagnóstico CERRADO (4 bugs encadenados)

Síntoma: ETH/SOL "a ciegas" (footprint no acumula → POC = midpoint → −29% del edge).
La hipótesis inicial ("no llegan los ticks") resultó **FALSA**. Cadena de detective:

| Hipótesis | Cómo se probó | Veredicto |
|-----------|---------------|-----------|
| "no llegan ticks (stream incompleto)" | contador `ticks=N` (commit `eb201a7`) | ❌ **falsa** — llegan 12k-66k/barra |
| "bin `$5` fijo colapsa el footprint" | logs `fp_bins`: SOL=1, ETH=4 | ✅ real (secundario) |
| "`write_footprint` falla" | BTC guarda con el mismo código | ❌ falsa |
| "la PK de la tabla es solo `ts_ms`" | 278 filas = 278 ts_ms; BTC pisa a ETH/SOL | ✅ **CAUSA RAÍZ** |

**Eran DOS bugs del footprint, encadenados:**

### Bug C — `FP_BIN=5.0` fijo (calidad del POC)
- `$5` en SOL ($69) = 7% del precio → todos los ticks en 1 bin → POC inútil. ETH = 4 bins.
- **Fix** (commit `4823ad3`): `BIN` de const → runtime (`set_bin/bin`, `OnceLock`),
  proporcional al precio (`ref_px × 0.0001` = 1 bps) con override por env `FP_BIN`.
  Resultado: ETH bin `$0.16`, SOL bin `$0.007`. `fp_bins` saltó: **ETH 4→133, SOL 1→83**.
- Toca `levels.rs` (BIN), `main.rs` (calcula `fp_bin` tras bootstrap), `supa.rs` (reconstrucción).
- Guarda **precios reales** (no índices) → cambiar el bin no corrompe el footprint restaurado.

### Bug D — PK del footprint era solo `ts_ms` (persistencia)
- Las barras M15 cierran al MISMO `ts_ms` para los 3 símbolos. Con PK `ts_ms` y upsert
  `merge-duplicates`, los 3 colisionaban → **BTC sobrescribía a ETH/SOL** en cada barra
  (BTC 273 barras, ETH/SOL 2-3).
- **Fix** (SQL, sin redeploy): `ALTER ... DROP CONSTRAINT pkey; ADD PRIMARY KEY (ts_ms, symbol, tf)`.
- Tras el ALTER, ETH/SOL empezaron a guardar su footprint propio desde el primer `bar_close`.

**Resultado final**: los 3 activos SANOS — footprint de alta resolución (ETH 133, SOL 83,
BTC ~60 bins), cada uno guarda el suyo (PK compuesta), sobreviven redeploys (persistencia).
ETH/SOL salen del −29% de ceguera tras ~5h de warmup (juntan 1 día de footprint bueno).

---

## 7. Estado por activo (CIERRE)

| | Footprint resolución | Guarda el suyo | Sobrevive redeploys | Estado |
|---|---------------------|----------------|---------------------|--------|
| BTC | ✅ ~60 bins | ✅ | ✅ | sano |
| **ETH** | ✅ **133 bins** | ✅ | ✅ | **sano** (madurando) |
| **SOL** | ✅ **83 bins** | ✅ | ✅ | **sano** (madurando) |

---

## 8. Pendientes / decisiones abiertas

1. **Cleanup del footprint** — ahora que los 3 guardan, sin cleanup la tabla crece ~110 MB/año.
   `migrations/footprint_cleanup.sql` mantiene solo las últimas 300 barras/símbolo (~1 MB constante).
   Correr periódicamente (o pg_cron) — `load_footprint` solo usa 200.
2. **Liquidaciones**: única señal de orderflow sin explorar (no hay histórico; capturable
   en vivo suscribiendo `allLiquidation` y guardando a futuro).
3. **UI vista live** (`apps/trade-lab/.../LivePaperView.tsx`): visor de trades del paper
   (chart de velas + tabla + detalle). **Sin commitear** (solo local).
4. **Dejar el paper corriendo sin redeploys** para juntar muestra real con los 3 sanos.
5. **Regla operativa**: cualquier push a `main` redeploya los 3 servicios → warmup ~5h. Usar
   ramas para cambios que NO van al deploy; main solo cuando se quiere redeployar a propósito.

---

## Archivos clave de esta sesión

- `crates/liquidity_monitor/src/{book,supa,main,levels}.rs` — fixes de persistencia, contador
  ticks, bin proporcional al precio.
- `crates/ob_heatmap/` — reconstructor del libro completo en Rust.
- `migrations/liquidity_paper_open_pos.sql` — tabla de posiciones persistidas.
- `migrations/footprint_cleanup.sql` — cleanup del footprint (últimas 300 barras/símbolo).
- Migraciones SQL aplicadas en Supabase: columna `size_mult` + PK compuesta
  `liquidity_paper_footprint (ts_ms, symbol, tf)`.
- `backtest/_*.py` — scripts de research (early_signal, book_heatmap_poc, sweep_test,
  flow_edge, cvd_div, side_bias, reconstruct_orphans, early_deep).
- `backtest/_strategy_ab.py` — params de investigación aditivos (defaults = original).

---

## 9. Footprint ETH/SOL — CAUSA RAÍZ encontrada y arreglada (commit `4823ad3`, main)

Diagnóstico cerrado **sin** depender de los logs de Railway, combinando 3 evidencias:
1. Bybit entrega `publicTrade` a los 3 (test WS propio: ETH 1520 / SOL 318 ticks en 15s).
2. En producción ETH/SOL tienen eventos cada 15min (kline OK) pero footprint congelado.
3. Logs del usuario: **`ticks=11792 fp_bins=1` (SOL)**, `ticks=66243 fp_bins=4` (ETH).

→ Los ticks SÍ llegaban; la causa era **`FP_BIN=5.0` fijo**: a $69 (SOL) todo el rango de la
barra colapsa en 1 bin → POC = midpoint redondeado (inútil). **Fix:** `BIN` const → runtime
(`set_bin`/`bin`, `OnceLock`), default proporcional al precio (1bps), override por env `FP_BIN`.
BTC ~$6 (≈igual), ETH ~$0.16, SOL ~$0.007. Reconstrucción guarda precios reales → no corrompe el
restore de BTC. **Deploy = warmup ~5h una vez** (BTC casi sin warmup: restaura 267 barras).

## 10. ⭐ FEATURE LAB — ecosistema de pruebas estándar (`backtest/featurelab.py`)

Problema resuelto: cada feature se probaba con gestión improvisada → el resultado dependía de
decisiones arbitrarias, no del feature. Ahora TODO pasa por el mismo protocolo y veredicto.
- **SEÑAL** (propone entradas) → `signal_verdict()`: gestión A+B FIJA, standalone, 3 activos.
- **FILTRO** (gatea trades base) → `filter_verdict()`: parte los trades base en PASA/descarta.
- **Regla dura** (igual para ambos): positivo en **IS y OOS en los 3 activos** con n mínimo.
  Lo que brilla en OOS pero es negativo en IS = artefacto de régimen, se rechaza.
- **Control de calidad** (lo que lo hace confiable): aprueba H5 ✅ (bueno conocido) y rechaza
  sweep ❌ (malo conocido). Reproduce los números base → no es el confound.
- `_strategy_ab.run_system` ahora emite `reason` (target/trail/be/stop/timeout) → autopsia mecánica.

## 11. Features probados por el lab (TODOS cerrados salvo nota)

| Feature | Tipo | Veredicto | Por qué (mecánico) |
|---|---|---|---|
| sweep (barrido+rechazo) | señal | ❌ −0.29/−0.31/−0.30 | reacción real pero 0% llega al target; trail no corre |
| FVG (hueco 3 velas) | señal | ❌ −0.34/−0.17/+0.01 | el hueco se rellena y CONTINÚA (no es reversión) |
| **sweep→FVG** (secuencia ICT real) | señal | ❌ | mejor que las piezas sueltas (WR 25→50, stop 33→17%) PERO ~0: reacción chica, payoff < stop. **Barrido de 162 configs (entry/stop/gap/W/gestión): 0 positivas IS+OOS en los 3** (`_swfvg_grid.py`) |
| frescura del nivel | filtro | ❌ degenerado | el fill ES el toque → bars_since≈1, "aguantó"=tautología (regla de entrada) |
| confluencia ≥2/≥3 fuentes | filtro | ❌ | niveles aislados rinden igual/mejor que los confluentes en ETH/SOL |
| momentum aproximación (velocidad) | filtro | ❌ ruido | mejora +0.02R, cruza umbral laxo y nada más |
| **tamaño de vela al tocar (≥1.5·ATR)** | filtro | 🔶 señal real, frágil | dirección robusta (IS sube en meseta en los 3: desplazamiento fuerte→reversión grande, encaja con markout VOL-HIGH) pero la mejora OOS la cargan 1-2 home-runs (top-1 = 40-100% netR) y corta 60-90% de trades → NO desplegable, es confirmación del filtro de vol, no edge nuevo |

**Mejor setup = A+B base pelado.** Ningún feature lo bate de forma robusta.

## 12. Liquidaciones — captura forward arrancada (no hay histórico)

Verificado: Bybit NO publica histórico (ni en parquets ni REST/public data) → **no backtesteable**.
El feed en vivo `allLiquidation.{symbol}` SÍ funciona (validado). Único camino: capturar desde ya.
- `live/liquidation_collector.py` — colector standalone (WS → Supabase en lotes), **independiente del
  paper** (cero warmup). `migrations/liquidity_liquidations.sql` (tabla + vista `liquidity_liq_1m`).
  `railway.liquidations.env.example` (4º servicio).
- **Hipótesis (a testear cuando haya semanas de datos):** no es filtro de entrada sino
  **confirmación de completitud del movimiento** — cascada de liquidaciones a favor cerca del nivel
  = combustible que lleva el precio al target lejano. Se enchufará al lab como FILTRO.

## Archivos clave (sesión, parte 2 — rama de research, NO main)
- `backtest/featurelab.py` — el laboratorio (signal_verdict, filter_verdict, regla dura, cache).
- `backtest/_lab_demo.py` `_lab_features.py` `_lab_candle_verify.py` — demos/verificaciones.
- `backtest/_smc_setups.py` `_swfvg_grid.py` `_confirm_freshness.py` `_fvg_test.py` — tests de features.
- `backtest/_strategy_ab.py` — añadido campo `reason` (autopsia de salidas).
- `live/liquidation_collector.py` + `migrations/liquidity_liquidations.sql` + `railway.liquidations.env.example`.

---

## 13. NautilusTrader — validación de ejecución + barrido de features (2026-06-25)

Se portó la estrategia liquidity (A+B) a **NautilusTrader** (v1.224, Bybit nativo). Enfoque:
`emit_signals()` REUSA nuestros niveles validados (`_listas2`) y Nautilus solo hace la ejecución
(fills maker post-only, fees, parcial). Scripts: `backtest/_nautilus_{spike,real,ticks,offset}.py`.

### Edge sobrevive ejecución realista (OOS ~108d, fees+parcial+R)
| | avgR Nautilus | ref backtest | fill ratio |
|---|---|---|---|
| BTC | +2.01 | +1.82 | 27% |
| ETH | +1.91 | +1.37 | 14% |
| SOL | +1.94 | +0.93 | 14% |

Fill ratio **~14-27% converge** en bar-level, trade-tick+queue (22%) y paper live (~19-40%).
→ La pregunta #1 (fill ratio maker) respondida offline: se llena ~1 de cada 4-7, subset rentable.

### Optimización de ejecución y features — TODO descartado por la regla dura
Nautilus + regla dura (positivo en los 3 con fills reales) cazaron varios falsos positivos:
- **Offset/fill-margin**: 3bps mejora BTC (+37% USD) pero **rompe SOL** → no generaliza. Offset 0 correcto.
- **fib_ote (golden pocket OTE)**: pasó el lab vectorizado (6/6 avgR) PERO bajo fills Nautilus
  **solo ayuda BTC** (+2.52 vs +2.01) y **empeora ETH/SOL** → BTC-only, NO desplegar.
- premium/discount (bias), muros de volumen, thin (vacíos): ❌ no generalizan.

**Lección**: Nautilus refutó features que el backtest vectorizado aprobaba (asume fill al toque).
Esa capa de ejecución es justo lo que faltaba. **El A+B base sigue siendo lo más robusto — no hay
edge nuevo escondido.** El bias ya está balanceado por el mirror (57/43, shorts ≈ longs).

### Conclusión del sprint
Research cerrado: probados sweep/FVG/sweep→FVG/frescura/confluencia/candle/offset/fib_ote/
premium-discount/muros/thin → ninguno generaliza con fills realistas. Nautilus = stack para live
cuando el paper confirme (evidencia ya fuerte). Memoria: [[nautilus-validation]].
