# Sesión 2026-06-26 — Revisión BD, reconstrucción tick-a-tick y fix del parcial

Sesión de auditoría profunda del paper trading de la estrategia de provisión de liquidez.
Se revisó la BD tras ~2 días sin tocarla, se reconstruyó **cada trade tick-a-tick** desde
los raw_trades de Bybit, y se halló + arregló un bug vivo del parcial.

**Conclusión de fondo: la estrategia y la observabilidad están sanas. El edge se confirma
en vivo (+163R era limpia). El binario registra métricas fieles. Se encontró y arregló un
bug del parcial (se desincronizaba al restaurar posiciones tras cada deploy). Liquidaciones
descartadas (preliminar). Ninguna optimización nueva pasó la regla dura.**

---

## 1. Revisión de la BD — el paper está vivo y en verde

Falsa alarma inicial: filtré por `closed_at` (null en nativos) → parecía muerto. La timeline
real de nativos es `created_at`. Servicios vivos (liquidaciones hasta 06-28, snapshots al día).

**Era limpia** (`HIGH_VOL_ONLY=true` activo desde **2026-06-25 14:00 UTC**, n=84 nativos):

| Símbolo | n | avgR | sumR | WR |
|---|---|---|---|---|
| BTCUSDT | 28 | +3.60 | +100.9 | 29% |
| ETHUSDT | 37 | +0.84 | +31.2 | 24% |
| SOLUSDT | 19 | +1.64 | +31.1 | 37% |
| **TOTAL** | 84 | **+1.94** | **+163.2** | 29% |

- avgR saltó de +0.315 (con baja vol) → **+1.94** limpio. El filtro ATR hace lo prometido.
- WR bajo = por diseño: 13 target (+10.3R) + 4 timeout (+12.7R) pagan 34 stops (−1.2R).
- **Fill ratio real** (tabla `fill_ratio`): maker alta vol **BTC 21% · ETH 28% · SOL 21%**.
  Converge con Nautilus (~14-27%). Pregunta abierta #1 cerrada.

### Tablas y espacio
10 tablas, ninguna muerta. Footprint **1.95 MB** (medido vía `footprint_stats.kb_used`),
total BD **~5-7 MB** (~1% del free tier). Crecimiento ~0.85 MB/día (footprint + liq).
`open_pos` vacía = sin posiciones abiertas (transitoria, no muerta). `events` se detiene en
periodos de baja vol (filtro idle = sin places = sin eventos; no es bug).

---

## 2. HIGH_VOL_ONLY confirmado en producción

El usuario ya lo había puesto en `true` el 06-25 14:00. Verificado: último trade de baja vol
06-25 14:00, **70+ trades desde entonces TODOS high vol** (cero filtraciones). Paridad de
cálculo confirmada: `atr_median()` = mediana últimos 500 ATR = `rolling(500).median()`.

---

## 3. Reconstrucción tick-a-tick (lo central)

Se descargaron ~42M ticks reales de Bybit (BTC/ETH/SOL, 06-22→06-27, `download_trades_symbol.py`)
y se reconstruyó cada trade desde el fill hasta el cierre. **Nota: el disco E: estaba
desconectado al inicio (symlink `data/bybit-perp` roto); el usuario lo reconectó.**

### 3.1 El binario es FIEL
Replay tick vs BD: MAE error **0.01R**, result_r **0.05R**, MFE 0.34R. **Se puede confiar
en `mfe_r`/`mae_r`/`result_r` de la BD.** (`closed_at` SÍ está poblada en nativos.)

### 3.2 tp1 — paridad de lógica idéntica
Mismo set {VAH, swing50, PDH, weekly}, mismo filtro `>entry*1.001`, tp1=cercano/tp2=lejano.
`SWING=50` = `swing_high_50`. Sin gap de lógica (Rust `struct_target` ≡ Python).

### 3.3 SL pequeñísimo — confirmado, by-design
Stop mediana: **BTC 0.37% · ETH 0.68% · SOL 0.86%**. Causa: ATR M15 de BTC es solo ~0.48%
del precio, y stop = 0.6·ATR sobre la entrada límite en el nivel. Solo 3/84 tocan el floor 0.15%.
Consecuencia: **RR enorme** (BTC mediana 16, poc_def 40, máx 99) = el modelo de payoff.
Costo: fee pesa 0.13-0.19R/trade + más noise-outs (WR bajo).

