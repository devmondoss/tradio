# Guía: Correr la calibración

---

## Setup inicial

```bash
cd calibration
pip install -r requirements.txt  # si existe, sino: pip install duckdb pandas optuna scipy supabase mlflow python-dotenv rich schedule
```

Crear `calibration/.env` (nunca commiteado a git):
```
SUPABASE_URL=https://{ref}.supabase.co
SUPABASE_KEY={service_role_key}
DATABASE_URL=postgresql://postgres:{password}@db.{ref}.supabase.co:5432/postgres
MLFLOW_TRACKING_URI=postgresql://postgres:{password}@db.{ref}.supabase.co:5432/postgres

MIN_TRADES_FOR_CALIBRATION=50
WALK_FORWARD_TRAIN_DAYS=60
WALK_FORWARD_TEST_DAYS=20
MAX_OPTUNA_TRIALS=200
OVERFIT_GAP_THRESHOLD=0.20
DEFLATED_SHARPE_THRESHOLD=0.95
```

---

## Verificar conectividad

```python
from core.db import query_df
print(query_df("SELECT 1 AS ok"))
# Debe imprimir: ok 1
```

---

## Comandos

### Ver estado del sistema

```bash
python monitor.py --status
```

Muestra:
```
┌─────────────────────────────────────────────┐
│          FlowSurface System Status          │
├───────────────────────────┬─────────────────┤
│ Current regime            │ TrendUp         │
│ Signals last 24h          │ 3               │
│ Recent expectancy (50t)   │ 0.234R          │
│ Last calibration          │ 2026-05-15 09:30│
│   test_expectancy         │ 0.312R          │
│   overfit_gap             │ 0.087R          │
│   approved                │ YES             │
└───────────────────────────┴─────────────────┘
```

### Check automático (un solo ciclo)

```bash
python monitor.py --once
```

Evalúa los 3 triggers (régimen, degradación, calendario). Si alguno dispara → ejecuta la calibración completa.

### Forzar calibración ahora

```bash
python monitor.py --force
```

Útil cuando quieres calibrar con datos actuales sin esperar a que los triggers se activen.

### Loop continuo (producción)

```bash
python monitor.py
```

Corre indefinidamente: check cada 30 minutos, status cada 6 horas.

---

## Interpretar los resultados

### Walk-forward output

```
Period 2026-03-01: train=0.421R test=0.312R gap=0.109R n_test=34
Period 2026-03-21: train=0.398R test=0.287R gap=0.111R n_test=28
Period 2026-04-10: train=0.445R test=0.195R gap=0.250R n_test=19  ← gap alto
Period 2026-04-30: train=0.412R test=0.334R gap=0.078R n_test=41
```

**Columnas:**
- `train`: expectancy en el período de entrenamiento (60 días)
- `test`: expectancy en el período de prueba fuera de muestra (20 días) — esta es la métrica real
- `gap`: diferencia. < 0.20R = robusto; > 0.30R = no desplegar
- `n_test`: trades en el período de test — mínimo 10 para que el período sea válido

El algoritmo selecciona el período con mejor `test` Y `gap < 0.20R`.

### Resultado del DSR

```
Calibration approved (DSR=0.97 test_exp=0.312R)
```

o:

```
Calibration rejected — edge not statistically significant (DSR=0.81 < 0.95). Keeping current params.
```

**DSR < 0.95**: no significa que la estrategia no funciona — significa que con los datos disponibles no podemos distinguir edge real de ruido estadístico en 200 trials de Optuna. Acumular más trades y reintentar.

### Cuándo los parámetros llegan a Rust

Tras un deploy aprobado, Rust recarga en la próxima verificación:
- Máximo 5 minutos (verificación periódica)
- O inmediatamente al cambio de régimen

Confirmación en los logs de Railway:
```
[config] Loaded calibrated params for regime 'TrendUp': min_score=0.68 liq_hunt_min_usd=750000 ...
```

---

## Ver experimentos en MLflow

```bash
cd calibration
mlflow ui --backend-store-uri postgresql://postgres:{password}@db.{ref}.supabase.co:5432/postgres
```

Abre `http://localhost:5000` en el navegador.

**Columnas útiles para comparar runs:**
- `test_expectancy` — métrica principal
- `overfit_gap` — robustez
- `deflated_sharpe` — significancia estadística
- `n_test_trades` — tamaño del sample de test
- `regime` (tag) — para filtrar por régimen

---

## Análisis manual con Python

```python
from core.db import query_signals, query_df

# Señales del régimen actual
signals = query_signals(regime="TrendUp")
print(f"Signals: {len(signals)}")
print(signals[["strategy","side","score","r_multiple"]].describe())

# Win rate por detector
wl = signals.groupby("strategy").agg(
    n=("r_multiple","count"),
    win_rate=("r_multiple", lambda x: (x > 0).mean()),
    expectancy=("r_multiple","mean"),
)
print(wl)

# Distribución de scores
import matplotlib.pyplot as plt
signals["score"].hist(bins=20)
plt.title("Score distribution")
plt.show()
```

---

## Cuándo calibrar manualmente

| Situación | Acción |
|-----------|--------|
| Nueva estrategia deployada | `--force` después de acumular 50+ trades |
| Régimen cambia abruptamente (crash de mercado) | El monitor detecta REGIME_CHANGE automáticamente |
| Expectancy cae > 0.20R respecto al benchmark | El monitor detecta PERFORMANCE_DEGRADATION automáticamente |
| Más de 14 días sin calibrar | El monitor detecta SCHEDULED_RECALIBRATION automáticamente |
| Cambio manual de parámetros en `deployed_params` | No necesario — Rust recarga solos |
