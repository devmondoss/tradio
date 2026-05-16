# Plan de Implementación — Sistema de Recalibración Continua
## FlowSurface Trading System

**Fecha:** Mayo 2026  
**Propósito:** Infraestructura completa para que el sistema aprenda y se adapte
cuando el mercado cambia, sin intervención manual constante  
**Stack:** Rust (sistema live) + Python (calibración offline) + Supabase + MLflow

---

## Por qué necesitamos todo esto — antes de ver código

### El problema fundamental

Cuando codificaste las estrategias, pusiste números concretos:

```
liq_hunt_min_usd  = $500,000
min_divergence    = 0.18
min_score         = 0.70
funding_extreme   = 0.0006
```

Esos números son razonables pero son **educated guesses**. El mercado no los conoce
ni le importan. Lo que funciona en BTC en un bull market de enero puede ser
completamente inútil en el chop de marzo. Los parámetros necesitan actualizarse
cuando el contexto del mercado cambia — no cada año, no cada mes, sino
**cuando el mercado lo exige**.

### Por qué el mercado crypto es especialmente difícil

En equities tradicionales, un régimen de mercado puede durar meses o años.
Un fondo institucional calibra cada trimestre y es suficiente.

En BTC perpetual:

```
Semana 1:  Bull run, alta volatilidad, funding extremo
Semana 2:  Corrección brusca, liquidaciones masivas
Semana 3:  Chop lateral, funding neutro, bajo volumen
Semana 4:  Expansión, nuevos mínimos, OI acumulando
```

Un sistema calibrado en la semana 1 con parámetros optimizados para bull run
va a generar señales incorrectas en la semana 3. Sin recalibración automática,
el sistema se vuelve obsoleto en días, no en meses.

### El riesgo específico: overfitting temporal

Sin las técnicas correctas, el proceso de calibración produce el problema opuesto:
parámetros perfectamente ajustados al pasado que no funcionan en el futuro.

```
Ejemplo concreto:
Calibrás con Optuna sobre 90 días de datos:
  → Encuentra: liq_min = $1,247,832, min_divergence = 0.2134
  → Expectancy histórica: +0.71R  ← sobre los datos que usaste

Aplicás esos parámetros en los próximos 30 días:
  → Expectancy real: +0.08R

¿Qué pasó? Los parámetros están ajustados al ruido del período histórico,
no a la señal real. Funcionan perfectamente para el pasado
y no predicen el futuro.
```

El sistema de recalibración que vamos a construir resuelve exactamente esto
con tres mecanismos: walk-forward (nunca usar datos futuros para calibrar),
regime-awareness (calibrar por contexto de mercado, no por tiempo fijo),
y deflated sharpe (verificar que el edge encontrado es real, no ruido estadístico).

---

## Arquitectura completa del sistema de recalibración

```
┌─────────────────────────────────────────────────────────────┐
│                    SISTEMA LIVE (Rust / Railway)             │
│                                                              │
│  6 detectores → señales → Supabase (PostgreSQL)              │
│  Snapshots institucionales cada 5min → Supabase              │
│  Outcomes (hit target/stop) → Supabase                       │
└──────────────────────────┬──────────────────────────────────┘
                           │ datos
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    SUPABASE (fuente de verdad)               │
│                                                              │
│  shadow_signals          signal_outcomes                     │
│  institutional_snapshots calibration_log                     │
│  regime_history          deployed_params                     │
└──────────────────────────┬──────────────────────────────────┘
                           │ DuckDB lee directo via pg extension
                           ▼
┌─────────────────────────────────────────────────────────────┐
│              PIPELINE DE RECALIBRACIÓN (Python)              │
│                                                              │
│  1. Monitor de degradación → ¿hay que recalibrar?            │
│  2. Detector de régimen → ¿en qué régimen estamos?           │
│  3. Walk-Forward Optimizer → ¿cuáles son los mejores params? │
│  4. Deflated Sharpe Gate → ¿el edge es real o ruido?         │
│  5. MLflow → registrar todo                                  │
│  6. Deploy automático → actualizar StrategyConfig             │
└──────────────────────────┬──────────────────────────────────┘
                           │ nuevos parámetros vía env vars
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                    RAILWAY (redeploy)                        │
│                                                              │
│  StrategyConfig cargado desde env vars actualizadas          │
│  Sistema live con parámetros calibrados para el régimen      │
│  actual del mercado                                          │
└─────────────────────────────────────────────────────────────┘
```

---

## FASE 1 — Supabase: esquema completo de base de datos

### Por qué Supabase y no MongoDB Atlas

MongoDB es una base documental optimizada para escritura flexible.
Supabase es PostgreSQL — una base relacional optimizada para queries analíticas.

Para calibración necesitás queries como:

```sql
SELECT regime, AVG(r_multiple), STDDEV(r_multiple)
FROM signals
WHERE short_liq_usd_5m > 1200000
  AND funding_percentile_30d > 70
GROUP BY regime
HAVING COUNT(*) > 30
```

En MongoDB eso es un aggregation pipeline de 30 líneas. En PostgreSQL son 8 líneas.
DuckDB se conecta directamente a PostgreSQL con una línea. La elección es clara.

### 1.1 Migración SQL completa

Ejecutar en Supabase SQL Editor:

