"""
Comparativa PnL anual: baseline vs configuracion nueva
Capital: $500 x 10x = $5,000 — riesgo 1% por trade = $50/trade
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

CAPITAL   = 500        # USD real
LEVERAGE  = 10
BUYING_PW = CAPITAL * LEVERAGE   # $5,000
RISK_PCT  = 0.01                  # 1% del capital real por trade
RISK_USD  = CAPITAL * RISK_PCT    # $5 por trade
FILL_RATE = 0.65                  # 65% de las ordenes maker se ejecutan (estimado)
SLIP_DISC = 0.15                  # 15% de penalizacion por slippage/spread real

# con leverage, el riesgo efectivo por trade en posicion = RISK_USD
# cada 1R = RISK_USD dolares
RISK_EFF = RISK_USD   # $5

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

def gen_pdh_pdl_confirmed(K_bars=96, touches=1, tol=0.003):
    def g(a, i):
        if i < K_bars: return
        out = []
        for side, lvl_col, lbl in [("long","prev_day_low","PDL"),("short","prev_day_high","PDH")]:
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
                stop = lvl - 0.6*a.atr[i] if side=="long" else lvl + 0.6*a.atr[i]
                tp1, tp2 = struct_target(a, i, side, lvl)
                if np.isfinite(tp2):
                    out.append((side, lvl, stop, tp1, tp2, lbl))
        return out if out else None
    return g

def run(a, gens, m1, timeout_min=24*60, margin=2.0, cooldown=6, max_day=2):
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
                            best=max(best,m1h[j]); trail=max(trail,best-4*atr0)
                            if m1l[j]<=trail: res=(trail-entry)/risk-fee_r; break
                        else:
                            best=min(best,m1l[j]); trail=min(trail,best+4*atr0)
                            if m1h[j]>=trail: res=(entry-trail)/risk-fee_r; break
                    if res is None:
                        jj=min(jend,len(m1ts))-1
                        if jj<=j0: continue
                        px=m1c[jj]; res=((px-entry) if side=="long" else (entry-px))/risk-fee_r
                trades.append(dict(
                    bar_ts=int(a.ts[i]), side=side, r=res,
                    oos=int(a.ts[i])>=OOS_MS, kind=kind,
                    regime="chop" if chop else "trend",
                    date=pd.Timestamp(int(a.ts[i]),unit='ms').strftime('%Y-%m'),
                ))
                cool=i+cooldown; dcount[d]=dcount.get(d,0)+1; break
    return pd.DataFrame(trades)


def pnl_report(name, df, risk_usd, fill_rate, slip):
    oos = df[df.oos]; is_ = df[~df.oos]
    oos_days=(pd.to_datetime(oos.bar_ts,unit='ms').max()-pd.to_datetime(oos.bar_ts,unit='ms').min()).days
    is_days =(pd.to_datetime(is_.bar_ts,unit='ms').max()-pd.to_datetime(is_.bar_ts,unit='ms').min()).days
    total_days = oos_days + is_days

    print(f"\n{'='*70}")
    print(f"  {name}")
    print(f"{'='*70}")

    for tag, d, days in [("IS  ", is_, is_days), ("OOS ", oos, oos_days), ("FULL", df, total_days)]:
        if len(d) < 3: continue
        n_effective = int(len(d) * fill_rate)
        # aplicar fill rate: tomar trades aleatorios pero deterministicamente
        # simplificacion: fill_rate reduce proporcionalmente los trades exitosos
        r_adj = d.r * (1 - slip)  # penalizacion slippage en cada trade
        # simular fill rate: los trades que no se ejecutan no suceden
        net_r = r_adj.sum() * fill_rate
        avg_r = r_adj.mean() * (1 - (1-fill_rate)*0.5)  # aprox
        gross_pnl = r_adj.sum() * fill_rate * risk_usd
        pnl_annual = gross_pnl * (365 / max(days,1))
        pnl_monthly= pnl_annual / 12
        td = len(d)/max(days,1)
        wr = 100*(d.r>0).mean()
        dd_r=(d.r.cumsum()-d.r.cumsum().cummax()).min()
        dd_usd=dd_r*fill_rate*risk_usd
        print(f"  {tag}: n={len(d):>4} ({n_effective} efectivos, fill={fill_rate:.0%}) | "
              f"{td:.2f} t/d | avgR={d.r.mean():>+.3f} | WR={wr:.1f}%")
        print(f"        netR={net_r:>+7.1f}R en {days}d | "
              f"PnL bruto=${gross_pnl:>+8.0f} | "
              f"PnL/año=${pnl_annual:>+8.0f} | "
              f"PnL/mes=${pnl_monthly:>+6.0f}")
        print(f"        Max DD = {dd_r:.1f}R = ${dd_usd:.0f}")

    # por mes OOS
    if len(oos) > 0:
        print(f"\n  PnL mensual OOS (fill={fill_rate:.0%}, slip={slip:.0%}):")
        print(f"  {'Mes':>8} | {'n':>4} | {'avgR':>7} | {'WR':>6} | {'PnL_R':>7} | {'PnL_$':>8}")
        print("  "+"-"*52)
        for mes, g in oos.groupby("date"):
            r_mes = g.r.sum() * fill_rate * (1-slip)
            pnl_mes = r_mes * risk_usd
            wr_mes = 100*(g.r>0).mean()
            print(f"  {mes:>8} | {len(g):>4} | {g.r.mean():>+7.3f} | {wr_mes:>5.1f}% | {r_mes:>+7.2f}R | ${pnl_mes:>+7.0f}")
        total_oos_r = oos.r.sum()*fill_rate*(1-slip)
        print(f"  {'TOTAL':>8} | {len(oos):>4} | {oos.r.mean():>+7.3f} | {100*(oos.r>0).mean():>5.1f}% | "
              f"{total_oos_r:>+7.2f}R | ${total_oos_r*risk_usd:>+7.0f}")


# ── Cargar datos ─────────────────────────────────────────────────────────────
print("Cargando BTC...", flush=True)
L2.M1 = ROOT/"data/bybit-perp/processed/btcusdt_perp_m1.parquet"
t=L2.load2(15,start_ms=0); a=L2.A2(t); m1=L2.load_m1_exit(start_ms=0)

# CONFIGS
cfg_base = dict(
    gens=[L2.gen_h5(), gen_h21_param(K=15,touches=2), gen_h21s_param(K=15,touches=2)],
    max_day=2, cooldown=6,
)
cfg_new = dict(
    gens=[L2.gen_h5(), gen_h21_param(K=15,touches=2), gen_h21s_param(K=15,touches=2)],
    max_day=4, cooldown=3,
)
cfg_new_pdl = dict(
    gens=[L2.gen_h5(), gen_h21_param(K=15,touches=2), gen_h21s_param(K=15,touches=2),
          gen_pdh_pdl_confirmed(K_bars=96, touches=1)],
    max_day=4, cooldown=3,
)

print(f"\n  Capital: ${CAPITAL}  |  Leverage: {LEVERAGE}x  |  Buying power: ${BUYING_PW}")
print(f"  Riesgo por trade: ${RISK_EFF}  (1% de ${CAPITAL})")
print(f"  Fill rate asumido: {FILL_RATE:.0%}  |  Slippage disc: {SLIP_DISC:.0%}")
print(f"  Riesgo ajustado efectivo: ${RISK_EFF*FILL_RATE*(1-SLIP_DISC):.2f}/trade efectivo")

df_base = run(a, cfg_base["gens"], m1, max_day=cfg_base["max_day"], cooldown=cfg_base["cooldown"])
df_new  = run(a, cfg_new["gens"],  m1, max_day=cfg_new["max_day"],  cooldown=cfg_new["cooldown"])
df_pdl  = run(a, cfg_new_pdl["gens"], m1, max_day=cfg_new_pdl["max_day"], cooldown=cfg_new_pdl["cooldown"])

pnl_report("BASELINE  (max_day=2, cool=6)", df_base, RISK_EFF, FILL_RATE, SLIP_DISC)
pnl_report("NUEVA     (max_day=4, cool=3)", df_new,  RISK_EFF, FILL_RATE, SLIP_DISC)
pnl_report("NUEVA+PDL (max_day=4 + PDH/PDL)", df_pdl, RISK_EFF, FILL_RATE, SLIP_DISC)

# Resumen comparativo
print(f"\n{'='*70}")
print("  RESUMEN COMPARATIVO — OOS ($500 capital, 10x lev, 65% fill, 15% slip)")
print(f"{'='*70}")
print(f"  {'Config':<28} | {'n_oos':>5} | {'t/d':>4} | {'avgR':>7} | {'PnL/año':>9} | {'PnL/mes':>8} | {'Max DD$':>8}")
print("  "+"-"*78)
for name, df in [("BASELINE", df_base), ("NUEVA max_day=4", df_new), ("NUEVA+PDL", df_pdl)]:
    oos=df[df.oos]
    oos_days=107
    td=len(oos)/oos_days
    r_adj=oos.r.sum()*FILL_RATE*(1-SLIP_DISC)
    pnl_y=r_adj/oos_days*365*RISK_EFF
    pnl_m=pnl_y/12
    dd_r=(oos.r.cumsum()-oos.r.cumsum().cummax()).min()
    dd_usd=dd_r*FILL_RATE*RISK_EFF
    print(f"  {name:<28} | {len(oos):>5} | {td:>4.2f} | {oos.r.mean():>+7.3f} | ${pnl_y:>+8.0f} | ${pnl_m:>+7.0f} | ${dd_usd:>+7.0f}")
