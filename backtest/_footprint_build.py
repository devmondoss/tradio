"""
_footprint_build.py — footprint (delta@precio) por día, OPTIMIZADO + cache.
Re-descarga trades tick, computa features de footprint por barra M1, cachea, borra crudo.

Features por barra (pasos 3-4 del embudo orderflow):
  fp_poc            : precio con más volumen DENTRO de la barra
  fp_buy_imb/sell_imb: imbalances diagonales (3:1)
  fp_stack_buy/sell : máximo de imbalances apilados (follow-through)
  fp_unfinished_hi/lo: auction sin terminar en el extremo
  fp_delta_top/bot  : delta en techo/piso de la barra
  fp_absorb_sell    : agresión vendedora ALTA pero precio NO cayó (atrapada) -> paso 4
  fp_absorb_buy     : agresión compradora alta pero precio no subió
  fp_result_sell    : agresión vendedora CON resultado (delta neg + cerró abajo)

Uso: python backtest/_footprint_build.py --start 2025-04-01 --end 2025-04-30 --bucket 10
"""
import gzip, argparse, urllib.request, shutil
from pathlib import Path
from datetime import datetime, timezone, timedelta
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
PERP = ROOT / "data/bybit-perp"
RAW  = PERP / "raw"
FPC  = PERP / "fp_cache"
TRADES_URL = "https://public.bybit.com/trading/BTCUSDT/BTCUSDT{date}.csv.gz"


def download(date_str):
    RAW.mkdir(parents=True, exist_ok=True)
    gz = RAW / f"_fp_{date_str}.csv.gz"
    if not gz.exists():
        req = urllib.request.Request(TRADES_URL.format(date=date_str), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=300) as r, open(gz, "wb") as f:
            shutil.copyfileobj(r, f, length=1 << 20)
    return gz


def footprint_day(gz, bucket=10.0):
    with gzip.open(gz, "rt") as f:
        df = pd.read_csv(f, usecols=["timestamp", "price", "size", "side"])
    df["ts_min"] = (df["timestamp"].astype("float64") * 1000 // 60_000 * 60_000).astype("int64")
    df["pb"] = (df["price"] // bucket * bucket).astype("int64")
    df["is_buy"] = (df["side"] == "Buy")
    df["bv"] = np.where(df["is_buy"], df["size"], 0.0)
    df["sv"] = np.where(df["is_buy"], 0.0, df["size"])

    # buy/sell por (minuto, nivel) en UNA pasada vectorizada
    lvl = df.groupby(["ts_min", "pb"], sort=True).agg(bv=("bv", "sum"), sv=("sv", "sum")).reset_index()
    lvl["vol"] = lvl["bv"] + lvl["sv"]
    lvl["delta"] = lvl["bv"] - lvl["sv"]

    # cierre de la barra (para result vs trapped)
    bar = df.groupby("ts_min").agg(close=("price", "last"), open=("price", "first")).reset_index()
    closes = dict(zip(bar.ts_min, bar.close)); opens = dict(zip(bar.ts_min, bar.open))

    out = []
    for ts, g in lvl.groupby("ts_min", sort=True):
        g = g.sort_values("pb")
        pb = g["pb"].values; bv = g["bv"].values; sv = g["sv"].values
        dl = g["delta"].values; vol = g["vol"].values
        n = len(pb)
        poc = pb[int(np.argmax(vol))]
        buy_imb  = int(np.sum(bv[1:] >= 3.0 * (sv[:-1] + 1e-9)))
        sell_imb = int(np.sum(sv[:-1] >= 3.0 * (bv[1:] + 1e-9)))
        signs = np.sign(dl)
        mxb = mxs = cb = cs = 0
        for s in signs:
            cb = cb + 1 if s > 0 else 0; cs = cs + 1 if s < 0 else 0
            mxb = max(mxb, cb); mxs = max(mxs, cs)
        tot_sv = sv.sum(); tot_bv = bv.sum(); tot = tot_sv + tot_bv + 1e-9
        c = closes[ts]; o = opens[ts]
        # paso 4: agresión vendedora fuerte (sell domina) PERO precio no cayó => atrapada
        sell_dom = tot_sv / tot
        absorb_sell = sell_dom > 0.60 and c >= o        # vendieron fuerte, cerró igual/arriba
        result_sell = sell_dom > 0.60 and c < o         # vendieron fuerte Y cerró abajo
        absorb_buy  = (tot_bv / tot) > 0.60 and c <= o
        out.append({
            "ts_ms": int(ts), "fp_poc": int(poc), "fp_n_levels": n,
            "fp_buy_imb": buy_imb, "fp_sell_imb": sell_imb,
            "fp_stack_buy": mxb, "fp_stack_sell": mxs,
            "fp_unfinished_hi": bool(bv[-1] > 0 and sv[-1] > 0),
            "fp_unfinished_lo": bool(bv[0] > 0 and sv[0] > 0),
            "fp_delta_top": float(dl[-1]), "fp_delta_bot": float(dl[0]),
            "fp_sell_dom": float(sell_dom),
            "fp_absorb_sell": bool(absorb_sell), "fp_result_sell": bool(result_sell),
            "fp_absorb_buy": bool(absorb_buy),
        })
    return pd.DataFrame(out)


def daterange(a, b):
    d0 = datetime.strptime(a, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    d1 = datetime.strptime(b, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    d = d0
    while d <= d1:
        yield d.strftime("%Y-%m-%d"); d += timedelta(days=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True); ap.add_argument("--end", required=True)
    ap.add_argument("--bucket", type=float, default=10.0)
    args = ap.parse_args()
    FPC.mkdir(parents=True, exist_ok=True)
    days = list(daterange(args.start, args.end))
    print(f"Footprint: {len(days)} dias ({args.start} - {args.end})")
    for i, ds in enumerate(days, 1):
        cache = FPC / f"{ds}.parquet"
        if cache.exists():
            continue
        gz = download(ds)
        try:
            fp = footprint_day(gz, args.bucket)
            fp.to_parquet(cache, index=False)
            print(f"  [{i}/{len(days)}] {ds}: {len(fp)} barras", flush=True)
        finally:
            gz.unlink()
    print("listo.")


if __name__ == "__main__":
    main()
