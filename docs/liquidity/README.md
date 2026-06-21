# 📂 Estrategia de Provisión de Liquidez — BTCUSDT perp

Toda la documentación de la estrategia de liquidez vive aquí.

## Por dónde empezar
- **[SESSION_HANDOFF_LIQUIDITY.md](SESSION_HANDOFF_LIQUIDITY.md)** ← **empieza aquí** (estado actual,
  dudas abiertas, infra live, pendientes, cómo retomar).
- **[LIQUIDITY_STRATEGY.md](LIQUIDITY_STRATEGY.md)** — doc maestro: config final, resultados, hallazgos,
  análisis de timeframe, fill ratio, parámetros, fix de targets estructurales.

## Cómo se llegó aquí (el camino)
- **[STRATEGY_BATCH_VERDICT_2026-06-20.md](STRATEGY_BATCH_VERDICT_2026-06-20.md)** — el reencuadre
  "estrategia ≠ predictor" y cómo apareció el primer candidato (H1).
- **[CATALOG_CLOSURE_2026-06-20.md](CATALOG_CLOSURE_2026-06-20.md)** — cierre de las 21+4 hipótesis;
  3 convergen en el mismo edge: liquidity provision (H1/H5/H21).

## Código relacionado (fuera de docs)
- `backtest/_listas2.py`, `backtest/liquidity_app_backtest.py`, `backtest/_consolidated.py`, `backtest/_filters.py`
- `live/levels.py`, `live/paper_liquidity.py`, `live/Dockerfile`, `live/README.md`
- `migrations/liquidity_paper.sql`
- `apps/trade-lab/` (app visual, tab único de liquidez)
- `Dockerfile.liquidity`, `railway.toml`, `railway.liquidity-paper.env.example`

## Contexto general (NO movido — sigue en docs/orderflow/)
`EDGE_VERDICT_2026-06-19.md`, `FEATURE_INVENTORY.md`, `STRATEGY_SET.md`, `02_catalogo_21_hipotesis.md`,
`01_opening_range_4_variants.md` — research base de orderflow del que salió todo esto.
