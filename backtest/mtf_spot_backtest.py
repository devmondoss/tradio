"""
mtf_spot_backtest.py
--------------------
Backtest MTF Shorts sobre datos Bybit SPOT BTCUSDT (parquet local).

Fuente: data/bybit-spot/processed/btcusdt_m1.parquet

Capas:
  D1  : precio < EMA20 diaria  →  filtro bear/neutral
  H1  : señal topping (shooting_star H1, equal_high H1, sell_climax)
  M1  : entrada de precisión (rejection M1 + OBI confirma vendedores)

Features disponibles en parquet:
  OHLCV: open, high, low, close, volume
  Flow : buy_vol, sell_vol, delta, cvd
  OB   : obi10_mean, obi10_min, obi10_max, obi5_mean, obi20_mean, spread_mean

Stop  : H1_high + margen
Target: 2R fijo → luego trailing
Risk  : 2% por trade, compounding

Uso:
    python backtest/mtf_spot_backtest.py [--capital 500] [--risk 0.02]
    python backtest/mtf_spot_backtest.py --report        (tabla detallada)
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

DATA_FILE = Path(__file__).parent.parent / "data/bybit-spot/processed/btcusdt_m1.parquet"

CAPITAL_INIT  = 500.0
RISK_PCT      = 0.02
FEE_RT        = 0.0007   # 0.07% RT (maker + taker aprox)
FORWARD_M1    = 1440     # 24h max por trade
MIN_STOP_PCT  = 0.30     # % mínimo de stop (descartamos señales con stop muy chico)
MAX_STOP_PCT  = 0.75
TARGET_R      = 2.0      # 2R fijo (más conservador en spot)
COOLDOWN_M1   = 60       # 1h entre trades


# ── Helpers de series de tiempo ───────────────────────────────────────────────

def ema_series(vals: np.ndarray, period: int) -> np.ndarray:
    k = 2 / (period + 1)
    out = np.empty_like(vals)
    out[0] = vals[0]
    for i in range(1, len(vals)):
        out[i] = vals[i] * k + out[i - 1] * (1 - k)
    return out


def atr_series(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    tr = np.maximum(high - low,
         np.maximum(np.abs(high - np.roll(close, 1)),
                    np.abs(low  - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    out = np.empty_like(tr)
    out[:period] = tr[:period].mean()
    k = 1 / period
    for i in range(period, len(tr)):
        out[i] = out[i - 1] * (1 - k) + tr[i] * k
    return out


# ── Resampleo a timeframes superiores ────────────────────────────────────────

def resample(df: pd.DataFrame, freq_ms: int) -> pd.DataFrame:
    """Resamplea barras M1 a un timeframe mayor (H1=3_600_000, H4=14_400_000, D1=86_400_000)."""
    df = df.copy()
    df["tf_ts"] = (df["ts_ms"] // freq_ms) * freq_ms
    g = df.groupby("tf_ts", sort=True)
    out = g.agg(
        open   =("open",   "first"),
        high   =("high",   "max"),
        low    =("low",    "min"),
        close  =("close",  "last"),
        volume =("volume", "sum"),
        delta  =("delta",  "sum"),
    ).reset_index().rename(columns={"tf_ts": "ts_ms"})
    return out


# ── Detección de señales en H1 ────────────────────────────────────────────────

def detect_h1_signals(h1: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula señales de topping en barras H1. Devuelve columnas extra:
    - shoot_star  : shooting star (mecha superior grande, cuerpo pequeño arriba)
    - equal_high  : high actual ≈ high previo (±0.05%)
    - sell_climax : delta acumulado H1 muy negativo (vendedores dominan)

    Requiere que h1 tenga columna 'delta'.
    """
    n = len(h1)
    rng  = (h1["high"] - h1["low"]).values
    body = np.abs(h1["close"] - h1["open"]).values
    wick_up = (h1["high"] - np.maximum(h1["close"], h1["open"])).values

    # Shooting star: mecha sup > 2.5x body, cuerpo en mitad inferior de la vela
    shoot = (wick_up > 2.5 * np.maximum(body, rng * 0.01)) & (body < rng * 0.4)

    # Equal high: high ≈ high hace 1 o 2 barras
    h = h1["high"].values
    equal = (np.abs(h - np.roll(h, 1)) / h < 0.0005) | \
            (np.abs(h - np.roll(h, 2)) / h < 0.0005)
    equal[:2] = False

    # Sell climax: delta H1 muy negativo
    d = h1["delta"].values if "delta" in h1.columns else np.zeros(n)
    d_z = (d - np.mean(d)) / (np.std(d) + 1e-9)
    climax = d_z < -1.5

    h1 = h1.copy()
    h1["shoot_star"] = shoot
    h1["equal_high"] = equal
    h1["sell_climax"] = climax
    return h1


