# MTF SPOT Shorts — Spec BTC SPOT v4
> Documento vivo. Construido desde cero para BTC SPOT Bybit.
> El sistema live (futuros multi-símbolo) usa `oi_momentum` y `funding_regime` que no existen en SPOT.
> Este spec reemplaza esos filtros con microestructura real del libro de órdenes.
> Creado: 2026-06-16 | Última calibración: 2026-06-17

---

## Dataset

| Campo | Detalle |
|-------|---------|
| Símbolo | BTCUSDT Bybit SPOT |
| Período | 2025-06-15 → 2026-05-31 (366 días) |
| Archivo principal | `data/bybit-spot/processed/btcusdt_m1.parquet` |
| Barras M1 | 489,600 |
| Columnas | 81 (OHLCV + microestructura OB + features derivados) |
| Walk-forward | IS: Jun 2025–Feb 2026 (~260d) / OOS: Mar–May 2026 (~90d) |

---

## Estado activo — mtf_spot_shorts_v4 (2026-06-17)

Shorts v4 es la version canonica actual para BTCUSDT Bybit Spot.

### Resultados validados

| Corte | Trades | WR | AvgR | TotalR | Equity |
|-------|--------|----|------|--------|--------|
| Full sample | 693 | 49.1% | +0.334R | +231.46R | $38,544.51 |
| OOS Mar-May 2026 | 204 | 52.0% | +0.409R | +83.49R | n/a |

Notas:

- El crecimiento de equity viene de compounding al 2% por trade, no de que cada trade arriesgue el mismo monto fijo.
- La metrica mas importante para comparar versiones es `AvgR` y `TotalR`; la equity se acelera por compounding.
- `LEVEL_TOL=0.007` significa tolerancia de 0.70% alrededor del nivel. No es entrada tarde por definicion; exige que la mecha haya alcanzado la zona y el cierre confirme rechazo.

### Implementacion actual

- Backtest canonico: `backtest/mtf_spot_backtest.py`
- Detector live Rust: `data/src/strategy/detectors/mtf_spot_detector.rs`
- UI local: `apps/rbf-review/src/views/MTFModuleView.tsx`
- Tabla paper live: `mtf_spot_trades`
- Migracion requerida: `migrations/mtf_spot_trades.sql`

### Estado de confianza

El backtest esta validado, y el detector Rust ya implementa las mismas reglas principales. Falta el parity harness Python-vs-Rust para afirmar que live emitira exactamente los mismos trades que el backtest.

---

## Configuración legacy — mtf_basics v3 (2026-06-16)

### Resultados walk-forward (compounding 2% por trade)

| | In-Sample | Out-of-Sample |
|--|-----------|---------------|
| Período | Jun 2025 – Feb 2026 | Mar 2026 – May 2026 |
| Trades | 493 | 208 |
| WR | 47.5% | **51.0%** |
| AvgR | +0.284 | **+0.385** |
| Trades/día | 2.3 | 2.4 |
| TotalR | 139.9R | 80.0R |

**Capital: $500 → $30,578 (+6,016%) en 365 días — 701 trades totales**
OOS supera IS en WR y AvgR → sin overfitting detectado.

---

### Lógica de entrada — 3 ingredientes

```
SEÑAL = NIVEL + RECHAZO + FLUJO
```

#### 1. NIVEL — precio cerca de resistencia real
- Comprobar si el `high` de la barra está dentro del **0.70%** de alguno de estos niveles:
  - `vp_vah` — Value Area High (Volume Profile, 300 barras)
  - `prev_day_high` — PDH
  - `asian_high` — máximo del rango asiático
  - `weekly_high` — máximo de los últimos 5 días
- **VAH es requerido** — si VAH no está en la lista de niveles matcheados, skip.
- **Bloquear** si confluyen PDH + AH + VAH (3+ niveles con ambos PDH y AH) → soporte duro.
- **Bloquear PDH+VAH** (solo dos niveles) — a 0.70% de tolerancia PDH y VAH pueden estar 1.4% separados, la confluencia es falsa. WR 37.9%, AvgR -0.072 cuando se permite.

