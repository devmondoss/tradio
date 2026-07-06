"""
_scalp_oifund.py — ¿OI + Funding como contexto mejoran los fades de sc3? (BTC, exploratorio)
================================================================================
Tesis (literatura): fadear con más convicción cuando vas CONTRA la multitud apalancada.
  funding favorable: long fade ↔ funding<0 (shorts crowded→squeeze) · short fade ↔ funding>0
  OI flush: OI cae fuerte alrededor del nivel = liquidaciones desarmándose = la V que fadeamos
Solo BTC (ETH/SOL no tienen OI/funding descargado). Si pega fuerte → bajar ETH/SOL y validar 3.
Uso: python backtest/_scalp_oifund.py
"""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
import _scalp as SC

SYM = "BTCUSDT"


def main():
    df, _ = SC.run_sc3(SYM)
    print(f"BTC sc3: {len(df)} trades · OOS avgR base {df[df.ts>=SC.OOS_MS].r.mean():+.3f}")
    oi = pd.read_parquet("data/bybit-perp/oi_5m.parquet").sort_values("ts_ms").reset_index(drop=True)
    fu = pd.read_parquet("data/bybit-perp/funding.parquet").sort_values("ts_ms").reset_index(drop=True)
    # features rolling
    oi["oi_z"] = (oi.open_interest - oi.open_interest.rolling(2016, min_periods=200).mean()) / (oi.open_interest.rolling(2016, min_periods=200).std()+1e-9)  # ~7d
    oi["oi_chg1h"] = oi.open_interest.pct_change(12)   # 12×5min = 1h
    fu["fund_z"] = (fu.funding_rate - fu.funding_rate.rolling(90, min_periods=20).mean()) / (fu.funding_rate.rolling(90, min_periods=20).std()+1e-9)  # ~30d

    oits = oi.ts_ms.values; futs = fu.ts_ms.values
    def lookup(arr_ts, arr_val, ts):
        k = np.searchsorted(arr_ts, ts, side="right") - 1
        return arr_val[k] if 0 <= k < len(arr_val) else np.nan
    rows = []
    for r in df.itertuples():
        fr = lookup(futs, fu.funding_rate.values, r.ts); fz = lookup(futs, fu.fund_z.values, r.ts)
        oz = lookup(oits, oi.oi_z.values, r.ts); och = lookup(oits, oi.oi_chg1h.values, r.ts)
        fav = (r.side == "long" and fr < 0) or (r.side == "short" and fr > 0)
        rows.append((r.r, r.oos, fr, fz, oz, och, fav))
    a = pd.DataFrame(rows, columns=["r","oos","fund","fund_z","oi_z","oi_chg1h","fund_fav"]).dropna(subset=["fund","oi_z"])
    o = a[a.oos]
    print(f"cobertura: {len(a)}/{len(df)} trades con OI+funding\n")

    def buckets(col, q=4):
        print(f"  {col}: Pearson(.,r)={a[[col,'r']].corr().iloc[0,1]:+.3f}")
        a["q"] = pd.qcut(a[col], q, labels=[f"Q{k+1}" for k in range(q)], duplicates="drop")
        for qq, g in a.groupby("q", observed=True):
            go = g[g.oos]
            print(f"    {qq} [{g[col].min():+.4f}..{g[col].max():+.4f}]  avgR {g.r.mean():+.3f}  "
                  f"OOS {go.r.mean() if len(go) else float('nan'):+.3f}  n {len(g)}")

    print("── FUNDING favorable (long↔fund<0 / short↔fund>0) ──")
    fav = a[a.fund_fav]; unf = a[~a.fund_fav]
    print(f"  favorable: n {len(fav)} avgR {fav.r.mean():+.3f} OOS {fav[fav.oos].r.mean():+.3f}")
    print(f"  contra:    n {len(unf)} avgR {unf.r.mean():+.3f} OOS {unf[unf.oos].r.mean():+.3f}")
    print("\n── |funding| extremo (fund_z) ──"); buckets("fund_z")
    print("\n── OI nivel (oi_z) ──"); buckets("oi_z")
    print("\n── OI cambio 1h (flush si negativo) ──"); buckets("oi_chg1h")

    # filtros candidatos
    print("\n── FILTROS (OOS) ──")
    base = o.r.mean()
    for name, mask in [
        ("funding favorable", a.fund_fav),
        ("OI flush (chg1h<0)", a.oi_chg1h < 0),
        ("OI alto (oi_z>0)", a.oi_z > 0),
        ("fav + OI flush", a.fund_fav & (a.oi_chg1h < 0)),
    ]:
        sub = a[mask & a.oos]
        print(f"  {name:<22} n {len(sub):>4} OOS {sub.r.mean():+.3f}  (base {base:+.3f}, Δ {sub.r.mean()-base:+.3f})")


if __name__ == "__main__":
    main()
