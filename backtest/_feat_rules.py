"""Test de reglas de filtrado basadas en features: H1/H4 EMA, VR, nivel."""
import pandas as pd, numpy as np, sys
sys.path.insert(0, "backtest")
import _scalp as SC
from _scalp_more import gen_sc3x, ALLK_L, ALLK_S

SYMS = ["BTCUSDT","ETHUSDT","SOLUSDT"]
ALLK_L_NO_POC = ("val","pdl","wl","swl")
ALLK_S_NO_POC = ("vah","pdh","wh","swh")

def load_htf(sym, tf_min):
    if sym == "BTCUSDT":
        label = {60:"h1",240:"h4"}[tf_min]
        h = pd.read_parquet(f"data/bybit-perp/processed/btcusdt_perp_{label}.parquet",
                            columns=["ts_ms","close"])
    else:
        m1 = pd.read_parquet(SC.ASSETS[sym]["m1"], columns=["ts_ms","close"])
        m1.index = pd.to_datetime(m1.ts_ms, unit="ms", utc=True)
        r = m1.resample(f"{tf_min}min").agg({"close":"last"}).dropna()
        r["ts_ms"] = r.index.astype(np.int64)//1_000_000
        h = r.reset_index(drop=True)[["ts_ms","close"]]
    h["ema20"] = h["close"].ewm(span=20).mean()
    h["slope"] = h["ema20"].diff(3)
    return h

def run_rule(sym, rule, longk=ALLK_L, shortk=ALLK_S):
    s = SC.load(sym, 5); m1 = SC.load_m1_exit(sym); c = SC.SC3[sym]
    gen0 = gen_sc3x(vr_thr=c["vr_thr"], stop_atr=c["stop_atr"], tol_atr=c["tol_atr"],
                    rr_cap=c["rr_cap"], longk=longk, shortk=shortk)
    h1 = load_htf(sym, 60); h4 = load_htf(sym, 240)
    h1_ts=h1.ts_ms.values; h1_c=h1.close.values; h1_e=h1.ema20.values; h1_sl=h1.slope.values
    h4_ts=h4.ts_ms.values; h4_c=h4.close.values; h4_e=h4.ema20.values

    def gen_f(s, i):
        sigs = gen0(s, i) or []
        if not sigs: return []
        t = s.ts_ms[i]
        i1 = np.searchsorted(h1_ts, t, "right")-1
        i4 = np.searchsorted(h4_ts, t, "right")-1
        if i1 < 0 or i4 < 0: return []
        h1_bull = h1_c[i1] > h1_e[i1]
        h4_bull = h4_c[i4] > h4_e[i4]
        sl_ok_raw = h1_sl[i1] if np.isfinite(h1_sl[i1]) else 0
        out = []
        for sig in sigs:
            side = sig[0]; vr = float(s.vr[i])
            h1_ok  = (h1_bull  if side=="long" else not h1_bull)
            h4_ok  = (h4_bull  if side=="long" else not h4_bull)
            sl_ok  = (sl_ok_raw>0 if side=="long" else sl_ok_raw<0)
            keep = False
            if   rule == "h1":                 keep = h1_ok
            elif rule == "h1_or_vr3":          keep = h1_ok or vr > 3
            elif rule == "h1_or_h4":           keep = h1_ok or h4_ok
            elif rule == "h1_and_h4":          keep = h1_ok and h4_ok
            elif rule == "h1_and_h4_or_vr3":   keep = (h1_ok and h4_ok) or vr > 3
            elif rule == "h1_or_h4_or_vr3":    keep = h1_ok or h4_ok or vr > 3
            elif rule == "h1_or_slope":        keep = h1_ok or sl_ok
            elif rule == "h1_and_slope":       keep = h1_ok and sl_ok
            elif rule == "h1_slope_or_vr3":    keep = (h1_ok or sl_ok) or vr > 3
            if keep: out.append(sig)
        return out

    df = SC.run_setup(s, gen_f, m1, 5, entry_mode="maker", mgmt="fade",
                      timeout_min=240, max_day=3, cooldown=6)
    if df is None or "r" not in df.columns or len(df) == 0:
        return 0, 0.0, float("nan"), float("nan")
    st = SC.stats(df); days = (df.ts.max()-df.ts.min())/86400000
    return st["n"], st["n"]/days, st["isA"], st["oosA"]