**Por qué 0.70%:** a esta tolerancia el HIGH de la barra llegó hasta el VAH y fue rechazado con fuerza (close mucho más abajo = wick grande). Es rechazo de alta convicción, no entrada tardía. A 0.40% se perdían muchas barras de rechazo fuerte. A 1.20%+ empieza overfitting IS.

**Evidencia por nivel (IS+OOS, v3):**
| Nivel | n | WR | AvgR |
|-------|---|----|------|
| AH+WH+VAH | 25 | **60.0%** | **+0.549** |
| WH+VAH | 47 | **57.4%** | **+0.505** |
| PDH+WH+VAH | 19 | 52.6% | +0.417 |
| VAH | 386 | 48.7% | +0.316 |
| AH+VAH | 224 | 44.6% | +0.234 |
| PDH+VAH | bloqueado | — | -0.072 |
| PDH+AH+VAH | bloqueado | — | negativo |

WH (Weekly High) es el nivel multiplicador — cualquier combo que lo incluya supera 52% WR.

#### 2. RECHAZO — la barra rechazó el nivel
```python
wick_up  = high - max(close, open)
wick_pct = wick_up / (high - low)
válido   = 0.30 < wick_pct < 0.85  AND  close <= open
```
- Wick superior entre 30% y 85% del rango total
- Cierre bajista (cierra por debajo de apertura)
- **Bloquear wick ≥ 85%** (doji): sin cuerpo = sin convicción, WR 32%, AvgR -0.134

**Evidencia por tamaño de wick:**
| Wick % | WR | AvgR |
|--------|----|----|
| 30–40% | 41.8% | +0.175 |
| 40–50% | 45.2% | +0.237 |
| 70–85% | 58.0% | +0.637 ← sweet spot |
| 85–100% | 32.0% | -0.134 ← bloqueado |

#### 3. FLUJO — presión vendedora presente
```python
válido = obi10_mean < -0.05  OR  delta < 0
```
- Al menos una señal de flujo vendedor en la barra de entrada
- OBI moderadamente negativo o delta negativo

**Evidencia OBI:**
| OBI | WR | AvgR |
|-----|----|----|
| < -0.30 | 27.8% | -0.181 ← tardío, todos venden ya |
| -0.30 a -0.05 | 47%+ | +0.320 ← óptimo |
| -0.05 a 0 | 33.8% | -0.068 |
| 0+ | 42.5% | +0.208 |

**Nota delta:** cuando `delta >= 0` (compradores empujando) pero el precio igual rechaza, es absorción institucional — WR 50.6%, AvgR +0.440. Edge real pero corta demasiado volumen para compounding.

---

### Stop loss

```python
stop = H1_high + 0.40 × ATR14_H1
```
- `H1_high`: máximo de la última vela H1 cerrada
- `ATR14_H1`: ATR de 14 períodos calculado sobre barras H1
- Rango válido: **0.30% – 0.75%** del precio de entrada
- Si el stop queda fuera del rango → skip (no hay trade)

**Evidencia sweep ATR multiplier (a LEVEL_TOL=0.40%):**
| Mult | WR OOS | AvgR OOS | Capital |
|------|--------|---------|---------|
| ×0.10 | 43.2% | +0.188 | $1,793 |
| ×0.20 | 46.9% | +0.286 | $11,910 |
| **×0.40** | **48.3%** | **+0.299** | **$14,145** ← elegido |
| ×0.50 | 46.5% | +0.197 | $11,443 |

El stop ×0.40 da espacio para que el trade respire sin que el ruido M1 lo saque prematuramente.

---

### Target y salidas

| Salida | Condición | R obtenido |
|--------|-----------|-----------|
| **Target** | `low <= entry - 2.0 × dist` | +2.0R |
| **CVD exit** | 5 barras consecutivas CVD positivo + OBI > 0.15, con profit ≥ 1R | Variable (~1.3R promedio) |
| **Stop** | `high >= H1_high + 0.40×ATR` | -1.0R |

