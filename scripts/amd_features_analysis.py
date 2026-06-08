#!/usr/bin/env python3
"""
Option A (v2): Features de microestructura EN LA BARRA DEL SPIKE.
El edge del AMD viene de liquidaciones en el spike — no de la dirección del mercado.
Buscamos si liq_ratio (y otras features) en el spike discriminan ganadores vs perdedores.
"""
import json, os, time, urllib.request
from collections import deque
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
import numpy as np

for line in Path(__file__).parent.parent.joinpath('.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

FAPI    = "https://fapi.binance.com"
DATASET = Path(__file__).parent.parent / "dataset"
FEATURES = ['obi_fast','obi_slow','obi_l5','l5_l10_div','vr','cvd_slope',
            'dz','absorption_long','absorption_short','liq_ratio','spread_ticks']


# ── 1. Datos M1 Binance ────────────────────────────────────────────────────────

def fetch_klines(start_ms, end_ms):
    rows = []; cur = start_ms
    while cur < end_ms:
        url = (f"{FAPI}/fapi/v1/klines?symbol=BTCUSDT&interval=1m"
               f"&startTime={cur}&endTime={end_ms}&limit=1500")
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent":"fs/1"}), timeout=30
        ) as r:
            page = json.loads(r.read())
        if not page: break
        rows.extend(page); cur = int(page[-1][0]) + 60000
        if len(page) < 1500: break
        time.sleep(0.05)
    return rows


def load_m1():
    cache = DATASET / "btcusdt_jun1_5_m1.csv"
    if cache.exists():
        df = pd.read_csv(cache)
        print(f"[cache] {len(df)} barras M1 jun1-5")
        return df
    # Jun 1 00:00 UTC -> Jun 5 23:59 UTC
    start = int(datetime(2026,6,1,0,0, tzinfo=timezone.utc).timestamp()*1000)
    end   = int(datetime(2026,6,6,0,0, tzinfo=timezone.utc).timestamp()*1000)
    print(f"[fetch] M1 Jun 1-5...")
    raw = fetch_klines(start, end)
    cols = ["ts_open","open","high","low","close","volume","tc","qv","nt","tbv","tbq","_"]
    df = pd.DataFrame(raw, columns=cols)
    for c in ["open","high","low","close","volume","tbv"]: df[c] = df[c].astype(float)
    df["ts_ms"]      = df["ts_open"].astype("int64")
    df["bar_delta"]  = 2.0*df["tbv"] - df["volume"]
    df.to_csv(cache, index=False)
    print(f"  {len(df)} barras")
    return df


# ── 2. Detector AMD ────────────────────────────────────────────────────────────

