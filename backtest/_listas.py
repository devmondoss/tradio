"""
_listas.py — Barrido de las hipótesis "listas" como ESTRATEGIAS COMPLETAS (no predictores)
============================================================================================
Reencuadre (usuario, 2026-06-20): el EDGE_VERDICT cerró la pregunta "¿alguna feature PREDICE el
retorno forward?" (no). Pero una estrategia no es un predictor: es ESTRUCTURA de entrada +
GESTIÓN asimétrica (stop, TP1 parcial, breakeven, TP2, timeout) + filtros de abstención. Una
expectativa positiva puede salir del path/payoff aunque el hit-rate sea ~50%. Eso es lo que el
framing de "predictor" nunca midió. Aquí se testea ese espacio, con el mismo rigor honesto.

Catálogo: docs/orderflow especificados en 02_catalogo_21_hipotesis.md / 01_opening_range_4_variants.md
"Listas" sin trabajo de datos extra: H1, H3, H4, H7, H9, H14, H15, H17, H19 (+H20).
  Standalone : H1 (4 variantes apertura), H7 (LVN→HVN), H14 (80/20), H15 (repair), H19 (daily sent.), H20 (pullback box)
  Overlays   : H3 (veto delta-en-contra), H4 (unfinished), H9 (régimen POC), H17 (salida tape)

Disciplina: causal (info <= t), fee 11 bps RT taker (4 bps maker variante), IS<2026-03-01/OOS,
$500 @ 1%, walk-forward por folds, reporta FECHAS ÚNICAS (no solo n trades).

Uso:  python backtest/_listas.py [h1|h7|h14|h15|h19|h20|all] [--maker] [--tf 5|15]
"""
import argparse
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
M1 = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"
OOS_MS = int(pd.Timestamp("2026-03-01", tz="UTC").value // 1_000_000)
TICK_MS = int(pd.Timestamp("2025-06-19", tz="UTC").value // 1_000_000)
FEE_TAKER, FEE_MAKER = 0.0011, 0.0004
CAP0, RISK = 500.0, 0.01

# ----------------------------------------------------------------------------- helpers
def atr(h, l, c, n=14):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    out = np.full(len(tr), np.nan); out[n-1] = np.nanmean(tr[:n]); k = 1.0/n
    for i in range(n, len(tr)): out[i] = out[i-1]*(1-k) + tr[i]*k
    return out

def load(tf_min, open_hour=0, start_ms=TICK_MS):
    cols = ["ts_ms","open","high","low","close","volume","delta","dz","vr","regime",
            "vp_poc","vp_vah","vp_val","vp_lvn_below","prev_day_high","prev_day_low",
            "fp_unfinished_hi","fp_unfinished_lo","buy_vol","sell_vol"]
    df = pd.read_parquet(M1, columns=cols).sort_values("ts_ms").reset_index(drop=True)
    df = df[df.ts_ms >= start_ms].reset_index(drop=True)
    # daily context (causal): prev-day frozen VP + prev_day_high/low + daily_open
    df["date"] = (df.ts_ms // 86_400_000)
    day_last = df.groupby("date")[["vp_poc","vp_vah","vp_val"]].last()
    day_last = day_last.shift(1).rename(columns={"vp_poc":"ppoc","vp_vah":"pvah","vp_val":"pval"})
    # daily_open = open de la 1ª vela en/después de open_hour UTC (parametrizable, spec §2)
    mod = (df.ts_ms // 60_000) % 1440
    at_open = df[mod >= open_hour*60]
    day_open = at_open.groupby("date")["open"].first().rename("dopen")
    ctx = day_last.join(day_open)
    df = df.merge(ctx, on="date", how="left")
    if tf_min == 1:
        t = df
    else:
        g = (df.ts_ms // (tf_min*60_000)) * (tf_min*60_000)
        agg = df.groupby(g).agg(
            ts_ms=("ts_ms","first"), open=("open","first"), high=("high","max"),
            low=("low","min"), close=("close","last"), volume=("volume","sum"),
            delta=("delta","sum"), buy_vol=("buy_vol","sum"), sell_vol=("sell_vol","sum"),
            regime=("regime","last"), vp_lvn_below=("vp_lvn_below","last"),
            fp_unfinished_hi=("fp_unfinished_hi","max"), fp_unfinished_lo=("fp_unfinished_lo","max"),
            ppoc=("ppoc","last"), pvah=("pvah","last"), pval=("pval","last"),
            dopen=("dopen","last"), prev_day_high=("prev_day_high","last"),
            prev_day_low=("prev_day_low","last"))
        t = agg.reset_index(drop=True)
    t["atr"] = atr(t.high.values, t.low.values, t.close.values)
    # delta z / volume ratio recalculados al tf de ejecución (causal, shift)
    t["dz"] = (t.delta - t.delta.rolling(50).mean().shift(1)) / (t.delta.rolling(50).std().shift(1)+1e-9)
    t["vr"] = t.volume / (t.volume.rolling(50).mean().shift(1)+1e-9)
    return t

# ----------------------------------------------------------------------------- simulador con gestión
def sim(i, side, entry, stop, tp1, tp2, hi, lo, cl, n, timeout, fee_rt,
        p1=0.5, be_after_tp1=True, start=None):
    """Path-dependent causal. Parcial p1 en TP1 -> stop a breakeven -> resto a TP2/stop/timeout.
    Devuelve R neto (1R = |entry-stop|). start=i para fills límite (cuenta selección adversa
    intrabar del propio bar de llenado); por defecto i+1 (mercado al cierre)."""
    risk = abs(entry - stop)
    if risk <= 0: return None
    fee_r = fee_rt * entry / risk           # fee RT en unidades de R (full notional)
    filled1 = False; cur_stop = stop; realized = 0.0; rem = 1.0
    j0 = (i+1) if start is None else start
    for j in range(j0, min(i+1+timeout, n)):
        if side == "long":
            if lo[j] <= cur_stop:
                realized += rem * ((cur_stop-entry)/risk); return realized - fee_r
            if not filled1 and tp1 is not None and hi[j] >= tp1:
                realized += p1 * ((tp1-entry)/risk); rem -= p1; filled1 = True
                if be_after_tp1: cur_stop = entry
            if tp2 is not None and hi[j] >= tp2:
                realized += rem * ((tp2-entry)/risk); return realized - fee_r
        else:
            if hi[j] >= cur_stop:
                realized += rem * ((entry-cur_stop)/risk); return realized - fee_r
            if not filled1 and tp1 is not None and lo[j] <= tp1:
                realized += p1 * ((entry-tp1)/risk); rem -= p1; filled1 = True
                if be_after_tp1: cur_stop = entry
            if tp2 is not None and lo[j] <= tp2:
                realized += rem * ((entry-tp2)/risk); return realized - fee_r
    px = cl[min(i+timeout, n-1)]            # timeout -> a mercado
    realized += rem * (((px-entry) if side=="long" else (entry-px))/risk)
    return realized - fee_r

# ----------------------------------------------------------------------------- reporte
def report(name, trades, span_days):
    if not trades:
        print(f"--- {name} ---\n   SIN TRADES\n"); return
    df = pd.DataFrame(trades)
    df["day"] = df.ts // 86_400_000
    def blk(d, tag, frac):
        if len(d)==0: return f"{tag} n=0"
        wr = 100*(d.r>0).mean(); avgr = d.r.mean(); ndays = d.day.nunique()
        shp = avgr/(d.r.std()+1e-9)*np.sqrt(len(d)/max(span_days*frac/365*252,1e-9)) if len(d)>1 else 0
        return (f"{tag} n={len(d):>4} días={ndays:>3} WR={wr:>4.1f}% avgR={avgr:>+.3f} "
                f"netR={d.r.sum():>+6.1f} Sharpe={shp:>+4.1f} {len(d)/max(span_days*frac,1):.2f}tr/d")
    di, do = df[~df.oos], df[df.oos]
    print(f"--- {name} ---")
    print(f"   {blk(di,'IS ',0.66)}")
    print(f"   {blk(do,'OOS',0.34)}")
    # walk-forward: 4 folds temporales en IS
    if len(di) >= 8:
        di2 = di.sort_values("ts"); folds = np.array_split(di2, 4)
        wf = " ".join(f"f{k+1}={f.r.mean():+.2f}" for k,f in enumerate(folds))
        print(f"   WF-IS: {wf}")
    print()

# ----------------------------------------------------------------------------- overlays (vetos)
def veto_delta_against(dz_arr, i, side, th=1.5, N=3):       # H3
    seg = dz_arr[max(0,i-N+1):i+1]
    if len(seg)==0: return False
    m = np.nanmean(seg)
    return (side=="long" and m <= -th) or (side=="short" and m >= th)

def veto_unfinished(uhi, ulo, i, side):                      # H4
    return (side=="short" and uhi[i]) or (side=="long" and ulo[i])

def veto_regime(regime, i, want_range=True):                # H9 (proxy: régimen Chop = rango)
    r = regime[i]
    is_range = (str(r) in ("Chop","chop","Range","range") )
    return (want_range and not is_range)

# ----------------------------------------------------------------------------- arrays comunes
class A:
    def __init__(s, t):
        s.ts=t.ts_ms.values.astype(np.int64); s.o=t.open.values.astype(float)
        s.h=t.high.values.astype(float); s.l=t.low.values.astype(float); s.c=t.close.values.astype(float)
        s.atr=t.atr.values.astype(float); s.dz=t.dz.values; s.vr=t.vr.values
        s.bv=t.buy_vol.values.astype(float); s.sv=t.sell_vol.values.astype(float)
        s.uhi=t.fp_unfinished_hi.fillna(0).values.astype(bool); s.ulo=t.fp_unfinished_lo.fillna(0).values.astype(bool)
        s.reg=t.regime.astype(str).values; s.lvn=t.vp_lvn_below.values.astype(float)
        s.ppoc=t.ppoc.values.astype(float); s.pvah=t.pvah.values.astype(float); s.pval=t.pval.values.astype(float)
        s.dopen=t.dopen.values.astype(float); s.pdh=t.prev_day_high.values.astype(float); s.pdl=t.prev_day_low.values.astype(float)
        s.day=(s.ts//86_400_000); s.n=len(s.ts)

def apply_overlays(a, i, side, ov):
    if "h3" in ov and veto_delta_against(a.dz, i, side): return False
    if "h4" in ov and veto_unfinished(a.uhi, a.ulo, i, side): return False
    if "h9" in ov and veto_regime(a.reg, i): return False
    return True

def finalize(a, i, side, entry, stop, tp1, tp2, timeout, fee, trades, p1=0.5, tag="", start=None):
    r = sim(i, side, entry, stop, tp1, tp2, a.h, a.l, a.c, a.n, timeout, fee, p1=p1, start=start)
    if r is None: return False
    trades.append({"ts": int(a.ts[i]), "side": side, "r": r, "oos": int(a.ts[i])>=OOS_MS, "tag": tag})
    return True

# ----------------------------------------------------------------------------- H1: 4 variantes apertura (spec fiel)
def h1(a, fee, timeout, stop_mode="max", tol_atr=0.15, trigger="close", min_rr=1.5,
       sub1b=True, session_only=False, entry_mode="taker_close", variants=("V1","V2","V3"),
       fill_margin_bps=0.0, vol_high_only=False, ov=()):
    """Spec 01_opening_range_4_variants.md. V1 fade rango (1a extremo + 1b reacción POC),
    V2/V3 lean (entrada POC tras retroceso, target dopen), V4 excluida. Stop modo local/max.
    Trigger 'wick' (mecha) o 'close' (cierre de rechazo). Gate min_RR. Filtro sesión NY opcional.
    entry_mode: 'taker_close' (mercado al close de rechazo, fee taker) o
                'maker_limit' (orden límite REPOSANDO en el nivel: entrada=nivel, llenada al
                 tocar, selección adversa intrabar contada; fee maker).
    vol_high_only: solo opera cuando ATR > su mediana móvil(500) causal (filtro de volatilidad)."""
    trades=[]; cool=0; maker = (entry_mode=="maker_limit")
    day_state={}  # d -> dict(v1_first_done, side, taken)
    hm = (a.ts // 60_000) % 1440
    sess = ((hm>=13*60)&(hm<17*60))
    atr_med = pd.Series(a.atr).rolling(500, min_periods=50).median().shift(1).values
    def trig_long(i, lvl):   # llegada al nivel desde arriba
        if maker:            # límite long reposando bajo el precio: llena si el low alcanza nivel
            return a.l[i] <= lvl - fill_margin_bps/1e4*lvl    # margin>0 = solo si lo atraviesa (adverso)
        touch = a.l[i] <= lvl + tol_atr*a.atr[i]
        if trigger=="wick": return touch
        return touch and a.c[i] > (a.h[i]+a.l[i])/2       # cierre en mitad alejada (rechazo)
    def trig_short(i, lvl):
        if maker:
            return a.h[i] >= lvl + fill_margin_bps/1e4*lvl
        touch = a.h[i] >= lvl - tol_atr*a.atr[i]
        if trigger=="wick": return touch
        return touch and a.c[i] < (a.h[i]+a.l[i])/2
    def px(i, lvl):          # precio de entrada: nivel (límite maker) o close (mercado taker)
        return lvl if maker else a.c[i]
    def emit(i, side, entry, stop, tp1, tp2, tag=""):
        if entry<=0 or not np.isfinite([entry,stop,tp2]).all(): return False
        if side=="long" and not (stop<entry<tp2): return False
        if side=="short" and not (tp2<entry<stop): return False
        if abs(tp2-entry)/max(abs(entry-stop),1e-9) < min_rr: return False     # gate min_RR
        if not apply_overlays(a,i,side,ov): return False
        return finalize(a,i,side,entry,stop,tp1,tp2,timeout,fee,trades,tag=tag,
                        start=(i if maker else None))
    for i in range(60, a.n-1):
        if i<cool: continue
        if session_only and not sess[i]: continue
        if vol_high_only and not (np.isfinite(atr_med[i]) and a.atr[i] > atr_med[i]): continue
        ppoc,pvah,pval,dop = a.ppoc[i],a.pvah[i],a.pval[i],a.dopen[i]
        if not np.isfinite([ppoc,pvah,pval,dop]).all() or a.atr[i]<=0: continue
        d=int(a.day[i]); st=day_state.setdefault(d, {"v1_done":False,"v1_side":None,"taken":0})
        if st["taken"]>=2: continue
        if pval<=dop<=pvah: variant="V1"
        elif pvah<dop<=a.pdh[i]: variant="V2"
        elif a.pdl[i]<=dop<pval: variant="V3"
        else: continue
        if variant not in variants: continue
        ok=False
        if variant=="V1":
            if not st["v1_done"]:
                # 1a: fade del extremo hacia POC (tp1) y extremo opuesto (tp2)
                if trig_short(i, pvah) and (maker or a.c[i]<pvah):
                    stop = a.pdh[i] if stop_mode=="max" else max(a.h[i],pvah)+0.25*a.atr[i]
                    ok = emit(i,"short",px(i,pvah),stop,ppoc,pval,tag="V1")
                    if ok: st.update(v1_done=True, v1_side="short")
                elif trig_long(i, pval) and (maker or a.c[i]>pval):
                    stop = a.pdl[i] if stop_mode=="max" else min(a.l[i],pval)-0.25*a.atr[i]
                    ok = emit(i,"long",px(i,pval),stop,ppoc,pvah,tag="V1")
                    if ok: st.update(v1_done=True, v1_side="long")
            elif sub1b:
                # 1b: reacción en POC -> 2ª entrada a rotación completa (extremo opuesto)
                if st["v1_side"]=="short" and a.l[i]<=ppoc and (maker or a.c[i]<ppoc) and trig_short(i,ppoc):
                    stop = max(a.h[i],ppoc)+0.5*a.atr[i]
                    ok = emit(i,"short",px(i,ppoc),stop,None,pval,tag="V1b")
                elif st["v1_side"]=="long" and a.h[i]>=ppoc and (maker or a.c[i]>ppoc) and trig_long(i,ppoc):
                    stop = min(a.l[i],ppoc)-0.5*a.atr[i]
                    ok = emit(i,"long",px(i,ppoc),stop,None,pvah,tag="V1b")
        elif variant=="V2":   # alcista leve: retroceso al POC -> long, target dopen
            if a.l[i]<=ppoc<=a.h[i] and (maker or a.c[i]>ppoc):
                stop = a.pval[i] if stop_mode=="max" else min(a.l[i],ppoc)-0.25*a.atr[i]
                ok = emit(i,"long",px(i,ppoc),stop,None,dop,tag="V2")
        elif variant=="V3":
            if a.l[i]<=ppoc<=a.h[i] and (maker or a.c[i]<ppoc):
                stop = a.pvah[i] if stop_mode=="max" else max(a.h[i],ppoc)+0.25*a.atr[i]
                ok = emit(i,"short",px(i,ppoc),stop,None,dop,tag="V3")
        if ok:
            st["taken"]+=1; cool=i+1
    return trades

def h1_variant_split(a, fee, timeout, **kw):
    """Reporta V1/V2/V3 por separado (spec §10: evaluar variantes por separado)."""
    return h1(a, fee, timeout, **kw)

# ----------------------------------------------------------------------------- H7: LVN entrada -> HVN/POC target
def h7(a, fee, timeout, tol_atr=0.25, ov=()):
    trades=[]; cool=0
    for i in range(60, a.n-1):
        if i<cool: continue
        lvn=a.lvn[i]; ppoc=a.ppoc[i]
        if not np.isfinite([lvn,ppoc]).all() or a.atr[i]<=0: continue
        tol=tol_atr*a.atr[i]
        # toque de LVN por debajo -> long reversion, target POC (HVN) por encima
        if abs(a.l[i]-lvn)<=tol and a.c[i]>lvn and ppoc>a.c[i]:
            side="long"; entry=a.c[i]; stop=lvn-0.5*a.atr[i]; tp2=ppoc
            if not (stop<entry<tp2): continue
            if not apply_overlays(a,i,side,ov): continue
            if finalize(a,i,side,entry,stop,(entry+tp2)/2,tp2,timeout,fee,trades): cool=i+3
    return trades

# ----------------------------------------------------------------------------- H14: niveles redondos 80/20
def h14(a, fee, timeout, grid=500.0, stop_bps=15.0, tp_bps=22.0, ov=()):
    trades=[]; cool=0
    for i in range(60, a.n-1):
        if i<cool: continue
        # nivel redondo más cercano (grid$ con sub .00/.50)
        base=round(a.c[i]/grid)*grid
        for lv in (base, base-grid/2, base+grid/2):
            if abs(a.c[i]-lv)/a.c[i] > 0.0006: continue   # toque instantáneo
            # reacción: cierre de vuelta del nivel
            if a.l[i]<=lv and a.c[i]>lv:        # rebote desde abajo -> long
                side="long"; entry=a.c[i]; stop=entry*(1-stop_bps/1e4); tp2=entry*(1+tp_bps/1e4)
            elif a.h[i]>=lv and a.c[i]<lv:      # rechazo desde arriba -> short
                side="short"; entry=a.c[i]; stop=entry*(1+stop_bps/1e4); tp2=entry*(1-tp_bps/1e4)
            else: continue
            if not apply_overlays(a,i,side,ov): continue
            if finalize(a,i,side,entry,stop,None,tp2,timeout,fee,trades,p1=1.0): cool=i+3
            break
    return trades

# ----------------------------------------------------------------------------- H15: repair candle re-entry
def h15(a, fee, timeout, max_wick=0.10, tol_atr=0.2, rr=1.5, ov=()):
    trades=[]; cool=0; zones=[]  # (price, side, expiry_idx)
    for i in range(60, a.n-1):
        rng=a.h[i]-a.l[i]
        if rng>0:
            body=abs(a.c[i]-a.o[i]); wick=(rng-body)/rng
            if wick<=max_wick:
                side="long" if a.c[i]>a.o[i] else "short"
                zones.append(((a.o[i]+a.c[i])/2, side, i+200, a.l[i], a.h[i]))
        if i<cool: continue
        tol=tol_atr*a.atr[i]
        for z in zones:
            zp,side,exp,zl,zh=z
            if i>exp: continue
            if abs(a.c[i]-zp)<=tol:
                entry=a.c[i]
                if side=="long":
                    stop=zl-0.25*a.atr[i]; tp2=entry+rr*(entry-stop)
                    if not (stop<entry<tp2): continue
                else:
                    stop=zh+0.25*a.atr[i]; tp2=entry-rr*(stop-entry)
                    if not (tp2<entry<stop): continue
                if not apply_overlays(a,i,side,ov): continue
                if finalize(a,i,side,entry,stop,None,tp2,timeout,fee,trades,p1=1.0):
                    cool=i+3; zones.remove(z); break
        zones=[z for z in zones if z[2]>=i]
    return trades

# ----------------------------------------------------------------------------- H19: daily sentiment continuation
def h19(a, fee, timeout_unused, stop_bps=60.0, hold_bars=None, ov=()):
    # bias = signo de la vela diaria anterior; entrar a la apertura del día, salir a cierre del día (o stop)
    trades=[]
    # construir velas diarias
    days = np.unique(a.day)
    dopen={}; dclose={}; didx={}
    for d in days:
        m=np.where(a.day==d)[0]
        dopen[d]=a.o[m[0]]; dclose[d]=a.c[m[-1]]; didx[d]=(m[0],m[-1])
    for k in range(1,len(days)):
        d=days[k]; dp=days[k-1]
        bias="long" if dclose[dp]>dopen[dp] else "short"
        i0,i1=didx[d]
        entry=a.o[i0]
        stop = entry*(1-stop_bps/1e4) if bias=="long" else entry*(1+stop_bps/1e4)
        if not apply_overlays(a,i0,bias,ov): continue
        # salida: stop intradía o cierre del día
        r=sim(i0,bias,entry,stop,None,None,a.h,a.l,a.c,a.n, i1-i0, fee, p1=1.0)
        if r is not None:
            trades.append({"ts":int(a.ts[i0]),"side":bias,"r":r,"oos":int(a.ts[i0])>=OOS_MS})
    return trades

# ----------------------------------------------------------------------------- H20: caja estadística de pullback
def h20(a, fee, timeout, N=50, lo_pct=25, hi_pct=75, rr=1.5, ov=()):
    trades=[]; cool=0
    c=a.c
    sma=pd.Series(c).rolling(N).mean().shift(1).values
    hh=pd.Series(a.h).rolling(N).max().shift(1).values
    ll=pd.Series(a.l).rolling(N).min().shift(1).values
    # distribución histórica de profundidad de pullback (expanding, causal)
    depths=[]
    for i in range(N+2, a.n-1):
        if i<cool: continue
        if not np.isfinite([sma[i],hh[i],ll[i]]).all(): continue
        rng=hh[i]-ll[i]
        if rng<=0: continue
        up = c[i]>sma[i]
        depth = (hh[i]-c[i])/rng if up else (c[i]-ll[i])/rng
        depths.append(depth)
        if len(depths)<60: continue
        box_lo=np.percentile(depths[-500:], lo_pct); box_hi=np.percentile(depths[-500:], hi_pct)
        if not (box_lo<=depth<=box_hi): continue
        if up:
            side="long"; entry=c[i]; stop=ll[i]; tp2=entry+rr*(entry-stop)
            if not(stop<entry<tp2): continue
        else:
            side="short"; entry=c[i]; stop=hh[i]; tp2=entry-rr*(stop-entry)
            if not(tp2<entry<stop): continue
        if not apply_overlays(a,i,side,ov): continue
        if finalize(a,i,side,entry,stop,None,tp2,timeout,fee,trades,p1=1.0): cool=i+5
    return trades

# ----------------------------------------------------------------------------- driver
STRATS = {"h1":h1, "h7":h7, "h14":h14, "h15":h15, "h19":h19, "h20":h20}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("which", nargs="?", default="all")
    ap.add_argument("--maker", action="store_true")
    ap.add_argument("--tf", type=int, default=5)
    ap.add_argument("--overlays", action="store_true", help="probar tambien con vetos H3/H4/H9")
    args=ap.parse_args()
    fee = FEE_MAKER if args.maker else FEE_TAKER
    print(f"Cargando M{args.tf}...")
    t=load(args.tf); a=A(t)
    span=(a.ts.max()-a.ts.min())/86_400_000
    timeout = int(8*60/args.tf)  # ~8h
    print(f"tf=M{args.tf} fee={fee*1e4:.0f}bps cap=${CAP0:.0f} risk={RISK*100:.0f}% "
          f"span={span:.0f}d OOS>=2026-03-01 timeout={timeout}barras\n")
    todo = list(STRATS) if args.which=="all" else [args.which]
    for key in todo:
        if key=="h1": continue   # H1 tiene su propio barrido (--which h1)
        fn=STRATS[key]
        tr = fn(a,fee,timeout)
        report(f"{key.upper()}", tr, span)
        if args.overlays and key!="h19":
            report(f"{key.upper()} [+H3+H4]", fn(a,fee,timeout,ov=("h3","h4")), span)

def report_by_tag(name, trades, span):
    report(name, trades, span)
    if trades:
        df=pd.DataFrame(trades)
        for tg in sorted(df.tag.unique()):
            sub=df[df.tag==tg]
            do=sub[sub.oos]; di=sub[~sub.oos]
            iv=f"IS n={len(di)} avgR={di.r.mean():+.3f}" if len(di) else "IS n=0"
            ov=f"OOS n={len(do)} avgR={do.r.mean():+.3f}" if len(do) else "OOS n=0"
            print(f"      [{tg}] {iv} | {ov}")
        print()

def h1_sweep(args):
    """Barrido de sensibilidades fiel al spec §10/§11."""
    for oh in (0, 13):
        t=load(args.tf, open_hour=oh); a=A(t); span=(a.ts.max()-a.ts.min())/86_400_000
        timeout=int(8*60/args.tf)
        print(f"\n############ H1  open_hour={oh:02d}UTC  tf=M{args.tf} ############")
        base=dict(stop_mode="max",trigger="close",min_rr=1.5,sub1b=True)
        # --- TAKER (mercado al close de rechazo) ---
        report_by_tag("H1 taker [max/close/rr1.5/1b]", h1(a,FEE_TAKER,timeout,**base), span)
        report("H1 taker [stop=local]", h1(a,FEE_TAKER,timeout,**{**base,"stop_mode":"local"}), span)
        # --- MAKER LÍMITE REALISTA (entrada=nivel, selección adversa intrabar) ---
        ml=dict(entry_mode="maker_limit")
        report_by_tag("H1 maker-LIMIT [max/1b]", h1(a,FEE_MAKER,timeout,**{**base,**ml}), span)
        report("H1 maker-LIMIT [stop=local]", h1(a,FEE_MAKER,timeout,**{**base,**ml,"stop_mode":"local"}), span)
        report("H1 maker-LIMIT [sin sub1b]", h1(a,FEE_MAKER,timeout,**{**base,**ml,"sub1b":False}), span)
        report("H1 maker-LIMIT [+veto H3/H4]", h1(a,FEE_MAKER,timeout,**{**base,**ml},ov=("h3","h4")), span)

def h1_robust(args):
    """¿open_hour=13 es pico curve-fit o meseta? Barrido de robustez, config limpia:
    maker-límite, stop=max, sin sub1b, V1+V3 (V2 decae OOS). Si el OOS es positivo en una
    MESETA de horas alrededor de NY, es señal; si solo en 13 exacto, es artefacto."""
    cfg=dict(stop_mode="max",trigger="close",min_rr=1.5,sub1b=False,
             entry_mode="maker_limit",variants=("V1","V3"))
    print(f"\n=== H1 robustez open_hour | maker-LIMIT, stop=max, sin1b, V1+V3, M{args.tf} ===")
    print(f"{'oh':>3} | {'IS n':>5} {'avgR':>7} | {'OOS n':>5} {'avgR':>7} {'WR':>5} | WF-IS folds")
    for oh in range(8, 17):
        t=load(args.tf, open_hour=oh); a=A(t); timeout=int(8*60/args.tf)
        tr=h1(a,FEE_MAKER,timeout,**cfg)
        if not tr: print(f"{oh:>3} | sin trades"); continue
        df=pd.DataFrame(tr); di,do=df[~df.oos],df[df.oos]
        wf=np.array_split(di.sort_values("ts"),4)
        wfs=" ".join(f"{f.r.mean():+.2f}" for f in wf)
        print(f"{oh:>3} | {len(di):>5} {di.r.mean():>+7.3f} | {len(do):>5} {do.r.mean():>+7.3f} "
              f"{100*(do.r>0).mean():>4.0f}% | {wfs}")
    # también taker en la mejor hora, para ver si el edge es estructural o solo fee
    print(f"\n=== mismo (V1+V3, sin1b, max) a TAKER por hora (¿edge estructural?) ===")
    for oh in (12,13,14):
        t=load(args.tf, open_hour=oh); a=A(t); timeout=int(8*60/args.tf)
        tr=h1(a,FEE_TAKER,timeout,**cfg)
        if not tr: continue
        df=pd.DataFrame(tr); di,do=df[~df.oos],df[df.oos]
        print(f" oh={oh} taker | IS avgR={di.r.mean():+.3f} (n={len(di)}) | OOS avgR={do.r.mean():+.3f} (n={len(do)})")
    # STRESS de selección adversa: el límite SOLO llena si el precio ATRAVIESA el nivel (entras offside)
    print(f"\n=== STRESS selección adversa: fill solo si atraviesa el nivel (maker, oh=12/13) ===")
    print(f"{'oh':>3} {'margin':>7} | {'OOS n':>5} {'avgR':>7} {'WR':>5} | netR")
    for oh in (12,13):
        t=load(args.tf, open_hour=oh); a=A(t); timeout=int(8*60/args.tf)
        for mg in (0.0, 1.0, 2.0, 4.0):
            tr=h1(a,FEE_MAKER,timeout,**cfg,fill_margin_bps=mg)
            if not tr: print(f"{oh:>3} {mg:>7.0f} | sin trades"); continue
            do=pd.DataFrame(tr); do=do[do.oos]
            print(f"{oh:>3} {mg:>6.0f}b | {len(do):>5} {do.r.mean():>+7.3f} {100*(do.r>0).mean():>4.0f}% | {do.r.sum():+.1f}")
    # REGIMEN: extender a 2025-01-01 (incluye régimen alcista distinto). Sub-split por semestre.
    print(f"\n=== REGIMEN: periodo completo 2025-01-01+ (maker, V1+V3, oh=12/13) ===")
    full0=int(pd.Timestamp('2025-01-01',tz='UTC').value//1_000_000)
    print(f"{'oh':>3} | {'tramo':<22} {'n':>5} {'avgR':>7} {'WR':>5} {'netR':>7}")
    for oh in (12,13):
        t=load(args.tf, open_hour=oh, start_ms=full0); a=A(t); timeout=int(8*60/args.tf)
        tr=h1(a,FEE_MAKER,timeout,**cfg)
        if not tr: continue
        df=pd.DataFrame(tr); df['q']=pd.to_datetime(df.ts,unit='ms',utc=True).dt.to_period('Q').astype(str)
        for q,sub in df.groupby('q'):
            print(f"{oh:>3} | {q:<22} {len(sub):>5} {sub.r.mean():>+7.3f} {100*(sub.r>0).mean():>4.0f}% {sub.r.sum():>+7.1f}")
        print(f"{oh:>3} | {'TOTAL':<22} {len(df):>5} {df.r.mean():>+7.3f} {100*(df.r>0).mean():>4.0f}% {df.r.sum():>+7.1f}")

def h1_final(args):
    """Config FIJADA: V1+V3, open_hour=12, maker-límite, stop=max, sin sub1b, min_rr 1.5.
    Métricas de cartera reales ($500 @ 1%, equity, MaxDD, Sharpe) IS/OOS y periodo completo."""
    cfg=dict(stop_mode="max",trigger="close",min_rr=1.5,sub1b=False,
             entry_mode="maker_limit",variants=("V1","V3"))
    full0=int(pd.Timestamp('2025-01-01',tz='UTC').value//1_000_000)
    t=load(args.tf, open_hour=12, start_ms=full0); a=A(t); timeout=int(8*60/args.tf)
    tr=pd.DataFrame(h1(a,FEE_MAKER,timeout,**cfg)).sort_values("ts").reset_index(drop=True)
    def metrics(d, tag):
        if len(d)==0: print(f"{tag}: n=0"); return
        cap=CAP0; peak=CAP0; dd=0.0
        for r in d.r.values:
            cap += CAP0*RISK*r            # riesgo fijo sobre capital inicial (sin compounding)
            peak=max(peak,cap); dd=max(dd,(peak-cap)/peak)
        days=(d.ts.max()-d.ts.min())/86_400_000
        shp=d.r.mean()/(d.r.std()+1e-9)*np.sqrt(len(d)/max(days/365*252,1e-9))
        print(f"{tag}: n={len(d):>4} días={d.ts.pipe(lambda s:(s//86_400_000)).nunique():>3} "
              f"WR={100*(d.r>0).mean():>4.1f}% avgR={d.r.mean():>+.3f} netR={d.r.sum():>+6.1f} "
              f"$500->${cap:>6.0f} MaxDD={dd*100:>4.1f}% Sharpe={shp:>+4.2f} {len(d)/max(days,1):.2f}tr/d")
    print("\n#### H1 CONFIG FIJADA — V1+V3, oh=12 UTC, maker-límite, stop=max, sin1b, minRR1.5 ####")
    print(f"     (fee maker {FEE_MAKER*1e4:.0f}bps RT, tf=M{args.tf}, $500 @ 1% riesgo fijo)\n")
    metrics(tr, "TODO 2025-01+")
    metrics(tr[tr.ts<OOS_MS], "IS  <2026-03")
    metrics(tr[tr.ts>=OOS_MS], "OOS >=2026-03")
    # mensual
    tr['m']=pd.to_datetime(tr.ts,unit='ms',utc=True).dt.to_period('M').astype(str)
    print("\n     netR por mes:")
    mm=tr.groupby('m').r.agg(['count','sum','mean'])
    for m,row in mm.iterrows():
        bar="#"*int(max(0,row['sum'])) + ("." if row['sum']<0 else "")
        print(f"       {m}  n={int(row['count']):>2}  netR={row['sum']:>+6.1f}  {bar}")

if __name__=="__main__":
    ap0=argparse.ArgumentParser(add_help=False); ap0.add_argument("which",nargs="?",default="all")
    pre,_=ap0.parse_known_args()
    if pre.which=="h1final":
        ap=argparse.ArgumentParser(); ap.add_argument("which"); ap.add_argument("--tf",type=int,default=5)
        h1_final(ap.parse_args())
    elif pre.which=="h1rob":
        ap=argparse.ArgumentParser(); ap.add_argument("which"); ap.add_argument("--tf",type=int,default=5)
        h1_robust(ap.parse_args())
    elif pre.which=="h1":
        ap=argparse.ArgumentParser(); ap.add_argument("which"); ap.add_argument("--tf",type=int,default=5)
        ap.add_argument("--maker",action="store_true"); ap.add_argument("--overlays",action="store_true")
        h1_sweep(ap.parse_args())
    else:
        main()
