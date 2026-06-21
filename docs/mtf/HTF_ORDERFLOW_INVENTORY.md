# HTF Orderflow — Inventario de datos y artefactos

Estado al 2026-06-19. Rama: `htf-orderflow`. Estrategia de **orderflow/microestructura
contrarian** construida desde cero sobre el orderbook crudo de Bybit perp (BTCUSDT).
Veredicto y metricas en [HTF_ORDERFLOW_FADE_2026-06-19.md](HTF_ORDERFLOW_FADE_2026-06-19.md).

## 1. Datos crudos (materia prima) — `data/bybit-perp/`

| Ruta | Contenido | Cobertura | Tamaño |
|---|---|---|---|
| `ob_1s/*.parquet` | Orderbook **agregado a 1s** (mid, spread, obi5/10/25, depth_bid/ask25, microprice) | 365 dias (2025-06-19 → 2026-06-18) | 1.8 GB |
| `raw_trades/*.parquet` | **Tick-by-tick** (ts, price, size, side, tick_dir) | 365 dias | 2.3 GB |
| `_ob_tmp/*_ob500.data.zip` | Orderbook **crudo L2 por-evento** (ob500, 100ms, snapshot+deltas) | **solo 6 dias** (2025-07-20→25) | 1.6 GB |
| `processed/btcusdt_perp_m1.parquet` | Dataset M1 fusionado **previo** (100+ cols ICT/footprint) — NO usado aqui | 365 dias | 384 MB |

**Nota clave:** el L2 crudo (ob500) del año completo NO esta en disco — se consumio
al generar `ob_1s` y se borro (~90 GB). Es **re-descargable** con
`backtest/download_raw.py` (fuente `quote-saver.bycsi.com/orderbook/linear/BTCUSDT/`).

## 2. Datasets derivados (features HTF) — `backtest/htf/`

| Archivo | Qué es | Barras |
|---|---|---|
| `htf_5m_full.parquet` | Features HTF causales, marco 5m (+mid_high/low) | 105.096 |
| `htf_15m_full.parquet` | idem 15m (+mid_high/low) | 35.034 |
| `htf_1h_full.parquet` | idem 1h (+mid_high/low) | 8.760 |
| `htf_4h_full.parquet` | idem 4h | 2.191 |
| `htf_1d_full.parquet` | idem 1d | 366 |
| `htf_15m_is.parquet` / `_oos.parquet` | Subsets viabilidad (primeros/ultimos 90d) | ~8.6k c/u |
| `probe_l2.parquet` | Features L2 por-evento (OFI, cancel/add) a 1s, 6 dias | 518.397 |

Features por barra: `mid_open/close/high/low`, `obi5/10/25_twa`, `depth_imb`,
`depth_ratio`, `micro_prem_bps`, `obi25_sign_stab`, `delta`, `taker_buy_frac`,
`spread_bps`, `vol`, `n_trades`. Todo **causal** (solo datos dentro de la barra).

## 3. Scripts (pipeline reutilizable) — `backtest/htf/`

| Script | Función |
|---|---|
| `build_htf_features.py` | Builder causal 1-marco + EDA de IC |
| `build_htf_multi.py` | Builder multi-marco (5m/15m/1h/4h/1d) en una pasada |
| `augment_ohlc.py` | Agrega mid_high/mid_low (para stop/target) desde ob_1s |
| `backtest_htf_fade.py` | Backtest por deciles, fee real |
| `sweep_htf_fade.py` | Barrido selectividad(colas) × holding |
| `validate_htf_fade.py` | Validación robusta: no-solapado, mensual, walk-forward, t-stats |
| `probe_l2_events.py` | Probe L2 crudo: OFI L1 + cancel/add asym vs fwd-return |
| `probe_ofi_strategy.py` | OFI persistente a minutos + OFI × nivel clave (PDH/PDL/POC/VWAP) |
| `backtest_system.py` | **Backtest de sistema**: WR, avgR, RR, expectancy, PF, DD, equity $500 |

## 4. Resultados (resumen)

- **Fade agregado (depth_imb + delta):** señal contrarian real y estable IS/OOS
  pero **sub-fee**. Backtest de sistema $500: 1h = breakeven (-8.5%, DD -41%);
  15m = -83%; 5m = -95%. No desplegable.
- **OFI crudo (L2):** IC +0.134 @1s (3-5× el agregado) pero **HFT-only** (vive en segundos).
- **OFI × nivel clave:** gross +2.85 bps, t=3.1, hit 62% — **estadísticamente real**
  pero net maker -1.15 bps (sigue sub-fee retail). Único lead vivo; requiere
  descargar el año de ob500 para backtest de sistema completo.

## 5. Qué está listo / pendiente

- ✅ Pipeline DE+DS completo, causal, sin lookahead, documentado.
- ✅ Datasets derivados de los 5 marcos + probe L2 de 6 dias, en disco.
- ✅ Backtest de sistema con todas las métricas (WR/avgR/RR/PF/DD/equity).
- ⏳ **Pendiente (decisión usuario):** descargar año completo ob500 (~90 GB) para
  validar OFI × nivel clave como sistema; o cerrar el frente orderbook-direccional.

## 6. Cómo reproducir

```bash
# features (ya generadas)
python backtest/htf/build_htf_multi.py --days 0 --bars 5min,15min,1h,4h,1d
python backtest/htf/augment_ohlc.py 5min,15min,1h
# backtest de sistema (metricas + barrido)
python backtest/htf/backtest_system.py --data backtest/htf/htf_1h_full.parquet --zwin 48 --timeout 16 --capital 500
# probe microestructura L2 (6 dias)
python backtest/htf/probe_l2_events.py --bar_ms 1000
python backtest/htf/probe_ofi_strategy.py --near_bps 5
```
