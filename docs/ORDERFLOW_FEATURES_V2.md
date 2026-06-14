# Order Flow Features V2 — Implementación Completa

**RBF + AMD · 22 nuevos campos · FASE 1–5**  
*Implementado: 2026-06-09*

> Documento histórico de instrumentación. Para RBF operativo usar `docs/rbf/RBF_REGLAS_ACTIVAS.md` y `docs/rbf/RBF_FEATURE_MATRIX.md`.
> Nota 2026-06-12: los campos HTF fueron renombrados de `htf_h4_*` a `htf_h1_*`; los scripts DS deben usar `htf_h1_aligned`.

---

## Contexto y motivación

Con las primeras semanas de paper trading activo se tenían señales con outcomes pero **sin variables predictoras** para entender por qué ganaba o perdía cada trade. El sistema funcionaba como caja negra: señal → resultado → sin capacidad de análisis estadístico.

El objetivo de esta implementación fue instrumentar ambas estrategias con los datos de order flow necesarios para responder preguntas como:

- ¿Los trades con mayor `absorption_score` tienen mejor WR?
- ¿Los AMD en Kill Zone superan a los fuera de Kill Zone?
- ¿El `vr_tier` 3 (4×+) tiene mejor follow-through que el tier 1?
- ¿Los trades alineados con H1 (`htf_h1_aligned=true`) tienen menor MAE?

Ninguna de esas preguntas era respondible antes. Ahora cada señal acumula 22 dimensiones de contexto en Supabase.

---

## Arquitectura del cambio

```
Bar M1 cierra
    │
    ├─ BarState::on_bar_close()
    │       ├─ Actualiza ema_h4 (EMA-240M1 ≈ 4H)
    │       ├─ Computa htf_h1_trend ("Bull"/"Bear")
    │       ├─ Construye RbfGateContext  ──→ rbf_state.on_bar_close()
    │       └─ Construye AmdContext      ──→ amd_state.on_bar_close()
    │
    ├─ RangeBreakoutState::on_bar_close()
    │       └─ Emite RbfSignal (+ 13 campos nuevos)
    │                   ↓
    │           supabase_writer::rbf_signal_body()
    │                   ↓
    │           rbf_signals (Supabase)
    │
    └─ AmdDetectorState::on_bar_close()
            └─ Emite AmdSignal (+ 13 campos nuevos)
                        ↓
                supabase_writer::amd_signal_body()
                        ↓
                amd_signals (Supabase)
```

---

## FASE 1 — Métricas de absorción y flow en el evento

### 1.1 Absorption Score

**Campo:** `absorption_score: f64` (0–1) en `RbfSignal` y `delta_dz_at_spike/entry: f64` en `AmdSignal`

**Qué mide:** Intensidad de presión direccional normalizada. En RBF mide cuánto está el delta alineado con el breakout. En AMD mide si el spike tuvo sellers reales (spike alcista falso con delta negativo = manipulación).

**Fórmula RBF:**
```rust
absorption_score = (dz_dir.max(0.0) / 3.0).min(1.0)
// dz_dir = delta z-score en dirección del breakout
// 0 = sin presión, 1 = dz ≥ 3σ (evento extremo)
```

**Fórmula AMD** (`compute_delta_dz`, rolling 50 barras):
```rust
dz = (bar_delta - mean) / std
// Negativo en SpikeDir::Up = sellers dominando spike alcista = manipulación
```

**Campo adicional:** `bar_displacement: f64` (0–1)
```rust
bar_displacement = |close - open| / (high - low)
// 0 = pin bar (precio no se movió, todo absorbido)
// 1 = marubozu (engulfing, sin sombras)
```

Pin bar en breakout = gran volumen pero precio apenas se desplazó → absorción real.

---

### 1.2 OI Delta %

**Campo:** `oi_delta_pct: Option<f64>` en RBF; `oi_delta_pct_at_spike/entry: Option<f64>` en AMD

**Qué mide:** Cambio porcentual de Open Interest en la ventana reciente (~6 lecturas del WebSocket de Binance).

**Fórmula:**
```rust
oi_delta_pct = oi_delta / |front_value| * 100.0
// front_value = OI más antiguo en oi_history (capacity 6)
```

**Interpretación:**
- Positivo = contratos expandiéndose (nuevas posiciones, convicción real)
- Negativo en spike alcista = posiciones cerrándose (cobertura de shorts = movimiento falso)

