"""
_scalp_nautilus_year.py — equity REAL (Nautilus, fills tick) de sc3 sobre el año, $500.
================================================================================
Camina chunks de N días sobre toda la cobertura de cada activo (subprocess por chunk
= memoria liberada), agrega TODOS los fills reales en una equity de $500 y reporta:
  · fixed $5/trade vs compounding 1%
  · período completo vs OOS-only, anualizado
Uso: python backtest/_scalp_nautilus_year.py [chunk_days] [max_offset]
"""
import sys, subprocess, glob, os
import numpy as np, pandas as pd

OOS_MS = int(pd.Timestamp("2026-03-01", tz="UTC").value // 1_000_000)
RESDIR = "E:/bybit-data/_scalp/results"
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
RISK = 0.01; CAP0 = 500.0


def run_chunks(chunk, max_off):
    os.makedirs(RESDIR, exist_ok=True)
    # limpiar csvs previos de nautilus
    for f in glob.glob(f"{RESDIR}/nautilus_*.csv"):
        os.remove(f)
    for sym in SYMS:
        off = 0
        while off <= max_off:
            print(f"\n>>> {sym} chunk offset={off} ({chunk}d)", flush=True)
            r = subprocess.run([sys.executable, "-X", "utf8", "backtest/_nautilus_scalp.py",
                                str(chunk), sym, "5", str(off)],
                               capture_output=True, text=True)
            tail = r.stdout.strip().splitlines()[-3:] if r.stdout else []
            for l in tail: print("   ", l)
            if "chunk vacío" in (r.stdout or ""):
                break
            off += chunk


def equity(df, compound):
    cap = CAP0; peak = CAP0; dd = 0.0
    for rr in df.r.values:
        risk_usd = cap*RISK if compound else CAP0*RISK
        cap += risk_usd*rr
        if cap <= 0: return 0.0, 100.0
        peak = max(peak, cap); dd = max(dd, (peak-cap)/peak)
    return cap, 100*dd


def report(df, label):
    if len(df) == 0: print(f"\n── {label} ── sin trades"); return
    span = (df.ts.max()-df.ts.min())/86_400_000
    print(f"\n── {label} ── n={len(df)}  span={span:.0f}d  ΣR={df.r.sum():+.0f}  "
          f"avgR={df.r.mean():+.3f}  WR={100*(df.r>0).mean():.0f}%")
    for comp in (False, True):
        cap, dd = equity(df, comp)
        ret = 100*(cap-CAP0)/CAP0
        ann = ((cap/CAP0)**(365/max(span,1))-1)*100 if cap > 0 else -100
        print(f"   {'COMPOUND 1%' if comp else 'FIJO $5   '}: $500 → ${cap:,.0f}  ({ret:+.0f}%)  "
              f"maxDD {dd:.0f}%  CAGR {ann:+.0f}%/año")


def aggregate():
    rows = []
    for sym in SYMS:
        fs = glob.glob(f"{RESDIR}/nautilus_{sym[:3]}_*.csv")
        if not fs: continue
        d = pd.concat([pd.read_csv(f) for f in fs], ignore_index=True)
        d = d.drop_duplicates("ts").sort_values("ts"); d["sym"] = sym[:3]
        rows.append(d)
    if not rows:
        print("sin csvs de nautilus"); return
    allt = pd.concat(rows, ignore_index=True).sort_values("ts").reset_index(drop=True)
    print(f"\n{'='*70}\n  EQUITY REAL sc3 (Nautilus fills tick) — $500, 3 activos\n{'='*70}")
    for sym in SYMS:
        report(allt[allt.sym == sym[:3]], sym[:3])
    report(allt, "PORTFOLIO (completo)")
    report(allt[allt.ts >= OOS_MS], "PORTFOLIO (OOS-only)")


def main():
    chunk = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    max_off = int(sys.argv[2]) if len(sys.argv) > 2 else 380
    run_chunks(chunk, max_off)
    aggregate()


if __name__ == "__main__":
    main()