```sql
-- ============================================================
-- TABLA PRINCIPAL: señales generadas por los detectores
-- ============================================================
CREATE TABLE shadow_signals (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    timestamp_ms            BIGINT NOT NULL,

    -- Identificación de la señal
    strategy                TEXT NOT NULL,
    side                    TEXT,           -- 'Long' | 'Short' | NULL si Blocked
    regime_slow             TEXT,           -- 'TrendUp' | 'TrendDown' | 'Chop'
    regime_fast             TEXT,           -- 'Expansion' | 'Compression'
    regime_combined         TEXT,           -- 'TrendUp_Expansion' etc.
    action                  TEXT NOT NULL,  -- 'ShadowSignal' | 'Wait' | 'Blocked'

    -- Trade hipotético
    entry_price             DOUBLE PRECISION,
    stop_price              DOUBLE PRECISION,
    target_price            DOUBLE PRECISION,
    score                   DOUBLE PRECISION,
    ttl_ms                  BIGINT,

    -- Por qué se rechazó o bloqueó (si aplica)
    block_reason            TEXT,           -- 'FLOW_NOT_LIVE' | 'SPREAD_TOO_WIDE' etc.
    reject_reason           TEXT,           -- 'LOW_SCORE' etc.

    -- Evidencias del detector
    evidence                TEXT[],
    missing                 TEXT[],

    -- ── Contexto técnico ──────────────────────────────────
    price                   DOUBLE PRECISION NOT NULL,
    vwap_session            DOUBLE PRECISION,
    poc                     DOUBLE PRECISION,
    vah                     DOUBLE PRECISION,
    val                     DOUBLE PRECISION,
    cvd_slope               DOUBLE PRECISION,
    cvd_absolute            DOUBLE PRECISION,
    atr                     DOUBLE PRECISION,
    spread_bps              DOUBLE PRECISION,
    obi_l5                  DOUBLE PRECISION,
    microprice              DOUBLE PRECISION,

    -- ── Contexto institucional ────────────────────────────
    -- Liquidaciones
    short_liq_usd_5m        DOUBLE PRECISION,
    long_liq_usd_5m         DOUBLE PRECISION,
    total_liq_usd_5m        DOUBLE PRECISION,
    cascade_active          BOOLEAN,
    liq_dominant_side       TEXT,

    -- Long/Short ratios
    top_traders_long_pct    DOUBLE PRECISION,
    retail_long_pct         DOUBLE PRECISION,
    ls_divergence           DOUBLE PRECISION,   -- retail_long - top_traders_long
    divergence_signal       TEXT,               -- 'SmartShortRetailLong' etc.

    -- Open Interest
    oi_current_btc          DOUBLE PRECISION,
    oi_change_30m_pct       DOUBLE PRECISION,
    oi_change_2h_pct        DOUBLE PRECISION,
    oi_trend                TEXT,               -- 'AccumulatingFast' | 'Flat' etc.

    -- Funding
    funding_current         DOUBLE PRECISION,
    funding_regime          TEXT,               -- 'ExtremeLong' | 'Neutral' etc.
    funding_percentile_30d  DOUBLE PRECISION,   -- 0-100
    funding_avg_7d          DOUBLE PRECISION,

    -- Taker
    taker_buy_sell_ratio    DOUBLE PRECISION,
    taker_imbalance         DOUBLE PRECISION    -- (buy-sell)/(buy+sell), -1 a +1
);

-- ============================================================
-- TABLA DE OUTCOMES: resultado de cada señal
-- ============================================================
CREATE TABLE signal_outcomes (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    signal_id           UUID NOT NULL REFERENCES shadow_signals(id) ON DELETE CASCADE,
    resolved_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    timestamp_ms        BIGINT NOT NULL,

    -- Resultado final
    close_reason        TEXT NOT NULL,  -- 'TARGET' | 'STOP' | 'EXPIRED' | 'INVALIDATED'
    close_price         DOUBLE PRECISION NOT NULL,
    duration_ms         BIGINT NOT NULL,
    r_multiple          DOUBLE PRECISION NOT NULL,

    -- PnL simulado (con fees 0.04%, slippage 0.02%, funding si aplica)
    pnl_gross_usd       DOUBLE PRECISION,
    pnl_net_usd         DOUBLE PRECISION,
    fee_entry_usd       DOUBLE PRECISION,
    fee_exit_usd        DOUBLE PRECISION,
    slippage_usd        DOUBLE PRECISION,
    funding_cost_usd    DOUBLE PRECISION,

    -- Maximum Favorable/Adverse Excursion
    mfe_r               DOUBLE PRECISION,   -- mejor punto del trade en R
    mae_r               DOUBLE PRECISION,   -- peor punto del trade en R

    -- Horizontes de evaluación (precio en cada punto)
    price_5m            DOUBLE PRECISION,
    price_15m           DOUBLE PRECISION,
    price_30m           DOUBLE PRECISION,
    price_1h            DOUBLE PRECISION,
    price_4h            DOUBLE PRECISION,

    -- R múltiple en cada horizonte
    r_5m                DOUBLE PRECISION,
    r_15m               DOUBLE PRECISION,
    r_30m               DOUBLE PRECISION,
    r_1h                DOUBLE PRECISION,
    r_4h                DOUBLE PRECISION
);

-- ============================================================
-- TABLA DE SNAPSHOTS INSTITUCIONALES (cada 5 minutos)
-- ============================================================
-- Esta tabla es crítica para análisis. Guarda el estado
-- institucional del mercado independientemente de si hay señal.
-- Permite preguntar: "¿qué pasó con el precio en las siguientes
-- 4 horas cuando funding estaba en percentil >80?"
CREATE TABLE institutional_snapshots (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    timestamp_ms            BIGINT NOT NULL,
    price                   DOUBLE PRECISION NOT NULL,

    short_liq_usd_5m        DOUBLE PRECISION,
    long_liq_usd_5m         DOUBLE PRECISION,
    cascade_active          BOOLEAN,

    top_traders_long_pct    DOUBLE PRECISION,
    retail_long_pct         DOUBLE PRECISION,
    ls_divergence           DOUBLE PRECISION,
    divergence_signal       TEXT,

    oi_btc                  DOUBLE PRECISION,
    oi_change_30m_pct       DOUBLE PRECISION,
    oi_trend                TEXT,

    funding_current         DOUBLE PRECISION,
    funding_regime          TEXT,
    funding_percentile      DOUBLE PRECISION,

    taker_ratio             DOUBLE PRECISION,
    taker_imbalance         DOUBLE PRECISION,

    -- Régimen del mercado en este momento
    regime_slow             TEXT,
    regime_fast             TEXT
);

-- ============================================================
-- TABLA DE HISTORIAL DE RÉGIMEN
-- ============================================================
-- Registra cada cambio de régimen con timestamp.
-- Fundamental para calibración regime-aware.
CREATE TABLE regime_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    timestamp_ms    BIGINT NOT NULL,
    regime_slow     TEXT NOT NULL,
    regime_fast     TEXT NOT NULL,
    regime_combined TEXT NOT NULL,
    duration_ms     BIGINT,         -- cuánto duró el régimen anterior
    price_at_change DOUBLE PRECISION
);

-- ============================================================
-- TABLA DE CALIBRACIONES
-- ============================================================
-- Registra cada calibración realizada: qué parámetros se
-- encontraron, qué performance tuvieron en train y test,
-- y si fueron aprobados para deploy.
CREATE TABLE calibration_log (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    calibrated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trigger_reason      TEXT NOT NULL,  -- 'REGIME_CHANGE' | 'DEGRADATION' | 'SCHEDULED'
    regime              TEXT NOT NULL,
    strategy            TEXT,           -- NULL = calibración global
    
    -- Parámetros encontrados
    params              JSONB NOT NULL,
    
    -- Performance walk-forward
    train_expectancy    DOUBLE PRECISION,
    test_expectancy     DOUBLE PRECISION,
    overfit_gap         DOUBLE PRECISION,   -- train - test
    n_train_trades      INTEGER,
    n_test_trades       INTEGER,
    
    -- Validación
    deflated_sharpe     DOUBLE PRECISION,
    edge_is_real        BOOLEAN,
    
    -- Decisión
    approved_for_deploy BOOLEAN NOT NULL DEFAULT FALSE,
    deploy_reason       TEXT,   -- por qué se aprobó o rechazó
    deployed_at         TIMESTAMPTZ
);

-- ============================================================
-- TABLA DE PARÁMETROS DESPLEGADOS
-- ============================================================
-- Parámetros actualmente en producción, por régimen y estrategia.
-- El sistema Rust los lee al iniciar o cuando hay cambio de régimen.
CREATE TABLE deployed_params (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    regime          TEXT NOT NULL,
    strategy        TEXT,               -- NULL = aplica a todos
    params          JSONB NOT NULL,
    calibration_id  UUID REFERENCES calibration_log(id),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    
    UNIQUE(regime, strategy, is_active)
);

-- ============================================================
-- ÍNDICES (críticos para performance de queries)
-- ============================================================
CREATE INDEX idx_signals_strategy_time
    ON shadow_signals(strategy, timestamp_ms DESC);

CREATE INDEX idx_signals_action_time
    ON shadow_signals(action, timestamp_ms DESC);

CREATE INDEX idx_signals_regime
    ON shadow_signals(regime_combined, strategy);

CREATE INDEX idx_signals_institutional
    ON shadow_signals(short_liq_usd_5m, funding_percentile_30d, ls_divergence);

CREATE INDEX idx_outcomes_signal
    ON signal_outcomes(signal_id);

CREATE INDEX idx_outcomes_reason
    ON signal_outcomes(close_reason, r_multiple);

CREATE INDEX idx_snapshots_time
    ON institutional_snapshots(timestamp_ms DESC);

CREATE INDEX idx_regime_history_time
    ON regime_history(timestamp_ms DESC);

CREATE INDEX idx_calibration_regime
    ON calibration_log(regime, calibrated_at DESC);

-- ============================================================
-- VISTA ANALÍTICA PRINCIPAL
-- ============================================================
-- Esta vista es lo que consumen DuckDB, walk-forward y Optuna.
-- Une señales con outcomes en una tabla plana lista para análisis.
CREATE VIEW v_signals_with_outcomes AS
SELECT
    s.id,
    s.timestamp_ms,
    TO_TIMESTAMP(s.timestamp_ms / 1000.0) AS signal_time,
    s.strategy,
    s.side,
    s.regime_combined,
    s.regime_slow,
    s.regime_fast,
    s.score,
    s.entry_price,
    s.stop_price,
    s.target_price,
    s.atr,
    s.spread_bps,

    -- Institucional
    s.short_liq_usd_5m,
    s.long_liq_usd_5m,
    s.cascade_active,
    s.top_traders_long_pct,
    s.retail_long_pct,
    s.ls_divergence,
    s.divergence_signal,
    s.oi_change_30m_pct,
    s.oi_trend,
    s.funding_current,
    s.funding_regime,
    s.funding_percentile_30d,
    s.taker_imbalance,
    s.cvd_slope,

    -- Outcome
    o.close_reason,
    o.r_multiple,
    o.pnl_net_usd,
    o.duration_ms,
    o.mfe_r,
    o.mae_r,
    o.r_5m,
    o.r_15m,
    o.r_30m,
    o.r_1h,

    -- Campos derivados para análisis directo
    CASE WHEN o.close_reason = 'TARGET'     THEN TRUE  ELSE FALSE END AS hit_target,
    CASE WHEN o.close_reason = 'STOP'       THEN TRUE  ELSE FALSE END AS hit_stop,
    CASE WHEN o.close_reason = 'EXPIRED'    THEN TRUE  ELSE FALSE END AS expired,
    CASE WHEN o.close_reason = 'INVALIDATED'THEN TRUE  ELSE FALSE END AS invalidated,

    -- Bucket de liquidaciones para análisis de threshold
    CASE
        WHEN s.short_liq_usd_5m > 3000000 THEN '>$3M'
        WHEN s.short_liq_usd_5m > 2000000 THEN '$2M-$3M'
        WHEN s.short_liq_usd_5m > 1000000 THEN '$1M-$2M'
        WHEN s.short_liq_usd_5m > 500000  THEN '$500K-$1M'
        ELSE '<$500K'
    END AS liq_bucket,

    -- Bucket de funding percentile
    CASE
        WHEN s.funding_percentile_30d > 85 THEN 'extreme'
        WHEN s.funding_percentile_30d > 65 THEN 'elevated'
        WHEN s.funding_percentile_30d > 35 THEN 'neutral'
        ELSE 'depressed'
    END AS funding_bucket

FROM shadow_signals s
LEFT JOIN signal_outcomes o ON s.id = o.signal_id
WHERE s.action = 'ShadowSignal';

-- ============================================================
-- TTL AUTOMÁTICO: borrar snapshots > 90 días
-- ============================================================
-- Ejecutar con pg_cron (habilitarlo en Supabase Extensions)
SELECT cron.schedule(
    'cleanup-old-snapshots',
    '0 3 * * *',  -- cada noche a las 3am UTC
    $$
    DELETE FROM institutional_snapshots
    WHERE created_at < NOW() - INTERVAL '90 days';
    $$
);
```

