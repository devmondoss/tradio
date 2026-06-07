# Estrategia ICT AMD — Liquidity Sweep

## Qué es

**ICT AMD** (Accumulation · Manipulation · Distribution) es una estrategia de reversión de Smart Money Concepts (SMC).

El mercado opera en tres fases repetibles en M1:

```
ACCUMULATION  →  MANIPULATION  →  DISTRIBUTION
(rango lateral)   (spike / stop hunt)  (el movimiento real)
```

Los institucionales acumulan posición en un rango silencioso, luego ejecutan un spike que barre los stops y pools de liquidez de los retail (manipulación), y finalmente mueven el precio con fuerza en la dirección opuesta (distribución real). El objetivo es detectar el final de la manipulación y entrar en la distribución.

---

## El patrón visual (lo que ves en el chart)

```
         ┌─── spike barre nivel (high > swing_hi) ───┐
         │                                            │ close vuelve adentro
─────────┤   rango 10–60 velas    ├──────────────────┤
         │   (consolidación)      │                  ▼
─────────┘                        └─── SHORT entry ──→ distribucion bajista
```

**SHORT**: precio spike UP barriendo Equal Highs / Asian High / PDH → cierra de vuelta → short en el close.
**LONG**: precio spike DOWN barriendo Equal Lows / Asian Low / PDL → cierra de vuelta → long en el close.

---

## Condiciones de entrada — en orden estricto

### Gate 1 — Kill Zone (filtro temporal)
Solo dentro de ventanas donde los institucionales manipulan liquidez:

| Kill Zone | UTC | Prioridad |
|---|---|---|
| London Open | 07:00–09:00 | Alta — barre Asian range |
| NY Open | 13:30–15:30 | Alta — barre London range |
| London Close / Silver Bullet | 10:00–11:00 | Media |

Fuera de Kill Zone el WR cae ~20pp. No operar.

---

### Gate 2 — El nivel barrido es un pool real
El spike debe barrer uno de estos niveles específicos, no un swing aleatorio:

| Nivel | Campo en btc_bars | Por qué es un pool |
|---|---|---|
| **Equal Highs / Equal Lows** | `equal_high` / `equal_low` | Dos o más swings al mismo precio = stops acumulados visibles para todos |
| **Asian High / Asian Low** | `asian_high` / `asian_low` | London barre el rango Asia en ~70% de los días |
| **Previous Day High / Low** | `prev_day_high` / `prev_day_low` | Pool diario institucional — NY open lo caza frecuentemente |
| **Swing 50 barras** | `swing_high_50` / `swing_low_50` | Nivel swing significativo como fallback |

---

### Gate 3 — Spike con firma de manipulación
En la barra del sweep:

| Condición | Umbral | Campo |
|---|---|---|
| Volume Ratio alto | `vr >= 5.0x` | calculado de `volume` vs media 50b |
| CVD diverge del precio | spike Up + `bar_delta < 0` ó spike Down + `bar_delta > 0` | `bar_delta` |
| Close de vuelta dentro del nivel | `close < swing_hi` (SHORT) / `close > swing_lo` (LONG) | `close` vs nivel |

---

### Gate 4 — Confirmación orderflow (filtro de calidad)

| Campo | Umbral | Qué confirma |
|---|---|---|
| `obi_l5` | `< -0.10` SHORT / `> +0.10` LONG | Libro confirma presión opuesta al spike |
| `absorption` | `Bid` en LONG / `Ask` en SHORT | Institucionales absorbieron el spike |
| `bid_wall` | `true` en LONG | Pared en el nivel barrido = pool real |
| `ask_wall` | `true` en SHORT | Pared en el nivel barrido = pool real |
| `cvd_slope` | gira de dirección | CVD ya reversa — distribución en marcha |

---

## Gestión del trade

| Parámetro | Valor |
|---|---|
| **Entry** | Close de la barra de rechazo (sweep bar) |
| **Stop** | Extremo del spike ± 0.08% buffer |
| **Target mínimo** | 2R (fallback si no hay nivel estructural) |
| **Target estructural** | FVG / Order Block / Naked POC / LVN en dirección de distribución |
| **Max hold** | 120 velas M1 (2 horas) |
| **Cooldown** | 10 velas entre señales |

---

## WR esperado por nivel de filtros

