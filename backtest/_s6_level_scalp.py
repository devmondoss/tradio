"""
S6 — Level Reaction Scalp (Okala 80/20) · alta frecuencia
==========================================================
Edge base: niveles redondos/cuartos (múltiplos de $STEP en BTC). Precio toca el nivel y reacciona
(cierra de vuelta) → entrada contraria (fade del nivel). Stop fijo, TP por RR. Capa OF: reacción real
(delta flip / absorción) en el nivel. Causal, fee real, IS/OOS. Compara base vs base+OF.

Variantes de stop: TIGHT (pocos bps, estilo Okala) y WIDE (fee-survivable) para aislar el efecto del fee.

Uso: python backtest/_s6_level_scalp.py [--step 250] [--rr 1.5]
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
M1 = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"
OOS_MS = int(pd.Timestamp("2026-03-01", tz="UTC").value // 1_000_000)
FEE_RT = 0.0011
CAP0, RISK = 500.0, 0.01


def atr(h, l, c, n=14):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    out = np.full(len(tr), np.nan); out[n-1] = np.nanmean(tr[:n]); k = 1.0/n
    for i in range(n, len(tr)): out[i] = out[i-1]*(1-k) + tr[i]*k
    return out


def resample_m5(df):
    g = (df["ts_ms"].values // 300_000) * 300_000
    b = pd.DataFrame({"g": g, "o": df.open.values, "h": df.high.values, "l": df.low.values,
                      "c": df.close.values, "v": df.volume.values, "d": df.delta.values})
    a = b.groupby("g").agg(open=("o","first"), high=("h","max"), low=("l","min"), close=("c","last"),
                           volume=("v","sum"), delta=("d","sum")).reset_index().rename(columns={"g":"ts_ms"})
    return a


def run(df, step, rr, stop_mode, use_of, minstop_bps):
    t = resample_m5(df)
    ts = t.ts_ms.values.astype(np.int64)
    o,h,l,c = (t[x].values.astype(float) for x in ("open","high","low","close"))
    vol, delta = t.volume.values.astype(float), t.delta.values.astype(float)
    n = len(t); a = atr(h,l,c); S = pd.Series
    dz = (delta - S(delta).rolling(50).mean().shift(1).values) / (S(delta).rolling(50).std().shift(1).values + 1e-9)
    hm = (ts // 60_000) % 1440
    sess = ((hm >= 7*60) & (hm < 10*60)) | ((hm >= 13*60) & (hm < 17*60))
    # nivel redondo más cercano tocado por la vela
    trades = []; next_ok = 0; cap = CAP0; peak = CAP0; dd = 0.0; risk_amt = CAP0*RISK; cur_mo = -1
    timeout = 24
    for i in range(60, n-1):
        if i < next_ok or not sess[i] or not np.isfinite(a[i]): continue
        mo = pd.Timestamp(ts[i], unit="ms").to_period("M").ordinal
        if mo != cur_mo: risk_amt = cap*RISK; cur_mo = mo
        lvl = round(c[i] / step) * step
        tol = 0.10 * a[i]                              # "toque" = dentro de 0.10 ATR del nivel
        for side in ("long","short"):
            if side == "long":
                touched = (l[i] <= lvl + tol) and (l[i] <= lvl)     # pinchó el nivel desde arriba
                react = c[i] > lvl                                   # cerró de vuelta arriba
                if not (touched and react): continue
                if use_of and not (dz[i] <= -0.5): continue          # venta agresiva absorbida en el nivel
                entry = c[i]
                base_stop = min(l[i], lvl) - 0.10*a[i]
                stop = base_stop if stop_mode == "tight" else min(base_stop, entry - minstop_bps/1e4*entry)
                risk_px = entry - stop
                if risk_px <= 0: continue
                tp = entry + rr*risk_px
            else:
                touched = (h[i] >= lvl - tol) and (h[i] >= lvl)
                react = c[i] < lvl
                if not (touched and react): continue
                if use_of and not (dz[i] >= 0.5): continue
                entry = c[i]
                base_stop = max(h[i], lvl) + 0.10*a[i]
                stop = base_stop if stop_mode == "tight" else max(base_stop, entry + minstop_bps/1e4*entry)
                risk_px = stop - entry
                if risk_px <= 0: continue
                tp = entry - rr*risk_px
            res = None
            for j in range(i+1, min(i+1+timeout, n)):
                if side == "long":
                    if l[j] <= stop: res = -1.0; break
                    if h[j] >= tp:   res = rr;  break
                else:
                    if h[j] >= stop: res = -1.0; break
                    if l[j] <= tp:   res = rr;  break
            if res is None:
                px = c[min(i+timeout, n-1)]
                res = ((px-entry) if side=="long" else (entry-px)) / risk_px
            r = res - FEE_RT*entry/risk_px
            cap += risk_amt*r; peak = max(peak, cap); dd = max(dd, (peak-cap)/peak)
            trades.append({"ts": int(ts[i]), "r": r, "oos": int(ts[i]) >= OOS_MS, "win": res > 0})
            next_ok = i + 2
            break
    return trades, cap, dd


def report(name, trades, span_days):
    if not trades: print(f"{name}: sin trades"); return
    d = pd.DataFrame(trades); di, do = d[~d.oos], d[d.oos]
    def blk(x, tag, frac):
        if len(x)==0: return f"{tag} n=0"
        wr=100*x.win.mean(); ar=x.r.mean(); sh=ar/(x.r.std()+1e-9)
        return f"{tag} n={len(x):>4} WR={wr:>4.1f}% avgR={ar:>+.3f} netR={x.r.sum():>+6.1f} {len(x)/(span_days*frac):>4.1f}tr/d Sharpe~{sh:>+.2f}"
    print(f"--- {name} ---")
    print("   " + blk(di,"IS ",0.66)); print("   " + blk(do,"OOS",0.34))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=float, default=250.0); ap.add_argument("--rr", type=float, default=1.5)
    ap.add_argument("--minstop", type=float, default=25.0)
    args = ap.parse_args()
    df = pd.read_parquet(M1, columns=["ts_ms","open","high","low","close","volume","delta"]).sort_values("ts_ms")
    df = df[df.ts_ms >= pd.Timestamp("2025-06-19", tz="UTC").value//10**6].reset_index(drop=True)
    span = (df.ts_ms.max()-df.ts_ms.min())/86_400_000
    print(f"S6 Level Scalp | step ${args.step:.0f} | RR {args.rr} | fee {FEE_RT*1e4:.0f}bps | span {span:.0f}d\n")
    for stop_mode in ("tight","wide"):
        for use_of in (False, True):
            tag = f"stop={stop_mode:<5} OF={'sí' if use_of else 'no'}"
            tr, cap, dd = run(df, args.step, args.rr, stop_mode, use_of, args.minstop)
            report(tag, tr, span)
            print(f"   $500 -> ${cap:,.0f} | MaxDD {dd*100:.0f}%\n")


if __name__ == "__main__":
    main()
