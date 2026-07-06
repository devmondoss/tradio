"""Análisis de features por trade: correlación H1/H4 EMA, slope, nivel, sesión con avgR real."""
import pandas as pd, numpy as np, sys
sys.path.insert(0, "backtest")
import _scalp as SC
from _scalp_more import gen_sc3x, ALLK_L, ALLK_S

SYMS = ["BTCUSDT","ETHUSDT","SOLUSDT"]

def load_htf(sym, tf_min):
    if sym == "BTCUSDT":
        label = {60:"h1",240:"h4"}[tf_min]
        h = pd.read_parquet(f"data/bybit-perp/processed/btcusdt_perp_{label}.parquet",
                            columns=["ts_ms","close","delta"])
    else:
        m1p = SC.ASSETS[sym]["m1"]
        m1 = pd.read_parquet(m1p, columns=["ts_ms","close","delta"])
        m1.index = pd.to_datetime(m1.ts_ms, unit="ms", utc=True)
        r = m1.resample(f"{tf_min}min").agg({"close":"last","delta":"sum"}).dropna()
        r["ts_ms"] = r.index.astype(np.int64)//1_000_000
        h = r.reset_index(drop=True)[["ts_ms","close","delta"]]
    h["ema20"] = h["close"].ewm(span=20).mean()
    h["ema50"] = h["close"].ewm(span=50).mean()
    h["slope20"] = h["ema20"].diff(3)
    return h

LONG_LV  = {"val":"vp_val","poc":"vp_poc","pdl":"prev_day_low","wl":"weekly_low","swl":"swing_low_50"}
SHORT_LV = {"vah":"vp_vah","poc":"vp_poc","pdh":"prev_day_high","wh":"weekly_high","swh":"swing_high_50"}
KEY_RANK = {"poc":0,"val":1,"vah":1,"pdl":2,"pdh":2,"wl":3,"wh":3,"swl":4,"swh":4}

all_dfs = []
for sym in SYMS:
    print(f"[{sym}] cargando...", flush=True)
    s  = SC.load(sym, 5)
    m1 = SC.load_m1_exit(sym)
    c  = SC.SC3[sym]
    gen0 = gen_sc3x(vr_thr=c["vr_thr"], stop_atr=c["stop_atr"], tol_atr=c["tol_atr"],
                    rr_cap=c["rr_cap"], longk=ALLK_L, shortk=ALLK_S)
    h1 = load_htf(sym, 60);  h4 = load_htf(sym, 240)
    h1_ts=h1.ts_ms.values; h1_c=h1.close.values; h1_e20=h1.ema20.values
    h1_sl=h1.slope20.values; h1_d=h1.delta.values
    h4_ts=h4.ts_ms.values; h4_c=h4.close.values; h4_e20=h4.ema20.values; h4_e50=h4.ema50.values
    m5_e20 = pd.Series(s.c).ewm(span=20).mean().values
    hour = (s.ts_ms/3_600_000).astype(int)%24

    feat_map = {}
    atr_med = pd.Series(s.atr).rolling(500,min_periods=50).median().shift(1).values
    cool=0; dcount={}

    for i in range(60, s.n-1):
        if i<cool or s.atr[i]<=0: continue
        if not (np.isfinite(atr_med[i]) and s.atr[i]>atr_med[i]): continue
        d=int(s.day[i])
        if dcount.get(d,0)>=3: continue
        sigs = gen0(s, i) or []
        if not sigs: continue
        side,lvl,stop,tp1,tp2,tag = sigs[0]
        if not np.isfinite([lvl,stop,tp2]).all(): continue
        ref=s.c[i-1]
        if side=="long" and not (lvl<ref): continue
        if side=="short" and not (lvl>ref): continue
        t=s.ts_ms[i]
        i1=np.searchsorted(h1_ts,t,"right")-1
        i4=np.searchsorted(h4_ts,t,"right")-1
        if i1<0 or i4<0:
            cool=i+6; dcount[d]=dcount.get(d,0)+1; continue
        h1_bull = h1_c[i1]>h1_e20[i1]
        h4_bull = h4_c[i4]>h4_e20[i4]
        h4_50_bull = h4_c[i4]>h4_e50[i4]
        h1_sl_ok = (h1_sl[i1]>0 if side=="long" else h1_sl[i1]<0) if np.isfinite(h1_sl[i1]) else False
        m5_ok = s.c[i]>m5_e20[i] if side=="long" else s.c[i]<m5_e20[i]
        hh=int(hour[i])
        best_key=None
        lv_dict = LONG_LV if side=="long" else SHORT_LV
        for key,col in lv_dict.items():
            v=getattr(s,col)[i]
            if np.isfinite(v) and abs(v-lvl)<1.0: best_key=key; break
        lvl_rank=KEY_RANK.get(best_key,5)
        h1_ok=(h1_bull if side=="long" else not h1_bull)
        h4_ok=(h4_bull if side=="long" else not h4_bull)
        h4_50_ok=(h4_50_bull if side=="long" else not h4_50_bull)
        feat_map[int(s.ts[i])] = dict(h1_ok=h1_ok, h4_ok=h4_ok, h4_50_ok=h4_50_ok,
                                       m5_ok=m5_ok, h1_slope_ok=h1_sl_ok,
                                       session=("london_ny" if 7<=hh<=17 else "asia"),
                                       lvl_rank=lvl_rank, vr=float(s.vr[i]))
        cool=i+6; dcount[d]=dcount.get(d,0)+1

    df = SC.run_setup(s, gen0, m1, 5, entry_mode="maker", mgmt="fade",
                      timeout_min=240, max_day=3, cooldown=6)
    if df is None or "r" not in df.columns: continue
    df["sym"]=sym[:3]; df["oos"]=df.ts>=SC.OOS_MS
    for col in ["h1_ok","h4_ok","h4_50_ok","m5_ok","h1_slope_ok","session","lvl_rank","vr"]:
        df[col] = df["ts"].map(lambda t,c=col: feat_map.get(int(t),{}).get(c, np.nan))
    all_dfs.append(df)

