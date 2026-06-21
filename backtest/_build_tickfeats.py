"""
_build_tickfeats.py — Columnas derivadas de raw_trades para H8, H10, H12
========================================================================
Las hipótesis 8/10/12 requieren datos que el M1 agregado no tiene; se reconstruyen desde
los ticks (raw_trades/*.parquet, 365 días). Genera:
  data/bybit-perp/_tickfeats_m5.parquet  (M5-alineado):
     absorbed_neg, absorbed_pos : delta absorbido intrabar = |min/max cumdelta| - |close| (H12)
     dmin, dmax                 : cumdelta mín/máx intrabar (H12)
  data/bybit-perp/_dayvp.parquet  (1 fila/día):
     vp_poc_d, vp_vah_d, vp_val_d : perfil de volumen del día desde ticks (H8 merge)
"""
from pathlib import Path
import numpy as np, pandas as pd, glob

ROOT = Path(__file__).parent.parent
FILES = sorted(glob.glob(str(ROOT/"data/bybit-perp/raw_trades/*.parquet")))
BIN = 5.0   # $ por nivel para el volume profile diario

def day_vp(price, size):
    lo=np.floor(price.min()/BIN)*BIN
    idx=((price-lo)//BIN).astype(int)
    vol=np.bincount(idx, weights=size)
    levels=lo+np.arange(len(vol))*BIN
    poc=levels[vol.argmax()]
    # value area 70% alrededor del POC (expansión bidireccional)
    order=np.argsort(vol)[::-1]; tot=vol.sum(); cum=0.0; sel=[]
    for k in order:
        sel.append(k); cum+=vol[k]
        if cum>=0.70*tot: break
    va=levels[sel]; return poc, va.max(), va.min()

def main():
    m5_rows=[]; day_rows=[]
    for fp in FILES:
        d=pd.read_parquet(fp, columns=["ts_ms","price","size","side"])
        sz=d["size"].values.astype(float)
        sgn=np.where(d.side.values=="Buy", 1.0, -1.0)*sz
        price=d.price.values.astype(float)
        # --- daily VP
        poc,vah,val=day_vp(price, sz)
        day=int(d.ts_ms.values[0]//86_400_000)
        day_rows.append((day*86_400_000, poc, vah, val))
        # --- M5 intrabar absorbed delta
        g=(d.ts_ms.values//300_000)
        df=pd.DataFrame({"g":g,"sgn":sgn})
        for gv, sub in df.groupby("g"):
            cd=np.cumsum(sub.sgn.values); mn=cd.min(); mx=cd.max(); cl=cd[-1]
            an = abs(mn)-abs(cl) if mn<0 else 0.0
            ap = mx-abs(cl) if mx>0 else 0.0
            m5_rows.append((int(gv*300_000), an, ap, mn, mx))
        print(".", end="", flush=True)
    print()
    m5=pd.DataFrame(m5_rows, columns=["ts_ms","absorbed_neg","absorbed_pos","dmin","dmax"])
    m5.to_parquet(ROOT/"data/bybit-perp/_tickfeats_m5.parquet", index=False)
    dv=pd.DataFrame(day_rows, columns=["ts_ms","vp_poc_d","vp_vah_d","vp_val_d"])
    dv.to_parquet(ROOT/"data/bybit-perp/_dayvp.parquet", index=False)
    print(f"OK m5={len(m5)} rows, dayvp={len(dv)} días")

if __name__=="__main__": main()
