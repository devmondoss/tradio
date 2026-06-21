"""
paper_liquidity.py — Validación en vivo del FILL RATIO maker (cartera de liquidez, CONFIG FINAL)
================================================================================================
Mide el único riesgo que el backtest no puede zanjar: ¿se llenan de verdad las órdenes límite maker
en los niveles de volumen, y los fills mantienen la expectativa? Conecta al feed PÚBLICO de Bybit
(datos reales), repone órdenes límite VIRTUALES en los niveles de live/levels.py (M15, estructural),
y mide en tiempo real — SEPARADO por régimen de volatilidad — el fill ratio, el outcome (stop/TP) y
el PnL paper. Gestión fiel: parcial 50% en POC → breakeven → resto al target estructural.

Persistencia (Railway): si SUPABASE_URL/SUPABASE_KEY están seteadas, escribe trades cerrados y
snapshots periódicos a Supabase; si no, log local a CSV.

Modos:
  DRY-RUN (default, sin claves, sin riesgo): órdenes virtuales sobre datos reales mainnet.
  LIVE-TESTNET (--live-testnet + BYBIT_TESTNET=true + claves): órdenes POST-ONLY reales en testnet.

Env: SYMBOL, TF (5|15), HIGH_VOL_ONLY (true|false), SUPABASE_URL, SUPABASE_KEY,
     BYBIT_API_KEY, BYBIT_API_SECRET, BYBIT_TESTNET.
Uso local: python -u live/paper_liquidity.py [--tf 15] [--high-vol-only]
"""
import os, json, time, argparse, csv, threading
from datetime import datetime, timezone
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
import requests
import websocket  # websocket-client
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
from levels import compute_levels

SYMBOL = os.getenv("SYMBOL", "BTCUSDT")
REST = "https://api.bybit.com"
WS_PUBLIC = "wss://stream.bybit.com/v5/public/linear"
LOG = Path(__file__).parent / "paper_fills.csv"
FEE_RT = 0.0004          # maker round-trip
SUPA_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPA_KEY = os.getenv("SUPABASE_KEY", "")

# --------------------------------------------------------------------------- Supabase (REST)
def supa_insert(table, rows):
    if not (SUPA_URL and SUPA_KEY) or not rows: return
    try:
        requests.post(f"{SUPA_URL}/rest/v1/{table}",
                      headers={"apikey": SUPA_KEY, "Authorization": f"Bearer {SUPA_KEY}",
                               "Content-Type": "application/json", "Prefer": "return=minimal"},
                      data=json.dumps(rows), timeout=10)
    except Exception as e:
        print("supabase insert error:", e)

def iso(ms): return datetime.fromtimestamp(ms/1000, tz=timezone.utc).isoformat()

# --------------------------------------------------------------------------- health server (Railway $PORT)
_BOOK = None
def start_health(port):
    """Mini HTTP server para el healthcheck de Railway + ver el fill ratio en vivo (GET /)."""
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(_BOOK.snapshot_row() if _BOOK else {"status":"starting"}).encode()
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
        def log_message(self, *a): pass
    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"health server en :{port} (GET / = fill ratio en vivo)")

