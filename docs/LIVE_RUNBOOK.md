# Live Trading Runbook — MTF Spot BTC/USDT

## FASE A: Testnet (HACER PRIMERO — sin dinero real)

### 1. Crear cuenta Bybit Testnet
- Ir a https://testnet.bybit.com → Create account
- Depositar USDT de testnet (botón "Get testnet funds" en el faucet)
- Ir a Account → API Management → Create New Key
- Permisos: **Spot Trading** (Read + Write). Sin Withdraw.
- Guardar API Key y Secret Key

### 2. Variables Railway (testnet)
```
MONITOR_EXCHANGE=bybit_spot
MONITOR_PROFILE=mtf_spot_live
SYMBOLS=BTCUSDT
TIMEFRAME_MIN=1
BYBIT_API_KEY=<testnet_api_key>
BYBIT_SECRET_KEY=<testnet_secret_key>
BYBIT_TESTNET=true
LIVE_INITIAL_CAPITAL=500.0
LIVE_RISK_PCT=0.02
LIVE_MAX_DAILY_LOSS_R=3.0
LIVE_MAX_DRAWDOWN_PCT=0.15
SLACK_WEBHOOK_URL=<your_slack_webhook>
SUPABASE_URL=<existing>
SUPABASE_KEY=<existing>
```

### 3. Deploy y monitoreo
- Deploy en Railway con las vars de testnet
- Verificar logs: `[live] ENTRY BTCUSDT ...` cuando dispare señal
- Verificar en Bybit Testnet → Spot → Orders que la orden apareció
- Verificar en Supabase `mtf_spot_trades` que `is_live=true` y `live_entry_order_id` está lleno
- Verificar en UI que el panel "BYBIT LIVE" aparece en el trade

### 4. Criterios de validación testnet
- [ ] 20+ trades ejecutados en testnet
- [ ] Fill prices dentro del 0.1% del precio de señal
- [ ] TP y SL orders colocadas correctamente
- [ ] Kill switch se activa correctamente (probar bajando `LIVE_MAX_DAILY_LOSS_R=0.1`)
- [ ] Alerts Slack llegan en cada open/close
- [ ] Reconciliación detecta exits de Bybit correctamente
- [ ] UI muestra order IDs y fill price

---

## FASE B: Live real (solo después de validar FASE A)

### 1. Crear API keys live
- Bybit.com → Account → API Management → Create New Key
- Permisos: **Spot Trading** (Read + Write). **Sin Withdraw.**
- IP whitelist: IP estática de Railway (ver en Railway → Settings → Networking)
- Guardar en Railway Secrets (no en .env)

### 2. Variables Railway (live)
Cambiar solo:
```
BYBIT_API_KEY=<live_api_key>
BYBIT_SECRET_KEY=<live_secret_key>
BYBIT_TESTNET=false
LIVE_INITIAL_CAPITAL=500.0   # ajustar al capital real depositado
```

### 3. Capital mínimo recomendado
- Mínimo: $200 USDT (para que el sizing funcione con BTCUSDT a ~$60K)
- Óptimo: $500+ (para sizing 2% = $10 de riesgo por trade)
- Con $500 y 2R promedio: ~$1 de comisión por trade (0.07% × 2 lados)

### 4. Checklist pre-live
- [ ] Testnet completado con ≥20 trades
- [ ] Kill switch configurado conservador: `LIVE_MAX_DAILY_LOSS_R=2.0`
- [ ] Slack webhook activo y testado
- [ ] Supabase tabla `mtf_spot_trades` con columnas live (ver migración abajo)
- [ ] USDT depositado en Bybit Spot wallet (no Derivatives)
- [ ] API key con IP whitelist de Railway

---

## Migración SQL — columnas live en Supabase

```sql
ALTER TABLE mtf_spot_trades
  ADD COLUMN IF NOT EXISTS is_live              BOOLEAN DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS live_entry_order_id  TEXT,
  ADD COLUMN IF NOT EXISTS live_fill_price      FLOAT8,
  ADD COLUMN IF NOT EXISTS live_filled_qty      FLOAT8,
  ADD COLUMN IF NOT EXISTS live_tp_order_id     TEXT,
  ADD COLUMN IF NOT EXISTS live_sl_order_id     TEXT;
```

Ejecutar en Supabase → SQL Editor antes de deploy live.

---

## Emergency procedures

### Kill switch manual
1. En Railway → Set `MONITOR_PROFILE=off` → Redeploy
2. Cancelar órdenes pendientes manualmente en Bybit → Spot → Orders → Cancel All
3. Cerrar posición abierta manualmente en Bybit si la hay

### Si el monitor se cae con posición abierta
1. Ir a Bybit Spot → Orders — el SL order sigue activo (Bybit lo mantiene)
2. El TP order también sigue activo
3. Al reiniciar el monitor, restaura el trade desde Supabase
4. Verificar que `is_open=true` en Supabase coincide con Bybit

### Drawdown de emergencia
Si PnL cae >15% del capital:
1. Monitor se para automáticamente (kill switch)
2. Revisar logs: `[live] KILL SWITCH — drawdown X%`
3. Para reactivar: ajustar `LIVE_MAX_DRAWDOWN_PCT` y redeploy
