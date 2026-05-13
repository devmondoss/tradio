# Strategy Module

## Repositorio

El código de estrategia va en un repo separado:
- Remote: `orderflow-strategy` → `https://github.com/inkamaia19/orderflow-strategy.git`
- Comando de push: `git push orderflow-strategy main`
- **NO** pushear al remote `origin` (flowsurface repo principal)

## Arquitectura

```
src/strategy/
├── mod.rs          — pipeline principal
├── types.rs        — StrategySignal, StrategyAction, Side
├── scoring.rs      — función de scoring (0.0–1.0)
└── detectors/
    ├── value_area.rs       — VALUE_AREA_FAILED_AUCTION
    ├── vwap_pullback.rs    — VWAP_VALUE_PULLBACK_CONTINUATION
    └── lvn_breakout.rs     — LVN_LIQUIDITY_VACUUM_BREAKOUT
```

## Pipeline

```
depth update → run_strategy_detection() → Vec<StrategySignal> → overlay en kline chart
```

Los signals se renderizan como overlay en el chart principal (`draw_strategy_overlay` en `src/chart/kline.rs`).

## StrategySignal

```rust
pub struct StrategySignal {
    pub action: StrategyAction,      // ShadowSignal (único modo activo)
    pub side: Option<Side>,          // Long / Short
    pub entry_price: Option<f64>,
    pub stop_price: Option<f64>,
    pub target_price: Option<f64>,
    pub confidence: f64,             // 0.0–1.0
    pub timestamp: UnixMs,
}
```

## Modo shadow-only

`StrategyAction::ShadowSignal` — nunca ejecuta órdenes, solo visualiza. El objetivo es medir primero (MFE/MAE) antes de hacer cualquier apuesta real.

## Logging

Las señales se guardan en JSONL para análisis posterior (Fase 8: outcome tracker).

## Fases de implementación

| Fase | Estado | Descripción |
|------|--------|-------------|
| 1 | ✅ | Strategy module + 3 detectores |
| 2 | ✅ | Overlay rendering en kline chart |
| 3 | ✅ | Wired a depth updates |
| 4 | ⏳ | Exponer VWAP/ATR/VolProfile latest values al contexto |
| 5 | ⏳ | AVWAP, windowed vol profile, HVN/LVN detection |
| 6 | ⏳ | Datos derivados (regime, vpin, cvd_slope, absorción) |
| 7 | ⏳ | UI toggle para activar/desactivar overlay |
| 8 | ⏳ | Outcome tracker — MFE/MAE por señal |

## Detectores implementados

### VALUE_AREA_FAILED_AUCTION
Precio entra al value area y es rechazado — señal de reversión.

### VWAP_VALUE_PULLBACK_CONTINUATION
Retroceso al VWAP o al value area en tendencia — señal de continuación.

### LVN_LIQUIDITY_VACUUM_BREAKOUT
Ruptura de una zona de bajo volumen (LVN) — señal de expansión rápida.
