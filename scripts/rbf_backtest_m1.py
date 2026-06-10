"""
RBF Backtest M1 — replica el detector sobre barras historicas.

Reglas activas (version 2026-06-10):
  - Consolidacion 15/20/30/45/60 barras, range 0.08-0.55%
  - Range >= 1.5x ATR (filtro potencial)
  - VR >= 3.0x en barra de breakout
  - CVD acumulado alineado con direccion
  - Stop dinamico = 1.0x ATR  (era 0.7x — grid search M1 mostro 1.0 optimo)
  - Target = 2.0x stop SHORT (RR 2:1) / 1.8x stop LONG (RR 1.8:1)
  - Trailing stop desde 1.5R (trail = 1.2x ATR desde extremo — era 0.5x)
  - Time stop: bar 15 si en perdida
  - Cooldown: 60 barras entre senales del mismo simbolo
  - Sesiones: London, LondonNyOverlap, NewYork (Asia/OffHours excluidas)

Datos: descarga directa desde Supabase (btc_bars, eth_bars, bnb_bars, sol_bars, xrp_bars).
"""

import json, datetime, urllib.request, urllib.parse
from collections import defaultdict, deque

# ── config RBF ────────────────────────────────────────────────────────────────
RANGE_WINDOWS    = [15, 20, 30, 45, 60]
RANGE_MIN_PCT    = 0.08
RANGE_MAX_PCT    = 0.55
VR_MIN           = 3.0
MIN_RANGE_ATR    = 1.5
ATR_STOP_K       = 1.0       # <-- actualizado de 0.7
TARGET_RR_SHORT  = 2.0
TARGET_RR_LONG   = 1.8
TRAIL_ACTIVATE_R = 1.5
TRAIL_ATR_K      = 1.2       # <-- actualizado de 0.5
TIME_STOP_BARS   = 15
COOLDOWN_BARS    = 60
MAX_TRADE_BARS   = 120
SESSIONS_OK      = {"London", "LondonNyOverlap", "NewYork"}

# ── Supabase ──────────────────────────────────────────────────────────────────
SUPABASE_URL = "https://ztdhvmcisjjyhbqlgkzm.supabase.co"
SUPABASE_KEY = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp0ZGh2bWNpc2pqeWhicWxna3ptIiwicm9sZSI6"
    "InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODk0MTc1MiwiZXhwIjoyMDk0NTE3NzUyfQ"
    ".sqMh9Jcxrxyg-ZBYWPaNN8DB9kf-KkC7ARPLucItN1Y"
)
TABLES = {
    "BTCUSDT": "btc_bars",
    "ETHUSDT": "eth_bars",
    "BNBUSDT": "bnb_bars",
    "SOLUSDT": "sol_bars",
    "XRPUSDT": "xrp_bars",
}
COLS = "ts_ms,open,high,low,close,volume,bar_delta,cvd_slope,obi_l5,dz,vr,stacked_imb,absorption,thin_above,thin_below,vpin,oi_momentum,vwap,atr,session,regime,symbol"


def fetch_bars(table, sym):
    bars = []
    offset = 0
    page = 1000
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Range-Unit": "items",
    }
    while True:
        url = (f"{SUPABASE_URL}/rest/v1/{table}"
               f"?select={COLS}&order=ts_ms.asc"
               f"&offset={offset}&limit={page}")
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req) as r:
            batch = json.loads(r.read())
        if not batch:
            break
        for b in batch:
            b["symbol"] = sym
        bars.extend(batch)
        print(f"  {sym} [{table}]: {len(bars)} barras...", end="\r")
        if len(batch) < page:
            break
        offset += page
    print(f"  {sym}: {len(bars)} barras totales          ")
    return bars


# ── helpers ───────────────────────────────────────────────────────────────────
def ts(ms):
    return datetime.datetime.utcfromtimestamp(ms / 1000).strftime("%m-%d %H:%M")

