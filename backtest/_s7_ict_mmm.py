"""
S7 — ICT Market Maker Model + OTE (Omar/MBB, "USA esta FÁCIL estrategia ICT")
=============================================================================
Modelo de las transcripciones, fiel:
  1. BIAS HTF (daily): bajista → solo shorts; alcista → solo longs.  (filtro direccional)
  2. NIVEL CLAVE: PDH (prev day high) para shorts / PDL para longs.
  3. MANIPULACIÓN: el precio barre el nivel (sweep) y cierra de vuelta (reclaim).
  4. DISPLACEMENT/CISD: vela de cuerpo fuerte que rompe el swing reciente a favor del bias.
     → la CAPA OF confirma: delta del lado correcto en la vela de displacement (volumen/intención).
  5. ENTRADA OTE: límite en el fib 0.62 del swing (manip-high → displacement-low). Stop 0.9 (swing).
     TP en el swing opuesto (0.0) + extensión. RR fijo ~2.2R.
Sesiones: London 07-10 + NY 12-15 UTC. M15. Causal, fee real, IS/OOS. Compara base vs base+OF.

Uso: python backtest/_s7_ict_mmm.py [--ote 0.62] [--stopfib 0.9]
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
M1 = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"
OOS_MS = int(pd.Timestamp("2026-03-01", tz="UTC").value // 1_000_000)
FEE_RT = 0.0011; CAP0, RISK = 500.0, 0.01


def ema(x, n):
    a = 2/(n+1); o = np.empty_like(x); o[0] = x[0]
    for i in range(1, len(x)): o[i] = a*x[i] + (1-a)*o[i-1]
    return o


def resample(df, ms):
    g = (df.ts_ms.values // ms) * ms
    b = pd.DataFrame({"g": g, "o": df.open.values, "h": df.high.values, "l": df.low.values,
                      "c": df.close.values, "v": df.volume.values, "d": df.delta.values})
    return b.groupby("g").agg(open=("o","first"), high=("h","max"), low=("l","min"), close=("c","last"),
                              volume=("v","sum"), delta=("d","sum")).reset_index().rename(columns={"g":"ts_ms"})


def atr(h,l,c,n=14):
    pc=np.roll(c,1); pc[0]=c[0]
    tr=np.maximum(h-l,np.maximum(np.abs(h-pc),np.abs(l-pc)))
    o=np.full(len(tr),np.nan); o[n-1]=np.nanmean(tr[:n]); k=1/n
    for i in range(n,len(tr)): o[i]=o[i-1]*(1-k)+tr[i]*k
    return o


def run(df, ote, stopfib, use_of, entry_mode="ote", of_mode="trend"):
    t = resample(df, 900_000)                    # M15
    ts = t.ts_ms.values.astype(np.int64)
    o,h,l,c = (t[x].values.astype(float) for x in ("open","high","low","close"))
    vol, delta = t.volume.values.astype(float), t.delta.values.astype(float)
    n = len(t); a = atr(h,l,c); S = pd.Series
    # daily bias (causal): cierre diario vs EMA20 de cierres diarios previos
    d1 = resample(df, 86_400_000); d1e = ema(d1.close.values, 20)
    dmap = {int(d1.ts_ms.values[i]): (d1.close.values[i], d1e[i]) for i in range(len(d1))}
    day0 = (ts // 86_400_000) * 86_400_000
    prevday = day0 - 86_400_000
    # PDH/PDL del día previo (causal)
    dd = pd.DataFrame({"day": day0, "h": h, "l": l})
    dh = dd.groupby("day")["h"].max(); dl = dd.groupby("day")["l"].min()
    pdh = np.array([dh.get(p, np.nan) for p in prevday])
    pdl = np.array([dl.get(p, np.nan) for p in prevday])
    bias = np.array([ (lambda v: 0 if v is None or not np.isfinite(v[1]) else (-1 if v[0] < v[1] else 1))(dmap.get(int(p))) for p in prevday])
    body = np.abs(c - o); rng = (h - l) + 1e-9
    disp = (body / rng > 0.50) & (body > 0.4 * a)            # displacement: cuerpo dominante
    swing_lo = S(l).rolling(6).min().shift(1).values         # swing reciente (1.5h)
    swing_hi = S(h).rolling(6).max().shift(1).values
    hm = (ts // 60_000) % 1440
    sess = ((hm >= 7*60) & (hm < 10*60)) | ((hm >= 12*60) & (hm < 15*60))

    trades = []; next_ok = 0; cap = CAP0; peak = CAP0; dd_ = 0.0; risk_amt = CAP0*RISK; cur_mo = -1
    look_fill = 10; timeout = 64; K = 12     # K = max barras entre sweep y displacement (3h)
    # estado de manipulación por lado (válido durante K barras tras el sweep)
    sweep_hi_age = 999; sweep_hi_px = np.nan      # short: sweep de PDH
    sweep_lo_age = 999; sweep_lo_px = np.nan      # long:  sweep de PDL
    cur_day = -1
    for i in range(50, n-1):
        if day0[i] != cur_day:                     # reset diario del estado de manipulación
            cur_day = day0[i]; sweep_hi_age = 999; sweep_lo_age = 999
        sweep_hi_age += 1; sweep_lo_age += 1
        # registrar sweeps (incluso fuera de sesión de entrada)
        if np.isfinite(pdh[i]) and h[i] > pdh[i]:
            if sweep_hi_age > K or h[i] > (sweep_hi_px if np.isfinite(sweep_hi_px) else -1):
                sweep_hi_px = h[i]
            sweep_hi_age = 0
        if np.isfinite(pdl[i]) and l[i] < pdl[i]:
            if sweep_lo_age > K or l[i] < (sweep_lo_px if np.isfinite(sweep_lo_px) else 1e18):
                sweep_lo_px = l[i]
            sweep_lo_age = 0
        if i < next_ok or not sess[i] or not np.isfinite(a[i]): continue
        mo = pd.Timestamp(ts[i], unit="ms").to_period("M").ordinal
        if mo != cur_mo: risk_amt = cap*RISK; cur_mo = mo
        took = False
        # SHORT: bias bajista + manipulación reciente (sweep PDH) + displacement bajista que rompe swing low
        if bias[i] == -1 and 0 < sweep_hi_age <= K and np.isfinite(sweep_hi_px):
            cisd = disp[i] and (c[i] < o[i]) and ((c[i] < swing_lo[i]) or (c[i] < pdh[i]))
            of_ok = (delta[i] < 0) if of_mode == "trend" else (delta[i] > 0)  # cripto: compra absorbida en el high
            if cisd and (not use_of or of_ok):
                SH = sweep_hi_px; SL = l[i]
                if SH - SL > 0.3*a[i]:
                    if entry_mode == "market":
                        entry = c[i]; stop = SH; tp = SL - (SH-SL)*0.0
                        if stop-entry > 0:
                            took = sim_and_record(trades,"short",entry,stop,tp,h,l,c,i,0,timeout,n,ts)
                    else:
                        entry = SL + ote*(SH-SL); stop = SL + stopfib*(SH-SL); tp = SL
                        if stop-entry > 0:
                            took = sim_and_record(trades,"short",entry,stop,tp,h,l,c,i,look_fill,timeout,n,ts)
                    if took: sweep_hi_age = 999
        # LONG espejo
        if not took and bias[i] == 1 and 0 < sweep_lo_age <= K and np.isfinite(sweep_lo_px):
            cisd = disp[i] and (c[i] > o[i]) and ((c[i] > swing_hi[i]) or (c[i] > pdl[i]))
            of_ok = (delta[i] > 0) if of_mode == "trend" else (delta[i] < 0)  # cripto: venta absorbida en el low
            if cisd and (not use_of or of_ok):
                SL = sweep_lo_px; SH = h[i]
                if SH - SL > 0.3*a[i]:
                    if entry_mode == "market":
                        entry = c[i]; stop = SL; tp = SH
                        if entry-stop > 0:
                            took = sim_and_record(trades,"long",entry,stop,tp,h,l,c,i,0,timeout,n,ts)
                    else:
                        entry = SH - ote*(SH-SL); stop = SH - stopfib*(SH-SL); tp = SH
                        if entry-stop > 0:
                            took = sim_and_record(trades,"long",entry,stop,tp,h,l,c,i,look_fill,timeout,n,ts)
                    if took: sweep_lo_age = 999
        if took:
            last = trades[-1]; cap += risk_amt*last["r"]; peak = max(peak,cap); dd_ = max(dd_,(peak-cap)/peak)
            next_ok = i + 4
    return trades, cap, dd_


def sim_and_record(trades, side, entry, stop, tp, h, l, c, i, look_fill, timeout, n, ts):
    if look_fill == 0:
        fill = i                                  # entrada a mercado en el cierre de la barra
    else:
        fill = None                               # esperar fill del límite OTE
        for j in range(i+1, min(i+1+look_fill, n)):
            if side == "short" and h[j] >= entry: fill = j; break
            if side == "long"  and l[j] <= entry: fill = j; break
        if fill is None: return False
    risk_px = abs(entry-stop); rr = abs(tp-entry)/risk_px
    res = None
    for j in range(fill+1, min(fill+1+timeout, n)):
        if side == "short":
            if h[j] >= stop: res = -1.0; break
            if l[j] <= tp:   res = rr;  break
        else:
            if l[j] <= stop: res = -1.0; break
            if h[j] >= tp:   res = rr;  break
    if res is None:
        px = c[min(fill+timeout, n-1)]; res = ((px-entry) if side=="long" else (entry-px))/risk_px
    r = res - FEE_RT*entry/risk_px
    trades.append({"ts": int(ts[i]), "side": side, "r": r, "rr": rr,
                   "oos": int(ts[i]) >= OOS_MS, "win": res > 0})
    return True


def report(name, trades, span):
    if not trades: print(f"--- {name} ---\n   sin trades\n"); return
    d = pd.DataFrame(trades); di, do = d[~d.oos], d[d.oos]
    def blk(x, tag, fr):
        if len(x)==0: return f"{tag} n=0"
        return f"{tag} n={len(x):>4} WR={100*x.win.mean():>4.1f}% avgR={x.r.mean():>+.3f} netR={x.r.sum():>+6.1f} {len(x)/(span*fr):>4.2f}tr/d Sharpe~{x.r.mean()/(x.r.std()+1e-9):>+.2f}"
    print(f"--- {name} ---"); print("   "+blk(di,"IS ",0.66)); print("   "+blk(do,"OOS",0.34))


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--ote",type=float,default=0.62); ap.add_argument("--stopfib",type=float,default=0.9)
    args=ap.parse_args()
    df=pd.read_parquet(M1,columns=["ts_ms","open","high","low","close","volume","delta"]).sort_values("ts_ms")
    df=df[df.ts_ms>=pd.Timestamp("2025-06-19",tz="UTC").value//10**6].reset_index(drop=True)
    span=(df.ts_ms.max()-df.ts_ms.min())/86_400_000
    print(f"S7 ICT Market Maker Model + OTE | M15 | OTE {args.ote} stop {args.stopfib} | fee {FEE_RT*1e4:.0f}bps | span {span:.0f}d\n")
    for em in ("market", "ote"):
        print(f"===== entrada: {em} =====")
        tr,cap,dd=run(df,args.ote,args.stopfib,False,entry_mode=em)
        report("OF=no (solo estructura)",tr,span); print(f"   $500 -> ${cap:,.0f} | MaxDD {dd*100:.0f}%")
        tr,cap,dd=run(df,args.ote,args.stopfib,True,entry_mode=em,of_mode="trend")
        report("OF=sí trend (delta a favor del bias, estilo forex)",tr,span); print(f"   $500 -> ${cap:,.0f} | MaxDD {dd*100:.0f}%")
        tr,cap,dd=run(df,args.ote,args.stopfib,True,entry_mode=em,of_mode="absorb")
        report("OF=sí absorb (delta EN CONTRA, estilo cripto)",tr,span); print(f"   $500 -> ${cap:,.0f} | MaxDD {dd*100:.0f}%\n")


if __name__=="__main__":
    main()
