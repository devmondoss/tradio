# Handoff — Estrategia de Provisión de Liquidez (BTCUSDT perp)
> Estado al cierre de sesión 2026-06-21. Para retomar en otra sesión sin perder contexto.
> Doc maestro de detalle: `LIQUIDITY_STRATEGY.md`. Cierre del catálogo: `CATALOG_CLOSURE_2026-06-20.md`.

## TL;DR — dónde estamos
- Reencuadre del proyecto: **el orderflow no predice dirección** (EDGE_VERDICT) → el único edge es
  **provisión de liquidez** (poner límites maker en niveles de volumen y cobrar la rotación).
- Estrategia construida, backtesteada (datos VERIFICADOS 365d), visualizable en `apps/trade-lab`, y
  **corriendo en paper 24/7** en Railway → escribiendo a Supabase.
- **Falta lo único que el backtest no puede zanjar: validar el fill ratio maker real** (en curso, paper).

## La estrategia (CONFIG FINAL CONGELADA)
- **Mercado/datos:** BTCUSDT perp Bybit · **era tick VERIFICADA 2025-06-19 → 2026-06 (~365d)**.
  IS<2026-03 / OOS≥2026-03. (El OHLCV existe desde 2025-01 pero el pre-tick NO se usa.)
- **3 componentes** (mismo principio = liquidez): **POC del order-block** (long/short) + **POC defendido** (long)
  + **mirror corto del POC defendido** (short, añadido 2026-06-21 → cartera balanceada 57/43 long/short).
