"""
_freq_test.py — BTC: prueba sistematica de todas las palancas de frecuencia
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _listas import OOS_MS, FEE_MAKER, FEE_TAKER
from _listas2 import struct_target

ROOT   = Path(__file__).parent.parent
MK,TK  = FEE_MAKER/2, FEE_TAKER/2
BAR_MS = 15*60_000

# ── Generadores parametrizables ─────────────────────────────────────────────

def gen_h21_param(K=15, touches=2, tol=0.002):
    def g(a, i):
        if i < K: return
        win = a.fp_poc[i-K:i]; win = win[np.isfinite(win)]
        if len(win) < 3: return
        lvl = np.median(win)
        n_touch = np.sum(np.abs(a.l[i-K:i] - lvl)/lvl <= tol)
        if n_touch >= touches and a.c[i] > a.c[i-1] and abs(a.l[i]-lvl)/lvl <= tol:
            stop = lvl - 0.6*a.atr[i]
            tp1, tp2 = struct_target(a, i, "long", lvl)
            if np.isfinite(tp2): return [("long", lvl, stop, tp1, tp2, "H21")]
    return g

def gen_h21s_param(K=15, touches=2, tol=0.002):
    def g(a, i):
        if i < K: return
        win = a.fp_poc[i-K:i]; win = win[np.isfinite(win)]
        if len(win) < 3: return
        lvl = np.median(win)
        n_touch = np.sum(np.abs(a.h[i-K:i] - lvl)/lvl <= tol)
        if n_touch >= touches and a.c[i] < a.c[i-1] and abs(a.h[i]-lvl)/lvl <= tol:
            stop = lvl + 0.6*a.atr[i]
            tp1, tp2 = struct_target(a, i, "short", lvl)
            if np.isfinite(tp2): return [("short", lvl, stop, tp1, tp2, "H21s")]
    return g

def gen_h5():
    return L2.gen_h5()

def gen_pdh_pdl_confirmed(K_bars=192, touches=2, tol=0.003):
    """PDH/PDL con confirmacion: nivel testado >= touches veces en ventana K_bars M15."""
    def g(a, i):
        if i < K_bars: return
        out = []
        for side, lvl_col, extremo, lbl in [
            ("long",  "prev_day_low",  "l", "PDL"),
            ("short", "prev_day_high", "h", "PDH"),
        ]:
            lvl = getattr(a, lvl_col)[i]
            if not np.isfinite(lvl) or lvl <= 0: continue
            tol_abs = lvl * tol
            # contar tests previos en la ventana
            if side == "long":
                n_t = np.sum(a.l[i-K_bars:i] <= lvl + tol_abs)
                touched = a.l[i] <= lvl + tol_abs
                confirm = a.c[i] > a.c[i-1] and lvl < a.c[i-1]
            else:
                n_t = np.sum(a.h[i-K_bars:i] >= lvl - tol_abs)
                touched = a.h[i] >= lvl - tol_abs
                confirm = a.c[i] < a.c[i-1] and lvl > a.c[i-1]
            if n_t >= touches and touched and confirm:
                if side == "long":
                    stop = lvl - 0.6*a.atr[i]
                    tp1, tp2 = struct_target(a, i, "long", lvl)
                    if np.isfinite(tp2): out.append(("long", lvl, stop, tp1, tp2, f"PDL{K_bars}"))
                else:
                    stop = lvl + 0.6*a.atr[i]
                    tp1, tp2 = struct_target(a, i, "short", lvl)
                    if np.isfinite(tp2): out.append(("short", lvl, stop, tp1, tp2, f"PDH{K_bars}"))
        return out if out else None
    return g

def gen_vah_val_confirmed(K_bars=96, touches=2, tol=0.003):
    """VAH/VAL con confirmacion: testado >= touches veces en ventana."""
    def g(a, i):
        if i < K_bars: return
        out = []
        for side, lvl_col, lbl in [
            ("long",  "vp_val", "VAL"),
            ("short", "vp_vah", "VAH"),
        ]:
            lvl = getattr(a, lvl_col)[i]
            if not np.isfinite(lvl) or lvl <= 0: continue
            tol_abs = lvl * tol
            if side == "long":
                n_t = np.sum(a.l[i-K_bars:i] <= lvl + tol_abs)
                touched = a.l[i] <= lvl + tol_abs
                confirm = a.c[i] > a.c[i-1] and lvl < a.c[i-1]
            else:
                n_t = np.sum(a.h[i-K_bars:i] >= lvl - tol_abs)
                touched = a.h[i] >= lvl - tol_abs
                confirm = a.c[i] < a.c[i-1] and lvl > a.c[i-1]
            if n_t >= touches and touched and confirm:
                if side == "long":
                    stop = lvl - 0.6*a.atr[i]
                    tp1, tp2 = struct_target(a, i, "long", lvl)
                    if np.isfinite(tp2): out.append(("long", lvl, stop, tp1, tp2, f"VAL{K_bars}"))
                else:
                    stop = lvl + 0.6*a.atr[i]
                    tp1, tp2 = struct_target(a, i, "short", lvl)
                    if np.isfinite(tp2): out.append(("short", lvl, stop, tp1, tp2, f"VAH{K_bars}"))
        return out if out else None
    return g

# ── Motor ────────────────────────────────────────────────────────────────────

def run(a, gens, m1, timeout_min=24*60, margin=2.0,
        trail_atr=4.0, cooldown=6, max_day=2):
    m1ts,m1h,m1l,m1c = m1
    atr_med = pd.Series(a.atr).rolling(500,min_periods=50).median().shift(1).values
    trades = []
    for g in gens:
        cool=0; dcount={}
        for i in range(60, a.n-1):
            if i<cool or a.atr[i]<=0: continue
            if not (np.isfinite(atr_med[i]) and a.atr[i]>atr_med[i]): continue
            d=int(a.day[i])
            if dcount.get(d,0)>=max_day: continue
            for side,lvl,stop,tp1,tp2,kind in (g(a,i) or []):
                if not np.isfinite([lvl,stop,tp2]).all(): continue
                ref=a.c[i-1]
                if side=="long" and not (lvl<ref): continue
                if side=="short" and not (lvl>ref): continue
                mf=margin/1e4
                if side=="long" and not (a.l[i]<=lvl*(1-mf)): continue
                if side=="short" and not (a.h[i]>=lvl*(1+mf)): continue
                entry=lvl; atr0=a.atr[i]
                sf=0.15/100*entry
                if abs(entry-stop)<sf: stop=entry-sf if side=="long" else entry+sf
                risk=abs(entry-stop)
                if risk<=0 or abs(tp2-entry)/risk<1.2: continue
                if tp1 and 100*abs(tp1-entry)/entry<0.5: continue
                chop=str(a.reg[i]).lower() in ("chop","range","balance","consolidation")
                j0=np.searchsorted(m1ts,a.ts[i]+BAR_MS)
                jend=np.searchsorted(m1ts,a.ts[i]+BAR_MS+timeout_min*60_000)
                res=None
                if chop:
                    cur=stop; realized=0.0; rem=1.0; f1=False
                    p1=0.5 if tp1 else 0.0; reason="timeout"
                    for j in range(j0,min(jend,len(m1ts))):
                        if side=="long":
                            if m1l[j]<=cur: realized+=rem*((cur-entry)/risk); reason="be" if f1 else "stop"; break
                            if not f1 and tp1 and m1h[j]>=tp1: realized+=p1*((tp1-entry)/risk); rem-=p1; f1=True; cur=entry
                            if m1h[j]>=tp2: realized+=rem*((tp2-entry)/risk); reason="target"; break
                        else:
                            if m1h[j]>=cur: realized+=rem*((entry-cur)/risk); reason="be" if f1 else "stop"; break
                            if not f1 and tp1 and m1l[j]<=tp1: realized+=p1*((entry-tp1)/risk); rem-=p1; f1=True; cur=entry
                            if m1l[j]<=tp2: realized+=rem*((entry-tp2)/risk); reason="target"; break
                    else:
                        jj=min(jend,len(m1ts))-1
                        if jj<=j0: continue
                        px=m1c[jj]; realized+=rem*(((px-entry) if side=="long" else (entry-px))/risk)
                    exit_s=MK if reason=="target" else TK
                    fee_r=(MK+(MK*p1 if f1 else 0)+exit_s*rem)*entry/risk
                    res=realized-fee_r
                else:
                    fee_r=(MK+TK)*entry/risk; best=entry; trail=stop
                    for j in range(j0,min(jend,len(m1ts))):
                        if side=="long":
                            best=max(best,m1h[j]); trail=max(trail,best-trail_atr*atr0)
                            if m1l[j]<=trail: res=(trail-entry)/risk-fee_r; break
                        else:
                            best=min(best,m1l[j]); trail=min(trail,best+trail_atr*atr0)
                            if m1h[j]>=trail: res=(entry-trail)/risk-fee_r; break
                    if res is None:
                        jj=min(jend,len(m1ts))-1
                        if jj<=j0: continue
                        px=m1c[jj]; res=((px-entry) if side=="long" else (entry-px))/risk-fee_r
                trades.append(dict(bar_ts=int(a.ts[i]),side=side,r=res,
                                   oos=int(a.ts[i])>=OOS_MS,kind=kind,
                                   regime="chop" if chop else "trend"))
                cool=i+cooldown; dcount[d]=dcount.get(d,0)+1; break
    return pd.DataFrame(trades)

def rep(df, label, oos_days, is_days):
    oos=df[df.oos]; is_=df[~df.oos]
    if len(oos)<3:
        print(f"  {label:<42} |  {len(oos):>3}  |  --- |  ---  |  ---  |  ---")
        return
    td=len(oos)/max(oos_days,1)
    dd=(oos.r.cumsum()-oos.r.cumsum().cummax()).min()
    sh=oos.r.mean()/(oos.r.std()+1e-9)*np.sqrt(252)
    wr=100*(oos.r>0).mean()
    # IS check
    iavg=is_.r.mean() if len(is_)>0 else float('nan')
    # expected R / day
    expR=td*oos.r.mean()
    ok = "OK" if oos.r.mean()>0.8 and iavg>0.5 and dd>-15 else "  "
    print(f"  {label:<42} | {len(oos):>4} | {td:>4.2f} | {iavg:>+6.3f} | {oos.r.mean():>+6.3f} | {wr:>5.1f}% | {dd:>+7.2f}R | {expR:>+6.3f} {ok}")

# ── Main ─────────────────────────────────────────────────────────────────────

print("Cargando BTC...", flush=True)
L2.M1 = ROOT/"data/bybit-perp/processed/btcusdt_perp_m1.parquet"
t=L2.load2(15,start_ms=0); a=L2.A2(t); m1=L2.load_m1_exit(start_ms=0)
oos_days=107; is_days=417

hdr = f"  {'Configuracion':<42} | {'n_oos':>4} | {'t/d':>4} | {'IS_avg':>6} | {'OOS_avg':>6} | {'WR':>5} | {'DD':>7} | {'expR/d':>6}"
sep = "  "+"-"*100

# ══════════════════════════════════════════════════════════════════════
print(f"\n{'='*100}")
print("  BLOQUE 1: PARAMETROS H21 — ventana K y touches requeridos")
print('='*100)
print(hdr); print(sep)

base_gens = [gen_h5(), gen_h21_param(K=15,touches=2), gen_h21s_param(K=15,touches=2)]
df=run(a,base_gens,m1,max_day=2); rep(df,"BASELINE  K=15 tch=2 max_day=2",oos_days,is_days)

for K in [24, 32, 48, 64]:
    for tch in [1, 2]:
        gens=[gen_h5(), gen_h21_param(K=K,touches=tch), gen_h21s_param(K=K,touches=tch)]
        df=run(a,gens,m1,max_day=2)
        rep(df,f"H21 K={K:<3} tch={tch} max_day=2",oos_days,is_days)

# ══════════════════════════════════════════════════════════════════════
print(f"\n{'='*100}")
print("  BLOQUE 2: MAX_DAY y COOLDOWN — mas oportunidades por dia")
print('='*100)
print(hdr); print(sep)

for md in [2, 3, 4]:
    for cd in [6, 4, 3]:
        gens=[gen_h5(), gen_h21_param(K=15,touches=2), gen_h21s_param(K=15,touches=2)]
        df=run(a,gens,m1,max_day=md,cooldown=cd)
        rep(df,f"H21 K=15 tch=2 max_day={md} cool={cd}",oos_days,is_days)

# ══════════════════════════════════════════════════════════════════════
print(f"\n{'='*100}")
print("  BLOQUE 3: NUEVOS NIVELES CON CONFIRMACION")
print('='*100)
print(hdr); print(sep)

base=[gen_h5(), gen_h21_param(K=15,touches=2), gen_h21s_param(K=15,touches=2)]
df=run(a,base,m1,max_day=2); rep(df,"BASELINE sin extras",oos_days,is_days)

for K_pdl in [96,192]:
    for tch in [1,2]:
        gens=base+[gen_pdh_pdl_confirmed(K_bars=K_pdl,touches=tch)]
        df=run(a,gens,m1,max_day=3)
        rep(df,f"+ PDH/PDL K={K_pdl} tch={tch}",oos_days,is_days)

for K_vv in [48,96]:
    for tch in [1,2]:
        gens=base+[gen_vah_val_confirmed(K_bars=K_vv,touches=tch)]
        df=run(a,gens,m1,max_day=3)
        rep(df,f"+ VAH/VAL K={K_vv} tch={tch}",oos_days,is_days)

# ══════════════════════════════════════════════════════════════════════
print(f"\n{'='*100}")
print("  BLOQUE 4: COMBINACIONES PROMETEDORAS")
print('='*100)
print(hdr); print(sep)

combos = [
    ("K=32 tch=1 + max_day=3",
     [gen_h5(), gen_h21_param(K=32,touches=1), gen_h21s_param(K=32,touches=1)], 3, 6),
    ("K=32 tch=1 + max_day=3 + cool=4",
     [gen_h5(), gen_h21_param(K=32,touches=1), gen_h21s_param(K=32,touches=1)], 3, 4),
    ("K=48 tch=1 + max_day=3",
     [gen_h5(), gen_h21_param(K=48,touches=1), gen_h21s_param(K=48,touches=1)], 3, 6),
    ("K=32 tch=1 + PDL96_1 + max_day=3",
     [gen_h5(), gen_h21_param(K=32,touches=1), gen_h21s_param(K=32,touches=1),
      gen_pdh_pdl_confirmed(96,1)], 3, 4),
    ("K=32 tch=1 + VAL48_1 + max_day=3",
     [gen_h5(), gen_h21_param(K=32,touches=1), gen_h21s_param(K=32,touches=1),
      gen_vah_val_confirmed(48,1)], 3, 4),
    ("K=32 tch=1 + PDL96_1 + VAL48_1 + max_day=3",
     [gen_h5(), gen_h21_param(K=32,touches=1), gen_h21s_param(K=32,touches=1),
      gen_pdh_pdl_confirmed(96,1), gen_vah_val_confirmed(48,1)], 3, 4),
    ("K=48 tch=1 + PDL192_2 + max_day=3",
     [gen_h5(), gen_h21_param(K=48,touches=1), gen_h21s_param(K=48,touches=1),
      gen_pdh_pdl_confirmed(192,2)], 3, 5),
]

best_df = None; best_label = ""
for label, gens, md, cd in combos:
    df=run(a,gens,m1,max_day=md,cooldown=cd)
    rep(df,label,oos_days,is_days)
    oos=df[df.oos]
    if len(oos)>10 and oos.r.mean()>1.2:
        if best_df is None or len(oos)/max(oos_days,1)*oos.r.mean() > len(best_df[best_df.oos])/max(oos_days,1)*best_df[best_df.oos].r.mean():
            best_df=df; best_label=label

# ══════════════════════════════════════════════════════════════════════
if best_df is not None:
    oos=best_df[best_df.oos]
    print(f"\n{'='*100}")
    print(f"  MEJOR COMBINACION: {best_label}")
    print(f"{'='*100}")
    print(f"  OOS: n={len(oos)}  t/d={len(oos)/oos_days:.2f}  avgR={oos.r.mean():+.3f}  WR={100*(oos.r>0).mean():.1f}%")
    dd=(oos.r.cumsum()-oos.r.cumsum().cummax()).min()
    print(f"  DD={dd:+.2f}R  Sharpe={oos.r.mean()/(oos.r.std()+1e-9)*np.sqrt(252):.2f}")
    print(f"  expR/dia = {len(oos)/oos_days*oos.r.mean():+.3f}R")
    print(f"\n  Por generador (OOS):")
    print(f"  {'Kind':>12} | {'n':>5} | {'avgR':>8} | {'WR':>7}")
    for k,g in oos.groupby("kind"):
        print(f"  {k:>12} | {len(g):>5} | {g.r.mean():>+8.3f} | {100*(g.r>0).mean():>6.1f}%")
    best_df.to_csv(ROOT/"backtest/best_freq_btc.csv",index=False)
    print(f"\n  Guardado: best_freq_btc.csv")
