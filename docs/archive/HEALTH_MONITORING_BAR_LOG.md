# Health Monitoring del Bar Log
## FlowSurface Monitor

**Implementado:** Mayo 2026 — `crates/monitor/src/main.rs`  
**Estado (2026-05-29):** ✅ Completamente implementado y activo en Railway.

El [bar] log del monitor expone el estado de cada stream y la frescura de cada fuente de datos institucional, permitiendo diagnosticar problemas de WebSocket o pipeline sin grep adicional.

---

## Estado actual del [bar] log

El log actual muestra:
```
[bar] ts=1779011100000 close=78112.69 regime=TrendUp slow=0.168 fast=0.223
      funding=0.2337 basis=-0.065% oi_delta=0 vwap=78074.33 cvd=858.4
      ob=live inst=Live ls_top=49.9%/55.6% liq=0$
      action=Wait score=0.000 delivery=208ms proc=1ms equity=298.50
      missing=["VAFA:SKIP",...] evidence=[]
      skip=[VWAP/L:not_in_value+cvd_slope_bearish]
```

**Problemas que NO se pueden detectar mirando este log:**

1. ¿El WebSocket de klines se desconectó y reconectó?
2. ¿El depth (orderbook) tiene datos frescos o está stale?
3. ¿El forceOrder stream está recibiendo eventos o está silencioso?
4. ¿El taker ratio llegó en este ciclo o usa datos viejos?
5. ¿Cuántos bars han pasado desde la última señal?
6. ¿El OI cambió o viene el mismo valor de hace 30 min?
7. ¿Hay algún stream desconectado que nadie notó?

---

## Campos del [bar] log — implementados ✅

### Grupo 1 — Freshness de cada stream

```
  depth_age=0ms        ✓
  trade_age=208ms      ✓
  liq_age=45s          ✓ cuándo llegó el último forceOrder event
  ls_age=4m32s         ✓ cuándo se fetched el último LS ratio
  taker_age=1m12s      ✓ cuándo se fetched el último taker ratio
  oi_age=4m58s         ✓ cuándo se fetched el último OI
  funding_age=32s      ✓ cuándo llegó el último funding tick
```

**Umbrales de alerta ([WARN] automático):**
```
liq_age    > 5min   → [WARN] forceOrder stream posiblemente caído
ls_age     > 10min  → [WARN] LS ratio fetch fallando
taker_age  > 5min   → [WARN] taker ratio fetch fallando
oi_age     > 10min  → [WARN] OI fetch fallando
funding_age > 2min  → [WARN] funding stream posiblemente caído
```

### Grupo 2 — Estado de cada WebSocket

```
  ws=kline:ok|disc depth:ok|disc trades:ok|disc liq:ok|disc|recon
```

### Grupo 3 — Contadores acumulados (en [metrics])

```
  bars_since_signal=47    cuántas barras sin señal
  signals_today=3         señales emitidas en este deploy
  liq_events_today=0      liquidaciones recibidas (0 = stream muerto o mercado quieto)
  ws_reconnects=2         reconexiones totales (0 es ideal)
```

### Grupo 4 — Calidad del dato institucional

```
inst=Live(6/6)    → todas las fuentes ok
inst=Live(4/6)    → 4 de 6 fuentes con datos frescos
inst=Partial(2/6) → mayoría fallando
inst=null         ✓
```

Las 6 fuentes: forceOrder liq · L/S top traders · L/S global · OI · taker ratio · funding rate histórico

---

## Implementación (código real en `crates/monitor/src/main.rs`)

### Structs implementados

```rust
// crates/monitor/src/main.rs

struct DataFreshness {
    liq_last_event_at:   Option<Instant>,
    liq_ws_connected_at: Option<Instant>,
    liq_stream_ok:       bool,
    ls_fetched_at:       Option<Instant>,
    taker_fetched_at:    Option<Instant>,
    oi_fetched_at:       Option<Instant>,
    funding_tick_at:     Option<Instant>,
}

// quality() → (sources_ok: u8, total: u8)
// threshold: 10 min. liq requiere stream conectado + al menos un raw message.

enum StreamHealth { Unknown, Ok, Reconnecting, Disc }

struct StreamStates {
    klines: StreamHealth,
    depth:  StreamHealth,
    liq:    StreamHealth,
}

struct Metrics {
    bars_since_signal:      u64,
    signals_today:          u64,
    liq_events_today:       u64,
    liq_raw_messages:       u64,
    liq_global_raw_messages: u64,
    ws_reconnects:          u64,
}
```

**Formato del [bar] log (producción):**
```
[bar] ts=1779011100000 close=78112.69 regime=TrendUp slow=0.168 fast=0.223
      funding=0.2337 basis=-0.065% oi_delta=0 vwap=78074.33 cvd=858.4
      ob=live inst=Live(6/6) ls_top=49.9%/55.6% liq=0$ liq_age=3m12s
      ws=[kline:ok depth:ok liq:ok]
      action=Wait score=0.000 delivery=95ms proc=1ms equity=298.50
      skip=[DRR:NO_RANGE]
```

**Con problema de forceOrder stream:**
```
[bar] ts=... inst=Partial(5/6) liq=0$ liq_age=47m32s
      ws=[kline:ok depth:ok liq:disc]

[WARN] forceOrder stream disconnected for 47m — liq data unavailable
```

**Formato del [metrics] log (cada 60s):**
```
[metrics] bars=47 signals=3 liq_events=0 liq_raw=0 liq_global_raw=0 ws_reconnects=2 |
          inst=5/6 liq_age=3m12s liq_connected=ok ls_age=4m32s taker_age=1m12s oi_age=4m58s funding_age=32s |
          ...
```

---

## Qué va a ser visible que hoy es invisible

| Problema | Hoy | Con el fix |
|---------|-----|-----------|
| forceOrder caído | `liq=0$` (ambiguo) | `liq=0$ liq_age=47m ws=[liq:disc] [WARN]` |
| LS ratio fetch fallando | `ls_top=0%/0%` | `ls_top=0%/0% inst=Partial(4/6) [WARN]` |
| Funding stream caído | `funding=0.0` | `funding_age=15m ws=[funding:disc] [WARN]` |
| Kline WebSocket reconectando | invisible | `ws=[kline:recon] delivery=1200ms` |
| OI fetch fallando silencioso | `oi_delta=0` (ambiguo) | `oi_age=25m inst=Partial(5/6)` |
| Mercado sin liquidaciones vs stream muerto | imposible distinguir | `liq=0$ liq_age=3s` vs `liq=0$ liq_age=47m` |

---

## Por qué `liq_age` es el campo más importante

Con el fix del forceOrder WebSocket de hoy, el sistema ahora recibe
liquidaciones reales. Pero si mañana el stream se cae y nadie lo nota,
los logs van a seguir mostrando `liq=0$` — exactamente igual a cuando
no había ninguna liquidación real.

`liq_age` resuelve esta ambigüedad:
```
liq=0$ liq_age=3s    → el stream está activo, simplemente no hay liquidaciones ahora
liq=0$ liq_age=47m   → el stream lleva 47 minutos sin eventos → probablemente caído
liq=1250000$ liq_age=8s → liquidación real recibida hace 8 segundos ✓
```
