"""
_rule80.py — Regla del 80% de Market Profile (Dalton/CBOT) sobre BTC/ETH/SOL perp M15
=====================================================================================
Regla: precio FUERA del value area re-entra con aceptación (N closes consecutivos dentro)
-> rota hasta el extremo OPUESTO del VA (~80% segun folklore). Long al re-entrar desde
abajo -> target VAH; short al re-entrar desde arriba -> target VAL.

ELECCIONES DOCUMENTADAS
- VA = VP ROLLING de 96 barras M15 (a.vp_vah/vp_val/vp_poc de load2), consistente con el
  resto del repo. NO es el VA diario congelado del Market Profile clasico.
- Descriptivo: VA CONGELADO en la barra de senal. Exito = high/low toca el extremo opuesto
  antes de un close de vuelta FUERA por el lado de entrada. Horizonte 7 dias.
- Backtest 100% causal: la senal se confirma al CIERRE de i-1 (los ultimos `accept` closes
  dentro del VA de su propia barra, y el anterior fuera). El nivel limite se emite con
  datos hasta i-1; el motor exige que el precio RETESTEE el nivel en la barra i para fill
  (fill implicito conservador: solo entra si vuelve al borde).
    lvl_mode='edge'  -> limite en el borde re-cruzado (VAL long / VAH short)
    lvl_mode='close' -> limite en c[i-1] -/+ 0.15*ATR
- stop = 0.5*ATR fuera del borde del VA (motor aplica stop_scale=0.8 + floor 0.15%)
- tp1 = POC (parcial 50% estilo casa), tp2 = extremo opuesto del VA. mode='fade'.

Uso: python backtest/_rule80.py desc bt sens overlap   (tareas combinables)
"""
import sys, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np, pandas as pd
from _listas import OOS_MS
from _strategy_ab import run_system
from _v3_parity import SYMS, load, run_cfg, make_filter, wrap, row, show

TF = 15
STD = dict(mode="fade", stop_scale=0.8, max_day=4, cooldown=3, min_range=0.0)

# ================================================================ 1) DESCRIPTIVO
def find_events(a, accept=2):
    """Eventos de re-entrada aceptada al VA. Cada excursion fuera dispara a lo sumo 1 evento
    (en la barra del accept-esimo close consecutivo dentro). VA rolling por barra."""
    events = []
    armed = None      # 'long' si la ultima excursion fue por debajo de VAL, 'short' por encima
    run_in = 0
    for i in range(100, a.n):
        vah, val = a.vp_vah[i], a.vp_val[i]
        if not (np.isfinite(vah) and np.isfinite(val) and vah > val):
            armed = None; run_in = 0; continue
        c = a.c[i]
        if c < val:
            armed = "long"; run_in = 0
        elif c > vah:
            armed = "short"; run_in = 0
        else:
            if armed is not None:
                run_in += 1
                if run_in == accept:
                    events.append(dict(i=i, side=armed, val=val, vah=vah,
                                       poc=a.vp_poc[i], ts=int(a.ts[i])))
                    armed = None; run_in = 0
    return events

def outcome(a, ev, horizon=96*7):
    """VA congelado en la senal. target = toca extremo opuesto; fail = close de vuelta fuera
    por el lado de entrada; timeout si no resuelve en `horizon` barras M15."""
    i, side, vah, val = ev["i"], ev["side"], ev["vah"], ev["val"]
    for j in range(i+1, min(i+1+horizon, a.n)):
        if side == "long":
            if a.h[j] >= vah: return "target", j-i
            if a.c[j] < val:  return "fail", j-i
        else:
            if a.l[j] <= val: return "target", j-i
            if a.c[j] > vah:  return "fail", j-i
    return "timeout", horizon

def desc():
    print("=" * 96)
    print("TAREA 1 — DESCRIPTIVO: ¿la rotacion al extremo opuesto ocurre el 80% de las veces?")
    print("(VA rolling 96xM15 congelado en la senal; fail = close de vuelta fuera; horizonte 7d)")
    print("=" * 96)
    for accept in (1, 2, 3):
        print(f"\n--- aceptacion = {accept} close(s) consecutivos dentro ---")
        print(f"  {'sym':<8} {'n':>5} {'target%':>8} {'fail%':>7} {'tout%':>6} "
              f"{'medH->tgt':>10} {'IS tgt%':>8} {'OOS tgt%':>8} {'long tgt%':>10} {'short tgt%':>10}")
        for sym in SYMS:
            a, _ = load(sym)
            evs = find_events(a, accept)
            rows = []
            for ev in evs:
                res, nb = outcome(a, ev)
                rows.append(dict(side=ev["side"], res=res, nb=nb, oos=ev["ts"] >= OOS_MS))
            d = pd.DataFrame(rows)
            if len(d) == 0:
                print(f"  {sym:<8} sin eventos"); continue
            tgt = d.res == "target"
            med_h = d[tgt].nb.median() * TF / 60 if tgt.any() else float("nan")
            def pct(m): return 100 * m.mean() if len(m) else float("nan")
            print(f"  {sym:<8} {len(d):>5} {100*tgt.mean():>7.1f}% {pct(d.res=='fail'):>6.1f}% "
                  f"{pct(d.res=='timeout'):>5.1f}% {med_h:>9.1f}h "
                  f"{pct(d[~d.oos].res=='target'):>7.1f}% {pct(d[d.oos].res=='target'):>7.1f}% "
                  f"{pct(d[d.side=='long'].res=='target'):>9.1f}% {pct(d[d.side=='short'].res=='target'):>9.1f}%")

