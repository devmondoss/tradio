# MTF Spot Completion Status

Fecha: 2026-06-17

## Terminado en codigo

- MTF Spot Shorts v4 portado a Rust en `data/src/strategy/detectors/mtf_spot_detector.rs`.
- MTF Spot Longs v1 portado a Rust en el mismo detector spot.
- Detector spot separado del detector futures.
- Monitor adaptativo con `MONITOR_EXCHANGE`, `MONITOR_PROFILE` y `MONITOR_STRATEGIES`.
- Autodeteccion Railway: si el servicio se llama `monitor-spot-paper`, usa Bybit Spot + MTF Spot paper aunque no existan envs `MONITOR_*`.
- Filtro de simbolos: MTF Spot usa solo `BTCUSDT` aunque `SYMBOLS` traiga los cinco simbolos del monitor futures.
- Persistencia spot separada en `mtf_spot_trades`.
- Writer/restorer Supabase para trades spot.
- UI local MTF Live con selector `Spot paper` / `Futures`.
- UI local MTF Backtest con selector Shorts / Longs.
- Harness de paridad Python-vs-Rust:
  - `backtest/mtf_spot_parity.py`
  - `crates/monitor/src/bin/mtf_spot_parity.rs`
- Paridad completa ejecutada:
  - Shorts v4: Python 693 trades / Rust 693 trades / 0 diferencias
  - Longs v1: Python 578 trades / Rust 578 trades / 0 diferencias
- Presets Railway:
  - `railway.monitor-futures.env.example`
  - `railway.monitor-spot-paper.env.example`
- Documentacion operativa Railway:
  - `docs/mtf/MTF_RAILWAY_SERVICES.md`

## Verificacion local ejecutada

Pasaron:

```bash
cargo fmt --check --package monitor --package flowsurface-data
python -m py_compile backtest/mtf_spot_parity.py backtest/mtf_spot_backtest.py backtest/mtf_spot_longs_backtest.py
python backtest/mtf_spot_parity.py --mode all --days 0
```

Tambien se ejecuto `npx tsc -b --pretty false`. El cambio MTF ya no agrega errores. El build completo sigue bloqueado por errores existentes fuera del cambio MTF:

- `src/components/TradeChart.tsx`
- `src/views/ChartView.tsx`
- `src/views/RBFModuleView.tsx`
- `src/views/StrategyModuleView.tsx`

## Bloqueos externos reales

Estos no se pueden completar desde este entorno sin credenciales/herramientas externas:

1. Supabase: resuelto.
   - `mtf_spot_trades` responde 200 por PostgREST.

2. Railway: CLI instalado pero no autenticado/linkeado.
   - `railway whoami` devuelve `Unauthorized`.
   - `railway status` devuelve `No linked project found`.
   - Falta `railway login` y `railway link` antes de crear/configurar servicios.

3. Parity runtime local: resuelto usando toolchain GNU portable.
   - El harness fuerza `cargo +1.95.0-x86_64-pc-windows-gnu`.
   - Paridad completa pasa para Shorts y Longs.

## Comandos finales cuando las credenciales esten disponibles

Supabase:

```text
OK - mtf_spot_trades existe y responde por PostgREST.
```

Railway:

```bash
railway login
railway link
# crear o seleccionar servicio monitor-spot-paper
# aplicar variables de railway.monitor-spot-paper.env.example
```

Paridad:

```bash
python backtest/mtf_spot_parity.py --mode all --days 0
```

Estado 2026-06-17: OK.

UI:

```bash
cd apps/rbf-review
npm run dev
```

Abrir MTF -> Live -> Spot paper.