---

## FASE 2 — Rust: leer parámetros dinámicos desde Supabase

### Por qué los parámetros deben ser dinámicos

Actualmente `StrategyConfig` está hardcodeado en el binario. Cuando el pipeline
de Python encuentra mejores parámetros, hay que recompilar y redesplegar.

El objetivo es que el sistema Rust lea los parámetros desde Supabase al iniciar
y cuando detecta un cambio de régimen — sin recompilar.

### 2.1 Cargar parámetros al inicio

```rust
// data/src/strategy/config_loader.rs

use sqlx::PgPool;
use serde_json::Value;

pub struct ConfigLoader {
    pool: PgPool,
}

impl ConfigLoader {
    pub async fn load_for_regime(
        &self,
        regime: &str,
    ) -> Result<StrategyConfig, sqlx::Error> {

        // Primero buscar parámetros específicos para este régimen
        let row = sqlx::query!(
            r#"
            SELECT params
            FROM deployed_params
            WHERE regime = $1
              AND strategy IS NULL
              AND is_active = TRUE
            ORDER BY updated_at DESC
            LIMIT 1
            "#,
            regime
        )
        .fetch_optional(&self.pool)
        .await?;

        match row {
            Some(r) => Ok(parse_config_from_json(&r.params)),
            // Si no hay parámetros para este régimen, usar defaults
            None => {
                log::warn!(
                    "No calibrated params for regime '{}', using defaults",
                    regime
                );
                Ok(StrategyConfig::default())
            }
        }
    }
}

fn parse_config_from_json(params: &Value) -> StrategyConfig {
    StrategyConfig {
        max_spread_bps: params["max_spread_bps"]
            .as_f64().unwrap_or(2.0),
        max_vpin: params["max_vpin"]
            .as_f64().unwrap_or(0.75),
        min_score: params["min_score"]
            .as_f64().unwrap_or(0.70),
        liq_hunt_min_usd: params["liq_hunt_min_usd"]
            .as_f64().unwrap_or(500_000.0),
        min_divergence: params["min_divergence"]
            .as_f64().unwrap_or(0.18),
        funding_extreme_threshold: params["funding_extreme_threshold"]
            .as_f64().unwrap_or(0.0006),
        smart_short_threshold: params["smart_short_threshold"]
            .as_f64().unwrap_or(0.45),
        retail_long_threshold: params["retail_long_threshold"]
            .as_f64().unwrap_or(0.60),
        // ... resto de campos
        ..StrategyConfig::default()
    }
}
```

### 2.2 Detectar cambio de régimen y recargar

```rust
// En el monitor loop principal

pub struct MonitorState {
    config: StrategyConfig,
    current_regime: String,
    config_loader: ConfigLoader,
    last_config_check: Instant,
}

impl MonitorState {
    pub async fn maybe_reload_config(
        &mut self,
        new_regime: &str,
    ) {
        let regime_changed = new_regime != self.current_regime;
        let stale = self.last_config_check.elapsed() > Duration::from_secs(300);

        if regime_changed || stale {
            match self.config_loader.load_for_regime(new_regime).await {
                Ok(new_config) => {
                    log::info!(
                        "Config reloaded for regime '{}' \
                         (min_score: {} → {}, liq_min: {} → {})",
                        new_regime,
                        self.config.min_score,
                        new_config.min_score,
                        self.config.liq_hunt_min_usd,
                        new_config.liq_hunt_min_usd,
                    );
                    self.config = new_config;
                    self.current_regime = new_regime.to_string();
                    self.last_config_check = Instant::now();
                }
                Err(e) => {
                    // No crashear si Supabase está temporalmente caído
                    log::error!("Failed to reload config: {e}. Keeping current params.");
                }
            }
        }
    }
}
```

---

## FASE 3 — Python: pipeline de recalibración

Estructura del proyecto Python:

```
calibration/
  requirements.txt
  .env.example
  
  monitor.py              ← punto de entrada principal
  
  core/
    __init__.py
    db.py                 ← conexión DuckDB → Supabase
    regime_detector.py    ← detectar régimen actual
    degradation_monitor.py← detectar cuándo recalibrar
  
  calibration/
    __init__.py
    walk_forward.py       ← walk-forward optimizer
    deflated_sharpe.py    ← gate de validación estadística
    param_deployer.py     ← escribir nuevos params a Supabase
  
  tracking/
    __init__.py
    mlflow_tracker.py     ← registro de experimentos
  
  analysis/
    __init__.py
    quick_report.py       ← reporte rápido del estado del sistema
```

### 3.1 `requirements.txt`

```
sqlalchemy>=2.0
duckdb>=0.10
pandas>=2.0
numpy>=1.26
scipy>=1.12
optuna>=3.6
mlflow>=2.12
python-dotenv>=1.0
supabase>=2.4
schedule>=1.2
rich>=13.0         # output bonito en terminal
```

### 3.2 `.env.example`

```bash
# Supabase
SUPABASE_URL=https://[PROJECT].supabase.co
SUPABASE_KEY=[ANON_KEY]
DATABASE_URL=postgresql://postgres:[PASSWORD]@db.[PROJECT].supabase.co:5432/postgres

# MLflow
MLFLOW_TRACKING_URI=http://localhost:5000
# O usar MLflow con Supabase como backend:
# MLFLOW_TRACKING_URI=postgresql://postgres:[PASSWORD]@db.[PROJECT].supabase.co:5432/postgres

# Calibración
MIN_TRADES_FOR_CALIBRATION=50
WALK_FORWARD_TRAIN_DAYS=60
WALK_FORWARD_TEST_DAYS=20
MAX_OPTUNA_TRIALS=200
OVERFIT_GAP_THRESHOLD=0.20
DEFLATED_SHARPE_THRESHOLD=0.95
```

### 3.3 `core/db.py` — conexión DuckDB a Supabase

```python
# core/db.py
import duckdb
import os
from dotenv import load_dotenv

load_dotenv()

_con = None

def get_connection() -> duckdb.DuckDBPyConnection:
    """
    Retorna conexión DuckDB con Supabase adjunto.
    DuckDB puede leer PostgreSQL directamente — una sola línea.
    """
    global _con
    if _con is None:
        _con = duckdb.connect()
        _con.execute("INSTALL postgres; LOAD postgres;")
        _con.execute(f"""
            ATTACH '{os.getenv("DATABASE_URL")}'
            AS supabase (TYPE postgres, READ_ONLY)
        """)
    return _con


def query_df(sql: str) -> "pd.DataFrame":
    """Helper para queries que retornan DataFrame."""
    return get_connection().execute(sql).df()


def query_signals(
    strategy: str = None,
    regime: str = None,
    min_timestamp_ms: int = None,
    action: str = "ShadowSignal",
) -> "pd.DataFrame":
    """
    Query tipada para la vista principal.
    Siempre filtra por action='ShadowSignal' por default.
    """
    conditions = [f"action = '{action}'"]

    if strategy:
        conditions.append(f"strategy = '{strategy}'")
    if regime:
        conditions.append(f"regime_combined LIKE '{regime}%'")
    if min_timestamp_ms:
        conditions.append(f"timestamp_ms > {min_timestamp_ms}")

    where = " AND ".join(conditions)

    return query_df(f"""
        SELECT * FROM supabase.v_signals_with_outcomes
        WHERE {where}
        ORDER BY timestamp_ms ASC
    """)
```

### 3.4 `core/degradation_monitor.py` — cuándo recalibrar

```python
# core/degradation_monitor.py
"""
El monitor de degradación responde una sola pregunta:
¿El sistema está funcionando peor que su calibración anterior?

Si sí → trigger de recalibración.
Si no → seguir operando con parámetros actuales.

Tres condiciones que disparan recalibración:
1. El régimen del mercado cambió
2. El overfit_gap de los últimos 50 trades supera 0.20R
3. Han pasado más de 14 días desde la última calibración
"""
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from core.db import query_df


class DegradationMonitor:

    def __init__(
        self,
        overfit_gap_threshold: float = 0.20,
        max_days_without_calibration: int = 14,
        min_trades_window: int = 50,
    ):
        self.overfit_gap_threshold = overfit_gap_threshold
        self.max_days_without_calibration = max_days_without_calibration
        self.min_trades_window = min_trades_window

    def check(self) -> dict:
        """
        Retorna dict con:
          should_recalibrate: bool
          reason: str
          details: dict
        """
        reasons = []
        details = {}

        # ── 1. Cambio de régimen ───────────────────────────
        regime_change = self._detect_regime_change()
        if regime_change["changed"]:
            reasons.append("REGIME_CHANGE")
            details["regime_change"] = regime_change

        # ── 2. Degradación de performance ─────────────────
        degradation = self._detect_performance_degradation()
        if degradation["is_degraded"]:
            reasons.append("PERFORMANCE_DEGRADATION")
            details["degradation"] = degradation

        # ── 3. Tiempo desde última calibración ────────────
        days_stale = self._days_since_last_calibration()
        if days_stale > self.max_days_without_calibration:
            reasons.append("SCHEDULED_RECALIBRATION")
            details["days_since_calibration"] = days_stale

        return {
            "should_recalibrate": len(reasons) > 0,
            "reasons": reasons,
            "details": details,
            "checked_at": datetime.utcnow().isoformat(),
        }

    def _detect_regime_change(self) -> dict:
        """
        Compara el régimen de los últimos 30 minutos
        con el régimen cuando se hizo la última calibración.
        """
        result = query_df("""
            SELECT regime_combined
            FROM supabase.regime_history
            ORDER BY timestamp_ms DESC
            LIMIT 2
        """)

        if len(result) < 2:
            return {"changed": False}

        current  = result.iloc[0]["regime_combined"]
        previous = result.iloc[1]["regime_combined"]

        return {
            "changed": current != previous,
            "current_regime": current,
            "previous_regime": previous,
        }

    def _detect_performance_degradation(self) -> dict:
        """
        Compara la expectancy de los últimos 50 trades
        con la expectancy histórica de la última calibración.

        Si la brecha supera overfit_gap_threshold, hay degradación.

        Por qué 50 trades: con menos de 50 trades, la variance es
        tan alta que cualquier resultado puede ser ruido. Con 50+
        empezás a tener señal estadística real.
        """
        # Expectancy de la última calibración (el "benchmark")
        last_cal = query_df("""
            SELECT test_expectancy
            FROM supabase.calibration_log
            WHERE approved_for_deploy = TRUE
            ORDER BY calibrated_at DESC
            LIMIT 1
        """)

        if last_cal.empty:
            return {"is_degraded": False, "reason": "no_calibration_yet"}

        benchmark_expectancy = last_cal.iloc[0]["test_expectancy"]

        # Expectancy de los últimos N trades
        recent = query_df(f"""
            SELECT r_multiple
            FROM supabase.v_signals_with_outcomes
            WHERE close_reason IS NOT NULL
            ORDER BY timestamp_ms DESC
            LIMIT {self.min_trades_window}
        """)

        if len(recent) < self.min_trades_window:
            return {
                "is_degraded": False,
                "reason": f"insufficient_trades_{len(recent)}/{self.min_trades_window}",
            }

        recent_expectancy = recent["r_multiple"].mean()
        gap = benchmark_expectancy - recent_expectancy

        return {
            "is_degraded": gap > self.overfit_gap_threshold,
            "benchmark_expectancy": round(benchmark_expectancy, 3),
            "recent_expectancy": round(recent_expectancy, 3),
            "gap": round(gap, 3),
            "threshold": self.overfit_gap_threshold,
            "n_trades": len(recent),
        }

    def _days_since_last_calibration(self) -> float:
        result = query_df("""
            SELECT calibrated_at
            FROM supabase.calibration_log
            ORDER BY calibrated_at DESC
            LIMIT 1
        """)

        if result.empty:
            return 999.0  # nunca calibrado = siempre stale

        last = pd.to_datetime(result.iloc[0]["calibrated_at"])
        delta = datetime.utcnow() - last.replace(tzinfo=None)
        return delta.total_seconds() / 86400
```