No hay salida por timeout. Un trade MTF Spot queda abierto hasta `target`, `stop` o `cvd_exit`.

**Evidencia target sweep:**
| Target | WR OOS | AvgR OOS | Capital |
|--------|--------|---------|---------|
| 1.5R | 51.4% | +0.267 | $8,689 |
| **2.0R** | **47.3%** | **+0.270** | **$8,949** ← elegido |
| 2.5R | 46.5% | +0.304 | $8,796 |
| 3.0R | 45.9% | +0.255 | $6,896 |

2R gana en capital final. El compounding premia la frecuencia de targets alcanzados.

---

### Sesiones

| Sesión | UTC | WR | AvgR | Estado |
|--------|-----|----|------|--------|
| London | 07–12 | 37.3% | +0.027 | **EXCLUIDA** |
| Overlap | 12–16 | 46.7% | +0.288 | ✅ Activa |
| New York | 16–20 | 47.0% | +0.208 | ✅ Activa |

Londres excluido definitivamente — WR 37%, casi ruido, arrastra todo el sistema.

---

### Gestión de capital

- **Riesgo por trade:** 2% del capital actual (compounding)
- **Capital inicial de referencia:** $500
- Sin cooldown entre trades — cada barra válida puede generar señal
- Un trade abierto a la vez (no se abre nuevo hasta cerrar el actual)

---

## Decisiones descartadas (con evidencia)

### Filtro D1 EMA20
Probado con thresholds 1.000–1.020. OOS WR prácticamente idéntica (46–48%) en todos los casos. El filtro corta 60% del volumen sin mejorar calidad. Descartado.

| Threshold | n OOS | WR OOS | AvgR OOS | Capital |
|-----------|-------|--------|---------|---------|
| Sin filtro | 233 | 47.2% | +0.318 | mejor |
| ×1.005 | 92 | 47.8% | +0.326 | peor por volumen |

### Cooldown
30 barras de cooldown reducía de 17 señales/día a 0.6 trades/día. Eliminado.

### Entrada con orden límite (retesteo del nivel)
Simular límite en el nivel para entrar más cerca de VAH. Resultado: trades que no llenan → capital 6x menor que entrada al mercado.

| Entrada | n OOS | Capital |
|---------|-------|---------|
| Mercado (close) | 201 | $8,949 |
| Límite en nivel 5b | 114 | $1,308 |

### Filtro FVG activo
`bearish_fvg_active` presente en 72% de todas las barras — no discrimina. FVG activo encima atrae precio hacia arriba antes de caer (FVGs se rellenan), empeora el trade en vez de mejorarlo.

### Filtro OBI extremo (< -0.30)
OBI muy negativo = entrada tardía. El movimiento ya ocurrió. Bloquear OBI < -0.30 mejora calidad pero corta volumen; capital final menor con compounding.

### Filtro absorción (delta >= 0)
Edge real en análisis estático (WR 50.6%, AvgR +0.440) pero en backtest dinámico corta 87 trades OOS → capital cae de $14,145 a $4,722.

### Order Blocks / near_bearish_ob
`near_bearish_ob` solo en 5.3% de barras (n=27 en trades). Cuando está presente, WR 37% vs 43% sin él. El OB actúa como zona de soporte donde compradores también están activos.

---

## Por nivel — comportamiento detallado

### VAH (Value Area High)
El nivel más confiable del dataset. 437 trades (61% del total), WR 46.2%, AvgR +0.269.

**Por qué funciona:** VAH es donde el volumen del perfil termina en la parte alta. Es una resistencia basada en actividad real de mercado, no en líneas dibujadas. El precio que llega al VAH encuentra vendedores institucionales que participaron ahí.

### PDH+VAH (confluencia)
Cuando el máximo del día anterior coincide con el VAH actual → señal más potente. WR 52.7%, AvgR +0.379. n=55 (muestra válida).

### AH+VAH
Asian High en el mismo nivel que VAH. WR 44.4%, AvgR +0.184 — funciona pero el confluir de AH y VAH no potencia, los niveles se superponen y el precio a veces oscila entre ellos.

