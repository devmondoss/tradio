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

FEE_MAKER=L2.FEE_MAKER; FEE_TAKER=L2.FEE_TAKER; CAP0, RISK = 500.0, 0.01

PARQUETS = {
    "BTCUSDT": Path(__file__).parent.parent / "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
    "ETHUSDT": Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet"),
    "SOLUSDT": Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"),
}

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

def gen_naked_poc(svp_naked, tol=0.002):
    """Fade en Naked POC: POC de sesion previa no revisitado = iman estructural.
    Logica: cuando el precio toca por primera vez un POC no visitado, hay liquidez
    atrapada ahi -> reversion de alta probabilidad. Stop: 0.6xATR mas alla del nivel.
    Target: siguiente nivel estructural (igual que H5/H21)."""
    def g(a, i):
        if not svp_naked: return
        day_int = int(a.ts[i]) // 86_400_000
        pocs = svp_naked.get(day_int, [])
        if not pocs: return
        out = []
        for poc in pocs:
            if not np.isfinite(poc): continue
            # LONG: precio cae hasta naked POC (soporte no visitado) y cierra arriba del open
            if abs(a.l[i] - poc) / poc <= tol and a.c[i] > a.o[i]:
                stop = poc - 0.6 * a.atr[i]
                tp1, tp2 = L2.struct_target(a, i, "long", poc)
                if np.isfinite(tp2):
                    out.append(("long", poc, stop, tp1, tp2, "naked_poc"))
            # SHORT: precio sube hasta naked POC (resistencia no visitada) y cierra abajo del open
            if abs(a.h[i] - poc) / poc <= tol and a.c[i] < a.o[i]:
                stop = poc + 0.6 * a.atr[i]
                tp1, tp2 = L2.struct_target(a, i, "short", poc)
                if np.isfinite(tp2):
                    out.append(("short", poc, stop, tp1, tp2, "naked_poc"))
        return out
    return g

def _is_chop(reg):
    return str(reg).lower() in ("chop","range","balance","consolidation")

def _micro_score(a, i, side):
    """Score 0-4 de microestructura para entrada en fade.
    Fades: queremos absorcion activa en el nivel + sesgo de libro favorable.
      OBI > 0 para long (mas bids) | OBI < 0 para short (mas asks)      +1
      VR > 1.5 (volumen elevado = nivel significativo, actividad real)   +1
      fp_absorb_buy/sell (footprint: compradores/vendedores absorben)    +1
      DZ < -0.5 para long (presion vendedora = buyers absorben sells)   +1
           DZ > +0.5 para short (presion compradora = sellers absorben)
    Threshold recomendado: >= 2 (no filtrar excesivo, pero confirmar 2 de 4)."""
    score = 0
    obi = a.obi5_mean[i] if np.isfinite(a.obi5_mean[i]) else 0.0
    if side == "long"  and obi > 0.02:  score += 1
    if side == "short" and obi < -0.02: score += 1
    if np.isfinite(a.vr[i]) and a.vr[i] > 1.5: score += 1
    if side == "long"  and bool(a.fp_absorb_buy[i]):  score += 1
    if side == "short" and bool(a.fp_absorb_sell[i]): score += 1
    if np.isfinite(a.dz[i]):
        if side == "long"  and a.dz[i] < -0.5: score += 1
        if side == "short" and a.dz[i] > +0.5: score += 1
    return score

