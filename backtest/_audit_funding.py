"""
_audit_funding.py — EV de FUNDING (nunca contado) sobre la cartera de liquidez
==============================================================================
Los perps pagan/cobran funding cada 8h. BTC perp: funding +0.33 bps/8h medio, positivo 73% del
tiempo -> los LONGS pagan, los SHORTS cobran. Con el mirror (shorts) esto es viento de cola.
Mide el funding_R real por trade cruzando los eventos de funding en su ventana [entry, exit].

funding_R por evento = -side · rate · (entry/risk)   [side: +1 long paga si rate>0, -1 short cobra]
PnL de funding como fracción del notional = rate; en R: × entry/risk (= notional/risk_usd).
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _audit_edge import run_audit, summ
from _audit_mirror import gen_h21_short

ROOT = Path(__file__).parent.parent


def main():
    tf = 15
    t = L2.load2(tf, start_ms=L2.TICK_MS)
    a = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=L2.TICK_MS)
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
    df = run_audit(a, gens, m1, tf, honest_fee=True)

    fund = pd.read_parquet(ROOT/"data/bybit-perp/funding.parquet").sort_values("ts_ms")
    fts = fund.ts_ms.values.astype(np.int64); frate = fund.funding_rate.values.astype(float)

    # funding por trade: suma de eventos en (entry, exit]
    fund_r = np.zeros(len(df)); n_events = np.zeros(len(df), dtype=int)
    ent = df.ts.values; ext = df.exit_ts.values; sd = (df.side.values == "long")
    e_over_r = df.entry.values/df.risk.values
    for k in range(len(df)):
        lo = np.searchsorted(fts, ent[k], side="right"); hi = np.searchsorted(fts, ext[k], side="right")
        if hi <= lo: continue
        rate_sum = frate[lo:hi].sum()
        sign = 1.0 if sd[k] else -1.0      # long paga (resta) si rate>0; short cobra (suma)
        fund_r[k] = -sign*rate_sum*e_over_r[k]
        n_events[k] = hi-lo
    df["fund_r"] = fund_r; df["n_fund"] = n_events
    df["r_net"] = df.r + df.fund_r

    hold_h = (df.exit_ts - df.ts)/3_600_000
    print(f"cartera h5+h21+mirror (fee honesto)  n={len(df)}  hold medio={hold_h.mean():.1f}h "
          f"(mediana {hold_h.median():.1f}h)  eventos funding medio={df.n_fund.mean():.2f}/trade\n")

    def blk(d, lbl):
        if len(d) == 0: print(f"{lbl}: n=0"); return
        print(f"{lbl:<26} n={len(d):>4} | avgR(sin fund) {d.r.mean():+.3f} | funding medio {d.fund_r.mean():+.4f}R "
              f"| avgR NETO {d.r_net.mean():+.3f} | impacto {100*d.fund_r.mean()/abs(d.r.mean()):+.1f}%")

    print("=== Impacto del funding por LADO (todo + OOS) ===")
    blk(df, "TODO")
    blk(df[df.side == "long"], "  LONG (paga funding)")
    blk(df[df.side == "short"], "  SHORT (cobra funding)")
    o = df[df.oos]
    print()
    blk(o, "OOS TODO")
    blk(o[o.side == "long"], "  OOS LONG")
    blk(o[o.side == "short"], "  OOS SHORT")

    print("\n=== Funding neto agregado ===")
    print(f"  netR sin funding: {df.r.sum():+.1f}  ->  con funding: {df.r_net.sum():+.1f}  "
          f"(funding total {df.fund_r.sum():+.1f}R)")
    print(f"  longs: {df[df.side=='long'].fund_r.sum():+.1f}R   shorts: {df[df.side=='short'].fund_r.sum():+.1f}R")
    print(f"  En $ (riesgo $5/trade): funding total = ${5*df.fund_r.sum():+.0f}")

    print("\n=== Implicación de sizing ===")
    ls = df[df.side == "long"]; ss = df[df.side == "short"]
    print(f"  avgR neto LONG {ls.r_net.mean():+.3f} vs SHORT {ss.r_net.mean():+.3f}  "
          f"-> el funding inclina el EV a favor de los SHORTS (+{ss.r_net.mean()-ls.r_net.mean():.3f}R)")


if __name__ == "__main__":
    main()
