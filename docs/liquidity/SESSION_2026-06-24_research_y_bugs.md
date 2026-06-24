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

## 6. Estado por activo (al cierre de la sesión)

| | Footprint | Estado |
|---|-----------|--------|
| **BTC** | ✅ maduro (267 barras, al día) | **sano — opera con edge completo** |
| ETH | ❌ 2 barras (congelado) | a ciegas (POC midpoint, −29%) |
| SOL | ❌ 2 barras | a ciegas |

- Persistencia: ✅ funciona (posiciones vivas guardadas).
- ETH/SOL **no acumulan footprint**. Logs confirman: **NO crashean** (1 arranque, 0 errores,
  estables 12h). Síntomas: footprint vacío + **fill ratio anómalo (ETH LOW 4%)**.
- **Hipótesis principal**: a ETH/SOL **no les llega el stream completo de `publicTrade`**
  (ticks). Sin ticks → `cur_fp` vacío → footprint no se guarda + fills "perdidos".
- Problema secundario: **`FP_BIN=5.0` fijo** es absurdo para SOL ($5 en precio $70 = 7%).
  Debería ser proporcional al precio. Pero es secundario — sin ticks no importa el bin.

---

## 7. Diagnóstico en curso (commit `eb201a7`, en `main`)

- Agregado **contador de ticks/min** al monitor: cada `bar_close` loguea
  `ticks=N fp_bins=M`.
- **Próximo paso**: tras el redeploy, comparar en los logs de Railway:
  - BTC `ticks=~40000` vs ETH/SOL `ticks=~pocos` → confirma stream incompleto.
  - Si los 3 reciben ticks pero ETH/SOL `fp_bins=0` → el problema sería binning/guardado.
- Según el resultado: arreglar la conexión WS (si faltan ticks) y/o el `FP_BIN` proporcional.

---

## 8. Pendientes / decisiones abiertas

1. **Leer los logs nuevos** (con `ticks=`) para confirmar la causa del footprint ETH/SOL.
2. **Bin proporcional al precio** (`FP_BIN = precio × 0.0001`) — para que todos los pares
   tengan footprint útil. Toca `levels.rs` (invasivo), hacer tras confirmar diagnóstico.
3. **Cleanup del footprint** (borrar barras > 300) → mantenerlo en ~1 MB constante.
4. **Liquidaciones**: única señal de orderflow sin explorar (no hay histórico; capturable
   en vivo suscribiendo `allLiquidation` y guardando a futuro).
5. **UI vista live** (`apps/trade-lab/.../LivePaperView.tsx`): visor de trades del paper
   (chart de velas + tabla + detalle). **Sin commitear** (solo local).
6. **Regla operativa**: cualquier push a `main` redeploya los 3 servicios → warmup. Usar
   ramas para cambios que NO van al deploy; main solo cuando se quiere redeployar a propósito.

---

## Archivos clave de esta sesión

- `crates/liquidity_monitor/src/{book,supa,main,levels}.rs` — fixes de persistencia + contador ticks.
- `crates/ob_heatmap/` — reconstructor del libro completo en Rust.
- `migrations/liquidity_paper_open_pos.sql` — tabla de posiciones persistidas.
- `backtest/_*.py` — scripts de research (early_signal, book_heatmap_poc, sweep_test,
  flow_edge, cvd_div, side_bias, reconstruct_orphans, early_deep).
- `backtest/_strategy_ab.py` — params de investigación aditivos (defaults = original).
