"""
_scalp_fp.py — features derivadas del FOOTPRINT por barra (para setups de scalping).
================================================================================
Lee processed/{sym}_perp_{tf}_footprint.parquet (prices[]/buy[]/sell[] por nivel)
y precomputa, por barra, las features que NO están en el M1:

  diagonal imbalance (footprint clásico):
    buy_imb  en nivel p  ⟺  buy[p]  >= R * sell[p-bin]   (compra agresiva pega al ask)
    sell_imb en nivel p  ⟺  sell[p] >= R * buy[p+bin]    (venta agresiva pega al bid)
  stacked = >=3 niveles CONSECUTIVOS con el mismo imbalance.
    stk_buy_len/hi/lo   stk_sell_len/hi/lo   (zona del stack para el breakout)

  absorción (alto volumen agresor sin desplazar precio):
    absorb_buy_v  = volumen COMPRA agresor total de la barra
    absorb_sell_v = volumen VENTA  agresor total
    poc_frac      = fracción del volumen en el POC (concentración → batalla en 1 nivel)

  delta acumulado (para divergencia): fp_delta ya viene; lo dejamos pasar.

Cache: processed/{sym}_scalp_fp_{tf}.parquet
Uso:  python backtest/_scalp_fp.py --symbol BTCUSDT --tf 1 5
"""
import argparse, warnings
from pathlib import Path
import numpy as np, pandas as pd
import pyarrow.parquet as pq

warnings.filterwarnings("ignore")
ROOT = Path(__file__).parent.parent

ASSETS = {
    "BTCUSDT": dict(fp_dir=ROOT/"data/bybit-perp/processed",            bin=5.0,  pre="btcusdt"),
    "ETHUSDT": dict(fp_dir=Path("E:/bybit-data/bybit-perp-eth/processed"), bin=1.0,  pre="ethusdt"),
    "SOLUSDT": dict(fp_dir=Path("E:/bybit-data/bybit-perp-sol/processed"), bin=0.1,  pre="solusdt"),
}
TF_LABEL = {1:"m1",5:"m5",15:"m15",60:"h1"}


def diag_feats(prices, buy, sell, binsz, R):
    """Imbalance diagonal + stacks sobre un footprint de 1 barra.
    Devuelve (stk_buy_len, stk_buy_hi, stk_buy_lo, stk_sell_len, stk_sell_hi, stk_sell_lo)."""
    n = len(prices)
    if n < 2:
        return 0, np.nan, np.nan, 0, np.nan, np.nan
    # dict precio->idx con tolerancia de medio bin (precios en grilla de 'bin')
    key = np.round(np.asarray(prices) / binsz).astype(np.int64)
    pos = {k: i for i, k in enumerate(key)}
    buy = np.asarray(buy); sell = np.asarray(sell)
    buy_imb = np.zeros(n, bool); sell_imb = np.zeros(n, bool)
    for i in range(n):
        below = pos.get(key[i] - 1)   # nivel un bin abajo
        above = pos.get(key[i] + 1)   # nivel un bin arriba
        if below is not None and sell[below] > 0 and buy[i] >= R * sell[below]:
            buy_imb[i] = True
        if above is not None and buy[above] > 0 and sell[i] >= R * buy[above]:
            sell_imb[i] = True
    # corridas consecutivas en la grilla (respetando huecos)
    def best_run(flag):
        best_len = 0; best_hi = np.nan; best_lo = np.nan
        run = 0; run_lo = None
        for i in range(n):
            contiguous = i > 0 and (key[i] - key[i-1] == 1)
            if flag[i] and (run == 0 or contiguous):
                if run == 0: run_lo = prices[i]
                run += 1
            elif flag[i]:
                run = 1; run_lo = prices[i]
            else:
                run = 0
            if run > best_len:
                best_len = run; best_hi = prices[i]; best_lo = run_lo
        return best_len, best_hi, best_lo
    bl, bhi, blo = best_run(buy_imb)
    sl, shi, slo = best_run(sell_imb)
    return bl, bhi, blo, sl, shi, slo


def build(sym, tf, R=3.0):
    cfg = ASSETS[sym]; binsz = cfg["bin"]
    lbl = TF_LABEL.get(tf, f"m{tf}")
    src = cfg["fp_dir"]/f"{cfg['pre']}_perp_{lbl}_footprint.parquet"
    if not src.exists():
        print(f"  [{sym} {lbl}] no existe {src}"); return
    out = cfg["fp_dir"]/f"{cfg['pre']}_scalp_fp_{lbl}.parquet"
    df = pq.read_table(src).to_pandas()
    print(f"  [{sym} {lbl}] {len(df):,} barras, R={R}...", flush=True)
    rows = []
    for r in df.itertuples():
        bl, bhi, blo, sl, shi, slo = diag_feats(r.prices, r.buy, r.sell, binsz, R)
        tb = float(np.sum(r.buy)); ts = float(np.sum(r.sell)); tv = tb + ts
        # concentración en POC
        if len(r.prices):
            kp = np.round(np.asarray(r.prices)/binsz).astype(np.int64)
            poc_k = np.round(r.poc/binsz)
            m = kp == poc_k
            poc_v = float(np.sum(np.asarray(r.buy)[m]) + np.sum(np.asarray(r.sell)[m]))
        else:
            poc_v = 0.0
        rows.append((r.bar_ts, bl, bhi, blo, sl, shi, slo, tb, ts,
                     poc_v/tv if tv > 0 else 0.0, r.delta, r.vol, r.imb_ratio, r.n_trades))
    cols = ["bar_ts","stk_buy_len","stk_buy_hi","stk_buy_lo","stk_sell_len","stk_sell_hi","stk_sell_lo",
            "buy_v","sell_v","poc_frac","fp_delta","fp_vol","fp_imb","fp_ntr"]
    o = pd.DataFrame(rows, columns=cols)
    o.to_parquet(out, compression="zstd")
    pct_b = 100*(o.stk_buy_len>=3).mean(); pct_s = 100*(o.stk_sell_len>=3).mean()
    print(f"  [{sym} {lbl}] OK → {out.name}  stacks≥3: buy {pct_b:.1f}% sell {pct_s:.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT", choices=list(ASSETS))
    ap.add_argument("--tf", nargs="+", type=int, default=[1,5])
    ap.add_argument("--R", type=float, default=3.0)
    args = ap.parse_args()
    print(f"\n{args.symbol}  bin=${ASSETS[args.symbol]['bin']}")
    for tf in args.tf:
        build(args.symbol, tf, args.R)
    print("Listo.")

if __name__ == "__main__":
    main()