# ================================================================ 2) BACKTEST
def gen_rule80(accept=2, lvl_mode="edge", off_atr=0.15):
    """Senal confirmada al cierre de i-1: closes de i-accept..i-1 dentro del VA (cada uno vs
    su VA) y close de i-accept-1 fuera. Emite limite para la barra i (motor exige retest)."""
    def g(a, i):
        if i < accept + 3: return None
        for k in range(1, accept + 1):
            b = i - k
            vah, val = a.vp_vah[b], a.vp_val[b]
            if not (np.isfinite(vah) and np.isfinite(val) and val <= a.c[b] <= vah): return None
        b0 = i - accept - 1
        vah0, val0 = a.vp_vah[b0], a.vp_val[b0]
        if not (np.isfinite(vah0) and np.isfinite(val0)): return None
        vah, val, poc = a.vp_vah[i-1], a.vp_val[i-1], a.vp_poc[i-1]
        atr = a.atr[i-1] if np.isfinite(a.atr[i-1]) and a.atr[i-1] > 0 else a.atr[i]
        if a.c[b0] < val0:      # venia de abajo -> long, target VAH
            lvl = val if lvl_mode == "edge" else a.c[i-1] - off_atr*atr
            stop = val - 0.5*atr
            tp1 = poc if lvl < poc < vah else None
            return [("long", lvl, stop, tp1, vah, "R80L")]
        if a.c[b0] > vah0:      # venia de arriba -> short, target VAL
            lvl = vah if lvl_mode == "edge" else a.c[i-1] + off_atr*atr
            stop = vah + 0.5*atr
            tp1 = poc if val < poc < lvl else None
            return [("short", lvl, stop, tp1, val, "R80S")]
        return None
    return g

def run_r80(sym, accept=2, lvl_mode="edge", volfilter=False, use_dist=False, **params):
    a, m1 = load(sym)
    gens = [gen_rule80(accept, lvl_mode)]
    if use_dist:
        gens = wrap(gens, make_filter(False, True))
    p = {**STD, "volfilter": volfilter, **params}
    return run_system(a, gens, m1, TF, **p)

def eval_r80(label, **kw):
    per = {sym: row(run_r80(sym, **kw)) for sym in SYMS}
    return show(label, per)

def bt():
    print("\n" + "=" * 96)
    print("TAREA 2 — BACKTEST regla 80% (motor casa: fade, stop 0.5ATR fuera del VA, ss=0.8,")
    print("          tp1=POC parcial 50%, tp2=extremo opuesto, salida M1, fee honesto)")
    print("=" * 96)
    eval_r80("(a) accept=2 · limite en borde VA (edge) · SIN filtros", accept=2, lvl_mode="edge", volfilter=False)
    eval_r80("(a) accept=2 · limite en c[i-1]-0.15ATR (close) · SIN filtros", accept=2, lvl_mode="close", volfilter=False)
    eval_r80("(b) accept=2 · edge · +ATR>mediana(500)", accept=2, lvl_mode="edge", volfilter=True)
    eval_r80("(b) accept=2 · close · +ATR>mediana(500)", accept=2, lvl_mode="close", volfilter=True)
    eval_r80("(b) accept=2 · edge · +ATR + dist>0.5ATR", accept=2, lvl_mode="edge", volfilter=True, use_dist=True)

def sens():
    print("\n" + "=" * 96)
    print("TAREA 2c — SENSIBILIDAD al parametro de aceptacion (edge, con y sin volfilter)")
    print("=" * 96)
    for accept in (1, 3):
        eval_r80(f"accept={accept} · edge · SIN filtros", accept=accept, lvl_mode="edge", volfilter=False)
        eval_r80(f"accept={accept} · edge · +ATR>mediana", accept=accept, lvl_mode="edge", volfilter=True)

# ================================================================ 3) SOLAPAMIENTO v3
def overlap(accept=2, lvl_mode="edge", volfilter=True, win_ms=2*3600_000):
    print("\n" + "=" * 96)
    print(f"TAREA 3 — SOLAPAMIENTO con v3 (fade+H1+dist+IFVG ss0.8): % trades r80 a ±2h de un")
    print(f"          trade v3 mismo simbolo/side. Config r80: accept={accept} {lvl_mode} volf={volfilter}")
    print("=" * 96)
    for sym in SYMS:
        r80 = run_r80(sym, accept=accept, lvl_mode=lvl_mode, volfilter=volfilter)
        v3 = run_cfg(sym, mode="fade", use_h1=True, use_dist=True, use_ifvg=True, stop_scale=0.8)
        if len(r80) == 0:
            print(f"  {sym:<8} r80 sin trades"); continue
        hits = 0
        for _, t in r80.iterrows():
            m = v3[(v3.side == t.side) & ((v3.ts - t.ts).abs() <= win_ms)]
            if len(m): hits += 1
        print(f"  {sym:<8} r80 n={len(r80):>4} | v3 n={len(v3):>4} | solapados={hits:>4} ({100*hits/len(r80):.0f}%)")

# ================================================================ main
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks", nargs="+", choices=["desc", "bt", "sens", "overlap", "all"])
    args = ap.parse_args()
    tasks = ["desc", "bt", "sens", "overlap"] if "all" in args.tasks else args.tasks
    for t in tasks:
        {"desc": desc, "bt": bt, "sens": sens, "overlap": overlap}[t]()
    print("\nDONE")
