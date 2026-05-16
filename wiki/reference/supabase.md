# Referencia: Supabase

Schema: `supabase/schema.sql`

---

## Tablas

### `shadow_signals` — señales detectadas

Cada señal que pasa el gate de score mínimo genera una fila aquí (escrita por `SupabaseWriter::write_signal`).

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `id` | UUID PK | Clave primaria |
| `timestamp_ms` | BIGINT | Timestamp del bar-close que generó la señal |
| `strategy` | TEXT | Nombre del detector (ej: `"ValueAreaFailedAuction"`) |
| `side` | TEXT | `"Long"` o `"Short"` |
| `regime_slow` / `regime_fast` / `regime_combined` | TEXT | Régimen en el momento |
| `action` | TEXT | `"ShadowSignal"`, `"Wait"`, `"Blocked"` |
| `entry_price` / `stop_price` / `target_price` | FLOAT | Niveles de la señal |
| `score` | FLOAT | Score 0.0–1.0 del scorer |
| `ttl_ms` | BIGINT | TTL en ms |
| `evidence` | TEXT[] | Condiciones cumplidas |
| `missing` | TEXT[] | Condiciones faltantes o razón de bloqueo |
| `price` | FLOAT | Precio al momento del bar-close |
| `vwap_session` / `poc` / `vah` / `val` | FLOAT | Contexto de VWAP y VP |
| `cvd_slope` / `cvd_absolute` / `atr` / `spread_bps` / `obi_l5` | FLOAT | Contexto de flujo |
| `short_liq_usd_5m` / `long_liq_usd_5m` / `total_liq_usd_5m` | FLOAT | Liquidaciones |
| `cascade_active` | BOOLEAN | Si hay cascade en curso |
| `liq_dominant_side` | TEXT | Lado dominante de liquidaciones |
| `top_traders_long_pct` / `retail_long_pct` / `ls_divergence` | FLOAT | L/S ratios |
| `oi_current_btc` / `oi_change_30m_pct` / `oi_change_2h_pct` / `oi_trend` | FLOAT/TEXT | OI |
| `funding_current` / `funding_regime` / `funding_percentile_30d` / `funding_avg_7d` | FLOAT/TEXT | Funding |
| `taker_buy_sell_ratio` / `taker_imbalance` | FLOAT | Taker flow |

---

### `signal_outcomes` — trades cerrados

Un row por cada posición cerrada (escrito por `SupabaseWriter::write_trade`). Referencia a `shadow_signals(id)` con `ON DELETE CASCADE`.

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `signal_id` | UUID FK | Señal que generó este trade |
| `timestamp_ms` | BIGINT | Timestamp del cierre |
| `close_reason` | TEXT | `STOP_HIT`, `TARGET_HIT`, `TTL_EXPIRED`, `INVALIDATED` |
| `close_price` | FLOAT | Precio de cierre (con slippage) |
| `duration_ms` | BIGINT | Duración del trade en ms |
| `r_multiple` | FLOAT | `(exit - entry) × side / |entry - stop|` |
| `pnl_gross_usd` / `pnl_net_usd` | FLOAT | PnL bruto y neto |
| `fee_entry_usd` / `fee_exit_usd` / `slippage_usd` / `funding_cost_usd` | FLOAT | Costos |
| `mfe_r` / `mae_r` | FLOAT | Max Favorable / Adverse Excursion en R |
| `price_5m` / `price_15m` / `price_30m` / `price_1h` / `price_4h` | FLOAT | Precio posterior (futuro) |
| `r_5m` / `r_15m` / `r_30m` / `r_1h` / `r_4h` | FLOAT | R a distintos horizontes |

---

### `institutional_snapshots` — snapshots cada 5 minutos

Estado institucional completo en cada bar-close (solo cuando `institutional.is_some()`).

Campos: mismas métricas institucionales que `shadow_signals` + `regime_slow` / `regime_fast`.

---

### `regime_history` — cambios de régimen

Un row cada vez que el régimen cambia.

| Campo | Descripción |
|-------|-------------|
| `timestamp_ms` | Momento del cambio |
| `regime_slow` / `regime_fast` / `regime_combined` | Regímenes |
| `duration_ms` | Duración del régimen anterior |
| `price_at_change` | Precio en el momento del cambio |

---

### `calibration_log` — historial de calibraciones

Un row por cada ejecución del pipeline de calibración (tanto aprobadas como rechazadas).

| Campo | Descripción |
|-------|-------------|
| `trigger_reason` | `REGIME_CHANGE`, `PERFORMANCE_DEGRADATION`, `SCHEDULED_RECALIBRATION`, `FORCED` |
| `regime` | Régimen para el que se calibró |
| `params` | JSONB con los parámetros calibrados |
| `train_expectancy` / `test_expectancy` | Expectancy en R |
| `overfit_gap` | `train_exp - test_exp` |
| `n_train_trades` / `n_test_trades` | Cantidad de trades en cada set |
| `deflated_sharpe` | DSR (0–1) |
| `edge_is_real` | Si DSR ≥ 0.95 |
| `approved_for_deploy` | Si se desplegó a `deployed_params` |
| `deploy_reason` | Razón de aprobación o rechazo |
| `deployed_at` | Timestamp de deploy (si aprobado) |

---

### `deployed_params` — parámetros activos por régimen

Fuente de verdad que lee el monitor Rust para `StrategyConfig` dinámico.

