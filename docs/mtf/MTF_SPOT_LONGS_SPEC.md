# MTF Spot Longs - Spec BTC SPOT v1

Documento vivo para BTCUSDT Bybit Spot. Longs v1 nace como el espejo operativo de Shorts, pero no se debe asumir simetria perfecta: los niveles, sesiones y comportamiento de flujo se validan por backtest.

Creado: 2026-06-17

---

## Dataset

| Campo | Detalle |
|-------|---------|
| Simbolo | BTCUSDT Bybit SPOT |
| Periodo | 2025-06-15 -> 2026-05-31 |
| Archivo principal | `data/bybit-spot/processed/btcusdt_m1.parquet` |
| Timeframe | M1 |
| Walk-forward | IS: Jun 2025-Feb 2026 / OOS: Mar-May 2026 |

---

## Estado activo - mtf_spot_longs_v1

### Resultados validados

| Corte | Trades | WR | AvgR | TotalR | Equity |
|-------|--------|----|------|--------|--------|
| Full sample | 578 | 52.4% | +0.354R | +204.38R | $23,971.18 |
| OOS Mar-May 2026 | 161 | 54.7% | +0.403R | +64.91R | n/a |

La equity reportada usa compounding al 2% por trade. Para evaluar edge, priorizar `AvgR`, `WR`, `TotalR`, estabilidad OOS y cantidad de trades.

---

## Logica de entrada

```
SENAL = NIVEL + RECHAZO + FLUJO
```

### 1. Nivel - precio cerca de soporte real

La barra debe tocar una zona de soporte dentro de `LEVEL_TOL=0.007` (0.70%):

- `vp_val` - Value Area Low
- `prev_day_low` - PDL
- `asian_low` - minimo del rango asiatico
- `weekly_low` - minimo rolling de 5 dias

Reglas:

- VAL es requerido.
- Se busca rechazo desde soporte, no compra en breakout.
- La confluencia se evalua con los niveles inferiores, no con los niveles de resistencia usados por shorts.

### 2. Rechazo - la barra defendio el nivel

```python
wick_down = min(open, close) - low
wick_pct = wick_down / (high - low)
valid = 0.30 < wick_pct < 0.85 and close >= open
```

La barra debe tener mecha inferior clara y cierre alcista. Dojis extremos se bloquean porque no muestran conviccion suficiente.

### 3. Flujo - presion compradora presente

```python
valid = obi10_mean > 0.05 or delta > 0
```

Se acepta OBI positivo moderado o delta positivo. Igual que en Shorts, flujo extremo no siempre es mejor: puede significar entrada tardia.

---

## Stop, target y salidas

```python
stop = H1_low - 0.40 * ATR14_H1
```

Parametros:

- `MIN_STOP_PCT = 0.0030`
- `MAX_STOP_PCT = 0.0075`
- `TARGET_R = 2.0`
- `FEE_RT = 0.0007`

Salidas:

| Salida | Condicion | R |
|--------|-----------|---|
| Target | `high >= entry + 2.0 * dist` | +2.0R |
| CVD exit | 5 barras con CVD negativo + OBI < -0.15 y profit >= 1R | variable |
| Stop | `low <= H1_low - 0.40 * ATR14_H1` | -1.0R |

No hay salida por timeout. Un trade MTF Spot queda abierto hasta `target`, `stop` o `cvd_exit`.

---

## Sesiones

Longs v1 opera en ventana 14:00-20:00 UTC.

Motivo: el long spot necesita liquidez y direccion. La sesion asiatica y el inicio de Londres no dieron la misma calidad en el backtest.

---

## Implementacion actual

- Backtest canonico: `backtest/mtf_spot_longs_backtest.py`
- Detector live Rust: `data/src/strategy/detectors/mtf_spot_detector.rs`
- UI local: `apps/rbf-review/src/views/MTFModuleView.tsx`
- Endpoint UI/backtest: soporte `mtf_spot_longs_btc`
- Tabla paper live: `mtf_spot_trades`

El detector Rust comparte estado con Shorts v4, pero la direccion, niveles, wick, flujo, stop y CVD exit son especificos para Longs.

---

## Relacion con Shorts

Longs son el espejo estructural de Shorts, pero no una copia ciega.

| Shorts | Longs |
|--------|-------|
| Resistencia | Soporte |
| VAH requerido | VAL requerido |
| PDH/AH/WH | PDL/AL/WL |
| Mecha superior | Mecha inferior |
| Cierre bajista | Cierre alcista |
| OBI < -0.05 o delta < 0 | OBI > 0.05 o delta > 0 |
| Stop sobre H1 high | Stop bajo H1 low |
| CVD positivo + OBI > 0.15 cierra short | CVD negativo + OBI < -0.15 cierra long |

---

## Pendiente antes de paper live serio

1. Construir parity harness Python-vs-Rust.
2. Confirmar que `MtfSpotState` genera las mismas entradas que `backtest/mtf_spot_longs_backtest.py`.
3. Validar diferencias de VAH/VAL calculado en live vs parquet/backtest.
4. Correr varios dias en paper live y comparar fills teoricos con precio real del stream.

Hasta completar eso, Longs v1 queda en estado **backtest validado + paper live preparado**.