- **TF decisión:** M15. **Entrada:** orden LÍMITE maker en el nivel (selección adversa 2 bps).
- **Filtro CLAVE:** volatilidad — solo opera con ATR > su mediana móvil(500). [sube avgR +0.13→+1.0]
- **Target:** ROTACIÓN al nivel de liquidez **LEJANO** (VAH/VAL/swing/PDH-PDL/**weekly**) ≈ **3.7% mediana**.
  Parcial 50% en el nivel cercano → stop a breakeven → el resto corre al lejano.
- **Salida:** evaluada en **M1** (honesto, sin ambigüedad intrabar). Timeout 24h. Riesgo FIJO $5/trade (1%, sin compounding).
- **Fee HONESTO:** maker 2bps/lado en entrada+tp1+target; **taker 5.5bps/lado en stop/BE/timeout** (88% de salidas a mercado).
- **Resultado OOS:** WR ~73% · avgR +0.89 · target mediana 3.7% · ~3-4 trades/día · $500→~$4.9k (365d, con mirror + fee honesto).

## ⚠️ Dudas abiertas / "lo que no cuadra" (revisar con cabeza fresca)
1. ✅ **RESUELTO (2026-06-21) — la duda "no cuadra"**: re-auditado. El edge NO es artefacto de
   stop-chico × RR — `corr(stopPct,R)=−0.09`, winners no tienen stop menor, sobrevive piso 0.50%
   (OOS +0.40), top-5 solo 11% del netR. Fee honesto (taker en salidas a mercado) cuesta −10%, edge
   aguanta. Peor caso apilado (fee+fills10bps+piso0.20%): OOS +0.47R, WR 70%. Ver §7c de LIQUIDITY_STRATEGY.
2. **El edge es maker-dependiente:** a TAKER es negativo. Todo descansa en conseguir fills maker.
   → **el fill ratio real es la pregunta que decide todo** (se está midiendo en paper).
3. **Concentración (fat tail):** top-5 trades = 21% del netR OOS. PERO avgR sin el top-5 = +0.81
   (sigue positivo) → no es pura lotería, pero hay dependencia de home runs.
4. **Perfil:** 57% de salidas en breakeven, ~11% home runs (la rotación), 26% stop. Los <1R NO son bug
   — son los parciales (sin ellos, WR cae a 22%). El sistema = muchos sencillos + pocos jonrones.
5. **Fidelidad live:** `live/levels.py` aproxima POC desde klines M15 (no footprint tick). El fill
   ratio (entrada) es fiel; el PnL paper es aproximado. Footprint real = mejora pendiente si valida.

## Infra live (Railway + Supabase) — CORRIENDO
- **Servicio Railway** = el mismo del monitor, ahora construye `Dockerfile.liquidity` (Python) vía
  `railway.toml`. El Rust (monitor MTF) quedó intacto en el repo pero NO se despliega (revertir =
  `dockerfilePath="Dockerfile"`). MTF/scalping/AMD = apagados (no se construyen).
- **Variables Railway:** SYMBOL=BTCUSDT · TF=15 · HIGH_VOL_ONLY=false · SUPABASE_URL/KEY (proyecto
  NUEVO `jubpovmsfvaqfnidozfh`; el viejo `ztdhvmci` quedó restringido por quota).
- **Supabase (proyecto nuevo):** migración `migrations/liquidity_paper.sql` corrida. Tablas:
  - `liquidity_paper_trades` (trades cerrados)
  - `liquidity_paper_snapshots` (estado por vela — contadores en-memoria, 'desde restart')
  - `liquidity_paper_events` (place/fill — **fuente de verdad, restart-proof**)
  - vistas: `liquidity_paper_latest`, `liquidity_paper_fill_ratio`
- **Confirmado escribiendo** (eventos place/fill entrando, fill_ratio view funcionando).
- **Qué leer para validar:** `select * from liquidity_paper_fill_ratio;` (fill ratio acumulado por
  régimen). CLAVE: ¿en VOL-HIGH los fills empeoran (selección adversa)? — aún sin datos high (mercado calmo).

## Mapa de código
| Archivo | Qué hace |
|---|---|
| `backtest/_listas2.py` | Motor: `gen_h5`/`gen_h21` (POC), `struct_target` (target lejano), `run_level_m1exit` (salida M1) |
| `backtest/liquidity_app_backtest.py` | Backtest para la app (emite trades con tp1/targetName/levels) |
| `backtest/_consolidated.py`, `_filters.py` | Cartera consolidada + ablación de filtros |
| `backtest/_build_tickfeats.py` | Columnas tick (footprint/VP) para H8/H10/H12 (ya cerrados) |
| `live/levels.py` | Cálculo de niveles en vivo (paridad con backtest) |
| `live/paper_liquidity.py` | Harness paper: WS Bybit, órdenes virtuales, fill ratio por vol, Supabase, health server |
| `apps/trade-lab/` | App visual (tab único liquidez); `LocalResultsView.tsx`, `TradeChart.tsx` |
| `migrations/liquidity_paper.sql` | Tablas Supabase |

## Pendientes (priorizados para la próxima sesión)
1. ✅ **Re-auditar el edge** (hecho 2026-06-21): duda refutada, fee honesto cableado. Ver §7c.
2. ✅ **Mirror corto del POC defendido** (hecho 2026-06-21): backtest (`gen_h21_short`) + live
   (`live/levels.py` → `poc_defendido_short`). Cartera balanceada 57/43. Ver §7b.
3. 🔴 **REINICIAR el servicio Railway del paper** para que tome el código nuevo (mirror + fee honesto).
   El proceso vivo corre el código viejo (solo longs, fee flat). Sin restart no coloca shorts.
4. **Leer el fill ratio del paper** (días de datos, esp. VOL-HIGH) → decide si es desplegable.
5. **Footprint real desde ticks en live** (cerrar gap de aproximación) — solo si valida.
6. Si valida fills → **portar a Rust** (motor de producción con ejecución real + kill-switch + Slack).

## Cómo retomar
- Leer este doc + `LIQUIDITY_STRATEGY.md`.
- Backtest: `python backtest/liquidity_app_backtest.py --days 540 --json` (365d verificados).
- Visual: `cd apps/trade-lab && npm run dev` (tab Liquidez auto-corre).
- Paper vivo: `select * from liquidity_paper_fill_ratio;` en Supabase (proyecto nuevo).