| Configuración | WR | Señales/semana |
|---|---|---|
| Solo VR ≥ 5 + CVD diverge | ~40–50% | 8–12 |
| + Kill Zone | ~55–60% | 4–6 |
| + Nivel real (Asian / PDH / EQH) | ~70–75% | 2–4 |
| + OBI gate + MSS confirmation | **~80–85%** | 1–3 |

**Target**: 80–85% WR con 1–3 señales por semana.

---

## Datos por barra — tabla `btc_bars` (y `eth_bars`, `bnb_bars`, `sol_bars`)

### OHLCV — señal primaria del patrón
| Campo | Tipo | Uso en AMD |
|---|---|---|
| `high` | float | Detectar sweep: `high > swing_hi` |
| `low` | float | Detectar sweep: `low < swing_lo` |
| `close` | float | Confirmar rechazo: cierra de vuelta |
| `open` | float | Contexto del cuerpo de vela |
| `volume` | float | Base para calcular VR |
| `bar_delta` | float | `taker_buy - taker_sell` — CVD divergence |
| `vr` | float | Volume ratio vs media 50 barras |

### Niveles estructurales ICT — determinan si el nivel vale
| Campo | Tipo | Cuándo se llena | Uso en AMD |
|---|---|---|---|
| `asian_high` | float? | Durante sesión Asia (00:00–07:00 UTC) | ¿El spike barrió el Asian range? |
| `asian_low` | float? | Durante sesión Asia (00:00–07:00 UTC) | ¿El spike barrió el Asian range? |
| `prev_day_high` | float? | Al cambiar de día UTC | PDH — pool diario institucional |
| `prev_day_low` | float? | Al cambiar de día UTC | PDL — pool diario institucional |
| `swing_high_50` | float? | Siempre (rolling 50b) | Nivel swing de referencia |
| `swing_low_50` | float? | Siempre (rolling 50b) | Nivel swing de referencia |
| `equal_high` | bool | Siempre | ¿High a ≤0.03% de swing previo? = EQH |
| `equal_low` | bool | Siempre | ¿Low a ≤0.03% de swing previo? = EQL |

### Orderflow — confirmación institucional
| Campo | Tipo | Uso en AMD |
|---|---|---|
| `obi_l5` | float | Presión del libro L5 en el momento del sweep |
| `absorption` | string | `Bid`/`Ask`/`None` — institucionales comieron el spike |
| `bid_wall` | bool | Pared de bids en el nivel barrido |
| `ask_wall` | bool | Pared de asks en el nivel barrido |
| `cvd_slope` | float | Slope del CVD — momentum de la distribución |
| `dz` | float | Z-score del delta — anormalidad estadística |
| `vpin` | float | Probabilidad de flujo informado (institucionales) |
| `stacked_imb` | string | Imbalances apiladas hacia el target |
| `thin_above` | bool | Camino libre arriba (LONG) |
| `thin_below` | bool | Camino libre abajo (SHORT) |

### Contexto de mercado
| Campo | Tipo | Uso en AMD |
|---|---|---|
| `session` | string | Kill Zone filter |
| `vwap` | float? | Referencia de valor justo de sesión |
| `regime` | string | Contexto macro (Bull/Bear/Range) |
| `atr` | float | Volatilidad normalizada |

---

## Configuración actual — `config/strategy.toml`

```toml
[amd_detector]
enabled = true                    # shadow mode — graba en amd_signals

# Acumulación
accum_range_min_pct       = 0.04  # rango mínimo del consolidación (%)
accum_range_max_pct       = 0.30  # rango máximo — > 0.30% no es acumulación real
accum_min_bars            = 10    # mínimo 10 velas M1 de consolidación
accum_max_bars            = 60    # timeout (1 hora)

# Spike de manipulación
manip_min_vr              = 1.5   # VR mínimo en la barra del spike
manip_vpin_threshold      = 0.55  # VPIN mínimo para confirmar flujo informado

# Entry de distribución
dist_min_vr               = 1.0   # VR mínimo en la barra de entry
dist_cvd_slope            = 5.0   # CVD slope mínimo (confirmación de giro)
dist_obi_confirm          = 0.08  # OBI mínimo alineado con la distribución

# Gestión
stop_buffer_pct           = 0.08  # buffer sobre el spike extreme (%)
min_rr                    = 2.0   # R:R mínimo para emitir señal
cooldown_bars             = 30    # barras entre señales
max_wait_bars_after_spike = 10    # timeout esperando reversión tras spike
```

