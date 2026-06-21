"""
_ml_eval.py — test capstone: ¿predice un modelo multivariante la dirección futura > fee, OOS?
=============================================================================================
Lee _ml_dataset.parquet (de _ml_build.py). Entrena LightGBM en IS (<2026-03-01) para predecir el
retorno fwd 5m/15m (bps); evalúa en OOS. Métrica clave de tradeabilidad:
  - ordena OOS por predicción; el DECIL más alcista (→long) y más bajista (→short) deben batir ±fee.
  - net long  = mean(realized | top decile)  - 11
  - net short = -mean(realized | bottom decile) - 11
  - sign-accuracy y Spearman OOS (¿hay señal direccional de cualquier tamaño?)

Nota honestidad: las ventanas fwd se solapan (muestreo 15s, label 15m) → las observaciones del
decil NO son independientes; el punto-estimado del retorno medio es insesgado pero su IC es más
ancho de lo que sugiere n. Por eso exigimos un margen amplio (≥ fee) y consistencia IS↔OOS.

Uso: python backtest/_ml_eval.py
"""
import os, sys
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from scipy.stats import spearmanr

ROOT = Path(__file__).parent.parent
PERP = Path(os.environ.get("TRADIO_PERP", str(ROOT / "data/bybit-perp")))
DS = PERP / "_ml_dataset.parquet"
FEE = 11.0
LABELS = ["fwd_5m", "fwd_15m"]


def decile_report(pred, real, name):
    n = len(pred)
    q_hi = np.quantile(pred, 0.9); q_lo = np.quantile(pred, 0.1)
    top = real[pred >= q_hi]; bot = real[pred <= q_lo]
    net_long = np.mean(top) - FEE
    net_short = -np.mean(bot) - FEE
    sa = np.mean(np.sign(pred) == np.sign(real)) * 100
    rho = spearmanr(pred, real).correlation
    print(f"  [{name}] n={n:,} | top-decile real {np.mean(top):+.2f}bps → net long {net_long:+.2f} | "
          f"bot-decile real {np.mean(bot):+.2f}bps → net short {net_short:+.2f}")
    print(f"          sign-acc {sa:.1f}% | Spearman {rho:+.4f} | "
          f"std(real) {np.std(real):.1f}bps")
    return net_long, net_short


def main():
    if not DS.exists(): sys.exit("Falta _ml_dataset.parquet — corré _ml_build.py")
    df = pd.read_parquet(DS)
    feat_cols = [c for c in df.columns if c not in ("ts_ms", "oos", *LABELS)]
    df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=feat_cols)
    tr, te = df[~df.oos], df[df.oos]
    print(f"Dataset: {len(df):,} | IS {len(tr):,} / OOS {len(te):,} | feats {len(feat_cols)} | fee {FEE:.0f}bps")
    print(f"OOS span: {pd.Timestamp(te.ts_ms.min(),unit='ms').date()} → {pd.Timestamp(te.ts_ms.max(),unit='ms').date()}\n")

    for lab in LABELS:
        sub_tr = tr.dropna(subset=[lab]); sub_te = te.dropna(subset=[lab])
        Xtr, ytr = sub_tr[feat_cols].values, sub_tr[lab].values
        Xte, yte = sub_te[feat_cols].values, sub_te[lab].values
        model = lgb.LGBMRegressor(n_estimators=400, learning_rate=0.03, num_leaves=63,
                                  subsample=0.7, subsample_freq=1, colsample_bytree=0.7,
                                  min_child_samples=200, reg_lambda=5.0, n_jobs=-1, verbose=-1)
        model.fit(Xtr, ytr)
        pin, pout = model.predict(Xtr), model.predict(Xte)
        print(f"=== label {lab} ===")
        decile_report(pin, ytr, "IS ")
        decile_report(pout, yte, "OOS")
        imp = sorted(zip(feat_cols, model.feature_importances_), key=lambda x: -x[1])[:8]
        print("  top features:", ", ".join(f"{k}({v})" for k, v in imp), "\n")

    print(f"Veredicto: un edge direccional tradeable necesita net long Y/O net short OOS > 0 (idealmente")
    print(f"≥ varios bps) y consistente con IS. Si ambos ≤ 0 OOS → ni un modelo full-feature bate el fee.")


if __name__ == "__main__":
    main()