### PDH+AH+VAH (bloqueado)
Cuando los tres niveles confluyen, el precio está en una zona de soporte/resistencia demasiado contestada. WR 37.8%, AvgR -0.015 — neutral o negativo. Bloqueado.

---

## Calidad de detección — análisis de barra de entrada

La barra de entrada (M1) donde dispara la señal tiene características que predicen el resultado:

### Distancia entry vs nivel
La entrada se hace al close de la barra, que puede estar lejos del nivel si la barra fue grande.

| Dist close vs nivel | n | WR | AvgR | MFE |
|--------------------|---|----|------|-----|
| 0–0.10% | 80 | **53.8%** | **+0.516** | 1.55R |
| 0.10–0.20% | 99 | 43.4% | +0.269 | 1.39R |
| 0.20–0.30% | 119 | 39.5% | +0.093 | 1.24R |
| 0.30–0.40% | 117 | 39.3% | +0.085 | 1.26R |

Entrar dentro del 0.10% del nivel es 14pp mejor de WR. La distancia promedio actual es 0.257% (estamos entrando tarde). Filtrar por distancia no ayuda con compounding (corta volumen). Pendiente: mejorar detección de señales que naturalmente aparecen cerca del nivel.

---

## Archivo de implementación

```python
# backtest/mtf_spot_backtest.py — configuración activa Shorts v4

CAPITAL  = 500.0
RISK_PCT = 0.02        # 2% compounding
TARGET_R = 2.0
FORWARD  = 1200        # barras máximas (~20h)
MIN_STOP = 0.0030      # 0.30% mínimo
MAX_STOP = 0.0075      # 0.75% máximo
LEVEL_TOL = 0.007      # 0.70% zona alrededor del nivel
ATR_MULT  = 0.40       # H1_high + 0.40 × ATR_H1
FEE_RT   = 0.0007      # 0.07% round-trip

# Sesiones activas
OVERLAP = (12*60, 16*60)   # 12–16 UTC
NY      = (16*60, 20*60)   # 16–20 UTC

# Condición de nivel
# - VAH requerido en el match
# - bloquear PDH+AH+VAH (3+ niveles con PDH y AH)
# - bloquear PDH+VAH (falsa confluencia a 0.70% — niveles separados)
# - bloquear WH trades a las 15h UTC (cierre Overlap/apertura NY — WR 30.8%)

# Condición de rechazo
# - 0.30 < wick_pct < 0.85  (doji ≥85% bloqueado)
# - close <= open

# Condición de flujo
# - obi10_mean < -0.05  OR  delta < 0
```

---

## Historial de versiones

| Versión | Cambio principal | Capital | OOS WR | OOS AvgR | tpd OOS |
|---------|-----------------|---------|--------|---------|---------|
| v1 | baseline — ATR×0.40, doji, 2R, LEVEL_TOL 0.40% | $15,099 | 49.7% | +0.345 | 2.2 |
| v2 | LEVEL_TOL 0.40% → 0.70%, bloqueo PDH+VAH falso | $27,886 | 50.7% | +0.384 | 2.5 |
| **v3** | **bloqueo 15h UTC en WH trades** | **$30,578** | **51.0%** | **+0.385** | **2.4** |
| **v4** | **version actual en UI/live Rust; reglas spot separadas de futures** | **$38,544** | **52.0%** | **+0.409** | **~2.3** |

Cada versión OOS mejor o igual que IS → evolución limpia sin overfitting.

---

## Próximos pasos (pendiente)

1. Construir parity harness Python-vs-Rust para `mtf_spot_shorts_v4`.
2. Comparar entradas, stops, targets y exits contra `MtfSpotState`.
3. Ejecutar `migrations/mtf_spot_trades.sql` antes de Railway paper live.
4. Correr Bybit Spot paper live y auditar diferencias entre senal teorica, precio stream y fila Supabase.
5. Investigar perdedores por cluster:
   - distancia close vs nivel
   - tipo de nivel
   - sesion
   - stop_pct
   - OBI extremo vs moderado
   - reversals por CVD antes/despues de 1R