def sl(s):
    return {"London": "LDN", "LondonNyOverlap": "OVR", "NewYork": "NY",
            "Asia": "ASI", "OffHours": "OFF"}.get(s, s[:3])

def stats(trades):
    if not trades:
        return (0, 0.0, 0.0, 0.0)
    wins  = sum(1 for t in trades if t["result_r"] > 0)
    total = sum(t["result_r"] for t in trades)
    return (len(trades), wins / len(trades) * 100, total / len(trades), total)


# ── backtest engine ───────────────────────────────────────────────────────────
def backtest_symbol(sym, bars):
    trades   = []
    n        = len(bars)
    cooldown = 0
    in_trade = None

    for i in range(60, n):
        bar = bars[i]
        atr = bar.get("atr") or 0.0
        session = bar.get("session", "OffHours")

        # gestionar trade activo
        if in_trade is not None:
            t = in_trade
            h, l, c = bar["high"], bar["low"], bar["close"]
            t["bars_held"] += 1
            cur_atr = atr if atr > 0 else t["atr"]

            if t["dir"] == "Short":
                if l < t["best_extreme"]:
                    t["best_extreme"] = l
                fav_r = (t["entry"] - t["best_extreme"]) / t["risk"]
                if fav_r >= TRAIL_ACTIVATE_R and not t["trailing"]:
                    t["trailing"] = True
                if t["trailing"] and cur_atr > 0:
                    ns = t["best_extreme"] + TRAIL_ATR_K * cur_atr
                    if ns < t["stop"]:
                        t["stop"] = ns
                stop_hit   = h >= t["stop"]
                target_hit = l <= t["target"]
            else:
                if h > t["best_extreme"]:
                    t["best_extreme"] = h
                fav_r = (t["best_extreme"] - t["entry"]) / t["risk"]
                if fav_r >= TRAIL_ACTIVATE_R and not t["trailing"]:
                    t["trailing"] = True
                if t["trailing"] and cur_atr > 0:
                    ns = t["best_extreme"] - TRAIL_ATR_K * cur_atr
                    if ns > t["stop"]:
                        t["stop"] = ns
                stop_hit   = l <= t["stop"]
                target_hit = h >= t["target"]

            reason = exit_price = None
            if stop_hit:
                reason = "TRAIL_STOP" if t["trailing"] else "STOP"
                exit_price = t["stop"]
            elif target_hit:
                reason = "TARGET"
                exit_price = t["target"]
            elif t["bars_held"] >= TIME_STOP_BARS:
                pnl = (t["entry"] - c) if t["dir"] == "Short" else (c - t["entry"])
                if pnl < 0:
                    reason = "TIME_STOP"
                    exit_price = c
            elif t["bars_held"] >= MAX_TRADE_BARS:
                reason = "TIMEOUT"
                exit_price = c

            if reason:
                pnl = (t["entry"] - exit_price) if t["dir"] == "Short" else (exit_price - t["entry"])
                t.update({
                    "exit_ms":      bar["ts_ms"],
                    "exit_price":   exit_price,
                    "result_r":     round(pnl / t["risk"], 4),
                    "reason":       reason,
                    "exit_session": session,
                })
                trades.append(t)
                in_trade = None
                cooldown = COOLDOWN_BARS
            continue

        if cooldown > 0:
            cooldown -= 1
            continue

        # solo sesiones operativas
        if session not in SESSIONS_OK:
            continue

        close = bar["close"]
        vr    = bar.get("vr") or 0.0
        if vr < VR_MIN or atr <= 0:
            continue

        for rw in RANGE_WINDOWS:
            if i < rw + 1:
                continue
            window = bars[i - rw: i]

            hi  = max(b["high"] for b in window)
            lo  = min(b["low"]  for b in window)
            rng = hi - lo
            range_pct = rng / close * 100.0

            if range_pct < RANGE_MIN_PCT or range_pct > RANGE_MAX_PCT:
                continue
            if rng < MIN_RANGE_ATR * atr:
                continue

            breaks_down = close < lo
            breaks_up   = close > hi
            if not breaks_down and not breaks_up:
                continue

            direction = "Short" if breaks_down else "Long"
            cvd = sum(b.get("bar_delta") or 0 for b in window)
            if direction == "Short" and cvd >= 0:
                continue
            if direction == "Long"  and cvd <= 0:
                continue

            stop_dist = ATR_STOP_K * atr
            rr_mult   = TARGET_RR_SHORT if direction == "Short" else TARGET_RR_LONG
            if direction == "Short":
                stop   = close + stop_dist
                target = close - stop_dist * rr_mult
            else:
                stop   = close - stop_dist
                target = close + stop_dist * rr_mult

            in_trade = {
                "sym":          sym,
                "entry_ms":     bar["ts_ms"],
                "entry":        close,
                "stop":         stop,
                "target":       target,
                "risk":         stop_dist,
                "atr":          atr,
                "dir":          direction,
                "session":      session,
                "range_bars":   rw,
                "range_pct":    round(range_pct, 4),
                "vr":           round(vr, 2),
                "dz":           round(bar.get("dz") or 0, 3),
                "cvd":          round(cvd, 1),
                "bars_held":    0,
                "trailing":     False,
                "best_extreme": close,
                "exit_ms":      None,
                "exit_price":   None,
                "result_r":     None,
                "reason":       None,
                "exit_session": None,
            }
            break

    return trades


