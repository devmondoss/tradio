#!/usr/bin/env python3
"""
AMD 3-Year Backtest  —  BTCUSDT M1
Fuente de datos: data.binance.vision (portal público, sin rate limit de API)
  - Klines  1m  : .../futures/um/daily/klines/BTCUSDT/1m/BTCUSDT-1m-YYYY-MM-DD.zip
  - Metrics 5min: .../futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-YYYY-MM-DD.zip
    contiene: OI, taker_long_short_vol_ratio, top_trader_ratio

Features reconstructidas desde datos públicos:
  bar_delta   -> 2*taker_buy_vol - total_vol  (CVD por barra)
  vr          -> vol / rolling_mean(vol, 50)
  dz          -> (close - vwap_session) / rolling_std(close, 20)  [z-score VWAP]
  oi_delta    -> OI[n] - OI[n-1]   (desde metrics 5min)
  taker_ratio -> sum_taker_long_short_vol_ratio  (proxy liq_ratio)
  tt_ratio    -> top_trader_long_short_ratio      (smart money positioning)

Gates a validar (todos testeados en sweep):
  abs(spk_dz)    >= 1.5   : spike rompio zona significativa (>1.5 sigma de VWAP)
  taker_ratio    <  1.2   : no es cascada — barrido contenido
  oi_delta       >  0     : nuevo OI abierto en spike (conviction)
  direccional_taker       : takers del lado del trade en el spike
"""

import sys, os, io, zipfile, urllib.request, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

# ─── Configuracion ────────────────────────────────────────────────────────────

SYMBOL  = "BTCUSDT"
START   = datetime(2023, 6, 8, tzinfo=timezone.utc)
END     = datetime(2026, 6, 7, tzinfo=timezone.utc)
BASE_DL = "https://data.binance.vision/data/futures/um/daily"

DATASET = Path(__file__).parent.parent / "dataset"
RAW_DIR = DATASET / "raw_3y"
RAW_DIR.mkdir(parents=True, exist_ok=True)

AMD_CFG = {
    "accum_min"  : 8,
    "accum_max"  : 50,
    "range_min"  : 0.06,   # % del precio
    "range_max"  : 0.45,
    "manip_vr"   : 1.5,    # VR minimo en el spike
    "entry_vr"   : 1.5,    # VR minimo en la barra de entry
    "max_wait"   : 10,     # barras maximo esperando entry tras spike
    "max_hold"   : 120,    # barras antes de TIMEOUT (2h)
    "stop_buf"   : 0.0008, # buffer sobre spike extreme
    "min_risk"   : 1.0,    # USD minimo de riesgo
    "rr"         : 2.0,    # risk/reward
}

KLINE_COLS  = ["ts_open","open","high","low","close","vol","ts_close",
               "qvol","ntrades","tbv","tbq","_ignore"]
METRIC_COLS = ["create_time","symbol","sum_oi","sum_oi_val",
               "cnt_tt","sum_tt_ratio","cnt_ls","sum_taker_ratio"]


# ─── Descarga ─────────────────────────────────────────────────────────────────

def _get(url, path):
    if path.exists():
        return True
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "amd-bt/1"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read()
        if len(data) < 100:   # archivo vacio / 404 en content
            return False
        path.write_bytes(data)
        return True
    except Exception:
        return False


def _dl_day(dt):
    ds = dt.strftime("%Y-%m-%d")
    k = _get(f"{BASE_DL}/klines/{SYMBOL}/1m/{SYMBOL}-1m-{ds}.zip",
             RAW_DIR / f"k_{ds}.zip")
    m = _get(f"{BASE_DL}/metrics/{SYMBOL}/{SYMBOL}-metrics-{ds}.zip",
             RAW_DIR / f"m_{ds}.zip")
    return ds, k, m


