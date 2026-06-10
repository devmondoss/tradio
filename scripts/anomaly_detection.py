"""
FASE 5: Detección de anomalías en señales recientes.

Detecta:
1. Regime drift: distribución de macro_regime o session cambió significativamente
2. Feature drift: distribución de features clave se salió del rango histórico
3. WR rolling drift: WR de la ventana rolling de 10 señales cayó por debajo del threshold
4. Score drift: signal_score_v2 promedio reciente cayó vs histórico

Uso:
    python scripts/anomaly_detection.py
    python scripts/anomaly_detection.py --strategy amd --window 10

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

RBF_WATCH = ["absorption_score", "vr_at_breakout", "obi_at_entry", "dz_at_entry",
             "confluence_score", "signal_score_v2"]
AMD_WATCH = ["delta_dz_at_spike", "quality_score", "vr_at_spike",
             "obi_at_entry", "signal_score_v2"]

def fetch_all(table: str) -> list[dict]:
    url = (
        f"{SUPABASE_URL}/rest/v1/{table}"
        f"?select=*&limit=1000&order=timestamp_ms.desc"
    )
    req = urllib.request.Request(
        url,
        headers={"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())

def run(strategy: str, window: int):
    try:
        import pandas as pd
        from scipy import stats
    except ImportError:
        print("Falta pandas/scipy: pip install pandas scipy")
        sys.exit(1)

    table = "rbf_signals" if strategy == "rbf" else "amd_signals"
    watch = RBF_WATCH if strategy == "rbf" else AMD_WATCH

    print(f"[{strategy.upper()}] Cargando señales de {table}...")
    rows = fetch_all(table)
    df = pd.DataFrame(rows)
    if df.empty or len(df) < window * 2:
        print(f"  Pocas señales ({len(df)}). Mínimo {window * 2} para análisis de drift.")
        return

    df = df.sort_values("timestamp_ms").reset_index(drop=True)
    n = len(df)
    recent = df.tail(window).copy()
    historical = df.iloc[:-window].copy()

    print(f"  Total: {n}  |  Recientes (ventana={window}): {len(recent)}  |  Histórico: {len(historical)}")
    anomalies = []

    # ── 1. WR rolling drift ──────────────────────────────────────────────────
    closed = df[df["result_r"].notna()].copy()
    closed["result_r"] = pd.to_numeric(closed["result_r"], errors="coerce")
    if len(closed) >= window:
        recent_closed = closed.tail(window)
        wr_recent = (recent_closed["result_r"] > 0).mean()
        wr_hist = (closed.iloc[:-window]["result_r"] > 0).mean() if len(closed) > window else wr_recent
        delta = wr_recent - wr_hist
        status = "⚠️  ANOMALÍA" if wr_recent < 0.30 or delta < -0.20 else "✓"
        print(f"\n[WR Rolling] {status}")
        print(f"  WR reciente ({window}):   {wr_recent:.1%}")
        print(f"  WR histórico:  {wr_hist:.1%}")
        print(f"  Delta:         {delta:+.1%}")
        if wr_recent < 0.30 or delta < -0.20:
            anomalies.append(f"WR rolling bajo: {wr_recent:.1%} (Δ{delta:+.1%})")

    # ── 2. Feature drift (KS test) ───────────────────────────────────────────
    print(f"\n[Feature Drift] KS test entre recientes vs histórico:")
    print(f"  {'Feature':<30} {'KS stat':>8} {'p-value':>8} {'Status':>10}")
    print("  " + "─" * 60)
    for feat in watch:
        if feat not in df.columns:
            continue
        col_h = pd.to_numeric(historical.get(feat, pd.Series()), errors="coerce").dropna()
        col_r = pd.to_numeric(recent.get(feat, pd.Series()), errors="coerce").dropna()
        if len(col_h) < 5 or len(col_r) < 3:
            continue
        ks, p = stats.ks_2samp(col_h.values, col_r.values)
        status = "⚠️  DRIFT" if p < 0.05 else "✓ ok"
        print(f"  {feat:<30} {ks:>8.4f} {p:>8.4f}  {status}")
        if p < 0.05:
            mean_h = float(col_h.mean())
            mean_r = float(col_r.mean())
            anomalies.append(f"Feature drift {feat}: hist_mean={mean_h:.3f} recent_mean={mean_r:.3f} (KS p={p:.3f})")

    # ── 3. Regime distribution drift ────────────────────────────────────────
    regime_col = "macro_regime" if strategy == "rbf" else "session_name"
    if regime_col in df.columns:
        hist_dist = historical[regime_col].value_counts(normalize=True)
        rec_dist  = recent[regime_col].value_counts(normalize=True)
        print(f"\n[{regime_col} Distribution]")
        all_vals = set(hist_dist.index) | set(rec_dist.index)
        for val in sorted(all_vals):
            h = hist_dist.get(val, 0)
            r = rec_dist.get(val, 0)
            delta = r - h
            flag = "⚠️" if abs(delta) > 0.20 else " "
            print(f"  {val:<20} hist={h:.1%} recent={r:.1%} Δ={delta:+.1%} {flag}")

    # ── 4. Score drift ───────────────────────────────────────────────────────
    if "signal_score_v2" in df.columns:
        sc_h = pd.to_numeric(historical["signal_score_v2"], errors="coerce").dropna()
        sc_r = pd.to_numeric(recent["signal_score_v2"], errors="coerce").dropna()
        if len(sc_h) > 0 and len(sc_r) > 0:
            mean_h = float(sc_h.mean())
            mean_r = float(sc_r.mean())
            delta = mean_r - mean_h
            status = "⚠️  BAJO" if mean_r < 0.35 or delta < -0.15 else "✓"
            print(f"\n[Score v2 Drift] {status}")
            print(f"  Score medio histórico: {mean_h:.3f}")
            print(f"  Score medio reciente:  {mean_r:.3f}  (Δ{delta:+.3f})")
            if mean_r < 0.35 or delta < -0.15:
                anomalies.append(f"score_v2 cayó: hist={mean_h:.3f} recent={mean_r:.3f}")

    # ── Resumen ──────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    if anomalies:
        print(f"⚠️  {len(anomalies)} ANOMALÍA(S) DETECTADA(S):")
        for a in anomalies:
            print(f"  - {a}")
    else:
        print("✓ Sin anomalías detectadas — distribuciones estables")
    print(f"{'='*60}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", choices=["rbf", "amd"], default="rbf")
    parser.add_argument("--window", type=int, default=10,
                        help="Número de señales recientes a comparar vs histórico")
    args = parser.parse_args()
    run(args.strategy, args.window)
