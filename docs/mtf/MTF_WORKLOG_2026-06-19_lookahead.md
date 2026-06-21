# MTF Worklog — 2026-06-19 (sesión 2): port a Rust, sidecar, y el LOOKAHEAD que tumbó el edge

> Sesión larga y de fondo. Empezó como "port a Rust + a live" y terminó descubriendo que
> el edge validado ($74.9K) era en gran parte **lookahead intra-hora**. El resultado más
> valioso: NO se puso dinero real sobre una ilusión. Infra correcta construida; edge honesto
> sobre BTC resultó **fino y no robusto**. Todo queda listo para continuar.

---

## TL;DR para la próxima sesión

1. **El "$74.9K" del modelo directions era lookahead.** La estructura H1/H4 del backtest mapeaba
   el bucket H1 EN-CURSO (close = fin de hora) a cada minuto → una barra de las 10:30 "veía" el
   cierre de las 10:59. Corregido a causal. Honesto: **~$3.3K, OOS combinado +0.126R**.
2. **El edge causal en BTC NO es desplegable.** Shorts causal oscilan +0.25 a −0.06R OOS según
   detalles de config = **nivel de ruido**. Cuello: n≈40-58 trades OOS, muy chico para validar/refinar.
3. **`vpin` murió causal** (era artefacto del lookahead). El "+0.44R recuperado" con `fp_result_sell`
   era **overfit a otro motor** (mtf_v2, timeout 20h); en el motor directions (4h) da OOS negativo.
4. **Infra nueva, correcta y verificada**: sidecar Python (`feature_server.py`, una fuente de verdad)
   + paridad de features PERFECTA + venue config (fee swappable) + parquet causal (backup guardado).
5. **NO ir a live ni paper con expectativa positiva.** Opciones abiertas abajo (§9).

---

## 1. Port del detector a Rust (modelo directions) — hecho, pero ojo

- `data/src/strategy/detectors/mtf_spot_detector.rs` reescrito al modelo directions:
  unión ICT shorts (rejection@VAH + FVG + OB + Displacement + Sweep), longs espejo,
  veto `vp_lvn_below`, sizing vpin-aware, timeout 240 (4h), cooldown 15m, `on_bar_close`
  ahora devuelve `Vec<MtfSpotTrade>` (cierre+apertura misma barra), `VecDeque` para samples
  (evita O(n²) de `Vec::remove(0)`).
- Harness nuevo `backtest/mtf_system_parity.py` (compara contra `mtf_system.py`, no el spot viejo).
- **Paridad lograda 391/391** — PERO contra el backtest CON lookahead. Tras corregir lookahead,
  el detector sigue en paridad estructural (usa el contexto que se le alimenta), pero los NÚMEROS
  del sistema cambiaron (ver §3). El detector Rust está OK; lo que cambió es la verdad del edge.
- 3 bugs de paridad cazados: (a) estructura H1/H4 calculada interna vs parquet → alimentar por
  contexto `Option<bool>`; (b) re-entrada en misma barra de cierre → `Vec` de eventos; (c) O(n²).

## 2. Costura Python↔Rust (decisión de arquitectura) — sidecar

- **Principio**: Python = laboratorio (research/features), Rust = motor (streaming/ejecución).
  Las features viven en Python (`compute_spot_features.py`), UNA fuente de verdad. Rust pide el
  contexto por barra. A 1 barra/min, la latencia de Python es irrelevante.
- `backtest/feature_server.py`: sidecar stdin/stdout JSON-lines. Warmup precarga cola del parquet,
  appendea barra base, corre `enrich()` + resample H1, emite `MtfSpotBarContext`. Funciona.
- `backtest/_feature_parity.py`: valida sidecar (rolling enrich) vs parquet (full enrich).
  **Resultado: PARIDAD PERFECTA** sobre datos causales (0 mismatches en 150 barras).
- **Pendiente** (no hecho, congelado): cablear el spawn+IPC del sidecar en el monitor Rust con
  política de fallo (si el sidecar muere → kill-switch de entradas, nunca operar a ciegas).

## 3. EL LOOKAHEAD (el hallazgo central)

`compute_spot_features.compute_h1_features`/`compute_h4_features` agrupaban M1 por bucket H1/H4
con `close='last'` y mapeaban el valor del bucket EN-CURSO a cada barra M1 del bucket
(`h1_idx = searchsorted(...)`). Como el `close` del bucket es el de fin-de-hora, **cada minuto
"veía" el cierre futuro de su hora** → lookahead intra-hora en `h1_bos_bear/bull`, `h1_choch_*`,
`h4_bos_bear`, etc.

- **Fix (causal)**: mapear al ÚLTIMO bucket CERRADO → `h1_idx = searchsorted(...) - 1` (idem H4).
  Editado en `compute_spot_features.py` (líneas ~887 y ~1010). Comentado con fecha.
- **Parquet regenerado** causal: `data/bybit-perp/processed/btcusdt_perp_m1.parquet`.
  Backup del lookahead en `...btcusdt_perp_m1.parquet.lookahead.bak` (REVERSIBLE).

### Impacto (lookahead → causal)
| | Lookahead | Causal honesto |
|---|---|---|
| Combinado OOS | WR 53.7% / +0.412R | WR 41.0% / **+0.126R** |
| Shorts OOS | 53.3% / +0.386 | 42.3% / **+0.164** |
| Longs OOS | +0.681 | **−0.259 (negativo)** |
| Capital $500→ | $74,916 | **$3,297** |

## 4. Autopsia: por qué colapsó (`_lookahead_impact.py`)