---

### 1.3 CVD Divergencia Persistente

**Campo:** `cvd_divergence_bars: Option<i32>` en ambas señales

**Qué mide:** Barras consecutivas donde la dirección del precio y la del CVD divergen. Calculado en `BarState` como `cvd_divergence_bars: i32` actualizado cada barra.

**Codificación:**
- Negativo = bullish divergence (precio baja, CVD sube) → confirma reversión LONG
- Positivo = bearish divergence (precio sube, CVD cae) → confirma reversión SHORT
- |valor| alto = divergencia persistente = mayor convicción

---

## FASE 2 — Contexto estructural

### 2.1 VP Open Variant (RBF)

**Campo:** `vp_open_bias: Option<String>` en `RbfSignal`

**Qué mide:** Clasifica el tipo de día según cómo abrió la sesión respecto al Value Area del día anterior. El tracker `DailyVpTracker` ya calculaba esto — solo se expuso a la señal.

**Variantes:**
| Valor | Significado | Implicación para RBF |
|-------|-------------|---------------------|
| `TrendDay` | Apertura fuera del rango del día anterior | Breakouts tienen mayor probabilidad de follow-through |
| `InsideValue` | Apertura dentro del VA anterior | Día de rango — breakouts fallan más |
| `OutsideVaInsidePa` | Fuera del VA pero dentro del PA anterior | Tendencia hacia el POC |
| `FadeGap` | Gap + precio regresó al VA | Reversión hacia el valor |

---

### 2.2 VR Tier (RBF)

**Campo:** `vr_tier: u8` en `RbfSignal`

**Fórmula:**
```rust
vr_tier = if vr >= 4.0 { 3 } else if vr >= 3.0 { 2 } else { 1 }
```

Tier 1 = 2–3×, Tier 2 = 3–4×, Tier 3 = 4×+. Un breakout con 4× el volumen medio tiene naturaleza distinta a uno con 2×. La categorización permite análisis discreto sin asumir linealidad.

---

### 2.3 Calidad del Rango (RBF)

Tres métricas nuevas que caracterizan la consolidación previa al breakout:

**`range_touch_symmetry: f64`** (0–1)
```rust
hi_ratio = touches_high / (touches_high + touches_low)
symmetry = 1.0 - |hi_ratio - 0.5| * 2.0
```
- 1.0 = ambos lados tocados igual (rango simétrico = consolidación real)
- 0.0 = solo un lado tocado (trampa, falso rango)

**`cvd_per_bar: f64`**
```rust
cvd_per_bar = cvd_in_range / range_bars
```
Densidad de presión por barra. Alto valor absoluto = mucho CVD en pocas barras = acumulación intensa dentro del rango.

**`breakout_extension_pct: f64`**
```rust
// Long
extension = (close - range_high) / close * 100.0
// Short  
extension = (range_low - close) / close * 100.0
```
Un breakout que cierra 0.02% más allá del nivel es distinto a uno que cierra 0.15% más allá. Mayor extensión = mayor probabilidad de follow-through.

---

### 2.4 Kill Zones (AMD)

**Campos:** `is_kill_zone: bool`, `kill_zone_name: String` en `AmdSignal`

**Qué son:** Las Kill Zones son ventanas horarias donde la actividad institucional es máxima — London Open y NY Open. En esas ventanas la probabilidad de un spike de manipulación AMD es significativamente mayor.

**Implementación:**
```rust
let mins_utc = (timestamp_ms / 60_000).rem_euclid(24 * 60) as u32;
// London: 07:00–09:00 UTC
if mins_utc >= 7*60 && mins_utc < 9*60 { ("London", true) }
// New York: 13:30–15:30 UTC
else if mins_utc >= 13*60+30 && mins_utc < 15*60+30 { ("NewYork", true) }
```

---

### 2.5 Spike Quality Metrics (AMD)

Tres métricas que caracterizan la calidad del spike de manipulación:

**`bars_to_entry: usize`**  
Barras entre el spike y la barra de distribución/entry. 1 = setup inmediato (más limpio). Alto = precio anduvo lateral indefinido post-spike (señal de debilidad).

**`spike_extension_pct: f64`**
```rust
// SpikeDir::Up
extension = (spike_extreme - range_high) / range_high * 100.0
```
Qué tanto sobre-extendió el spike respecto al rango. Mayor extensión = más stops barridos = reversión más violenta esperada.

