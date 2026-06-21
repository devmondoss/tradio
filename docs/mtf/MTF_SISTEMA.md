# MTF System — Documentación Canónica (FUTUROS)

**Última actualización:** 2026-06-19 (sesión 2 — corrección lookahead)
**Mercado:** Bybit **perpetual** (linear), **BTCUSDT único**, timeframe M1
**Estado:** infra correcta y verificada; **edge causal honesto fino y NO desplegable** en BTC solo.
NO poner dinero real con expectativa positiva. Paper/forward únicamente.

> **Fuente de verdad única.** Este documento describe el sistema MTF **sobre futuros**. Las eras
> anteriores (multi-símbolo Binance, BTC spot) están en [`../archive/mtf/`](../archive/mtf/) y NO
> son el estado vigente. Si algo aquí contradice un doc archivado, manda este.
>
> Arco histórico de la pivota a futuros: worklogs [`MTF_WORKLOG_2026-06-18.md`](MTF_WORKLOG_2026-06-18.md)
> (fees + dataset real), [`MTF_WORKLOG_2026-06-19.md`](MTF_WORKLOG_2026-06-19.md) (sistema v2 + directions)
> y [`MTF_WORKLOG_2026-06-19_lookahead.md`](MTF_WORKLOG_2026-06-19_lookahead.md) (el lookahead que tumbó el edge).

---

## 0. ⚠️ Verdad vigente (leer antes que nada)

El sistema `directions` (`backtest/mtf_system.py`) llegó a reportar **$500 → $74.9K** (OOS WR 53.7%,
AvgR +0.412). **Ese número era LOOKAHEAD.** Las features de estructura H1/H4 mapeaban el bucket HTF
**en-curso** (close = fin de hora) a cada barra M1 → cada minuto "veía" el cierre futuro de su hora.

- **Corregido a causal** (`compute_spot_features.py`, mapear al último bucket CERRADO, `idx-1`).
- Parquet regenerado causal: `data/bybit-perp/processed/btcusdt_perp_m1.parquet`
  (backup del lookahead: `…btcusdt_perp_m1.parquet.lookahead.bak`, reversible).

**Impacto lookahead → causal:**

| | Lookahead (falso) | Causal (honesto) |
|---|---|---|
| Combinado OOS | WR 53.7% / +0.412R | WR 41.0% / **+0.126R** |
| Shorts OOS | 53.3% / +0.386 | 42.3% / **+0.164** |
| Longs OOS | +0.681 | **−0.259 (negativo)** |
| Capital $500→ | $74,916 | **$3,297** |

**Conclusión:** sobre BTC solo, con n≈40-58 trades OOS, no hay edge causal robusto que extraer o
refinar — todo refinamiento sobreajusta y los configs oscilan de signo con cambios menores (ruido).
Lo que sí quedó: **infra correcta** (sidecar de features, paridad perfecta Python↔Rust, venue config,
parquet causal). El config actual está revertido a un **baseline honesto** (unión de triggers, SIN
gates `fp_*` overfit, SIN `vpin`, **longs OFF**).

> ### ⚠️ 2026-06-19 (sesión 3): 2º LOOKAHEAD (en el stop) + template muerto + pivota a ticks
> Ver [`MTF_WORKLOG_2026-06-19_microstructure_pivot.md`](MTF_WORKLOG_2026-06-19_microstructure_pivot.md).
> - **2º lookahead:** el motor ancla el stop al high del bucket H1 **en-curso** (`h1hi.get((ts//H1_MS)*H1_MS)`,
>   `mtf_system.py:179`) → conoce el máximo futuro de la hora. Inflaba ~+0.2R IS. Con stop **causal**
>   (swing reciente / bucket cerrado) el "+0.16R residual" desaparece (OOS negativo). En LIVE no existe
>   (streaming es causal) → el **backtest sobrestimaba lo que live haría**.
> - **Template muerto:** 24 setups ICT+orderflow (fade/sweep/continuación/orderflow/exhaustion), ambas
>   direcciones, M1/M5/M15 y geometría swing → **todos negativos causal**, peores que un baseline nulo OOS.
> - **Micro M1 no predice:** retorno futuro 1/5/15m condicionado a cada señal (15k+ instancias) da
>   exceso ±1-2 bps vs **11 bps de fee** → ~0. **La micro agregada a M1 no tiene alfa intradía.**
> - **Pivota:** los ticks crudos estaban borrados; se re-bajan ticks + OB 1s + **derivados (OI/funding)**
>   a `E:\Tonnio` (portátil) para medir microestructura en horizonte NATIVO (eventos sub-minuto).
>   Pipeline nuevo: `download_raw.py`, `download_derivatives.py`, `build_events.py`, `_event_predict.py`.