def run(a, gens, timeout_min, volfilter, m1, tf_min, margin=2.0, stop_floor_pct=0.0, min_tp1_pct=0.0,
        system="A", trail_atr=4.0, min_tp1_rr=2.5,
        svp_dayvp=None, svp_naked=None, m1_cvd=None, cvd_reversal_pct=0.35,
        lvn_filter=False, micro_min=0, cvd_after_partial=False, tp2_cap_r=0.0):
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
                if tp2_cap_r > 0:
                    cap = entry + tp2_cap_r*risk if side=="long" else entry - tp2_cap_r*risk
                    if (side=="long" and tp2 > cap) or (side=="short" and tp2 < cap):
                        tp2 = cap
                        if tp1 is not None and not (min(entry,tp2) < tp1 < max(entry,tp2)):
                            tp1 = None
                if abs(tp2-entry)/risk < 1.2: continue
                # FILTRO LVN: solo operar si hay zona de bajo volumen entre entry y target
                if lvn_filter and svp_dayvp:
                    day_int = int(a.ts[i]) // 86_400_000
                    if not SVP.lvn_in_path(svp_dayvp, day_int, entry, tp2): continue
                # FILTRO MICROESTRUCTURA: absorcion + OBI + VR + DZ
                if micro_min > 0 and _micro_score(a, i, side) < micro_min: continue
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
                    cur_stop=stop; realized=0.0; rem=1.0; filled1=False; j_partial=-1
                    for j in range(j0, min(jend,len(m1ts))):
                        if side=="long":
                            if m1l[j]<=cur_stop:
                                realized+=rem*((cur_stop-entry)/risk); exit_px=cur_stop
                                reason=("breakeven" if filled1 else "stop"); exit_ts=m1ts[j]; break
                            if not filled1 and take_partial and m1h[j]>=tp1:
                                realized+=p1*((tp1-entry)/risk); rem-=p1; filled1=True; cur_stop=entry; j_partial=j
                            if filled1 and cvd_after_partial and m1_cvd is not None and j_partial>=0:
                                if SVP.cvd_exit_check(m1_cvd, j_partial, j, "long", reversal_pct=0.50, min_swing=200.0):
                                    exit_px=m1c[j]; reason="cvd_partial"; exit_ts=m1ts[j]
                                    realized+=rem*((m1c[j]-entry)/risk); break
                            if m1h[j]>=tp2:
                                realized+=rem*((tp2-entry)/risk); exit_px=tp2; reason="target"; exit_ts=m1ts[j]; break
                        else:
                            if m1h[j]>=cur_stop:
                                realized+=rem*((entry-cur_stop)/risk); exit_px=cur_stop
                                reason=("breakeven" if filled1 else "stop"); exit_ts=m1ts[j]; break
                            if not filled1 and take_partial and m1l[j]<=tp1:
                                realized+=p1*((entry-tp1)/risk); rem-=p1; filled1=True; cur_stop=entry; j_partial=j
                            if filled1 and cvd_after_partial and m1_cvd is not None and j_partial>=0:
                                if SVP.cvd_exit_check(m1_cvd, j_partial, j, "short", reversal_pct=0.50, min_swing=200.0):
                                    exit_px=m1c[j]; reason="cvd_partial"; exit_ts=m1ts[j]
                                    realized+=rem*((entry-m1c[j])/risk); break
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
                mscore  = _micro_score(a, i, side)
                m_obi   = float(a.obi5_mean[i])   if np.isfinite(a.obi5_mean[i])   else None
                m_vr    = float(a.vr[i])           if np.isfinite(a.vr[i])           else None
                m_dz    = float(a.dz[i])           if np.isfinite(a.dz[i])           else None
                m_cvds  = float(a.cvd_slope[i])    if np.isfinite(a.cvd_slope[i])    else None
                m_abs   = bool(a.fp_absorb_buy[i] if side=="long" else a.fp_absorb_sell[i])
                trades.append(dict(tsMs=int(a.ts[i]), dir=("Long" if side=="long" else "Short"),
                                   entry=float(entry), stop=float(stop), target=tgt,
                                   tp1=tp1_out, targetName=tname,
                                   exit=float(exit_px), resultR=float(r), reason=reason,
                                   kind=kind, closedAt=int(exit_ts), stopPct=float(100*risk/entry),
                                   regime=str(a.reg[i]), gestion=gestion,
                                   mscore=mscore, obi=m_obi, vr=m_vr, dz=m_dz,
                                   cvdSlope=m_cvds, absorb=m_abs))
                cool=i+6; dcount[d]=dcount.get(d,0)+1; break
    return sorted(trades, key=lambda t:t["tsMs"])

