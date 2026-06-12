# RBF — Calibración por Activo (2026-06-11)

> Snapshot histórico de calibración. No es fuente de verdad operativa. Para reglas live usar [RBF_REGLAS_ACTIVAS.md](RBF_REGLAS_ACTIVAS.md).
> Nota 2026-06-12: el monitor live volvió a `expansion_max_bars = Some(1)` para BTC/ETH/BNB al promover la configuración calibrada de la app; el score live actual tiene 6 flags y usa `obi_trap`, no `obi_multi_depth`/`obi_intrabar_mean` como puntos.

Análisis basado en **72 trades live** (2026-06-02 → 2026-06-10).
Dataset: `scripts/rbf_microstructure.csv`, `scripts/rbf_pnl_450.csv`, `scripts/rbf_entry_lag.csv`.

> **Nota calibración v3 (2026-06-11):** `expansion_max_bars` bajado de 3 a **1** para BTC/ETH/BNB tras análisis comparativo.
> Se añadieron: pre-CVD gate, score≠4 gate, London CVD gate, spread gate, OI covering gate, OBI multi-depth (+1), OBI intrabar mean (+1).
> Ver [RBF_REGLAS_ACTIVAS.md](RBF_REGLAS_ACTIVAS.md) para el estado actual completo.

---

## Resumen global pre-calibración

| Sesión | n | WR | PnL |
|--------|---|----|-----|
| London | 45 | 33% | +0.65R |
| LondonNyOverlap | 22 | 50% | +6.47R |
| NewYork | 5 | 60% | +2.57R |

**Insight clave (entry lag):** las señales bearish de microestructura aparecen ~24 barras antes de que el detector dispare. El mercado lleva entre 32–54% del movimiento consumido cuando se entra. Si se entrara al inicio del move, el target potencial sería 4.30R vs 2.00R actual.

---

## BTCUSDT

### Datos de performance
| Métrica | Valor |
|---------|-------|
| n | 32 |
| WR | 41% |
| PnL | +6.37R |
| avg/trade | +0.20R |
| Risk % precio | ~0.24% |

### Microestructura (Shorts con datos, n=17)
| Métrica | Wins | Losses | Δ |
|---------|------|--------|---|
| Cum delta 25b | -127 | **+377** | −504 |
| OBI avg | **+0.141** | -0.011 | +0.152 |
| Barras en Expansion | 4.0 | **7.2** | −3.2 |
| Barras OI momentum | 8.0 | 6.7 | +1.3 |

**Hallazgo BTC:** Las pérdidas en BTC Short tienen delta acumulado **positivo** (+377). Hay compradores activos cuando el sistema entra short — es una entrada contra el flujo real. Las wins tienen delta apenas negativo (-127) con OBI positivo (bids al entrar), lo que señala absorción real: precio rompe con flujo comprador siendo absorbido.

### Calibración aplicada
- **`expansion_max_bars = Some(1)`** — filtro activo. Barras con expansion>3 en la ventana pre-entry indican move maduro.
- **Trailing Short: 1.75R** (antes 1.5R) — BTC tuvo 2 trailing cases (+1.32R, +1.00R) cuando target era 2R.

### Pendiente
- Investigar por qué cum_delta positivo en losses — posible entrada en fake breakdown con compradores agresivos. Candidato a filtro: `cum_delta < umbral` en Shorts.

---

## ETHUSDT

### Datos de performance
| Métrica | Valor |
|---------|-------|
| n | 11 |
| WR | 27% |
| PnL | −2.88R |
| avg/trade | −0.26R |
| Risk % precio | ~0.25% |

### Microestructura (Shorts con datos, n=8)
| Métrica | Wins | Losses | Δ |
|---------|------|--------|---|
| Cum delta 25b | -1468 | -1320 | −148 (sin diferencia) |
| OBI avg | **-0.094** | -0.024 | -0.070 |
| Barras en Expansion | **1.3** | 4.8 | −3.5 |
| Barras OI momentum | 5.3 | **8.2** | −2.9 |

