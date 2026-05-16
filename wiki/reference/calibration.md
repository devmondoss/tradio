# Referencia: Pipeline de Calibración

Archivos: `calibration/`

---

## Entry point: `monitor.py`

```bash
python monitor.py            # loop continuo (check cada 30 min)
python monitor.py --once     # un solo check
python monitor.py --force    # fuerza calibración sin importar triggers
python monitor.py --status   # tabla de estado del sistema
```

### Flujo completo `run_calibration_pipeline(trigger_reason)`

```mermaid
flowchart TD
    A["run_calibration_pipeline(trigger_reason)"]
    B["1. get_current_regime()\nregime_history ORDER BY ts DESC LIMIT 1"]
    C["2. query_signals(regime)\nv_signals_with_outcomes WHERE regime LIKE X"]
    D{"≥ 50 trades?"}
    E["Abort\npocos datos"]
    F["3. walk_forward_optimize\ntrain 60d Optuna 200 trials\ntest 20d aplica best_params\nregistra train_exp · test_exp · gap · n_trades"]
    G["get_best_robust_params\nmejor test_exp con gap < 0.20R"]
    H{"algún período\ngap < 0.20R?"}
    I["Abort\noverfitting sistemático"]
    J["4. edge_is_real\nDeflated Sharpe Ratio DSR"]
    K{"DSR ≥ 0.95?"}
    L["Rechaza\npuede ser ruido estadístico"]
    M["5. CalibrationTracker.log_calibration → MLflow\n6. INSERT calibration_log"]
    N["7. ParamDeployer.deploy\nUPDATE is_active=False para régimen\nINSERT nuevo row activo"]
    O["Rust recarga en < 5 min"]

    A --> B --> C --> D
    D -->|no| E
    D -->|sí| F --> G --> H
    H -->|no| I
    H -->|sí| J --> K
    K -->|no| L --> M
    K -->|sí| M --> N --> O
```

### Loop continuo

```python
schedule.every(30).minutes.do(run_check)
schedule.every(6).hours.do(print_status)
```

---

## Triggers de recalibración (`core/degradation_monitor.py`)

`DegradationMonitor.check()` retorna `{"should_recalibrate": bool, "reasons": [...]}`.

### Trigger 1: REGIME_CHANGE

```python
query regime_history ORDER BY timestamp_ms DESC LIMIT 2
if result[0].regime != result[1].regime → triggered
```

### Trigger 2: PERFORMANCE_DEGRADATION

```python
benchmark = calibration_log[-1].test_expectancy  (última calibración aprobada)
recent_exp = mean(v_signals_with_outcomes[-50].r_multiple)
gap = benchmark - recent_exp
if gap > overfit_gap_threshold (default 0.20R) → triggered
```

Requiere mínimo 50 trades cerrados. Bajo ese umbral la varianza es demasiado alta.

### Trigger 3: SCHEDULED_RECALIBRATION

```python
days_since = (now - calibration_log[-1].calibrated_at) / 86400
if days_since > 14 → triggered
```

---

## Walk-Forward Optimization (`calibration/walk_forward.py`)

### Por qué walk-forward (no full-sample)

Calibrar en toda la historia encuentra parámetros perfectos para el pasado. Walk-forward simula producción real: calibra en 60 días, aplica en 20, repite. Nunca ves el futuro al calibrar.

### PARAM_SPACE

| Parámetro | Rango |
|-----------|-------|
| `liq_hunt_min_usd` | $100K – $5M |
| `min_divergence` | 0.08 – 0.35 |
| `funding_extreme_threshold` | 0.0002 – 0.0012 |
| `funding_elevated_threshold` | 0.0001 – 0.0006 |
| `smart_short_threshold` | 0.38 – 0.52 |
| `retail_long_threshold` | 0.52 – 0.72 |
| `min_score` | 0.55 – 0.82 |
| `max_spread_bps` | 1.0 – 3.5 |

### `simulate_with_params(df, params) → pd.Series`

Aplica los parámetros a señales históricas y retorna la serie de `r_multiple` de las señales que habrían pasado todos los filtros. No ejecuta trades reales — solo filtra el histórico.

### `walk_forward_optimize(df, ...) → pd.DataFrame`

Para cada ventana:
1. Define `train_df` (60 días previos) y `test_df` (20 días siguientes)
2. Optuna: 200 trials, `direction="maximize"`, objetivo = `mean(simulate(train_df, params))`
3. Aplica `best_params` en `test_df`
4. Registra `train_expectancy`, `test_expectancy`, `overfit_gap = train - test`, `n_trades`

### `get_best_robust_params(wf_results) → Optional[dict]`

