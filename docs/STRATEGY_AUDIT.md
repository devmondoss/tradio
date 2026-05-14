# Auditoria Actual del Modulo de Estrategia

Estado basado en el codigo actual del arbol local.

## Estado implementado

- Deteccion shadow-only integrada en `KlineChart` al cierre de vela.
- Logging de senales/bloqueos en `strategy_signals.jsonl`.
- Tracker legacy de MFE/MAE en `strategy_outcomes.jsonl`.
- Paper trading con `PaperAccount`, posiciones abiertas, sizing por riesgo, slippage, fees, funding, contradicciones y persistencia en `paper_account_state.json`.
- Scoring basado en magnitudes normalizadas, R:R, distancia a target en ATR, VPIN, spread, regimen y confluencia de niveles.
- TTL dinamico por timeframe: `TTL_BARS * interval_ms`.
- `stacked_imbalance`, `sweep_confirmed`, `mss_active`, `cvd_divergence`, `Stress` y `Aftermath` ya se derivan desde datos recientes de velas/delta.

## Riesgos restantes

| Gap | Impacto |
|-----|---------|
| Microestructura usa heuristicas OHLC/delta | Las senales ya no son placeholders, pero `stacked_imbalance`, sweep y MSS deben calibrarse con footprint/swing engine dedicado. |
| `Stress` y `Aftermath` se derivan por rango/movimiento normalizados por ATR | Funcionan como guardrail inicial; requieren calibracion con datos reales. |
| Los strings de `signal.invalidation` son descriptivos | El cierre real depende de stop, target y TTL. |
| Paper trading sigue siendo simulacion OHLC por vela | Si stop y target se tocan en la misma vela cierra en stop y marca `STOP_AND_TARGET_SAME_BAR`. |
| Config de paper se lee de variables de entorno | Cambiar env vars entre sesiones puede alterar como se interpreta una cuenta restaurada. |

## Prerrequisitos implicitos para senales

- `CumulativeDelta` debe estar activo: si no hay `cvd` o `delta`, `flow.quality = Missing` y el toxic-flow gate bloquea.
- `VolumeProfile` debe exponer `poc`, `vah` y `val`: si no, `volume_profile.quality != Live`.
- `last_depth` debe existir: sin orderbook reciente no corre deteccion.
- `ATR` mejora stops/scoring; sin ATR muchas senales quedan degeneradas o con score bajo.

## Archivos de salida

```text
%APPDATA%\flowsurface\shadow_events\
|-- strategy_signals.jsonl
|-- strategy_outcomes.jsonl
|-- paper_trades.jsonl
|-- contradictions.jsonl
`-- paper_account_state.json
```

`strategy_outcomes.jsonl` se mantiene como tracker legacy. Para evaluar PnL realista, usar `paper_trades.jsonl` y `scripts/analyze_outcomes.py`.
