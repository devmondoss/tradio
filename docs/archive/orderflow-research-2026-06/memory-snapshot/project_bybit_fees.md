---
name: project-bybit-fees
description: Bybit SPOT fees oficiales (0.1%/0.1% no-VIP) + bug de fees en backtest que infló el edge. Critico antes de live.
metadata: 
  node_type: memory
  type: project
  originSessionId: 6e68e5ac-3db3-410e-b29a-fc0c3545801d
---

# Bybit SPOT fees — realidad vs backtest (2026-06-18)

Fuente oficial (doc "Spot Trading: Fees Explained", actualizado 2025-12-17), confirmada por el usuario.

## Tarifas reales (tier básico, usuario nuevo)
- **No-VIP SPOT: Maker 0.1% / Taker 0.1%** — todos los pares. Maker = taker en básico.
- Fórmula: `fee = cantidad ejecutada × rate`. **Se cobra en el activo COMPRADO** (compras BTC → fee en BTC; vendes BTC→USDT → fee en USDT).
- **Órdenes no ejecutadas / canceladas NO pagan fee** → un límite que no llena es gratis.
- Maker puede bajar hasta 0.00% en tiers VIP altos; taker baja más lento.
- Pago de fees con token **MNT**: hasta −25% (0.20% → 0.15% round-trip).

## El bug de fees en el backtest (CRÍTICO)
`mtf_basics.py` / `mtf_longs.py`: `pnl_usd = entry_risk * pnl_r - entry_risk * FEE_RT` con FEE_RT=0.0007.
- Resta 0.0007 R por trade. La fee real es sobre el **notional**: `fee_r = FEE_RT * entry/dist = FEE_RT / stop_pct`.
- A stop 0.5% y round-trip 0.20%: **fee_r ≈ 0.42 R por trade**, no 0.0007.
- Fix: `entry_risk * FEE_RT * ep/dist` (y FEE_RT debe ser 0.0020, no 0.0007).

## Edge real (shorts OOS, gross +0.305R, stop medio 0.506%)
| Round-trip | fee_r | Net AvgR |
|---|---|---|
| 0.07% (asumido, falso) | 0.148 | +0.157 |
| 0.10% | 0.211 | +0.094 |
| **0.20% (tu tier, market×2)** | **0.422** | **−0.117 ❌** |

**A tier básico entrando a market, shorts Y longs PIERDEN dinero.** Breakeven exige rt < 0.154% al stop 0.5%.

## Implicaciones estratégicas
1. La fee fija (~0.4R) hace que **frecuencia = pasivo**. El sistema estaba optimizado para frecuencia (compounding) bajo fee falsa → hay que re-optimizar para **R/trade**: stops más anchos (bajan fee_r), targets más altos, menos trades.
2. Calibraciones previas (target 2R, stop 0.40×ATR ajustado, descarte de limit) se hicieron bajo fee falsa → revisar todas.
3. **Limit/maker es la ruta a fee viable** pero solo en VIP (maker→0%); en básico maker=taker=0.1%, el límite solo ayuda por mejor precio + cancels gratis.
4. **Spot shorting**: en spot puro NO se puede vender en corto sin margin/borrow (interés extra). Revisar si "MTF SPOT Shorts" es ejecutable en la cuenta del usuario o requiere spot-margin.

## Edge real desglosado (shorts @ fee futuros 0.11%)
avg WIN +1.24R / avg LOSS −1.21R / WR 52.5% → AvgR neto **+0.075R** (verificado, NO bug).
Moneda al aire cargada 2.5pp — real pero fino. PnL: spot $500→$25 (pierde), futuros $500→$2,423 (+385% compounding).
Longs negativos incluso a 0.11% (−25R). Solo shorts-en-futuros tiene pulso.
El edge descansa en pocos meses (marzo 2026 = mitad del año) → frágil.