| Campo | Descripción |
|-------|-------------|
| `regime` | Régimen para el que aplican |
| `strategy` | NULL = parámetros globales; si aplican a un detector específico: nombre del detector |
| `params` | JSONB — mapeado a `StrategyConfig` por `parse_config_from_json()` en Rust |
| `calibration_id` | FK a `calibration_log` |
| `is_active` | Solo el row activo tiene `true`; al deployer desactiva los anteriores |

**UNIQUE constraint**: `(regime, strategy, is_active)` — previene parámetros duplicados activos.

**Query que usa el monitor Rust**:
```
GET /rest/v1/deployed_params
  ?regime=eq.{regime}
  &strategy=is.null
  &is_active=eq.true
  &order=updated_at.desc
  &limit=1
```

Si no hay fila activa → Rust usa `StrategyConfig::default()` con `enabled: true`.

---

## Vista analítica principal: `v_signals_with_outcomes`

JOIN entre `shadow_signals` y `signal_outcomes`. Solo incluye filas con `action = 'ShadowSignal'`.

**Columnas derivadas:**
```sql
hit_target   BOOLEAN  -- close_reason = 'TARGET'
hit_stop     BOOLEAN  -- close_reason = 'STOP'
expired      BOOLEAN  -- close_reason = 'EXPIRED'
invalidated  BOOLEAN  -- close_reason = 'INVALIDATED'

liq_bucket   TEXT     -- '>$3M' | '$2M-$3M' | '$1M-$2M' | '$500K-$1M' | '<$500K'
funding_bucket TEXT   -- 'extreme' | 'elevated' | 'neutral' | 'depressed'
               -- basado en funding_percentile_30d: 85+ | 65+ | 35+ | else
```

Esta es la tabla que lee el pipeline Python para calibración.

---

## Índices

| Índice | Tabla | Columnas |
|--------|-------|----------|
| `idx_signals_strategy_time` | shadow_signals | `(strategy, timestamp_ms DESC)` |
| `idx_signals_action_time` | shadow_signals | `(action, timestamp_ms DESC)` |
| `idx_signals_regime` | shadow_signals | `(regime_combined, strategy)` |
| `idx_signals_institutional` | shadow_signals | liquidaciones + funding + divergencia |
| `idx_outcomes_signal` | signal_outcomes | `(signal_id)` |
| `idx_outcomes_reason` | signal_outcomes | `(close_reason, r_multiple)` |
| `idx_snapshots_time` | institutional_snapshots | `(timestamp_ms DESC)` |
| `idx_regime_history_time` | regime_history | `(timestamp_ms DESC)` |
| `idx_calibration_regime` | calibration_log | `(regime, calibrated_at DESC)` |

---

## RLS (Row Level Security)

RLS está habilitado en todas las tablas. El monitor Rust y el pipeline Python usan la **service_role key** que bypasea RLS para escrituras del backend.

La **anon key** se usa para lecturas públicas si se requiere en el futuro (dashboards). No usar en el backend.

> La service_role key nunca debe estar expuesta en código cliente.

---

## Queries útiles

**Señales de las últimas 24h:**
```sql
SELECT strategy, COUNT(*), AVG(score)
FROM shadow_signals
WHERE timestamp_ms > EXTRACT(EPOCH FROM NOW() - INTERVAL '24 hours') * 1000
  AND action = 'ShadowSignal'
GROUP BY strategy;
```

**Expectancy reciente (últimos 50 trades cerrados):**
```sql
SELECT AVG(r_multiple) AS expectancy
FROM v_signals_with_outcomes
WHERE close_reason IS NOT NULL
ORDER BY timestamp_ms DESC
LIMIT 50;
```

**Trades por detector y resultado:**
```sql
SELECT strategy, close_reason, COUNT(*), AVG(r_multiple)
FROM v_signals_with_outcomes
WHERE close_reason IS NOT NULL
GROUP BY strategy, close_reason
ORDER BY strategy, close_reason;
```

**Parámetros activos del régimen actual:**
```sql
SELECT regime, params, updated_at
FROM deployed_params
WHERE is_active = TRUE
ORDER BY updated_at DESC;
```

**Última calibración por régimen:**
```sql
SELECT regime, calibrated_at, test_expectancy, overfit_gap, approved_for_deploy
FROM calibration_log
ORDER BY calibrated_at DESC
LIMIT 10;
```

**Señales bloqueadas por gate (para ajustar thresholds):**
```sql
SELECT missing, COUNT(*)
FROM shadow_signals
WHERE action = 'Blocked'
  AND timestamp_ms > EXTRACT(EPOCH FROM NOW() - INTERVAL '7 days') * 1000
GROUP BY missing
ORDER BY COUNT(*) DESC;
```

---

## Acceso desde Python (DuckDB)

```python
from core.db import query_df

# Query directa a cualquier tabla
df = query_df("SELECT * FROM supabase.v_signals_with_outcomes LIMIT 100")

# Lectura de parámetros desplegados
params = query_df("""
    SELECT regime, params
    FROM supabase.deployed_params
    WHERE is_active = TRUE
""")
```

El attach se hace una sola vez en `core/db.py`:
```python
conn.execute(f"ATTACH '{DATABASE_URL}' AS supabase (TYPE postgres, READ_ONLY)")
```

---

## Acceso desde Rust (reqwest REST)

El monitor Rust NO usa sqlx ni conexión directa a PostgreSQL. Usa la REST API de Supabase via reqwest:

```
Headers:
  apikey: {SUPABASE_KEY}
  Authorization: Bearer {SUPABASE_KEY}
  Content-Type: application/json
  Prefer: return=minimal   (para writes)
```

Todos los writes son fire-and-forget en `tokio::spawn` — nunca bloquean el pipeline de bar-close.