**Hallazgo ETH:** Es el único símbolo donde el cum_delta no discrimina wins vs losses (−1468 vs −1320 casi iguales). La diferencia útil está en:
1. **OBI:** wins tienen OBI más negativo (−0.094) → mayor presión ask al entrar Short. ETH tiene dinámica inversa al resto: en ETH ganar un Short requiere ask pressure real, no absorción de bids.
2. **Expansion:** wins 1.3 vs losses 4.8 — filtro expansion aplica igual que BTC/BNB.
3. **OI momentum:** losses tienen 8.2 barras con OI alineado — señal de move maduro.

### Calibración aplicada
- **`expansion_max_bars = Some(1)`** — filtro activo. Wins tienen avg 1.3 barras expansion vs 4.8 en losses.
- **Trailing Short: 1.75R** — ETH no tuvo trailing cases, pero la calibración por dirección aplica.
- **expansion_bars_recent** se pasa con conteo real (no bypass como SOL).

### Pendiente
- Con n=11 la muestra es pequeña. Revisar con n=25+.
- El comportamiento invertido del OBI sugiere calibrar `obi_threshold` específico para ETH cuando se active el OBI gate.
- Posible: `obi_gate = true` para ETH con threshold en dirección inversa (obi < -0.05 para Short).

---

## BNBUSDT

### Datos de performance
| Métrica | Valor |
|---------|-------|
| n | 17 |
| WR | 41% |
| PnL | +1.40R |
| avg/trade | +0.08R |
| Risk % precio | ~0.23% |

### Microestructura (Shorts con datos, n=9)
| Métrica | Wins | Losses | Δ |
|---------|------|--------|---|
| Cum delta 25b | **-78** | -1357 | +1279 |
| OBI avg | **+0.051** | -0.018 | +0.069 |
| Barras en Expansion | **3.6** | 7.8 | −4.2 |
| Barras OI momentum | **4.3** | 9.1 | −4.8 |

**Hallazgo BNB:** Patrón más claro de todos los símbolos. Wins con delta casi neutro (-78) vs losses con -1357 — cuando el delta ya acumuló mucho selling en BNB, el move está terminado. OBI positivo en wins (absorción de bids) junto a poco tiempo en expansión (3.6 barras) define la señal de calidad.

El mayor impacto del filtro expansion se ve aquí: losses tienen 7.8 barras expansion vs 3.6 en wins.

### Calibración aplicada
- **`expansion_max_bars = Some(1)`** — filtro más impactante en BNB (diferencia 4.2 barras).
- **Trailing Short: 1.75R** — BNB tuvo los peores trailing cases: 2 trades salieron a +0.90R y +0.80R cuando target era 2R. Diferencia de −1.10R por trade. El 1.75R reduce drásticamente estos exits prematuros.

### Pendiente
- Calibrar threshold de cum_delta para BNB Short: `cum_delta > −500` como filtro candidato (wins promedio −78, losses −1357).
- OI momentum (4.3 wins vs 9.1 losses): candidato a veto si `oi_mom_count > 6`.

---

## SOLUSDT

### Datos de performance
| Métrica | Valor |
|---------|-------|
| n | 11 |
| WR | 55% |
| PnL | +5.80R |
| avg/trade | +0.53R |
| Risk % precio | ~0.24% |

### Microestructura (todos, n=11)
| Métrica | Wins | Losses | Δ |
|---------|------|--------|---|
| Cum delta 25b | **-9,702** | -43,138 | +33,436 |
| OBI avg | -0.007 | -0.023 | +0.016 |
| Barras en Expansion | 5.2 | **4.2** | **+1.0 (invertido)** |
| Barras OI momentum | 8.2 | 5.0 | +3.2 (invertido) |

**Hallazgo SOL — correlación invertida:**
SOL es el único símbolo donde el filtro expansion está **invertido**: las wins tienen MÁS expansion (5.2) que las losses (4.2). Posibles causas:
1. SOL tiene movimientos más explosivos y rápidos — entra en expansión antes pero el target se alcanza igual.
2. La ventana de 25 barras M1 puede estar capturando un régimen diferente al del breakout real en SOL.
3. n=11 es pequeño — posible ruido estadístico.

Por esto se pasa `expansion_bars_recent = 0` para SOL, desactivando el filtro.

El cum_delta discrimina bien: wins -9,702 vs losses -43,138. Delta demasiado negativo en SOL = move maduro.

