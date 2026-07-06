import pandas as pd, numpy as np, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from _listas import OOS_MS

df = pd.read_csv(Path(__file__).parent / "footprint_delta_btc.csv")
print(f"Total: {len(df)}  OOS: {df.oos.sum()}")

df["delta_favor"] = ((df.side=="long") & (df.delta_at_fill > 0)) | ((df.side=="short") & (df.delta_at_fill < 0))

def show(label, sub):
    oos = sub[sub.oos]
    cap=500.0; peak=500.0; dd=0.0
    for r in sub.sort_values("bar_ts").r.values:
        cap+=5*r; peak=max(peak,cap); dd=max(dd,(peak-cap)/peak)
    oos_r = oos.r.mean() if len(oos) else float("nan")
    print(f"\n{label}")
    print(f"  n={len(sub)}  n_oos={len(oos)}  WR={100*(sub.r>0).mean():.1f}%  avgR={sub.r.mean():+.3f}  OOS={oos_r:+.3f}  DD={100*dd:.1f}%")
    for s in ["long","short"]:
        g = sub[sub.side==s]
        if len(g): print(f"  {s}: n={len(g)} avgR={g.r.mean():+.3f} WR={100*(g.r>0).mean():.1f}%")

show("TODOS (baseline tick-window)", df)
show("ADVERSO only — skip delta favorable", df[~df["delta_favor"]])
show("FAVORABLE only (skip these)", df[df["delta_favor"]])

# Umbral de delta_norm
print("\n--- Threshold analysis (adverso = |delta_norm| > umbral) ---")
for thr in [0.0, 0.05, 0.10, 0.20]:
    adverso = df[(df.side=="long") & (df.delta_at_fill < -thr * df.atr)] | \
              df[(df.side=="short") & (df.delta_at_fill > thr * df.atr)]
    # mejor: construir mascara correcta
    mask = ((df.side=="long") & (df.delta_at_fill < -thr * df.atr)) | \
           ((df.side=="short") & (df.delta_at_fill > thr * df.atr))
    sub = df[mask]
    oos = sub[sub.oos]
    oos_r = oos.r.mean() if len(oos) else float("nan")
    print(f"  |delta|>{thr:.2f}*ATR  n={len(sub)}  avgR={sub.r.mean():+.3f}  OOS={oos_r:+.3f}")