### 3.4 Parcial vs correr ENTERA — KEEP THE PARTIAL (la regla dura ganó)
La reconstrucción (era limpia, n chico) sugería correr entera (+2.50 vs +1.61 avgR) porque el
binario *de hecho* corría entera. **Pero el backtest 365d con regla dura lo refuta:**

| | OOS parcial → entera | DD p→e | Sharpe p→e |
|---|---|---|---|
| BTC | +1.82 → +2.13 MEJORA | 8.5→12.6% | 6.7→5.6 |
| ETH | +1.37 → +1.53 MEJORA | 6.8→16.9% | 5.0→4.1 |
| SOL | +0.93 → **+0.42 PEOR** | 5.2→8.8% | 6.6→5.4 |

Entera **rompe SOL** y dispara DD en los 3. El parcial es gestión de riesgo. El "entera rinde
más" era espejismo de muestra chica dominada por winners. Se agregó `use_partial` a `run_system`.

### 3.5 Liquidaciones — preliminar NO ayudan
Forward capture OK (11.9k filas, $133M; Buy/shorts-liq domina). Pero solo 10/83 trades tienen
cluster de liq favorable cerca del nivel (entradas=nodos de volumen ≠ zonas de liq), y esos 10
rinden PEOR (−0.21 vs +2.28). corr(liq_fav, result_r)=−0.08. Lógica: cascada *hacia* el nivel =
lo rompen, no lo defienden. Caveat: 3 días overlap, n chico; test riguroso es forward (FASE 2).

### 3.6 Niveles legítimos
**98% de entradas dentro del value area** reconstruido. poc_def entra en el POC (0.01%), poc_ob
offset 0.61% (y los offset son los de mayor payoff: lejos POC +2.45R vs cerca +0.92R).

### 3.7 Duración de trades (R≥2)
- Por R: 2-5R → 2.5h med · 5-10R → 2.7h med · 10R+ → 6.6h med.
- **Ganadores 3.5h mediana vs perdedores 1.3h** → perdedores mueren rápido (stop chico),
  ganadores corren. Targets pegan en 0.6-7h; timeouts corrían aún a las 24h (+18-23R).
- Fill maker: 15-19 min.

---

## 4. El bug del parcial — hallado y arreglado

**Síntoma:** 7/18 fades que tocaron tp1 antes del stop NO bancaron el parcial (`filled1=False`).
6 de 7 con `mfe_r` del binario MUY por encima de tp1 → lo vio y no bancó (no fue gap de WS).

**Causa raíz:** los casos tenían `take_partial=False` pese a `tp1` seteado — imposible en un
nivel fresco (`compute_levels` siempre pone `take_partial = tp1.is_some()`). El flag **se
desincroniza al restaurar posiciones tras cada restart** (deploys). 13/19 casos abrieron
post-restart → bug vivo, no solo legacy. (También: `atr_median` no se persistía en `open_pos`
→ restaurados quedaban con atr_median=0.)

**Fix (commit `d2decdc`, robusto, inmune a restarts):** derivar el parcial de `tp1.is_some()`
en vez del flag persistido (son equivalentes por diseño).
- `book.rs`: las 2 ramas fade usan `p.level.tp1.is_some()` en vez de `p.level.take_partial`.
- `supa.rs` restore: `take_partial` derivado de `tp1`.
- `supa.rs` write: se persiste `atr_median` en `open_pos`.

Compila OK (`cargo build --release`, 35s). **Efecto esperado:** bajará el avgR titular del
paper (la era limpia se infló corriendo enteras) pero alinea con el backtest validado (parcial
protege DD y el edge de SOL). Verificar en 1-2 días: `filled1` debe subir, cero `take_partial=False`
en fades nuevos.

---

## 5. Incidente de git (resuelto)

El repo local tenía un solo remote: `flowsurface-backup` → `guepardez013-commits/flowsurface-dev`.
El primer push fue ahí por error (no se verificó el repo destino de Railway). **Corregido:** se
agregó remote `tradio` → `devmondoss/tradio` (el repo real que ve Railway) y se pusheó `d2decdc`
ahí (fast-forward sobre `e875438`). Se borraron 2 ramas mergeadas que el usuario pidió eliminar
(`fix/persist-open-positions`, `research/orderflow-heatmap-optims`) — cero commits únicos, sin
pérdida. tradio quedó solo con `main` en `d2decdc`.

---

## 6. Optimización anti-overfit (segunda tanda de la sesión)

