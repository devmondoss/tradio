import pandas as pd, numpy as np
from pathlib import Path

for sym in ["btc","eth","sol"]:
    p = Path(f"backtest/touches_{sym}.csv")
    if not p.exists(): continue
    df = pd.read_csv(p)
    oos = df[df.oos==True]
    is_ = df[df.oos==False]

    oos_days = (pd.to_datetime(oos.bar_ts, unit='ms').max() - pd.to_datetime(oos.bar_ts, unit='ms').min()).days
    is_days  = (pd.to_datetime(is_.bar_ts, unit='ms').max() - pd.to_datetime(is_.bar_ts, unit='ms').min()).days

    print(f"\n{sym.upper()} baseline: total={len(df)} | IS={len(is_)}({is_days}d) | OOS={len(oos)}({oos_days}d)")
    print(f"  baseline   -> {len(oos)/max(oos_days,1):.2f} t/dia OOS | avgR={oos.r.mean():+.3f} | WR={100*(oos.r>0).mean():.1f}%")

    filtros = [
        ("bounce 1-3",    (df.bounce_h4>=1) & (df.bounce_h4<=3)),
        ("bounce 1-2",    (df.bounce_h4>=1) & (df.bounce_h4<=2)),
        ("NO confirmada", df.zona != "confirmada"),
        ("fresca H4",     (df.n_h4>=1) & (df.n_h4<=2)),
    ]
    for lbl, mask in filtros:
        go = oos[mask[oos.index]]
        gi = is_[mask[is_.index]]
        if len(go) < 5: continue
        dd = (go.r.cumsum() - go.r.cumsum().cummax()).min()
        print(f"  {lbl:<18} -> {len(go):>3} OOS ({len(go)/max(oos_days,1):.2f} t/d) | {len(gi):>3} IS | avgR={go.r.mean():+.3f} | WR={100*(go.r>0).mean():.1f}% | DD={dd:+.2f}R")