**`range_spike_ratio: f64`**
```rust
ratio = |cvd_in_range| / spike_extension_pct
```
Absorción institucional por punto de precio extendido. Alto = mucho CVD acumulado en el rango por cada % de extensión del spike = acumulación intensa antes del movimiento falso.

---

### 2.6 HTF H1 Estructura (ambas)

**Campos actuales:** `htf_h1_trend: Option<String>`, `htf_h1_aligned: Option<bool>` en `RbfSignal` y `AmdSignal`

**Implementación:** EMA exponencial de 240 barras M1 (≈ 4H) añadida a `BarState`:

```rust
// En BarState
ema_h4: f64,
ema_h4_bars: usize,  // warmup counter

// Actualización cada barra
const H4_ALPHA: f64 = 2.0 / (240.0 + 1.0);  // ≈ 0.00830
self.ema_h4 = self.ema_h4 * (1.0 - H4_ALPHA) + c * H4_ALPHA;

// Trend
htf_h1_trend = if c > self.ema_h1 { "Bull" } else { "Bear" }
// Disponible solo tras 240 barras de warmup (≈4h desde arranque)
```

**`htf_h1_aligned`:** `true` si Long+Bull o Short+Bear.

**Por qué:** Un RBF Short con H4 alcista tiene una dificultad estructural diferente. Operar contra la tendencia H4 puede tener menor WR o requerir mayor confluencia. Este campo permite segmentar la muestra.

---

## FASE 3 — Scripts de análisis estadístico

Todos en `scripts/`. Requieren `SUPABASE_URL` y `SUPABASE_KEY` en el entorno.

### `feature_importance.py`

```bash
python scripts/feature_importance.py --strategy rbf
python scripts/feature_importance.py --strategy amd
```

Entrena `RandomForestClassifier` (target: WR > 0) y `RandomForestRegressor` (target: result_r) con cross-validation. Reporta:
- ROC-AUC CV (clasificador) y R² CV (regresor)
- Top-10 feature importances por impurity
- Top-10 permutation importances (más honesta, mide degradación real al aleatorizar cada feature)
- Guarda JSON en `docs/amd/feature_importance_{strategy}.json`

**Cuándo usar:** n ≥ 50 señales cerradas por estrategia.

---

### `correlation_analysis.py`

```bash
python scripts/correlation_analysis.py --strategy rbf
python scripts/correlation_analysis.py --strategy amd --min_n 15
```

Para cada feature numérico:
- **Spearman ρ**: correlación monotónica, robusta a outliers. p < 0.05 = estadísticamente significativo.
- **Point-biserial**: correlación con variable binaria WR.
- **Segmentación por cuartiles Q1–Q4**: muestra WR y AvgR en cada cuarto de la distribución. Permite detectar relaciones no lineales (ej. el Q4 explota pero Q1–Q3 son iguales).

**Cuándo usar:** n ≥ 30.

---

### `signal_calibration.py`

```bash
python scripts/signal_calibration.py --strategy rbf
python scripts/signal_calibration.py --strategy amd --min_n 15
```

Para cada feature con dirección conocida (ej. `absorption_score >= X`), hace grid search en percentiles 10%–90% y encuentra el umbral que maximiza AvgR en el subconjunto filtrado. Marca con ★ los candidatos a gate (delta AvgR > +0.1R y n_filtered ≥ min_n).

**Output directo a gates:** Si `absorption_score >= 0.45` da AvgR +0.35 vs baseline +0.08, ese umbral es candidato a añadir en `config/strategy.toml`.

**Cuándo usar:** n ≥ 30. Con n < 30 los umbrales son ruido estadístico.

---

## FASE 4 — Score continuo + sizing dinámico

### `signal_score_v2: f64` (0–1)

Score experimental que pondera las features más relevantes en el momento del trade.

**Fórmula RBF:**
```
score = absorption_score × 0.25
      + (vr_tier - 1) / 2 × 0.20
      + min(breakout_extension_pct, 0.10) / 0.10 × 0.15
      + htf_h1_aligned × 0.15
      + confluence_score / 6 × 0.25
```