### 3.5 `calibration/walk_forward.py` — el corazón del sistema

```python
# calibration/walk_forward.py
"""
Walk-Forward Optimization — por qué es el método correcto.

El problema con optimizar sobre todos los datos históricos es que
siempre encontrás los mejores parámetros para el pasado. El mercado
del futuro es diferente.

Walk-forward simula exactamente lo que va a pasar en producción:
- Calibrás con los últimos 60 días
- Aplicás en los próximos 20 días
- Repetís

Nunca ves el futuro. Siempre calibrás sobre el pasado inmediato.

La métrica clave es el overfit_gap: train_expectancy - test_expectancy.
Un gap < 0.15R indica que los parámetros son robustos.
Un gap > 0.30R indica overfitting severo.
"""
import pandas as pd
import numpy as np
import optuna
from typing import Optional
from datetime import timedelta

optuna.logging.set_verbosity(optuna.logging.WARNING)


# ── Espacio de búsqueda de parámetros ─────────────────────────────────────────
# Estos son los parámetros que Optuna va a explorar.
# Los rangos están definidos por lógica de negocio:
#   - liq_hunt_min_usd: mínimo $100K (mucho ruido), máximo $5M (muy pocas señales)
#   - min_score: mínimo 0.50 (demasiadas señales malas), máximo 0.85 (demasiado restrictivo)
PARAM_SPACE = {
    "liq_hunt_min_usd":         (100_000,  5_000_000),
    "min_divergence":           (0.08,     0.35),
    "funding_extreme_threshold":(0.0002,   0.0012),
    "funding_elevated_threshold":(0.0001,  0.0006),
    "smart_short_threshold":    (0.38,     0.52),
    "retail_long_threshold":    (0.52,     0.72),
    "min_score":                (0.55,     0.82),
    "max_spread_bps":           (1.0,      3.5),
}


def simulate_with_params(df: pd.DataFrame, params: dict) -> pd.Series:
    """
    Aplica los parámetros sobre un DataFrame de señales
    y retorna la serie de r_multiple resultante.

    Esta función es el corazón del walk-forward.
    No ejecuta trades reales — filtra las señales históricas
    según los parámetros y retorna los r_multiple de las que pasarían.
    """
    mask = pd.Series([True] * len(df), index=df.index)

    # Filtro de liquidaciones (solo para LiquidationHunt)
    liq_mask = (df["strategy"] != "LIQUIDATION_HUNT") | \
               (df["short_liq_usd_5m"] > params.get("liq_hunt_min_usd", 500_000)) | \
               (df["long_liq_usd_5m"]  > params.get("liq_hunt_min_usd", 500_000))
    mask &= liq_mask

    # Filtro de divergencia (SmartMoneyDivergence)
    div_mask = (df["strategy"] != "SMART_MONEY_DIVERGENCE") | \
               (df["ls_divergence"].abs() > params.get("min_divergence", 0.18))
    mask &= div_mask

    # Filtro de funding (FundingExhaustion)
    fund_mask = (df["strategy"] != "FUNDING_EXHAUSTION_REVERSAL") | \
                (df["funding_current"].abs() > params.get("funding_extreme_threshold", 0.0006))
    mask &= fund_mask

    # Filtro de score (todos los detectores)
    mask &= df["score"] > params.get("min_score", 0.70)

    # Filtro de spread
    spread_col = df.get("spread_bps", pd.Series([0.0] * len(df)))
    mask &= (spread_col.isna()) | (spread_col <= params.get("max_spread_bps", 2.0))

    filtered = df[mask & df["r_multiple"].notna()]
    return filtered["r_multiple"]


def walk_forward_optimize(
    df: pd.DataFrame,
    train_days: int = 60,
    test_days: int = 20,
    n_optuna_trials: int = 200,
    min_trades_per_window: int = 20,
) -> pd.DataFrame:
    """
    Ejecuta walk-forward optimization sobre el DataFrame.

    Por cada ventana de test:
    1. Toma los train_days anteriores como datos de entrenamiento
    2. Usa Optuna para encontrar los mejores parámetros en train
    3. Aplica esos parámetros en el período de test
    4. Registra train_expectancy, test_expectancy y overfit_gap

    Retorna DataFrame con resultados de cada período.
    """
    df = df.copy()
    df["signal_date"] = pd.to_datetime(df["timestamp_ms"], unit="ms").dt.date

    results = []
    dates = pd.date_range(
        df["signal_date"].min() + timedelta(days=train_days),
        df["signal_date"].max(),
        freq=f"{test_days}D",
    )

    for test_start in dates:
        train_start = test_start - timedelta(days=train_days)
        test_end    = test_start + timedelta(days=test_days)

        train_df = df[
            (df["signal_date"] >= train_start.date()) &
            (df["signal_date"] <  test_start.date())
        ]
        test_df = df[
            (df["signal_date"] >= test_start.date()) &
            (df["signal_date"] <  test_end.date())
        ]

        if len(train_df) < min_trades_per_window:
            continue

        # ── Optimización en train ──────────────────────────────
        def objective(trial):
            params = {
                k: trial.suggest_float(k, lo, hi)
                for k, (lo, hi) in PARAM_SPACE.items()
            }
            r = simulate_with_params(train_df, params)
            if len(r) < min_trades_per_window:
                return -1.0  # penalizar parámetros muy restrictivos
            return r.mean()

        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=n_optuna_trials, show_progress_bar=False)
        best_params = study.best_params

        # ── Aplicar en test ────────────────────────────────────
        train_r = simulate_with_params(train_df, best_params)
        test_r  = simulate_with_params(test_df,  best_params)

        train_exp = train_r.mean() if len(train_r) > 0 else None
        test_exp  = test_r.mean()  if len(test_r)  > 0 else None

        results.append({
            "test_period_start":  test_start,
            "best_params":        best_params,
            "train_expectancy":   round(train_exp, 3) if train_exp else None,
            "test_expectancy":    round(test_exp,  3) if test_exp  else None,
            "overfit_gap":        round(train_exp - test_exp, 3)
                                  if train_exp and test_exp else None,
            "n_train_trades":     len(train_r),
            "n_test_trades":      len(test_r),
        })

        print(
            f"  Period {test_start.date()}: "
            f"train={train_exp:.3f}R, test={test_exp:.3f}R, "
            f"gap={train_exp-test_exp:.3f}R, n_test={len(test_r)}"
            if train_exp and test_exp else
            f"  Period {test_start.date()}: insufficient data"
        )

    return pd.DataFrame(results)


def get_best_robust_params(wf_results: pd.DataFrame) -> Optional[dict]:
    """
    De todos los períodos walk-forward, selecciona los parámetros
    del período con mejor test_expectancy Y overfit_gap < 0.20R.

    Si todos los períodos tienen gap > 0.20R → el sistema está
    overfitteando sistemáticamente → no deployar nada.
    """
    robust = wf_results[
        (wf_results["overfit_gap"].notna()) &
        (wf_results["overfit_gap"] < 0.20) &
        (wf_results["n_test_trades"] >= 10)
    ]

    if robust.empty:
        return None

    best_row = robust.loc[robust["test_expectancy"].idxmax()]
    return best_row["best_params"]
```