---

## 1. Concepto

MTF = **Multi-TimeFrame**. Tres timeframes simultáneos; sin los tres alineados no hay trade:

| TF | Rol |
|----|-----|
| **D1** | Régimen (EMA20): contexto bajista/alcista |
| **H1** | Estructura (BOS / ChoCH): confirma momentum direccional |
| **M1** | Entry (wick + orderflow): momento preciso de entrada |

El stop estructural H1 es el corazón del diseño: un stop ajustado (~0.11%) es destruido por fees
(~0.4R/trade sobre notional). Con stop H1 acotado a **0.30%–0.75%** el fee real (0.11% RT en perp)
pesa ~0.05–0.10R y deja viable un sistema M1.

---

## 2. Dataset y venue

```
data/bybit-perp/processed/
  btcusdt_perp_m1.parquet            → ~767K barras M1, features causales
  btcusdt_perp_m1.parquet.lookahead.bak → backup pre-corrección (NO usar)
```

- **Venue:** Bybit perpetual (linear). Fee round-trip **0.11%** (`DEFAULT_FEE_RT = 0.0011`).
- La estrategia es **agnóstica de venue**: el fee es config swappable (`fee_rt` + `set_fee_rt()` en
  el detector Rust). Default preserva paridad con el backtest.
- **Por qué futuros y no spot:** a fee spot (0.20%) el sistema pierde; en spot puro no se puede
  shortear; no hay data histórica de futuros descargable más allá de este dataset. Ver worklog 06-18.

---

## 3. Arquitectura (SHORTS — única dirección activa)

```
Gate 0  : D1 EMA20      — close <= EMA20 × 0.980  (régimen bajista sin sobreextensión)
Gate 0b : H1 estructura — h1_bos_bear OR h1_choch_bear   ← CAUSAL (bucket H1 cerrado)
Gate 1  : Nivel         — VAH (±0.7%) | AH (asian_high) | PDH (solo con VAH) | WH (weekly_high)
Gate 2  : Rechazo       — wick superior > 30% del rango, close <= open
Gate 2a : body_below_poc — el cuerpo cierra por debajo del POC de sesión
Gate 2b : agresión      — minus_ticks > plus_ticks
Veto    : NOT fp_absorb_buy   (comprador absorbiendo activamente → skip)
Veto    : vp_lvn_below == False bloqueado (ese subconjunto pierde IS/OOS)
Sesión  : london | overlap | ny

Disparador (unión ICT, ver §5)
Stop    : H1_high + 0.40 × ATR_H1, acotado 0.30% < stop_pct < 0.75%
Salida  : target 2.8R | stop H1 | timeout 240 barras (4h)
```

**Longs (DESACTIVADOS, `LONGS_ENABLED = False`).** El detector espejo (rechazo en VAL/AL/PDL/WL,
régimen alcista D1 1.000–1.030, `h1_bos_bull`, vetos `h4_bos_bear` / `fp_absorb_sell`) existe en
código, pero sobre datos causales los longs dan AvgR negativo (≈−0.10 a −0.26R OOS). Se conservan
apagados; reactivar solo si aparece evidencia causal IS/OOS.

---

## 4. Parámetros (valores reales en código)

Fuente: `backtest/mtf_system.py` y `data/src/strategy/detectors/mtf_spot_detector.rs`.

| Parámetro | Valor | Notas |
|-----------|-------|-------|
| Capital inicial | $500 | rebalanceo mensual |
| Risk por trade | 2% | base; sizing en 1.0× (sin boost vpin, ver §6) |
| Fee RT | **0.11%** | perp Bybit taker, sobre notional (`DEFAULT_FEE_RT=0.0011`) |
| MIN_STOP_PCT | 0.30% | — |
| MAX_STOP_PCT | 0.75% | **crítico — el edge vive aquí** |
| TARGET_R | **2.8R** | barrido 1.5→4.0R sobre 17 meses: maximiza TotalR IS, min divergencia IS/OOS |
| ATR_MULT | 0.40 | stop = H1_high + 0.40·ATR_H1 |
| FORWARD / timeout | **240 barras (4h)** | la cola swing >4h aporta poco; se siente intradía |
| COOLDOWN | 15 barras | anti-stack: misma dirección no re-entra dentro de 15 min |
| MAX_OPEN_PER_SIDE | 1 | modelo `directions` (1 short + 1 long, no se solapan) |
| LEVEL_TOL | 0.7% | proximidad al nivel |
| D1_REGIME_THR | 0.980 | shorts: close < EMA20·0.98 |