def run_amd_full(bars_df, manip_vr=1.5, accum_min=8):
    vol_h = deque(maxlen=55); hist = deque(maxlen=60)
    cvd_h = deque(maxlen=25); cvd = 0.0
    phase = "IDLE"; rh = rl = cvd_sum = ab = None
    spk_ext = spk_dir = mrh = mrl = bss = None
    seen = 0; sigs = []

    def vr_(v):
        if len(vol_h) < 5: return 1.0
        m = sum(vol_h)/len(vol_h)
        return v/m if m > 0 else 1.0

    for row in bars_df.itertuples():
        h,l,c,v,d,ts = row.high, row.low, row.close, row.volume, row.bar_delta, row.ts_ms
        seen += 1; vol_h.append(v); hist.append({"h":h,"l":l,"c":c,"d":d})
        cvd += d; cvd_h.append(cvd)
        if seen < 60: continue
        vr__ = vr_(v)

        if phase == "IDLE":
            n = accum_min
            if len(hist) < n: continue
            w = list(hist)[-n:]
            rh_ = max(b["h"] for b in w); rl_ = min(b["l"] for b in w)
            pct = (rh_-rl_)/c*100
            if 0.06 <= pct <= 0.45:
                phase="ACCUM"; rh=rh_; rl=rl_
                cvd_sum = sum(b["d"] for b in w); ab = n

        elif phase == "ACCUM":
            if rl < c < rh:
                nrh=max(rh,h); nrl=min(rl,l)
                if (nrh-nrl)/c*100 > 0.45: phase="IDLE"; continue
                rh=nrh; rl=nrl; ab+=1; cvd_sum+=d
                if ab > 50: phase="IDLE"
                continue
            pct=(rh-rl)/c*100
            if not(0.06<=pct<=0.45) or ab<accum_min: phase="IDLE"; continue
            if vr__ < manip_vr: phase="IDLE"; continue
            sd = "Up" if c>rh else "Down"
            phase="MANIP"; spk_ext=h if sd=="Up" else l
            spk_dir=sd; mrh=rh; mrl=rl; bss=0
            spk_ts=ts  # timestamp de la barra del spike

        elif phase == "MANIP":
            if bss >= 10: phase="IDLE"; continue
            bss += 1
            dd = "Short" if spk_dir=="Up" else "Long"
            ok = (dd=="Short" and c<mrh) or (dd=="Long" and c>mrl)
            if not ok: continue
            if vr__ < 1.5: continue
            entry=c; buf=0.0008
            stop = spk_ext*(1+buf) if dd=="Short" else spk_ext*(1-buf)
            risk = abs(stop-entry)
            if risk < 1: phase="IDLE"; continue
            target = entry-risk*2 if dd=="Short" else entry+risk*2
            hh = (ts//3600000)%24
            ses = ("London" if 8<=hh<13 else
                   "LondonNY" if 13<=hh<17 else
                   "NewYork" if 17<=hh<22 else "Asia")
            sigs.append({"ts_ms": ts, "spike_ts": spk_ts,
                         "direction":dd,"entry":entry,
                         "stop":stop,"target":target,"session":ses})
            phase="IDLE"
    return sigs


# ── 3. Simular outcomes ────────────────────────────────────────────────────────

def simulate(sigs, bars_df):
    idx = {row.ts_ms: i for i,row in enumerate(bars_df.itertuples())}
    for s in sigs:
        i0 = idx.get(s["ts_ms"])
        s["exit"] = "OPEN"; s["result_r"] = None; s["exit_bars"] = None
        if i0 is None: continue
        for k in range(1, 121):
            if i0+k >= len(bars_df): break
            b = bars_df.iloc[i0+k]
            if s["direction"] == "Short":
                if b.low  <= s["target"]: s["exit"]="TARGET"; s["result_r"]=2.0;  s["exit_bars"]=k; break
                if b.high >= s["stop"]:   s["exit"]="STOP";   s["result_r"]=-1.0; s["exit_bars"]=k; break
            else:
                if b.high >= s["target"]: s["exit"]="TARGET"; s["result_r"]=2.0;  s["exit_bars"]=k; break
                if b.low  <= s["stop"]:   s["exit"]="STOP";   s["result_r"]=-1.0; s["exit_bars"]=k; break
    return sigs


# ── 4. Join con scalping_bars EN EL SPIKE ─────────────────────────────────────

def join_features_at_spike(sigs, sb):
    """Busca los features de microestructura en la barra del SPIKE, no de entry.
    El liq_ratio relevante es el del momento en que ocurren las liquidaciones."""
    sb_idx = sb.set_index("ts_ms")
    for s in sigs:
        spk_ts = s.get("spike_ts", s["ts_ms"])
        match = None
        # Buscar en la barra del spike ±2 barras (ventana de 5 barras = 5 min)
        for delta_ms in [0, 60000, -60000, 120000, -120000]:
            ts_try = spk_ts + delta_ms
            if ts_try in sb_idx.index:
                match = sb_idx.loc[ts_try]
                break
        if match is not None:
            for f in FEATURES:
                s[f"spk_{f}"] = float(match[f]) if f in match and match[f] is not None else None
        else:
            for f in FEATURES:
                s[f"spk_{f}"] = None
    return sigs


# ── 5. Analisis enfocado en liquidaciones ─────────────────────────────────────

def analyze(sigs):
    df = pd.DataFrame(sigs)
    closed = df[df["exit"].isin(["TARGET","STOP"])].copy()
    closed["won"] = closed["result_r"] > 0

    n = len(closed)
    wins = int(closed["won"].sum())
    print(f"\n{'='*65}")
    print(f"  AMD — features en barra del SPIKE (jun 1-5)")
    print(f"{'='*65}")
    print(f"  Total AMD signals  : {len(sigs)}")
    print(f"  Cerradas           : {n}  (TARGET={wins}  STOP={n-wins})")
    if n == 0: return
    print(f"  Win rate baseline  : {wins/n*100:.1f}%")
    print(f"  Avg R baseline     : {closed['result_r'].mean():+.3f}")

    matched = closed[closed["spk_liq_ratio"].notna()].copy()
    print(f"  Con spike match    : {len(matched)}")
    if len(matched) < 4:
        print(f"\n  Pocos datos con spike match — mostrando lo disponible:")
        print(matched[["ts_ms","spike_ts","direction","session","spk_liq_ratio","spk_dz","exit","result_r"]].to_string())
        # Guardar de todas formas
        out = DATASET / "amd_features_jun1_5.csv"
        df.to_csv(out, index=False)
        print(f"[output] {out}")
        return

    winners = matched[matched["won"] == True]
    losers  = matched[matched["won"] == False]

    SPIKE_FEATS = [f"spk_{f}" for f in FEATURES]
    print(f"\n  -- Features en barra del spike: Ganadores (n={len(winners)}) vs Perdedores (n={len(losers)}) --")
    print(f"  {'Feature':<26} {'Win_med':>9} {'Los_med':>9} {'Diff%':>8}  {'Gate?'}")
    print(f"  {'-'*70}")
    for f in SPIKE_FEATS:
        if f not in matched.columns or matched[f].isna().all(): continue
        wm = winners[f].median() if len(winners) > 0 else float('nan')
        lm = losers[f].median()  if len(losers)  > 0 else float('nan')
        diff = wm - lm if not (np.isnan(wm) or np.isnan(lm)) else float('nan')
        flag = ""
        if not np.isnan(diff):
            rng = matched[f].std()
            if rng > 0 and abs(diff) > 0.25 * rng:
                flag = "<-- GATE POTENCIAL"
        wm_s = f"{wm:.3f}" if not np.isnan(wm) else "  NaN"
        lm_s = f"{lm:.3f}" if not np.isnan(lm) else "  NaN"
        diff_s = f"{diff:+.3f}" if not np.isnan(diff) else "   NaN"
        print(f"  {f:<26} {wm_s:>9} {lm_s:>9} {diff_s:>8}  {flag}")

    # Gate liq_ratio: threshold sweep
    print(f"\n  -- Gate liq_ratio en spike: ¿umbral optimo? --")
    liq_col = "spk_liq_ratio"
    if liq_col in matched.columns and matched[liq_col].notna().sum() > 4:
        percentiles = [20, 30, 40, 50, 60, 70]
        thresholds = [matched[liq_col].quantile(p/100) for p in percentiles]
        print(f"  {'Threshold':>12} {'n_pass':>8} {'WR%':>8} {'avgR':>8} {'vs_base':>10}")
        base_wr = wins/n*100
        for thr in sorted(set([round(t,3) for t in thresholds])):
            subset = matched[matched[liq_col] >= thr]
            if len(subset) < 3: continue
            w = int((subset["result_r"]>0).sum())
            wr = w/len(subset)*100
            avg = subset["result_r"].mean()
            delta_wr = wr - base_wr
            flag = " <--" if wr > base_wr + 10 else ""
            print(f"  liq>={thr:>7.3f}   n={len(subset):>4}   WR={wr:>5.1f}%   avgR={avg:>+6.3f}   vs_base={delta_wr:>+5.1f}%{flag}")

    # Gate dz
    print(f"\n  -- Gate dz>=1 en spike --")
    dz_col = "spk_dz"
    if dz_col in matched.columns and matched[dz_col].notna().sum() > 4:
        for dz_thr in [0.5, 1.0, 1.5, 2.0]:
            subset = matched[matched[dz_col] >= dz_thr]
            if len(subset) < 3: continue
            w = int((subset["result_r"]>0).sum())
            wr = w/len(subset)*100
            avg = subset["result_r"].mean()
            print(f"  dz>={dz_thr:.1f}   n={len(subset):>4}   WR={wr:>5.1f}%   avgR={avg:>+6.3f}")

    # Por sesión (sin filtrar por dirección — liquidaciones funcionan en ambos lados)
    print(f"\n  -- Por sesion (Long+Short combinados) --")
    for ses, grp in matched.groupby("session"):
        c = grp
        if len(c) == 0: continue
        w = int((c["result_r"]>0).sum())
        liq_med = c[liq_col].median() if liq_col in c.columns else float('nan')
        print(f"  {ses:<14} n={len(c):>3}  WR={w/len(c)*100:.0f}%  avgR={c['result_r'].mean():+.2f}"
              f"  liq_ratio_med={liq_med:.3f}")

    # Tabla individual
    print(f"\n  -- Señales con spike features --")
    cols = ["ts_ms","spike_ts","direction","session","spk_liq_ratio","spk_dz","spk_vr","spk_absorption_long","spk_absorption_short","exit","result_r"]
    available = [c for c in cols if c in matched.columns]
    print(matched[available].sort_values("ts_ms").to_string(index=False))

    out = DATASET / "amd_features_jun1_5.csv"
    df.to_csv(out, index=False)
    print(f"\n[output] {out}  ({len(df)} filas)")


def main():
    sb = pd.read_csv(DATASET / "scalping_bars.csv")
    print(f"[scalping_bars] {len(sb)} filas, {sb['datetime_utc'].iloc[0]} -> {sb['datetime_utc'].iloc[-1]}")

    m1 = load_m1()
    m1 = m1.sort_values("ts_ms").reset_index(drop=True)

    print(f"[AMD detector] corriendo sobre {len(m1)} barras M1...")
    sigs = run_amd_full(m1)
    print(f"  {len(sigs)} señales AMD detectadas")

    sigs = simulate(sigs, m1)
    closed = [s for s in sigs if s["exit"] in ("TARGET","STOP")]
    print(f"  {len(closed)} cerradas ({sum(1 for s in closed if s['result_r']>0)} TARGET, {sum(1 for s in closed if s['result_r']<0)} STOP)")

    sigs = join_features_at_spike(sigs, sb)
    matched = [s for s in sigs if s.get("spk_liq_ratio") is not None]
    print(f"  {len(matched)} con spike feature match en scalping_bars")

    analyze(sigs)


if __name__ == "__main__":
    main()
