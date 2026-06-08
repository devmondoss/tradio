#!/usr/bin/env python3
"""Grid search manip_min_vr x accum_min_bars para encontrar parametros optimos."""
import json, os, time, urllib.request
from collections import deque
from pathlib import Path
import pandas as pd

for line in Path(__file__).parent.parent.joinpath('.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1); os.environ.setdefault(k.strip(), v.strip())

FAPI = "https://fapi.binance.com"
CACHE = Path(__file__).parent.parent / "dataset" / "btcusdt_90d_m1.csv"


def fetch_or_load():
    if CACHE.exists():
        df = pd.read_csv(CACHE)
        print(f"[cache] {len(df)} barras desde {CACHE.name}")
        return df
    now_ms = int(time.time()*1000); start_ms = now_ms - 90*86400000
    rows = []; cur = start_ms
    print("[fetch] descargando 90d M1...")
    while cur < now_ms:
        url = f"{FAPI}/fapi/v1/klines?symbol=BTCUSDT&interval=1m&startTime={cur}&endTime={now_ms}&limit=1500"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent":"fs/1"}), timeout=30) as r:
            page = json.loads(r.read())
        if not page: break
        rows.extend(page); cur = int(page[-1][0]) + 60000
        if len(page) < 1500: break
        time.sleep(0.08)
    cols = ["ts_open","open","high","low","close","volume","tc","qv","nt","tbv","tbq","_"]
    df = pd.DataFrame(rows, columns=cols)
    for c in ["open","high","low","close","volume","tbv"]: df[c] = df[c].astype(float)
    df["ts_ms"] = df["ts_open"].astype("int64")
    df["bar_delta"] = 2.0*df["tbv"] - df["volume"]
    df.to_csv(CACHE, index=False)
    print(f"  {len(df)} barras guardadas en cache")
    return df


def run_amd(bars_list, manip_vr, accum_min):
    vol_h = deque(maxlen=55); hist = deque(maxlen=60); cvd_h = deque(maxlen=25)
    cvd = 0.0; phase = "IDLE"
    rh = rl = cvd_sum = ab = None
    spk_ext = spk_dir = mrh = mrl = mrb = mcvd = bss = None
    seen = 0; sigs = []

    def vr(v):
        if len(vol_h) < 5: return 1.0
        m = sum(vol_h)/len(vol_h)
        return v/m if m > 0 else 1.0

    for h, l, c, v, d, ts in bars_list:
        seen += 1; vol_h.append(v); hist.append({"h":h,"l":l,"c":c,"d":d})
        cvd += d; cvd_h.append(cvd)
        if seen < 60: continue
        vr_ = vr(v)

        if phase == "IDLE":
            n = accum_min
            if len(hist) < n: continue
            w = list(hist)[-n:]
            rh_ = max(b["h"] for b in w); rl_ = min(b["l"] for b in w)
            pct = (rh_-rl_)/c*100
            if 0.06 <= pct <= 0.45:
                phase = "ACCUM"; rh = rh_; rl = rl_
                cvd_sum = sum(b["d"] for b in w); ab = n

        elif phase == "ACCUM":
            if rl < c < rh:
                nrh = max(rh, h); nrl = min(rl, l)
                if (nrh-nrl)/c*100 > 0.45: phase = "IDLE"; continue
                rh = nrh; rl = nrl; ab += 1; cvd_sum += d
                if ab > 50: phase = "IDLE"
                continue
            pct = (rh-rl)/c*100
            if not (0.06 <= pct <= 0.45) or ab < accum_min: phase = "IDLE"; continue
            if vr_ < manip_vr: phase = "IDLE"; continue
            sd = "Up" if c > rh else "Down"
            phase = "MANIP"
            spk_ext = h if sd == "Up" else l; spk_dir = sd
            mrh = rh; mrl = rl; bss = 0

        elif phase == "MANIP":
            if bss >= 10: phase = "IDLE"; continue
            bss += 1
            dd = "Short" if spk_dir == "Up" else "Long"
            ok = (dd == "Short" and c < mrh) or (dd == "Long" and c > mrl)
            if not ok: continue
            if vr_ < 1.5: continue
            entry = c; buf = 0.0008
            stop = spk_ext*(1+buf) if dd == "Short" else spk_ext*(1-buf)
            risk = abs(stop-entry)
            if risk < 1: phase = "IDLE"; continue
            target = entry - risk*2 if dd == "Short" else entry + risk*2
            hh = (ts//3600000) % 24
            ses = "London" if 8<=hh<13 else ("LondonNY" if 13<=hh<17 else ("NewYork" if 17<=hh<22 else "Asia"))
            sigs.append({"ts":ts, "dir":dd, "entry":entry, "stop":stop, "target":target, "ses":ses})
            phase = "IDLE"
    return sigs


def simulate(sigs, bars_list):
    idx = {ts: i for i, (h,l,c,v,d,ts) in enumerate(bars_list)}
    results = []
    for s in sigs:
        i0 = idx.get(s["ts"])
        if i0 is None: results.append({**s, "exit":"OPEN", "r":None}); continue
        exit_ = "OPEN"; r_ = None
        for k in range(1, 121):
            if i0+k >= len(bars_list): break
            bh, bl = bars_list[i0+k][0], bars_list[i0+k][1]
            if s["dir"] == "Short":
                if bl <= s["target"]: exit_ = "TARGET"; r_ = 2.0; break
                if bh >= s["stop"]: exit_ = "STOP"; r_ = -1.0; break
            else:
                if bh >= s["target"]: exit_ = "TARGET"; r_ = 2.0; break
                if bl <= s["stop"]: exit_ = "STOP"; r_ = -1.0; break
        results.append({**s, "exit":exit_, "r":r_})
    return results


def stats(res, seg_fn=None):
    pool = [r for r in res if r["exit"] in ("TARGET","STOP")]
    if not pool: return 0, 0, 0
    n = len(pool)
    wins = sum(1 for r in pool if r["r"] > 0)
    avg  = sum(r["r"] for r in pool) / n
    return n, wins/n*100, avg


def main():
    df   = fetch_or_load()
    bars_list = list(zip(df.high, df.low, df.close, df.volume, df.bar_delta, df.ts_ms))
    WEEKS = 90/7

    print(f"\n{'VR_spk':>8} {'a_min':>6} {'sig/dia':>8} {'n':>6} {'WR%':>7} {'avgR':>7}  London_L              Asia_L")
    print("-"*85)

    for manip_vr in [1.3, 1.5, 1.7, 2.0, 2.5]:
        for accum_min in [8, 12, 15]:
            sigs = run_amd(bars_list, manip_vr, accum_min)
            res  = simulate(sigs, bars_list)
            n, wr, avg = stats(res)
            per_day = len(res) / 90

            ll = [r for r in res if r["ses"]=="London" and r["dir"]=="Long" and r["exit"] in ("TARGET","STOP")]
            al = [r for r in res if r["ses"]=="Asia"   and r["dir"]=="Long" and r["exit"] in ("TARGET","STOP")]

            ll_str = f"{sum(1 for r in ll if r['r']>0)/len(ll)*100:.0f}%({len(ll)}n)" if ll else "  --"
            al_str = f"{sum(1 for r in al if r['r']>0)/len(al)*100:.0f}%({len(al)}n)" if al else "  --"

            flag = " <---" if 3 <= per_day <= 6 and avg > 0 else ""
            print(f"{manip_vr:>8.1f} {accum_min:>6} {per_day:>8.1f} {n:>6} {wr:>7.1f}% {avg:>+7.3f}  {ll_str:<22} {al_str}{flag}")


if __name__ == "__main__":
    main()