### 3.6 `calibration/deflated_sharpe.py` — gate de validación

```python
# calibration/deflated_sharpe.py
"""
Deflated Sharpe Ratio — por qué existe y qué resuelve.

Problema: probaste 200 combinaciones de parámetros con Optuna.
Una de ellas tiene Sharpe 1.8. ¿Es edge real o encontraste esa
combinación por casualidad después de 200 intentos?

Con 200 intentos, la probabilidad de encontrar un Sharpe 1.8
puramente por azar es no despreciable. El Deflated Sharpe Ratio
ajusta el Sharpe observado por el número de trials realizados.

Si deflated_sharpe > 0.95 → 95% de confianza de que el edge es real.
Si deflated_sharpe < 0.95 → el resultado puede ser ruido estadístico.

Fuente: López de Prado, "Advances in Financial Machine Learning" (2018)
"""
import numpy as np
from scipy.stats import norm


def deflated_sharpe_ratio(
    returns: "pd.Series",
    n_trials: int,
    sr_benchmark: float = 0.0,
) -> float:
    """
    Calcula el Deflated Sharpe Ratio.

    Args:
        returns:      Serie de r_multiple de los trades en el período de test
        n_trials:     Número de combinaciones de parámetros que probaste
        sr_benchmark: Sharpe mínimo esperado por azar (default 0)

    Returns:
        Probabilidad (0-1) de que el edge sea real y no ruido estadístico.
        > 0.95 = aprobado para deploy.
        < 0.95 = rechazado, posiblemente ruido.
    """
    n = len(returns)
    if n < 10:
        return 0.0

    sr_observed = returns.mean() / returns.std() * np.sqrt(252)

    # Ajuste por número de trials:
    # Si probaste 200 combinaciones y tomás la mejor, el Sharpe
    # esperado por pura suerte es más alto que si probaste solo 1.
    expected_max_sr = (1 - np.euler_gamma) * norm.ppf(1 - 1.0 / n_trials) + \
                      np.euler_gamma * norm.ppf(1 - 1.0 / (n_trials * np.e))

    # Ajuste por no-normalidad de los retornos financieros
    # (los retornos de trading tienen fat tails y skew)
    skew = float(returns.skew())
    kurt = float(returns.kurtosis())

    dsr = norm.cdf(
        (sr_observed - expected_max_sr) *
        np.sqrt(n - 1) /
        np.sqrt(1 - skew * sr_observed + (kurt - 1) / 4.0 * sr_observed ** 2)
    )

    return float(np.clip(dsr, 0.0, 1.0))


def edge_is_real(
    returns: "pd.Series",
    n_trials: int,
    threshold: float = 0.95,
) -> tuple[bool, float]:
    """
    Wrapper simple que retorna (aprobado, dsr_score).
    """
    dsr = deflated_sharpe_ratio(returns, n_trials)
    return dsr >= threshold, dsr
```

### 3.7 `calibration/param_deployer.py` — escribir params a Supabase

```python
# calibration/param_deployer.py
"""
Escribe los nuevos parámetros calibrados a Supabase.
El sistema Rust los leerá automáticamente en el próximo
check de régimen (cada 5 minutos).
"""
import json
from datetime import datetime
from supabase import create_client
import os


class ParamDeployer:

    def __init__(self):
        self.client = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_KEY"),
        )

    def deploy(
        self,
        regime: str,
        params: dict,
        calibration_id: str,
        test_expectancy: float,
        overfit_gap: float,
        dsr: float,
    ) -> bool:
        """
        Desactiva parámetros anteriores para este régimen
        y activa los nuevos.

        Retorna True si el deploy fue exitoso.
        """
        try:
            # Desactivar params anteriores
            self.client.table("deployed_params") \
                .update({"is_active": False}) \
                .eq("regime", regime) \
                .eq("is_active", True) \
                .execute()

            # Insertar nuevos params activos
            self.client.table("deployed_params").insert({
                "regime":           regime,
                "strategy":         None,  # aplica a todos los detectores
                "params":           params,
                "calibration_id":   calibration_id,
                "is_active":        True,
                "updated_at":       datetime.utcnow().isoformat(),
            }).execute()

            print(f"✅ Deployed params for regime '{regime}'")
            print(f"   test_expectancy={test_expectancy:.3f}R")
            print(f"   overfit_gap={overfit_gap:.3f}R")
            print(f"   deflated_sharpe={dsr:.3f}")
            return True

        except Exception as e:
            print(f"❌ Deploy failed: {e}")
            return False
```

### 3.8 `tracking/mlflow_tracker.py`

```python
# tracking/mlflow_tracker.py
import mlflow
import os
from datetime import datetime


class CalibrationTracker:

    def __init__(self):
        mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI"))
        mlflow.set_experiment("flowsurface_calibration")

    def log_calibration(
        self,
        regime: str,
        trigger_reason: str,
        wf_results: "pd.DataFrame",
        best_params: dict,
        dsr: float,
        approved: bool,
    ) -> str:
        """
        Registra una calibración completa en MLflow.
        Retorna el run_id para referencia.
        """
        run_name = f"{regime}_{trigger_reason}_{datetime.utcnow():%Y%m%d_%H%M}"

        with mlflow.start_run(run_name=run_name) as run:

            # Contexto
            mlflow.log_param("regime",         regime)
            mlflow.log_param("trigger_reason", trigger_reason)
            mlflow.log_param("timestamp",      datetime.utcnow().isoformat())

            # Parámetros encontrados
            mlflow.log_params(best_params)

            # Performance walk-forward
            valid = wf_results.dropna(subset=["test_expectancy"])
            if not valid.empty:
                mlflow.log_metric("avg_train_expectancy",
                                  valid["train_expectancy"].mean())
                mlflow.log_metric("avg_test_expectancy",
                                  valid["test_expectancy"].mean())
                mlflow.log_metric("avg_overfit_gap",
                                  valid["overfit_gap"].mean())
                mlflow.log_metric("n_wf_periods", len(valid))

            # Validación estadística
            mlflow.log_metric("deflated_sharpe",  dsr)
            mlflow.log_param("approved",          str(approved))

            # Guardar tabla completa como artefacto
            wf_path = f"/tmp/wf_results_{regime}.csv"
            wf_results.to_csv(wf_path, index=False)
            mlflow.log_artifact(wf_path)

        return run.info.run_id
```

### 3.9 `monitor.py` — punto de entrada principal

