"""
_scalp_book.py — BOOKMAP/HEATMAP como complemento de sc3 (¿la liquidez resting mejora el edge?)
================================================================================
"Ver las posiciones": reconstruye el DOM de 500 niveles (crate ob_heatmap) en el momento
del fill de cada trade sc3 y extrae la liquidez RESTING en el nivel:
  liq_ratio = liq a favor / liq en contra · wall_sz = muro mayor · book_imb = imbalance resting
Testea: ¿esas features separan ganadores de perdedores? ¿filtrar por libro mejora OOS?

Solo ETH/SOL (DOM 365d). Uso: python backtest/_scalp_book.py [ETHUSDT|SOLUSDT|both]
"""
import sys, subprocess
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
from _scalp import load as sc_load, load_m1_exit as sc_m1exit, run_setup, gen_sc3, OOS_MS

ROOT = Path(__file__).parent.parent
EXE = ROOT/"target/release/ob_heatmap.exe"
OBDIR = {"ETHUSDT": "E:/bybit-data/bybit-perp-eth/orderbook",
         "SOLUSDT": "E:/bybit-data/bybit-perp-sol/orderbook"}
UNI = dict(stop_atr=0.5, tol_atr=0.6, rr_cap=2.5, poc_frac_thr=0.0)
VR = {"ETHUSDT": 1.5, "SOLUSDT": 2.5}


def gen_trades_csv(sym):
    cfg = dict(UNI); cfg["vr_thr"] = VR[sym]
    s = sc_load(sym, 5); m1 = sc_m1exit(sym)
    df = run_setup(s, gen_sc3(**cfg), m1, 5, entry_mode="maker", mgmt="fade", timeout_min=240)
    df = df.reset_index(drop=True); df["tid"] = df.index
    out = ROOT/f"backtest/_trades_sc3_{sym}.csv"
    df[["tid","ts","entry","side","risk","r","oos"]].to_csv(out, index=False)
    print(f"  {sym}: {len(df)} trades sc3 → {out.name}")
    return df, out


def run_heatmap(sym, trades_csv):
    out = ROOT/f"backtest/_heatmap_sc3_{sym}.csv"
    print(f"  corriendo ob_heatmap sobre {sym} (reconstruye DOM en cada fill)...", flush=True)
    r = subprocess.run([str(EXE), "--ob-dir", OBDIR[sym], "--trades", str(trades_csv), "--out", str(out)],
                       capture_output=True, text=True)
    print("   ", (r.stdout or "").strip().splitlines()[-1] if r.stdout else r.stderr[-200:])
    return out


def analyze(sym, df, heat_csv):
    h = pd.read_csv(heat_csv)
    m = df.merge(h, on="tid", how="inner")
    print(f"\n{'='*64}\n  {sym[:3]} — bookmap en {len(m)}/{len(df)} trades (resto sin libro/fill)\n{'='*64}")
    if len(m) < 40:
        print("  cobertura insuficiente"); return
    print(f"  baseline (con libro): n={len(m)} avgR {m.r.mean():+.3f} OOS {m[m.oos].r.mean():+.3f}")
    for feat in ("liq_ratio", "book_imb", "wall_sz"):
        m[f"q_{feat}"] = pd.qcut(m[feat], 4, labels=["Q1","Q2","Q3","Q4"], duplicates="drop")
        print(f"\n  {feat}: Pearson(.,r)={m[[feat,'r']].corr().iloc[0,1]:+.3f}")
        for q, g in m.groupby(f"q_{feat}", observed=True):
            go = g[g.oos]
            print(f"    {q} [{g[feat].min():.2f}..{g[feat].max():.2f}]  avgR {g.r.mean():+.3f}  "
                  f"OOS {go.r.mean() if len(go) else float('nan'):+.3f}  WR {100*(g.r>0).mean():3.0f}%  n {len(g)}")
    # filtro: libro a favor (liq_ratio alto = más liquidez defendiendo el nivel)
    med = m.liq_ratio.median()
    hi = m[m.liq_ratio >= med]; ho = hi[hi.oos]; bo = m[m.oos]
    print(f"\n  FILTRO liq_ratio≥mediana: n={len(hi)} OOS {ho.r.mean():+.3f} (base {bo.r.mean():+.3f}, "
          f"Δ {ho.r.mean()-bo.r.mean():+.3f})")


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    syms = ["SOLUSDT", "ETHUSDT"] if which == "both" else [which]
    if not EXE.exists():
        print(f"ERROR: falta {EXE} — compilar: cargo build --release -p ob-heatmap"); return
    for sym in syms:
        df, tcsv = gen_trades_csv(sym)
        hcsv = run_heatmap(sym, tcsv)
        analyze(sym, df, hcsv)


if __name__ == "__main__":
    main()
