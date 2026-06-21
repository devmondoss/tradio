# MTF Worklog — 2026-06-18 (la sesión grande: fees, futuros, dataset real)

> Día clave. De "tenemos $119K en backtest" a "el edge es real pero fino, validado sobre
> 17 meses de futuros reales". Resumen de TODO lo descubierto, hecho y el estado actual.

---

## 1. Hallazgos que cambiaron el panorama

### 1.1 Bug de fees (el gran descubrimiento)
- El backtest restaba `entry_risk * 0.0007` (fee sobre el RIESGO). La fee real es sobre el
  **NOTIONAL**: `fee_r = FEE_RT * entry/dist = FEE_RT / stop_pct ≈ 0.4R` por trade.
- El edge reportado estaba inflado ~2×. AvgR real ≈ la mitad.
- Corregido en `mtf_basics.py`, `mtf_longs.py`, `mtf_spot_backtest.py`.

### 1.2 Fees reales de Bybit (verificado oficial)
- **Spot no-VIP: 0.1% maker / 0.1% taker** → round-trip 0.20% a market.
- **Futuros (perp) taker ~0.055%** → round-trip 0.11%.
- A 0.20% (spot básico) el sistema **PIERDE**. A 0.11% (futuros) es **rentable**.
- Bybit NO publica order book histórico oficial; el de spot/futuros viene de terceros (quote-saver.bycsi.com).

### 1.3 Spot no sirve para este sistema
- En spot puro NO se puede shortear ni apalancar. El short necesita futuros.
- Spot + $500 + stop 0.5% topa el riesgo a ~0.5%/trade (no 2%) — sin leverage.

### 1.4 Sizing = apalancamiento, no edge
- Score sizing NO cambia WR/AvgR (idénticos en toda escala) — solo el capital.
- El "$119K" era artefacto de leverage + fee falsa. Simplificado a `[1,1,1,1,1.5]` (boost solo sc4, único bucket robusto). Ver `MTF_SPOT_SIZING_AUDIT.md`.

### 1.5 Optimización de salidas (Paso 1)
- El CVD exit + regime target **cortaban ganadores** a ~1.3R. Quitarlos + target fijo subió AvgR.
- Barrido de targets → **1.8R = mejor balance** WR/AvgR/volumen, IS≈OOS más estrecho.
- Config final short: **target 1.8R fijo, sin CVD exit, timeout 1200, London activa**.

### 1.6 Longs descartados
- Análisis de salidas: todas las variantes negativas en IS. Shorts y longs NO simétricos
  (longs revierten, no corren). Sin edge robusto. Solo shorts.

---

## 2. Lo que construimos

### 2.1 Detector Rust alineado + paper live
- `mtf_spot_detector.rs`: target 1.8R, CVD eliminado (short), timeout 1200, London re-habilitada, fee 0.0011.
- Perfil `mtf_spot_futures_paper` en `monitor_config.rs`: corre el detector spot sobre `bybit_linear` (futuros), solo shorts.
- Logging arreglado: `market_type` real (`linear`/`spot`) según exchange.
- **Parity Python↔Rust: 891/891, 0 diferencias.**
- Compila con toolchain GNU (`cargo +stable-x86_64-pc-windows-gnu`), NO con msvc (sin linker).
- **Paper live corriendo** en Railway (`mtf_spot_futures_paper`, bybit_linear, mainnet real, BTC ~$63k).

### 2.2 Pipeline de datos de futuros
- `build_futures_dataset.py`: descarga incremental (trades oficiales + OB terceros) → parquet M1.
- `crates/ob_parser` (Rust + rayon): parser de order book paralelo, configurable (--ob-dir/--cache-dir/--out/--start/--end). ~30× más rápido que Python, mismo OBI (mean_diff 0.0001).
- Reusa `compute_spot_features.enrich()` → mismas 81 features que spot.

### 2.3 Dataset de futuros COMPLETO
- `data/bybit-perp/processed/btcusdt_perp_m1.parquet`
- **767,520 barras | Ene 2025 → Jun 2026 | 100% cobertura M1 (0 huecos) | OBI 100% | VAH 100% | 87 cols.**