def to_trade_json(raw, i, sym="BTCUSDT"):
    risk_usd=CAP0*RISK
    return {
        "idx":i+1, "id":f"liq-{i+1}", "sym":sym, "dir":raw["dir"], "session":"",
        "score":None, "entry":raw["entry"], "stop":raw["stop"], "target":raw["target"],
        "tp1":raw.get("tp1"), "targetName":raw.get("targetName"),
        "exit":raw["exit"], "resultR":round(raw["resultR"],4), "pnlUsd":round(raw["resultR"]*risk_usd,2),
        "riskUsd":risk_usd, "stopPct":round(raw["stopPct"],3), "equity":0.0,
        "reason":raw["reason"], "tsMs":raw["tsMs"], "ts":raw["tsMs"]//1000,
        "closedAt":pd.to_datetime(raw["closedAt"],unit="ms",utc=True).isoformat(),
        "regime":raw["regime"], "gestion":raw.get("gestion","fade"), "sessionPhase":"", "evidence":[raw["kind"]],
        "confluenceFlags":[raw["kind"], raw.get("gestion","fade"), f"micro={raw.get('mscore',0)}/4"],
        "vetoReason":"", "cvdInRange":None,
        "vr":raw.get("vr"), "priceVsVwap":None, "funding":None,
        "cvdSlope":raw.get("cvdSlope"), "obi":raw.get("obi"), "dz":raw.get("dz"),
        "rangePct":None, "rangeBars":None, "rangeTouch":None, "durationMin":None, "isOpen":False,
        "mscore":raw.get("mscore"), "absorb":raw.get("absorb"),
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
                    help="TP1 debe estar a >=N*riesgo para tomar la parcial. Default 2.3.")
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--no-naked-poc", action="store_true",
                    help="Desactiva gen_naked_poc() (activo por defecto cuando hay cache SVP).")
    ap.add_argument("--lvn-filter", action="store_true",
                    help="Solo operar fades con LVN entre entry y target (experimental, suele reducir netR).")
    ap.add_argument("--micro-min", type=int, default=0,
                    help="Score minimo de microestructura para tomar un trade (0=sin filtro, 2=recomendado).")
    ap.add_argument("--cvd-partial", action="store_true",
                    help="Salida CVD post-parcial: cuando el parcial esta tomado (BE+), salir si CVD revierte 50%%.")
    ap.add_argument("--tp2-cap", type=float, default=0.0,
                    help="Cap target a N×riesgo (0=sin cap). Default 2.25 para SOLUSDT auto.")
    args=ap.parse_args()

    # Monkeypatch datos según símbolo
    sym = args.symbol.upper()
    if sym not in PARQUETS:
        print(json.dumps({"error": f"símbolo no soportado: {sym}"})); sys.exit(1)
    if sym != "BTCUSDT":
        p = PARQUETS[sym]
        if not p.exists():
            print(json.dumps({"error": f"parquet no encontrado: {p}"})); sys.exit(1)
        L2.M1 = p
    full0 = L2.TICK_MS if sym == "BTCUSDT" else 0

    tp2_cap_r = args.tp2_cap if args.tp2_cap > 0 else (2.25 if sym == "SOLUSDT" else 0.0)

    t=L2.load2(args.tf, start_ms=full0)
    if args.days and args.days>0:
        cutoff=t.ts_ms.max()-args.days*86_400_000
        t=t[t.ts_ms>=cutoff].reset_index(drop=True)
    if args.info:
        avail=int((t.ts_ms.max()-t.ts_ms.min())/86_400_000)
        lbl=pd.to_datetime(t.ts_ms.min(),unit="ms",utc=True).strftime("%d %b %Y")
        print(json.dumps({"available_days":avail,"start_label":lbl})); return

    a=L2.A2(t)
    m1=L2.load_m1_exit(start_ms=full0)

    # SVP cache solo disponible para BTC
    svp_dayvp = SVP.load_dayvp() if sym == "BTCUSDT" else None
    svp_naked = SVP.load_naked_poc() if sym == "BTCUSDT" else None
    m1_cvd    = SVP.load_m1_cvd(start_ms=full0) if svp_dayvp else None

    gens=[L2.gen_h5(), L2.gen_h21(), L2.gen_h21_short()]
    if not args.no_naked_poc and svp_naked:
        gens.append(gen_naked_poc(svp_naked))
    sys_arg = "AB" if args.system == "C" else args.system
    raws=run(a, gens, timeout_min=24*60, volfilter=not args.no_volfilter, m1=m1, tf_min=args.tf,
             stop_floor_pct=args.stop_floor, min_tp1_pct=args.min_range, system=sys_arg,
             min_tp1_rr=args.min_tp1_rr,
             svp_dayvp=svp_dayvp, svp_naked=svp_naked, m1_cvd=m1_cvd,
             lvn_filter=args.lvn_filter,
             micro_min=args.micro_min, cvd_after_partial=args.cvd_partial,
             tp2_cap_r=tp2_cap_r)
    trades=[to_trade_json(r,i,sym=sym) for i,r in enumerate(raws)]
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