# ── main ──────────────────────────────────────────────────────────────────────
print("Descargando barras desde Supabase...")
all_bars = {}
for sym, table in TABLES.items():
    try:
        all_bars[sym] = fetch_bars(table, sym)
    except Exception as e:
        print(f"  ERROR {sym}: {e}")

print("\nCorriendo backtest...\n")
all_trades = []
for sym, bars in all_bars.items():
    trades = backtest_symbol(sym, bars)
    all_trades.extend(trades)
    print(f"  {sym}: {len(bars)} barras -> {len(trades)} trades simulados")

all_trades.sort(key=lambda t: t["entry_ms"])
closed  = [t for t in all_trades if t["result_r"] is not None]
open_tr = [t for t in all_trades if t["result_r"] is None]

print(f"\nTotal senales: {len(all_trades)} | Cerradas: {len(closed)} | Abiertas: {len(open_tr)}")

if not closed:
    print("Sin trades cerrados.")
    raise SystemExit

# ── estadisticas ──────────────────────────────────────────────────────────────
print("\n" + "=" * 90)
print("BACKTEST RBF v2026-06-10  (ATR_STOP=1.0x  TRAIL=1.2x  VR>=3x  sesiones LDN/OVR/NY)")
print("=" * 90)

n, wr, avg_r, tot_r = stats(closed)
days = (max(t["entry_ms"] for t in closed) - min(t["entry_ms"] for t in closed)) / 86400000
days = max(days, 1)
print(f"\nGLOBAL: n={n}  WR={wr:.1f}%  AvgR={avg_r:+.3f}  TotalR={tot_r:+.2f}  "
      f"periodo={days:.1f} dias  ({n/days:.1f} senales/dia)")

print("\n--- Por simbolo ---")
by_sym = defaultdict(list)
for t in closed: by_sym[t["sym"]].append(t)
for sym, lst in sorted(by_sym.items()):
    nn, ww, aa, tt = stats(lst)
    print(f"  {sym:12s}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}  TotalR={tt:+.2f}")

print("\n--- Por sesion ---")
by_sess = defaultdict(list)
for t in closed: by_sess[t["session"]].append(t)
for sess, lst in sorted(by_sess.items(), key=lambda x: -sum(t["result_r"] for t in x[1])):
    nn, ww, aa, tt = stats(lst)
    print(f"  {sess:22s}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}  TotalR={tt:+.2f}")

