# Documentación — flowsurface

Trading chart en Rust/iced con indicadores de order flow y detección automatizada de estrategias microestructurales.

## Quick start

```bat
cd flowsurface
./run.bat
```

Ver [BUILD.md](BUILD.md) para el setup completo del toolchain GNU/MinGW.

---

## Archivos de documentación

| Archivo | Contenido |
|---------|-----------|
| [BUILD.md](BUILD.md) | Setup del entorno: MinGW, toolchain GNU, run.bat |
| [INDICATORS.md](INDICATORS.md) | Todos los indicadores — fórmulas, configs, trait API |
| [STRATEGY.md](STRATEGY.md) | Módulo de estrategia completo — 8 fases, detectores, contexto, tracker |
| [BUGS_Y_FIXES.md](BUGS_Y_FIXES.md) | Bugs resueltos con causa raíz y archivos afectados |
| [PENDIENTE.md](PENDIENTE.md) | Mejoras futuras — indicadores, sesiones, layout |

---

## Estado actual (Mayo 2026)

### Completado

| Área | Descripción |
|------|-------------|
| Chart de velas + footprint | Candles y footprint con zoom, crosshair, escalas |
| VWAP overlay | Session reset UTC, ±1σ/±2σ, AVWAP BOS |
| Volume Profile overlay | 150 bins, POC/VAH/VAL, histograma horizontal, HVN/LVN |
| CVD | Línea + delta por vela, serie de tiempo y tick |
| Volume | Barras buy/sell split |
| Open Interest | Línea, solo perps |
| OI Delta | Barras verde/rojo por diferencia de OI |
| ATR(14) | Línea, Wilder smoothing |
| Strategy module | 8 fases completas, 3 detectores, scoring, tracker |
| UI toggle | Botón ⭐ en toolbar activa/desactiva el strategy overlay |
| Outcome tracker | MFE/MAE por señal, JSONL en `%APPDATA%\flowsurface\shadow_events\` |

### Estructura del repo

```
flowsurface/
├── src/
│   ├── chart/kline.rs              — KlineChart principal
│   ├── chart/indicator/kline/      — 7 indicadores
│   ├── strategy/                   — módulo completo de detección
│   └── screen/dashboard/pane.rs   — UI, eventos, toggle
├── data/src/chart/indicator.rs     — enum KlineIndicator, UiIndicator
├── exchange/src/                   — tipos: Kline, Trade, Depth, OI
└── docs/                           — esta documentación
```

### Datos persistidos

```
%APPDATA%\flowsurface\shadow_events\
├── strategy_signals.jsonl     — señales generadas al momento de detección
└── strategy_outcomes.jsonl    — resultados de señales cerradas (MFE/MAE/outcome)
```