# --------------------------------------------------------------------------- libro paper
class PaperBook:
    """Órdenes límite virtuales + posiciones; fill ratio y PnL SEPARADOS por régimen de volatilidad.
    Gestión fiel: parcial 50% en POC (tp1) → stop a breakeven → resto al target estructural (tp)."""
    def __init__(self, tf):
        self.tf = tf
        self.resting = []      # niveles activos
        self.open_pos = []     # posiciones abiertas (con estado de gestión)
        self.placed = {"high":0, "low":0}   # contadores en-memoria (solo vista 'desde restart')
        self.filled = {"high":0, "low":0}
        self.closed = []       # acumulado en-memoria (para stats live)
        self.pending_log = []  # trades cerrados aún no escritos
        self.pending_events = []  # eventos place/fill aún no escritos (fuente de verdad, restart-proof)
        self.lock = threading.Lock()

    def _ev(self, etype, lv, ts):
        return dict(at=iso(ts), symbol=SYMBOL, tf=self.tf, event_type=etype,
                    kind=lv["kind"], side=lv["side"], vol_regime=lv.get("vol_regime","low"),
                    price=lv["price"] if "price" in lv else lv.get("entry"))

    def refresh(self, levels, ts):
        with self.lock:
            for lv in levels:
                self.placed[lv.get("vol_regime","low")] += 1
                self.pending_events.append(self._ev("place", lv, ts))   # evento PLACE por nivel
            self.resting = [dict(**lv, placed_ts=ts) for lv in levels]

    def on_trade(self, px, ts):
        with self.lock:
            still = []
            for o in self.resting:
                hit = (o["side"]=="long" and px <= o["price"]) or (o["side"]=="short" and px >= o["price"])
                if hit:
                    reg = o.get("vol_regime","low"); self.filled[reg] += 1
                    self.pending_events.append(self._ev("fill", o, ts))   # evento FILL
                    self.open_pos.append(dict(side=o["side"], entry=o["price"], stop=o["stop"],
                                              tp1=o.get("tp1"), tp=o["tp"], kind=o["kind"], vol_regime=reg,
                                              fill_ts=ts, cur_stop=o["stop"], realized=0.0, rem=1.0, filled1=False))
                else:
                    still.append(o)
            self.resting = still
            rem_pos = []
            for p in self.open_pos:
                risk = abs(p["entry"]-p["stop"])
                if risk <= 0: continue
                done = False; reason = None; exit_px = None
                if p["side"]=="long":
                    if px <= p["cur_stop"]:
                        p["realized"] += p["rem"]*((p["cur_stop"]-p["entry"])/risk)
                        reason = "breakeven" if p["filled1"] else "stop"; exit_px = p["cur_stop"]; done = True
                    elif (not p["filled1"]) and p["tp1"] and px >= p["tp1"]:
                        p["realized"] += 0.5*((p["tp1"]-p["entry"])/risk); p["rem"] -= 0.5
                        p["filled1"] = True; p["cur_stop"] = p["entry"]
                    if (not done) and px >= p["tp"]:
                        p["realized"] += p["rem"]*((p["tp"]-p["entry"])/risk); reason="target"; exit_px=p["tp"]; done=True
                else:
                    if px >= p["cur_stop"]:
                        p["realized"] += p["rem"]*((p["entry"]-p["cur_stop"])/risk)
                        reason = "breakeven" if p["filled1"] else "stop"; exit_px = p["cur_stop"]; done = True
                    elif (not p["filled1"]) and p["tp1"] and px <= p["tp1"]:
                        p["realized"] += 0.5*((p["entry"]-p["tp1"])/risk); p["rem"] -= 0.5
                        p["filled1"] = True; p["cur_stop"] = p["entry"]
                    if (not done) and px <= p["tp"]:
                        p["realized"] += p["rem"]*((p["entry"]-p["tp"])/risk); reason="target"; exit_px=p["tp"]; done=True
                if done:
                    r = p["realized"] - FEE_RT*p["entry"]/risk
                    rec = dict(symbol=SYMBOL, tf=str(self.tf), kind=p["kind"], side=p["side"],
                               vol_regime=p["vol_regime"], entry=p["entry"], stop=p["stop"], target=p["tp"],
                               exit_price=exit_px, result_r=round(r,4), win=bool(r>0), reason=reason,
                               opened_at=iso(p["fill_ts"]), closed_at=iso(ts))
                    self.closed.append(rec); self.pending_log.append(rec)
                else:
                    rem_pos.append(p)
            self.open_pos = rem_pos

    def drain_log(self):
        with self.lock:
            out = self.pending_log; self.pending_log = []
            return out

    def drain_events(self):
        with self.lock:
            out = self.pending_events; self.pending_events = []
            return out

    def snapshot_row(self):
        with self.lock:
            df = pd.DataFrame(self.closed)
            row = dict(symbol=SYMBOL, tf=str(self.tf), at=iso(int(time.time()*1000)),
                       resting=len(self.resting), open_pos=len(self.open_pos))
            for reg in ("high","low"):
                pl, fi = self.placed[reg], self.filled[reg]
                seg = df[df.vol_regime==reg] if len(df) else df
                row[f"placed_{reg}"]=pl; row[f"filled_{reg}"]=fi
                row[f"fill_ratio_{reg}"]=round(fi/max(pl,1),4)
                row[f"closed_{reg}"]=len(seg)
                row[f"wr_{reg}"]=round(float((seg.win).mean()),4) if len(seg) else None
                row[f"avg_r_{reg}"]=round(float(seg.result_r.mean()),4) if len(seg) else None
            return row

    def line(self):
        r = self.snapshot_row()
        return (f"VOL-HIGH fill={r['filled_high']}/{r['placed_high']} ({r['fill_ratio_high']*100:.0f}%) "
                f"cerr={r['closed_high']} avgR={r['avg_r_high']} | "
                f"VOL-LOW fill={r['filled_low']}/{r['placed_low']} ({r['fill_ratio_low']*100:.0f}%) "
                f"cerr={r['closed_low']} avgR={r['avg_r_low']}")