print("\n--- Por direccion ---")
by_dir = defaultdict(list)
for t in closed: by_dir[t["dir"]].append(t)
for d, lst in sorted(by_dir.items()):
    nn, ww, aa, tt = stats(lst)
    print(f"  {d:8s}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}  TotalR={tt:+.2f}")

print("\n--- Sesion x Direccion ---")
for sess in ["London", "LondonNyOverlap", "NewYork"]:
    for d in ["Short", "Long"]:
        lst = [t for t in closed if t["session"] == sess and t["dir"] == d]
        if len(lst) < 2: continue
        nn, ww, aa, tt = stats(lst)
        print(f"  {sl(sess):<3} {d:<5}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}  TotalR={tt:+.2f}")

print("\n--- Por razon de salida ---")
by_reason = defaultdict(list)
for t in closed: by_reason[t["reason"]].append(t)
for r, lst in sorted(by_reason.items(), key=lambda x: -len(x[1])):
    nn, ww, aa, tt = stats(lst)
    print(f"  {r:15s}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}  TotalR={tt:+.2f}")

print("\n--- Por VR ---")
for lo, hi, label in [(3, 4, "3-4x"), (4, 5, "4-5x"), (5, 99, "5x+")]:
    lst = [t for t in closed if lo <= t["vr"] < hi]
    if lst:
        nn, ww, aa, tt = stats(lst)
        print(f"  VR {label}: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}  TotalR={tt:+.2f}")

print("\n--- Por ventana de consolidacion ---")
for rw in RANGE_WINDOWS:
    lst = [t for t in closed if t["range_bars"] == rw]
    if lst:
        nn, ww, aa, tt = stats(lst)
        print(f"  {rw:3d} barras: n={nn:3d}  WR={ww:4.0f}%  AvgR={aa:+.3f}  TotalR={tt:+.2f}")

# ── tabla de trades ───────────────────────────────────────────────────────────
print(f"\n{'=' * 105}")
print("TRADES COMPLETOS")
print(f"{'=' * 105}")
print(f"{'#':>3} {'Entrada':>11} {'Sym':>8} {'D':>1} {'Ses':>3} "
      f"{'VR':>4} {'Rng%':>5} {'ATR':>7} {'Risk':>7} "
      f"{'Bars':>5} {'Reason':>12} {'R':>7}")
print("-" * 105)
for i, t in enumerate(closed):
    print(f"{i+1:>3} {ts(t['entry_ms']):>11} {t['sym']:>8} {t['dir'][0]:>1} "
          f"{sl(t['session']):>3} {t['vr']:>4.1f} {t['range_pct']:>5.3f} "
          f"{t['atr']:>7.2f} {t['risk']:>7.3f} "
          f"{t['bars_held']:>5} {t['reason']:>12} {t['result_r']:>+7.3f}")

# ── proyeccion ────────────────────────────────────────────────────────────────
print(f"\n{'=' * 90}")
print(f"PROYECCION MENSUAL ({days:.1f} dias de datos, {n/days:.1f} senales/dia)")
print(f"{'=' * 90}")
print("  ADVERTENCIA: periodo muy corto — proyeccion orientativa, no estadistica.")
for cap, risk_pct in [(50, 0.02), (500, 0.02), (1000, 0.01)]:
    risk_usd    = cap * risk_pct
    monthly_r   = (n / days) * 22 * avg_r
    monthly_pnl = monthly_r * risk_usd
    print(f"  ${cap:>5} / {risk_pct*100:.0f}% riesgo (${risk_usd:.1f}/trade): "
          f"{monthly_r:+.1f}R = ${monthly_pnl:+.0f}/mes")

with open("/tmp/rbf_backtest_results.json", "w") as f:
    json.dump(closed, f, indent=2, default=str)
print(f"\nGuardado: /tmp/rbf_backtest_results.json")
