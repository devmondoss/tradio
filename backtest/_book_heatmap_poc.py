"""
_book_heatmap_poc.py v2 — PoC heatmap del libro completo, capturando el libro
en el momento del FILL (cuando el precio toca el entry), no en la señal.

Features: liquidez hacia target vs stop, muro más grande en el camino al target.
"""
import sys, zipfile, json, glob, datetime as dt
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
import _listas2 as L2
from _strategy_ab import run_system
from _audit_mirror import gen_h21_short

SYM = sys.argv[1] if len(sys.argv) > 1 else "ETHUSDT"
NDAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 15
OB_DIR = {"ETHUSDT": "E:/bybit-data/bybit-perp-eth/orderbook",
          "SOLUSDT": "E:/bybit-data/bybit-perp-sol/orderbook"}[SYM]
PARQ = {"ETHUSDT": "E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet",
        "SOLUSDT": "E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"}[SYM]
TIMEOUT_MS = 24 * 3600 * 1000

L2.M1 = Path(PARQ); t = L2.load2(15, start_ms=0); a = L2.A2(t); m1 = L2.load_m1_exit(start_ms=0)
df = run_system(a, [L2.gen_h5(), L2.gen_h21(), gen_h21_short()], m1, 15, mode="routed", max_day=4, cooldown=3)
df = df[df.oos].reset_index(drop=True).reset_index().rename(columns={"index": "tid"})
df["day"] = df.ts.apply(lambda ms: dt.datetime.fromtimestamp(ms/1000, dt.timezone.utc).strftime("%Y-%m-%d"))
zips = {Path(p).name.split("_")[0]: p for p in glob.glob(f"{OB_DIR}/*.zip")}
counts = df[df.day.isin(zips)].day.value_counts()
days = list(counts.index[:NDAYS])
print(f"{SYM}: {len(df)} trades OOS | procesando top {len(days)} días (~{int(counts[days].sum())} trades)\n", flush=True)

def book_at_fills(zip_path, trades):
    """Captura el libro en el instante en que el mid cruza el entry de cada trade."""
    pend = sorted(trades, key=lambda x: x["ts"]); pi = 0
    active = []; out = {}; bids = {}; asks = {}
    with zipfile.ZipFile(zip_path) as zf:
        with zf.open(zf.namelist()[0]) as f:
            for line in f:
                try: m = json.loads(line)
                except Exception: continue
                ts = m["ts"]; d = m["data"]
                if m["type"] == "snapshot":
                    bids = {float(p): float(s) for p, s in d.get("b", [])}
                    asks = {float(p): float(s) for p, s in d.get("a", [])}
                else:
                    for p, s in d.get("b", []):
                        p, s = float(p), float(s); bids.pop(p, None) if s == 0 else bids.__setitem__(p, s)
                    for p, s in d.get("a", []):
                        p, s = float(p), float(s); asks.pop(p, None) if s == 0 else asks.__setitem__(p, s)
                while pi < len(pend) and ts >= pend[pi]["ts"]:
                    active.append(pend[pi]); pi += 1
                if not active or not bids or not asks: continue
                mid = (max(bids) + min(asks)) / 2
                still = []
                for tr in active:
                    hit = (mid <= tr["entry"]) if tr["side"] == "long" else (mid >= tr["entry"])
                    if hit:
                        out[tr["tid"]] = (dict(bids), dict(asks))
                    elif ts - tr["ts"] < TIMEOUT_MS:
                        still.append(tr)
                active = still
                if pi >= len(pend) and not active: break
    return out

rows = []
for di, day in enumerate(days, 1):
    sub = df[df.day == day]
    print(f"  [{di}/{len(days)}] {day} ({len(sub)} trades)...", flush=True)
    trades = sub.to_dict("records")
    books = book_at_fills(zips[day], trades)
    for x in trades:
        bk = books.get(x["tid"])
        if not bk: continue
        bids, asks = bk
        e, risk, side = x["entry"], x["risk"], x["side"]
        long = side == "long"
        tp = x["entry"] + 3*risk if long else x["entry"] - 3*risk  # proxy 3R hacia target
        win = 3 * risk
        # muros relevantes: hacia el target y hacia el stop (desde el precio = entry)
        up = {p: s for p, s in asks.items() if e < p <= e + win}       # liquidez arriba
        dn = {p: s for p, s in bids.items() if e - win <= p < e}        # liquidez abajo
        if not up or not dn: continue
        liq_up, liq_dn = sum(up.values()), sum(dn.values())
        liq_target = liq_up if long else liq_dn
        liq_stop = liq_dn if long else liq_up
        wall_t_p, wall_t_s = (max(up.items(), key=lambda kv: kv[1]) if long else max(dn.items(), key=lambda kv: kv[1]))
        rows.append(dict(r=x["r"], side=side,
                         liq_ratio=liq_target / (liq_stop + 1e-9),
                         wall_t_dist=abs(wall_t_p - e) / risk,
                         wall_t_sz=wall_t_s,
                         book_imb=(liq_target - liq_stop) / (liq_target + liq_stop)))

if not rows:
    print("\nSin datos."); sys.exit()
R = pd.DataFrame(rows)
print(f"\ntrades con libro EN EL FILL: {len(R)}  (ganadores {sum(R.r>0)})\n")
print("== correlación features de libro con R_final ==")
for c in ["liq_ratio", "wall_t_dist", "wall_t_sz", "book_imb"]:
    print(f"  corr({c:<12}, R) = {np.corrcoef(R[c], R.r)[0,1]:+.3f}")
print("\n== avgR según liquidez hacia el target ==")
hi = R[R.liq_ratio > 1.3]; lo = R[R.liq_ratio < 0.77]
print(f"  más liq hacia TARGET (imán):  avgR {hi.r.mean():+.2f} WR {(hi.r>0).mean()*100:.0f}% n{len(hi)}")
print(f"  más liq hacia STOP (muro):    avgR {lo.r.mean():+.2f} WR {(lo.r>0).mean()*100:.0f}% n{len(lo)}")