### Calibración aplicada
- **`expansion_bars_recent = 0` (bypass)** — el filtro expansion no aplica para SOL. Dato invertido. Revisar con más trades.
- **`expansion_max_bars = None`** — el config de SOL no pone umbral.
- **Trailing Short: 1.75R** — 0 casos trailing en SOL, calibración por dirección aplica igualmente.

### Pendiente
- Seguir acumulando datos (objetivo: n=25 Shorts y n=25 Longs).
- Investigar filtro cum_delta para SOL: `cum_delta > −20,000` como candidato (wins avg −9,702).
- Entender por qué expansion está invertida: revisar si el `regime` de SOL se computa con los mismos parámetros ATR que BTC.

---

## XRPUSDT

### Datos de performance
| Métrica | Valor |
|---------|-------|
| n | 1 |
| WR | 0% |
| PnL | −1.00R |
| avg/trade | −1.00R |
| Risk % precio | ~0.25% |

**Estado:** muestra insuficiente. Un solo trade (Long, NY, score=4, STOP).

### Calibración aplicada
- **`expansion_bars_recent = 0` (bypass)** — sin datos para calibrar.
- **`expansion_max_bars = None`** — sin umbral hasta tener n≥15.

### Pendiente
- Acumular mínimo 15 trades antes de cualquier calibración.

---

## Cambios implementados en código

### `data/src/strategy/detectors/range_breakout_flow.rs`
```rust
// Nuevo campo en RbfGateContext
pub expansion_bars_recent: u8,

// Nuevo campo en RangeBreakoutConfig
pub expansion_max_bars: Option<u8>,  // Default: None

// Filtro en on_bar_close (dentro del loop de range windows)
if let Some(max_exp) = cfg.expansion_max_bars {
    if ctx.expansion_bars_recent > max_exp { continue; }
}
```

### `data/src/strategy/detectors/rbf_paper.rs`
```rust
// Antes: const TRAIL_ACTIVATE_R: f64 = 1.5
// Ahora, direction-aware:
const TRAIL_ACTIVATE_R_SHORT: f64 = 1.75;  // Shorts target 2R → activa 0.25R antes
const TRAIL_ACTIVATE_R_LONG:  f64 = 1.5;   // Longs target 1.8R → sin cambio
```

### `crates/monitor/src/main.rs`
```rust
// Campo nuevo en BarState
regime_hist_25: VecDeque<data::strategy::types::Regime>,

// Actualización en on_bar_close (después de computar effective_regime)
self.regime_hist_25.push_back(effective_regime);
if self.regime_hist_25.len() > 25 { self.regime_hist_25.pop_front(); }

// Al construir rbf_gate — bypass SOL/XRP
let expansion_bars_recent: u8 = if symbol == "SOLUSDT" || symbol == "XRPUSDT" {
    0
} else {
    self.regime_hist_25.iter()
        .filter(|&&r| r == Regime::Expansion)
        .count().min(25) as u8
};

// Config por símbolo
let mut rbf_cfg = cfg.range_breakout.clone();
rbf_cfg.expansion_max_bars = match symbol {
    "SOLUSDT" | "XRPUSDT" => None,
    _                      => Some(1),
};
```

---

## Qué falta calibrar (próximos pasos)

| Filtro candidato | Símbolo | Dato | Estado |
|-----------------|---------|------|--------|
| cum_delta threshold Short | BNB | wins −78 vs losses −1357 | Activo: `cum_delta_25b >= -500` |
| cum_delta threshold Short | SOL | wins −9,702 vs losses −43,138 | Pendiente n≥25 |
| cum_delta positivo veto Short | BTC | losses cum_delta +377 | Activo: `cum_delta_25b <= 200` |
| obi_gate direction-aware | ETH | wins OBI −0.094 vs losses −0.024 | Activo como `obi_l5 <= 0.10` |
| oi_mom_count veto | BNB | losses 9.1 barras vs wins 4.3 | Pendiente n≥25 |
| expansion invertida | SOL | revisar si ATR de régimen está bien calibrado | Investigación |
| trailing por símbolo | BNB | mayor gap trailing vs target | Posible ajuste 1.85 |

---

*Generado: 2026-06-10. Re-analizar cuando cada símbolo alcance n=25 Shorts cerrados.*