# ── Señal M1 ─────────────────────────────────────────────────────────────────

def is_m1_rejection(row) -> bool:
    """
    Vela M1 de rechazo bajista:
    - Mecha superior > 2x cuerpo
    - Cierra en mitad inferior de la vela
    - OBI confirma flujo vendedor (obi10_mean < 0)
    """
    rng  = row["high"] - row["low"]
    if rng <= 0:
        return False
    body    = abs(row["close"] - row["open"])
    wick_up = row["high"] - max(row["close"], row["open"])
    obi     = row.get("obi10_mean", 0.0) or 0.0
    return (wick_up > 2.0 * max(body, rng * 0.005)) and (obi < -0.05)


# ── Backtest principal ────────────────────────────────────────────────────────

def run_backtest(df: pd.DataFrame, capital_init: float, risk_pct: float, verbose: bool = False):
    df = df.sort_values("ts_ms").reset_index(drop=True)

    # ── D1 y H1 resampling ──────────────────────────────────────────────────
    D1_MS = 86_400_000
    H1_MS = 3_600_000

    d1 = resample(df, D1_MS)
    h1 = resample(df, H1_MS)

    # EMA20 diaria
    d1["ema20"] = ema_series(d1["close"].values, 20)

    # ATR H1
    h1["atr"] = atr_series(h1["high"].values, h1["low"].values, h1["close"].values, 14)

    # Señales H1
    h1 = detect_h1_signals(h1)

    # Construir mapas ts → valor para acceso rápido por barra M1
    d1_map  = dict(zip(d1["ts_ms"], zip(d1["close"], d1["ema20"])))
    h1_idx  = {row.ts_ms: i for i, row in enumerate(h1.itertuples())}

    trades = []
    capital = capital_init
    last_trade_m1 = -999

    in_trade     = False
    entry_price  = 0.0
    stop_price   = 0.0
    target_price = 0.0
    trade_start  = 0
    trade_sig    = ""

    for i, row in enumerate(df.itertuples()):
        ts_ms = row.ts_ms

        # ── ¿En trade? gestionar salida ──────────────────────────────────
        if in_trade:
            bars_elapsed = i - trade_start
            # Stop hit
            if row.high >= stop_price:
                exit_p = stop_price
                pnl_r  = (entry_price - exit_p) / (stop_price - entry_price)
                fee    = capital * risk_pct * FEE_RT
                pnl_usd = capital * risk_pct * pnl_r - fee
                capital += pnl_usd
                trades.append({
                    "entry_ts":  df["ts_ms"].iloc[trade_start],
                    "exit_ts":   ts_ms,
                    "sig":       trade_sig,
                    "entry":     entry_price,
                    "exit":      exit_p,
                    "resultR":   round(pnl_r, 3),
                    "pnl_usd":   round(pnl_usd, 2),
                    "reason":    "stop",
                    "bars":      bars_elapsed,
                })
                in_trade = False
                last_trade_m1 = i

            # Target hit
            elif row.low <= target_price:
                exit_p = target_price
                pnl_r  = (entry_price - exit_p) / (stop_price - entry_price)
                fee    = capital * risk_pct * FEE_RT
                pnl_usd = capital * risk_pct * pnl_r - fee
                capital += pnl_usd
                trades.append({
                    "entry_ts":  df["ts_ms"].iloc[trade_start],
                    "exit_ts":   ts_ms,
                    "sig":       trade_sig,
                    "entry":     entry_price,
                    "exit":      exit_p,
                    "resultR":   round(pnl_r, 3),
                    "pnl_usd":   round(pnl_usd, 2),
                    "reason":    "target",
                    "bars":      bars_elapsed,
                })
                in_trade = False
                last_trade_m1 = i

            # Max time
            elif bars_elapsed >= FORWARD_M1:
                exit_p = row.close
                pnl_r  = (entry_price - exit_p) / (stop_price - entry_price)
                fee    = capital * risk_pct * FEE_RT
                pnl_usd = capital * risk_pct * pnl_r - fee
                capital += pnl_usd
                trades.append({
                    "entry_ts":  df["ts_ms"].iloc[trade_start],
                    "exit_ts":   ts_ms,
                    "sig":       trade_sig,
                    "entry":     entry_price,
                    "exit":      exit_p,
                    "resultR":   round(pnl_r, 3),
                    "pnl_usd":   round(pnl_usd, 2),
                    "reason":    "timeout",
                    "bars":      bars_elapsed,
                })
                in_trade = False
                last_trade_m1 = i

            continue

        if i - last_trade_m1 < COOLDOWN_M1:
            continue

        # ── Filtro D1: bearish/neutral ────────────────────────────────────
        d1_ts = (ts_ms // D1_MS) * D1_MS
        # Buscar el D1 anterior (el d1_ts actual puede no estar cerrado)
        d1_prev_ts = d1_ts - D1_MS
        d1_entry = d1_map.get(d1_prev_ts)
        if d1_entry is None:
            continue
        d1_close, d1_ema = d1_entry
        if d1_close > d1_ema * 1.01:
            continue  # D1 alcista → skip

        # ── Señal H1 ─────────────────────────────────────────────────────
        h1_ts = (ts_ms // H1_MS) * H1_MS
        h1_i  = h1_idx.get(h1_ts)
        if h1_i is None or h1_i < 1:
            continue

        h1_row    = h1.iloc[h1_i]
        h1_high   = h1_row["high"]
        h1_atr    = h1_row["atr"]

        # Necesitamos al menos una señal H1 en la barra anterior
        h1_prev   = h1.iloc[h1_i - 1]
        h1_signal = (bool(h1_prev["shoot_star"]) or
                     bool(h1_prev["equal_high"])  or
                     bool(h1_prev["sell_climax"]))

        if not h1_signal:
            continue

        sig_name = ("shoot" if h1_prev["shoot_star"] else
                    "equal_high" if h1_prev["equal_high"] else "climax")

        # ── Señal M1 de precisión ────────────────────────────────────────
        if not is_m1_rejection(row._asdict()):
            continue

        # ── Calcular stop y target ────────────────────────────────────────
        stop  = h1_high + 0.25 * h1_atr
        dist  = stop - row.close
        if dist <= 0:
            continue

        stop_pct = dist / row.close * 100
        if not (MIN_STOP_PCT <= stop_pct <= MAX_STOP_PCT):
            continue

        target = row.close - TARGET_R * dist

        in_trade    = True
        entry_price = row.close
        stop_price  = stop
        target_price = target
        trade_start = i
        trade_sig   = sig_name

    # Cerrar trade abierto al final
    if in_trade:
        last_row = df.iloc[-1]
        exit_p = last_row["close"]
        pnl_r  = (entry_price - exit_p) / (stop_price - entry_price)
        fee    = capital * risk_pct * FEE_RT
        pnl_usd = capital * risk_pct * pnl_r - fee
        capital += pnl_usd
        trades.append({
            "entry_ts": df["ts_ms"].iloc[trade_start],
            "exit_ts":  last_row["ts_ms"],
            "sig":      trade_sig,
            "entry":    entry_price,
            "exit":     exit_p,
            "resultR":  round(pnl_r, 3),
            "pnl_usd":  round(pnl_usd, 2),
            "reason":   "eod",
            "bars":     len(df) - 1 - trade_start,
        })

    return trades, capital


# ── Estadísticas y reporte ────────────────────────────────────────────────────

def summarize(trades: list[dict], capital_init: float, capital_final: float):
    n = len(trades)
    if n == 0:
        print("Sin trades.")
        return

    wins  = [t for t in trades if t["resultR"] > 0]
    wr    = len(wins) / n * 100
    avg_r = sum(t["resultR"] for t in trades) / n
    max_r = max(t["resultR"] for t in trades)
    min_r = min(t["resultR"] for t in trades)
    equity_pct = (capital_final / capital_init - 1) * 100

    print(f"\n{'─'*55}")
    print(f"  MTF Shorts — Bybit SPOT BTCUSDT  (datos locales)")
    print(f"{'─'*55}")
    print(f"  Trades    : {n}")
    print(f"  Win Rate  : {wr:.1f}%")
    print(f"  Avg R     : {avg_r:.3f}")
    print(f"  Max R     : {max_r:.2f}   Min R: {min_r:.2f}")
    print(f"  Capital   : ${capital_init:.0f}  →  ${capital_final:.0f}  ({equity_pct:+.1f}%)")
    print()

    by_sig = defaultdict(list)
    for t in trades:
        by_sig[t["sig"]].append(t)

    print(f"  {'Señal':<12} {'N':>4} {'WR%':>6} {'AvgR':>7}")
    print(f"  {'-'*12} {'-'*4} {'-'*6} {'-'*7}")
    for sig in sorted(by_sig, key=lambda s: -len(by_sig[s])):
        ts = by_sig[sig]
        w  = sum(1 for t in ts if t["resultR"] > 0)
        print(f"  {sig:<12} {len(ts):>4} {w/len(ts)*100:>5.1f}% {sum(t['resultR'] for t in ts)/len(ts):>7.3f}")

    by_reason = defaultdict(int)
    for t in trades:
        by_reason[t["reason"]] += 1
    print(f"\n  Salidas: {dict(by_reason)}")
    print(f"{'─'*55}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capital", type=float, default=CAPITAL_INIT)
    parser.add_argument("--risk",    type=float, default=RISK_PCT)
    parser.add_argument("--report",  action="store_true", help="Mostrar trades detallados")
    parser.add_argument("--out",     type=str, help="Guardar trades en CSV")
    args = parser.parse_args()

    if not DATA_FILE.exists():
        sys.exit(
            f"No se encontró {DATA_FILE}\n"
            "Ejecuta primero:\n"
            "  python backtest/parse_trades.py\n"
            "  python backtest/parse_orderbook.py\n"
            "  python backtest/build_dataset.py"
        )

    print(f"Cargando {DATA_FILE} ...", end=" ", flush=True)
    df = pd.read_parquet(DATA_FILE)

    # Asegurar que obi10_mean existe (puede ser NaN si OB no está completo)
    for col in ("obi10_mean", "obi5_mean", "obi20_mean", "spread_mean"):
        if col not in df.columns:
            df[col] = np.nan

    df = df.fillna({"obi10_mean": 0.0, "obi5_mean": 0.0})
    print(f"{len(df):,} barras M1")

    trades, capital_final = run_backtest(df, args.capital, args.risk, verbose=args.report)

    summarize(trades, args.capital, capital_final)

    if args.report and trades:
        print("\nTrades detallados:")
        for t in trades:
            ts = pd.Timestamp(t["entry_ts"], unit="ms", tz="UTC").strftime("%Y-%m-%d %H:%M")
            print(f"  {ts}  {t['sig']:<12} entry={t['entry']:.1f}  "
                  f"R={t['resultR']:>6.3f}  ${t['pnl_usd']:>8.2f}  [{t['reason']}]")

    if args.out:
        pd.DataFrame(trades).to_csv(args.out, index=False)
        print(f"\nTrades guardados en {args.out}")


if __name__ == "__main__":
    main()
