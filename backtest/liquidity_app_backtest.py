"""
liquidity_app_backtest.py — Backtest de la cartera de LIQUIDEZ para la app de review (trade-lab)
=================================================================================================
Emite los trades en el shape `Trade` que espera apps/trade-lab (entry/stop/target/exit/tsMs/dir/
resultR...) para visualizarlos en el chart. Misma lógica que la cartera consolidada:
  • fade del área-valor del día previo (VAH/VAL/POC)
  • POC del order block previo
  • POC defendido
Órdenes LÍMITE MAKER (fee 4bps), filtro de volatilidad (ATR>mediana móvil, por defecto ON),
fill next-bar, selección adversa 2bps, gestión stop/target/timeout. Riesgo fijo $500@1%.

Uso (lo invoca vite.config):
  python backtest/liquidity_app_backtest.py --info
  python backtest/liquidity_app_backtest.py --days 180 --json [--no-volfilter]
"""
import argparse, json, sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
import _session_vp as SVP

FEE_MAKER=L2.FEE_MAKER; FEE_TAKER=L2.FEE_TAKER; CAP0, RISK = 500.0, 0.01; SYM="BTCUSDT"

def day_vp_prev(m5_df):
    """VP por día (close-ponderado, bins $5) congelado: dict date->(poc,vah,val) del día PREVIO."""
    df=m5_df.copy(); df["date"]=df.ts_ms//86_400_000
    out={}
    days=sorted(df.date.unique())
    prev=None
    for d in days:
        if prev is not None:
            sub=df[df.date==prev]; p=sub.close.values; v=sub.volume.values
            if len(p):
                lo=np.floor(p.min()/5)*5; idx=((p-lo)//5).astype(int)
                vol=np.bincount(idx,weights=v); lv=lo+np.arange(len(vol))*5
                poc=lv[vol.argmax()]; order=np.argsort(vol)[::-1]; tot=vol.sum(); cum=0; sel=[]
                for k in order:
                    sel.append(k); cum+=vol[k]
                    if cum>=0.70*tot: break
                va=lv[sel]; out[d]=(poc,va.max(),va.min())
        prev=d
    return out

def gen_area_valor(a, prevvp):
    """Fade del área-valor del día previo. Stop = máx/mín del día previo (como H1 'stop=max'),
    target = extremo opuesto del área de valor."""
    def g(_a,i):
        d=int(a.day[i]); vp=prevvp.get(d)
        if not vp: return
        poc,vah,val=vp
        pdh=a.prev_day_high[i]; pdl=a.prev_day_low[i]
        out=[]
        if np.isfinite(pdh) and pdh>vah:
            out.append(("short",vah,pdh,poc,val,"area_valor"))
        if np.isfinite(pdl) and pdl<val:
            out.append(("long", val,pdl,poc,vah,"area_valor"))
        return out
    return g

def _is_chop(reg):
    return str(reg).lower() in ("chop","range","balance","consolidation")

def run(a, gens, timeout_min, volfilter, m1, tf_min, margin=2.0, stop_floor_pct=0.0, min_tp1_pct=0.0,
        system="A", trail_atr=4.0, min_tp1_rr=2.5,
        svp_dayvp=None, svp_naked=None, m1_cvd=None, cvd_reversal_pct=0.35):
    """Entrada decidida en el TF de 'a'; SALIDA simulada en M1 (honesto, sin ambigüedad intrabar).
    system='A' → FADE: parcial 50% en TP1 (solo si TP1 ≥ min_tp1_rr×riesgo) → BE → target estructural.
    system='AB'/'C' → ENRUTA por régimen: Chop→fade · Tendencia→trailing stop (monta la continuación).
    stop_floor_pct: piso de stop. min_tp1_pct: rango mínimo al TP1. min_tp1_rr: parcial mínima en R."""
    m1ts,m1h,m1l,m1c=m1; bar_ms=tf_min*60_000
    atr_med=pd.Series(a.atr).rolling(500,min_periods=50).median().shift(1).values
    mk=FEE_MAKER/2.0; tk=FEE_TAKER/2.0
    trades=[]
    for g in gens:
        cool=0; dcount={}
        for i in range(60,a.n-1):
            if i<cool or a.atr[i]<=0: continue
            if volfilter and not (np.isfinite(atr_med[i]) and a.atr[i]>atr_med[i]): continue
            d=int(a.day[i])
            if dcount.get(d,0)>=2: continue
            for side,lvl,stop,tp1,tp2,kind in (g(a,i) or []):
                if not np.isfinite([lvl,stop,tp2]).all(): continue
                ref=a.c[i-1]
                if side=="long" and not (lvl<ref): continue
                if side=="short" and not (lvl>ref): continue
                if side=="long" and not (a.l[i] <= lvl - margin/1e4*lvl): continue
                if side=="short" and not (a.h[i] >= lvl + margin/1e4*lvl): continue
                entry=lvl
                if stop_floor_pct>0:
                    min_risk=stop_floor_pct/100.0*entry
                    if abs(entry-stop)<min_risk:
                        stop = entry-min_risk if side=="long" else entry+min_risk
                risk=abs(entry-stop)
                if risk<=0: continue
                if side=="long" and not (stop<entry<tp2): continue
                if side=="short" and not (tp2<entry<stop): continue
                if abs(tp2-entry)/risk < 1.2: continue
                use_fade = (system=="A") or (system=="AB" and _is_chop(a.reg[i]))
                j0=np.searchsorted(m1ts, a.ts[i]+bar_ms)
                jend=np.searchsorted(m1ts, a.ts[i]+bar_ms+timeout_min*60_000)
                if use_fade:
                    # RANGO MÍNIMO: TP1 debe representar un nivel real (no migaja).
                    if tp1 is None: continue
                    if min_tp1_pct>0 and 100*abs(tp1-entry)/entry < min_tp1_pct: continue
                    # PARCIAL solo si TP1 ≥ min_tp1_rr × riesgo.
                    # Con min_tp1_rr=2.5: parcial de 0.5×2.5R = 1.25R → BE → neto ≥1R antes de fees.
                    # Si TP1 < min_tp1_rr → no hay parcial: trade va entero a target (más WR potencial).
                    take_partial = abs(tp1-entry)/risk >= min_tp1_rr
                    p1 = 0.5 if take_partial else 0.0
                    # --- FADE: parcial en TP1 → breakeven → target estructural ---
                    exit_px=None; exit_ts=None; reason="timeout"
                    cur_stop=stop; realized=0.0; rem=1.0; filled1=False
                    for j in range(j0, min(jend,len(m1ts))):
                        if side=="long":
                            if m1l[j]<=cur_stop:
                                realized+=rem*((cur_stop-entry)/risk); exit_px=cur_stop
                                reason=("breakeven" if filled1 else "stop"); exit_ts=m1ts[j]; break
                            if not filled1 and take_partial and m1h[j]>=tp1:
                                realized+=p1*((tp1-entry)/risk); rem-=p1; filled1=True; cur_stop=entry
                            if m1h[j]>=tp2:
                                realized+=rem*((tp2-entry)/risk); exit_px=tp2; reason="target"; exit_ts=m1ts[j]; break
                        else:
                            if m1h[j]>=cur_stop:
                                realized+=rem*((entry-cur_stop)/risk); exit_px=cur_stop
                                reason=("breakeven" if filled1 else "stop"); exit_ts=m1ts[j]; break
                            if not filled1 and take_partial and m1l[j]<=tp1:
                                realized+=p1*((entry-tp1)/risk); rem-=p1; filled1=True; cur_stop=entry
                            if m1l[j]<=tp2:
                                realized+=rem*((entry-tp2)/risk); exit_px=tp2; reason="target"; exit_ts=m1ts[j]; break
                    if exit_px is None:
                        jj=min(jend,len(m1ts))-1
                        if jj<=j0: continue
                        px=m1c[jj]; realized+=rem*(((px-entry) if side=="long" else (entry-px))/risk)
                        exit_px=px; exit_ts=m1ts[jj]
                    exit_side=mk if reason=="target" else tk
                    fee_r=(mk*1.0 + (mk*p1 if filled1 else 0.0) + exit_side*rem)*entry/risk
                    r=realized-fee_r
                    if side=="long":
                        opts={"weekly_high":a.weekly_high[i],"prev_day_high":a.prev_day_high[i],
                              "swing_high":a.swing_high_50[i],"vp_vah":a.vp_vah[i]}
                    else:
                        opts={"weekly_low":a.weekly_low[i],"prev_day_low":a.prev_day_low[i],
                              "swing_low":a.swing_low_50[i],"vp_val":a.vp_val[i]}
                    tname=min((k2 for k2 in opts if np.isfinite(opts[k2])),
                              key=lambda k2: abs(opts[k2]-tp2), default="estructural")
                    tgt=float(tp2); tp1_out=float(tp1); gestion="fade"
                else:
                    # --- TRAILING: monta la continuación (best ± trail_atr·ATR) · maker in / taker out ---
                    atr0=a.atr[i]; fee_r=(mk+tk)*entry/risk
                    best=entry; trail=stop; exit_px=None; exit_ts=None; reason="trail"
                    for j in range(j0, min(jend,len(m1ts))):
                        if side=="long":
                            best=max(best,m1h[j]); trail=max(trail,best-trail_atr*atr0)
                            if m1l[j]<=trail: exit_px=trail; exit_ts=m1ts[j]; break
                        else:
                            best=min(best,m1l[j]); trail=min(trail,best+trail_atr*atr0)
                            if m1h[j]>=trail: exit_px=trail; exit_ts=m1ts[j]; break
                    if exit_px is None:
                        jj=min(jend,len(m1ts))-1
                        if jj<=j0: continue
                        exit_px=m1c[jj]; exit_ts=m1ts[jj]
                    r=(((exit_px-entry) if side=="long" else (entry-exit_px))/risk)-fee_r
                    tgt=float(exit_px); tp1_out=None; tname="trailing"; gestion="trail"
                trades.append(dict(tsMs=int(a.ts[i]), dir=("Long" if side=="long" else "Short"),
                                   entry=float(entry), stop=float(stop), target=tgt,
                                   tp1=tp1_out, targetName=tname,
                                   exit=float(exit_px), resultR=float(r), reason=reason,
                                   kind=kind, closedAt=int(exit_ts), stopPct=float(100*risk/entry),
                                   regime=str(a.reg[i]), gestion=gestion))
                cool=i+6; dcount[d]=dcount.get(d,0)+1; break
    return sorted(trades, key=lambda t:t["tsMs"])

def to_trade_json(raw, i):
    risk_usd=CAP0*RISK
    return {
        "idx":i+1, "id":f"liq-{i+1}", "sym":SYM, "dir":raw["dir"], "session":"",
        "score":None, "entry":raw["entry"], "stop":raw["stop"], "target":raw["target"],
        "tp1":raw.get("tp1"), "targetName":raw.get("targetName"),
        "exit":raw["exit"], "resultR":round(raw["resultR"],4), "pnlUsd":round(raw["resultR"]*risk_usd,2),
        "riskUsd":risk_usd, "stopPct":round(raw["stopPct"],3), "equity":0.0,
        "reason":raw["reason"], "tsMs":raw["tsMs"], "ts":raw["tsMs"]//1000,
        "closedAt":pd.to_datetime(raw["closedAt"],unit="ms",utc=True).isoformat(),
        "regime":raw["regime"], "gestion":raw.get("gestion","fade"), "sessionPhase":"", "evidence":[raw["kind"]],
        "confluenceFlags":[raw["kind"], raw.get("gestion","fade")], "vetoReason":"", "cvdInRange":None, "vr":None,
        "priceVsVwap":None, "funding":None, "cvdSlope":None, "obi":None, "dz":None,
        "rangePct":None, "rangeBars":None, "rangeTouch":None, "durationMin":None, "isOpen":False,
    }

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=540)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--info", action="store_true")
    ap.add_argument("--no-volfilter", action="store_true")
    ap.add_argument("--tf", type=int, default=15)   # M15: targets estructurales sobre niveles reales
    ap.add_argument("--stop-floor", type=float, default=0.15,
                    help="piso de stop %% (ensancha stops minúsculos; 0 = sin piso). Default 0.15.")
    ap.add_argument("--min-range", type=float, default=0.5,
                    help="rango mínimo al primer objetivo %% (mata migajas; 0 = sin mínimo). Default 0.5.")
    ap.add_argument("--system", choices=["A","AB","C"], default="A",
                    help="A = solo fader (rangos). AB/C = enrutado por régimen (fade en chop, trailing en tendencia).")
    ap.add_argument("--min-tp1-rr", type=float, default=2.3,
                    help="TP1 debe estar a ≥N×riesgo para tomar la parcial. Default 2.5 → parcial ≥1.25R, neto ≥1R.")
    ap.add_argument("--symbol", default="BTCUSDT")
    args=ap.parse_args()

    full0=L2.TICK_MS   # era tick VERIFICADA (2025-06-19+, 365d). Pre-tick era OHLCV no verificado.
    t=L2.load2(args.tf, start_ms=full0)
    if args.days and args.days>0:
        cutoff=t.ts_ms.max()-args.days*86_400_000
        t=t[t.ts_ms>=cutoff].reset_index(drop=True)
    if args.info:
        avail=int((t.ts_ms.max()-t.ts_ms.min())/86_400_000)
        lbl=pd.to_datetime(t.ts_ms.min(),unit="ms",utc=True).strftime("%d %b %Y")
        print(json.dumps({"available_days":avail,"start_label":lbl})); return

    a=L2.A2(t)
    m1=L2.load_m1_exit(start_ms=full0)   # OHLC M1 para salidas honestas (sin ambigüedad intrabar)
    # Footprint: session VP (LVN/HVN/Naked POC) + CVD para targets y exit signals
    svp_dayvp = SVP.load_dayvp()
    svp_naked = SVP.load_naked_poc()
    m1_cvd    = SVP.load_m1_cvd(start_ms=full0) if svp_dayvp else None
    if not svp_dayvp:
        import sys as _sys; print("[footprint] sin cache → ejecuta: python backtest/_session_vp.py --build", file=_sys.stderr)
    # Componentes POC (provisión de liquidez en niveles de volumen): replican el edge validado
    # exacto. El fade de área-valor (H1) requiere su motor completo (clasificación de día +
    # VP congelado) y se valida aparte en backtest/_consolidated.py; no se incluye en el visual.
    gens=[L2.gen_h5(), L2.gen_h21(), L2.gen_h21_short()]   # +mirror corto del POC defendido (balancea long/short)
    sys_arg = "AB" if args.system == "C" else args.system
    raws=run(a, gens, timeout_min=24*60, volfilter=not args.no_volfilter, m1=m1, tf_min=args.tf,
             stop_floor_pct=args.stop_floor, min_tp1_pct=args.min_range, system=sys_arg,
             min_tp1_rr=args.min_tp1_rr,
             svp_dayvp=svp_dayvp, svp_naked=svp_naked, m1_cvd=m1_cvd)
    trades=[to_trade_json(r,i) for i,r in enumerate(raws)]
    eq=CAP0
    for tr in trades: eq+=tr["pnlUsd"]; tr["equity"]=round(eq,2)
    closed=[tr for tr in trades if not tr["isOpen"]]
    wins=sum(1 for tr in closed if tr["resultR"]>0)
    totalR=sum(tr["resultR"] for tr in closed)
    actual=int((t.ts_ms.max()-t.ts_ms.min())/86_400_000)
    print(json.dumps({
        "trades":trades, "n":len(closed), "wins":wins,
        "total_r":round(totalR,2), "avg_r":round(totalR/len(closed),3) if closed else 0,
        "wr_pct":round(100*wins/len(closed),1) if closed else 0,
        "equity":round(eq,2), "actual_days":actual, "micro_start":None, "longs_enabled":True,
    }))

if __name__=="__main__": main()
