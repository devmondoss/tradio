"""
_footprint.py — construye FOOTPRINT (delta por nivel de precio) desde trades tick.
El M1 normal agrega delta por barra; el footprint lo desglosa por PRECIO dentro de
la barra -> el orderflow real (absorcion, imbalances apilados, auction sin terminar).

Test: python backtest/_footprint.py --date 2026-05-31 --bucket 10
"""
import gzip, io, argparse, urllib.request, shutil
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
TMP  = ROOT / "data/bybit-perp/raw"
TRADES_URL = "https://public.bybit.com/trading/BTCUSDT/BTCUSDT{date}.csv.gz"


def load_ticks(date_str):
    TMP.mkdir(parents=True, exist_ok=True)
    gz = TMP / f"_fp_{date_str}.csv.gz"
    if not gz.exists():
        req = urllib.request.Request(TRADES_URL.format(date=date_str), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=300) as r, open(gz, "wb") as f:
            shutil.copyfileobj(r, f, length=1 << 20)
    with gzip.open(gz, "rt") as f:
        df = pd.read_csv(f, usecols=["timestamp", "price", "size", "side"])
    gz.unlink()
    df["ts_min"] = (df["timestamp"].astype("float64") * 1000 // 60_000 * 60_000).astype("int64")
    df["signed"] = np.where(df["side"] == "Buy", df["size"], -df["size"])
    return df


def footprint_features(df, bucket=10.0):
    """Por cada barra M1: features de footprint derivados del delta por precio."""
    df = df.copy()
    df["pb"] = (df["price"] // bucket * bucket).astype("int64")   # price bucket
    out = []
    for ts, g in df.groupby("ts_min", sort=True):
        # delta y volumen por nivel de precio
        lvl = g.groupby("pb").agg(
            buy=("size", lambda s: s[g.loc[s.index, "side"] == "Buy"].sum()),
            sell=("size", lambda s: s[g.loc[s.index, "side"] == "Sell"].sum()),
            vol=("size", "sum"),
        )
        if lvl.empty:
            continue
        lvl["delta"] = lvl["buy"] - lvl["sell"]
        lvl = lvl.sort_index()                                  # precio ascendente
        prices = lvl.index.values
        dvol   = lvl["vol"].values
        ddelta = lvl["delta"].values
        nb     = lvl["buy"].values
        ns     = lvl["sell"].values

        # bar POC (nivel con mas volumen)
        poc = prices[int(np.argmax(dvol))]
        # imbalance diagonal: bid[i] vs ask[i-1] ratio >=3 (apilado)
        buy_imb  = sum(1 for i in range(1, len(prices)) if ns[i-1] > 0 and nb[i] / (ns[i-1] + 1e-9) >= 3.0)
        sell_imb = sum(1 for i in range(len(prices)-1) if nb[i+1] > 0 and ns[i] / (nb[i+1] + 1e-9) >= 3.0)
        # stacked imbalance: 3+ niveles consecutivos con delta mismo signo
        signs = np.sign(ddelta)
        stk_buy = stk_sell = mx_buy = mx_sell = 0
        for s in signs:
            stk_buy = stk_buy + 1 if s > 0 else 0
            stk_sell = stk_sell + 1 if s < 0 else 0
            mx_buy = max(mx_buy, stk_buy); mx_sell = max(mx_sell, stk_sell)
        # unfinished auction: extremos con compra Y venta (no se agoto)
        unfinished_high = nb[-1] > 0 and ns[-1] > 0
        unfinished_low  = nb[0] > 0 and ns[0] > 0
        # delta extremos del footprint
        out.append({
            "ts_ms": int(ts),
            "fp_poc": poc,
            "fp_n_levels": len(prices),
            "fp_buy_imb": buy_imb,
            "fp_sell_imb": sell_imb,
            "fp_stack_buy": mx_buy,
            "fp_stack_sell": mx_sell,
            "fp_unfinished_high": unfinished_high,
            "fp_unfinished_low": unfinished_low,
            "fp_delta_top": float(ddelta[-1]),     # delta en el techo de la barra
            "fp_delta_bot": float(ddelta[0]),      # delta en el piso
            "fp_max_lvl_delta": float(ddelta.max()),
            "fp_min_lvl_delta": float(ddelta.min()),
        })
    return pd.DataFrame(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-05-31")
    ap.add_argument("--bucket", type=float, default=10.0)
    args = ap.parse_args()

    print(f"Cargando ticks {args.date}...")
    ticks = load_ticks(args.date)
    print(f"  ticks: {len(ticks):,}")
    fp = footprint_features(ticks, bucket=args.bucket)
    print(f"  barras M1 con footprint: {len(fp)}")
    print(f"  features: {list(fp.columns)}")
    print()
    print(fp.head(6).to_string())
    print()
    print("=== resumen del dia ===")
    print(f"  imbalances compra (apilados): {fp['fp_buy_imb'].sum()}  | venta: {fp['fp_sell_imb'].sum()}")
    print(f"  barras con stack>=3 compra: {(fp['fp_stack_buy']>=3).sum()}  venta: {(fp['fp_stack_sell']>=3).sum()}")
    print(f"  auctions sin terminar (techo): {fp['fp_unfinished_high'].sum()}  (piso): {fp['fp_unfinished_low'].sum()}")
