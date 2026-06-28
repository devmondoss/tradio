"""
_bt_slip.py — stress test de slippage: ¿stop_scale ajustado sobrevive fills realistas?
=======================================================================================
La gran sospecha: el backtest asume fill exacto en el stop. Con stops más chicos cada bp de
slippage pesa más en R. Aplico slippage adverso a salidas taker (stop/be/trail/timeout) y veo
si la ganancia de stop_scale<1 aguanta. trail_atr=6 también, para confirmar que es robusto.
"""
import sys; from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
import _listas2 as L2
from _strategy_ab import run_system
from _audit_mirror import gen_h21_short

PARQ = {"BTCUSDT": "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
        "ETHUSDT": "E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet",
        "SOLUSDT": "E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"}
TF=15
def oosA(df): o=df[df.oos]; return o.r.mean() if len(o) else 0
DATA={}
for sym in ["BTCUSDT","ETHUSDT","SOLUSDT"]:
    if not Path(PARQ[sym]).exists(): continue
    L2.M1=PARQ[sym]; a=L2.A2(L2.load2(TF,start_ms=L2.TICK_MS)); m1=L2.load_m1_exit(start_ms=L2.TICK_MS)
    DATA[sym]=(a,m1,[L2.gen_h5(),L2.gen_h21(),gen_h21_short()]); print("cargado",sym,file=sys.stderr)

def run(sym,**kw):
    a,m1,gens=DATA[sym]; return oosA(run_system(a,gens,m1,TF,mode="routed",atr_mult=1.0,**kw))

print("="*88)
print("STRESS DE SLIPPADE — OOS avgR por (config × slippage bps en salidas taker)")
print("="*88)
print("Contexto: stop BTC base ~0.37%, a 0.7x ~0.26%. 1bp=0.01%. fee maker/taker ya incluido.\n")
CONFIGS = {
    "base (stop 1.0, trail 4)":        dict(),
    "stop_scale=0.8":                  dict(stop_scale=0.8),
    "stop_scale=0.7":                  dict(stop_scale=0.7),
    "trail_atr=6":                     dict(trail_atr=6.0),
    "stop=0.8 + trail=6":              dict(stop_scale=0.8, trail_atr=6.0),
}
SLIPS=[0.0, 0.5, 1.0, 2.0]
hdr="  {:<26}".format("config")+"".join(f"slip{int(s*10)/10 if s%1 else int(s)}bp".rjust(14) for s in SLIPS)
print(hdr.replace("slip0bp","   slip0bp"))
print("  "+"-"*86)
for name,cfg in CONFIGS.items():
    cells=[]
    for s in SLIPS:
        vals={sym:run(sym,slip_bps=s,**cfg) for sym in DATA}
        cells.append("/".join(f"{vals[x]:+.2f}" for x in ["BTCUSDT","ETHUSDT","SOLUSDT"]))
    print(f"  {name:<26}"+"".join(c.rjust(20) for c in cells))

print("\n  (cada celda = BTC/ETH/SOL OOS avgR)")
print("\n  Lectura: si stop_scale<1 sigue >= base a 1-2bp en LOS 3 -> real. Si cae bajo base -> artefacto.")

# veredicto explícito a 1bp y 2bp
print("\n=== VEREDICTO a slippage realista ===")
for s in (1.0, 2.0):
    base={sym:run(sym,slip_bps=s) for sym in DATA}
    for name,cfg in [("stop_scale=0.7",dict(stop_scale=0.7)),
                     ("stop_scale=0.8",dict(stop_scale=0.8)),
                     ("trail_atr=6",dict(trail_atr=6.0)),
                     ("stop=0.8+trail=6",dict(stop_scale=0.8,trail_atr=6.0))]:
        v={sym:run(sym,slip_bps=s,**cfg) for sym in DATA}
        ok=all(v[x]>base[x] for x in DATA)
        d=" ".join(f"{x[:3]}{v[x]-base[x]:+.2f}" for x in DATA)
        print(f"  slip{s:.0f}bp  {name:<20} vs base@{s:.0f}bp: {d}  {'PASA' if ok else 'NO'}")