Metodología fija: regla dura (mejora OOS en BTC+ETH+SOL), mismos params en los 3, mirar DD/Sharpe,
optimizar en histórico (deja el paper como forward), solo palancas estructurales (no señales).

### 6.1 Sweep de palancas (`_bt_levers.py`)
- ✅ **`trail_atr 4→6`** — deja correr los trends. Óptimo en 6-7 (no artefacto), slippage-tolerante.
- ✅ **`stop_scale 0.8`** — stop más chico → más R en el target estructural (riesgo fijo/trade). WR
  estable, sobrevive slippage (ver 6.2), acotado por STOP_FLOOR 0.15%. Conservador (no 0.4-0.7).
- ❌ Descartadas: atr_mult (1.0 correcto), timeout (ruido), p1_frac (0.5 correcto), correr entera
  sin parcial (rompe SOL).
- Combo OOS: BTC +1.82→+2.27, ETH +1.37→+1.86, SOL +0.93→+1.56. Commit `ddbfc66`.

### 6.2 Stress de slippage (`_bt_slip.py`)
La sospecha: el backtest asume fill exacto en el stop. Apliqué slippage adverso a salidas taker
(1-2bp). `stop_scale=0.8` y `trail_atr=6` **sobreviven con margen** en los 3 → no eran artefacto.

### 6.3 El detector de régimen (lo que destrabó el flow-trail)
Diagnóstico: el bucket negativo del paper era **flow-trail (−0.10R)**, porque el binario ruteaba con
`is_trend = |precio-sma50|>0.6·atr`, un detector **inferior**. El backtest rutea con la columna
`regime` (compute_regime), muy superior.
- Port a M15 con EMA20 (=5h) → **NO replica** (peor, DD ×3): el regime del dataset es M1 (EMA20=20min).
- El pushback "¿por qué M1 si M15 es estable?" llevó a la solución: el problema era el **lookback**
  (5h), no la vela. **EMA CORTA en M15.** Sweep + walk-forward (`_bt_regime_fast.py`, `_bt_regime_wf.py`):
- ✅ **`ema5/st2/x1.30`** (EMA5=75min + racha 2 + ATR-expansión 1.3): maximin OOS **+1.66**
  (BTC +1.66/ETH +1.90/SOL +1.88) — **más robusto que el regime M1** (+1.56), muy por encima del
  sma50 (+0.85). Meseta estable ema3-6/st2. Walk-forward: 0 bloques negativos en los 3. **Sin M1.**
- Matiz: M1 le sacaría más a BTC (+2.27 vs +1.66) por su baja vol; ema5/st2 es más parejo. Commit `02c9d15`.

## 7. Plan de deploy

Estado `tradio/main`: `d2decdc` (parcial) → `ddbfc66` (trail/stop) → `02c9d15` (regime ema5/st2).
**Pendiente operativo (manual en Railway):**
1. Redeploy `tradio/main` en los 3 servicios.
2. **`SYSTEM=both` → `SYSTEM=flow`** — ahora `flow` (ruteado único) tiene buen detector y deja de
   duplicar posiciones (el `both` corría maker+flow sobre cada señal).

Validar en paper: flow-trail deja de sangrar, parcial dispara, avgR converge al backtest optimizado.

Roadmap: FASE 1 (acumular limpio) → FASE 2∥3 (liquidaciones forward + testnet/real money).
FASE 4 diferida (M1 regime solo si BTC en paper pide el +0.6R extra).

---

## Scripts nuevos (en `backtest/`)
- `_recon_trades.py` / `_recon_liquidations.py` / `_recon_footprint.py` / `_recon_out.csv` — reconstrucción tick-a-tick.
- `_bt_partial.py` — parcial vs entera. `_bt_levers.py` — sweep de palancas. `_bt_slip.py` — stress slippage.
- `_bt_regime.py` / `_bt_regime_m15.py` / `_bt_regime_fast.py` / `_bt_regime_wf.py` — detector de régimen.
- Motor `_strategy_ab.run_system`: params nuevos `use_partial`, `stop_scale`, `slip_bps`.

## Cerrado esta sesión
tp1 paridad · SL entendido · niveles validados (98% en VA) · parcial (bug arreglado, se queda) ·
liquidaciones descartadas (prelim) · binario validado fiel · fill ratio confirmado ·
**optims trail_atr=6 + stop_scale=0.8 + detector régimen ema5/st2 (validadas, commiteadas).**
