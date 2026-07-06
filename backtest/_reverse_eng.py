"""
Ingenieria inversa: firma de los trades perdedores
Pregunta: que tienen en comun los losers que NO tienen los winners?
"""
import pandas as pd, numpy as np
from pathlib import Path

for sym in ["btc","eth","sol"]:
    p = Path(f"backtest/touches_{sym}.csv")
    if not p.exists(): continue
    df = pd.read_csv(p)
    oos = df[df.oos==True].copy()
    oos_days = (pd.to_datetime(oos.bar_ts,unit='ms').max() -
                pd.to_datetime(oos.bar_ts,unit='ms').min()).days

    W = oos[oos.r > 0]
    L = oos[oos.r <= 0]

    print(f"\n{'='*65}")
    print(f"  {sym.upper()}  OOS: {len(oos)} trades | W={len(W)} L={len(L)} | {oos_days}d")
    print(f"  Baseline avgR={oos.r.mean():+.3f}  WR={100*len(W)/len(oos):.1f}%")
    print(f"{'='*65}")

    # --- Numericas: media en winners vs losers
    num_cols = ["n_h4","n_h1","n_m15","bounce_h4","bounce_h1",
                "last_h4_h","last_h1_h","last_m15_h","atr"]
    print(f"\n  FEATURES NUMERICAS — media en W vs L:")
    print(f"  {'Feature':>16} | {'W_mean':>9} | {'L_mean':>9} | {'diff%':>7} | {'t-stat':>7}")
    print("  "+"-"*55)
    for c in num_cols:
        if c not in oos.columns: continue
        wm = W[c].mean(); lm = L[c].mean()
        diff = (lm-wm)/(abs(wm)+1e-9)*100
        # t-stat simple
        pooled = np.sqrt((W[c].var()/len(W)+L[c].var()/len(L))+1e-9)
        t = (wm-lm)/pooled
        flag = " <--" if abs(diff)>30 else ""
        print(f"  {c:>16} | {wm:>9.2f} | {lm:>9.2f} | {diff:>+6.1f}% | {t:>+7.2f}{flag}")

    # --- Categoricas: distribucion en W vs L
    cat_cols = ["zona","regime","kind"]
    for c in cat_cols:
        if c not in oos.columns: continue
        print(f"\n  {c.upper()} distribution (W vs L):")
        vals = oos[c].value_counts().index
        print(f"  {'Valor':>14} | {'W%':>6} | {'L%':>6} | {'ratio L/W':>10} | {'n_L':>5}")
        print("  "+"-"*48)
        for v in vals:
            nw=len(W[W[c]==v]); nl=len(L[L[c]==v])
            pw=nw/len(W) if len(W)>0 else 0
            pl=nl/len(L) if len(L)>0 else 0
            ratio = pl/(pw+1e-9)
            flag = " LOSER" if ratio>1.5 and nl>=3 else ""
            print(f"  {str(v):>14} | {100*pw:>5.1f}% | {100*pl:>5.1f}% | {ratio:>10.2f}{flag}")

    # --- Buscar el mejor EXCLUDER: variable+umbral que elimina mas losers con minimos winners
    print(f"\n  MEJOR EXCLUSION SIMPLE (minimiza losers, maximiza winners retenidos):")
    print(f"  {'Regla':>28} | {'n_kept':>6} | {'t/d':>5} | {'avgR':>8} | {'WR':>7} | {'L_eliminados':>13}")
    print("  "+"-"*78)

    best_results = []
    for c in ["n_h4","n_h1","bounce_h4","bounce_h1","n_m15"]:
        if c not in oos.columns: continue
        vals = sorted(oos[c].unique())
        for v in vals:
            # excluir trades donde c == v
            mask_excl = oos[c] != v
            kept = oos[mask_excl]
            if len(kept) < 10: continue
            l_excl = len(L[L[c]==v])
            w_excl = len(W[W[c]==v])
            if l_excl < 2: continue
            # calidad: cuantos losers eliminamos vs winners que sacrificamos
            loser_ratio = l_excl / len(L)
            winner_cost = w_excl / len(W)
            score = loser_ratio - winner_cost   # > 0 = bueno
            if score > 0.05:
                best_results.append((score, f"excluir {c}=={v}", kept, l_excl))

        for pct in [25,33,50,75]:
            thresh = np.percentile(oos[c], pct)
            mask_excl = oos[c] > thresh   # excluir los de ABAJO (high = bueno)
            kept = oos[mask_excl]
            if len(kept)<10: continue
            l_excl = len(L[~mask_excl])
            w_excl = len(W[~mask_excl])
            loser_ratio = l_excl/len(L)
            winner_cost = w_excl/len(W)
            score = loser_ratio - winner_cost
            if score > 0.05:
                best_results.append((score, f"{c} > P{pct}({thresh:.0f})", kept, l_excl))

            mask_excl2 = oos[c] < thresh   # excluir los de ARRIBA
            kept2 = oos[mask_excl2]
            if len(kept2)<10: continue
            l_excl2 = len(L[~mask_excl2])
            w_excl2 = len(W[~mask_excl2])
            score2 = l_excl2/len(L) - w_excl2/len(W)
            if score2 > 0.05:
                best_results.append((score2, f"{c} < P{pct}({thresh:.0f})", kept2, l_excl2))

    best_results.sort(reverse=True)
    for score, lbl, kept, l_excl in best_results[:8]:
        dd = (kept.r.cumsum()-kept.r.cumsum().cummax()).min()
        td = len(kept)/max(oos_days,1)
        print(f"  {lbl:>28} | {len(kept):>6} | {td:>5.2f} | {kept.r.mean():>+8.3f} | {100*(kept.r>0).mean():>6.1f}% | -{l_excl} losers (score={score:.2f})")
