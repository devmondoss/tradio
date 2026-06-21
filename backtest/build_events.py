"""
build_events.py — eventos de microestructura sub-minuto + etiqueta de outcome
==============================================================================
La microestructura agregada a M1 no predecía (probado). Aquí la medimos en su horizonte
NATIVO: construye un panel a 1s (ticks + order book), detecta EVENTOS (no velas) y etiqueta
cada uno con el retorno futuro a 1/5/15 min y MFE/MAE — para luego medir si el evento predice.

Panel 1s por día = ob_1s (mid/microprice/obi/spread/depth) ⨝ agregados de ticks por segundo
(vol, delta, n_trades, max_trade, plus/minus ticks). Umbrales por rolling causal (30 min).

Eventos (cada uno con dir_hint = +1 alcista / -1 bajista esperado a priori):
  big_trade_buy/sell, delta_burst_pos/neg, vol_spike, absorb_ask/bid,
  sweep_up/down (stop-run de extremo 15m), obi_extreme_pos/neg, obi_flip_up/down,
  spread_spike, micro_div_up/down

Salida: data/bybit-perp/events.parquet (una fila por instancia de evento, todos los días).
Uso: python backtest/build_events.py            # todos los días disponibles
     python backtest/build_events.py --max-days 5
"""
import sys, os, argparse
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
PERP = Path(os.environ.get("TRADIO_PERP", str(ROOT / "data/bybit-perp")))
RAW_T = PERP / "raw_trades"; OB_1S = PERP / "ob_1s"
OUT = PERP / "events.parquet"
OOS_MS = int(pd.Timestamp("2026-03-01", tz="UTC").value // 1_000_000)
DAY_S = 86_400


def panel_1s(date):
    t = pd.read_parquet(RAW_T / f"{date}.parquet")
    o = pd.read_parquet(OB_1S / f"{date}.parquet")
    sec = (t["ts_ms"].values // 1000)
    buy = t["side"].values == "Buy"
    td = t["tick_dir"].values if "tick_dir" in t else np.array([""] * len(t))
    g = pd.DataFrame({
        "sec": sec, "size": t["size"].values.astype(float),
        "bsz": np.where(buy, t["size"].values, 0.0).astype(float),
        "ssz": np.where(~buy, t["size"].values, 0.0).astype(float),
        "plus": (td == "PlusTick").astype(float), "minus": (td == "MinusTick").astype(float),
    })
    agg = g.groupby("sec").agg(vol=("size", "sum"), buy_vol=("bsz", "sum"), sell_vol=("ssz", "sum"),
                               n_trades=("size", "size"), max_trade=("size", "max"),
                               plus=("plus", "sum"), minus=("minus", "sum"))
    agg["delta"] = agg["buy_vol"] - agg["sell_vol"]
    o = o.copy(); o["sec"] = o["ts_ms"].values // 1000
    p = o.set_index("sec").join(agg, how="left")
    for c in ["vol", "buy_vol", "sell_vol", "n_trades", "max_trade", "plus", "minus", "delta"]:
        p[c] = p[c].fillna(0.0)
    p["mid"] = p["mid"].ffill()
    return p.reset_index(drop=True)


def detect(p):
    n = len(p)
    mid = p["mid"].values.astype(float)
    delta = p["delta"].values; vol = p["vol"].values; mxt = p["max_trade"].values
    obi = p["obi10"].values; spread = p["spread_bps"].values
    micro = p["microprice"].values
    S = pd.Series
    W = 1800  # 30 min rolling causal
    def rmean(x): return S(x).rolling(W, min_periods=120).mean().values
    def rstd(x):  return S(x).rolling(W, min_periods=120).std().values
    def roll_sum(x, k): return S(x).rolling(k, min_periods=1).sum().values
    def roll_max(x, k): return S(x).rolling(k, min_periods=1).max().values
    def roll_min(x, k): return S(x).rolling(k, min_periods=1).min().values

    d10 = roll_sum(delta, 10); v10 = roll_sum(vol, 10)
    dstd = rstd(delta) * np.sqrt(10) + 1e-9
    vm, vs = rmean(vol), rstd(vol) + 1e-9
    mxm, mxs = rmean(mxt), rstd(mxt) + 1e-9
    prc10 = (mid - S(mid).shift(10).values) / mid  # cambio 10s
    hi15 = roll_max(mid, 900); lo15 = roll_min(mid, 900)
    sp_m, sp_s = rmean(spread), rstd(spread) + 1e-9
    obi_prev = S(obi).shift(10).values

    big = mxt >= (mxm + 3 * mxs)
    vspike = v10 >= 10 * (vm + 3 * vs)   # vol acumulado 10s vs 10×media-1s
    ev = {
        "big_trade_buy":   (big & (delta > 0),  +1),
        "big_trade_sell":  (big & (delta < 0),  -1),
        "delta_burst_pos": (d10 >= 3 * dstd,     +1),
        "delta_burst_neg": (d10 <= -3 * dstd,    -1),
        "vol_spike":       (vspike,              0),
        "absorb_ask":      ((v10 >= 10 * (vm + vs)) & (np.abs(prc10) < 0.0002) & (d10 > 0), -1),  # compran fuerte, no sube → absorción venta
        "absorb_bid":      ((v10 >= 10 * (vm + vs)) & (np.abs(prc10) < 0.0002) & (d10 < 0), +1),  # venden fuerte, no baja → absorción compra
        "sweep_up":        ((mid > S(hi15).shift(1).values) & vspike, -1),  # barre highs → revierte abajo
        "sweep_down":      ((mid < S(lo15).shift(1).values) & vspike, +1),
        "obi_extreme_pos": (obi >= 0.8,          +1),
        "obi_extreme_neg": (obi <= -0.8,         -1),
        "obi_flip_up":     ((obi_prev < -0.5) & (obi > 0.5),  +1),
        "obi_flip_down":   ((obi_prev > 0.5) & (obi < -0.5),  -1),
        "spread_spike":    (spread >= sp_m + 5 * sp_s,        0),
        # micro_lean = posición del microprice dentro del spread (-0.5..+0.5): presión de tope de libro
        "micro_div_up":    (((micro - mid) * 1e4 / (spread * mid + 1e-9)) > 0.25,  +1),
        "micro_div_down":  (((micro - mid) * 1e4 / (spread * mid + 1e-9)) < -0.25, -1),
    }

    # labels forward (bps) + MFE/MAE 15m
    def fwd(h): return (S(mid).shift(-h).values / mid - 1) * 1e4
    f1, f5, f15 = fwd(60), fwd(300), fwd(900)
    fmax = S(mid).iloc[::-1].rolling(900, min_periods=1).max().iloc[::-1].values
    fmin = S(mid).iloc[::-1].rolling(900, min_periods=1).min().iloc[::-1].values
    mfe = (fmax / mid - 1) * 1e4; mae = (fmin / mid - 1) * 1e4
    ts_ms = p["ts_ms"].values

    rows = []
    for name, (mask, hint) in ev.items():
        idx = np.where(mask & np.isfinite(f15))[0]
        # cooldown 60s por tipo (evita contar el mismo evento muchas veces seguidas)
        if len(idx):
            keep = [idx[0]]
            for j in idx[1:]:
                if j - keep[-1] >= 60: keep.append(j)
            idx = np.array(keep)
        for j in idx:
            rows.append((int(ts_ms[j]), name, hint, f1[j], f5[j], f15[j], mfe[j], mae[j]))
    return rows


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--max-days", type=int, default=0); args = ap.parse_args()
    dates = sorted(set(p.stem for p in RAW_T.glob("*.parquet")) & set(p.stem for p in OB_1S.glob("*.parquet")))
    if args.max_days: dates = dates[:args.max_days]
    if not dates: sys.exit("No hay días con ticks+ob_1s.")
    print(f"Días disponibles: {len(dates)} ({dates[0]} → {dates[-1]})")
    allrows = []
    for i, d in enumerate(dates, 1):
        try:
            allrows.extend(detect(panel_1s(d)))
        except Exception as e:
            print(f"  WARN {d}: {e}", file=sys.stderr)
        if i % 20 == 0 or i == len(dates): print(f"  {i}/{len(dates)} días | eventos: {len(allrows):,}", flush=True)
    ev = pd.DataFrame(allrows, columns=["ts_ms", "event", "dir_hint", "fwd_1m", "fwd_5m", "fwd_15m", "mfe_15m", "mae_15m"])
    ev["oos"] = ev["ts_ms"] >= OOS_MS
    ev.to_parquet(OUT, index=False)
    print(f"\n[OK] {OUT}  ({len(ev):,} eventos)")
    print(ev["event"].value_counts().to_string())


if __name__ == "__main__":
    main()
