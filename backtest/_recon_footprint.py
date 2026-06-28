"""
_recon_footprint.py — ¿los niveles de entrada eran nodos de volumen REALES?
===========================================================================
Reconstruye el volume-profile/footprint de las 24h previas a cada entrada (desde ticks)
y mide la distancia entry<->POC reconstruido. Valida que entramos en zonas de volumen
genuinas (no en aire). También POC del día y value area.
"""
import json, urllib.request, sys
from pathlib import Path
import numpy as np, pandas as pd

URL="https://jubpovmsfvaqfnidozfh.supabase.co"
KEY=("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imp1YnBvdm1z"
     "ZnZhcWZuaWRvemZoIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MTk4OTY1NywiZXhwIjoyMDk3"
     "NTY1NjU3fQ.X_UMT7FypCvmPX1WxmI_Sg29FVRG2IliVBOwkWMh3ZI")
DIRS={"BTCUSDT":"E:/bybit-data/bybit-perp-btc/raw_trades","ETHUSDT":"E:/bybit-data/bybit-perp-eth/raw_trades",
      "SOLUSDT":"E:/bybit-data/bybit-perp-sol/raw_trades"}
BINPCT={"BTCUSDT":0.0001,"ETHUSDT":0.0001,"SOLUSDT":0.0001}  # bin proporcional ~1bp
FLIP=pd.Timestamp("2026-06-25 14:00",tz="UTC")

def get(q):
    req=urllib.request.Request(f"{URL}/rest/v1/{q}",headers={"apikey":KEY,"Authorization":f"Bearer {KEY}"})
    with urllib.request.urlopen(req,timeout=60) as r: return json.loads(r.read())
def ms(iso): return int(pd.Timestamp(iso).timestamp()*1000)

_cache={}
def load(sym):
    if sym in _cache: return _cache[sym]
    d=Path(DIRS[sym]); days=sorted(p for p in d.glob("2026-06-2*.parquet") if p.stem>="2026-06-21")
    df=pd.concat([pd.read_parquet(p,columns=["ts_ms","price","size"]) for p in days],ignore_index=True).sort_values("ts_ms")
    _cache[sym]=(df.ts_ms.values.astype(np.int64),df.price.values.astype(np.float64),df["size"].values.astype(np.float64))
    return _cache[sym]

def poc_va(sym, t0, t1, ref_px):
    ts,px,sz=load(sym); i0=np.searchsorted(ts,t0); i1=np.searchsorted(ts,t1)
    if i1-i0<50: return None
    p=px[i0:i1]; s=sz[i0:i1]
    binsz=ref_px*BINPCT[sym]
    b=np.round(p/binsz).astype(np.int64)
    vol=pd.Series(s).groupby(b).sum()
    poc_bin=vol.idxmax(); poc=poc_bin*binsz
    # value area 70%
    order=vol.sort_values(ascending=False); cum=order.cumsum(); sel=order[cum<=0.70*vol.sum()]
    if len(sel)==0: sel=order.head(1)
    vah=sel.index.max()*binsz; val=sel.index.min()*binsz
    return poc,vah,val

def main():
    tr=pd.DataFrame(get("liquidity_paper_trades?select=*&reconstructed=eq.false&order=created_at.asc&limit=10000"))
    tr["created_at"]=pd.to_datetime(tr.created_at,utc=True)
    c=tr[tr.created_at>FLIP].copy()
    rows=[]
    for _,t in c.iterrows():
        o=ms(t["opened_at"]); e=float(t["entry"])
        r=poc_va(t["symbol"], o-24*3600_000, o, e)
        if r is None: continue
        poc,vah,val=r
        rows.append(dict(symbol=t["symbol"],kind=t["kind"],side=t["side"],result_r=float(t["result_r"]),
                         entry=e,poc=poc,dist_poc=abs(e-poc)/e*100,
                         in_va=(val<=e<=vah) or (vah<=e<=val)))
    R=pd.DataFrame(rows)
    print(f"niveles reconstruidos: {len(R)}")
    print(f"\n=== distancia entry <-> POC reconstruido (24h) ===")
    print(R.dist_poc.describe(percentiles=[.25,.5,.75,.9]).round(3).to_string())
    print(f"\n  entradas dentro de ±0.3% del POC: {int((R.dist_poc<=0.3).sum())}/{len(R)} ({100*(R.dist_poc<=0.3).mean():.0f}%)")
    print(f"  entradas dentro del value area:   {int(R.in_va.sum())}/{len(R)} ({100*R.in_va.mean():.0f}%)")
    print(f"\n=== por kind ===")
    for k,d in R.groupby("kind"):
        print(f"  {k:<16} n={len(d):>3} dist_poc_med={d.dist_poc.median():.3f}% in_VA={100*d.in_va.mean():.0f}%")
    # ¿la cercanía al POC predice el outcome?
    near=R[R.dist_poc<=0.3]; far=R[R.dist_poc>0.3]
    print(f"\n=== outcome cerca vs lejos del POC ===")
    print(f"  cerca POC (<=0.3%): n={len(near)} avgR={near.result_r.mean():+.3f} WR={100*(near.result_r>0).mean():.0f}%")
    print(f"  lejos POC (>0.3%):  n={len(far)} avgR={far.result_r.mean():+.3f} WR={100*(far.result_r>0).mean():.0f}%")

if __name__=="__main__": main()