```python
# monitor.py
"""
Punto de entrada del pipeline de recalibración.

Ejecutar:
  python monitor.py            ← loop continuo
  python monitor.py --once     ← una sola verificación
  python monitor.py --force    ← forzar recalibración ahora

Lógica principal:
  Cada 30 minutos:
    1. Verificar si hay que recalibrar (degradación, régimen, tiempo)
    2. Si sí → ejecutar walk-forward para el régimen actual
    3. Validar con Deflated Sharpe
    4. Si aprobado → deployar a Supabase → Rust lo lee automáticamente
    5. Registrar todo en MLflow
"""
import argparse
import time
import schedule
from datetime import datetime
from rich.console import Console
from rich.table import Table

from core.db import query_signals, query_df
from core.degradation_monitor import DegradationMonitor
from calibration.walk_forward import walk_forward_optimize, get_best_robust_params
from calibration.deflated_sharpe import edge_is_real
from calibration.param_deployer import ParamDeployer
from tracking.mlflow_tracker import CalibrationTracker

console = Console()


def get_current_regime() -> str:
    result = query_df("""
        SELECT regime_combined
        FROM supabase.regime_history
        ORDER BY timestamp_ms DESC
        LIMIT 1
    """)
    return result.iloc[0]["regime_combined"] if not result.empty else "Unknown"


def run_calibration_pipeline(trigger_reason: str = "MANUAL"):
    regime = get_current_regime()
    console.print(f"\n🔄 Starting calibration for regime: [bold]{regime}[/bold]")
    console.print(f"   Trigger: {trigger_reason}")

    # ── 1. Cargar datos del régimen actual ─────────────────
    # Solo datos de este régimen — no mezclamos bull con chop
    signals = query_signals(regime=regime)

    min_trades = int(os.getenv("MIN_TRADES_FOR_CALIBRATION", 50))
    if len(signals) < min_trades:
        console.print(
            f"⚠️  Insufficient data: {len(signals)} trades "
            f"(need {min_trades}). Skipping."
        )
        return

    console.print(f"   Data: {len(signals)} trades for regime '{regime}'")

    # ── 2. Walk-Forward Optimization ──────────────────────
    console.print("   Running walk-forward optimization...")
    wf_results = walk_forward_optimize(
        df=signals,
        train_days=int(os.getenv("WALK_FORWARD_TRAIN_DAYS", 60)),
        test_days=int(os.getenv("WALK_FORWARD_TEST_DAYS", 20)),
        n_optuna_trials=int(os.getenv("MAX_OPTUNA_TRIALS", 200)),
    )

    if wf_results.empty:
        console.print("❌ Walk-forward returned no results. Aborting.")
        return

    best_params = get_best_robust_params(wf_results)
    if best_params is None:
        console.print(
            "❌ All walk-forward periods show high overfitting. "
            "Keeping current params."
        )
        return

    # ── 3. Deflated Sharpe Gate ────────────────────────────
    from calibration.walk_forward import simulate_with_params
    # Usar el último período de test como datos de validación final
    last_test_signals = signals.tail(100)
    test_returns = simulate_with_params(last_test_signals, best_params)

    n_trials = int(os.getenv("MAX_OPTUNA_TRIALS", 200))
    approved, dsr = edge_is_real(
        test_returns,
        n_trials=n_trials,
        threshold=float(os.getenv("DEFLATED_SHARPE_THRESHOLD", 0.95)),
    )

    # ── 4. Registrar en MLflow ────────────────────────────
    tracker = CalibrationTracker()
    run_id = tracker.log_calibration(
        regime=regime,
        trigger_reason=trigger_reason,
        wf_results=wf_results,
        best_params=best_params,
        dsr=dsr,
        approved=approved,
    )

    # ── 5. Registrar en Supabase ──────────────────────────
    valid = wf_results.dropna(subset=["test_expectancy"])
    avg_test = valid["test_expectancy"].mean()
    avg_gap  = valid["overfit_gap"].mean()

    from supabase import create_client
    client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

    cal_row = client.table("calibration_log").insert({
        "trigger_reason":       trigger_reason,
        "regime":               regime,
        "params":               best_params,
        "train_expectancy":     float(valid["train_expectancy"].mean()),
        "test_expectancy":      float(avg_test),
        "overfit_gap":          float(avg_gap),
        "n_train_trades":       int(valid["n_train_trades"].sum()),
        "n_test_trades":        int(valid["n_test_trades"].sum()),
        "deflated_sharpe":      float(dsr),
        "edge_is_real":         approved,
        "approved_for_deploy":  approved,
        "deploy_reason":        "DSR >= 0.95" if approved else f"DSR = {dsr:.3f} < 0.95",
    }).execute()

    calibration_id = cal_row.data[0]["id"]

    # ── 6. Deploy si aprobado ──────────────────────────────
    if approved:
        deployer = ParamDeployer()
        deployer.deploy(
            regime=regime,
            params=best_params,
            calibration_id=calibration_id,
            test_expectancy=float(avg_test),
            overfit_gap=float(avg_gap),
            dsr=dsr,
        )
        console.print(
            f"✅ [green]Calibration approved and deployed[/green] "
            f"(DSR={dsr:.3f}, test_expectancy={avg_test:.3f}R)"
        )
    else:
        console.print(
            f"⚠️  [yellow]Calibration rejected[/yellow] — "
            f"edge not statistically significant "
            f"(DSR={dsr:.3f} < 0.95). Keeping current params."
        )

    console.print(f"   MLflow run: {run_id}\n")


def run_check():
    """Verificación periódica — ¿hay que recalibrar?"""
    monitor = DegradationMonitor()
    status = monitor.check()

    if status["should_recalibrate"]:
        reason = "_".join(status["reasons"])
        console.print(f"[yellow]Recalibration triggered:[/yellow] {reason}")
        run_calibration_pipeline(trigger_reason=reason)
    else:
        console.print(
            f"[green]System healthy[/green] — "
            f"no recalibration needed "
            f"({datetime.utcnow():%H:%M UTC})"
        )


def print_status():
    """Reporte rápido del estado del sistema."""
    table = Table(title="FlowSurface System Status")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    from core.db import query_df

    # Último régimen
    regime = get_current_regime()
    table.add_row("Current regime", regime)

    # Trades últimas 24h
    n_24h = query_df("""
        SELECT COUNT(*) as n
        FROM supabase.shadow_signals
        WHERE timestamp_ms > EXTRACT(EPOCH FROM NOW() - INTERVAL '24 hours') * 1000
          AND action = 'ShadowSignal'
    """).iloc[0]["n"]
    table.add_row("Signals last 24h", str(int(n_24h)))

    # Expectancy últimos 50 trades
    recent = query_df("""
        SELECT AVG(r_multiple) as exp
        FROM supabase.v_signals_with_outcomes
        WHERE close_reason IS NOT NULL
        ORDER BY timestamp_ms DESC
        LIMIT 50
    """)
    exp = recent.iloc[0]["exp"]
    table.add_row(
        "Recent expectancy (50t)",
        f"{exp:.3f}R" if exp else "N/A"
    )

    # Última calibración
    last_cal = query_df("""
        SELECT calibrated_at, test_expectancy, overfit_gap, approved_for_deploy
        FROM supabase.calibration_log
        ORDER BY calibrated_at DESC
        LIMIT 1
    """)
    if not last_cal.empty:
        row = last_cal.iloc[0]
        table.add_row("Last calibration", str(row["calibrated_at"])[:16])
        table.add_row("  test_expectancy", f"{row['test_expectancy']:.3f}R")
        table.add_row("  overfit_gap",     f"{row['overfit_gap']:.3f}R")
        table.add_row("  approved",        "✅" if row["approved_for_deploy"] else "❌")

    console.print(table)


if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser()
    parser.add_argument("--once",   action="store_true")
    parser.add_argument("--force",  action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.status:
        print_status()

    elif args.force:
        run_calibration_pipeline(trigger_reason="FORCED")

    elif args.once:
        run_check()

    else:
        # Loop continuo — verificar cada 30 minutos
        console.print("🚀 FlowSurface calibration monitor started")
        print_status()

        schedule.every(30).minutes.do(run_check)
        schedule.every(6).hours.do(print_status)

        while True:
            schedule.run_pending()
            time.sleep(60)
```

