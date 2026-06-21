"""
paper_liquidity.py — Paper trading de MAKER y FLOW (fill ratio + PnL virtual)
===============================================================================
MAKER (--system maker, default):
  Fade puro. Provee liquidez maker en niveles de volumen (POC OB / POC def / naked POC).
  Gestion: parcial 50% en TP1 (si TP1 >= 2.3R) -> stop a BE -> target estructural.

FLOW (--system flow):
  Mismas entradas. Chop -> fade identico a MAKER. Tendencia -> ATR trail (4xATR).

Mide el unico riesgo que el backtest no puede zanjar: fill ratio real de ordenes maker.
Conecta al feed PUBLICO de Bybit (datos reales), coloca ordenes virtuales en los niveles
de levels.py (M15, estructural) y mide en tiempo real el fill ratio, outcome y PnL paper.

Persistencia (Railway): SUPABASE_URL/SUPABASE_KEY -> Supabase. Si no: CSV local.

Env: SYSTEM (maker|flow), SYMBOL, TF, HIGH_VOL_ONLY,
     SUPABASE_URL, SUPABASE_KEY, BYBIT_API_KEY, BYBIT_API_SECRET, BYBIT_TESTNET.

Uso local:
  python -u live/paper_liquidity.py --system maker
  python -u live/paper_liquidity.py --system flow
Railway: env SYSTEM=maker o SYSTEM=flow (default maker).
"""
import os, json, time, argparse, csv, threading
from datetime import datetime, timezone
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
import requests
import websocket
import pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
from levels import compute_levels
from footprint import FootprintAccumulator

SYMBOL          = os.getenv("SYMBOL", "BTCUSDT")
REST            = "https://api.bybit.com"
WS_PUBLIC       = "wss://stream.bybit.com/v5/public/linear"
LOG             = Path(__file__).parent / "paper_fills.csv"
FEE_MAKER_SIDE  = 0.0002
FEE_TAKER_SIDE  = 0.00055
TRAIL_ATR       = 4.0   # multiplicador ATR trail (FLOW tendencia)
SUPA_URL        = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPA_KEY        = os.getenv("SUPABASE_KEY", "")


# ---------- Supabase -------------------------------------------------------
def supa_insert(table, rows):
    if not (SUPA_URL and SUPA_KEY) or not rows: return
    try:
        requests.post(f"{SUPA_URL}/rest/v1/{table}",
                      headers={"apikey": SUPA_KEY, "Authorization": f"Bearer {SUPA_KEY}",
                               "Content-Type": "application/json", "Prefer": "return=minimal"},
                      data=json.dumps(rows), timeout=10)
    except Exception as e:
        print("supabase error:", e)

