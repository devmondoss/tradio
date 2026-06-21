---
name: project-htf-orderflow
description: Estrategia HTF fade desde orderbook crudo - senal contrarian real pero sub-fee, no desplegable; pipeline reutilizable en backtest/htf
metadata:
  type: project
---

Estrategia HTF construida desde cero explotando el orderbook historico crudo
(`data/bybit-perp/ob_1s` 1Hz + `raw_trades`), rama `htf-orderflow`. Pipeline en
`backtest/htf/` (build_htf_features, build_htf_multi, backtest/sweep/validate_htf_fade).
Datasets derivados: `backtest/htf/htf_{5m,15m,1h,4h,1d}_full.parquet`.

**Hallazgo:** el orderbook a HTF en BTC perp es CONTRARIAN (fade), no de seguimiento
— opuesto al manual HTF. IC negativo estable IS/OOS en depth_imb, obi25, delta.
**Veredicto:** senal estadisticamente real (15m: IC -0.03/-0.05, t~3-4) pero
economicamente sub-fee — el movimiento capturable (~3-6 bps) < 11 bps fee taker, y
se desvanece a 1h+. El "edge" de 15-38 bps del barrido por colas era artefacto de
SOLAPAMIENTO; con trades no solapados t-stats en [-0.73, +0.74] (cero). Maker da
~breakeven/+2.6bps dentro del ruido.

**Why:** confirma de forma independiente y rigurosa la conclusion previa "sin edge
desplegable en BTC" [[project-mtf-strategy]], esta vez desde el orderbook crudo sin
heredar supuestos ni lookahead [[project-orderflow-funnel]].

**How to apply:** no perseguir conditioning/regime fishing para fabricar positivo
(es el trap del lookahead $74.9K). Limitacion real: depth L25 agregada a 1Hz pierde
dinamica L2 — probado el stream ob500 crudo (6 dias en _ob_tmp, año re-descargable
via backtest/download_raw.py). RESULTADO L2: OFI IC +0.134@1s (3-5x el agregado)
pero vive en SEGUNDOS (HFT-only). OFI NO persiste a minutos. OFI x NIVEL CLAVE
(PDH/PDL/POC/VWAP, <=5bps) SI concentra: cofi180@5min gross +2.85bps t=3.1 hit62%
REAL pero net maker -1.15 (break-even ~2.85bps RT, solo a maker MM/VIP/rebate; +
sesgo de fill optimista). Veredicto: señal real en todos los niveles pero SIEMPRE
sub-fee. Pipeline en backtest/htf/probe_l2_events.py + probe_ofi_strategy.py.
Ver docs/mtf/HTF_ORDERFLOW_FADE_2026-06-19.md
