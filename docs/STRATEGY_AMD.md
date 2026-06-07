# Estrategia ICT AMD — Liquidity Sweep

## Qué es

ICT AMD (Accumulation · Manipulation · Distribution) es una estrategia de reversión basada en Smart Money Concepts (SMC) de Inner Circle Trader (ICT).

El mercado opera en tres fases repetibles:
1. **Accumulation** — rango lateral donde los institucionales construyen posición silenciosamente
2. **Manipulation** — spike que barre stops y pools de liquidez (el "stop hunt")
3. **Distribution** — el movimiento real en la dirección opuesta al spike

El objetivo es detectar el final de la manipulación y entrar en la distribución.

---

## El patrón concreto (observado en backtests manuales)

```
[Rango 15–60 velas M1]     ← acumulación
        │
        ▼
[Spike 0.3–1.5% beyond key level]  ← manipulación: barre el nivel
        │
        ▼  (misma barra o siguiente cierra de vuelta)
[Rejection / close back inside]    ← señal de entrada
        │
        ▼
[Move 1–2% en dirección opuesta]   ← distribución: el trade
```

---

## Condiciones de entrada (en orden de prioridad)

### 1. Kill Zone (obligatorio)
Solo operar dentro de ventanas horarias donde la manipulación es estructural:
- **London Open**: 07:00–09:00 UTC
- **NY Open**: 13:30–15:30 UTC
- **London Close / Silver Bullet**: 10:00–11:00 UTC (secundario)

Fuera de Kill Zone el WR cae drásticamente.

### 2. Nivel barrido es un pool real (obligatorio)
El spike debe barrer uno de estos niveles, no un swing aleatorio:
- **Asian High / Asian Low** — H/L de la sesión Asia (00:00–07:00 UTC). London los barre con frecuencia.
- **Previous Day High (PDH) / Previous Day Low (PDL)** — pool institucional diario.
- **Equal Highs / Equal Lows (EQH/EQL)** — dos o más swings al mismo nivel ±0.03%. Son los pools más obvios y más cazados.
- **Swing High/Low 50** — swing significativo de las últimas 50 barras como fallback.

### 3. Spike con volumen anormal (obligatorio)
- `vr >= 5.0x` — volume ratio vs media 50 barras. El spike sin volumen es ruido.
- `bar_delta < 0` en spike Up (SHORT) / `bar_delta > 0` en spike Down (LONG). CVD diverge: precio sube pero los vendedores ganan — manipulación confirmada.

### 4. Cierre de vuelta dentro del nivel (obligatorio)
- La barra del spike cierra **por debajo** del nivel barrido (SHORT) o **por encima** (LONG).
- Cuanto más rápido cierra de vuelta, más fuerte el rechazo institucional.

### 5. Confirmación orderflow (filtro de calidad)
- `obi_l5 < -0.10` en SHORT / `obi_l5 > +0.10` en LONG — el libro confirma presión opuesta al spike.
- `absorption > 0` — institucionales absorbieron el spike sin mover precio más.
- `bid_wall` (LONG) / `ask_wall` (SHORT) — pared en el nivel barrido confirma que era un pool real.

---

## Gestión del trade

| Parámetro | Valor |
|---|---|
| Entry | Close de la barra de rechazo |
| Stop | Extremo del spike ± 0.08% buffer |
| Target mínimo | 2R |
| Target estructural | FVG o OB en dirección de distribución (si existe) |
| Max hold | 120 velas M1 (2 horas) |
| Cooldown | 10 velas entre señales |

---

## WR esperado por nivel de filtros

| Filtros activos | WR estimado | Señales/semana |
|---|---|---|
| Solo VR ≥ 5 + CVD div | ~40–50% | 8–12 |
| + Kill Zone | ~55–60% | 4–6 |
| + Nivel real (Asian/PDH/EQH) | ~70–75% | 2–4 |
| + OBI gate + MSS | ~80–85% | 1–3 |

---

## Datos necesarios por barra (todos en `btc_bars`)

### OHLCV — señal primaria del patrón
| Campo | Uso |
|---|---|
| `high`, `low`, `close` | Detectar sweep y cierre de vuelta |
| `volume` → `vr` | Confirmar que el spike tiene volumen institucional |
| `bar_delta` | CVD divergence: detectar manipulación |

### Niveles estructurales ICT — determinan si el nivel vale
| Campo | Uso |
|---|---|
| `asian_high`, `asian_low` | ¿El spike barrió el Asian range? |
| `prev_day_high`, `prev_day_low` | ¿El spike barrió PDH/PDL? |
| `swing_high_50`, `swing_low_50` | Nivel swing significativo de 50 barras |
| `equal_high`, `equal_low` | ¿Es un EQH/EQL? Pool de máxima probabilidad |

### Orderflow — confirmación institucional
| Campo | Uso |
|---|---|
| `obi_l5` | Presión del libro en el momento del sweep |
| `absorption` | Institucionales comieron el spike |
| `bid_wall`, `ask_wall` | Pared en el nivel = pool real |
| `cvd_slope` | Momentum del CVD en la distribución |
| `dz` | Z-score del delta — ¿estadísticamente anormal? |
| `vpin` | Flujo informado alto = institucionales activos |
| `stacked_imb` | Imbalances apiladas hacia el target |
| `thin_above`, `thin_below` | Camino libre hacia el target |

---

## Estado de implementación

| Componente | Estado |
|---|---|
| Detector Python (backtest) | `scripts/amd_chart.py` — liquidity sweep básico, WR ~55% |
| Detector Rust (live shadow) | `data/src/strategy/detectors/amd_detector.rs` — shadow mode, n=0 señales aún |
| Datos OHLCV + orderflow | `btc_bars` — activo, ~2800 barras acumuladas (Jun 5–) |
| Niveles ICT en btc_bars | Añadidos Jun 7: asian_h/l, pdh/pdl, swing50, eq_h/l |
| Backtest con niveles ICT | **Pendiente** — necesita 30+ días de datos con niveles |
| MSS detection | Pendiente de implementar en detector |
| FVG como target estructural | Pendiente |

---

## Próximos pasos

1. Acumular 30+ días de `btc_bars` con los nuevos campos ICT
2. Reescribir `scripts/amd_chart.py` para usar `asian_high/low`, `pdh/pdl`, `equal_high/low` como filtro de nivel
3. Backtest: comparar WR con nivel aleatorio vs nivel ICT real
4. Implementar MSS detection en el detector Rust
5. Subir `min_vr` y añadir Kill Zone en el detector live cuando haya n≥50 señales validadas
