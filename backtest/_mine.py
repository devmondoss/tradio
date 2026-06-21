"""
_mine.py — minería de patrones ANTI-OVERFITTING sobre la etiqueta triple-barrera
=================================================================================
Lee _mine_dataset.parquet. Dos vías, ambas con disciplina out-of-sample y control de multiple-testing:

(1) MODELO FLEXIBLE (LightGBM clasificador): IS-fit, OOS-test. AUC + P(up) del decil más confiado.
    Si ni el modelo separa OOS > breakeven → no hay patrón harvesteable.

(2) REGLAS INTERPRETABLES: para cada feature (terciles) y cada par, regla direccional. Se SELECCIONA por
    edge IS (neto de fee) con soporte mínimo; se VALIDA en OOS. Control de data-snooping: se repite TODO el
    pipeline (seleccionar-por-IS → medir-OOS) sobre B barajados de la etiqueta (null). El edge OOS real de
    las reglas IS-seleccionadas debe superar el percentil 95 del null; si no, lo "mejor" es ruido de buscar.

Net por trade (barrera simétrica ±B, RR=1): si la regla apunta arriba → long → net = (2·P_up − 1) − fee_r,
con fee_r = 11/B (R). Rentable si P_up_oos > (1+fee_r)/2.

Uso: python backtest/_mine.py [--B 35] [--minsup 300] [--nperm 200]
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import roc_auc_score

ROOT = Path(__file__).parent.parent
DS = ROOT / "data/bybit-perp/_mine_dataset.parquet"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--B", type=float, default=35.0); ap.add_argument("--minsup", type=int, default=300)
    ap.add_argument("--nperm", type=int, default=200); ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)
    fee_r = 11.0/args.B
    be = (1+fee_r)/2
    d = pd.read_parquet(DS)
    feats = [c for c in d.columns if c not in ("label","ts_ms","oos")]
    tr, te = d[~d.oos], d[d.oos]
    yI, yO = tr.label.values.astype(int), te.label.values.astype(int)
    print(f"Filas IS {len(tr):,} / OOS {len(te):,} | feats {len(feats)} | fee_r {fee_r:.3f}R | "
          f"breakeven P(up) {be:.3f} | P(up) IS {yI.mean():.3f}/OOS {yO.mean():.3f}\n")

    # ---------- (1) modelo flexible ----------
    XI, XO = tr[feats].values, te[feats].values
    m = lgb.LGBMClassifier(n_estimators=500, learning_rate=0.02, num_leaves=63, subsample=0.7,
                           subsample_freq=1, colsample_bytree=0.7, min_child_samples=300,
                           reg_lambda=5.0, n_jobs=-1, verbose=-1)
    m.fit(XI, yI)
    pI, pO = m.predict_proba(XI)[:,1], m.predict_proba(XO)[:,1]
    print("(1) MODELO FLEXIBLE (LightGBM)")
    print(f"    AUC  IS {roc_auc_score(yI,pI):.4f} | OOS {roc_auc_score(yO,pO):.4f}  (0.5=azar)")
    for q,lbl in [(0.9,"decil ↑ (long)"),(0.1,"decil ↓ (short)")]:
        thr_i = np.quantile(pI,q); thr_o = np.quantile(pO,q)
        if q==0.9:
            puI = yI[pI>=thr_i].mean(); puO = yO[pO>=thr_o].mean()
            netO = (2*puO-1)-fee_r
            print(f"    {lbl}: P(up) IS {puI:.3f} / OOS {puO:.3f} → net OOS {netO:+.3f}R (be P(up) {be:.3f})")
        else:
            pdI = 1-yI[pI<=thr_i].mean(); pdO = 1-yO[pO<=thr_o].mean()
            netO = (2*pdO-1)-fee_r
            print(f"    {lbl}: P(dn) IS {pdI:.3f} / OOS {pdO:.3f} → net OOS {netO:+.3f}R")
    imp = sorted(zip(feats, m.feature_importances_), key=lambda x:-x[1])[:8]
    print("    top feats:", ", ".join(f"{k}" for k,_ in imp), "\n")

    # ---------- (2) reglas interpretables ----------
    # construir condiciones binarias: terciles alto/bajo de cada feature numérica + booleanas
    def conds(X):
        out = {}
        for f in feats:
            v = X[f].values
            uniq = np.unique(v[:200])
            if set(np.unique(v)).issubset({0.0,1.0}):           # booleana
                out[f"{f}=1"] = v==1
            else:
                qhi = np.quantile(v,0.67); qlo = np.quantile(v,0.33)
                out[f"{f}>hi"] = v>=qhi; out[f"{f}<lo"] = v<=qlo
        return out
    cI = conds(tr); cO = conds(te)
    names = list(cI.keys())
    # rejilla de reglas: singles + pares
    rules = [(a,) for a in names] + [(a,b) for i,a in enumerate(names) for b in names[i+1:]]

    def eval_rules(yI_, yO_):
        best = []
        for r in rules:
            mi = cI[r[0]].copy(); mo = cO[r[0]].copy()
            for x in r[1:]: mi &= cI[x]; mo &= cO[x]
            ni = int(mi.sum()); no = int(mo.sum())
            if ni < args.minsup or no < 50: continue
            pi = yI_[mi].mean()
            direction = 1 if pi >= 0.5 else -1           # dirección fijada en IS
            net_i = (direction*(2*pi-1)) - fee_r
            if net_i <= 0: continue                       # exige edge IS que ya bate fee
            po = yO_[mo].mean()
            net_o = (direction*(2*po-1)) - fee_r
            best.append((r, direction, ni, no, pi, po, net_i, net_o))
        return best

    # edge CRUDO (ignora fee) — caracteriza el techo de señal direccional
    raw = []
    for r in rules:
        mi = cI[r[0]].copy(); mo = cO[r[0]].copy()
        for x in r[1:]: mi &= cI[x]; mo &= cO[x]
        ni=int(mi.sum()); no=int(mo.sum())
        if ni < args.minsup or no < 50: continue
        pi = yI[mi].mean(); dr = 1 if pi>=0.5 else -1
        po = yO[mo].mean()
        raw.append((r, dr, no, pi, po, dr*(2*pi-1), dr*(2*po-1)))   # últimas: edge IS/OOS crudos (sin fee)
    raw.sort(key=lambda x:-x[5])
    print("(2pre) Edge CRUDO (sin fee) — top 10 reglas por edge IS direccional y su OOS:")
    print(f'    {"regla":<40} {"dir":>3} {"n_oos":>6} {"P_is":>5} {"P_oos":>6} {"edgeIS":>6} {"edgeOOS":>7}')
    for r,dr,no,pi,po,ei,eo in raw[:10]:
        print(f'    {"&".join(r):<40} {dr:>+3} {no:>6} {pi:>.3f} {po:>.3f} {ei:>+.3f} {eo:>+.3f}')
    if raw:
        sel_raw_oos = np.mean([x[6] for x in raw[:15]])
        print(f"    → edge OOS crudo medio (top-15 por IS): {sel_raw_oos:+.4f} (necesita > fee_r {fee_r:.3f} para ser tradeable)\n")

    real = eval_rules(yI, yO)
    real.sort(key=lambda x:-x[6])                          # ordenar por edge IS
    print(f"(2) REGLAS — {len(rules):,} candidatas; {len(real)} pasan (edge IS bate fee, soporte≥{args.minsup})")
    if real:
        topIS = real[:15]
        print("    Top 15 por edge IS  →  su OOS:")
        print(f'    {"regla":<40} {"dir":>3} {"n_oos":>6} {"P_is":>5} {"P_oos":>6} {"netIS":>6} {"netOOS":>7}')
        for r,dr,ni,no,pi,po,nei,neo in topIS:
            rs = "&".join(r)
            print(f'    {rs:<40} {dr:>+3} {no:>6} {pi:>.3f} {po:>.3f} {nei:>+.3f} {neo:>+.3f}')
        # promedio OOS de las IS-seleccionadas
        real_oos = np.array([x[7] for x in real])
        sel_oos = np.mean([x[7] for x in topIS])
        print(f"\n    Edge OOS medio de las top-15-por-IS: {sel_oos:+.4f}R | "
              f"reglas con netOOS>0: {(real_oos>0).mean()*100:.0f}% de {len(real)}")

        # ---------- null por permutación (control multiple-testing) ----------
        null_best = []
        for _ in range(args.nperm):
            ys = rng.permutation(yI); yt = rng.permutation(yO)
            rb = eval_rules(ys, yt)
            if rb:
                rb.sort(key=lambda x:-x[6]); null_best.append(np.mean([x[7] for x in rb[:15]]))
        null_best = np.array(null_best)
        if len(null_best):
            p95 = np.quantile(null_best, 0.95)
            print(f"    NULL (etiqueta barajada, {len(null_best)} perms): edge OOS top-15 medio = "
                  f"{null_best.mean():+.4f} ± {null_best.std():.4f}; p95 = {p95:+.4f}")
            print(f"    → ¿edge real ({sel_oos:+.4f}) supera el p95 del null ({p95:+.4f})? "
                  f"{'SÍ — patrón candidato' if sel_oos>p95 else 'NO — indistinguible de ruido de búsqueda'}")
    else:
        print("    Ninguna regla con edge IS que bata el fee.")


if __name__ == "__main__":
    main()
