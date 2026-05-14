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
| VWAP overlay | Session reset UTC, ±1σ/±2σ, AVWAP BOS (swing pivot automático) |
| Volume Profile VRVP | 150 bins, POC/VAH/VAL, HVN/LVN — adapta al rango visible en pantalla |
| CVD | Línea + delta, session reset UTC, VPIN candle-level, CVD divergencias |
| Volume | Barras buy/sell split |
| Relative Volume | Ratio volumen actual / media últimas 20 velas, dirección coloreada |
| Open Interest | Línea, solo perps |
| OI Delta | Barras verde/rojo por diferencia de OI |
| ATR(14) | Línea, Wilder smoothing |
| Session lines | Verticales punteadas Asia/London/NY en timeframes ≤4h |
| Key levels | PDH, PDL, Daily Open, Weekly Open — horizontales con etiqueta |
| Strategy module | 8 fases completas, 3 detectores, scoring, tracker |
| UI toggle | Botón ⭐ en toolbar activa/desactiva el strategy overlay |
| Outcome tracker | MFE/MAE por señal, JSONL en `%APPDATA%\flowsurface\shadow_events\` |
| analyze_outcomes.py | Script Python: win rate, MFE/MAE, confianza por detector |

### Pendiente

| Feature | Descripción |
|---------|-------------|
| Funding rate panel | Tasa 8h en perps como indicador de sesgo de mercado |
| Session VWAPs | VWAP separado por sesión Asia / London / NY |
| AVWAP manual | Anchor a punto elegido por el usuario con clic en el chart |
| OI z-score | Desviación normalizada del OI vs su media histórica |
| Stacked imbalance | N niveles consecutivos de imbalance en footprint |
| Regime mejorado | EMA crosses + volatility squeeze como confirmación adicional |

Ver [PENDIENTE.md](PENDIENTE.md) para descripción completa de cada item.

### Estructura del repo

```
flowsurface/
├── src/
│   ├── chart/kline.rs              — KlineChart principal + session lines + key levels
│   ├── chart/indicator/kline/      — 8 indicadores (+ relative_volume)
│   ├── strategy/                   — módulo completo de detección
│   └── screen/dashboard/pane.rs   — UI, eventos, toggle
├── data/src/chart/indicator.rs     — enum KlineIndicator, UiIndicator
├── exchange/src/                   — tipos: Kline, Trade, Depth, OI
├── scripts/analyze_outcomes.py     — análisis de outcomes post-sesión
└── docs/                           — esta documentación
```

### Datos persistidos

```
%APPDATA%\flowsurface\shadow_events\
├── strategy_signals.jsonl     — señales generadas al momento de detección
└── strategy_outcomes.jsonl    — resultados de señales cerradas (MFE/MAE/outcome)
```
