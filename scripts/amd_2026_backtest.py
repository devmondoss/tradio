import sys, os
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd
import numpy as np
from collections import deque
from pathlib import Path

DATASET = Path("dataset")

# Cargar parquet con 3 años de datos reales
df = pd.read_parquet(DATASET / "btcusdt_3y_m1.parquet")
df['dt'] = pd.to_datetime(df['ts_ms'], unit='ms', utc=True)
print(f"Parquet total: {len(df):,} barras  {df['dt'].iloc[0].date()} -> {df['dt'].iloc[-1].date()}")

# Filtrar solo 2026
df26 = df[df['dt'].dt.year == 2026].copy().reset_index(drop=True)
print(f"Solo 2026:     {len(df26):,} barras  {df26['dt'].iloc[0].date()} -> {df26['dt'].iloc[-1].date()}")

# Cargar metrics (taker_ratio como proxy de liq_ratio)
mdf = pd.read_parquet(DATASET / "btcusdt_3y_metrics.parquet")
mdf['dt'] = pd.to_datetime(mdf['ts_ms'], unit='ms', utc=True)
m26 = mdf[mdf['dt'].dt.year == 2026].copy()
print(f"Metrics 2026:  {len(m26):,} barras 5min")
print()

# AMD config (igual al strategy.toml)
CFG = {
    "accum_min"  : 10,
    "accum_max"  : 60,
    "range_min"  : 0.04,
    "range_max"  : 0.30,
    "manip_vr"   : 1.5,
    "max_wait"   : 10,
    "max_hold"   : 120,
    "stop_buf"   : 0.0008,
    "min_risk"   : 1.0,
    "rr"         : 2.0,
    "cooldown"   : 30,
}

