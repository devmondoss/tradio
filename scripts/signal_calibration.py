"""
FASE 3: Calibración de umbrales óptimos por feature para RBF y AMD.

Para cada feature numérico: encuentra el umbral que maximiza AvgR en el subconjunto
filtrado. Busca en percentiles 10%-90% y reporta el punto óptimo.

Útil para convertir features observacionales en gates de producción cuando n≥30.

Uso:
    python scripts/signal_calibration.py --strategy rbf
    python scripts/signal_calibration.py --strategy amd [--min_n 20]

Requiere:
    pip install pandas numpy requests
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

RBF_GATES = [
    ("absorption_score",    ">="),
    ("bar_displacement",    "<="),  # bajo = mayor absorción
    ("vr_tier",             ">="),
    ("breakout_extension_pct", ">="),
    ("obi_at_entry",        ">="),  # positivo para Long
    ("dz_at_entry",         "between_0.5_3"),  # Sweet spot
    ("confluence_score",    ">="),
    ("range_touch_symmetry",">="),
]
AMD_GATES = [
    ("delta_dz_at_spike",   "<="),   # negativo para SpikeUp = manipulación
    ("quality_score",        ">="),
    ("vr_at_spike",          ">="),
    ("bars_to_entry",        "<="),  # más rápido = más limpio
    ("spike_extension_pct",  ">="),
    ("liq_ratio_at_spike",   "<="),  # bajo = no cascada
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

def find_threshold(values: np.ndarray, results: np.ndarray, direction: str, min_n: int):
    """
    Busca el umbral que maximiza AvgR en el subconjunto filtrado.
    direction: ">=" (mantener valores altos) o "<=" (mantener valores bajos).
    Retorna (threshold, avg_r_filtered, wr_filtered, n_filtered).
    """
    percentiles = np.percentile(values, np.arange(10, 91, 5))
    best = None
    for threshold in percentiles:
        if direction == ">=":
            mask = values >= threshold
        else:
            mask = values <= threshold
        if mask.sum() < min_n:
            continue
        avg_r = results[mask].mean()
        wr = (results[mask] > 0).mean()
        if best is None or avg_r > best[1]:
            best = (float(threshold), float(avg_r), float(wr), int(mask.sum()))
    return best

def run(strategy: str, min_n: int = 10):
    try:
        import pandas as pd
    except ImportError:
        print("Falta pandas: pip install pandas")
        sys.exit(1)

    table = "rbf_signals" if strategy == "rbf" else "amd_signals"
    gates = RBF_GATES if strategy == "rbf" else AMD_GATES

    print(f"[{strategy.upper()}] Descargando señales de {table}...")
    rows = fetch(table)
    df = pd.DataFrame(rows)
    df["result_r"] = pd.to_numeric(df["result_r"], errors="coerce")
    df = df[df["result_r"].notna()].copy()
    n_total = len(df)
    wr_global = (df["result_r"] > 0).mean()
    avg_r_global = df["result_r"].mean()
    print(f"  n={n_total}  WR={wr_global:.1%}  AvgR={avg_r_global:+.3f}\n")

    results = []
    print(f"{'Feature':<35} {'Dir':>4} {'Threshold':>12} {'AvgR (filt)':>12} {'WR (filt)':>10} {'n_filt':>8}")
    print("-" * 90)

    for feat, direction in gates:
        if feat not in df.columns:
            print(f"  {feat:<35} — columna no existe aún en DB")
            continue
        col = pd.to_numeric(df[feat], errors="coerce")
        valid_mask = col.notna()
        if valid_mask.sum() < min_n * 2:
            print(f"  {feat:<35} — muy pocos datos ({valid_mask.sum()})")
            continue

        vals = col[valid_mask].values
        ress = df.loc[valid_mask, "result_r"].values

        # Para features con dirección conocida
        if direction in (">=", "<="):
            result = find_threshold(vals, ress, direction, min_n)
        else:
            # between_0.5_3: busca el rango dz óptimo
            best = None
            for lo, hi in [(0.3, 2.5), (0.5, 3.0), (0.5, 2.5), (1.0, 3.0), (0.0, 2.0)]:
                mask = (np.abs(vals) >= lo) & (np.abs(vals) <= hi)
                if mask.sum() < min_n:
                    continue
                avg_r = ress[mask].mean()
                wr = (ress[mask] > 0).mean()
                if best is None or avg_r > best[1]:
                    best = (f"{lo:.1f}–{hi:.1f}", float(avg_r), float(wr), int(mask.sum()))
            result = best

        if result is None:
            print(f"  {feat:<35} — sin umbral válido con n≥{min_n}")
            continue

        threshold, avg_r_f, wr_f, n_f = result
        delta_wr = wr_f - wr_global
        delta_avg = avg_r_f - avg_r_global
        flag = "★" if avg_r_f > avg_r_global + 0.1 and n_f >= min_n else " "
        print(f"  {feat:<35} {direction:>4} {str(threshold):>12}  "
              f"AvgR {avg_r_f:+.3f} ({delta_avg:+.3f}) "
              f"WR {wr_f:.1%} ({delta_wr:+.1%}) "
              f"n={n_f:3d} {flag}")

        results.append({
            "feature": feat,
            "direction": direction,
            "optimal_threshold": threshold,
            "avg_r_filtered": round(avg_r_f, 4),
            "wr_filtered": round(wr_f, 4),
            "n_filtered": n_f,
            "delta_avg_r": round(delta_avg, 4),
            "delta_wr": round(delta_wr, 4),
            "actionable": avg_r_f > avg_r_global + 0.1 and n_f >= min_n,
        })

    print(f"\nBaseline global: WR={wr_global:.1%}  AvgR={avg_r_global:+.3f}  n={n_total}")
    print("★ = candidato a gate (delta AvgR > +0.1R y n_filtered >= min_n)")

    out = {
        "strategy": strategy,
        "n_total": n_total,
        "wr_global": round(float(wr_global), 4),
        "avg_r_global": round(float(avg_r_global), 4),
        "gates": results,
    }
    os.makedirs("docs/amd", exist_ok=True)
    fname = f"docs/amd/calibration_{strategy}.json"
    with open(fname, "w") as fp:
        json.dump(out, fp, indent=2)
    print(f"\n[ok] Resultados guardados en {fname}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=["rbf", "amd"], default="rbf")
    parser.add_argument("--min_n", type=int, default=10,
                        help="Mínimo de señales en el subconjunto filtrado")
    args = parser.parse_args()
    run(args.strategy, args.min_n)
