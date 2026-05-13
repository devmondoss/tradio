# Documentación — flowsurface

## Archivos

| Archivo | Contenido |
|---------|-----------|
| [BUILD.md](BUILD.md) | Setup del entorno de build en Windows — MinGW, run.bat, toolchain GNU |
| [INDICATORS.md](INDICATORS.md) | Arquitectura de indicadores — overlay vs panel, VWAP, Volume Profile, OI Delta |
| [BUGS_Y_FIXES.md](BUGS_Y_FIXES.md) | Bugs corregidos y uno pendiente (lyon_path panic) |
| [STRATEGY.md](STRATEGY.md) | Strategy module — detectores, pipeline, fases de implementación, repo separado |
| [PENDIENTE.md](PENDIENTE.md) | Trabajo por hacer — improvements, nuevos indicadores, sesiones, layout sugerido |

## Quick start

```bat
cd flowsurface
./run.bat
```

## Estado actual del proyecto (Mayo 2026)

### Funcionando
- Chart de velas + footprint con VWAP y Volume Profile como overlays
- VWAP con session reset diario (UTC) y bandas ±1σ / ±2σ
- Volume Profile con histograma horizontal (150 bins) + POC/VAH/VAL/HVN/LVN
- CVD, Volume, OI, OI Delta como paneles separados
- ATR como panel separado
- Strategy module en modo shadow con 3 detectores (overlay en chart)
- Fix del loop infinito de ERROR en OI

### Pendiente urgente
- Panic de lyon_path al arrancar (ver [BUGS_Y_FIXES.md](BUGS_Y_FIXES.md#6-panic-de-lyon_path--pendiente-de-resolver))
  → Correr `./run.bat` y copiar el backtrace completo