- El gate de estructura dispara con la MISMA frecuencia (h1_bos_bear 31.0% en ambos) — el lookahead
  no cambió cuántas, cambió CUÁLES barras.
- Diff de trades (mismo sistema, ambos parquets):
  - comunes 205: WR 48% / +0.349
  - **solo-causal (nuevos) 169: WR 41% / +0.021** (duds que el lookahead vetaba)
  - **solo-lookahead (perdidos) 186: WR 61% / +0.631** (ganadores que el peek cherry-pickeaba)
- **El lookahead era un filtro casi-perfecto del futuro**: entraba justo cuando la hora cerraba a
  favor. Quitarlo = pierdes los +0.631, heredas los +0.021. Edge honesto vive en downtrends
  estables (comunes, +0.388 en mtf_v2), pero aislarlos causalmente sin peek no da algo robusto.

## 5. Re-research causal (foco shorts) — qué sobrevive y qué no

Sobre `_edge_research.py` (entradas mtf_v2 v2, causal):
- **`vpin` MUERTO**: flipea IS/OOS. Era artefacto del lookahead. → eliminado del sizing.
- `fp_result_sell` / `fp_sell_dom`: parecían robustos (+0.36/+0.35) sobre el motor mtf_v2.
- **PERO** al hornearlos en `mtf_system` (motor directions): OOS NEGATIVO (−0.063). El +0.44R era
  **overfit a otro motor** (mtf_v2 timeout 20h + level-blocks, ≠ directions 4h + cooldown).
- Por-trigger causal (decidido por IS): solo `rejection_VAH` algo robusto (IS +0.332/OOS +0.208).
  FVG IS≈0, Displacement/Sweep flipean OOS. Pero rejection-solo en directions da OOS −0.063;
  la unión completa da ~+0.16 por dinámica de slots. **Ningún config supera nivel de ruido.**

**Meta-lección**: con n≈40-58 OOS en un símbolo, todo refinamiento sobreajusta. No hay datos
suficientes para extraer/validar un edge fino en BTC solo.

## 6. Venue config (adaptabilidad)

- `FEE_RT` (0.0011) sacado del detector a campo `fee_rt` + `set_fee_rt()` (default preserva paridad).
  La estrategia es agnóstica de venue; fee/market son config swappable (Bybit/Binance/WhiteBit).
- **Rename agnóstico `Mtf*` DIFERIDO**: colisiona con detectores legacy (`mtf_shorts_detector` define
  `MtfBarContext/MtfSignal/MtfTrade`, aún cableados en monitor). Hacer cuando se limpien legacy.

## 7. Estado de archivos (para continuar)

| Archivo | Estado |
|---|---|
| `backtest/mtf_system.py` | Sistema directions. Revertido a baseline honesto: unión triggers, SIN gates fp (overfit), SIN vpin, `LONGS_ENABLED=False`. Docstring aún dice $74.9K (DESACTUALIZADO — corregir). |
| `backtest/feature_server.py` | Sidecar features (una fuente de verdad). Funciona. |
| `backtest/_feature_parity.py` | Valida sidecar vs parquet. Paridad perfecta causal. |
| `backtest/mtf_system_parity.py` | Paridad detector Rust vs mtf_system. 391/391 (era lookahead). |
| `backtest/_lookahead_impact.py` | Autopsia del colapso. |
| `backtest/_edge_research.py` / `_edge_research2.py` | Análisis de features (correr sobre causal). |
| `backtest/compute_spot_features.py` | **Lookahead H1/H4 corregido a causal** (líneas ~887, ~1010). |
| `data/...btcusdt_perp_m1.parquet` | **Causal** (regenerado). Backup: `.lookahead.bak`. |
| `data/src/strategy/detectors/mtf_spot_detector.rs` | Modelo directions, venue fee, Vec eventos, VecDeque. Compila (toolchain GNU 1.95.0). |
| `crates/monitor/src/main.rs` | 2 sitios construyen `MtfSpotBarContext` con defaults (live degradado hasta cablear sidecar). |

## 8. Cómo reproducir
```
python backtest/mtf_system.py                      # sistema causal honesto (shorts, ~+0.16 OOS, fino)
python backtest/_lookahead_impact.py               # autopsia lookahead vs causal
python backtest/_feature_parity.py --warmup 15000  # valida sidecar == parquet
python backtest/mtf_system_parity.py --reuse-ctx   # paridad Rust (recompila bin si hace falta)
# revertir a lookahead (NO recomendado): copiar .lookahead.bak sobre el parquet
```

## 9. Opciones abiertas (decisión del usuario, próxima sesión)

1. **Explorar setups genuinamente distintos** sobre datos causales (absorción pura, CVD divergence
   multi-barra, sweep+reclaim como trigger primario, otro timeframe). Mismo límite de muestra.
2. **Paper el config menos-malo** (unión causal ~+0.16R) solo para forward real — NO validado-positivo,
   expectativa break-even. Requiere cablear el sidecar al monitor (§2 pendiente).
3. **Re-pensar la premisa**: ¿M1 intradía BTC con stops 0.3-0.75% deja señal sobre ruido+fees?
   Quizás el edge esté en otro timeframe/horizonte. Multi-instrumento resolvería el cuello de
   muestra pero no hay datos (solo BTC).

## 10. NO repetir (errores de esta sesión)
- No validar refinamientos sobre un motor (`_edge_research`/mtf_v2) y asumir que transfieren a otro
  (mtf_system directions). Validar SIEMPRE en el motor de despliegue.
- No tunear sobre n≈50 OOS — es pescar ruido. Si el config oscila de signo con cambios menores, no hay edge.
- No confiar en features de HTF mapeadas del bucket en-curso (lookahead). Usar bucket cerrado.