## Plan (ver docs/mtf/MTF_SPOT_EDGE_REALITY_Y_PLAN.md)
1. **Subir AvgR vía salidas — HECHO (shorts).** Fix: target FIJO 2.5R + SIN CVD exit (cortaba ganadores a ~1.3R). AvgR shorts +0.072→+0.121 OOS, IS≈OOS robusto. Aplicado a mtf_basics.py (TARGET_R=2.5, regime+CVD eliminados). Trailing/BE/parcial fallaron.
2. **NO hay data histórica de futuros descargable** → cambio de plan: validar FORWARD en PAPER sobre bybit_linear. Prep HECHO (2026-06-18): perfil `mtf_spot_futures_paper` en monitor_config.rs (solo shorts), detector actualizado (TARGET_R 2.5, FEE_RT 0.0011, CVD exit eliminado en shorts). Runbook: docs/mtf/MTF_FUTURES_PAPER_RUNBOOK.md. BUILD + PARITY HECHOS (2026-06-18): compila con toolchain GNU (`cargo +stable-x86_64-pc-windows-gnu build -p monitor`, NO con msvc que no tiene linker). Parity Python↔Rust shorts = **769/769, 0 diferencias, ok=True**. Fixes en mtf_spot_detector.rs para lograrlo: target 2.5R, CVD exit eliminado (short), timeout 1200 añadido, **London re-habilitada en short_session** (estaba en v4 excluyendo London → era la divergencia raíz), fee 0.0011. mtf_spot_backtest.py (ref parity) alineado + fee-bug corregido.
PENDIENTE: commit+push a main (Railway auto-redeploya; usuario crea servicio nuevo monitor-fut-paper con env MONITOR_EXCHANGE=bybit_linear MONITOR_PROFILE=mtf_spot_futures_paper). OJO commit: working tree mezcla este trabajo con sesión previa (live_executor.rs/slack_alert.rs untracked) — commit parcial rompería build. Railway CLI no autenticado (login es del usuario). PnL config final shorts @ futuros: $500→$3,416 (AvgR +0.125, IS≈OOS). ← SIGUIENTE: verificar build + deploy paper
3. **LONGS DESCARTADOS.** Análisis de salidas (_longs_exits.py): todas las variantes negativas en IS → sin edge robusto. Shorts y longs NO simétricos (longs revierten, no corren; el CVD exit los ayudaba). Muere "shorts+longs paralelo". Solo shorts.
4. Bajar fee: MNT −25%, VIP, maker/límite.
5. Recién después: paper live → Rust → real.

Features: suficientes para salidas y refinar entradas. FALTA solo dataset de futuros (OI/funding/libro perp) si migramos.

## VEREDICTO FUTUROS (2026-06-18) — EL EDGE TRANSFIERE ✅
Sí hay histórico de futuros: trades en public.bybit.com/trading/ (oficial, año completo) + order book en quote-saver.bycsi.com/orderbook/linear/ (ob500 hasta 2025-08-20, ob200 después, año completo). Bybit NO publica OB oficial; el de terceros cubre el año.
Pipeline: `backtest/build_futures_dataset.py` (descarga incremental) + `crates/ob_parser` Rust paralelo (configurable via --ob-dir/--cache-dir/--out/--start/--end, ~30x más rápido que Python, mismo OBI: mean_diff 0.0001). Compilar con toolchain GNU. Dataset: data/bybit-perp/processed/btcusdt_perp_m1.parquet (87 cols, mismas features que spot via enrich()).
**Backtest OOS futuros real (Mar-May 2026, 92d, OBI real): WR 48%, AvgR +0.093, win+1.52/loss-1.23, 3 tpd, n=273.** Casi calcado al spot (+0.107). El short es real y transferible a futuros. Config: target 1.8R, sin CVD, timeout 1200, fee 0.0011.
Histórico completo Ene2025-Jun2026 corriendo (build_futures_dataset, ~4-5h).

## DATASET FUTUROS COMPLETO + WALK-FORWARD (2026-06-18)
`data/bybit-perp/processed/btcusdt_perp_m1.parquet`: 767,520 barras, Ene2025-Jun2026, 100% M1 (0 huecos), OBI 100%, VAH 100%, 87 cols. Construido con build_futures_dataset.py + ob_parser Rust paralelo (~30x). Zips crudos ya borrados.
**Walk-forward futuros real (fee 0.0011):** IS(ene25-feb26) WR45% AvgR +0.015 | OOS(mar-may26) WR50% AvgR +0.148 | FULL WR46% AvgR +0.045. EDGE REAL PERO CHOPPY/dependiente de régimen: meses brutales (abr2025 -0.375R = uptrend arrolla al short) y excelentes (mar2026 +0.345). NO desplegable consistente sin filtrar régimen.
Orderflow: catálogo en docs/ORDERFLOW_CATALOGO.md, footprint prototipado (_footprint.py: delta@precio, imbalances, auctions). Toolkit base ya en el dataset (POC/VAH/VAL/OBI/absorción/vpin/big_trades). Falta: footprint integrado, tickDirection, OBI profundo, HVN/naked POC, OI/funding/liq (fuente nueva).

## Estado / PRÓXIMO
Worklog completo: docs/mtf/MTF_WORKLOG_2026-06-18.md. SIGUIENTE research (no más descargas): (1) filtro de régimen para matar meses-desastre, (2) integrar orderflow (footprint/tickDirection/OBI profundo) al dataset completo, (3) re-calibrar thresholds sobre futuros. Paper live corriendo (mtf_spot_futures_paper, bybit_linear, market_type=linear).
