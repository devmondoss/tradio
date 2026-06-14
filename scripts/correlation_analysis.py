"""
FASE 3: Correlación de features vs result_r para RBF y AMD.

Calcula Spearman ρ, punto biserial (WR), y segmentación por cuantiles para
cada feature numérico. Útil para identificar si un feature discrimina outcome.

Uso:
    python scripts/correlation_analysis.py --strategy rbf
    python scripts/correlation_analysis.py --strategy amd [--min_n 15]

Requiere:
    pip install pandas scipy numpy requests
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
    "htf_h1_aligned", "signal_score_v2", "sizing_multiplier", "is_pre_breakout",
]
AMD_FEATURES = [
    "delta_dz_at_spike", "delta_dz_at_entry", "oi_delta_pct_at_spike",
    "oi_delta_pct_at_entry", "cvd_divergence_bars", "vr_at_spike", "vr_at_entry",
    "bars_to_entry", "spike_extension_pct", "range_spike_ratio",
    "range_pct", "range_bars", "obi_at_entry", "quality_score",
    "liq_ratio_at_spike", "htf_h1_aligned", "signal_score_v2", "sizing_multiplier",
]

def fetch(table: str) -> list[dict]:
    url = (
        f"{SUPABASE_URL}/rest/v1/{table}"
        f"?result_r=not.is.null&select=*&limit=2000"
    )
    req = urllib.request.Request(
        url,
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def run(strategy: str, min_n: int = 10):
    try:
        import pandas as pd
        from scipy import stats
    except ImportError:
        print("Falta scipy/pandas: pip install pandas scipy")
        sys.exit(1)

    table = "rbf_signals" if strategy == "rbf" else "amd_signals"
    features = RBF_FEATURES if strategy == "rbf" else AMD_FEATURES

    print(f"[{strategy.upper()}] Descargando señales con outcome de {table}...")
    rows = fetch(table)
    df = pd.DataFrame(rows)

    for col in df.columns:
        if df[col].dtype == object:
            unique = df[col].dropna().unique()
            if set(unique).issubset({"True", "False", True, False}):
                df[col] = df[col].map({"True": 1, "False": 0, True: 1, False: 0})

    df["result_r"] = pd.to_numeric(df["result_r"], errors="coerce")
    df = df[df["result_r"].notna()].copy()
    df["win"] = (df["result_r"] > 0).astype(int)
    n_total = len(df)
    wr_global = df["win"].mean()
    avg_r = df["result_r"].mean()
    print(f"  n={n_total}  WR={wr_global:.1%}  AvgR={avg_r:.3f}")

    results = []
    for feat in features:
        if feat not in df.columns:
            print(f"  [warn] feature not in table yet: {feat}")
            continue
        col = pd.to_numeric(df[feat], errors="coerce")
        null_ratio = col.isna().mean()
        if null_ratio > 0.50:
            print(f"  [warn] feature {feat} has {null_ratio:.0%} NULL; split backfill/live before trusting it")
        valid = df[col.notna()].copy()
        valid["_x"] = col[col.notna()]
        if len(valid) < min_n or valid["_x"].std() < 1e-9:
            continue

        rho, p_rho = stats.spearmanr(valid["_x"], valid["result_r"])
        pb, p_pb   = stats.pointbiserialr(valid["win"], valid["_x"])

        # Cuantiles Q1/Q2/Q3/Q4
        valid["_q"] = pd.qcut(valid["_x"], q=4, labels=["Q1","Q2","Q3","Q4"],
                               duplicates="drop")
        q_stats = valid.groupby("_q")["result_r"].agg(
            wr=lambda x: (x > 0).mean(),
            avg_r="mean",
            n="count",
        ).to_dict("index")

        results.append({
            "feature": feat,
            "n": len(valid),
            "spearman_rho": round(float(rho), 4),
            "p_rho": round(float(p_rho), 4),
            "pointbiserial": round(float(pb), 4),
            "p_pb": round(float(p_pb), 4),
            "significant": p_rho < 0.05 or p_pb < 0.05,
            "quartiles": {
                k: {
                    "wr": round(float(v.get("wr", 0)), 3),
                    "avg_r": round(float(v.get("avg_r", 0)), 3),
                    "n": int(v.get("n", 0)),
                }
                for k, v in q_stats.items()
            },
        })

    results.sort(key=lambda x: -abs(x["spearman_rho"]))

    print(f"\n{'Feature':<35} {'Spearman ρ':>10} {'p':>7} {'Pb':>8} {'Sig':>4}")
    print("-" * 70)
    for r in results:
        sig = "★" if r["significant"] else " "
        print(f"  {r['feature']:<33} {r['spearman_rho']:>+10.4f} {r['p_rho']:>7.4f}"
              f" {r['pointbiserial']:>+8.4f}  {sig}")

    print("\nSegmentación por cuartiles (Q1=bajo, Q4=alto):")
    for r in results[:8]:
        if not r["quartiles"]:
            continue
        print(f"\n  {r['feature']}:")
        for q, s in r["quartiles"].items():
            bar = "█" * int(s["wr"] * 20)
            print(f"    {q}: WR={s['wr']:.1%} AvgR={s['avg_r']:+.3f} n={s['n']:3d}  {bar}")

    out = {
        "strategy": strategy,
        "n_total": n_total,
        "wr_global": round(float(wr_global), 4),
        "avg_r_global": round(float(avg_r), 4),
        "features": results,
    }
    os.makedirs("docs/amd", exist_ok=True)
    fname = f"docs/amd/correlation_{strategy}.json"
    with open(fname, "w") as fp:
        json.dump(out, fp, indent=2)
    print(f"\n[ok] Resultados guardados en {fname}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=["rbf", "amd"], default="rbf")
    parser.add_argument("--min_n", type=int, default=10,
                        help="Mínimo de observaciones válidas por feature")
    args = parser.parse_args()
    run(args.strategy, args.min_n)