### 2.4 Orderflow
- Catálogo completo: `docs/ORDERFLOW_CATALOGO.md` (todo lo explotable de trades + OB L2).
- **Footprint prototipado** (`_footprint.py`): delta@precio, imbalances diagonales/apilados, auctions sin terminar.

### 2.5 Limpieza
- Purga de disco: 120GB → 2GB (target/, .git gc, dataset ETH, crudo spot). Liberados ~118GB.

---

## 3. VEREDICTO: walk-forward sobre futuros reales (fee 0.11%)

| Período | n | WR | AvgR neto | TotalR |
|---|---|---|---|---|
| IS (Ene2025–Feb2026) | 1111 | 44.9% | **+0.015** | +16.6 |
| OOS (Mar–May2026) | 322 | 49.7% | **+0.148** | +47.8 |
| FULL (533 días) | 1433 | 46.0% | +0.045 | +64.4 |

**El edge transfiere a futuros (OOS incluso mejor que spot +0.107), PERO es choppy y dependiente de régimen.** Curva mensual muy variable: meses brutales (abr 2025 −0.375R, WR 32% = uptrend que arrolla al short) y excelentes (mar 2026 +0.345). El OOS lo cargó marzo 2026. Acumulado cayó a −36R a mediados 2025 antes de recuperar a +64R.

**Conclusión: real pero fino. NO desplegable como estrategia consistente sin filtrar régimen.**

---

## 4. Estado actual (qué tenemos)

| Activo | Estado |
|---|---|
| Dataset spot | `data/bybit-spot/processed/btcusdt_m1.parquet` (intacto) |
| Dataset futuros | `data/bybit-perp/processed/btcusdt_perp_m1.parquet` (completo, 0 huecos) 🆕 |
| Short backtest | `mtf_basics.py` (1.8R, fee corregida) |
| Detector Rust | en paridad, paper live en futuros mainnet |
| Footprint | prototipo funcionando |
| Disco | 139GB libres |

---

## 5. PENDIENTE — qué probar (research sobre los datos, no más descargas)

### Prioridad alta (atacar el edge choppy)
1. **Filtro de régimen/tendencia** — matar meses-desastre (uptrends como abr 2025). Probar D1 EMA, H4 EMA, no-shortear-en-TrendUp/Expansion-alcista sobre los 17 meses de futuros.
2. **Integrar orderflow al dataset completo:** footprint (delta@precio), tickDirection (gratis, en el crudo), OBI profundo (50/100/200 — futuros tiene la profundidad).

### Prioridad media
3. **Explorar features de orderflow sin usar** (absorción, big_trades, POC, vpin, stacked_imb) como gates o boosts — ¿discriminan los buenos setups?
4. **Volume profile completo:** HVN, naked POC, LVN above.
5. **Re-calibrar score/thresholds sobre futuros** (los Q50 actuales son de spot; futuros tiene otra distribución de volumen).

### Prioridad baja / requiere fuente nueva
6. **Futuros-exclusivo:** OI, funding, liquidaciones, basis (API Bybit / Tardis).
7. **Setups compuestos:** sweep+reclaim, absorción en VAH, delta divergence en swing.

### Validación / despliegue
8. Forward del paper live en futuros (acumulando data propia + comparar con backtest).
9. Re-calibrar el target/filtros sobre el dataset de futuros (no spot).

---

## Archivos clave de esta sesión
- `backtest/build_futures_dataset.py`, `backtest/_footprint.py`, `backtest/_shorts_exits.py`, `backtest/_recalibrate_fees.py`, `backtest/_runner_discriminant.py`
- `crates/ob_parser/src/main.rs` (configurable)
- `data/src/strategy/detectors/mtf_spot_detector.rs` (1.8R)
- `docs/ORDERFLOW_CATALOGO.md`, `docs/mtf/MTF_SPOT_EDGE_REALITY_Y_PLAN.md`, `docs/mtf/MTF_FUTURES_PAPER_RUNBOOK.md`, `docs/mtf/MTF_SPOT_SIZING_AUDIT.md`