**Fórmula AMD:**
```
score = quality_score / 10 × 0.30
      + min(|delta_dz_at_spike|, 3) / 3 × 0.20
      + is_kill_zone × 0.15
      + (6 - bars_to_entry) / 5 × 0.15
      + htf_h1_aligned × 0.20
```

**`sizing_multiplier: f64`:**
| Score | Multiplicador |
|-------|--------------|
| < 0.30 | 0.5× |
| 0.30–0.50 | 1.0× |
| 0.50–0.70 | 1.5× |
| ≥ 0.70 | 2.0× |

> **⚠️ IMPORTANTE:** Los pesos son especulativos — diseñados desde la lógica del orderflow, no calibrados con datos reales. El `sizing_multiplier` **no se aplica automáticamente** al paper trader. Se graba para validar correlación `signal_score_v2 vs result_r` cuando n ≥ 30. Solo activar sizing dinámico cuando esa correlación sea estadísticamente significativa (p < 0.05, ρ > 0.3).

---

## FASE 5 — Monitoreo continuo

### `daily_report.py`

```bash
python scripts/daily_report.py           # últimas 24h
python scripts/daily_report.py --days 7  # últimos 7 días
```

Muestra por estrategia: n señales, WR, AvgR, TotalR, P&L estimado en USD ($50 capital, 10× leverage), tabla de últimos 10 trades con exit_reason y signal_score_v2.

Alertas automáticas:
- 3+ pérdidas consecutivas recientes
- Drawdown del día < −3R

---

### `anomaly_detection.py`

```bash
python scripts/anomaly_detection.py --strategy rbf --window 10
python scripts/anomaly_detection.py --strategy amd --window 8
```

Compara las últimas `window` señales contra el histórico:

1. **WR rolling drift**: WR de la ventana < 30% o cayó > 20pp vs histórico → alerta
2. **Feature drift (KS test)**: Kolmogorov-Smirnov entre distribución histórica vs reciente. p < 0.05 → distribución del feature cambió significativamente (posible cambio de régimen o bug en el pipeline)
3. **Distribución de régimen/sesión**: cambios > 20pp en la proporción de cada categoría
4. **Score drift**: `signal_score_v2` medio cayó > 15pp vs histórico

---

## Migrations SQL

Todas en `migrations/`. Usar `ADD COLUMN IF NOT EXISTS` — idempotentes, se pueden aplicar en cualquier orden.

| Archivo | Campos | Estrategia |
|---------|--------|-----------|
| `rbf_absorption_score.sql` | `absorption_score`, `bar_displacement` | RBF |
| `amd_delta_dz.sql` | `delta_dz_at_spike`, `delta_dz_at_entry` | AMD |
| `oi_delta_pct.sql` | `oi_delta_pct` (RBF), `oi_delta_pct_at_spike/entry` (AMD) | Ambas |
| `cvd_divergence_bars.sql` | `cvd_divergence_bars` | Ambas |
| `rbf_quality_metrics.sql` | `vr_tier`, `range_touch_symmetry`, `cvd_per_bar`, `breakout_extension_pct` | RBF |
| `amd_kill_zone_spike_quality.sql` | `is_kill_zone`, `kill_zone_name`, `bars_to_entry`, `spike_extension_pct`, `range_spike_ratio` | AMD |
| `rename_htf_h4_to_h1.sql` | `htf_h1_trend`, `htf_h1_aligned` | Ambas |
| `rbf_vp_open_bias.sql` | `vp_open_bias` | RBF |
| `signal_score_v2.sql` | `signal_score_v2`, `sizing_multiplier` | Ambas |

---

## Roadmap de uso

```
Ahora (n < 30)
└─ daily_report.py diario
└─ anomaly_detection.py semanal
└─ Los 22 campos se acumulan en Supabase

Cuando n ≥ 30 por estrategia
└─ correlation_analysis.py → identificar features con ρ significativo
└─ signal_calibration.py  → candidatos concretos a gates

Cuando n ≥ 50
└─ feature_importance.py  → validar con RF cuáles predicen realmente
└─ Recalibrar pesos de signal_score_v2 con regresión

Cuando correlación signal_score_v2 vs result_r sea significativa
└─ Activar sizing_multiplier en el paper trader
└─ Medir impacto en AvgR vs WR (sizing dinámico puede mejorar AvgR a costa de WR)
```

---

*Commit: `2d498fb` · Branch: `main` · 2026-06-09*
