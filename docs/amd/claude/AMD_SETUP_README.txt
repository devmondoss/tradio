# AMD Setup — Configuración del Backtest
## Estrategia: Accumulation·Manipulation·Distribution (AMD)
## Resultados: $500 → $856 en 30 días (64 trades, WR 81.2%, maker fees)

=============================================================
PARÁMETROS DE DETECCIÓN (equivalentes a strategy.toml)
=============================================================

[FASE 1 — ACUMULACIÓN]
accum_range_min_pct  = 0.04%   # rango mínimo de consolidación
accum_range_max_pct  = 0.30%   # rango máximo
accum_min_bars       = 10      # mínimo de barras M1 en rango
accum_max_bars       = 60      # máximo antes de descartar

  → El CVD durante la acumulación debe ser equilibrado
    (abs(cvd_sum) < 45% del volumen total del rango)

[FASE 2 — MANIPULACIÓN / SPIKE]
manip_min_vr         = 1.5     # Volume Ratio mínimo (vs media 50 barras)
manip_vpin_threshold = 0.30    # VPIN instantáneo |delta|/vol ≥ 0.30
  + CVD DIVERGENTE: precio sube pero delta < 0 (o viceversa)
  + OBI confirma divergencia (obi_l5 < -0.05 si spike UP)

[GATE 1 — dz_spike ≥ 1.0]  ← gate activo en config "ambos gates"
  dz_spike = (close - vwap) / atr
  El spike debe desplazar el precio ≥ 1 ATR del VWAP de sesión

[GATE 2 — liq_ratio < 1.5]  ← gate activo en config "ambos gates"
  liq_ratio = z-score de actividad de liquidaciones en el spike
  < 1.5 → spike "contenido" (market makers absorben, no cascada)
  ≥ 1.5 → cascada de liquidaciones → precio puede continuar → DESCARTAR

[FASE 3 — ENTRY / DISTRIBUCIÓN]
max_wait_bars_after_spike = 10  # máximo 10 barras M1 de espera
dist_min_vr               = 1.0 # VR mínimo en la barra de entry
dist_cvd_slope            = 5.0 # CVD slope OLS 20 barras debe confirmar
dist_obi_confirm          = 0.08 # OBI debe confirmar la dirección

[GESTIÓN]
stop    = spike_extreme ± 0.08% buffer
target  = entry ± 2.0 × risk  (RR mínimo 2:1)
cooldown_bars = 30             # 30 barras sin nuevas señales tras entry

=============================================================
SIZING CON $500 DE CAPITAL
=============================================================

Método: Fixed Fractional con cap de leverage
  risk_pct       = 1% del capital = $5 por trade
  qty            = $5 / risk_per_btc
  max_notional   = $5,000 (cap 10x leverage)
  qty_final      = min(qty, $5000 / entry)
  fee_maker      = notional × 0.04% (Binance post-only con BNB)

=============================================================
COLUMNAS DEL CSV amd_trades_500usd.csv
=============================================================

#               Número de trade (orden cronológico)
fecha_utc       Timestamp de entry (UTC)
lado            LONG o SHORT
sesion          Asia / London / LondonNyOverlap / NewYork / LateNY
entry           Precio de entrada (USD)
stop            Precio de stop loss (USD)
target          Precio de take profit (USD)
rr              Risk/Reward ratio real
liq_ratio_spike liq_ratio en el momento del spike (gate < 1.5)
dz_spike        Desplazamiento VWAP normalizado (gate ≥ 1.0)
barras_acum     Barras M1 de consolidación antes del spike
rango_pct       % del precio del rango de consolidación
qty_btc         Tamaño de posición en BTC
notional_usd    Valor nocional de la posición (USD)
leverage_x      Leverage efectivo (notional / $500)
risk_usd        Riesgo real en USD (puede ser < $5 si cap activo)
fee_usd         Fee de entrada + salida (maker 0.04% rt)
resultado       TARGET_HIT o STOP_HIT
barras_exit     Barras M1 hasta alcanzar target/stop
r_multiple      Resultado en R (+2.0 o -1.0)
pnl_usd         PnL neto en USD (después de fees)
equity_usd      Capital acumulado después de este trade

=============================================================
ESTADÍSTICAS FINALES (ambos gates activos)
=============================================================

Capital inicial:    $500.00
Capital final:      $856.44
PnL total:         +$356.44
ROI 30 días:        +71.3%
Fees totales:        $83.54 (maker) / $208.84 (taker)

Señales totales:     64 (2.1/día)
Win rate:           81.2% (52W / 12L)
avgR:              +1.44R
Expectancy:        +1.44R por trade
Max drawdown:       -$8.89 (-1.8%)
Max racha ganadora:  11 trades seguidos
Max racha perdedora:  2 trades seguidos

Por sesión (maker):
  LondonNyOverlap:  n=11  WR=91%  PnL=$70
  Asia:             n=21  WR=81%  PnL=$107
  London:           n=15  WR=80%  PnL=$93
  NewYork:          n=13  WR=77%  PnL=$67

Por dirección:
  SHORT: n=33  WR=85%  avgR=+1.54
  LONG:  n=31  WR=77%  avgR=+1.32

=============================================================
ADVERTENCIA
=============================================================

Estos resultados son de un backtest sobre datos SINTÉTICOS calibrados
con parámetros reales del sistema FlowSurface. Los números reales en 
live serán más conservadores por:
  - Slippage en la ejecución
  - Spreads variables (especialmente en Asia/LateNY)
  - Datos de liq_ratio/dz son proxies, no los valores exactos del sistema
  - El sistema live tiene 14 días de datos reales con WR ~67% (n=4 con gates)

Roadmap hasta live trading:
  1. 30+ días datos live → activar gates liq_ratio y dz
  2. WR ≥ 50% con gates (n≥20) → paper trading
  3. WR ≥ 65%, n ≥ 100 → evaluar live con tamaño mínimo