---

## Arquitectura del sistema

```
Binance WS (klines M1)
        │
        ▼
  BarState::on_bar_close()          ← Rust monitor, cada vela M1
        │
        ├─ Calcula ICT levels:
        │    asian_high/low          (acumula 00:00–07:00 UTC)
        │    prev_day_high/low       (guarda al cambiar día)
        │    swing_high/low_50       (rolling max/min 50b)
        │    equal_high/low          (≤0.03% vs swing previo)
        │
        ├─ AmdDetectorState::on_bar_close()
        │    Fase 1: Idle → Accumulating (rango válido)
        │    Fase 2: Accumulating → ManipulationDetected (spike + CVD + VPIN)
        │    Fase 3: ManipulationDetected → AmdSignal (primera barra de reversión)
        │
        └─ write_rbf_bar() → Supabase btc_bars   (todos los campos arriba)
           write_amd_signal() → Supabase amd_signals  (solo cuando hay señal)
```

### Tablas Supabase

| Tabla | Contenido | Estado |
|---|---|---|
| `btc_bars` | Barras M1 BTC con todos los campos | Activa, ~2800 filas |
| `eth_bars` | Barras M1 ETH | Activa, ~1644 filas |
| `bnb_bars` | Barras M1 BNB | Activa, ~1489 filas |
| `sol_bars` | Barras M1 SOL | Activa, ~1489 filas |
| `amd_signals` | Señales AMD emitidas | Activa, n=0 (shadow mode) |

---

## Scripts de backtest / análisis

| Script | Función |
|---|---|
| `scripts/amd_chart.py` | Chart interactivo M1 con señales, posiciones, CVD, VR, OBI. Flags: `--micro`, `--obi-gate`, `--min-vr`, `--lb` |
| `scripts/amd_backtest.py` | Backtest básico sobre OHLCV Binance |
| `scripts/amd_sweep.py` | Sweep de parámetros para calibración |
| `scripts/rbf_backtest.py` | Backtest RBF con microestructura Supabase (`--extended`) |

Generar chart con microestructura:
```bash
python scripts/amd_chart.py --days 14 --min-vr 5 --micro -o amd_chart.html
```

---

## Estado de implementación

| Componente | Estado | Nota |
|---|---|---|
| Detector Python (backtest) | ✅ Funcional | WR ~55%, n=13 en 14d |
| Detector Rust (live) | ✅ Shadow mode | n=0 señales — umbral VPIN muy estricto |
| OHLCV + orderflow en btc_bars | ✅ Activo | Desde Jun 5 |
| Niveles ICT en btc_bars | ✅ Activo | Desde Jun 7 (asian/pdh/swing50/eq) |
| Kill Zone filter | ⏳ Pendiente | Añadir al detector Rust |
| Nivel real como gate | ⏳ Pendiente | Añadir al detector Python + Rust |
| MSS confirmation | ⏳ Pendiente | Mejora WR +10–15pp |
| FVG como target | ⏳ Pendiente | Ya tiene detección en `fvg_detector` |
| Backtest con niveles ICT | ⏳ Pendiente | Necesita 30+ días de datos |
| Live alerts / entries | ⏳ Pendiente | Cuando WR ≥ 75% en backtest |

---

## Próximos pasos (en orden)

1. **Aplicar SQL migration** en Supabase Dashboard → `supabase/migrations/20260607_ict_levels_bars.sql`
2. **Acumular datos**: dejar el monitor corriendo ~30 días para tener suficientes barras con todos los campos ICT
3. **Actualizar `amd_chart.py`**: añadir filtros `asian_high`, `prev_day_high`, `equal_high` como gates de nivel
4. **Backtest comparativo**: WR con swing aleatorio vs WR con nivel ICT real
5. **Implementar Kill Zone** en el detector Rust (`config/strategy.toml`)
6. **Reducir VPIN threshold** en el detector Rust (actualmente 0.55 — nunca dispara en live)
7. **MSS detection**: primera rotura de estructura tras el spike → +10–15pp WR
8. **Live alerts** cuando el backtest valide WR ≥ 75% con n ≥ 50 señales
