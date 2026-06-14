"""
FASE 3: Feature Importance para RBF y AMD.

Entrena un RandomForest sobre signals con outcome conocido (result_r != NULL)
y reporta feature importances + partial dependence para las top-5 features.

Uso:
    python scripts/feature_importance.py --strategy rbf
    python scripts/feature_importance.py --strategy amd

Requiere:
    pip install pandas scikit-learn numpy requests matplotlib
Env:
    SUPABASE_URL  SUPABASE_KEY
"""
import argparse
import json
import os
import sys
import urllib.request
import numpy as np

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://ztdhvmcisjjyhbqlgkzm.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

RBF_FEATURES = [
    "absorption_score", "bar_displacement", "oi_delta_pct", "cvd_divergence_bars",
    "vr_at_breakout", "vr_tier", "range_touch_symmetry", "cvd_per_bar",
    "breakout_extension_pct", "range_pct", "range_bars", "cvd_in_range",
    "obi_at_entry", "dz_at_entry", "liq_ratio_pre", "confluence_score",
    "htf_h1_aligned",
]
AMD_FEATURES = [
    "delta_dz_at_spike", "delta_dz_at_entry", "oi_delta_pct_at_spike",
    "oi_delta_pct_at_entry", "cvd_divergence_bars", "vr_at_spike", "vr_at_entry",
    "is_kill_zone", "bars_to_entry", "spike_extension_pct", "range_spike_ratio",
    "range_pct", "range_bars", "obi_at_entry", "quality_score", "htf_h1_aligned",
    "liq_ratio_at_spike",
]

def fetch_signals(table: str) -> list[dict]:
    url = (
        f"{SUPABASE_URL}/rest/v1/{table}"
        f"?result_r=not.is.null"
        f"&select=*"
        f"&limit=2000"
    )
    req = urllib.request.Request(
        url,
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def build_matrix(rows: list[dict], features: list[str]):
    import pandas as pd
    df = pd.DataFrame(rows)
    # Coerce booleans to int
    for col in df.columns:
        if df[col].dtype == object:
            unique = df[col].dropna().unique()
            if set(unique).issubset({"True", "False", True, False}):
                df[col] = df[col].map({"True": 1, "False": 0, True: 1, False: 0})
    present = [f for f in features if f in df.columns]
    missing = [f for f in features if f not in df.columns]
    if missing:
        print(f"  [warn] features not in table yet: {missing}")
    for feat in present:
        null_ratio = df[feat].isna().mean()
        if null_ratio > 0.50:
            print(f"  [warn] feature {feat} has {null_ratio:.0%} NULL; split backfill/live before trusting it")
    X = df[present].apply(pd.to_numeric, errors="coerce").fillna(0)
    y_r = pd.to_numeric(df["result_r"], errors="coerce")
    y_bin = (y_r > 0).astype(int)
    mask = y_r.notna()
    return X[mask], y_r[mask], y_bin[mask], present

def run(strategy: str):
    try:
        from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
        from sklearn.inspection import permutation_importance
        from sklearn.model_selection import cross_val_score
        import pandas as pd
    except ImportError:
        print("Falta scikit-learn: pip install scikit-learn pandas numpy")
        sys.exit(1)

    table = "rbf_signals" if strategy == "rbf" else "amd_signals"
    features = RBF_FEATURES if strategy == "rbf" else AMD_FEATURES

    print(f"[{strategy.upper()}] Descargando señales con outcome de {table}...")
    rows = fetch_signals(table)
    print(f"  {len(rows)} señales con result_r")

    if len(rows) < 20:
        print("  Muy pocas señales para análisis (mínimo 20). Acumular más datos.")
        return

    X, y_r, y_bin, present = build_matrix(rows, features)
    print(f"  {len(X)} filas válidas, {len(present)} features")
    print(f"  WR global: {y_bin.mean():.1%}  AvgR: {y_r.mean():.3f}")

    # ── Clasificador (WR) ────────────────────────────────────────────────────
    clf = RandomForestClassifier(n_estimators=300, max_depth=4, min_samples_leaf=3,
                                  random_state=42)
    cv_auc = cross_val_score(clf, X, y_bin, cv=min(5, len(X)//5), scoring="roc_auc")
    print(f"\n[Clasificador WR] ROC-AUC CV: {cv_auc.mean():.3f} ± {cv_auc.std():.3f}")

    clf.fit(X, y_bin)
    importances = sorted(zip(present, clf.feature_importances_), key=lambda x: -x[1])
    print("\nTop feature importances (clasificador WR > 0):")
    for feat, imp in importances[:10]:
        bar = "█" * int(imp * 40)
        print(f"  {feat:<35} {imp:.4f}  {bar}")

    # ── Regresor (AvgR) ──────────────────────────────────────────────────────
    reg = RandomForestRegressor(n_estimators=300, max_depth=4, min_samples_leaf=3,
                                 random_state=42)
    cv_r2 = cross_val_score(reg, X, y_r, cv=min(5, len(X)//5), scoring="r2")
    print(f"\n[Regresor AvgR] R² CV: {cv_r2.mean():.3f} ± {cv_r2.std():.3f}")

    reg.fit(X, y_r)
    importances_r = sorted(zip(present, reg.feature_importances_), key=lambda x: -x[1])
    print("\nTop feature importances (regresor AvgR):")
    for feat, imp in importances_r[:10]:
        bar = "█" * int(imp * 40)
        print(f"  {feat:<35} {imp:.4f}  {bar}")

    # ── Permutation importance (más honesta con CV) ──────────────────────────
    perm = permutation_importance(clf, X, y_bin, n_repeats=30, random_state=42, n_jobs=-1)
    perm_sorted = sorted(zip(present, perm.importances_mean, perm.importances_std),
                         key=lambda x: -x[1])
    print("\nPermutation importance (clasificador):")
    for feat, mean, std in perm_sorted[:10]:
        bar = "█" * max(0, int(mean * 80))
        sign = "+" if mean > 0 else "-"
        print(f"  {feat:<35} {sign}{abs(mean):.4f} ± {std:.4f}  {bar}")

    # ── Guardar resumen JSON ──────────────────────────────────────────────────
    out = {
        "strategy": strategy,
        "n_signals": len(X),
        "wr_global": float(y_bin.mean()),
        "avg_r_global": float(y_r.mean()),
        "roc_auc_cv": float(cv_auc.mean()),
        "r2_cv": float(cv_r2.mean()),
        "top_features_clf": [{"feature": f, "importance": round(float(i), 5)}
                              for f, i in importances[:10]],
        "top_features_reg": [{"feature": f, "importance": round(float(i), 5)}
                              for f, i, in importances_r[:10]],
        "top_perm_importance": [{"feature": f, "mean": round(float(m), 5), "std": round(float(s), 5)}
                                 for f, m, s in perm_sorted[:10]],
    }
    fname = f"docs/amd/feature_importance_{strategy}.json"
    os.makedirs("docs/amd", exist_ok=True)
    with open(fname, "w") as fp:
        json.dump(out, fp, indent=2)
    print(f"\n[ok] Resultados guardados en {fname}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=["rbf", "amd"], default="rbf")
    args = parser.parse_args()
    run(args.strategy)