def run_baseline(sym, longk, shortk):
    s = SC.load(sym, 5); m1 = SC.load_m1_exit(sym); c = SC.SC3[sym]
    gen0 = gen_sc3x(vr_thr=c["vr_thr"], stop_atr=c["stop_atr"], tol_atr=c["tol_atr"],
                    rr_cap=c["rr_cap"], longk=longk, shortk=shortk)
    df = SC.run_setup(s, gen0, m1, 5, entry_mode="maker", mgmt="fade",
                      timeout_min=240, max_day=3, cooldown=6)
    if df is None or "r" not in df.columns or len(df) == 0:
        return 0, 0.0, float("nan"), float("nan")
    st = SC.stats(df); days = (df.ts.max()-df.ts.min())/86400000
    return st["n"], st["n"]/days, st["isA"], st["oosA"]

rules = [
    ("baseline",             None,                 ALLK_L,        ALLK_S),
    ("baseline sin POC",     None,                 ALLK_L_NO_POC, ALLK_S_NO_POC),
    ("H1 EMA",               "h1",                 ALLK_L,        ALLK_S),
    ("H1 OR VR>3",           "h1_or_vr3",          ALLK_L,        ALLK_S),
    ("H1 OR H4",             "h1_or_h4",           ALLK_L,        ALLK_S),
    ("H1 AND H4",            "h1_and_h4",          ALLK_L,        ALLK_S),
    ("H1+H4 OR VR>3",        "h1_and_h4_or_vr3",   ALLK_L,        ALLK_S),
    ("H1 OR H4 OR VR>3",     "h1_or_h4_or_vr3",    ALLK_L,        ALLK_S),
    ("H1 OR slope",          "h1_or_slope",         ALLK_L,        ALLK_S),
    ("H1+slope OR VR>3",     "h1_slope_or_vr3",     ALLK_L,        ALLK_S),
    ("H1 OR VR>3 sinPOC",    "h1_or_vr3",           ALLK_L_NO_POC, ALLK_S_NO_POC),
    ("H1 OR H4 sinPOC",      "h1_or_h4",            ALLK_L_NO_POC, ALLK_S_NO_POC),
    ("H1+H4 OR VR>3 sinPOC", "h1_and_h4_or_vr3",    ALLK_L_NO_POC, ALLK_S_NO_POC),
]

hdr = ("regla", "BTC_n", "BTC_nd", "BTC_IS", "BTC_OOS",
       "ETH_n", "ETH_nd", "ETH_IS", "ETH_OOS",
       "SOL_n", "SOL_nd", "SOL_IS", "SOL_OOS", "peorOOS", "tot_nd")
print(f"{'regla':<25} | {'BTC':>4} {'/d':>3} {'IS':>6} {'OOS':>6} | {'ETH':>4} {'/d':>3} {'IS':>6} {'OOS':>6} | {'SOL':>4} {'/d':>3} {'IS':>6} {'OOS':>6} | peor   tot")
print("-"*112)

for label, rule, lk, sk in rules:
    row = []; worst = 99.0; total_nd = 0.0
    for sym in SYMS:
        if rule is None:
            n, nd, isr, oos = run_baseline(sym, lk, sk)
        else:
            n, nd, isr, oos = run_rule(sym, rule, lk, sk)
        row.append((n, nd, isr, oos))
        if np.isfinite(oos): worst = min(worst, oos)
        total_nd += nd
    r = row
    print(f"{label:<25} | {r[0][0]:>4} {r[0][1]:>3.1f} {r[0][2]:>+6.3f} {r[0][3]:>+6.3f} | "
          f"{r[1][0]:>4} {r[1][1]:>3.1f} {r[1][2]:>+6.3f} {r[1][3]:>+6.3f} | "
          f"{r[2][0]:>4} {r[2][1]:>3.1f} {r[2][2]:>+6.3f} {r[2][3]:>+6.3f} | "
          f"{worst:>+5.3f}  {total_nd:.1f}")
