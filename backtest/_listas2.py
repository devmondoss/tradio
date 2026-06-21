"""
_listas2.py — Cierre de pendientes del catálogo: H2,H5,H6,H9,H11,H13,H16,H17,H18,H21
=====================================================================================
Reutiliza el motor de _listas.py. CLAVE: el hallazgo de H1 fue que el modo de entrada
LÍMITE-MAKER (entrada en el nivel + fee 4bps) cambia el veredicto vs taker-market. Por eso
cada hipótesis se corre en AMBOS modos. Runner genérico: cada H es un generador de candidatos
(side, level, stop, tp1, tp2, tag) por barra; el runner simula taker y maker-límite.

Disciplina idéntica: causal, IS<2026-03-01/OOS, walk-forward, fechas únicas, $500@1%.
Uso:  python backtest/_listas2.py [h2|h5|h6|h9|h11|h13|h16|h18|h21|all] [--tf 5]
"""
import argparse
from pathlib import Path
import numpy as np, pandas as pd
from _listas import (sim, OOS_MS, TICK_MS, FEE_TAKER, FEE_MAKER, CAP0, RISK, atr)

ROOT = Path(__file__).parent.parent
M1 = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"

def load2(tf_min=5, start_ms=TICK_MS):
    cols = ["ts_ms","open","high","low","close","volume","delta","cvd","cvd_div","cvd_slope",
            "dz","vr","regime","vp_poc","vp_vah","vp_val","vp_lvn_below","prev_day_high","prev_day_low",
            "big_trade_bullish","big_trade_bearish","max_trade","abs_bid","abs_ask","buy_vol","sell_vol",
            "swing_high_50","swing_low_50","equal_high","equal_low","fp_poc","fp_absorb_buy","fp_absorb_sell",
            "h1_choch_bear","h1_choch_bull","h1_bos_bear","h1_bos_bull","displacement_bear",
            "bearish_fvg_active","near_bearish_ob","ote_62","fib_ote","h4_bearish","h4_bos_bear",
            "weekly_high","weekly_low","asian_high","asian_low",
            "spread_mean","obi5_mean","obi10_mean","ask_wall","bid_wall","thin_above","thin_below","vpin"]
    df = pd.read_parquet(M1, columns=cols).sort_values("ts_ms").reset_index(drop=True)
    df = df[df.ts_ms >= start_ms].reset_index(drop=True)
    if tf_min!=1:
        g=(df.ts_ms//(tf_min*60_000))*(tf_min*60_000)
        num_last=["close","vp_poc","vp_vah","vp_val","vp_lvn_below","prev_day_high","prev_day_low",
                  "cvd","cvd_slope","regime","swing_high_50","swing_low_50","equal_high","equal_low",
                  "fp_poc","weekly_high","weekly_low","asian_high","asian_low","h4_bearish","h4_bos_bear",
                  "spread_mean","obi5_mean","obi10_mean","vpin"]
        flags=["big_trade_bullish","big_trade_bearish","abs_bid","abs_ask","cvd_div","fp_absorb_buy",
               "fp_absorb_sell","h1_choch_bear","h1_choch_bull","h1_bos_bear","h1_bos_bull",
               "displacement_bear","bearish_fvg_active","near_bearish_ob","ote_62","fib_ote",
               "ask_wall","bid_wall","thin_above","thin_below"]
        a={"ts_ms":("ts_ms","first"),"open":("open","first"),"high":("high","max"),
           "low":("low","min"),"volume":("volume","sum"),"delta":("delta","sum"),
           "buy_vol":("buy_vol","sum"),"sell_vol":("sell_vol","sum"),"max_trade":("max_trade","max")}
        for c in num_last: a[c]=(c,"last")
        for c in flags: a[c]=(c,"max")
        df=df.groupby(g).agg(**a).reset_index(drop=True)
    df["atr"]=atr(df.high.values,df.low.values,df.close.values)
    df["dz"]=(df.delta-df.delta.rolling(50).mean().shift(1))/(df.delta.rolling(50).std().shift(1)+1e-9)
    df["vr"]=df.volume/(df.volume.rolling(50).mean().shift(1)+1e-9)
    return df

def load_m1_exit(start_ms=TICK_MS):
    """OHLC M1 crudo para simular SALIDAS con granularidad fina (evita ambigüedad intrabar
    de evaluar stop/target en velas grandes de M15/H1)."""
    df=pd.read_parquet(M1, columns=["ts_ms","high","low","close"]).sort_values("ts_ms")
    df=df[df.ts_ms>=start_ms].reset_index(drop=True)
    return (df.ts_ms.values.astype(np.int64), df.high.values.astype(float),
            df.low.values.astype(float), df.close.values.astype(float))

def run_level_m1exit(a, gen, m1, timeout_min, mode, tf_min, cooldown=6, max_day=2,
                     min_rr=1.2, fill_margin_bps=2.0, filt=None, rr_floor=None, rr_cap=None):
    """Entrada decidida en el TF de 'a'; SALIDA simulada barra-a-barra en M1 (honesto).
    rr_floor/rr_cap: recorta el target estructural a un rango de RR (híbrido)."""
    m1ts,m1h,m1l,m1c=m1; maker=(mode=="maker"); fee=FEE_MAKER if maker else FEE_TAKER
    trades=[]; cool=0; dcount={}; bar_ms=tf_min*60_000
    for i in range(60, a.n-1):
        if i<cool or a.atr[i]<=0: continue
        d=int(a.day[i])
        if dcount.get(d,0)>=max_day: continue
        for side,lvl,stop,tp1,tp2,tag in (gen(a,i) or []):
            if not np.isfinite([lvl,stop,tp2]).all(): continue
            if filt is not None and not filt(a,i,side): continue
            if maker:
                ref=a.c[i-1]
                if side=="long" and not (lvl<ref): continue
                if side=="short" and not (lvl>ref): continue
                if side=="long" and not (a.l[i] <= lvl - fill_margin_bps/1e4*lvl): continue
                if side=="short" and not (a.h[i] >= lvl + fill_margin_bps/1e4*lvl): continue
                entry=lvl
            else:
                entry=a.c[i]
            risk=abs(entry-stop)
            if risk<=0: continue
            # híbrido: recorta el target estructural a [rr_floor, rr_cap]
            if rr_floor is not None or rr_cap is not None:
                rr=abs(tp2-entry)/risk
                if rr_floor is not None: rr=max(rr,rr_floor)
                if rr_cap   is not None: rr=min(rr,rr_cap)
                tp2 = entry+rr*risk if side=="long" else entry-rr*risk
                if tp1 is not None and not (min(entry,tp2) < tp1 < max(entry,tp2)): tp1=None
            if side=="long" and not (stop<entry<tp2): continue
            if side=="short" and not (tp2<entry<stop): continue
            if abs(tp2-entry)/risk < min_rr: continue
            # --- SALIDA en M1 desde el cierre de la barra de entrada ---
            j0=np.searchsorted(m1ts, a.ts[i]+bar_ms)
            jend=np.searchsorted(m1ts, a.ts[i]+bar_ms+timeout_min*60_000)
            fee_r=fee*entry/risk; cur_stop=stop; realized=0.0; rem=1.0; filled1=False
            p1=0.5 if tp1 else 0.0; res=None
            for j in range(j0, min(jend,len(m1ts))):
                if side=="long":
                    if m1l[j]<=cur_stop: realized+=rem*((cur_stop-entry)/risk); res=realized-fee_r; break
                    if (not filled1) and tp1 and m1h[j]>=tp1: realized+=p1*((tp1-entry)/risk); rem-=p1; filled1=True; cur_stop=entry
                    if m1h[j]>=tp2: realized+=rem*((tp2-entry)/risk); res=realized-fee_r; break
                else:
                    if m1h[j]>=cur_stop: realized+=rem*((entry-cur_stop)/risk); res=realized-fee_r; break
                    if (not filled1) and tp1 and m1l[j]<=tp1: realized+=p1*((entry-tp1)/risk); rem-=p1; filled1=True; cur_stop=entry
                    if m1l[j]<=tp2: realized+=rem*((entry-tp2)/risk); res=realized-fee_r; break
            if res is None:
                jj=min(jend,len(m1ts))-1
                if jj<=j0: continue
                px=m1c[jj]; realized+=rem*(((px-entry) if side=="long" else (entry-px))/risk); res=realized-fee_r
            trades.append({"ts":int(a.ts[i]),"side":side,"r":res,"oos":int(a.ts[i])>=OOS_MS,"tag":tag})
            cool=i+cooldown; dcount[d]=dcount.get(d,0)+1; break
    return trades

class A2:
    def __init__(s,t):
        for c in t.columns: setattr(s,c,t[c].values)
        s.ts=t.ts_ms.values.astype(np.int64); s.o=t.open.values.astype(float)
        s.h=t.high.values.astype(float); s.l=t.low.values.astype(float); s.c=t.close.values.astype(float)
        s.n=len(s.ts); s.day=(s.ts//86_400_000)
        s.reg=t.regime.astype(str).values
        for b in ["big_trade_bullish","big_trade_bearish","abs_bid","abs_ask","cvd_div","h1_choch_bear",
                  "h1_choch_bull","h1_bos_bear","h1_bos_bull","displacement_bear","bearish_fvg_active",
                  "near_bearish_ob","ote_62","fib_ote","fp_absorb_buy","fp_absorb_sell","h4_bearish","h4_bos_bear",
                  "ask_wall","bid_wall","thin_above","thin_below"]:
            setattr(s,b, np.nan_to_num(getattr(s,b).astype(float)).astype(bool))

# ---------------------------------------------------------------- runner genérico (taker + maker)
def run_level(a, gen, timeout, mode, cooldown=3, max_day=99, min_rr=1.2,
              tol_atr=0.15, fill_margin_bps=0.0, exit_tape=False, fill_start="next", filt=None):
    maker = (mode=="maker"); fee = FEE_MAKER if maker else FEE_TAKER
    trades=[]; cool=0; dcount={}
    for i in range(60, a.n-1):
        if i<cool or a.atr[i]<=0: continue
        d=int(a.day[i])
        if dcount.get(d,0)>=max_day: continue
        for cand in (gen(a,i) or []):
            side,lvl,stop,tp1,tp2,tag = cand
            if not np.isfinite([lvl,stop,tp2]).all(): continue
            if filt is not None and not filt(a,i,side): continue
            # fill / trigger
            if maker:
                ref=a.c[i-1]                          # precio al COLOCAR el límite (barra previa)
                # un límite maker debe reposar del lado correcto del mercado
                if side=="long"  and not (lvl < ref): continue      # buy-limit por debajo
                if side=="short" and not (lvl > ref): continue      # sell-limit por encima
                if side=="long"  and not (a.l[i] <= lvl - fill_margin_bps/1e4*lvl): continue
                if side=="short" and not (a.h[i] >= lvl + fill_margin_bps/1e4*lvl): continue
                entry=lvl; start=(i if fill_start=="same" else None)
            else:
                tol=tol_atr*a.atr[i]
                if side=="long":
                    if not (a.l[i]<=lvl+tol and a.c[i]>(a.h[i]+a.l[i])/2): continue
                else:
                    if not (a.h[i]>=lvl-tol and a.c[i]<(a.h[i]+a.l[i])/2): continue
                entry=a.c[i]; start=None
            if side=="long" and not (stop<entry<tp2): continue
            if side=="short" and not (tp2<entry<stop): continue
            if abs(tp2-entry)/max(abs(entry-stop),1e-9) < min_rr: continue
            r = sim_exit(a,i,side,entry,stop,tp1,tp2,timeout,fee,start,exit_tape)
            if r is None: continue
            trades.append({"ts":int(a.ts[i]),"side":side,"r":r,"oos":int(a.ts[i])>=OOS_MS,"tag":tag})
            cool=i+cooldown; dcount[d]=dcount.get(d,0)+1
            break
    return trades

def sim_exit(a,i,side,entry,stop,tp1,tp2,timeout,fee,start,exit_tape):
    if not exit_tape:
        return sim(i,side,entry,stop,tp1,tp2,a.h,a.l,a.c,a.n,timeout,fee,start=start)
    # H17: salida dinámica por debilidad del tape (ratio agresión a favor < 0.45)
    risk=abs(entry-stop);
    if risk<=0: return None
    fee_r=fee*entry/risk; j0=(i+1) if start is None else start
    for j in range(j0, min(i+1+timeout,a.n)):
        if side=="long" and a.l[j]<=stop: return (stop-entry)/risk - fee_r
        if side=="short" and a.h[j]>=stop: return (entry-stop)/risk - fee_r
        if side=="long" and a.h[j]>=tp2: return (tp2-entry)/risk - fee_r
        if side=="short" and a.l[j]<=tp2: return (entry-tp2)/risk - fee_r
        w=slice(max(0,j-5),j+1); bv=a.buy_vol[w].sum(); sv=a.sell_vol[w].sum(); tot=bv+sv
        if tot>0:
            ratio = bv/tot if side=="long" else sv/tot
            if ratio<0.45:
                px=a.c[j]; return ((px-entry) if side=="long" else (entry-px))/risk - fee_r
    px=a.c[min(i+timeout,a.n-1)]; return ((px-entry) if side=="long" else (entry-px))/risk - fee_r

# ---------------------------------------------------------------- reporte
def report(name, trades, span):
    if not trades: print(f"--- {name} ---  SIN TRADES\n"); return
    df=pd.DataFrame(trades); df["day"]=df.ts//86_400_000
    def blk(d,tag,fr):
        if len(d)==0: return f"{tag} n=0"
        shp=d.r.mean()/(d.r.std()+1e-9)*np.sqrt(len(d)/max(span*fr/365*252,1e-9)) if len(d)>1 else 0
        return (f"{tag} n={len(d):>4} días={d.day.nunique():>3} WR={100*(d.r>0).mean():>4.1f}% "
                f"avgR={d.r.mean():>+.3f} netR={d.r.sum():>+6.1f} Sh={shp:>+4.1f}")
    di,do=df[~df.oos],df[df.oos]
    print(f"--- {name} ---"); print(f"   {blk(di,'IS ',0.66)}"); print(f"   {blk(do,'OOS',0.34)}")
    if len(di)>=8:
        fo=np.array_split(di.sort_values('ts'),4)
        print("   WF-IS: "+" ".join(f"{f.r.mean():+.2f}" for f in fo))
    print()

# ================================================================ GENERADORES (hipótesis)
def gen_h2(W=20):   # Delta Range Reversal: fade extremos de rango intradía (rolling)
    def g(a,i):
        rh=np.nanmax(a.h[i-W:i]); rl=np.nanmin(a.l[i-W:i]); mid=(rh+rl)/2
        if not np.isfinite([rh,rl]).all() or (rh-rl) < 0.8*a.atr[i] or (rh-rl) > 3.5*a.atr[i]: return
        # absorción: DZ contra el extremo
        out=[]
        if a.dz[i] <= -1.0:   # venta agresiva en el low -> fade long
            out.append(("long", rl, rl-0.5*a.atr[i], mid, rh, "H2"))
        if a.dz[i] >= 1.0:
            out.append(("short", rh, rh+0.5*a.atr[i], mid, rl, "H2"))
        return out
    return g

def struct_target(a, i, side, entry):
    """Target ESTRUCTURAL = captura la ROTACIÓN GRANDE de liquidez (no el rebote chico):
    tp2 = nivel de liquidez MÁS LEJANO (VAH/VAL · swing · día previo · SEMANAL).
    tp1 = nivel más cercano (parcial 50% ahí → breakeven → el resto corre al lejano).
    Esto capta movimientos ~3-4% (la rotación real) en vez de scalps de 0.4%."""
    if side=="long":
        cands=[a.vp_vah[i], a.swing_high_50[i], a.prev_day_high[i], a.weekly_high[i]]
        cands=[c for c in cands if np.isfinite(c) and c>entry*1.001]
        if not cands: return None, np.nan
        tp2 = max(cands)        # FAR = rotación grande
        tp1 = min(cands)        # parcial en el más cercano
    else:
        cands=[a.vp_val[i], a.swing_low_50[i], a.prev_day_low[i], a.weekly_low[i]]
        cands=[c for c in cands if np.isfinite(c) and c<entry*0.999]
        if not cands: return None, np.nan
        tp2 = min(cands)
        tp1 = max(cands)
    return tp1, tp2

def gen_h5():   # Order Block: entrada en POC del OB, TARGET ESTRUCTURAL (siguiente nivel de liquidez)
    def g(a,i):
        out=[]
        # bearish OB: vela alcista (c>o) + swing_high + siguiente cierra bajo 50% -> short en fp_poc
        if i+1<a.n and a.c[i-1]>a.o[i-1] and a.swing_high_50[i]>0 and np.isfinite(a.fp_poc[i]):
            obmid=(a.h[i-1]+a.l[i-1])/2
            if a.c[i] < obmid:
                lvl=a.fp_poc[i-1] if np.isfinite(a.fp_poc[i-1]) else obmid
                stop=a.h[i-1]+0.25*a.atr[i]
                tp1,tp2=struct_target(a,i,"short",lvl)
                if np.isfinite(tp2): out.append(("short",lvl,stop,tp1,tp2,"H5"))
        if i+1<a.n and a.c[i-1]<a.o[i-1] and a.swing_low_50[i]>0 and np.isfinite(a.fp_poc[i]):
            obmid=(a.h[i-1]+a.l[i-1])/2
            if a.c[i] > obmid:
                lvl=a.fp_poc[i-1] if np.isfinite(a.fp_poc[i-1]) else obmid
                stop=a.l[i-1]-0.25*a.atr[i]
                tp1,tp2=struct_target(a,i,"long",lvl)
                if np.isfinite(tp2): out.append(("long",lvl,stop,tp1,tp2,"H5"))
        return out
    return g

def gen_h6():   # Big trades: fade en balance (extremo VA), continuación en expansión
    def g(a,i):
        out=[]; bal = a.reg[i] in ("Chop","chop","Range","range")
        vah,val=a.vp_vah[i],a.vp_val[i]
        if not np.isfinite([vah,val]).all(): return
        if bal:
            if a.big_trade_bullish[i] and a.c[i]>=vah*0.999:   # compra grande en extremo alto -> fade short
                out.append(("short", a.c[i], a.c[i]+0.6*a.atr[i], None, a.c[i]-1.5*0.6*a.atr[i], "H6fade"))
            if a.big_trade_bearish[i] and a.c[i]<=val*1.001:
                out.append(("long", a.c[i], a.c[i]-0.6*a.atr[i], None, a.c[i]+1.5*0.6*a.atr[i], "H6fade"))
        else:  # expansión/tendencia -> continuación
            if a.big_trade_bullish[i]:
                out.append(("long", a.c[i], a.c[i]-0.6*a.atr[i], None, a.c[i]+1.5*0.6*a.atr[i], "H6cont"))
            if a.big_trade_bearish[i]:
                out.append(("short", a.c[i], a.c[i]+0.6*a.atr[i], None, a.c[i]-1.5*0.6*a.atr[i], "H6cont"))
        return out
    return g

def gen_h11(tol=0.0025):   # Confluencia: >=2 niveles HTF coinciden -> fade hacia el cluster
    lv=["vp_poc","vp_vah","vp_val","prev_day_high","prev_day_low","weekly_high","weekly_low","asian_high","asian_low"]
    def g(a,i):
        price=a.c[i]; levels=[getattr(a,k)[i] for k in lv]
        near=[L for L in levels if np.isfinite(L) and abs(price-L)/price<=tol]
        if len(near)<2: return
        # fade: si viene subiendo al cluster -> short; bajando -> long
        if a.c[i] > a.c[i-3]:
            return [("short", price, price+0.6*a.atr[i], None, price-1.5*0.6*a.atr[i], "H11")]
        else:
            return [("long", price, price-0.6*a.atr[i], None, price+1.5*0.6*a.atr[i], "H11")]
    return g

def gen_h13():   # LVN + defensa pasiva (proxy abs_bid/abs_ask)
    def g(a,i):
        lvn=a.vp_lvn_below[i]
        if not np.isfinite(lvn): return
        if abs(a.l[i]-lvn)<=0.3*a.atr[i] and a.abs_bid[i]:   # absorción de venta en LVN
            return [("long", lvn, lvn-0.5*a.atr[i], None, lvn+1.8*0.5*a.atr[i], "H13")]
        return
    return g

def gen_h16():   # Bid refill creciente (proxy: max_trade creciente en mismo nivel)
    def g(a,i):
        if i<4: return
        seq=a.max_trade[i-2:i+1]
        if len(seq)==3 and seq[2]>seq[1]>seq[0]>0:
            # convicción creciente: a favor del delta de la barra
            if a.dz[i]>0:
                return [("long", a.c[i], a.c[i]-0.6*a.atr[i], None, a.c[i]+1.5*0.6*a.atr[i], "H16")]
            else:
                return [("short", a.c[i], a.c[i]+0.6*a.atr[i], None, a.c[i]-1.5*0.6*a.atr[i], "H16")]
        return
    return g

def gen_h18():   # Jerarquía: macro H4 vs debilidad secundaria H1 -> entrada táctica
    def g(a,i):
        # macro alcista (no h4_bearish) + CHoCH bajista H1 -> short setup
        if (not a.h4_bearish[i]) and a.h1_choch_bull[i]:
            return [("short", a.c[i], a.c[i]+0.7*a.atr[i], None, a.c[i]-2.0*0.7*a.atr[i], "H18S")]
        if a.h4_bearish[i] and a.h4_bos_bear[i] and a.h1_choch_bear[i]:
            return [("long", a.c[i], a.c[i]-0.7*a.atr[i], None, a.c[i]+2.0*0.7*a.atr[i], "H18L")]
        return
    return g

def gen_h21(K=15, tol=0.002):   # POC defendido >=2 veces + lift -> long, TARGET ESTRUCTURAL
    def g(a,i):
        if i<K: return
        win=a.fp_poc[i-K:i]; win=win[np.isfinite(win)]
        if len(win)<3: return
        lvl=np.median(win)
        touches=np.sum(np.abs(a.l[i-K:i]-lvl)/lvl<=tol)
        if touches>=2 and a.c[i]>a.c[i-1] and abs(a.l[i]-lvl)/lvl<=tol:
            stop=lvl-0.6*a.atr[i]
            tp1,tp2=struct_target(a,i,"long",lvl)
            if np.isfinite(tp2): return [("long", lvl, stop, tp1, tp2, "H21")]
        return
    return g

def gen_h9():   # POC desplazándose (régimen) -> trend-follow pullback al POC del día
    def g(a,i):
        # POC del día sube vs ayer y antier -> bias long; entrar en pullback al vp_poc
        if i<3: return
        if a.vp_poc[i]>a.vp_poc[i-1]>a.vp_poc[i-2] and a.l[i]<=a.vp_poc[i]<=a.h[i]:
            return [("long", a.vp_poc[i], a.vp_poc[i]-0.7*a.atr[i], None, a.vp_poc[i]+1.5*0.7*a.atr[i], "H9")]
        if a.vp_poc[i]<a.vp_poc[i-1]<a.vp_poc[i-2] and a.l[i]<=a.vp_poc[i]<=a.h[i]:
            return [("short", a.vp_poc[i], a.vp_poc[i]+0.7*a.atr[i], None, a.vp_poc[i]-1.5*0.7*a.atr[i], "H9")]
        return
    return g

# ================================================================ TICK-DERIVADAS (H8/H10/H12)
def attach_tick(t):
    """Merge de _tickfeats_m5 (absorbed delta) y _dayvp (VP diario congelado) al frame M5."""
    tf=pd.read_parquet(ROOT/"data/bybit-perp/_tickfeats_m5.parquet")
    t=t.merge(tf, on="ts_ms", how="left")
    dv=pd.read_parquet(ROOT/"data/bybit-perp/_dayvp.parquet")
    dv["date"]=dv.ts_ms//86_400_000
    dv=dv.sort_values("date")
    dv[["pvpoc","pvvah","pvval"]]=dv[["vp_poc_d","vp_vah_d","vp_val_d"]].shift(1)  # día previo
    dv[["pvpoc2","pvvah2","pvval2"]]=dv[["vp_poc_d","vp_vah_d","vp_val_d"]].shift(2)
    t["date"]=t.ts_ms//86_400_000
    t=t.merge(dv[["date","pvpoc","pvvah","pvval","pvpoc2","pvvah2","pvval2"]], on="date", how="left")
    # z-score causal del delta absorbido
    for c in ("absorbed_neg","absorbed_pos"):
        t[c+"_z"]=(t[c]-t[c].rolling(100).mean().shift(1))/(t[c].rolling(100).std().shift(1)+1e-9)
    return t

def gen_h12():   # Delta absorbido intrabar: señal de absorción -> fade. Entrada a MERCADO (close);
                 # NO usar el extremo realizado de la barra como límite (sería look-ahead).
    def g(a,i):
        zn=getattr(a,"absorbed_neg_z")[i]; zp=getattr(a,"absorbed_pos_z")[i]; c=a.c[i]; b=0.6*a.atr[i]
        out=[]
        if np.isfinite(zn) and zn>=2.0:   # venta absorbida por compradores -> long
            out.append(("long", c, c-b, None, c+1.8*b, "H12"))
        if np.isfinite(zp) and zp>=2.0:
            out.append(("short", c, c+b, None, c-1.8*b, "H12"))
        return out
    return g

def gen_h8(ov_thr=0.5):   # Merge de perfiles: si VP de hoy-previo solapa antier -> POC fusionado, fade hacia él
    def g(a,i):
        v1h,v1l,p1=getattr(a,"pvvah")[i],getattr(a,"pvval")[i],getattr(a,"pvpoc")[i]
        v2h,v2l,p2=getattr(a,"pvvah2")[i],getattr(a,"pvval2")[i],getattr(a,"pvpoc2")[i]
        if not np.isfinite([v1h,v1l,p1,v2h,v2l,p2]).all(): return
        ol=max(0,min(v1h,v2h)-max(v1l,v2l)); rng=min(v1h-v1l,v2h-v2l)
        if rng<=0 or ol/rng < ov_thr: return  # no solapan -> no merge
        # POC fusionado ~ promedio de los dos POC; VAH/VAL fusionados = unión
        pm=(p1+p2)/2; vh=max(v1h,v2h); vl=min(v1l,v2l)
        out=[]
        if a.h[i]>=vh and vh>a.c[i-1]:  out.append(("short", vh, vh+0.5*a.atr[i], pm, vl, "H8"))
        if a.l[i]<=vl and vl<a.c[i-1]:  out.append(("long",  vl, vl-0.5*a.atr[i], pm, vh, "H8"))
        return out
    return g

def build_vol_bars_proxy(a, mult=1.0):
    """Volume bars (proxy desde M5): acumula volumen hasta umbral = mult * mediana(vol diario M5)."""
    thr=mult*np.nanmedian(a.volume)*48   # ~ media sesión
    bars=[]; o=h=l=c=None; v=0; si=0
    for i in range(a.n):
        if o is None: o=a.o[i]; h=a.h[i]; l=a.l[i]; si=i
        h=max(h,a.h[i]); l=min(l,a.l[i]); c=a.c[i]; v+=a.volume[i]
        if v>=thr:
            bars.append((si,i,o,h,l,c)); o=None; v=0
    return bars

def gen_h10_factory(a):   # Velas de volumen: reversión de estructura en barras de volumen
    bars=build_vol_bars_proxy(a); endidx={b[1]:k for k,b in enumerate(bars)}
    def g(aa,i):
        if i not in endidx: return
        k=endidx[i]
        if k<2: return
        _,_,o0,h0,l0,c0=bars[k]; _,_,_,h1,l1,c1=bars[k-1]; _,_,_,h2,l2,c2=bars[k-2]
        # reversión: rompe el mínimo previo y cierra de vuelta (CHoCH alcista) -> long
        if l0<l1 and c0>l1 and c0>o0:
            return [("long", aa.c[i], aa.c[i]-0.8*aa.atr[i], None, aa.c[i]+1.8*0.8*aa.atr[i], "H10")]
        if h0>h1 and c0<h1 and c0<o0:
            return [("short", aa.c[i], aa.c[i]+0.8*aa.atr[i], None, aa.c[i]-1.8*0.8*aa.atr[i], "H10")]
        return
    return g

GENS={"h2":gen_h2,"h5":gen_h5,"h6":gen_h6,"h9":gen_h9,"h11":gen_h11,"h13":gen_h13,
      "h16":gen_h16,"h18":gen_h18,"h21":gen_h21}
TICKGENS={"h8":gen_h8,"h12":gen_h12}  # h10 necesita factory con 'a'

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("which",nargs="?",default="all")
    ap.add_argument("--tf",type=int,default=5); args=ap.parse_args()
    print(f"Cargando M{args.tf}..."); t=load2(args.tf); a=A2(t)
    span=(a.ts.max()-a.ts.min())/86_400_000; timeout=int(8*60/args.tf)
    print(f"tf=M{args.tf} span={span:.0f}d OOS>=2026-03-01 timeout={timeout}\n")
    todo=list(GENS) if args.which in ("all","allbase") else ([args.which] if args.which in GENS else [])
    for k in todo:
        gen=GENS[k]()
        for mode in ("taker","maker"):
            report(f"{k.upper()} [{mode}]", run_level(a,gen,timeout,mode), span)
    # tick-derivadas
    tickdo = list(TICKGENS)+["h10"] if args.which in ("all","tick") else ([args.which] if args.which in (set(TICKGENS)|{"h10"}) else [])
    if tickdo:
        print("Adjuntando columnas tick-derivadas...")
        at=A2(attach_tick(t))
        for k in tickdo:
            gen = gen_h10_factory(at) if k=="h10" else TICKGENS[k]()
            for mode in ("taker","maker"):
                report(f"{k.upper()} [{mode}]", run_level(at,gen,timeout,mode), span)

if __name__=="__main__": main()
