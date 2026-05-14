# Pendientes

Estado actualizado tras la reparacion de placeholders criticos del modulo de estrategia.

## Resuelto en el arbol local

- `flow.stacked_imbalance` ya no queda siempre en `Unknown`: se deriva desde runs recientes de delta.
- `flow.sweep_confirmed` ya no queda siempre en `false`: detecta barrida del ultimo extremo y cierre de vuelta dentro del rango previo.
- `flow.mss_active` ya no queda siempre en `false`: detecta cambios simples de estructura con highs/lows recientes.
- `cvd_divergence` ya puede trabajar con 5 a 10 velas recientes.
- `Regime::Stress` y `Regime::Aftermath` ya pueden salir de `derive_regime`.
- `Threshold::Qty` en audio ya no usa `todo!`.
- Los `unimplemented!` simples de TickBasis en comparison/heatmap degradan sin panic.

## Pendiente - indicadores y visuales

| Feature | Trabajo |
|---------|---------|
| Funding rate panel | Fetch de endpoints de funding por exchange y panel de linea. |
| OI z-score | Rolling z-score en `OpenInterest` para detectar cambios anormales. |
| Large OI change markers | Marcadores en el price chart cuando OI delta supera threshold/z-score. |
| Manual AVWAP anchor | Permitir click en chart para elegir ancla de AVWAP. Es el pendiente UI mas grande. |
| Session VWAPs | Separar VWAP por Asia/London/NY. |

## Pendiente - estrategia

| Area | Trabajo |
|------|---------|
| Stacked imbalance real | La version actual usa delta por vela. La version final debe leer footprint por niveles consecutivos y ratio buy/sell. |
| Swing/MSS real | La version actual usa highs/lows recientes. La version final debe tener swing engine con pivots confirmados. |
| Regime calibrado | `Stress`/`Aftermath` existen como heuristica ATR; falta calibrar con datos reales y agregar EMA 21/55/squeeze. |
| Invalidation runtime | `signal.invalidation` sigue siendo descriptivo; cierres reales dependen de stop, target y TTL. |

## Pendiente - paper trading y outcomes

| Area | Trabajo |
|------|---------|
| Simulacion intrabar | El motor sigue siendo OHLC por vela. Cuando stop y target tocan en la misma vela cierra en stop y marca `STOP_AND_TARGET_SAME_BAR`; falta replay con trades/ticks para resolver orden real. |
| Config persistida | La config de paper se lee desde env vars en cada arranque; cambiar env entre sesiones puede alterar la interpretacion de cuenta restaurada. |
| Analisis de resultados | Usar `paper_trades.jsonl` para win rate por sesion, score decay, MAE/MFE y TTL optimization cuando haya 100+ trades. |

## Pendiente - infraestructura

| Area | Trabajo |
|------|---------|
| Audit de dependencias | Integrar `cargo-audit` o `cargo-deny` en CI. |
| TLS/deps exchange | Modernizar dependencias rustls/webpki para evitar pila TLS duplicada/antigua. |
| CI remoto | El workflow existe localmente; falta validar ejecucion en GitHub Actions. |
