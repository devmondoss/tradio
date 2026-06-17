# MTF Railway Services

Estado objetivo: dos servicios separados, mismo Dockerfile, distinto set de variables.

---

## monitor-futures

Uso: MTF Futures paper/live sobre Binance linear.

Variables:

```bash
MONITOR_EXCHANGE=binance_linear
MONITOR_PROFILE=mtf_futures_paper
MONITOR_STRATEGIES=mtf_futures_shorts,mtf_futures_longs
SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT
TIMEFRAME_MIN=1
SUPABASE_URL=...
SUPABASE_KEY=...
```

Tabla principal:

```text
mtf_trades
```

Preset versionable:

```text
railway.monitor-futures.env.example
```

---

## monitor-spot-paper

Uso: MTF Spot paper sobre Bybit spot.

Si el servicio se llama exactamente `monitor-spot-paper`, el monitor autodetecta:

```bash
MONITOR_EXCHANGE=bybit_spot
MONITOR_PROFILE=mtf_spot_paper
```

Con tus envs base actuales basta para arrancar Spot paper. Aunque `SYMBOLS` tenga los cinco simbolos, el runtime filtra MTF Spot a `BTCUSDT` porque la estrategia spot actual solo esta calibrada para BTC.

Variables:

```bash
MONITOR_EXCHANGE=bybit_spot
MONITOR_PROFILE=mtf_spot_paper
MONITOR_STRATEGIES=mtf_spot_shorts,mtf_spot_longs
SYMBOLS=BTCUSDT
TIMEFRAME_MIN=1
SUPABASE_URL=...
SUPABASE_KEY=...
```

Tabla principal:

```text
mtf_spot_trades
```

Preset versionable:

```text
railway.monitor-spot-paper.env.example
```

---

## Requisitos antes de levantar monitor-spot-paper

1. Ejecutar `migrations/mtf_spot_trades.sql` en Supabase.
2. Confirmar que la tabla `mtf_spot_trades` existe.
3. Confirmar que Realtime esta habilitado si se quiere ver la UI local actualizandose sin refrescar.
4. Desplegar `monitor-spot-paper` con las variables de este documento.
5. Buscar en logs:

```text
[mtf_spot] SIGNAL
[mtf_spot] CLOSED
```

6. Revisar la UI local en MTF -> Live -> Spot paper.
