# Paper Futuros — Runbook (shorts spot-calibrados sobre Bybit Linear)

> Objetivo: validar FORWARD el sistema de shorts (única estrategia con edge) en
> futuros, porque no hay data histórica de futuros para backtestear. El paper
> recoge su propia data hacia adelante.
> Creado: 2026-06-18. Estado vigente y el porqué de futuros: [`MTF_SISTEMA.md`](MTF_SISTEMA.md).
> Razonamiento original (archivado): [`../archive/mtf/MTF_SPOT_EDGE_REALITY_Y_PLAN.md`](../archive/mtf/MTF_SPOT_EDGE_REALITY_Y_PLAN.md).

---

## Qué validamos y por qué futuros

- A fee spot (0.20%) el sistema pierde. A fee futuros (0.11%) los **shorts** dan +0.121R OOS.
- No se puede shortear en spot puro ni bajar data histórica de futuros → **paper live en futuros** es el único camino.
- **Hipótesis a validar:** el edge calibrado sobre order book de SPOT transfiere al order book de FUTUROS. El precio transfiere casi seguro; el flujo (OBI/CVD/delta) es la incógnita.

---

## Cambios de código hechos (2026-06-18)

| Archivo | Cambio |
|---|---|
| `crates/monitor/src/monitor_config.rs` | Nuevo perfil `mtf_spot_futures_paper` (solo shorts) + gate para NO desactivar detector spot en exchange de futuros |
| `data/src/strategy/detectors/mtf_spot_detector.rs` | `TARGET_R 2.0→2.5`, `FEE_RT 0.0007→0.0011`, **CVD exit eliminado en shorts** (cortaba ganadores) |

**Longs NO se ejecutan** (`mtf_spot_longs=false` en el perfil) — descartados por falta de edge.

⚠️ **Pendiente de verificar build:** no se pudo compilar localmente (falta linker MSVC).
Antes de desplegar correr: `cargo build -p monitor` (en máquina con toolchain o en Railway).

---

## Config de despliegue (env vars)

```bash
MONITOR_EXCHANGE=bybit_linear        # FUTUROS (no bybit_spot)
MONITOR_PROFILE=mtf_spot_futures_paper
MONITOR_STRATEGIES=mtf_spot_shorts   # redundante pero explícito; longs off
SYMBOLS=BTCUSDT
TIMEFRAME_MIN=1
```

Servicio Railway separado (ej. `monitor-fut-paper`) para no mezclar con spot/futures viejos.

---

## Validación forward (lo que de verdad importa)

El paper no es solo "ver si gana plata" — es **construir la data de futuros que no pudimos descargar** y comparar contra la hipótesis:

1. **Order book de futuros se loguea solo:** el monitor ya escribe muestras OBI a Supabase (`obi_10s`) y la microestructura de cada barra. En futuros, esto acumula el dataset de perp.
2. **Comparar flujo futuros vs spot:** en cada señal, revisar `obi_entry`, `delta_entry`, `cvd_slope_entry` (se guardan en `mtf_spot_trades`). ¿El OBI en el rechazo es negativo como en spot? Si el flujo de futuros se comporta distinto, los umbrales (`obi<-0.05`) podrían necesitar recalibración.
3. **Comparar fills teóricos vs precio real** del stream de futuros (slippage de entrada a `close`, gaps en stop/target).
4. **Métricas objetivo (≥2-4 semanas):** WR ~40%, AvgR neto ~+0.12, sin desvío grande vs backtest spot. Si WR/AvgR colapsan → el flujo no transfiere.

---

## Checklist de arranque

1. [ ] `cargo build -p monitor` compila sin errores.
2. [ ] Confirmar que el feed `bybit_linear` conecta klines + order book para BTCUSDT.
3. [ ] Confirmar que las filas caen en `mtf_spot_trades` (distinguibles del spot — verificar si se loguea el exchange/venue en la fila; si no, añadir columna).
4. [ ] Logs `[mtf_spot] SIGNAL` / `[mtf_spot] CLOSED` aparecen.
5. [ ] Revisar primeras señales: dirección Short, target 2.5R, sin cvd_exit.
6. [ ] Dejar correr, comparar semanalmente contra las métricas objetivo.

---

## Riesgos conocidos

- **El flujo de futuros puede no transferir** (más liquidez, liquidaciones, funding). Es lo que validamos.
- **Edge fino y volátil** (un mes carga gran parte del año). 2-4 semanas de paper pueden no ser concluyentes — idealmente correr más.
- **El detector Rust aún no tiene parity re-verificada** contra el Python actualizado (target 2.5R, sin CVD). Conviene re-correr `mtf_spot_parity.py` tras el cambio.
- `mtf_spot_trades` guarda las señales de futuros mezcladas con histórico spot si las hubiera — separar por exchange/strategy al analizar.
