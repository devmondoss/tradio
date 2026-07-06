"""
_pnl_3assets.py — PnL consolidado BTC + ETH + SOL
Config ganadora: max_day=4, cool=3, scaled entry 50/50
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _listas import OOS_MS, FEE_MAKER, FEE_TAKER
from _listas2 import struct_target
from _audit_mirror import gen_h21_short as _gen_h21_short_orig

ROOT   = Path(__file__).parent.parent
MK,TK  = FEE_MAKER/2, FEE_TAKER/2
BAR_MS = 15*60_000

CAPITAL  = 500
LEVERAGE = 10
RISK_PCT = 0.01
RISK_USD = CAPITAL * RISK_PCT   # $5/trade
FILL     = 0.65
SLIP     = 0.15

ASSETS = {
    "BTC": dict(
        m1     = ROOT/"data/bybit-perp/processed/btcusdt_perp_m1.parquet",
        margin = 2.0, timeout = 24*60, use_h5 = True,
        oos_days = 107, is_days = 417,
    ),
    "ETH": dict(
        m1     = Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet"),
        margin = 6.0, timeout = 24*60, use_h5 = True,
        oos_days = 109, is_days = 248,
    ),
    "SOL": dict(
        m1     = Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"),
        margin = 2.0, timeout = 6*60,  use_h5 = False,
        oos_days = 107, is_days = 246,
    ),
}

# ── Generadores ──────────────────────────────────────────────────────────────

def gen_h21(K=15, tol=0.002):
    def g(a, i):
        if i < K: return
        win = a.fp_poc[i-K:i]; win = win[np.isfinite(win)]
        if len(win) < 3: return
        lvl = np.median(win)
        if np.sum(np.abs(a.l[i-K:i]-lvl)/lvl<=tol)>=2 and a.c[i]>a.c[i-1] and abs(a.l[i]-lvl)/lvl<=tol:
            stop = lvl - 0.6*a.atr[i]
            tp1,tp2 = struct_target(a, i, "long", lvl)
            if np.isfinite(tp2): return [("long", lvl, stop, tp1, tp2, "H21")]
    return g

def gen_h21s(K=15, tol=0.002):
    def g(a, i):
        if i < K: return
        win = a.fp_poc[i-K:i]; win = win[np.isfinite(win)]
        if len(win) < 3: return
        lvl = np.median(win)
        if np.sum(np.abs(a.h[i-K:i]-lvl)/lvl<=tol)>=2 and a.c[i]<a.c[i-1] and abs(a.h[i]-lvl)/lvl<=tol:
            stop = lvl + 0.6*a.atr[i]
            tp1,tp2 = struct_target(a, i, "short", lvl)
            if np.isfinite(tp2): return [("short", lvl, stop, tp1, tp2, "H21s")]
    return g

# ── Motor con scaled entry ───────────────────────────────────────────────────

def run_scaled(a, gens, m1, timeout_min=24*60, margin=2.0, cooldown=3, max_day=4):
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
                if side=="long"  and not (lvl<ref): continue
                if side=="short" and not (lvl>ref): continue
                mf=margin/1e4
                if side=="long"  and not (a.l[i]<=lvl*(1-mf)): continue
                if side=="short" and not (a.h[i]>=lvl*(1+mf)): continue

                entry=lvl; atr0=a.atr[i]
                sf=0.15/100*entry
                if abs(entry-stop)<sf: stop=entry-sf if side=="long" else entry+sf
                risk=abs(entry-stop)
                if risk<=0 or abs(tp2-entry)/risk<1.2: continue
                if tp1 and 100*abs(tp1-entry)/entry<0.5: continue

                # orden 2: 0.3 ATR mas profundo
                entry2 = lvl - 0.3*atr0 if side=="long" else lvl + 0.3*atr0
                filled2 = (a.l[i]<=entry2) if side=="long" else (a.h[i]>=entry2)

                chop = str(a.reg[i]).lower() in ("chop","range","balance","consolidation")
                j0   = np.searchsorted(m1ts,a.ts[i]+BAR_MS)
                jend = np.searchsorted(m1ts,a.ts[i]+BAR_MS+timeout_min*60_000)

                def sim(ent, stp):
                    rk = abs(ent-stp)
                    if rk<=0: return None
                    if chop:
                        cur=stp; realized=0.0; rem=1.0; f1=False
                        p1=0.5 if tp1 else 0.0; reason="timeout"
                        for j in range(j0, min(jend,len(m1ts))):
                            if side=="long":
                                if m1l[j]<=cur: realized+=rem*((cur-ent)/rk); reason="be" if f1 else "stop"; break
                                if not f1 and tp1 and m1h[j]>=tp1: realized+=p1*((tp1-ent)/rk); rem-=p1; f1=True; cur=ent
                                if m1h[j]>=tp2: realized+=rem*((tp2-ent)/rk); reason="target"; break
                            else:
                                if m1h[j]>=cur: realized+=rem*((ent-cur)/rk); reason="be" if f1 else "stop"; break
                                if not f1 and tp1 and m1l[j]<=tp1: realized+=p1*((ent-tp1)/rk); rem-=p1; f1=True; cur=ent
                                if m1l[j]<=tp2: realized+=rem*((ent-tp2)/rk); reason="target"; break
                        else:
                            jj=min(jend,len(m1ts))-1
                            if jj<=j0: return None
                            px=m1c[jj]; realized+=rem*(((px-ent) if side=="long" else (ent-px))/rk)
                        es=MK if reason=="target" else TK
                        fr=(MK+(MK*p1 if f1 else 0)+es*rem)*ent/rk
                        return realized-fr
                    else:
                        fr=(MK+TK)*ent/rk; best=ent; trail=stp
                        for j in range(j0, min(jend,len(m1ts))):
                            if side=="long":
                                best=max(best,m1h[j]); trail=max(trail,best-4*atr0)
                                if m1l[j]<=trail: return (trail-ent)/rk-fr
                            else:
                                best=min(best,m1l[j]); trail=min(trail,best+4*atr0)
                                if m1h[j]>=trail: return (ent-trail)/rk-fr
                        jj=min(jend,len(m1ts))-1
                        if jj<=j0: return None
                        px=m1c[jj]; return ((px-ent) if side=="long" else (ent-px))/rk-fr

                r1 = sim(entry, stop)
                if r1 is None: continue

                if filled2:
                    r2 = sim(entry2, stop)
                    res = (r1*0.5 + r2*0.5) if r2 is not None else r1
                else:
                    res = r1

                trades.append(dict(
                    bar_ts=int(a.ts[i]), side=side, r=res,
                    oos=int(a.ts[i])>=OOS_MS, kind=kind,
                    regime="chop" if chop else "trend",
                    date=pd.Timestamp(int(a.ts[i]),unit='ms').strftime('%Y-%m'),
                    scaled_both=filled2,
                ))
                cool=i+cooldown; dcount[d]=dcount.get(d,0)+1; break
    return pd.DataFrame(trades)


# ── Reporte ──────────────────────────────────────────────────────────────────

all_oos = []

print(f"\n  Capital: ${CAPITAL} x {LEVERAGE}x lev = ${CAPITAL*LEVERAGE} buying power")
print(f"  Riesgo: ${RISK_USD}/trade | Fill: {FILL:.0%} | Slippage: {SLIP:.0%}")
print(f"  Riesgo efectivo por trade: ${RISK_USD*FILL*(1-SLIP):.2f}")

for sym, cfg in ASSETS.items():
    if not cfg["m1"].exists():
        print(f"\n{sym}: archivo no encontrado, skip"); continue

    print(f"\nCargando {sym}...", flush=True)
    L2.M1 = cfg["m1"]
    t = L2.load2(15, start_ms=0); a = L2.A2(t); m1 = L2.load_m1_exit(start_ms=0)

    gens = []
    if cfg["use_h5"]: gens.append(L2.gen_h5())
    gens += [gen_h21(), gen_h21s()]

    # baseline
    from _entry_improve import run as run_base
    # rebuild baseline (sin scaled)
    def run_base_clean(a, gens, m1, timeout_min, margin, cooldown=6, max_day=2):
        # simple wrapper del run base sin palancas
        return run_scaled.__wrapped__(a,gens,m1,timeout_min,margin,cooldown,max_day) if hasattr(run_scaled,'__wrapped__') else None

    df_base_local = run_scaled(a, gens, m1, timeout_min=cfg["timeout"],
                               margin=cfg["margin"], cooldown=6, max_day=2)
    df_new        = run_scaled(a, gens, m1, timeout_min=cfg["timeout"],
                               margin=cfg["margin"], cooldown=3, max_day=4)

    oos_days = cfg["oos_days"]; is_days = cfg["is_days"]

    print(f"\n{'='*72}")
    print(f"  {sym}")
    print(f"{'='*72}")
    print(f"  {'Config':<22} | {'n_oos':>5} | {'t/d':>4} | {'IS_avg':>7} | {'OOS_avg':>7} | {'WR':>6} | {'DD':>8} | {'PnL/y':>9}")
    print("  "+"-"*80)

    for lbl, df in [("Baseline (max2,cd6)", df_base_local), ("Nueva   (max4,cd3,sc)", df_new)]:
        oos=df[df.oos]; is_=df[~df.oos]
        if len(oos)<3: print(f"  {lbl:<22} | sin datos"); continue
        td=len(oos)/oos_days
        dd=(oos.r.cumsum()-oos.r.cumsum().cummax()).min()
        iavg=is_.r.mean() if len(is_)>0 else float('nan')
        pnl_y=oos.r.sum()*FILL*(1-SLIP)*RISK_USD/oos_days*365
        print(f"  {lbl:<22} | {len(oos):>5} | {td:>4.2f} | {iavg:>+7.3f} | {oos.r.mean():>+7.3f} | {100*(oos.r>0).mean():>5.1f}% | {dd:>+8.2f}R | ${pnl_y:>+8.0f}/y")

    # detalle mensual OOS nueva config
    oos=df_new[df_new.oos]
    print(f"\n  OOS mensual — {sym} (nueva config, fill={FILL:.0%}, slip={SLIP:.0%}):")
    print(f"  {'Mes':>8} | {'n':>4} | {'avgR':>7} | {'WR':>6} | {'PnL_R':>7} | {'PnL_$':>8} | {'sc%':>5}")
    print("  "+"-"*58)
    for mes, g in oos.groupby("date"):
        r_mes = g.r.sum()*FILL*(1-SLIP)
        pnl_mes = r_mes*RISK_USD
        sc_pct = 100*g.scaled_both.mean() if "scaled_both" in g.columns else 0
        print(f"  {mes:>8} | {len(g):>4} | {g.r.mean():>+7.3f} | {100*(g.r>0).mean():>5.1f}% | {r_mes:>+7.2f}R | ${pnl_mes:>+7.0f} | {sc_pct:>4.0f}%")
    tot_r=oos.r.sum()*FILL*(1-SLIP)
    print(f"  {'TOTAL':>8} | {len(oos):>4} | {oos.r.mean():>+7.3f} | {100*(oos.r>0).mean():>5.1f}% | {tot_r:>+7.2f}R | ${tot_r*RISK_USD:>+7.0f}")

    df_new["sym"] = sym
    all_oos.append(df_new[df_new.oos])

# ── CONSOLIDADO ──────────────────────────────────────────────────────────────
if len(all_oos) >= 2:
    combined = pd.concat(all_oos, ignore_index=True)
    combined["date"] = pd.to_datetime(combined.bar_ts, unit='ms').dt.strftime('%Y-%m')

    print(f"\n{'='*72}")
    print("  CONSOLIDADO BTC + ETH + SOL — nueva config")
    print(f"{'='*72}")
    print(f"  Capital total comprometido: ${CAPITAL} (mismo capital rota entre activos)")
    print(f"  NOTA: trades simultaneos en distintos activos compiten por el mismo $RISK_USD")

    total_r_oos = sum(df.r.sum() for df in all_oos)
    total_n     = sum(len(df) for df in all_oos)
    total_days  = 107  # OOS aprox igual para los 3

    print(f"\n  {'Activo':>6} | {'n_oos':>5} | {'avgR':>7} | {'WR':>6} | {'PnL/y':>9}")
    print("  "+"-"*45)
    for df in all_oos:
        sym=df.sym.iloc[0]; od=107
        pnl_y=df.r.sum()*FILL*(1-SLIP)*RISK_USD/od*365
        print(f"  {sym:>6} | {len(df):>5} | {df.r.mean():>+7.3f} | {100*(df.r>0).mean():>5.1f}% | ${pnl_y:>+8.0f}/y")

    # escenarios capital
    print(f"\n  {'='*65}")
    print(f"  ESCENARIOS DE CAPITAL — PnL anual proyectado (fill={FILL:.0%}, slip={SLIP:.0%})")
    print(f"  {'Capital':>10} | {'Lev':>4} | {'Risk/trade':>10} | {'BTC/y':>8} | {'ETH/y':>8} | {'SOL/y':>8} | {'TOTAL/y':>9}")
    print("  "+"-"*68)

    # calcular R netos OOS por activo anualizado
    r_ann = {}
    for df in all_oos:
        sym=df.sym.iloc[0]
        r_ann[sym] = df.r.sum()*FILL*(1-SLIP)/107*365

    for cap, lev in [(500,10),(1000,10),(2000,10),(5000,5),(10000,3)]:
        risk = cap * 0.01
        totals = {sym: r_ann[sym]*risk for sym in r_ann}
        total  = sum(totals.values())
        row = "  ".join(f"${v:>+7.0f}" for v in totals.values())
        print(f"  ${cap:>9} | {lev:>4}x | ${risk:>9.2f} | {row} | ${total:>+8.0f}")

    # PnL mensual consolidado
    print(f"\n  PnL mensual consolidado (todos los activos, $500, 65% fill, 15% slip):")
    print(f"  {'Mes':>8} | {'n_tot':>5} | {'PnL_R':>7} | {'PnL_$':>8}")
    print("  "+"-"*38)
    mon_r = combined.groupby("date").r.sum() * FILL*(1-SLIP)
    mon_n = combined.groupby("date").r.count()
    for mes in sorted(mon_r.index):
        pnl_m = mon_r[mes]*RISK_USD
        print(f"  {mes:>8} | {mon_n[mes]:>5} | {mon_r[mes]:>+7.2f}R | ${pnl_m:>+7.0f}")
    tot=mon_r.sum()*RISK_USD
    print(f"  {'TOTAL':>8} | {total_n:>5} | {mon_r.sum():>+7.2f}R | ${tot:>+7.0f}")
