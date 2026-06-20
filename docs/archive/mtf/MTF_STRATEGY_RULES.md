# MTF Mined Signal Strategy — Reglas Exactas
> Congelado 2026-06-14. No modificar hasta walk-forward ~2026-07-05.
> Criterio pass: WR ≥ 55% y AvgR ≥ +0.30R en datos nuevos.

---

## Descripción

Sistema multi-timeframe (D1/H4 → H1 → M1) que combina:
- **Filtro de tendencia** en timeframe alto (D1 para shorts, H4 para longs)
- **Stop estructural** anclado al extremo horario (H1) + buffer ATR
- **Entrada** en patrones M1 mineados estadísticamente sobre 8,700+ barras reales
- **Salida** por TP fijo 2.5R, stop hit, o agotamiento de flujo CVD/OBI

---

## Parámetros globales

| Parámetro | Valor | Nota |
|---|---|---|
| Target R | 2.5R | fijo en todos los símbolos |
| Stop mínimo | 0.30% del entry | filtra ruidos de microestructura |
| Stop máximo | 0.75% del entry | evita stops demasiado amplios (edge cae) |
| Cooldown | 30 barras M1 | desde el último trade cerrado o señal emitida |
| Forward max | 1200 barras M1 | = 20 horas; cierra por EXPIRED si no resuelve |
| Fees | 0.07% round-trip | taker × 2; descontado como `fee_r = 0.07% × entry / risk` |
| Sesiones permitidas | London, LondonNyOverlap, NewYork | Asia y OffHours bloqueadas |

---

## SHORTS

### 1. Filtro de tendencia D1

- Se calcula **EMA20 de cierres D1** (velas diarias reales de Binance, no proxy M1).
- EMA20 se actualiza al cierre de cada día UTC (barra M1 de las 23:59 UTC).
- Al arrancar el monitor se hace seed con las últimas 30+ velas D1 via REST.

| Condición | Resultado |
|---|---|
| `precio > EMA20_D1 × 1.005` | tendencia **bull** → señal BLOQUEADA |
| `precio < EMA20_D1 × 0.995` | tendencia **bear** → señal PERMITIDA |
| entre ambos | tendencia **neutral** → señal PERMITIDA |

### 2. Stop estructural H1

- Se agregan barras M1 en **buckets H1 exactos UTC** (no rolling 60 barras).
- `h1_high` = máximo de todas las barras M1 de la hora UTC actual.
- `ATR_H1` = promedio de (high - low) de las últimas 14 velas H1 completas.

```
stop_price  = h1_high + 0.3 × ATR_H1
risk        = stop_price - entry_price
stop_pct    = risk / entry_price × 100

RECHAZAR si stop_pct < 0.30% o stop_pct > 0.75%
```

### 3. Patrones M1 (por símbolo)

Todos requieren sesión ≠ Asia y ≠ OffHours.

#### BTCUSDT
Requiere sesión London o NewYork.

| Patrón | Condición | SIG key |
|---|---|---|
| Shooting Star + Ask + OBI | `is_shoot AND absorption=="Ask" AND obi_fast < 0` | `btc:shoot+ask+obi` |
| Shooting Star + London | `is_shoot AND session=="London"` | `btc:shoot+london` |

#### ETHUSDT

| Patrón | Condición | SIG key |
|---|---|---|
| NY + OI + Equal High | `session=="NewYork" AND oi_momentum AND equal_high` | `eth:ny+oi+eq` |
| Ask + London + Expansión | `absorption=="Ask" AND London AND regime=="Expansion"` | `eth:ask+london+exp` |
| Ask + VPIN + NY | `absorption=="Ask" AND vpin > 0.6 AND session=="NewYork"` | `eth:ask+vpin+ny` |

#### SOLUSDT

| Patrón | Condición | SIG key |
|---|---|---|
| NY + VR>4 + OI | `session=="NewYork" AND vr > 4.0 AND oi_momentum` | `sol:ny+vr4+oi` |
| NY + VR>4 + Equal High | `session=="NewYork" AND vr > 4.0 AND equal_high` | `sol:ny+vr4+eq` |
| Equal High + London + Expansión | `equal_high AND London AND regime=="Expansion"` | `sol:eq+london+exp` |

#### BNBUSDT
Solo NewYork (London WR=30-42% en todos los patrones, calibrado 2026-06-14).

| Patrón | Condición | SIG key |
|---|---|---|
| Equal High + OI + NY | `equal_high AND oi_momentum AND session=="NewYork"` | `bnb:eq+ny+oi` |
| OI + NY | `oi_momentum AND session=="NewYork"` | `bnb:oi+ny` |

#### XRPUSDT
Solo NewYork (London WR=26-39%, calibrado 2026-06-14).

| Patrón | Condición | SIG key |
|---|---|---|
| Equal High + OI + NY | `equal_high AND oi_momentum AND session=="NewYork"` | `xrp:eq+ny+oi` |
| Ask + NY | `absorption=="Ask" AND session=="NewYork"` | `xrp:ask+ny` |
| OI + NY | `oi_momentum AND session=="NewYork"` | `xrp:oi+ny` |

### Definición de indicadores usados en shorts

| Indicador | Definición |
|---|---|
| `is_shoot` | wick_hi/rng > 0.45 AND body/rng < 0.40 (wick_hi = high - max(open,close)) |
| `absorption=="Ask"` | vendedores absorbidos; CVD diverge negativamente vs precio |
| `obi_fast` | Order Book Imbalance rápido (bid-ask / total); negativo = presión vendedora |
| `vr` | Volume Ratio = volumen barra / media últimas N barras |
| `vpin` | Volume-synchronized Probability of Informed Trading; >0.6 = flujo tóxico |
| `oi_momentum` | Open Interest aumentando (true/false) |
| `equal_high` | doble techo detectado en las últimas barras M1 |
| `regime=="Expansion"` | fase de expansión del rango detectada por AMD detector |

