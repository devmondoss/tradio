# Memory Index

- [Orderflow Edge Verdict (año completo)](project_orderflow_edge_verdict.md) — ⛔ VEREDICTO: BTC perp orderflow NO tiene edge direccional desplegable IS/OOS; 3 frentes (eventos/derivados/ML capstone OOS Spearman 0.0008) convergen <1bps vs 11bps fee. Ver docs/orderflow/EDGE_VERDICT_2026-06-19.md

- [MTF Strategy & Data Pipeline](project_mtf_strategy.md) — ⚠️ el "$74.9K" era LOOKAHEAD H1/H4 (corregido causal 2026-06-19); sin edge desplegable en BTC; infra sidecar/paridad sí construida. Ver docs/mtf/MTF_WORKLOG_2026-06-19_lookahead.md
- [Live Trading Infrastructure](project_live_infra.md) — módulos Rust para ejecutar órdenes reales en Bybit SPOT, kill switch, Slack, UI panel. Pendiente: migración SQL + testnet
- [User Profile](user_profile.md) — ingeniero de datos, trabaja con Claude como científico de datos, stack Rust/Python/React/Supabase
- [Bybit Fees & Backtest Fee Bug](project_bybit_fees.md) — fees reales 0.1%/0.1%, bug que infló el edge, sistema pierde a tier básico/market
- [Orderflow Funnel Sweep](project_orderflow_funnel.md) — qué features de orderflow sirven vs no (validado IS/OOS); sistema refinado régimen+agresión+veto absorb_buy; OB L2 a la entrada NO generaliza
- [HTF Orderflow Fade](project_htf_orderflow.md) — estrategia HTF desde orderbook crudo: contrarian real pero sub-fee (señal vive en minutos, no bate 11bps), no desplegable; pipeline reutilizable en backtest/htf/
