"""
_gen_new_levels.py — Generadores de señal en niveles clásicos S/R
=================================================================
Misma logica que H21 (limit maker, ATR filter, struct_target)
pero entrando en PDH/PDL, VAH/VAL, Asian H/L, Swing H/L, Weekly H/L.

Uso: python backtest/_gen_new_levels.py [--sym BTC|ETH|SOL]
"""
import argparse, sys
from pathlib import Path
import numpy as np, pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _listas import OOS_MS, FEE_MAKER, FEE_TAKER
from _audit_mirror import gen_h21_short
from _listas2 import struct_target

ROOT  = Path(__file__).parent.parent
MK,TK = FEE_MAKER/2, FEE_TAKER/2
BAR_MS = 15*60_000

ASSETS = {
    "BTC": dict(m1=ROOT/"data/bybit-perp/processed/btcusdt_perp_m1.parquet", margin=2.0, timeout=24*60),
    "ETH": dict(m1=Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet"), margin=6.0, timeout=24*60),
    "SOL": dict(m1=Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"), margin=2.0, timeout=6*60),
}

# ── Helpers ────────────────────────────────────────────────────────────────

def make_sr_gen(get_lvl_long, get_lvl_short, tag, tol=0.002, atr_stop=0.5, confirm_close=True):
    """
    Generador genérico S/R fade.
    get_lvl_long(a,i) → float  (soporte para long)
    get_lvl_short(a,i) → float (resistencia para short)
    confirm_close: requiere que la barra cierre por encima/debajo del nivel (absorcion)
    """
    def g(a, i):
        if i < 5: return
        out = []
        # LONG en soporte
        lvl_l = get_lvl_long(a, i)
        if np.isfinite(lvl_l) and lvl_l > 0:
            tol_abs = lvl_l * tol
            touched = a.l[i] <= lvl_l + tol_abs
            above_ref = lvl_l < a.c[i-1]  # nivel por debajo del precio previo (pullback)
            close_ok = (not confirm_close) or (a.c[i] > lvl_l - tol_abs)
            if touched and above_ref and close_ok:
                stop = lvl_l - atr_stop * a.atr[i]
                tp1, tp2 = struct_target(a, i, "long", lvl_l)
                if np.isfinite(tp2) and tp2 > lvl_l:
                    out.append(("long", lvl_l, stop, tp1, tp2, tag))

        # SHORT en resistencia
        lvl_s = get_lvl_short(a, i)
        if np.isfinite(lvl_s) and lvl_s > 0:
            tol_abs = lvl_s * tol
            touched = a.h[i] >= lvl_s - tol_abs
            below_ref = lvl_s > a.c[i-1]  # nivel por encima del precio previo (pullback)
            close_ok = (not confirm_close) or (a.c[i] < lvl_s + tol_abs)
            if touched and below_ref and close_ok:
                stop = lvl_s + atr_stop * a.atr[i]
                tp1, tp2 = struct_target(a, i, "short", lvl_s)
                if np.isfinite(tp2) and tp2 < lvl_s:
                    out.append(("short", lvl_s, stop, tp1, tp2, tag))
        return out if out else None
    return g


# ── Generadores nuevos ──────────────────────────────────────────────────────

def gen_pdh_pdl():
    """Previous Day High / Low — niveles mas clasicos de S/R intradía."""
    return make_sr_gen(
        lambda a,i: a.prev_day_low[i],
        lambda a,i: a.prev_day_high[i],
        tag="PDH_PDL",
    )

def gen_vah_val():
    """Value Area High / Low — extremos del area de valor del VP de sesion."""
    return make_sr_gen(
        lambda a,i: a.vp_val[i],
        lambda a,i: a.vp_vah[i],
        tag="VAH_VAL",
    )

def gen_asian_hl():
    """Asian session High / Low — niveles de la sesion asiatica."""
    return make_sr_gen(
        lambda a,i: a.asian_low[i],
        lambda a,i: a.asian_high[i],
        tag="ASIAN",
    )

def gen_swing_hl():
    """Swing High / Low (50 barras) — extremos del rango de 50 barras M15 = 12.5h."""
    return make_sr_gen(
        lambda a,i: a.swing_low_50[i],
        lambda a,i: a.swing_high_50[i],
        tag="SWING",
    )

def gen_weekly_hl():
    """Weekly High / Low — niveles semanales."""
    return make_sr_gen(
        lambda a,i: a.weekly_low[i],
        lambda a,i: a.weekly_high[i],
        tag="WEEKLY",
    )

def gen_poc_val_vah():
    """POC + VAH + VAL (triple VP) — todos los niveles del perfil juntos."""
    def get_long(a,i):
        cands = [a.vp_val[i], a.vp_poc[i] if a.vp_poc[i] < a.c[i-1] else np.nan]
        cands = [c for c in cands if np.isfinite(c) and c > 0]
        return min(cands) if cands else np.nan

    def get_short(a,i):
        cands = [a.vp_vah[i], a.vp_poc[i] if a.vp_poc[i] > a.c[i-1] else np.nan]
        cands = [c for c in cands if np.isfinite(c) and c > 0]
        return max(cands) if cands else np.nan

    return make_sr_gen(get_long, get_short, tag="VP_ALL")


# ── Motor backtest ──────────────────────────────────────────────────────────

def run_multi_gen(a, gens, m1, timeout_min=24*60, margin=2.0,
                  trail_atr=4.0, cooldown=6, max_day=3):
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
                if abs(entry-stop)<sf:
                    stop=entry-sf if side=="long" else entry+sf
                risk=abs(entry-stop)
                if risk<=0 or abs(tp2-entry)/risk<1.2: continue
                if tp1 and 100*abs(tp1-entry)/entry<0.5: continue

                chop=str(a.reg[i]).lower() in ("chop","range","balance","consolidation")
                j0   = np.searchsorted(m1ts,a.ts[i]+BAR_MS)
                jend = np.searchsorted(m1ts,a.ts[i]+BAR_MS+timeout_min*60_000)
                res  = None

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

                trades.append(dict(
                    bar_ts=int(a.ts[i]), side=side, lvl=lvl, atr=atr0, r=res,
                    oos=int(a.ts[i])>=OOS_MS, kind=kind, regime="chop" if chop else "trend",
                ))
                cool=i+cooldown; dcount[d]=dcount.get(d,0)+1; break
    return pd.DataFrame(trades)


def stats(df, sym, oos_days):
    oos = df[df.oos]; is_ = df[~df.oos]
    is_days = (pd.to_datetime(is_.bar_ts,unit='ms').max()-pd.to_datetime(is_.bar_ts,unit='ms').min()).days
    td_oos = len(oos)/max(oos_days,1)
    dd = (oos.r.cumsum()-oos.r.cumsum().cummax()).min() if len(oos)>0 else 0
    sh = oos.r.mean()/(oos.r.std()+1e-9)*np.sqrt(252) if len(oos)>1 else 0
    return dict(sym=sym, n_oos=len(oos), n_is=len(is_), td_oos=td_oos,
                avgR_oos=oos.r.mean() if len(oos)>0 else 0,
                wr_oos=100*(oos.r>0).mean() if len(oos)>0 else 0,
                dd=dd, sharpe=sh,
                avgR_is=is_.r.mean() if len(is_)>0 else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym", default="BTC", choices=list(ASSETS.keys()))
    args = ap.parse_args()
    sym = args.sym
    cfg = ASSETS[sym]

    if not cfg["m1"].exists():
        print(f"ERROR: {cfg['m1']} no existe"); sys.exit(1)

    print(f"\nCargando {sym}...", flush=True)
    L2.M1 = cfg["m1"]
    t=L2.load2(15,start_ms=0); a=L2.A2(t); m1=L2.load_m1_exit(start_ms=0)
    oos_days = (pd.to_datetime(t[t.ts_ms>=OOS_MS].ts_ms,unit='ms').max() -
                pd.to_datetime(pd.Series([OOS_MS]),unit='ms').iloc[0]).days

    # Baseline existente
    baseline_gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

    # Nuevos generadores
    new_gens = {
        "PDH_PDL":  gen_pdh_pdl(),
        "VAH_VAL":  gen_vah_val(),
        "ASIAN":    gen_asian_hl(),
        "SWING":    gen_swing_hl(),
        "WEEKLY":   gen_weekly_hl(),
        "VP_ALL":   gen_poc_val_vah(),
    }

    print(f"\n  {'Generador':>12} | {'n_oos':>6} | {'t/dia':>5} | {'IS_avgR':>8} | {'OOS_avgR':>9} | {'WR':>7} | {'DD':>8} | {'Sharpe':>7}")
    print("  "+"-"*78)

    # Baseline
    df_base = run_multi_gen(a, baseline_gens, m1, timeout_min=cfg["timeout"],
                             margin=cfg["margin"], max_day=2)
    s = stats(df_base, sym, oos_days)
    print(f"  {'BASELINE':>12} | {s['n_oos']:>6} | {s['td_oos']:>5.2f} | {s['avgR_is']:>+8.3f} | {s['avgR_oos']:>+9.3f} | {s['wr_oos']:>6.1f}% | {s['dd']:>+8.2f}R | {s['sharpe']:>7.2f}")

    # Cada nuevo generador por separado
    all_new_dfs = []
    for name, gen in new_gens.items():
        df = run_multi_gen(a, [gen], m1, timeout_min=cfg["timeout"],
                           margin=cfg["margin"], max_day=3)
        if len(df)==0:
            print(f"  {name:>12} | {'0':>6} | {'0.00':>5} | {'---':>8} | {'---':>9} | {'---':>7} | {'---':>8} | {'---':>7}")
            continue
        s = stats(df, sym, oos_days)
        print(f"  {name:>12} | {s['n_oos']:>6} | {s['td_oos']:>5.2f} | {s['avgR_is']:>+8.3f} | {s['avgR_oos']:>+9.3f} | {s['wr_oos']:>6.1f}% | {s['dd']:>+8.2f}R | {s['sharpe']:>7.2f}")
        all_new_dfs.append(df)

    # Todo junto (baseline + todos los nuevos)
    if all_new_dfs:
        print("  "+"-"*78)
        df_all = run_multi_gen(a, baseline_gens + list(new_gens.values()), m1,
                               timeout_min=cfg["timeout"], margin=cfg["margin"], max_day=3)
        s = stats(df_all, sym, oos_days)
        print(f"  {'COMBINADO':>12} | {s['n_oos']:>6} | {s['td_oos']:>5.2f} | {s['avgR_is']:>+8.3f} | {s['avgR_oos']:>+9.3f} | {s['wr_oos']:>6.1f}% | {s['dd']:>+8.2f}R | {s['sharpe']:>7.2f}")

        # Breakdown por kind en OOS
        oos_all = df_all[df_all.oos]
        print(f"\n  Breakdown OOS por nivel (combinado):")
        print(f"  {'Kind':>12} | {'n':>5} | {'avgR':>8} | {'WR':>7}")
        print("  "+"-"*38)
        for k, g in oos_all.groupby("kind"):
            print(f"  {k:>12} | {len(g):>5} | {g.r.mean():>+8.3f} | {100*(g.r>0).mean():>6.1f}%")

        df_all.to_csv(ROOT/f"backtest/new_levels_{sym.lower()}.csv", index=False)
        print(f"\n  Guardado: new_levels_{sym.lower()}.csv")


if __name__ == "__main__":
    main()
