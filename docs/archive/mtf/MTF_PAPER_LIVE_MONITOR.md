# MTF Paper Live Monitor

Objetivo: correr paper live sin mezclar reglas de mercado. El monitor ahora separa:

- venue/exchange: de donde salen streams y REST
- market kind: spot vs linear futures
- strategy profile: que familias de senales pueden emitir eventos
- persistence: cada familia escribe en su tabla correcta

## Estado 2026-06-17

Ya existe la base para correr MTF Spot en paper live:

- `MONITOR_EXCHANGE` selecciona el venue (`bybit_spot`, `binance_linear`, etc.).
- `MONITOR_PROFILE` selecciona el perfil (`mtf_spot_paper`, `mtf_futures_paper`, `mtf_all_paper`, `off`).
- `MONITOR_STRATEGIES` permite seleccion explicita por estrategia.
- El runtime bloquea detectores incompatibles con el exchange. Spot no corre futures; futures no corre spot.
- `MtfSpotState` implementa Shorts v4 y Longs v1 en Rust.
- Spot escribe en `mtf_spot_trades`.
- Futures sigue escribiendo en `mtf_trades`.
- El monitor restaura trades abiertos despues de redeploy y puede resolver TP/SL durante warm-up.

Esto prepara paper live. Todavia no autoriza trading real: falta una prueba de paridad contra los backtests Python.

## Variables principales

```bash
MONITOR_EXCHANGE=bybit_spot
MONITOR_PROFILE=mtf_spot_paper
SYMBOLS=BTCUSDT
TIMEFRAME_MIN=1
```

`MONITOR_EXCHANGE` soporta:

- `binance_linear`
- `binance_spot`
- `bybit_spot`
- `bybit_linear`
- `okx_spot`
- `okx_linear`
- `hyperliquid_spot`
- `hyperliquid_linear`

`MONITOR_PROFILE` soporta:

- `mtf_futures_paper`: habilita detectores MTF futures.
- `mtf_spot_paper`: habilita MTF spot y bloquea detectores futures.
- `mtf_all_paper`: acepta ambos grupos en config, pero el runtime desactiva el grupo incompatible con el exchange seleccionado.
- `off`: no habilita MTF.

Seleccion explicita:

```bash
MONITOR_STRATEGIES=mtf_futures_shorts,mtf_futures_longs
MONITOR_STRATEGIES=mtf_spot_shorts,mtf_spot_longs
```

## Presets recomendados

Bybit spot BTC paper:

```bash
MONITOR_EXCHANGE=bybit_spot
MONITOR_PROFILE=mtf_spot_paper
SYMBOLS=BTCUSDT
TIMEFRAME_MIN=1
```

Binance futures paper:

```bash
MONITOR_EXCHANGE=binance_linear
MONITOR_PROFILE=mtf_futures_paper
SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT
TIMEFRAME_MIN=1
```

OKX futures research:

```bash
MONITOR_EXCHANGE=okx_linear
MONITOR_PROFILE=mtf_futures_paper
SYMBOLS=BTCUSDT
TIMEFRAME_MIN=1
```

Hyperliquid research:

```bash
MONITOR_EXCHANGE=hyperliquid_linear
MONITOR_PROFILE=mtf_futures_paper
SYMBOLS=BTCUSDT
TIMEFRAME_MIN=1
```

## Arquitectura implementada

### Config runtime

Archivo: `crates/monitor/src/monitor_config.rs`

- Lee `MONITOR_PROFILE`.
- Lee `MONITOR_STRATEGIES`.
- Si no se define perfil, usa `mtf_spot_paper` para exchange spot y `mtf_futures_paper` para futures.
- Expone flags internos:
  - `mtf_futures_shorts`
  - `mtf_futures_longs`
  - `mtf_spot_shorts`
  - `mtf_spot_longs`

### Exchange config

Archivo: `crates/monitor/src/exchange_config.rs`

- Agrega soporte spot/linear para Binance, Bybit, OKX y Hyperliquid.
- Expone `is_spot()`.
- `klines_url()` y `spot_price_url()` devuelven `Option<String>` porque algunos venues no tienen el mismo REST warm-up.

### Detector Spot

Archivo: `data/src/strategy/detectors/mtf_spot_detector.rs`

- Implementa Shorts v4 y Longs v1.
- Mantiene estado H1, ATR14 H1, PDH/PDL, Asia high/low y rolling weekly high/low.
- Usa VAH para shorts y VAL para longs.
- Un trade abierto a la vez por simbolo.
- `restore_active_trade()` permite recuperar posicion abierta despues de redeploy.

### Persistencia Spot

Archivo: `crates/monitor/src/supabase_writer.rs`

- `write_mtf_spot_trade()`
- `load_mtf_spot_active()`

Tabla:

```sql
-- migrations/mtf_spot_trades.sql
```

Spot no debe escribir en `mtf_trades` porque esa tabla historicamente pertenece a MTF Futures.

## Checklist antes de Railway

1. Ejecutar la migracion `migrations/mtf_spot_trades.sql` en Supabase.
2. Crear servicio separado en Railway, por ejemplo `monitor-spot-paper`.
3. Configurar env vars:

```bash
MONITOR_EXCHANGE=bybit_spot
MONITOR_PROFILE=mtf_spot_paper
SYMBOLS=BTCUSDT
TIMEFRAME_MIN=1
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
```

4. Revisar logs esperados:

```text
[mtf_spot] SIGNAL ...
[mtf_spot] CLOSED ...
```

5. Confirmar que no aparecen senales futures en el servicio spot.
6. Confirmar que las filas nuevas caen en `mtf_spot_trades`.

## Parity harness pendiente

La pregunta critica es: "como sabemos que actuara igual al backtest?"

Respuesta actual: todavia no se debe asumir. Falta construir y correr un harness que compare:

- `backtest/mtf_spot_backtest.py` vs `MtfSpotState` Shorts v4
- `backtest/mtf_spot_longs_backtest.py` vs `MtfSpotState` Longs v1
- misma data M1
- mismos VAH/VAL
- mismos stops
- mismas salidas target/CVD/stop; MTF Spot no usa timeout

Criterio de aceptacion:

- mismo numero de trades
- mismas entradas por timestamp
- misma direccion
- mismo stop/target dentro de tolerancia numerica pequena
- mismo resultado o diferencia explicada con log

Hasta que eso pase, el estado es **paper live / observacion**.
