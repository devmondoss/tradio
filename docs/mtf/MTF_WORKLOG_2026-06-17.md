# MTF Worklog - 2026-06-17

Resumen de lo avanzado hasta hoy en MTF Spot, UI local y preparacion de paper live.

---

## 1. Backtest MTF Spot

### Shorts v4

Archivo canonico:

```text
backtest/mtf_spot_backtest.py
```

Resultados actuales:

| Corte | Trades | WR | AvgR | TotalR | Equity |
|-------|--------|----|------|--------|--------|
| Full sample | 693 | 49.1% | +0.334R | +231.46R | $38,544.51 |
| OOS Mar-May 2026 | 204 | 52.0% | +0.409R | +83.49R | n/a |

Reglas principales:

- BTCUSDT Bybit Spot.
- Sesiones activas: Overlap/NY.
- VAH requerido.
- Niveles: PDH, Asian High, Weekly High, VAH.
- Rechazo con mecha superior 30-85% y cierre bajista.
- Flujo: `obi10_mean < -0.05` o `delta < 0`.
- Stop: `H1_high + 0.40 * ATR14_H1`.
- Stop valido: 0.30%-0.75%.
- Target: 2R.
- CVD exit desde 1R.

### Longs v1

Archivo canonico:

```text
backtest/mtf_spot_longs_backtest.py
```

Resultados actuales:

| Corte | Trades | WR | AvgR | TotalR | Equity |
|-------|--------|----|------|--------|--------|
| Full sample | 578 | 52.4% | +0.354R | +204.38R | $23,971.18 |
| OOS Mar-May 2026 | 161 | 54.7% | +0.403R | +64.91R | n/a |

Reglas principales:

- BTCUSDT Bybit Spot.
- Sesion activa: 14:00-20:00 UTC.
- VAL requerido.
- Niveles: PDL, Asian Low, Weekly Low, VAL.
- Rechazo con mecha inferior 30-85% y cierre alcista.
- Flujo: `obi10_mean > 0.05` o `delta > 0`.
- Stop: `H1_low - 0.40 * ATR14_H1`.
- Stop valido: 0.30%-0.75%.
- Target: 2R.
- CVD exit desde 1R.

---

## 2. UI local

Se actualizo el modulo MTF para revisar backtests desde la UI local:

- Selector Shorts/Longs.
- Soporte para `mtf_spot_shorts_btc`.
- Soporte para `mtf_spot_longs_btc`.
- Vista de trades con grafico.
- Estadisticas visibles por direccion.
- Graficos para investigar ganadores/perdedores por trade.

Archivos principales:

```text
apps/rbf-review/src/views/MTFModuleView.tsx
apps/rbf-review/src/views/BacktestView.tsx
```

Estado: funcional localmente para inspeccion visual.

---

## 3. Monitor adaptativo

Se preparo el monitor para no hardcodear una sola estrategia ni mezclar spot con futures.

Archivos principales:

```text
crates/monitor/src/monitor_config.rs
crates/monitor/src/exchange_config.rs
crates/monitor/src/main.rs
```

Variables:

```bash
MONITOR_EXCHANGE=bybit_spot
MONITOR_PROFILE=mtf_spot_paper
MONITOR_STRATEGIES=mtf_spot_shorts,mtf_spot_longs
```

Perfiles:

- `mtf_futures_paper`
- `mtf_spot_paper`
- `mtf_all_paper`
- `off`

Exchanges configurados:

- `binance_linear`
- `binance_spot`
- `bybit_spot`
- `bybit_linear`
- `okx_spot`
- `okx_linear`
- `hyperliquid_spot`
- `hyperliquid_linear`

Decision importante: el runtime desactiva automaticamente estrategias incompatibles con el exchange seleccionado.

---

## 4. Detector Rust MTF Spot

Archivo:

```text
data/src/strategy/detectors/mtf_spot_detector.rs
```

Implementado:

- `MtfSpotState`
- `MtfSpotDirection`
- Shorts v4
- Longs v1
- Warm-up historico
- Restauracion de trade abierto
- Un trade activo a la vez
- TP/SL/CVD/timeout
- Calculo de estado H1, ATR, PDH/PDL, Asia high/low y weekly high/low

El detector vive separado del MTF Futures detector para evitar mezclar reglas de mercado.

---

## 5. Supabase y persistencia

Nueva tabla:

```text
mtf_spot_trades
```

Migracion:

```text
migrations/mtf_spot_trades.sql
```

Funciones agregadas:

```text
write_mtf_spot_trade()
load_mtf_spot_active()
```

Motivo: Spot y Futures deben auditarse por separado. No queremos que MTF Spot contamine `mtf_trades`, que historicamente representa MTF Futures.

---

## 6. Railway

Cambios preparados:

- `railway.toml` ya no debe pisar el `CMD` del Dockerfile.
- Docker defaults apuntan a timeframe M1 y perfil futures por seguridad.
- Spot paper debe correr como servicio separado, por ejemplo `monitor-spot-paper`.

Preset recomendado:

```bash
MONITOR_EXCHANGE=bybit_spot
MONITOR_PROFILE=mtf_spot_paper
SYMBOLS=BTCUSDT
TIMEFRAME_MIN=1
```

Antes de desplegar:

1. Ejecutar `migrations/mtf_spot_trades.sql`.
2. Crear servicio Railway separado.
3. Confirmar env vars.
4. Revisar logs `[mtf_spot] SIGNAL` y `[mtf_spot] CLOSED`.
5. Confirmar filas en `mtf_spot_trades`.

---

## 7. Verificacion hecha

Comandos que pasaron:

```bash
cargo fmt --check --package monitor
cargo fmt --check --package flowsurface-data
```

Limitaciones locales:

- `cargo check -p monitor` no pudo completarse por toolchain local de Windows: falta linker MSVC (`link.exe`) / librerias del SDK.
- `npm run build` sigue bloqueado por errores TypeScript preexistentes en archivos no relacionados con MTF.

---

## 8. Pendiente critico

Paridad Python-vs-Rust ejecutada:

- Shorts v4: 693 trades Python / 693 trades Rust / 0 diferencias.
- Longs v1: 578 trades Python / 578 trades Rust / 0 diferencias.
- Comando: `python backtest/mtf_spot_parity.py --mode all --days 0`

Estado correcto hoy:

```text
Backtest validado + Rust live en paridad + UI local lista + monitor paper live preparado.
No trading real todavia: falta forward test paper.
```