def iso(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


# ---------- Health server (Railway $PORT) ----------------------------------
_BOOK = None
def start_health(port):
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(_BOOK.snapshot() if _BOOK else {"status": "starting"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
        def log_message(self, *a): pass
    threading.Thread(target=ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever,
                     daemon=True).start()
    print(f"health server :{port} (GET / = stats en vivo)")


# ---------- PaperBook ------------------------------------------------------
class PaperBook:
    """Ordenes limites virtuales. Soporta gestion fade (MAKER) y trail (FLOW)."""

    def __init__(self, tf, system):
        self.tf      = tf
        self.system  = system   # "maker" | "flow"
        self.resting = []
        self.open_pos = []
        self.placed  = {"high": 0, "low": 0}
        self.filled  = {"high": 0, "low": 0}
        self.closed  = []
        self.pending_log    = []
        self.pending_events = []
        self.lock = threading.Lock()

    def _ev(self, etype, lv, ts):
        return dict(at=iso(ts), symbol=SYMBOL, tf=self.tf, system=self.system,
                    event_type=etype, kind=lv.get("kind"), side=lv.get("side"),
                    vol_regime=lv.get("vol_regime", "low"),
                    regime=lv.get("regime", "chop"),
                    gestion=lv.get("gestion", "fade"),
                    price=lv.get("price", lv.get("entry")))

    def refresh(self, levels, ts):
        with self.lock:
            for lv in levels:
                self.placed[lv.get("vol_regime", "low")] += 1
                self.pending_events.append(self._ev("place", lv, ts))
            self.resting = [dict(**lv, placed_ts=ts) for lv in levels]

    def on_trade(self, px, ts):
        with self.lock:
            # -- Fills --
            still = []
            for o in self.resting:
                hit = ((o["side"] == "long"  and px <= o["price"]) or
                       (o["side"] == "short" and px >= o["price"]))
                if hit:
                    reg = o.get("vol_regime", "low")
                    self.filled[reg] += 1
                    self.pending_events.append(self._ev("fill", o, ts))
                    pos = dict(
                        side=o["side"], entry=o["price"], stop=o["stop"],
                        tp1=o.get("tp1"), tp=o["tp"], kind=o["kind"],
                        vol_regime=reg, regime=o.get("regime", "chop"),
                        gestion=o.get("gestion", "fade"),
                        atr0=o.get("atr", 0.0),
                        take_partial=o.get("take_partial", True),
                        fill_ts=ts,
                        # fade state
                        cur_stop=o["stop"], realized=0.0, rem=1.0, filled1=False,
                        # trail state
                        best_price=o["price"],
                        trail_stop=o["stop"],
                    )
                    self.open_pos.append(pos)
                else:
                    still.append(o)
            self.resting = still

            # -- Position management --
            rem_pos = []
            for p in self.open_pos:
                risk = abs(p["entry"] - p["stop"])
                if risk <= 0: continue
                done   = False
                reason = None
                exit_px = None

                if p["gestion"] == "trail":
                    # ATR trailing stop (FLOW tendencia)
                    atr0 = p["atr0"] if p["atr0"] > 0 else risk / TRAIL_ATR
                    if p["side"] == "long":
                        p["best_price"] = max(p["best_price"], px)
                        p["trail_stop"] = max(p["trail_stop"],
                                              p["best_price"] - TRAIL_ATR * atr0)
                        if px <= p["trail_stop"]:
                            exit_px = p["trail_stop"]; reason = "trail"; done = True
                    else:
                        p["best_price"] = min(p["best_price"], px)
                        p["trail_stop"] = min(p["trail_stop"],
                                              p["best_price"] + TRAIL_ATR * atr0)
                        if px >= p["trail_stop"]:
                            exit_px = p["trail_stop"]; reason = "trail"; done = True

                    if done:
                        r_gross = ((exit_px - p["entry"]) if p["side"] == "long"
                                   else (p["entry"] - exit_px)) / risk
                        fee_r   = (FEE_MAKER_SIDE + FEE_TAKER_SIDE) * p["entry"] / risk
                        r       = r_gross - fee_r

                else:
                    # Fade management (MAKER siempre; FLOW en chop)
                    if p["side"] == "long":
                        if px <= p["cur_stop"]:
                            p["realized"] += p["rem"] * ((p["cur_stop"] - p["entry"]) / risk)
                            reason = "breakeven" if p["filled1"] else "stop"
                            exit_px = p["cur_stop"]; done = True
                        elif not p["filled1"] and p["take_partial"] and p["tp1"] and px >= p["tp1"]:
                            p["realized"] += 0.5 * ((p["tp1"] - p["entry"]) / risk)
                            p["rem"] -= 0.5; p["filled1"] = True; p["cur_stop"] = p["entry"]
                        if not done and px >= p["tp"]:
                            p["realized"] += p["rem"] * ((p["tp"] - p["entry"]) / risk)
                            reason = "target"; exit_px = p["tp"]; done = True
                    else:
                        if px >= p["cur_stop"]:
                            p["realized"] += p["rem"] * ((p["entry"] - p["cur_stop"]) / risk)
                            reason = "breakeven" if p["filled1"] else "stop"
                            exit_px = p["cur_stop"]; done = True
                        elif not p["filled1"] and p["take_partial"] and p["tp1"] and px <= p["tp1"]:
                            p["realized"] += 0.5 * ((p["entry"] - p["tp1"]) / risk)
                            p["rem"] -= 0.5; p["filled1"] = True; p["cur_stop"] = p["entry"]
                        if not done and px <= p["tp"]:
                            p["realized"] += p["rem"] * ((p["entry"] - p["tp"]) / risk)
                            reason = "target"; exit_px = p["tp"]; done = True

                    if done:
                        exit_side = FEE_MAKER_SIDE if reason == "target" else FEE_TAKER_SIDE
                        fee_r = (FEE_MAKER_SIDE + (FEE_MAKER_SIDE * 0.5 if p["filled1"] else 0.0)
                                 + exit_side * p["rem"]) * p["entry"] / risk
                        r = p["realized"] - fee_r

                if done:
                    rec = dict(
                        symbol=SYMBOL, tf=str(self.tf), system=self.system,
                        kind=p["kind"], side=p["side"],
                        vol_regime=p["vol_regime"], regime=p["regime"], gestion=p["gestion"],
                        entry=p["entry"], stop=p["stop"], target=p["tp"],
                        exit_price=round(exit_px, 2), result_r=round(r, 4), win=bool(r > 0),
                        reason=reason,
                        opened_at=iso(p["fill_ts"]), closed_at=iso(ts),
                    )
                    self.closed.append(rec)
                    self.pending_log.append(rec)
                else:
                    rem_pos.append(p)
            self.open_pos = rem_pos

    def drain_log(self):
        with self.lock:
            out = self.pending_log; self.pending_log = []; return out

    def drain_events(self):
        with self.lock:
            out = self.pending_events; self.pending_events = []; return out

    def snapshot(self):
        with self.lock:
            df   = pd.DataFrame(self.closed) if self.closed else pd.DataFrame()
            snap = dict(symbol=SYMBOL, tf=str(self.tf), system=self.system,
                        at=iso(int(time.time() * 1000)),
                        resting=len(self.resting), open_pos=len(self.open_pos),
                        closed_total=len(df))
            for reg in ("high", "low"):
                pl, fi = self.placed[reg], self.filled[reg]
                seg = df[df.vol_regime == reg] if len(df) else df
                snap[f"placed_{reg}"]     = pl
                snap[f"filled_{reg}"]     = fi
                snap[f"fill_ratio_{reg}"] = round(fi / max(pl, 1), 4)
                snap[f"closed_{reg}"]     = len(seg)
                snap[f"wr_{reg}"]         = round(float(seg.win.mean()), 4) if len(seg) else None
                snap[f"avg_r_{reg}"]      = round(float(seg.result_r.mean()), 4) if len(seg) else None
            return snap

    def line(self):
        s = self.snapshot()
        return (f"VOL-HIGH fill={s['filled_high']}/{s['placed_high']}"
                f" ({s['fill_ratio_high']*100:.0f}%)"
                f" cerr={s['closed_high']} avgR={s['avg_r_high']} | "
                f"VOL-LOW fill={s['filled_low']}/{s['placed_low']}"
                f" ({s['fill_ratio_low']*100:.0f}%)"
                f" cerr={s['closed_low']} avgR={s['avg_r_low']}")


# ---------- Feed Bybit -----------------------------------------------------
def bootstrap(interval):
    r = requests.get(f"{REST}/v5/market/kline",
                     params=dict(category="linear", symbol=SYMBOL,
                                 interval=interval, limit=1000), timeout=10)
    k = r.json()["result"]["list"][::-1]
    return pd.DataFrame([dict(ts_ms=int(x[0]), open=float(x[1]), high=float(x[2]),
                              low=float(x[3]), close=float(x[4]), volume=float(x[5]))
                         for x in k])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--system", default=os.getenv("SYSTEM", "both"),
                    choices=["maker", "flow", "both"],
                    help="maker | flow | both (default). 'both' corre MAKER y FLOW en el mismo proceso.")
    ap.add_argument("--live-testnet", action="store_true")
    ap.add_argument("--high-vol-only", action="store_true",
                    default=os.getenv("HIGH_VOL_ONLY", "").lower() == "true")
    ap.add_argument("--tf", default=os.getenv("TF", "15"))
    args  = ap.parse_args()
    SYS   = args.system
    HVO   = args.high_vol_only
    TF    = str(args.tf)

    # Un PaperBook por sistema activo. "both" arranca los dos en paralelo.
    systems = ["maker", "flow"] if SYS == "both" else [SYS]
    books   = {s: PaperBook(TF, s) for s in systems}
    global _BOOK; _BOOK = list(books.values())[0]   # health muestra el primero

    port  = os.getenv("PORT")
    if port: start_health(int(port))

    sink  = "Supabase" if (SUPA_URL and SUPA_KEY) else "CSV local"
    print(f">>> {'LIVE-TESTNET' if args.live_testnet else 'DRY-RUN'}"
          f" · SYSTEM={SYS.upper()} · {SYMBOL} · M{TF}"
          f" · high_vol_only={HVO} · persistencia={sink}\n")

    if args.live_testnet and (os.getenv("BYBIT_TESTNET", "").lower() != "true"
                               or not os.getenv("BYBIT_API_KEY")):
        print("LIVE-TESTNET requiere BYBIT_TESTNET=true + claves. Abortando."); return

    fp = FootprintAccumulator()   # acumulador de ticks → volume profile real

    m  = bootstrap(TF)
    ts0 = int(time.time() * 1000)
    for s, book in books.items():
        lvls = compute_levels(m, system=s, high_vol_only=HVO, fp_bars=fp.bars())
        book.refresh(lvls, ts0)
        print(f"[{s.upper()}] Bootstrap {len(m)} velas M{TF}. Niveles: {len(book.resting)}")
        for o in book.resting:
            print(f"  {o['side']:>5} {o['kind']:<18} @ {o['price']:.1f}"
                  f"  stop={o['stop']:.1f}  tp={o['tp']:.1f}"
                  f"  gestion={o['gestion']}  vol={o['vol_regime']}"
                  f"  fp={o.get('fp_source','ohlcv')}")

    if not (SUPA_URL and SUPA_KEY) and not LOG.exists():
        with open(LOG, "w", newline="") as f:
            csv.writer(f).writerow([
                "closed_at", "system", "kind", "side", "vol_regime", "regime",
                "gestion", "entry", "exit_price", "result_r", "reason",
            ])

    def _flush(book):
        ev = book.drain_events()
        if ev: supa_insert("liquidity_paper_events", ev)
        new = book.drain_log()
        if new:
            supa_insert("liquidity_paper_trades", new)
            if not (SUPA_URL and SUPA_KEY):
                with open(LOG, "a", newline="") as f:
                    w = csv.writer(f)
                    for r in new:
                        w.writerow([r["closed_at"], r["system"], r["kind"], r["side"],
                                    r["vol_regime"], r["regime"], r["gestion"],
                                    r["entry"], r["exit_price"], r["result_r"], r["reason"]])

    def on_msg(ws, msg):
        d = json.loads(msg); topic = d.get("topic", "")
        if topic.startswith("publicTrade"):
            for t in d.get("data", []):
                px = float(t["p"]); ts = int(t["T"])
                vol  = float(t.get("v", 1.0))
                side = t.get("S", "Buy")          # "Buy" | "Sell"
                fp.on_trade(px, vol, side)         # acumular tick en footprint
                for book in books.values():
                    book.on_trade(px, ts)
            for book in books.values():
                _flush(book)
        elif topic.startswith("kline"):
            for bar in d.get("data", []):
                if bar.get("confirm"):
                    bar_ts = int(bar["start"])
                    row = dict(ts_ms=bar_ts, open=float(bar["open"]),
                               high=float(bar["high"]), low=float(bar["low"]),
                               close=float(bar["close"]), volume=float(bar["volume"]))
                    nonlocal m
                    m = pd.concat([m, pd.DataFrame([row])], ignore_index=True).tail(2000)
                    # Cerrar barra en el footprint ANTES de recalcular niveles
                    closed_fp = fp.on_bar_close(bar_ts)
                    fp_bars   = fp.bars()
                    if closed_fp:
                        print(f"  [FP] barra {bar_ts} POC={closed_fp['poc']:.1f}"
                              f"  delta={closed_fp['delta']:+.1f}"
                              f"  bars_acum={fp.n_bars()}"
                              f"  source={'tick' if len(fp_bars) >= 20 else 'ohlcv (calentando)'}")
                    snaps = []
                    lines = []
                    for s, book in books.items():
                        lvls = compute_levels(m, system=s, high_vol_only=HVO, fp_bars=fp_bars)
                        book.refresh(lvls, bar_ts)
                        _flush(book)
                        snaps.append(book.snapshot())
                        lines.append(f"[{s.upper()}] {book.line()}")
                    print(f"[{datetime.now(timezone.utc):%m-%d %H:%M}] M{TF} @ {row['close']:.1f}")
                    for ln in lines: print(f"  {ln}")
                    supa_insert("liquidity_paper_snapshots", snaps)

    def on_open(ws):
        ws.send(json.dumps({"op": "subscribe",
                            "args": [f"publicTrade.{SYMBOL}", f"kline.{TF}.{SYMBOL}"]}))
        print(f"WS suscrito publicTrade + kline.{TF}\n")

    while True:
        try:
            websocket.WebSocketApp(WS_PUBLIC, on_open=on_open,
                                   on_message=on_msg).run_forever(
                ping_interval=20, ping_timeout=10)
        except Exception as e:
            print("WS error, reconectando en 5s:", e); time.sleep(5)


if __name__ == "__main__":
    main()
