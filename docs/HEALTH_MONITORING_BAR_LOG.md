# Plan — Health Monitoring Completo del Bar Log
## FlowSurface Monitor

**Fecha:** Mayo 2026  
**Objetivo:** Hacer que el [bar] log sea suficientemente completo para diagnosticar
cualquier problema de WebSocket, ingesta de datos, o pipeline institucional
sin necesidad de grep adicional.

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

## Los campos a agregar al [bar] log

### Grupo 1 — Freshness de cada stream (crítico)

Cada fuente de datos debe mostrar cuántos segundos tiene el dato más reciente.
Si un stream se cae, el age sube y se vuelve visible en los logs.

```
Campos nuevos en el [bar] log:
  depth_age=0ms        ← ya existe ✓
  trade_age=208ms      ← ya existe ✓
  liq_age=45s          ← NUEVO: cuándo llegó el último forceOrder event
  ls_age=4m32s         ← NUEVO: cuándo se fetched el último LS ratio
  taker_age=1m12s      ← NUEVO: cuándo se fetched el último taker ratio
  oi_age=4m58s         ← NUEVO: cuándo se fetched el último OI
  funding_age=32s      ← NUEVO: cuándo llegó el último funding tick
```

**Umbrales de alerta (agregar [WARN] automático si se supera):**
```
liq_age    > 5min   → [WARN] forceOrder stream posiblemente caído
ls_age     > 10min  → [WARN] LS ratio fetch fallando
taker_age  > 5min   → [WARN] taker ratio fetch fallando  
oi_age     > 10min  → [WARN] OI fetch fallando
funding_age > 2min  → [WARN] funding stream posiblemente caído
```

### Grupo 2 — Estado de cada WebSocket

```
Campos nuevos:
  ws=kline:ok|disc depth:ok|disc trades:ok|disc funding:ok|disc liq:ok|disc
```

Cada stream tiene estado `ok` o `disc` (disconnected). Si está en reconexión
activa, mostrar `recon`.

Ejemplo con problema:
```
ws=kline:ok depth:ok trades:ok funding:disc liq:recon
```

### Grupo 3 — Contadores acumulados (para detectar gaps)

```
Campos nuevos en [metrics] (no en [bar] para no saturar):
  bars_since_signal=47    ← cuántas barras sin señal (útil para debug)
  signals_today=3         ← señales emitidas en este deploy
  liq_events_today=0      ← liquidaciones recibidas (0 = stream muerto o mercado quieto)
  ws_reconnects=2         ← cuántas reconexiones hubo (0 es ideal)
```

### Grupo 4 — Calidad del dato institucional

Cuando `inst=Live` pero los datos son todos 0 o null, el log no dice nada.
Agregar un indicador de calidad:

```
inst=Live(4/6)   ← 4 de 6 fuentes institucionales tienen datos frescos
inst=Live(6/6)   ← todas las fuentes ok
inst=Partial(2/6) ← mayoría fallando, datos parciales
inst=null        ← ya existe ✓
```

Las 6 fuentes:
1. forceOrder liquidaciones
2. Long/Short top traders ratio
3. Global long/short ratio
4. OI histórico
5. Taker ratio
6. Funding rate histórico (percentil)

---

## Implementación

### Paso 1 — Agregar timestamps de última actualización a cada tracker

En el `InstitutionalContext` o en `BarState`, guardar cuándo se actualizó
cada fuente por última vez:

```rust
// En data/src/institutional/context_builder.rs o en monitor/src/main.rs
pub struct DataFreshness {
    pub liq_last_event_ms:   Option<i64>,   // último forceOrder recibido
    pub ls_ratio_fetched_ms: Option<i64>,   // último fetch de LS ratio
    pub taker_fetched_ms:    Option<i64>,   // último fetch de taker ratio
    pub oi_fetched_ms:       Option<i64>,   // último fetch de OI
    pub funding_tick_ms:     Option<i64>,   // último tick de funding
}

impl DataFreshness {
    pub fn liq_age_secs(&self, now_ms: i64) -> Option<u64> {
        self.liq_last_event_ms.map(|t| ((now_ms - t) / 1000) as u64)
    }
    // ... etc para cada campo
    
    pub fn institutional_quality(&self, now_ms: i64) -> (u8, u8) {
        // retorna (fuentes_ok, total_fuentes)
        let mut ok = 0u8;
        let total = 6u8;
        let threshold_ms = 10 * 60 * 1000; // 10 minutos
        
        if self.liq_last_event_ms.map(|t| now_ms - t < threshold_ms).unwrap_or(false) { ok += 1; }
        if self.ls_ratio_fetched_ms.map(|t| now_ms - t < threshold_ms).unwrap_or(false) { ok += 1; }
        // ... etc
        
        (ok, total)
    }
}
```