# AMD detector — replica la maquina de estados del Rust
def run_amd(bars, cfg):
    vol_h = deque(maxlen=55)
    hist  = deque(maxlen=65)
    phase = "IDLE"
    rh = rl = ab = spk_ext = spk_dir = mrh = mrl = bss = spk_ts = spk_dz = spk_vr = spk_delta = None
    seen = 0; last_sig = -cfg["cooldown"]; sigs = []

    for row in bars.itertuples():
        seen += 1
        vol_h.append(row.vol)
        hist.append((row.high, row.low, row.close, row.bar_delta))
        if seen < 60: continue
        if seen - last_sig < cfg["cooldown"]: continue

        vr = row.vol / (sum(vol_h) / len(vol_h)) if vol_h else 1.0

        if phase == "IDLE":
            n = cfg["accum_min"]
            w = list(hist)[-n:]
            rh_ = max(b[0] for b in w)
            rl_ = min(b[1] for b in w)
            pct = (rh_ - rl_) / row.close * 100
            if cfg["range_min"] <= pct <= cfg["range_max"]:
                phase = "ACCUM"; rh = rh_; rl = rl_; ab = n

        elif phase == "ACCUM":
            if rl < row.close < rh:
                nrh = max(rh, row.high); nrl = min(rl, row.low)
                if (nrh - nrl) / row.close * 100 > cfg["range_max"]:
                    phase = "IDLE"; continue
                if ab + 1 > cfg["accum_max"]:
                    phase = "IDLE"; continue
                rh = nrh; rl = nrl; ab += 1; continue

            pct = (rh - rl) / row.close * 100
            if not (cfg["range_min"] <= pct <= cfg["range_max"]) or ab < cfg["accum_min"]:
                phase = "IDLE"; continue
            if vr < cfg["manip_vr"]:
                phase = "IDLE"; continue

            spike_up   = row.close > rh
            spike_down = row.close < rl
            if not (spike_up or spike_down):
                phase = "IDLE"; continue

            spk_dir = "Up" if spike_up else "Down"
            spk_ext = row.high if spike_up else row.low
            mrh = rh; mrl = rl; bss = 0
            spk_ts = row.ts_ms; spk_dz = row.dz; spk_vr = vr; spk_delta = row.bar_delta
            phase = "MANIP"

        elif phase == "MANIP":
            if bss >= cfg["max_wait"]:
                phase = "IDLE"; continue
            bss += 1
            dd = "Short" if spk_dir == "Up" else "Long"
            ok = (dd == "Short" and row.close < mrh) or (dd == "Long" and row.close > mrl)
            if not ok: continue
            if vr < 1.0: continue

            stop   = spk_ext*(1+cfg["stop_buf"]) if dd=="Short" else spk_ext*(1-cfg["stop_buf"])
            risk   = abs(stop - row.close)
            if risk < cfg["min_risk"]: phase = "IDLE"; continue
            target = row.close - risk*cfg["rr"] if dd=="Short" else row.close + risk*cfg["rr"]

            hh = (row.ts_ms // 3_600_000) % 24
            ses = ("London"          if  8 <= hh < 13 else
                   "LondonNyOverlap" if 13 <= hh < 17 else
                   "NewYork"         if 17 <= hh < 22 else "Asia")

            sigs.append({
                "ts_ms": row.ts_ms, "spike_ts": spk_ts,
                "direction": dd, "session": ses,
                "entry": row.close, "stop": stop, "target": target,
                "spk_dz": spk_dz, "spk_vr": spk_vr, "spk_delta": spk_delta,
                "accum_bars": ab,
            })
            last_sig = seen; phase = "IDLE"

    return sigs

print("Corriendo detector AMD sobre datos reales 2026...")
sigs = run_amd(df26, CFG)
print(f"Senales detectadas: {len(sigs)}")
days_covered = (df26['ts_ms'].iloc[-1]-df26['ts_ms'].iloc[0])/86400000
print(f"Ritmo: {len(sigs)/days_covered:.2f} senales/dia en {days_covered:.0f} dias")
print()

# Simular outcomes
hi = df26["high"].values
lo = df26["low"].values
ts_arr = df26["ts_ms"].values
idx_map = {int(t): i for i, t in enumerate(ts_arr)}

for s in sigs:
    i0 = idx_map.get(s["ts_ms"])
    s["exit"] = "TIMEOUT"; s["result_r"] = 0.0
    if i0 is None: continue
    for k in range(1, 121):
        if i0+k >= len(hi): break
        bh = hi[i0+k]; bl = lo[i0+k]
        if s["direction"] == "Short":
            if bl <= s["target"]: s["exit"]="TARGET"; s["result_r"]=2.0; break
            if bh >= s["stop"]:   s["exit"]="STOP";   s["result_r"]=-1.0; break
        else:
            if bh >= s["target"]: s["exit"]="TARGET"; s["result_r"]=2.0; break
            if bl <= s["stop"]:   s["exit"]="STOP";   s["result_r"]=-1.0; break

# Join taker_ratio del spike
m26_5m = m26.copy()
m26_5m["ts_5m"] = (m26_5m["ts_ms"] // 300_000) * 300_000
m5idx = m26_5m.set_index("ts_5m")["taker_ratio"].to_dict()
for s in sigs:
    t5 = (s["spike_ts"] // 300_000) * 300_000
    s["taker_ratio"] = m5idx.get(t5) or m5idx.get(t5+300_000) or m5idx.get(t5-300_000)

# Analisis
sf = pd.DataFrame(sigs)
cl = sf[sf["exit"] != "TIMEOUT"].copy()
cl["win"] = cl["exit"] == "TARGET"

print(f"=== RESULTADOS AMD REAL 2026 ===")
print(f"Senales totales:  {len(sf)}")
print(f"Cerradas:         {len(cl)}")
print(f"Timeout (< 120b): {len(sf)-len(cl)}")
print()
wr  = cl["win"].mean()*100
ar  = cl["result_r"].mean()
print(f"WR:      {wr:.1f}%")
print(f"avgR:    {ar:+.3f}")
print(f"n dias:  {days_covered:.0f}")
print()

print("--- Por sesion ---")
for ses, g in cl.groupby("session"):
    print(f"  {ses:20s}  n={len(g):3d}  WR={g['win'].mean()*100:.0f}%  avgR={g['result_r'].mean():+.2f}")

print()
print("--- Por direccion ---")
for d, g in cl.groupby("direction"):
    print(f"  {d:6s}  n={len(g):3d}  WR={g['win'].mean()*100:.0f}%  avgR={g['result_r'].mean():+.2f}")

print()
print("--- Gate dz (|dz| del spike) ---")
cl["dz_abs"] = cl["spk_dz"].abs()
for label, mask in [("dz < 1.0", cl["dz_abs"]<1.0),
                    ("dz 1.0-1.5", (cl["dz_abs"]>=1.0)&(cl["dz_abs"]<1.5)),
                    ("dz 1.5-2.5", (cl["dz_abs"]>=1.5)&(cl["dz_abs"]<2.5)),
                    ("dz >= 2.5",  cl["dz_abs"]>=2.5)]:
    g = cl[mask]
    if len(g) < 3: continue
    print(f"  {label:15s}  n={len(g):3d}  WR={g['win'].mean()*100:.0f}%  avgR={g['result_r'].mean():+.2f}")

print()
print("--- Gate taker_ratio (proxy liq_ratio) ---")
tr = cl[cl["taker_ratio"].notna()].copy()
print(f"  (taker_ratio disponible: {len(tr)}/{len(cl)})")
for label, mask in [("ratio < 1.0", tr["taker_ratio"]<1.0),
                    ("ratio 1.0-1.2", (tr["taker_ratio"]>=1.0)&(tr["taker_ratio"]<1.2)),
                    ("ratio 1.2-1.5", (tr["taker_ratio"]>=1.2)&(tr["taker_ratio"]<1.5)),
                    ("ratio >= 1.5",  tr["taker_ratio"]>=1.5)]:
    g = tr[mask]
    if len(g) < 3: continue
    print(f"  {label:18s}  n={len(g):3d}  WR={g['win'].mean()*100:.0f}%  avgR={g['result_r'].mean():+.2f}")

print()
print("--- Gates combinados ---")
gated_dz   = cl[cl["dz_abs"] >= 1.5]
gated_tr   = cl[(cl["taker_ratio"].notna()) & (cl["taker_ratio"] < 1.2)]
gated_both = cl[(cl["dz_abs"] >= 1.5) & (cl["taker_ratio"].notna()) & (cl["taker_ratio"] < 1.2)]
for label, g in [("dz >= 1.5",              gated_dz),
                 ("taker_ratio < 1.2",       gated_tr),
                 ("ambos gates",             gated_both)]:
    if len(g) < 3: continue
    print(f"  {label:25s}  n={len(g):3d}  WR={g['win'].mean()*100:.0f}%  avgR={g['result_r'].mean():+.2f}")

# Guardar CSV
sf.to_csv(DATASET / "amd_2026_signals.csv", index=False)
print(f"\nGuardado: dataset/amd_2026_signals.csv")