big = pd.concat(all_dfs, ignore_index=True)
big["h1_ok"]       = big["h1_ok"].astype("boolean")
big["h4_ok"]       = big["h4_ok"].astype("boolean")
big["h4_50_ok"]    = big["h4_50_ok"].astype("boolean")
big["m5_ok"]       = big["m5_ok"].astype("boolean")
big["h1_slope_ok"] = big["h1_slope_ok"].astype("boolean")

def show(label, mask):
    g=big[mask]; o=g[g.oos]
    if len(g)<5: return
    print(f"  {label:<36} n={len(g):>4}  WR={100*(g.r>0).mean():>3.0f}%  avgR={g.r.mean():>+.3f}  OOS={len(o):>3}  OOS_avgR={o.r.mean() if len(o) else float('nan'):>+.3f}")

print("\n=== BASELINE ===")
show("todo",                              big.r.notna())

print("\n-- H1 EMA ALIGNMENT --")
show("H1 alineado",                       big.h1_ok==True)
show("H1 contra",                         big.h1_ok==False)

print("\n-- H4 EMA ALIGNMENT --")
show("H4 EMA20 alineado",                 big.h4_ok==True)
show("H4 EMA50 alineado",                 big.h4_50_ok==True)
show("H4 contra",                         big.h4_ok==False)

print("\n-- COMBINACIONES H1+H4 --")
show("H1+H4 ambos alineados",             (big.h1_ok==True)&(big.h4_ok==True))
show("H1 alin + H4 contra",              (big.h1_ok==True)&(big.h4_ok==False))
show("H4 alin + H1 contra",              (big.h1_ok==False)&(big.h4_ok==True))
show("H1+H4 ambos CONTRA",               (big.h1_ok==False)&(big.h4_ok==False))

print("\n-- SLOPE EMA H1 --")
show("H1 slope alineado",                 big.h1_slope_ok==True)
show("H1 alin + slope alineado",         (big.h1_ok==True)&(big.h1_slope_ok==True))
show("H1 alin + slope CONTRA",           (big.h1_ok==True)&(big.h1_slope_ok==False))

print("\n-- NIVEL (0=POC, 1=VAL/VAH, 2=PDH/PDL, 3=weekly, 4=swing) --")
for r in [0,1,2,3,4]:
    show(f"rank={r}",                     big.lvl_rank==r)

print("\n-- SESION --")
show("London+NY (7-17 UTC)",             big.session=="london_ny")
show("Asia (resto)",                     big.session=="asia")

print("\n-- COMBINACIONES COMPLETAS --")
show("H1+H4 + London/NY",               (big.h1_ok==True)&(big.h4_ok==True)&(big.session=="london_ny"))
show("H1 + London/NY",                  (big.h1_ok==True)&(big.session=="london_ny"))
show("H1 + rank<=1 (POC/VAL/VAH)",     (big.h1_ok==True)&(big.lvl_rank<=1))
show("H1 + slope + rank<=1",            (big.h1_ok==True)&(big.h1_slope_ok==True)&(big.lvl_rank<=1))
show("H4_50 + rank<=1",                 (big.h4_50_ok==True)&(big.lvl_rank<=1))
show("H1+H4 OR (POC+VR>3)",            ((big.h1_ok==True)&(big.h4_ok==True))|((big.lvl_rank==0)&(big.vr>3)))
show("H1 contra + VR>3 (reversal?)",   (big.h1_ok==False)&(big.vr>3))
show("H1 + VR>3",                       (big.h1_ok==True)&(big.vr>3))
show("VR>3 solo",                        big.vr>3)
show("H1+H4+slope alineados",           (big.h1_ok==True)&(big.h4_ok==True)&(big.h1_slope_ok==True))
show("H1+H4 OR H1+slope (OR expandido)",(big.h1_ok==True)&((big.h4_ok==True)|(big.h1_slope_ok==True)))

print("\n-- RANK<=1 (POC/VAL/VAH) DESAGREGADO --")
show("rank<=1 todo",                      big.lvl_rank<=1)
show("rank<=1 + H1 alineado",            (big.lvl_rank<=1)&(big.h1_ok==True))
show("rank<=1 + H1 CONTRA",             (big.lvl_rank<=1)&(big.h1_ok==False))
show("rank>=2 + H1 alineado",           (big.lvl_rank>=2)&(big.h1_ok==True))