# --------------------------------------------------------------------------- feed
def bootstrap(interval):
    r = requests.get(f"{REST}/v5/market/kline",
                     params=dict(category="linear", symbol=SYMBOL, interval=interval, limit=1000), timeout=10)
    k = r.json()["result"]["list"][::-1]
    return pd.DataFrame([dict(ts_ms=int(x[0]), open=float(x[1]), high=float(x[2]),
                              low=float(x[3]), close=float(x[4]), volume=float(x[5])) for x in k])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-testnet", action="store_true")
    ap.add_argument("--high-vol-only", action="store_true",
                    default=os.getenv("HIGH_VOL_ONLY","").lower()=="true")
    ap.add_argument("--tf", default=os.getenv("TF","15"))
    args = ap.parse_args()
    HVO = args.high_vol_only; TF = str(args.tf)
    book = PaperBook(TF)
    global _BOOK; _BOOK = book
    port = os.getenv("PORT")
    if port: start_health(int(port))   # Railway inyecta PORT → healthcheck OK + stats por HTTP
    sink = "Supabase" if (SUPA_URL and SUPA_KEY) else "CSV local"
    print(f">>> {'LIVE-TESTNET' if args.live_testnet else 'DRY-RUN'} · {SYMBOL} · M{TF} · "
          f"high_vol_only={HVO} · persistencia={sink}\n")
    if args.live_testnet and (os.getenv("BYBIT_TESTNET","").lower()!="true" or not os.getenv("BYBIT_API_KEY")):
        print("LIVE-TESTNET requiere BYBIT_TESTNET=true + claves. Abortando por seguridad."); return

    m = bootstrap(TF)
    book.refresh(compute_levels(m, high_vol_only=HVO), int(time.time()*1000))
    print(f"Bootstrap {len(m)} velas M{TF}. Niveles activos: {len(book.resting)}")
    for o in book.resting:
        print(f"   {o['side']:>5} {o['kind']:<14} @ {o['price']:.1f}  stop {o['stop']:.1f}  tp {o['tp']:.1f}  vol={o['vol_regime']}")
    if not (SUPA_URL and SUPA_KEY) and not LOG.exists():
        with open(LOG,"w",newline="") as f:
            csv.writer(f).writerow(["closed_at","kind","side","vol_regime","entry","exit","result_r","reason"])

    def on_msg(ws, msg):
        d = json.loads(msg); topic = d.get("topic","")
        if topic.startswith("publicTrade"):
            for t in d.get("data",[]): book.on_trade(float(t["p"]), int(t["T"]))
            ev = book.drain_events()
            if ev: supa_insert("liquidity_paper_events", ev)   # fills (restart-proof)
            new = book.drain_log()
            if new:
                supa_insert("liquidity_paper_trades", new)
                if not (SUPA_URL and SUPA_KEY):
                    with open(LOG,"a",newline="") as f:
                        w=csv.writer(f)
                        for r in new: w.writerow([r["closed_at"],r["kind"],r["side"],r["vol_regime"],
                                                  r["entry"],r["exit_price"],r["result_r"],r["reason"]])
        elif topic.startswith("kline"):
            for bar in d.get("data",[]):
                if bar.get("confirm"):
                    row = dict(ts_ms=int(bar["start"]), open=float(bar["open"]), high=float(bar["high"]),
                               low=float(bar["low"]), close=float(bar["close"]), volume=float(bar["volume"]))
                    nonlocal m
                    m = pd.concat([m, pd.DataFrame([row])], ignore_index=True).tail(2000)
                    book.refresh(compute_levels(m, high_vol_only=HVO), row["ts_ms"])
                    ev = book.drain_events()
                    if ev: supa_insert("liquidity_paper_events", ev)   # places (restart-proof)
                    print(f"[{datetime.now(timezone.utc):%m-%d %H:%M}] M{TF} @ {row['close']:.1f} | "
                          f"niveles={len(book.resting)} | {book.line()}")
                    supa_insert("liquidity_paper_snapshots", [book.snapshot_row()])

    def on_open(ws):
        ws.send(json.dumps({"op":"subscribe","args":[f"publicTrade.{SYMBOL}", f"kline.{TF}.{SYMBOL}"]}))
        print(f"WS suscrito a publicTrade + kline.{TF}\n")

    while True:
        try:
            websocket.WebSocketApp(WS_PUBLIC, on_open=on_open, on_message=on_msg).run_forever(
                ping_interval=20, ping_timeout=10)
        except Exception as e:
            print("WS error, reconectando en 5s:", e); time.sleep(5)

if __name__ == "__main__":
    main()