def download_all():
    days = []
    d = START
    while d <= END:
        days.append(d); d += timedelta(days=1)

    need = [d for d in days if not (RAW_DIR / f"k_{d.strftime('%Y-%m-%d')}.zip").exists()
                             or not (RAW_DIR / f"m_{d.strftime('%Y-%m-%d')}.zip").exists()]
    if not need:
        print("[download] todo en cache")
        return

    print(f"[download] {len(need)} dias pendientes de {len(days)} total ...")
    n_k = n_m = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = {ex.submit(_dl_day, d): d for d in need}
        for i, f in enumerate(as_completed(futs), 1):
            _, k, m = f.result(); n_k += k; n_m += m
            if i % 100 == 0 or i == len(need):
                print(f"  {i}/{len(need)}  klines={n_k}  metrics={n_m}")
    print(f"[download] done  klines={n_k}  metrics={n_m}")


# ─── Carga y preprocesado ─────────────────────────────────────────────────────

def _read_zip(path, col_names):
    try:
        with zipfile.ZipFile(path) as z:
            with z.open(z.namelist()[0]) as f:
                df = pd.read_csv(f, header=None, names=col_names)
        # Si la primera fila es el header textual, eliminarlo
        if not pd.to_numeric(df.iloc[0, 0], errors="coerce") == df.iloc[0, 0]:
            df = df.iloc[1:].reset_index(drop=True)
        return df
    except Exception:
        return None