**Ventanas walk-forward:** IS = Ene 2025–Feb 2026 (~425 días); OOS = Mar–Jun 2026 (~92 días).

---

## 5. Disparadores ICT — unión (shorts)

Un solo patrón de vela (rejection@VAH) limita el volumen a ~0.31 tpd. La unión de disparadores ICT
sobre la **misma** confirmación orderflow sube el volumen sin diluir el edge:

| Trigger | Condición | Estado |
|---------|-----------|--------|
| `rejection_VAH` | rechazo en VAH | base |
| `FVG` | `near_bearish_fvg` + rechazo | en unión |
| `OrderBlock` | `near_bearish_ob` + rechazo | en unión |
| `Displacement` | `displacement_bear` | en unión |
| `LiquiditySweep` | `sweep_confirmed` | en unión |
| ~~OTE (fib 62-79)~~ | — | EXCLUIDO (colapsa OOS) |
| ~~EqualHigh sweep~~ | — | EXCLUIDO (flip OOS) |

**La unión es NO-separable (slot-competition):** quitar un trigger por su número aislado EMPEORA el
sistema, porque libera slots a entradas marginales peores. No "limpiar" triggers por su métrica suelta.

⚠️ **Sobre datos causales** la robustez por-trigger es débil (FVG IS≈0, Displacement/Sweep flipean
OOS). La unión completa da OOS ~+0.16R vs rejection-solo ~−0.06R, pero **ninguna config supera el
nivel de ruido**. Documentado como el mejor baseline honesto, no como edge validado.

---

## 6. Sizing — estado honesto

El "score sizing" (era spot) y el "sizing vpin-aware" (era directions con lookahead) **no sobreviven**:

- **`vpin` MUERTO en causal:** flipea IS/OOS. Era artefacto del lookahead → eliminado del sizing.
- **`fp_result_sell` / `fp_sell_dom`:** parecían robustos sobre el motor `mtf_v2` (+0.36/+0.35R), pero
  al hornearlos en `mtf_system` (motor directions) dan OOS **negativo**. Eran **overfit a otro motor**.
- **Score sizing (spot):** auditado como **apalancamiento, no edge** — no cambia WR/AvgR, solo cuánto
  monta cada trade (ver `../archive/mtf/MTF_SPOT_SIZING_AUDIT.md`).

**Config actual (`_sizing` en `mtf_system.py`):** tiers sobre `cvd_slope` / `n_trades` (tape ≥ Q50 IS)
/ `obi10_mean`, **sin `vpin`**:

| Condición | Mult |
|-----------|------|
| `cvd_slope>0` AND tape | 2.0× |
| (shorts) `cvd>0` OR tape OR `obi10≥0` | 1.5× |
| (longs) `cvd>0` OR `obi10>0` | 1.5× |
| ninguno | 1.0× |

Recordar: el sizing es apalancamiento, no edge — sube/baja el $ por trade pero no el AvgR. Con el
edge causal ~ruido, estos multiplicadores amplifican un edge no validado; no cementarlos como señal.

---

## 7. Infra construida y verificada (esto SÍ sirve)

**Principio de arquitectura:** Python = laboratorio (research + features, UNA fuente de verdad),
Rust = motor (streaming + ejecución). A 1 barra/min la latencia de Python es irrelevante.

| Componente | Archivo | Estado |
|------------|---------|--------|
| Sidecar de features (stdin/stdout JSON-lines) | `backtest/feature_server.py` | Funciona; warmup precarga cola del parquet, `enrich()` + resample H1, emite `MtfSpotBarContext` |
| Validador sidecar vs parquet | `backtest/_feature_parity.py` | **Paridad perfecta causal** (0 mismatches / 150 barras) |
| Paridad detector Rust vs Python | `backtest/mtf_system_parity.py` | 391/391 (estructural; los números cambiaron al corregir lookahead, el detector sigue fiel al contexto que recibe) |
| Detector | `data/src/strategy/detectors/mtf_spot_detector.rs` | Modelo directions, venue fee, `Vec` de eventos (cierre+apertura misma barra), `VecDeque` (evita O(n²)) |
| Autopsia del lookahead | `backtest/_lookahead_impact.py` | Reproduce el colapso |

