# Ejecutor real (testnet/live) — liquidity

Ejecución REAL de la estrategia ruteada (flow) sobre Bybit perps. Construido en
`crates/liquidity_monitor/src/{exec.rs, executor.rs}`, **env-gated** (default OFF → los
servicios de paper no se ven afectados salvo que se active explícitamente).

## Filosofía (V1)
- El **exchange gestiona** stop / take-profit / trailing (robusto ante caídas del proceso).
- Nosotros **orquestamos**: colocar entradas PostOnly en los niveles, detectar fills,
  registrar fill timing y R real. Un símbolo por proceso (= un PaperBook).
- Lifecycle: `Idle → Resting` (PostOnly en cada nivel flow) `→ InPos` (stop+tp / trailing
  exchange-side) `→ Idle` (+cooldown). Reconciliación cada ~8s.

## Mapeo de la gestión
- **Fade** (chop): `set_trading_stop` con `stopLoss=stop` + `takeProfit=tp2` (estructural).
- **Trail** (tendencia): `set_trading_stop` con `trailingStop = TRAIL_ATR(6)·ATR` (nativo).
- Entrada: **PostOnly limit** (maker garantizado — si cruzaría, se rechaza, igual que el edge).

## ⚠️ Limitaciones V1 (a refinar en V2)
1. **Sin parcial 50%@tp1 → BE.** V1 usa stop+tp2 directo. (El parcial es V2; en backtest
   protege DD/SOL, pero V1 prioriza validar fills/timing reales primero.)
2. **One-way mode:** se colocan entradas long y short a la vez; en el primer fill se cancelan
   las demás. Ventana de riesgo = 1 ciclo de poll (~8s) si llenan ambas. Aceptable en testnet.
3. **qty/tick por símbolo** hardcodeados (BTC 0.001/1dec, ETH 0.01/2, SOL 0.1/3); override por env.

## ⚠️ DEMO vs TESTNET — usar DEMO
El monitor lee **datos REALES** (stream.bybit.com mainnet) para señales/niveles. **Testnet tiene su
propio mercado falso/ilíquido** (precios disparatados) → poner órdenes a niveles reales sobre ese
mercado NO valida nada. **Bybit Demo Trading** (`api-demo.bybit.com`) ejecuta contra el **mercado
REAL** (precios/libro reales) con **plata virtual** → fills/timing/slippage realistas. **Usar `EXEC_MODE=demo`.**

## Setup (DEMO — recomendado)
1. **Crear la tabla:** correr `migrations/liquidity_exec_trades.sql` en el SQL editor de Supabase.
2. **Activar Demo Trading:** en bybit.com (cuenta real) → menú de cuenta → **Demo Trading** → te da
   fondos virtuales al instante. Dentro de Demo: **API → crear key** con permisos *Orders + Positions*
   (Unified Trading). Esas son keys de DEMO (distintas de las de mainnet/testnet).
3. **Env vars:**
   ```
   EXEC_MODE=demo             # off (default) | demo | testnet | live
   BYBIT_API_KEY=...          # key de TESTNET
   BYBIT_API_SECRET=...
   SYMBOL=BTCUSDT
   TF=15
   HIGH_VOL_ONLY=true
   SYSTEM=flow                # el paper sigue su lógica; el ejecutor siempre usa flow
   SUPABASE_URL=...           # mismo proyecto
   SUPABASE_KEY=...
   # opcionales:
   EXEC_QTY=0.001             # tamaño base (default por símbolo)
   EXEC_LEVERAGE=1
   EXEC_PX_DEC=1              # decimales de precio (default por símbolo)
   ```

## Cómo correr (local) — vía `.env`
El binario carga **`.env`** de la raíz del repo automáticamente (dotenvy). Poné ahí las vars
(las de Bybit + EXEC_MODE=demo; Supabase ya está). El `.env` está en `.gitignore` → no se sube.
```
# 1) verificar claves (lee .env)
python backtest/_testnet_check.py
# 2) correr el ejecutor (lee .env de la raíz aunque corras desde el crate)
cd crates/liquidity_monitor && cargo run --release
```
Logs esperados: `[exec] ejecutor LISTO ... base=https://api-demo.bybit.com`, `colocadas N entradas
PostOnly`, `FILL ... ttf=Ns`, `CLOSED ... R=...`. Los trades caen en `liquidity_exec_trades`.

Para Railway: las env van en el dashboard (no `.env`). OFF por default → sin `EXEC_MODE` no pasa nada.

## Qué validar
- **Fill ratio / timing real** (`time_to_fill_s`) vs lo que asume el paper.
- **Slippage**: entry real vs nivel; exit real vs stop/tp.
- **avgR real** vs backtest optimizado.
- Que el lifecycle no deje órdenes huérfanas (reconciliación cancel_all en cierre).
