# Guía: Deploy en Railway

---

## Configuración del proyecto

**Archivo `railway.toml`**:
```toml
[build]
builder = "dockerfile"
dockerfilePath = "Dockerfile"

[deploy]
startCommand = "monitor"
restartPolicyType = "always"
region = "asia-southeast1"
```

El monitor corre en `asia-southeast1` (Singapur) para minimizar latencia a los WebSockets de Binance.

---

## Variables de entorno requeridas

Configurar en Railway Dashboard → Settings → Variables:

| Variable | Valor | Descripción |
|----------|-------|-------------|
| `SUPABASE_URL` | `https://{ref}.supabase.co` | URL del proyecto Supabase |
| `SUPABASE_KEY` | `eyJ...` (service_role) | Key que bypasea RLS |
| `SYMBOL` | `BTCUSDT` | Par a monitorear |
| `TIMEFRAME_MIN` | `5` | Timeframe en minutos |
| `PAPER_INITIAL_CAPITAL` | `3000.0` | Capital inicial |
| `PAPER_RISK_PCT` | `0.01` | Riesgo por trade (1%) |
| `PAPER_MAX_POSITIONS` | `1` | Máx posiciones simultáneas |
| `PAPER_SLIPPAGE_BPS` | `1.0` | Slippage por lado |
| `PAPER_TAKER_FEE` | `0.0004` | Fee taker Binance |
| `PAPER_FUNDING_RATE` | `0.0001` | Funding rate asumido |
| `PAPER_LEVERAGE` | `1.0` | Leverage (sin apalancamiento) |

> `MONGODB_URI` puede eliminarse — ya no se usa desde la migración a Supabase.

---

## Proceso de deploy

### Deploy inicial (o después de cambios)

```bash
# 1. Verificar que el build local pasa
cargo build -p monitor

# 2. Push a la rama main de GitHub
git push origin main
```

Railway detecta el push automáticamente, construye el Dockerfile multi-stage y despliega el binario.

### Forzar redeploy (sin cambios de código)

Railway Dashboard → Deployments → botón "Redeploy".

### Ver logs en tiempo real

Railway Dashboard → Deployments → el deploy activo → pestaña "Logs".

```
# Filtrar por tipo de evento:
# stdout → señales y trades
# stderr → diagnóstico de barras y métricas
```

---

## Dockerfile multi-stage

El Dockerfile usa un builder stage con Rust y produce un binario final mínimo:

```dockerfile
# Stage 1: build
FROM rust:1.76 AS builder
WORKDIR /app
COPY . .
RUN cargo build -p monitor --release

# Stage 2: runtime (imagen mínima)
FROM debian:bookworm-slim
COPY --from=builder /app/target/release/monitor /usr/local/bin/monitor
CMD ["monitor"]
```

El binario final es ~8MB. Sin Rust ni dependencias de compilación en la imagen de producción.

---

## Monitorear el estado

### Logs de barras (stderr)

```
[bar] ts=1748000000000 close=95420.50 regime=TrendUp slow=0.312 fast=0.421 score=0.71 latency=12ms equity=3045.20
```

Ver cada 5 minutos — confirma que el WebSocket está activo y los bars se procesan.

### Métricas (cada 10 barras)

```
[metrics] bars=100 latency_avg=12ms max=45ms depth_age=1200ms trade_age=800ms
```

Si `depth_age > 5s` o `latency_avg > 500ms` → posible problema de conectividad.

### Señales emitidas (stdout)

```json
{"event":"signal","data":{"strategy":"LvnLiquidityVacuumBreakout","side":"Long","score":0.73,"entry_price":95200}}
```

### Trades cerrados (stdout)

```json
{"event":"trade_closed","data":{"close_reason":"TARGET_HIT","r_multiple":1.82,"net_pnl":14.40}}
```

---

## Alertas de problemas

| Síntoma | Causa probable | Acción |
|---------|---------------|--------|
| No hay logs de bar en > 10 min | WebSocket caído o rate limit | Railway auto-restart por `restartPolicyType = "always"` |
| `[supabase] POST error 422` | Campo requerido faltante en el body | Revisar `supabase_writer.rs` — campo cambiado en el schema |
| `[supabase] POST error 401` | Service_role key incorrecta o expirada | Verificar variable `SUPABASE_KEY` en Railway |
| `[config] Using default config` | Supabase no tiene parámetros para el régimen actual | Normal al inicio — ejecutar `python monitor.py --force` para calibrar |
| Equity baja consistentemente | Estrategia perdedora en el régimen actual | Ejecutar `python monitor.py --force` para recalibrar |

---

## Actualizar parámetros sin redeploy

Los parámetros en `deployed_params` se recargan automáticamente cada 5 minutos o al cambio de régimen — **sin redeploy de Railway**.

Para actualizar parámetros manualmente:
1. `python monitor.py --force` (recalibra con datos actuales)
2. O editar `deployed_params` directamente en Supabase Dashboard → Table Editor