---

## LONGS

### 1. Filtro de tendencia H4

- Se calcula **EMA20 de cierres H4** (velas H4 reales de Binance).
- Se actualiza al cerrar cada bucket de 4 horas UTC.

| Condición | Resultado |
|---|---|
| `precio < EMA20_H4 × 0.995` | tendencia **bear** → señal BLOQUEADA |
| `precio > EMA20_H4 × 1.005` | tendencia **bull** → señal PERMITIDA |
| entre ambos | tendencia **neutral** → señal PERMITIDA |

> Nota: Para longs se usa H4 (no D1) para capturar recuperaciones intraday en períodos D1 bear.

### 2. Stop estructural H1

```
stop_price  = h1_low - 0.3 × ATR_H1
risk        = entry_price - stop_price
stop_pct    = risk / entry_price × 100

RECHAZAR si stop_pct < 0.30% o stop_pct > 0.75%
```

### 3. Patrones M1 (por símbolo)

#### ETHUSDT (mineado sobre 8,731 barras, WR=54.2%, AvgR=+0.412R)

| Patrón | Condición | SIG key |
|---|---|---|
| Stacked Bull + London | `stacked_imb=="Bullish" AND London` | `eth:stacked_bull+london` |
| Stacked Bull + NY | `stacked_imb=="Bullish" AND session=="NewYork"` | `eth:stacked_bull+ny` |
| Hammer + DZ buy | `is_hammer AND dz > 0.5` | `eth:hammer+dz_buy` |
| Equal Low + London + Expansión | `equal_low AND London AND regime=="Expansion"` | `eth:eq_low+london+exp` |
| OI + NY | `oi_momentum AND session=="NewYork"` | `eth:oi+ny` |

#### SOLUSDT (WR=59.5%, AvgR=+0.753R)

| Patrón | Condición | SIG key |
|---|---|---|
| Hammer + London | `is_hammer AND London` | `sol:hammer+london` |
| Stacked Bull + London | `stacked_imb=="Bullish" AND London` | `sol:stacked_bull+london` |
| Hammer + OBI positivo | `is_hammer AND obi_l5 > 0.2` | `sol:hammer+obi_pos` |
| OI + London | `oi_momentum AND London` | `sol:oi+london` |
| Equal Low + London | `equal_low AND London` | `sol:eq_low+london` |

#### BTC / BNB / XRP
Sin edge en longs según minería 2026-06-14. No generan señales.

### Definición de indicadores adicionales en longs

| Indicador | Definición |
|---|---|
| `is_hammer` | wick_lo/rng > 0.45 AND body/rng < 0.40 (wick_lo = min(open,close) - low) |
| `stacked_imb=="Bullish"` | 2+ imbalances alcistas apilados en la misma zona de precio |
| `equal_low` | doble suelo detectado en las últimas barras M1 |
| `obi_l5` | OBI promedio de las últimas 5 barras |
| `dz` | Delta Z-score de CVD (desviaciones estándar del CVD vs su media) |

---

## Gestión del trade (igual para shorts y longs)

### Targets y stop
```
target = entry ± TARGET_R × risk    (TARGET_R = 2.5)
stop   = calculado en entrada (H1 extremo ± 0.3×ATR)
```

### Orden de prioridad de cierre en cada barra M1

1. **TAKE_PROFIT** — precio toca target (gross_r = +2.5R)
2. **STOP_LOSS** — precio toca stop (gross_r = -1.0R)
3. **CVD_EXHAUSTION** — las tres condiciones juntas:
   - `cvd_streak ≥ 5` (5 barras consecutivas con CVD en dirección contraria)
   - `obi_streak ≥ 1` (al menos 1 barra con OBI flipping: |obi_fast| > 0.15 en contra)
   - `curr_r ≥ 1.0R` (solo salir con ganancia ≥ 1R)
4. **EXPIRED** — 1200 barras M1 sin resolución (≈20h) → cierra al precio de mercado

### Resultado neto
```
result_r = gross_r - fee_r
fee_r    = 0.07% × entry / risk    (varía: stop más ajustado = fee_r más alto en R)
```

---

## Microestructura registrada en entrada (para auditoría futura)

Estos campos se guardan en Supabase pero **no son gates** activos — se acumulan para análisis posterior:

| Campo | Descripción |
|---|---|
| `obi_entry` | OBI fast en el momento de la señal |
| `cvd_slope_entry` | Pendiente del CVD en la señal |
| `dz_score` | Delta Z-score del CVD en la señal |
| `stacked_imb` | Estado de imbalances apilados ("Bullish"/"Bearish"/"None") |
| `equal_low` | Si se detectó equal low en la señal |

---

## Criterio de validez (walk-forward ~2026-07-05)

Correr `shorts_mtf_backtest.py --days 30` sobre datos posteriores a 2026-06-14 (datos que el modelo nunca vio).

**Pass**: WR ≥ 55% Y AvgR ≥ +0.30R  
**Fail**: cualquiera de los dos por debajo → recalibrar patrones, no agregar filtros arbitrarios

---

## Historial de calibraciones

| Fecha | Cambio |
|---|---|
| 2026-06-13 | Sistema congelado v1 (n≈63 shorts, WR=71.4%) |
| 2026-06-14 | BNB/XRP shorts bloqueados en London (WR=26-39%); longs ETH/SOL activados (n=125, WR=56%, AvgR=+0.526R); bloqueo horario revertido (sobreajuste con n<100) |