**Pendiente (congelado):** cablear spawn+IPC del sidecar en el monitor Rust con política de fallo
(si el sidecar muere → kill-switch de entradas; nunca operar a ciegas). Hoy el live corre degradado
(2 sitios en `crates/monitor/src/main.rs` construyen `MtfSpotBarContext` con defaults).

> **Nota de nombres:** el detector se llama `mtf_spot_detector.rs` y la tabla `mtf_spot_trades`, pero
> **corren sobre futuros**. El rename a `Mtf*` agnóstico está diferido: colisiona con detectores legacy
> (`mtf_shorts_detector` define `MtfBarContext/MtfSignal/MtfTrade`, aún cableados). Hacer al limpiar legacy.

---

## 8. Cómo reproducir

```bash
python backtest/mtf_system.py                      # sistema causal honesto (shorts, ~+0.16R OOS, fino)
python backtest/_lookahead_impact.py               # autopsia lookahead vs causal
python backtest/_feature_parity.py --warmup 15000  # valida sidecar == parquet
python backtest/mtf_system_parity.py --reuse-ctx   # paridad Rust (recompila bin si hace falta)
# revertir a lookahead (NO recomendado): copiar .lookahead.bak sobre el parquet
```

---

## 9. Paper forward (sin expectativa positiva)

Si se corre paper, es para **construir data de futuros hacia adelante** y observar transferencia de
flujo, NO porque haya edge validado. Setup en [`MTF_FUTURES_PAPER_RUNBOOK.md`](MTF_FUTURES_PAPER_RUNBOOK.md):

```bash
MONITOR_EXCHANGE=bybit_linear            # FUTUROS
MONITOR_PROFILE=mtf_spot_futures_paper   # solo shorts
MONITOR_STRATEGIES=mtf_spot_shorts
SYMBOLS=BTCUSDT
TIMEFRAME_MIN=1
```

Las señales caen en `mtf_spot_trades` (separar por venue/strategy al analizar). Antes de desplegar:
`cargo build -p monitor` y re-correr `mtf_system_parity.py` tras cualquier cambio del Python.

---

## 10. Opciones abiertas (decisión del usuario)

1. **Setups genuinamente distintos** sobre datos causales (absorción pura, CVD divergence multi-barra,
   sweep+reclaim como trigger primario, otro timeframe/horizonte). Mismo límite de muestra.
2. **Paper el config menos-malo** (unión causal ~+0.16R) solo para forward real — break-even esperado.
   Requiere cablear el sidecar al monitor (§7 pendiente).
3. **Re-pensar la premisa:** ¿M1 intradía BTC con stops 0.3–0.75% deja señal sobre ruido+fees? Quizás
   el edge esté en otro horizonte.
4. **Multi-instrumento:** resolvería el cuello de muestra (edge independiente en ETH/SOL paralelos,
   los tpd suman sin inflar DD), pero hoy solo hay dataset de BTC (ETH purgado 06-18, re-descargar).

---

## 11. NO repetir (lecciones)

- **No validar refinamientos en un motor** (`_edge_research`/`mtf_v2`) y asumir que transfieren a otro
  (`mtf_system` directions). Validar SIEMPRE en el motor de despliegue.
- **No tunear sobre n≈50 OOS** — es pescar ruido. Si el config oscila de signo con cambios menores, no hay edge.
- **No usar features HTF del bucket en-curso** (lookahead). Usar siempre el último bucket CERRADO.
- **No confundir sizing con edge.** El "score/vpin sizing" es apalancamiento; no infla el AvgR.

---

## 12. Historia

- **Worklogs (cronología):** `MTF_WORKLOG_2026-06-17.md` → `…06-18.md` → `…06-19.md` → `…06-19_lookahead.md`.
- **Eras previas (archivadas):** [`../archive/mtf/`](../archive/mtf/) — multi-símbolo Binance (era 1) y
  BTC spot (era 2). Razonamiento de research preservado; no es spec activa.
- **Catálogo de conceptos orderflow:** [`../ORDERFLOW_CATALOGO.md`](../ORDERFLOW_CATALOGO.md).
