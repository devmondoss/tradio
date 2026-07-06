"""
_entry_improve.py — BTC: mejoras en el sistema de entrada
Testa 4 palancas: hora sesion, offset dentro del nivel, entrada escalonada, barra confirma
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
MS_PER_HOUR = 3_600_000

def gen_h21_param(K=15, touches=2, tol=0.002):
    def g(a, i):
        if i < K: return
        win = a.fp_poc[i-K:i]; win = win[np.isfinite(win)]
        if len(win) < 3: return
        lvl = np.median(win)
        n_t = np.sum(np.abs(a.l[i-K:i] - lvl)/lvl <= tol)
        if n_t >= touches and a.c[i] > a.c[i-1] and abs(a.l[i]-lvl)/lvl <= tol:
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
        n_t = np.sum(np.abs(a.h[i-K:i] - lvl)/lvl <= tol)
        if n_t >= touches and a.c[i] < a.c[i-1] and abs(a.h[i]-lvl)/lvl <= tol:
            stop = lvl + 0.6*a.atr[i]
            tp1, tp2 = struct_target(a, i, "short", lvl)
            if np.isfinite(tp2): return [("short", lvl, stop, tp1, tp2, "H21s")]
    return g

BASELINE_GENS = lambda: [L2.gen_h5(), gen_h21_param(), gen_h21s_param()]

# ─── Motor genérico ──────────────────────────────────────────────────────────

def run(a, gens, m1,
        timeout_min=24*60, margin=2.0, cooldown=6, max_day=4,
        # palancas de entrada
        hour_filter=None,     # None | (h_ini, h_fin) UTC
        entry_offset_bps=0,   # bps dentro del nivel (+ = mejor precio)
        scaled=False,         # True = 2 ordenes (50% en nivel, 50% en nivel-0.3atr)
        confirm_bar=False,    # True = entrar en barra i+1 si confirma
        ):
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

            # filtro horario
            if hour_filter is not None:
                h0,h1 = hour_filter
                bar_hour = (int(a.ts[i]) % 86_400_000) // MS_PER_HOUR
                if not (h0 <= bar_hour < h1): continue

            for side,lvl,stop,tp1,tp2,kind in (g(a,i) or []):
                if not np.isfinite([lvl,stop,tp2]).all(): continue
                ref=a.c[i-1]
                if side=="long"  and not (lvl<ref): continue
                if side=="short" and not (lvl>ref): continue

                # confirm_bar: señal en barra i, pero ejecutamos en i+1
                # la barra i+1 debe abrir en dirección correcta
                exec_i = i
                if confirm_bar:
                    if i+1 >= a.n: continue
                    next_open = a.o[i+1] if hasattr(a,'o') else a.c[i]
                    # long: i+1 abre por encima del nivel → nivel sigue siendo soporte
                    if side=="long"  and not (next_open > lvl): continue
                    if side=="short" and not (next_open < lvl): continue
                    exec_i = i+1

                atr0 = a.atr[exec_i]

                # entry offset: entrar ligeramente dentro del nivel
                offset = lvl * entry_offset_bps / 10000
                if side=="long":
                    entry = lvl - offset   # entry más bajo = mejor precio para long
                else:
                    entry = lvl + offset   # entry más alto = mejor precio para short

                # verificar fill en la barra de ejecucion
                mf = margin/1e4
                if side=="long"  and not (a.l[exec_i] <= entry*(1-mf)): continue
                if side=="short" and not (a.h[exec_i] >= entry*(1+mf)): continue

                # recalcular stop/tp con nuevo entry
                stop_adj = stop - offset if side=="long" else stop + offset
                sf=0.15/100*entry
                if abs(entry-stop_adj)<sf:
                    stop_adj = entry-sf if side=="long" else entry+sf
                risk=abs(entry-stop_adj)
                if risk<=0 or abs(tp2-entry)/risk<1.2: continue
                if tp1 and 100*abs(tp1-entry)/entry<0.5: continue

                chop=str(a.reg[exec_i]).lower() in ("chop","range","balance","consolidation")
                j0  =np.searchsorted(m1ts, a.ts[exec_i]+BAR_MS)
                jend=np.searchsorted(m1ts, a.ts[exec_i]+BAR_MS+timeout_min*60_000)

                if not scaled:
                    res = _sim_exit(m1ts,m1h,m1l,m1c,j0,jend,side,entry,stop_adj,tp1,tp2,risk,chop)
                else:
                    # ESCALONADO: 2 ordenes
                    # Orden 1 (50%): entry en lvl (ya calculado)
                    # Orden 2 (50%): entry2 = lvl - 0.3*atr (long) o + 0.3*atr (short)
                    if side=="long":
                        entry2 = lvl - 0.3*atr0
                        stop2  = stop_adj
                    else:
                        entry2 = lvl + 0.3*atr0
                        stop2  = stop_adj
                    filled1 = True  # orden 1 ya confirmada arriba
                    filled2 = (a.l[exec_i] <= entry2) if side=="long" else (a.h[exec_i] >= entry2)
                    # simular las dos posiciones
                    r1 = _sim_exit(m1ts,m1h,m1l,m1c,j0,jend,side,entry,stop_adj,tp1,tp2,risk,chop)
                    if filled2:
                        risk2 = abs(entry2-stop2)
                        if risk2 > 0:
                            r2 = _sim_exit(m1ts,m1h,m1l,m1c,j0,jend,side,entry2,stop2,tp1,tp2,risk2,chop)
                            res = (r1*0.5 + r2*0.5) if r2 is not None else r1
                        else:
                            res = r1
                    else:
                        res = r1  # solo orden 1

                if res is None: continue

                trades.append(dict(
                    bar_ts=int(a.ts[exec_i]), side=side, r=res,
                    oos=int(a.ts[exec_i])>=OOS_MS, kind=kind,
                    regime="chop" if chop else "trend",
                    hour=(int(a.ts[exec_i])%86_400_000)//MS_PER_HOUR,
                ))
                cool=exec_i+cooldown; dcount[d]=dcount.get(d,0)+1; break
    return pd.DataFrame(trades)


def _sim_exit(m1ts,m1h,m1l,m1c,j0,jend,side,entry,stop,tp1,tp2,risk,chop):
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
            if jj<=j0: return None
            px=m1c[jj]; realized+=rem*(((px-entry) if side=="long" else (entry-px))/risk)
        exit_s=MK if reason=="target" else TK
        fee_r=(MK+(MK*p1 if f1 else 0)+exit_s*rem)*entry/risk
        return realized-fee_r
    else:
        fee_r=(MK+TK)*entry/risk; best=entry; trail=stop
        for j in range(j0,min(jend,len(m1ts))):
            if side=="long":
                best=max(best,m1h[j]); trail=max(trail,best-4*risk)
                if m1l[j]<=trail: return (trail-entry)/risk-fee_r
            else:
                best=min(best,m1l[j]); trail=min(trail,best+4*risk)
                if m1h[j]>=trail: return (entry-trail)/risk-fee_r
        jj=min(jend,len(m1ts))-1
        if jj<=j0: return None
        px=m1c[jj]; return ((px-entry) if side=="long" else (entry-px))/risk-fee_r


def rep(name, df, oos_days=107, is_days=417):
    oos=df[df.oos]; is_=df[~df.oos]
    if len(oos)<5:
        print(f"  {name:<45} | {'---':>4} | {'---':>4} | {'---':>7} | {'---':>7} | {'---':>6} | {'---':>8}"); return
    td=len(oos)/oos_days
    dd=(oos.r.cumsum()-oos.r.cumsum().cummax()).min()
    sh=oos.r.mean()/(oos.r.std()+1e-9)*np.sqrt(252)
    iavg=is_.r.mean() if len(is_)>0 else float('nan')
    expR=td*oos.r.mean()
    # PnL con $5 risk, 65% fill, 15% slip
    pnl_y = oos.r.sum()*0.65*0.85*5/oos_days*365
    gain_vs_base = ""
    print(f"  {name:<45} | {len(oos):>4} | {td:>4.2f} | {iavg:>+7.3f} | {oos.r.mean():>+7.3f} | {100*(oos.r>0).mean():>5.1f}% | {dd:>+8.2f}R | {expR:>+6.3f} | ${pnl_y:>+6.0f}/y")


# ─── Cargar ──────────────────────────────────────────────────────────────────
print("Cargando BTC...", flush=True)
L2.M1 = ROOT/"data/bybit-perp/processed/btcusdt_perp_m1.parquet"
t=L2.load2(15,start_ms=0); a=L2.A2(t); m1=L2.load_m1_exit(start_ms=0)
# agregar open al objeto a (para confirm_bar)
a.o = t.open.values

HDR = f"  {'Config':<45} | {'n_oos':>4} | {'t/d':>4} | {'IS_avg':>7} | {'OOS_avg':>7} | {'WR':>5} | {'DD':>8} | {'expR/d':>6} | {'PnL/y':>8}"
SEP = "  "+"-"*115

# ══ BLOQUE 1: FILTRO HORARIO ══════════════════════════════════════════════════
print(f"\n{'='*115}")
print("  BLOQUE 1 — FILTRO HORARIO (UTC)")
print('='*115); print(HDR); print(SEP)

df_base = run(a, BASELINE_GENS(), m1, max_day=4, cooldown=3)
rep("BASELINE (sin filtro horario)", df_base)

sesiones = [
    ("Asia      00-07",  (0,  7)),
    ("Londres   07-13",  (7, 13)),
    ("NY        13-20",  (13,20)),
    ("Londres+NY 07-20", (7, 20)),
    ("No Asia   07-23",  (7, 23)),
]
for lbl, hf in sesiones:
    df=run(a, BASELINE_GENS(), m1, max_day=4, cooldown=3, hour_filter=hf)
    rep(f"Sesion {lbl}", df)

# ══ BLOQUE 2: ENTRY OFFSET ════════════════════════════════════════════════════
print(f"\n{'='*115}")
print("  BLOQUE 2 — OFFSET DE ENTRADA (bps dentro del nivel)")
print('='*115); print(HDR); print(SEP)

rep("BASELINE offset=0bps", df_base)
for bps in [3, 5, 8, 10, 15, 20]:
    df=run(a, BASELINE_GENS(), m1, max_day=4, cooldown=3, entry_offset_bps=bps)
    rep(f"Entry offset={bps}bps dentro nivel", df)

# ══ BLOQUE 3: ENTRADA ESCALONADA ════════════════════════════════════════════
print(f"\n{'='*115}")
print("  BLOQUE 3 — ENTRADA ESCALONADA (50% nivel + 50% nivel-0.3ATR)")
print('='*115); print(HDR); print(SEP)

rep("BASELINE sin escalar", df_base)
df_scaled=run(a, BASELINE_GENS(), m1, max_day=4, cooldown=3, scaled=True)
rep("Scaled 50/50 (nivel + nivel-0.3atr)", df_scaled)

# con offset + escalonado
df_sc_off=run(a, BASELINE_GENS(), m1, max_day=4, cooldown=3, scaled=True, entry_offset_bps=5)
rep("Scaled + offset=5bps", df_sc_off)

# ══ BLOQUE 4: BARRA DE CONFIRMACION ═════════════════════════════════════════
print(f"\n{'='*115}")
print("  BLOQUE 4 — BARRA DE CONFIRMACION (entrar en barra i+1)")
print('='*115); print(HDR); print(SEP)

rep("BASELINE sin confirm", df_base)
df_conf=run(a, BASELINE_GENS(), m1, max_day=4, cooldown=3, confirm_bar=True)
rep("Confirm bar (i+1 confirma direction)", df_conf)

df_conf_off=run(a, BASELINE_GENS(), m1, max_day=4, cooldown=3, confirm_bar=True, entry_offset_bps=5)
rep("Confirm bar + offset=5bps", df_conf_off)

# ══ BLOQUE 5: MEJORES COMBINACIONES ════════════════════════════════════════
print(f"\n{'='*115}")
print("  BLOQUE 5 — COMBINACIONES GANADORAS")
print('='*115); print(HDR); print(SEP)

rep("BASELINE puro", df_base)

# Por hora — ver cual fue mejor
for lbl,hf in [("Londres+NY 07-20",(7,20)), ("No Asia 07-23",(7,23))]:
    for bps in [0,5]:
        for sc in [False, True]:
            df=run(a, BASELINE_GENS(), m1, max_day=4, cooldown=3,
                   hour_filter=hf, entry_offset_bps=bps, scaled=sc)
            tag = f"{'ScAL' if sc else '    '} {lbl} off={bps}bps"
            rep(tag, df)

# confirm + hora + offset
for lbl,hf in [("Londres+NY 07-20",(7,20))]:
    df=run(a, BASELINE_GENS(), m1, max_day=4, cooldown=3,
           hour_filter=hf, confirm_bar=True, entry_offset_bps=5)
    rep(f"Confirm+{lbl}+off5bps", df)

# ══ PnL por hora (baseline) para entender la sesion ════════════════════════
print(f"\n{'='*70}")
print("  DISTRIBUCION PnL POR HORA UTC — OOS (baseline)")
print(f"{'='*70}")
oos_base=df_base[df_base.oos]
print(f"  {'Hora UTC':>8} | {'n':>4} | {'avgR':>7} | {'WR':>6} | {'netR':>7}")
print("  "+"-"*42)
for h in range(0,24):
    g=oos_base[oos_base.hour==h]
    if len(g)<3: continue
    print(f"  {h:>8}h | {len(g):>4} | {g.r.mean():>+7.3f} | {100*(g.r>0).mean():>5.1f}% | {g.r.sum():>+7.2f}R")