---

## FASE 4 — Kelly Criterion dinámico

### Por qué Kelly y no fixed sizing

Con fixed sizing de 1 BTC por trade, una racha de 5 pérdidas consecutivas
te saca el mismo capital que una racha de 5 ganancias te da. No hay
asimetría que proteja el capital en drawdown.

Kelly asigna más capital a señales con mayor edge esperado y menos
a señales con edge bajo o incierto.

```python
# analysis/kelly_sizer.py

import pandas as pd
import numpy as np
from core.db import query_df


def calculate_kelly_by_detector(
    min_trades: int = 100,
    kelly_fraction: float = 0.25,  # Quarter Kelly — más conservador
) -> dict:
    """
    Calcula el Kelly óptimo por detector usando datos históricos.

    Quarter Kelly (0.25) es el estándar en trading algorítmico.
    El Kelly completo es matemáticamente óptimo pero maximiza
    la variance — en práctica lleva a drawdowns inaceptables.
    Quarter Kelly da el 75% del crecimiento del Kelly completo
    con mucho menos volatilidad.
    """
    results = {}

    strategies = query_df("""
        SELECT DISTINCT strategy
        FROM supabase.v_signals_with_outcomes
        WHERE close_reason IS NOT NULL
    """)["strategy"].tolist()

    for strategy in strategies:
        data = query_df(f"""
            SELECT r_multiple
            FROM supabase.v_signals_with_outcomes
            WHERE strategy = '{strategy}'
              AND close_reason IS NOT NULL
        """)

        if len(data) < min_trades:
            results[strategy] = {
                "kelly": None,
                "reason": f"insufficient_trades_{len(data)}"
            }
            continue

        r = data["r_multiple"]
        win_rate  = (r > 0).mean()
        avg_win   = r[r > 0].mean()
        avg_loss  = abs(r[r < 0].mean())

        if avg_loss == 0:
            continue

        b = avg_win / avg_loss
        full_kelly = (b * win_rate - (1 - win_rate)) / b
        quarter_kelly = full_kelly * kelly_fraction

        results[strategy] = {
            "win_rate":     round(win_rate, 3),
            "avg_win_r":    round(avg_win, 3),
            "avg_loss_r":   round(avg_loss, 3),
            "full_kelly":   round(full_kelly, 4),
            "quarter_kelly":round(quarter_kelly, 4),
            "max_size_pct": round(min(quarter_kelly, 0.10), 4),
            # Cap de 10% del equity por trade — nunca más
            "n_trades":     len(data),
        }

    return results


def get_position_size(
    signal_score: float,
    strategy: str,
    equity_btc: float,
    kelly_table: dict,
) -> float:
    """
    Calcula el tamaño de posición para una señal específica.

    La idea: señales con score más alto reciben más size
    dentro del quarter Kelly del detector.

    score 0.90 → 100% del quarter kelly
    score 0.70 → 78% del quarter kelly
    score 0.60 → 67% del quarter kelly
    """
    if strategy not in kelly_table:
        return 0.01  # mínimo si no hay datos

    max_fraction = kelly_table[strategy].get("max_size_pct", 0.02)
    if max_fraction <= 0:
        return 0.01

    # Escalar por score — no lineal, penalizamos scores bajos
    score_multiplier = (signal_score / 0.90) ** 1.5
    final_fraction = max_fraction * score_multiplier

    # Cap absoluto: nunca más de 10% del equity por trade
    final_fraction = min(final_fraction, 0.10)

    return equity_btc * final_fraction
```

---

## FASE 5 — Calendario de calibración

```
CONTINUO (automático, cada 30 minutos):
  monitor.py verifica:
    ├── ¿Cambió el régimen?      → recalibrar para nuevo régimen
    ├── ¿Overfit gap > 0.20R?    → recalibrar (sistema degradando)
    └── ¿+14 días sin calibrar?  → recalibrar por schedule

CADA CAMBIO DE RÉGIMEN (automático):
  Cargar datos del nuevo régimen
  Walk-forward optimization (30-60 min)
  Deflated Sharpe gate
  Si aprobado → deployar a Supabase
  Rust recarga parámetros en próximo check (5 min)

SEMANAL (manual, 15 minutos):
  python monitor.py --status
  Revisar MLflow dashboard
  ¿Algún detector con win_rate < 35%? → considerar deshabilitar

MENSUAL (manual, 1-2 horas):
  Recalibrar Kelly por detector con datos frescos
  Revisar feature importance (¿los mismos features siguen siendo relevantes?)
  Actualizar PARAM_SPACE si hay nuevo conocimiento del mercado
```

---

## Estructura de archivos completa

```
calibration/
  .env
  .env.example
  requirements.txt
  monitor.py                      ← punto de entrada
  
  core/
    __init__.py
    db.py                         ← DuckDB + Supabase
    degradation_monitor.py        ← cuándo recalibrar
  
  calibration/
    __init__.py
    walk_forward.py               ← corazón del sistema
    deflated_sharpe.py            ← gate estadístico
    param_deployer.py             ← escribir a Supabase
  
  tracking/
    __init__.py
    mlflow_tracker.py             ← registro MLflow
  
  analysis/
    __init__.py
    kelly_sizer.py                ← sizing óptimo
    quick_report.py               ← estado del sistema
  
  notebooks/
    01_initial_analysis.ipynb     ← exploración primera vez
    02_feature_importance.ipynb   ← qué features predicen
    03_regime_deep_dive.ipynb     ← análisis por régimen
```

---

## Por qué cada componente es necesario

| Componente | Sin él | Con él |
|------------|--------|--------|
| **Supabase** | JSONL manual difícil de consultar | SQL analytics, joins, vistas |
| **Walk-Forward** | Parámetros perfectos para el pasado, malos para el futuro | Parámetros que simulan producción real |
| **Degradation Monitor** | Sabés que el sistema falló cuando ya perdiste dinero | Sabés que el sistema está fallando antes de perder |
| **Regime-Aware** | Calibración promedia bull + bear + chop = parámetros mediocres para todos | Parámetros óptimos para el contexto actual |
| **Deflated Sharpe** | "Encontré Sharpe 1.8" puede ser ruido de 200 trials | Certeza estadística de que el edge es real |
| **MLflow** | No sabés por qué cambiaste un parámetro hace 3 meses | Historial completo de cada decisión de calibración |
| **Kelly** | Fixed sizing no protege el capital en drawdown | Sizing proporcional al edge — más cuando hay señal fuerte |

---

## Referencia

**Marcos López de Prado — *Advances in Financial Machine Learning* (2018)**  
Wiley. ISBN: 978-1-119-48208-6

Todo el framework de walk-forward, CPCV, Deflated Sharpe Ratio y bet sizing
viene de este libro. Es la referencia estándar en quant finance para ML aplicado
a trading algorítmico. Si vas a profundizar en cualquiera de estas técnicas,
ese es el libro correcto.

---

*Stack: Python 3.11+ + Supabase (PostgreSQL) + DuckDB + Optuna + MLflow*  
*Modo: Offline calibration — el sistema Rust nunca ejecuta código Python*  
*Frecuencia: Automático cada 30 minutos + triggers por régimen y degradación*