### Paso 2 — Actualizar los timestamps en cada fetch/event

```rust
// En el task de LS ratio:
loop {
    interval.tick().await;
    let top = fetch_top_trader_ls(&ls_symbol).await;
    let global = fetch_global_ls(&ls_symbol).await;
    let now_ms = chrono::Utc::now().timestamp_millis();
    let _ = ls_tx.send((top, global, now_ms)).await;  // incluir timestamp
}

// En el handler de forceOrder WebSocket:
// Cuando llega un evento, actualizar freshness.liq_last_event_ms = now_ms
```

### Paso 3 — Estado de WebSocket streams

En `BarState`, agregar un enum de estado por stream:

```rust
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum StreamHealth {
    Ok,
    Reconnecting,
    Disconnected,
}

pub struct StreamStates {
    pub klines:   StreamHealth,
    pub depth:    StreamHealth,
    pub trades:   StreamHealth,
    pub funding:  StreamHealth,
    pub liq:      StreamHealth,
}
```

Actualizar cuando el stream se conecta, desconecta, o inicia reconexión.

### Paso 4 — Nuevo formato del [bar] log

```rust
// En on_bar_close, armar la línea de health:
let (inst_ok, inst_total) = freshness.institutional_quality(now_ms);
let inst_quality = if inst_ok == inst_total {
    format!("Live({inst_ok}/{inst_total})")
} else if inst_ok > 0 {
    format!("Partial({inst_ok}/{inst_total})")
} else {
    "null".to_string()
};

let liq_age = freshness.liq_age_secs(now_ms)
    .map(|s| format!("{s}s"))
    .unwrap_or("never".to_string());

let ws_health = format!(
    "kline:{} depth:{} trades:{} funding:{} liq:{}",
    fmt_health(streams.klines),
    fmt_health(streams.depth),
    fmt_health(streams.trades),
    fmt_health(streams.funding),
    fmt_health(streams.liq),
);

eprintln!(
    "[bar] ts={ts} close={close:.2} regime={regime} \
     slow={slow:.3} fast={fast:.3} \
     funding={funding:.4} basis={basis:.3}% oi_delta={oi_delta} \
     vwap={vwap:.2} cvd={cvd:.1} \
     ob={ob_status} inst={inst_quality} \
     ls_top={ls_top:.1}%/{ls_bot:.1}% liq={liq}$ liq_age={liq_age} \
     ws=[{ws_health}] \
     action={action} score={score:.3} \
     delivery={delivery}ms proc={proc}ms \
     equity={equity:.2} \
     missing={missing:?} skip={skip}",
    // ... params
);
```

**Resultado final del [bar] log:**
```
[bar] ts=1779011100000 close=78112.69 regime=TrendUp slow=0.168 fast=0.223
      funding=0.2337 basis=-0.065% oi_delta=0 vwap=78074.33 cvd=858.4
      ob=live inst=Live(6/6) ls_top=49.9%/55.6% liq=0$ liq_age=3m12s
      ws=[kline:ok depth:ok trades:ok funding:ok liq:ok]
      action=Wait score=0.000 delivery=95ms proc=1ms equity=298.50
      skip=[VWAP/L:not_in_value+cvd_slope_bearish]
```

**Con problema de forceOrder stream:**
```
[bar] ts=... inst=Partial(5/6) liq=0$ liq_age=47m32s
      ws=[kline:ok depth:ok trades:ok funding:ok liq:disc]
      
[WARN] forceOrder stream disconnected for 47m — liq data unavailable
```

### Paso 5 — [WARN] automáticos en el [metrics] log

```rust
// En el metrics loop (cada 60s):
if let Some(age) = freshness.liq_age_secs(now_ms) {
    if age > 300 {  // 5 minutos
        eprintln!("[WARN] forceOrder stream: last event {}s ago — possibly disconnected", age);
    }
}
if let Some(age) = freshness.ls_age_secs(now_ms) {
    if age > 600 {  // 10 minutos
        eprintln!("[WARN] LS ratio fetch: last update {}s ago — fetch may be failing", age);
    }
}
// ... etc para cada fuente
```

---

## Orden de implementación

```
Día 1 — Base (DataFreshness + timestamps en cada fetch):
  [ ] Agregar struct DataFreshness a BarState
  [ ] Actualizar timestamp en cada task de fetch/stream
  [ ] liq_age en el [bar] log
  [ ] inst=Live(N/M) en el [bar] log

Día 2 — Stream health + warnings:
  [ ] Agregar enum StreamHealth y StreamStates
  [ ] ws=[...] en el [bar] log
  [ ] [WARN] automáticos en [metrics] cuando age supera umbral

Día 3 — Contadores en [metrics]:
  [ ] bars_since_signal
  [ ] liq_events_today
  [ ] ws_reconnects
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
