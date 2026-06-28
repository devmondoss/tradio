"""
_recon_liquidations.py — ¿las liquidaciones ayudan a nuestros trades?
=====================================================================
Para cada trade nativo (era limpia), mide la liquidación cerca del nivel/tiempo de entrada:
ventana [open-30min, open+5min], precio dentro de ±0.3% del entry.
FAVORABLE = Sell-liq para un long (longs capitulando en soporte) / Buy-liq para un short.
Compara outcome (avgR/WR) de trades CON cluster favorable vs SIN. Honesto sobre el n.
"""
import json, urllib.request
import numpy as np, pandas as pd

URL = "https://jubpovmsfvaqfnidozfh.supabase.co"
KEY = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
       ".eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imp1YnBvdm1zZnZhcWZuaWRvemZoIiwicm9sZSI6"
       "InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MTk4OTY1NywiZXhwIjoyMDk3NTY1NjU3fQ"
       ".X_UMT7FypCvmPX1WxmI_Sg29FVRG2IliVBOwkWMh3ZI")
FLIP = pd.Timestamp("2026-06-25 14:00", tz="UTC")

def get(q, headers=None):
    req = urllib.request.Request(f"{URL}/rest/v1/{q}", headers={"apikey":KEY,"Authorization":f"Bearer {KEY}",**(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r: return json.loads(r.read())

def get_all(table, select="*", order="ts_ms.asc"):
    out=[]; off=0
    while True:
        chunk=get(f"{table}?select={select}&order={order}&limit=1000&offset={off}")
        if not chunk: break
        out+=chunk; off+=1000
        if len(chunk)<1000: break
    return pd.DataFrame(out)

def ms(iso): return int(pd.Timestamp(iso).timestamp()*1000)

# liquidaciones (paginado)
lq = get_all("liquidity_liquidations", "ts_ms,symbol,side,price,usd")
lq["usd"]=pd.to_numeric(lq.usd,errors="coerce"); lq["price"]=pd.to_numeric(lq.price,errors="coerce")
print(f"liquidaciones: n={len(lq)}  rango {pd.to_datetime(lq.ts_ms.min(),unit='ms')} -> {pd.to_datetime(lq.ts_ms.max(),unit='ms')}")
print(f"  USD total: {lq.usd.sum():,.0f}  | Sell(longs liq)={lq[lq.side=='Sell'].usd.sum():,.0f}  Buy(shorts liq)={lq[lq.side=='Buy'].usd.sum():,.0f}")

tr = get_all("liquidity_paper_trades", "*", "created_at.asc")
tr["created_at"]=pd.to_datetime(tr.created_at,utc=True)
c = tr[(tr.reconstructed==False)&(tr.created_at>FLIP)].copy()
for col in ("entry","result_r"): c[col]=pd.to_numeric(c[col],errors="coerce")
LIQ_START = lq.ts_ms.min()

PROX_PCT=0.003; WIN_PRE=30*60_000; WIN_POST=5*60_000
def liq_around(t):
    o=ms(t["opened_at"]); e=float(t["entry"]); sym=t["symbol"]
    w=lq[(lq.symbol==sym)&(lq.ts_ms>=o-WIN_PRE)&(lq.ts_ms<=o+WIN_POST)&((lq.price-e).abs()/e<=PROX_PCT)]
    fav_side = "Sell" if t["side"]=="long" else "Buy"
    fav=w[w.side==fav_side].usd.sum(); against=w[w.side!=fav_side].usd.sum()
    return fav, against, len(w)

rows=[]
for _,t in c.iterrows():
    if ms(t["opened_at"]) < LIQ_START: continue   # sin data de liq para ese trade
    fav,ag,n=liq_around(t)
    rows.append(dict(symbol=t["symbol"],side=t["side"],gestion=t["gestion"],result_r=t["result_r"],
                     liq_fav=fav,liq_against=ag,liq_n=n))
R=pd.DataFrame(rows)
print(f"\ntrades con data de liq disponible (opened>=liq_start): {len(R)} de {len(c)} (el resto es antes del 06-24)")
if not len(R):
    raise SystemExit("sin overlap")

def agg(name,d):
    if not len(d): print(f"  {name:<32} n=0"); return
    r=d.result_r
    print(f"  {name:<32} n={len(d):>3} avgR={r.mean():+.3f} WR={100*(r>0).mean():>3.0f}% sumR={r.sum():+6.1f}")

print("\n=== outcome segun cluster de liquidacion FAVORABLE cerca del nivel/entrada ===")
for thr in (0, 50_000, 200_000, 1_000_000):
    agg(f"liq_fav > ${thr:,}", R[R.liq_fav>thr])
print("\n  (referencia)")
agg("SIN liq favorable (==0)", R[R.liq_fav==0])
agg("TODOS (con data liq)", R)

print("\n=== ¿y si hay liq EN CONTRA (cascada que nos atropella)? ===")
agg("liq_against > $200k", R[R.liq_against>200_000])
agg("liq_against == 0", R[R.liq_against==0])

print("\n=== correlacion liq_fav vs result_r ===")
if R.liq_fav.std()>0:
    print(f"  corr(liq_fav, result_r) = {R.liq_fav.corr(R.result_r):+.3f}")
    print(f"  corr(liq_fav-liq_against, result_r) = {(R.liq_fav-R.liq_against).corr(R.result_r):+.3f}")
print("\n  reparto: trades con algun cluster favorable:", int((R.liq_fav>0).sum()), "/", len(R))