```python
robust = wf_results[
    overfit_gap < 0.20   # no overfitting severo
    AND n_test_trades >= 10
]
best = robust.argmax("test_expectancy")
```

Retorna `None` si todos los períodos muestran gap ≥ 0.20R.

### Interpretación del overfit_gap

| Gap | Interpretación |
|-----|---------------|
| < 0.15R | Parámetros robustos ✓ |
| 0.15 – 0.30R | Mild overfitting, usar con precaución |
| > 0.30R | Overfitting severo — no desplegar |

---

## Deflated Sharpe Ratio (`calibration/deflated_sharpe.py`)

### El problema

Con 200 trials de Optuna, es posible encontrar por azar una combinación con Sharpe 1.8. ¿Es edge real o ruido estadístico de 200 intentos?

### La solución: DSR (López de Prado)

```python
def deflated_sharpe_ratio(returns, n_trials, sr_benchmark=0.0):
    sr_observed = returns.mean() / returns.std() * sqrt(252)
    
    # Máximo Sharpe esperado por suerte en n_trials intentos
    expected_max_sr = (1 - euler_gamma) * norm.ppf(1 - 1/n_trials)
                    + euler_gamma * norm.ppf(1 - 1/(n_trials * e))
    
    # Ajuste por no-normalidad (fat tails, skew)
    denom = 1 - skew * sr_observed + (kurt - 1)/4 * sr_observed²
    
    dsr = norm.cdf((sr_observed - expected_max_sr) * sqrt(n-1) / sqrt(denom))
    return clip(dsr, 0, 1)
```

**DSR ≥ 0.95** → 95% de confianza de que el edge es real → aprobado para deploy.
**DSR < 0.95** → puede ser ruido → mantener parámetros actuales.

---

## Deploy de parámetros (`calibration/param_deployer.py`)

`ParamDeployer.deploy(regime, params, calibration_id, ...)`:

1. `UPDATE deployed_params SET is_active=False WHERE regime=X AND is_active=True`
2. `INSERT deployed_params (regime, params, calibration_id, is_active=True)`

El monitor Rust detecta el cambio en la próxima verificación (máximo 5 minutos) y recarga `StrategyConfig`.

---

## Tracking MLflow (`tracking/mlflow_tracker.py`)

`CalibrationTracker.log_calibration(regime, trigger_reason, wf_results, best_params, dsr, approved)`

Registra en MLflow:
- **Params**: todos los campos de `best_params` (uno por uno para poder filtrar)
- **Métricas**: `test_expectancy`, `overfit_gap`, `deflated_sharpe`, `n_test_trades`
- **Tags**: `regime`, `trigger_reason`, `approved`
- **Artefacto**: CSV con todos los períodos de walk-forward

`MLFLOW_TRACKING_URI` = PostgreSQL de Supabase → experimentos accesibles via `mlflow ui`.

---

## Kelly Sizer (`analysis/kelly_sizer.py`)

Sizing óptimo por detector (no en producción activa — uso analítico):

```python
kelly_fraction = (win_rate - (1 - win_rate) / avg_rr)
quarter_kelly = kelly_fraction * 0.25  # conservador
score_scaled = min(quarter_kelly * signal.score / 0.7, 0.10)  # cap 10% equity
```

**Quarter Kelly (0.25×)**: reduce el riesgo de ruina por varianza en estimaciones de win_rate.  
**Score-scaled**: señales de mayor score obtienen mayor sizing (no lineal — normalizado por score umbral).  
**Hard cap**: máximo 10% del equity por trade, sin importar el Kelly calculado.

---

## Variables de entorno

Todas en `calibration/.env` (nunca commiteado a git):

```
SUPABASE_URL=https://...supabase.co
SUPABASE_KEY=eyJ...         # service_role key — bypasa RLS
DATABASE_URL=postgresql://postgres:{password}@db.{ref}.supabase.co:5432/postgres
MLFLOW_TRACKING_URI=postgresql://postgres:{password}@...

MIN_TRADES_FOR_CALIBRATION=50
WALK_FORWARD_TRAIN_DAYS=60
WALK_FORWARD_TEST_DAYS=20
MAX_OPTUNA_TRIALS=200
OVERFIT_GAP_THRESHOLD=0.20
DEFLATED_SHARPE_THRESHOLD=0.95
```

---

## Dependencias Python

```
duckdb          # conexión a PostgreSQL via pg extension
pandas, numpy
optuna          # optimización bayesiana
scipy           # DSR (norm.cdf)
supabase        # client oficial para writes (param_deployer)
mlflow          # tracking de experimentos
python-dotenv   # carga de .env
rich            # tablas de status en consola
schedule        # loop de 30 minutos
```