def load_klines():
    cache = DATASET / "btcusdt_3y_m1.parquet"
    if cache.exists():
        df = pd.read_parquet(cache)
        print(f"[cache] klines {len(df):,} barras M1")
        return df

    print("[build] klines desde zips ...")
    dfs = []
    paths = sorted(RAW_DIR.glob("k_*.zip"))
    for i, p in enumerate(paths):
        df = _read_zip(p, KLINE_COLS)
        if df is not None:
            dfs.append(df)
        if (i + 1) % 200 == 0:
            print(f"  {i+1}/{len(paths)} archivos leidos")

    df = pd.concat(dfs, ignore_index=True)

    for c in ["open", "high", "low", "close", "vol", "tbv"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["ts_ms"]     = pd.to_numeric(df["ts_open"], errors="coerce").astype("int64")
    df["bar_delta"] = 2.0 * df["tbv"] - df["vol"]
    df = (df.dropna(subset=["ts_ms", "close"])
            .sort_values("ts_ms")
            .drop_duplicates("ts_ms")
            .reset_index(drop=True))

    # VR: rolling 50 barras
    df["vr"] = df["vol"] / df["vol"].rolling(50, min_periods=5).mean()

    # VWAP con reset diario UTC
    df["day"]     = df["ts_ms"] // 86_400_000
    df["typical"] = (df["high"] + df["low"] + df["close"]) / 3.0
    df["_tpv"]    = df["typical"] * df["vol"]
    df["cum_tpv"] = df.groupby("day")["_tpv"].cumsum()
    df["cum_vol"] = df.groupby("day")["vol"].cumsum()
    df["vwap"]    = df["cum_tpv"] / df["cum_vol"].replace(0.0, np.nan)

    # dz = z-score respecto VWAP (ventana 20 barras para std)
    df["std20"] = df["close"].rolling(20, min_periods=5).std()
    df["dz"]    = (df["close"] - df["vwap"]) / df["std20"].replace(0.0, np.nan)
    df["dz"]    = df["dz"].fillna(0.0).clip(-10, 10)

    df = df[["ts_ms", "open", "high", "low", "close", "vol",
             "bar_delta", "vr", "vwap", "dz"]]
    df.to_parquet(cache, index=False)
    print(f"[klines] {len(df):,} barras  -> {cache.name}")
    return df


def load_metrics():
    cache = DATASET / "btcusdt_3y_metrics.parquet"
    if cache.exists():
        df = pd.read_parquet(cache)
        print(f"[cache] metrics {len(df):,} barras 5min")
        return df

    print("[build] metrics desde zips ...")
    dfs = []
    for p in sorted(RAW_DIR.glob("m_*.zip")):
        try:
            with zipfile.ZipFile(p) as z:
                with z.open(z.namelist()[0]) as f:
                    # El CSV tiene header real y create_time como datetime string
                    dfs.append(pd.read_csv(f, header=0))
        except Exception:
            pass

    df = pd.concat(dfs, ignore_index=True)
    # create_time = "2023-06-08 00:00:00" -> epoch ms UTC
    df["ts_ms"] = (pd.to_datetime(df["create_time"], utc=True)
                     .astype("int64") // 1_000_000)
    df["oi"]          = pd.to_numeric(df["sum_open_interest"],              errors="coerce")
    df["taker_ratio"] = pd.to_numeric(df["sum_taker_long_short_vol_ratio"], errors="coerce")
    df["tt_ratio"]    = pd.to_numeric(df["sum_toptrader_long_short_ratio"], errors="coerce")
    df = (df.dropna(subset=["ts_ms"])
            .sort_values("ts_ms")
            .drop_duplicates("ts_ms")
            .reset_index(drop=True))
    df["oi_delta"] = df["oi"].diff()

    df = df[["ts_ms", "oi", "oi_delta", "taker_ratio", "tt_ratio"]]
    df.to_parquet(cache, index=False)
    print(f"[metrics] {len(df):,} barras 5min  -> {cache.name}")
    return df


# ─── AMD Detector ─────────────────────────────────────────────────────────────

def run_amd(bars_df, cfg=AMD_CFG):
    vol_h = deque(maxlen=55)
    hist  = deque(maxlen=60)
    phase = "IDLE"
    rh = rl = ab = None
    spk_ext = spk_dir = mrh = mrl = bss = None
    spk_ts = spk_dz = spk_vr = None
    seen = 0; sigs = []

    vals = bars_df[["ts_ms", "high", "low", "close", "vol", "dz"]].values

    def vr_(v):
        if len(vol_h) < 5: return 1.0
        m = sum(vol_h) / len(vol_h)
        return v / m if m > 0 else 1.0

    for ts_ms, h, l, c, v, dz_v in vals:
        ts_ms = int(ts_ms)
        dz_v  = float(dz_v) if dz_v == dz_v else 0.0
        seen += 1; vol_h.append(v); hist.append((h, l, c))
        if seen < 60: continue
        vr = vr_(v)

        if phase == "IDLE":
            n = cfg["accum_min"]
            if len(hist) < n: continue
            w   = list(hist)[-n:]
            rh_ = max(b[0] for b in w)
            rl_ = min(b[1] for b in w)
            pct = (rh_ - rl_) / c * 100
            if cfg["range_min"] <= pct <= cfg["range_max"]:
                phase = "ACCUM"; rh = rh_; rl = rl_; ab = n

        elif phase == "ACCUM":
            if rl < c < rh:
                nrh = max(rh, h); nrl = min(rl, l)
                if (nrh - nrl) / c * 100 > cfg["range_max"]:
                    phase = "IDLE"; continue
                rh = nrh; rl = nrl; ab += 1
                if ab > cfg["accum_max"]:
                    phase = "IDLE"
                continue
            pct = (rh - rl) / c * 100
            if not (cfg["range_min"] <= pct <= cfg["range_max"]) or ab < cfg["accum_min"]:
                phase = "IDLE"; continue
            if vr < cfg["manip_vr"]:
                phase = "IDLE"; continue
            sd      = "Up" if c > rh else "Down"
            phase   = "MANIP"
            spk_ext = h if sd == "Up" else l
            spk_dir = sd; mrh = rh; mrl = rl; bss = 0
            spk_ts  = ts_ms; spk_dz = dz_v; spk_vr = vr

        elif phase == "MANIP":
            if bss >= cfg["max_wait"]:
                phase = "IDLE"; continue
            bss += 1
            dd = "Short" if spk_dir == "Up" else "Long"
            ok = (dd == "Short" and c < mrh) or (dd == "Long" and c > mrl)
            if not ok: continue
            if vr < cfg["entry_vr"]: continue
            buf    = cfg["stop_buf"]
            stop   = spk_ext * (1 + buf) if dd == "Short" else spk_ext * (1 - buf)
            risk   = abs(stop - c)
            if risk < cfg["min_risk"]: phase = "IDLE"; continue
            target = c - risk * cfg["rr"] if dd == "Short" else c + risk * cfg["rr"]
            hh  = (ts_ms // 3_600_000) % 24
            ses = ("London"   if  8 <= hh < 13 else
                   "LondonNY" if 13 <= hh < 17 else
                   "NewYork"  if 17 <= hh < 22 else "Asia")
            sigs.append({
                "ts_ms"    : ts_ms,   "spike_ts": spk_ts,
                "direction": dd,      "session" : ses,
                "entry"    : c,       "stop"    : stop,
                "target"   : target,  "spk_dz"  : spk_dz,
                "spk_vr"   : spk_vr,
            })
            phase = "IDLE"

    return sigs


# ─── Simulate outcomes ────────────────────────────────────────────────────────

def simulate(sigs, bars_df):
    hi      = bars_df["high"].values
    lo      = bars_df["low"].values
    ts_arr  = bars_df["ts_ms"].values
    idx_map = {int(t): i for i, t in enumerate(ts_arr)}
    max_h   = AMD_CFG["max_hold"]

    for s in sigs:
        i0 = idx_map.get(s["ts_ms"])
        s["exit"] = "TIMEOUT"; s["result_r"] = 0.0
        if i0 is None: continue
        for k in range(1, max_h + 1):
            if i0 + k >= len(hi): break
            bh = hi[i0 + k]; bl = lo[i0 + k]
            if s["direction"] == "Short":
                if bl <= s["target"]: s["exit"] = "TARGET"; s["result_r"] =  2.0; break
                if bh >= s["stop"]:   s["exit"] = "STOP";   s["result_r"] = -1.0; break
            else:
                if bh >= s["target"]: s["exit"] = "TARGET"; s["result_r"] =  2.0; break
                if bl <= s["stop"]:   s["exit"] = "STOP";   s["result_r"] = -1.0; break
    return sigs


# ─── Join metrics en barra del spike ─────────────────────────────────────────

def join_metrics(sigs, mdf):
    mdf = mdf.copy()
    mdf["ts_5m"] = (mdf["ts_ms"] // 300_000) * 300_000
    m5 = mdf.set_index("ts_5m")[["taker_ratio", "oi_delta", "tt_ratio"]]

    for s in sigs:
        spk_5m = (s["spike_ts"] // 300_000) * 300_000
        row = None
        for delta in [0, 300_000, -300_000, 600_000, -600_000]:
            key = spk_5m + delta
            if key in m5.index:
                row = m5.loc[key]; break
        if row is not None:
            s["m_taker"] = None if pd.isna(row["taker_ratio"]) else float(row["taker_ratio"])
            s["m_oi_d"]  = None if pd.isna(row["oi_delta"])    else float(row["oi_delta"])
            s["m_tt"]    = None if pd.isna(row["tt_ratio"])    else float(row["tt_ratio"])
        else:
            s["m_taker"] = s["m_oi_d"] = s["m_tt"] = None
    return sigs


# ─── Analisis + gate sweep ────────────────────────────────────────────────────

def _row(label, sub, days):
    c = sub[sub["exit"].isin(["TARGET", "STOP"])]
    if len(c) < 10:
        return f"  {label:<54} n<10"
    n = len(c); w = int((c["result_r"] > 0).sum())
    avg = c["result_r"].mean()
    expectancy = w / n * 2.0 + (1 - w / n) * (-1.0)   # E[R] = WR*2 - (1-WR)*1
    spd = n / days
    return (f"  {label:<54} n={n:>5}  WR={w/n*100:>5.1f}%  "
            f"avgR={avg:>+6.3f}  E[R]={expectancy:>+5.2f}  sig/dia={spd:.2f}")


def analyze(sigs, days):
    df = pd.DataFrame(sigs)
    out = DATASET / "amd_3y_signals.csv"
    df.to_csv(out, index=False)

    closed = df[df["exit"].isin(["TARGET", "STOP"])]
    n = len(closed); wins = int((closed["result_r"] > 0).sum())

    print(f"\n{'='*80}")
    print(f"  AMD  3 AÑOS  |  {SYMBOL} M1  |  {START.date()} -> {END.date()}")
    print(f"{'='*80}")
    print(f"  Señales totales  : {len(df):,}  ({len(df)/days:.2f}/dia)")
    print(f"  TARGET           : {wins:,}")
    print(f"  STOP             : {n - wins:,}")
    print(f"  TIMEOUT          : {(df['exit']=='TIMEOUT').sum():,}")
    if n == 0:
        print("  Sin trades cerrados."); return
    print(f"  Win rate         : {wins/n*100:.1f}%")
    print(f"  Avg R            : {closed['result_r'].mean():+.3f}")
    print(f"  Expectancy E[R]  : {wins/n*2 + (1-wins/n)*(-1):+.3f}")

    # Por sesion
    print(f"\n  -- Por sesion (baseline, sin gates) --")
    for ses, g in closed.groupby("session"):
        w_ = int((g["result_r"] > 0).sum())
        print(f"  {ses:<14}  n={len(g):>5}  WR={w_/len(g)*100:.1f}%  "
              f"avgR={g['result_r'].mean():+.3f}")

    # Por direccion
    print(f"\n  -- Por direccion --")
    for dd, g in closed.groupby("direction"):
        w_ = int((g["result_r"] > 0).sum())
        print(f"  {dd:<8}  n={len(g):>5}  WR={w_/len(g)*100:.1f}%  "
              f"avgR={g['result_r'].mean():+.3f}")

    # Gate sweep
    m = df[df["m_taker"].notna()].copy()
    pct_cov = len(m) / len(df) * 100
    print(f"\n  -- Gate sweep  ({len(m):,} senales con metrics = {pct_cov:.0f}%) --")
    print(f"  {'Gate':<54} {'n':>6}  {'WR%':>6}  {'avgR':>7}  {'E[R]':>6}  sig/dia")
    print(f"  {'-'*80}")

    # Para taker direccional: Long AMD quiere taker>1 (compradores en spike down)
    #                         Short AMD quiere taker<1 (vendedores en spike up)
    taker_aligned = (
        ((m["direction"] == "Long")  & (m["m_taker"] > 1.0)) |
        ((m["direction"] == "Short") & (m["m_taker"] < 1.0))
    )

    gates = [
        ("BASELINE con metrics",                            m),
        ("dz_spike >= 1.0",                                 m[m["spk_dz"].abs() >= 1.0]),
        ("dz_spike >= 1.5",                                 m[m["spk_dz"].abs() >= 1.5]),
        ("dz_spike >= 2.0",                                 m[m["spk_dz"].abs() >= 2.0]),
        ("taker_ratio < 1.2  (barrido contenido)",          m[m["m_taker"] < 1.2]),
        ("taker_ratio < 0.9",                               m[m["m_taker"] < 0.9]),
        ("taker_direccional (alineado con trade)",           m[taker_aligned]),
        ("oi_delta > 0  (nuevo OI en spike)",               m[m["m_oi_d"] > 0]),
        ("oi_delta < 0  (OI cerrando / stop hunters)",      m[m["m_oi_d"] < 0]),
        ("----- COMBINADOS -----",                          None),
        ("dz>=1.0 + taker<1.2",                             m[(m["spk_dz"].abs()>=1.0) & (m["m_taker"]<1.2)]),
        ("dz>=1.5 + taker<1.2",                             m[(m["spk_dz"].abs()>=1.5) & (m["m_taker"]<1.2)]),
        ("dz>=1.5 + oi_delta>0",                            m[(m["spk_dz"].abs()>=1.5) & (m["m_oi_d"]>0)]),
        ("dz>=1.5 + taker_dir",                             m[(m["spk_dz"].abs()>=1.5) & taker_aligned]),
        ("dz>=1.5 + taker<1.2 + oi>0",                     m[(m["spk_dz"].abs()>=1.5) & (m["m_taker"]<1.2) & (m["m_oi_d"]>0)]),
        ("----- POR SESION -----",                          None),
        ("London",                                          m[m["session"]=="London"]),
        ("London + dz>=1.5",                                m[(m["session"]=="London") & (m["spk_dz"].abs()>=1.5)]),
        ("London + dz>=1.5 + taker<1.2",                   m[(m["session"]=="London") & (m["spk_dz"].abs()>=1.5) & (m["m_taker"]<1.2)]),
        ("London + dz>=1.5 + taker_dir",                   m[(m["session"]=="London") & (m["spk_dz"].abs()>=1.5) & taker_aligned[m.index]]),
        ("NewYork",                                         m[m["session"]=="NewYork"]),
        ("NewYork + dz>=1.5",                               m[(m["session"]=="NewYork") & (m["spk_dz"].abs()>=1.5)]),
        ("NewYork + dz>=1.5 + taker_dir",                  m[(m["session"]=="NewYork") & (m["spk_dz"].abs()>=1.5) & taker_aligned[m.index]]),
        ("Asia  (referencia — esperamos WR bajo)",          m[m["session"]=="Asia"]),
    ]

    for label, sub in gates:
        if sub is None:
            print(f"\n  {label}")
            continue
        print(_row(label, sub, days))

    # Equity curve del mejor gate (dz>=1.5 + taker<1.2)
    best = m[(m["spk_dz"].abs() >= 1.5) & (m["m_taker"] < 1.2)].copy()
    best = best[best["exit"].isin(["TARGET","STOP"])].sort_values("ts_ms")
    if len(best) > 10:
        _plot_equity(best, "AMD — dz>=1.5 + taker<1.2")

    print(f"\n[output] {out}")


def _plot_equity(df, title):
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        df = df.copy()
        df["dt"]      = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
        df["cumR"]    = df["result_r"].cumsum()
        df["color"]   = df["result_r"].apply(lambda r: "green" if r > 0 else "red")

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                            row_heights=[0.7, 0.3],
                            subplot_titles=["Equity curve (R acumulado)", "R por trade"])

        fig.add_trace(go.Scatter(x=df["dt"], y=df["cumR"],
                                 mode="lines", name="Equity",
                                 line=dict(color="royalblue", width=2)), row=1, col=1)

        fig.add_trace(go.Bar(x=df["dt"], y=df["result_r"],
                             marker_color=df["color"], name="Trade R"), row=2, col=1)

        n = len(df); wins = (df["result_r"] > 0).sum()
        avg = df["result_r"].mean()
        dd = (df["cumR"] - df["cumR"].cummax()).min()
        fig.update_layout(
            title=f"{title} | n={n}  WR={wins/n*100:.1f}%  avgR={avg:+.3f}  MaxDD={dd:.1f}R",
            height=600, template="plotly_dark",
            showlegend=False,
        )

        out = DATASET / "amd_3y_equity.html"
        fig.write_html(str(out))
        print(f"[chart] {out}")
    except ImportError:
        print("[chart] instala plotly: pip install plotly")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    t0 = time.time()

    # Descargar si falta
    download_all()

    # Cargar
    print("\n[load] klines ...")
    bars = load_klines()
    print("[load] metrics ...")
    mdf  = load_metrics()

    days = (END - START).days

    # AMD detector
    print(f"\n[AMD] corriendo sobre {len(bars):,} barras M1 ...")
    sigs = run_amd(bars)
    print(f"  {len(sigs):,} señales  ({len(sigs)/days:.2f}/dia)")

    # Simular outcomes
    print("[simulate] outcomes ...")
    sigs = simulate(sigs, bars)
    n_t = sum(1 for s in sigs if s["exit"] == "TARGET")
    n_s = sum(1 for s in sigs if s["exit"] == "STOP")
    n_o = sum(1 for s in sigs if s["exit"] == "TIMEOUT")
    print(f"  TARGET={n_t}  STOP={n_s}  TIMEOUT={n_o}")

    # Join metrics en barra del spike
    print("[join] metrics en spike bars ...")
    sigs = join_metrics(sigs, mdf)
    n_m = sum(1 for s in sigs if s.get("m_taker") is not None)
    print(f"  {n_m:,}/{len(sigs):,} señales con metrics match")

    # Analisis
    analyze(sigs, days)

    print(f"\n[done] {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
