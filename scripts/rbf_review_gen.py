#!/usr/bin/env python3
"""
Genera rbf_review.html con dos pestanas:
  - Live Trades: trades reales desde Supabase
  - Backtest:    replay del detector RBF sobre barras historicas de Supabase

Uso:
    python scripts/rbf_review_gen.py
"""

import json, os, sys, time, urllib.request, urllib.parse, webbrowser, datetime, csv
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent.parent
env = {}
env_file = ROOT / '.env'
if env_file.exists():
    for line in env_file.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip('"').strip("'")

SUPABASE_URL = env.get('SUPABASE_URL', os.environ.get('SUPABASE_URL', ''))
SUPABASE_KEY = env.get('SUPABASE_KEY', os.environ.get('SUPABASE_KEY', ''))

if not SUPABASE_URL or not SUPABASE_KEY:
    print("ERROR: SUPABASE_URL / SUPABASE_KEY no encontrados en .env")
    sys.exit(1)

ACCOUNT  = 50.0
LEVERAGE = 15
POSITION = ACCOUNT * LEVERAGE   # $750

# ── Supabase helpers ──────────────────────────────────────────────────────────
def sb_get(table, params):
    rows, limit, offset = [], 1000, 0
    while True:
        p = {**params, 'limit': str(limit), 'offset': str(offset)}
        qs = urllib.parse.urlencode(p)
        req = urllib.request.Request(
            f"{SUPABASE_URL}/rest/v1/{table}?{qs}",
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
        )
        with urllib.request.urlopen(req) as r:
            chunk = json.loads(r.read())
        rows.extend(chunk)
        if len(chunk) < limit: break
        offset += limit
    return rows

def sb_get_key(table, cols):
    rows, limit, offset = [], 1000, 0
    while True:
        url = (f"{SUPABASE_URL}/rest/v1/{table}"
               f"?select={cols}&order=ts_ms.asc&limit={limit}&offset={offset}")
        req = urllib.request.Request(url,
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'})
        with urllib.request.urlopen(req) as r:
            chunk = json.loads(r.read())
        rows.extend(chunk)
        if len(chunk) < limit: break
        offset += limit
    return rows

def binance_klines(symbol, start_ms, limit=300):
    from_ms = start_ms - 100 * 60 * 1000
    url = (f"https://fapi.binance.com/fapi/v1/klines"
           f"?symbol={symbol}&interval=1m&startTime={from_ms}&limit={limit}")
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            raw = json.loads(r.read())
        return [{'time': k[0]//1000, 'open': float(k[1]), 'high': float(k[2]),
                 'low': float(k[3]), 'close': float(k[4])} for k in raw]
    except Exception as e:
        print(f"  WARN klines: {e}")
        return []

def _calc_duration(entry_ms, closed_at_str):
    if not closed_at_str or not entry_ms: return None
    try:
        closed_dt = datetime.datetime.fromisoformat(closed_at_str.replace('Z', '+00:00'))
        entry_dt  = datetime.datetime.fromtimestamp(entry_ms / 1000, tz=datetime.timezone.utc)
        mins = int((closed_dt - entry_dt).total_seconds()) // 60
        return mins if mins >= 0 else None
    except Exception:
        return None

# ══════════════════════════════════════════════════════════════════════════════
# LIVE TRADES
# ══════════════════════════════════════════════════════════════════════════════
print("Fetching live trades de Supabase...")
raw = sb_get('rbf_signals', {'select': '*', 'result_r': 'not.is.null', 'order': 'timestamp_ms.asc'})
print(f"  {len(raw)} trades live")

equity = ACCOUNT
live_trades = []
for i, t in enumerate(raw):
    r_val    = t.get('result_r') or 0
    entry    = t.get('entry_price') or 1
    stop     = t.get('stop_price')  or entry
    stop_pct = abs(entry - stop) / entry
    risk_usd = round(POSITION * stop_pct, 4)
    pnl_usd  = round(r_val * risk_usd, 2)
    equity   = round(equity + pnl_usd, 2)
    live_trades.append({
        'idx':              i + 1,
        'sym':              t.get('symbol', 'BTCUSDT'),
        'dir':              t.get('direction', 'Short'),
        'session':          t.get('session', ''),
        'score':            t.get('confluence_score'),
        'entry':            t.get('entry_price', 0),
        'stop':             t.get('stop_price', 0),
        'target':           t.get('target_price', 0),
        'exit':             t.get('exit_price', 0),
        'result_r':         r_val,
        'pnl_usd':          pnl_usd,
        'risk_usd':         risk_usd,
        'stop_pct':         round(stop_pct * 100, 3),
        'equity':           equity,
        'reason':           t.get('exit_reason') or t.get('status') or '?',
        'ts':               (t.get('timestamp_ms', 0)) // 1000,
        'ts_ms':            t.get('timestamp_ms', 0),
        'closed_at':        t.get('closed_at'),
        'regime':           t.get('macro_regime', ''),
        'session_phase':    t.get('session_phase', ''),
        'evidence':         t.get('evidence') or [],
        'confluence_flags': t.get('confluence_flags') or [],
        'veto_reason':      t.get('veto_reason') or '',
        'cvd_in_range':     t.get('cvd_in_range'),
        'vr_at_breakout':   t.get('vr_at_breakout'),
        'price_vs_vwap_pct':t.get('price_vs_vwap_pct'),
        'funding':          t.get('funding_at_entry'),
        'cvd_slope':        t.get('cvd_slope_at_entry'),
        'obi':              t.get('obi_at_entry'),
        'dz':               t.get('dz_at_entry'),
        'range_pct':        t.get('range_pct'),
        'range_bars':       t.get('range_bars'),
        'range_touch':      t.get('range_touch_count'),
        'duration_min':     _calc_duration(t.get('timestamp_ms', 0), t.get('closed_at')),
        'is_backtest':      False,
    })

# ══════════════════════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ══════════════════════════════════════════════════════════════════════════════
RANGE_WINDOWS    = [15, 20, 30, 45, 60]
RANGE_MIN_PCT    = 0.08
RANGE_MAX_PCT    = 0.55
VR_MIN           = 3.0
MIN_RANGE_ATR    = 1.5
ATR_STOP_K       = 1.0
TARGET_RR_SHORT  = 2.0
TARGET_RR_LONG   = 1.8
TRAIL_ACTIVATE_R = 1.5
TRAIL_ATR_K      = 1.2
TIME_STOP_BARS   = 15
COOLDOWN_BARS    = 60
MAX_TRADE_BARS   = 120
SESSIONS_OK      = {"London", "LondonNyOverlap", "NewYork"}
LONGS_ENABLED    = False

TABLES = {
    "BTCUSDT": "btc_bars",
    "ETHUSDT": "eth_bars",
    "BNBUSDT": "bnb_bars",
    "SOLUSDT": "sol_bars",
    "XRPUSDT": "xrp_bars",
}
BAR_COLS = "ts_ms,open,high,low,close,volume,bar_delta,vr,atr,session,dz,cvd_slope,obi_l5"

def backtest_symbol(sym, bars):
    trades = []
    n = len(bars)
    cooldown = 0
    in_trade = None
    for i in range(60, n):
        bar = bars[i]
        atr = bar.get("atr") or 0.0
        session = bar.get("session", "OffHours")
        if in_trade is not None:
            t = in_trade
            h, l, c = bar["high"], bar["low"], bar["close"]
            t["bars_held"] += 1
            cur_atr = atr if atr > 0 else t["atr"]
            if t["dir"] == "Short":
                if l < t["best_extreme"]: t["best_extreme"] = l
                fav_r = (t["entry"] - t["best_extreme"]) / t["risk"]
                if fav_r >= TRAIL_ACTIVATE_R and not t["trailing"]: t["trailing"] = True
                if t["trailing"] and cur_atr > 0:
                    ns = t["best_extreme"] + TRAIL_ATR_K * cur_atr
                    if ns < t["stop"]: t["stop"] = ns
                stop_hit   = h >= t["stop"]
                target_hit = l <= t["target"]
            else:
                if h > t["best_extreme"]: t["best_extreme"] = h
                fav_r = (t["best_extreme"] - t["entry"]) / t["risk"]
                if fav_r >= TRAIL_ACTIVATE_R and not t["trailing"]: t["trailing"] = True
                if t["trailing"] and cur_atr > 0:
                    ns = t["best_extreme"] - TRAIL_ATR_K * cur_atr
                    if ns > t["stop"]: t["stop"] = ns
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
                if pnl < 0: reason = "TIME_STOP"; exit_price = c
            elif t["bars_held"] >= MAX_TRADE_BARS:
                reason = "TIMEOUT"; exit_price = c
            if reason:
                pnl = (t["entry"] - exit_price) if t["dir"] == "Short" else (exit_price - t["entry"])
                t.update({
                    "exit_ms":    bar["ts_ms"],
                    "exit_price": exit_price,
                    "result_r":   round(pnl / t["risk"], 4),
                    "reason":     reason,
                    "exit_session": session,
                    "duration_min": t["bars_held"],
                })
                trades.append(t)
                in_trade = None
                cooldown = COOLDOWN_BARS
            continue
        if cooldown > 0: cooldown -= 1; continue
        if session not in SESSIONS_OK: continue
        close = bar["close"]
        vr    = bar.get("vr") or 0.0
        if vr < VR_MIN or atr <= 0: continue
        for rw in RANGE_WINDOWS:
            if i < rw + 1: continue
            window = bars[i - rw: i]
            hi  = max(b["high"] for b in window)
            lo  = min(b["low"]  for b in window)
            rng = hi - lo
            range_pct = rng / close * 100.0
            if range_pct < RANGE_MIN_PCT or range_pct > RANGE_MAX_PCT: continue
            if rng < MIN_RANGE_ATR * atr: continue
            breaks_down = close < lo
            breaks_up   = close > hi
            if not breaks_down and not breaks_up: continue
            direction = "Short" if breaks_down else "Long"
            if direction == "Long" and not LONGS_ENABLED: continue
            cvd = sum(b.get("bar_delta") or 0 for b in window)
            if direction == "Short" and cvd >= 0: continue
            if direction == "Long"  and cvd <= 0: continue
            stop_dist = ATR_STOP_K * atr
            rr_mult   = TARGET_RR_SHORT if direction == "Short" else TARGET_RR_LONG
            if direction == "Short":
                stop   = close + stop_dist
                target = close - stop_dist * rr_mult
            else:
                stop   = close - stop_dist
                target = close + stop_dist * rr_mult
            in_trade = {
                "sym": sym, "dir": direction, "entry_ms": bar["ts_ms"],
                "entry": close, "stop": stop, "target": target,
                "risk": stop_dist, "atr": atr, "session": session,
                "range_bars": rw, "range_pct": round(range_pct, 4),
                "vr": round(vr, 2), "dz": round(bar.get("dz") or 0, 3),
                "cvd": round(cvd, 1),
                "cvd_slope": bar.get("cvd_slope"),
                "obi": bar.get("obi_l5"),
                "bars_held": 0, "trailing": False, "best_extreme": close,
                "exit_ms": None, "exit_price": None,
                "result_r": None, "reason": None, "exit_session": None,
                "duration_min": None,
            }
            break
    return trades

print("Fetching barras historicas para backtest...")
all_bars = {}
for sym, table in TABLES.items():
    bars = sb_get_key(table, BAR_COLS)
    for b in bars:
        b["symbol"] = sym
    all_bars[sym] = bars
    print(f"  {sym}: {len(bars)} barras")

print("Corriendo backtest...")
bt_raw = []
for sym, bars in all_bars.items():
    t_list = backtest_symbol(sym, bars)
    bt_raw.extend(t_list)
    print(f"  {sym}: {len(t_list)} trades simulados")

bt_raw.sort(key=lambda t: t["entry_ms"])
bt_raw = [t for t in bt_raw if t["result_r"] is not None]
print(f"  Total backtest: {len(bt_raw)} trades cerrados")

bt_equity = ACCOUNT
bt_trades = []
for i, t in enumerate(bt_raw):
    stop_pct = t["risk"] / t["entry"]
    risk_usd = round(POSITION * stop_pct, 4)
    pnl_usd  = round(t["result_r"] * risk_usd, 2)
    bt_equity = round(bt_equity + pnl_usd, 2)
    closed_at_iso = None
    if t.get("exit_ms"):
        closed_at_iso = datetime.datetime.fromtimestamp(
            t["exit_ms"]/1000, tz=datetime.timezone.utc).isoformat()
    bt_trades.append({
        'idx':              i + 1,
        'sym':              t["sym"],
        'dir':              t["dir"],
        'session':          t["session"],
        'score':            None,
        'entry':            t["entry"],
        'stop':             t["stop"],
        'target':           t["target"],
        'exit':             t.get("exit_price") or 0,
        'result_r':         t["result_r"],
        'pnl_usd':          pnl_usd,
        'risk_usd':         risk_usd,
        'stop_pct':         round(stop_pct * 100, 3),
        'equity':           bt_equity,
        'reason':           t.get("reason") or "?",
        'ts':               t["entry_ms"] // 1000,
        'ts_ms':            t["entry_ms"],
        'closed_at':        closed_at_iso,
        'regime':           '',
        'session_phase':    '',
        'evidence':         [],
        'confluence_flags': [],
        'veto_reason':      '',
        'cvd_in_range':     t.get("cvd"),
        'vr_at_breakout':   t.get("vr"),
        'price_vs_vwap_pct':None,
        'funding':          None,
        'cvd_slope':        t.get("cvd_slope"),
        'obi':              t.get("obi"),
        'dz':               t.get("dz"),
        'range_pct':        t.get("range_pct"),
        'range_bars':       t.get("range_bars"),
        'range_touch':      None,
        'duration_min':     t.get("duration_min"),
        'is_backtest':      True,
    })

# ── Fetch klines para ambos sets ──────────────────────────────────────────────
print("Fetching klines de Binance (live)...")
live_klines = {}
for t in live_trades:
    print(f"  live #{t['idx']:02d} {t['sym']}...", end=' ', flush=True)
    k = binance_klines(t['sym'], t['ts_ms'])
    live_klines[t['idx']] = k
    print(f"{len(k)} barras")
    time.sleep(0.05)

print("Fetching klines de Binance (backtest)...")
bt_klines = {}
seen = set()
for t in bt_trades:
    key = (t['sym'], t['ts_ms'])
    if key in seen: bt_klines[t['idx']] = []; continue
    seen.add(key)
    print(f"  bt #{t['idx']:02d} {t['sym']}...", end=' ', flush=True)
    k = binance_klines(t['sym'], t['ts_ms'])
    bt_klines[t['idx']] = k
    print(f"{len(k)} barras")
    time.sleep(0.05)

print("Generando HTML...")

LIVE_JSON    = json.dumps(live_trades, ensure_ascii=False)
BT_JSON      = json.dumps(bt_trades,   ensure_ascii=False)
LIVE_K_JSON  = json.dumps(live_klines, ensure_ascii=False)
BT_K_JSON    = json.dumps(bt_klines,   ensure_ascii=False)
ACCOUNT_JSON = json.dumps(ACCOUNT,     ensure_ascii=False)
POS_JSON     = json.dumps(POSITION,    ensure_ascii=False)

# ── Stats PnL data (from rbf_pnl_450.csv) ─────────────────────────────────────
stats_trades = []
pnl_csv = ROOT / 'scripts' / 'rbf_pnl_450.csv'
if pnl_csv.exists():
    with open(pnl_csv, newline='', encoding='utf-8') as _f:
        for _row in csv.DictReader(_f):
            stats_trades.append({
                'num':   int(_row['num']),
                'fecha': _row['fecha'],
                'sym':   _row['symbol'],
                'dir':   _row['direction'],
                'ses':   _row['session'],
                'r':     float(_row['result_r']),
                'pnl':   float(_row['pnl_usd']),
                'bal':   float(_row['balance']),
                'reason': _row.get('reason', ''),
            })
else:
    print("WARN: rbf_pnl_450.csv no encontrado — Stats tab vacio")
STATS_JSON = json.dumps(stats_trades, ensure_ascii=False)
STATS_CAP  = json.dumps(450.0)

HTML = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>RBF Trade Review</title>
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.2/dist/chart.umd.min.js"></script>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0d1117;color:#e6edf3;font-family:'SF Mono','Fira Code',monospace;font-size:12px;height:100vh;display:flex;flex-direction:column;overflow:hidden}}

#hdr{{background:#161b22;border-bottom:1px solid #21262d;padding:7px 14px;display:flex;align-items:center;gap:12px;flex-shrink:0;flex-wrap:wrap}}
#hdr h1{{font-size:13px;font-weight:700;color:#e6edf3;letter-spacing:.5px}}
.hsep{{color:#30363d}}
.hstat{{color:#8b949e;font-size:11px}} .hstat b{{color:#e6edf3}}
.filters{{display:flex;gap:6px;margin-left:auto}}
select{{background:#0d1117;color:#8b949e;border:1px solid #30363d;border-radius:5px;padding:3px 8px;font-size:11px;font-family:inherit;cursor:pointer;outline:none}}
select:hover{{border-color:#388bfd;color:#e6edf3}}

/* tabs */
.tab-btns{{display:flex;gap:4px}}
.tab-btn{{padding:3px 12px;border-radius:5px;border:1px solid #30363d;background:transparent;color:#8b949e;font-family:inherit;font-size:11px;cursor:pointer;transition:all .15s}}
.tab-btn:hover{{border-color:#388bfd;color:#e6edf3}}
.tab-btn.active{{background:#388bfd;border-color:#388bfd;color:#fff;font-weight:700}}
.tab-btn.bt-active{{background:#d29922;border-color:#d29922;color:#0d1117;font-weight:700}}

#wrap{{display:flex;flex:1;overflow:hidden}}

#sidebar{{width:290px;flex-shrink:0;background:#0d1117;border-right:1px solid #21262d;display:flex;flex-direction:column}}
#sb-hdr{{padding:7px 10px;border-bottom:1px solid #21262d;color:#8b949e;font-size:10px;display:flex;gap:8px;align-items:center}}
#sb-hdr b{{color:#e6edf3}}
#sb-equity{{margin-left:auto;font-size:11px;font-weight:700}}
#trade-list{{flex:1;overflow-y:auto;padding:4px 0}}
#trade-list::-webkit-scrollbar{{width:4px}}
#trade-list::-webkit-scrollbar-thumb{{background:#30363d;border-radius:2px}}

.trade-item{{padding:7px 10px;cursor:pointer;border-left:3px solid transparent;border-bottom:1px solid rgba(33,38,45,0.4);transition:background .1s}}
.trade-item:hover{{background:#161b22}}
.trade-item.selected{{background:#1c2128}}
.win{{border-left-color:#3fb950}} .loss{{border-left-color:#f85149}} .bug{{border-left-color:#d29922}}

.t-row1{{display:flex;align-items:center;gap:5px}}
.t-num{{color:#484f58;font-size:10px;width:22px;flex-shrink:0}}
.t-badge{{font-size:10px;font-weight:700;padding:1px 5px;border-radius:3px;flex-shrink:0}}
.badge-short{{background:rgba(248,81,73,0.2);color:#f85149}}
.badge-long{{background:rgba(63,185,80,0.2);color:#3fb950}}
.t-sym{{font-size:11px;font-weight:700;color:#e6edf3}}
.t-ses{{color:#8b949e;font-size:10px;flex:1}}
.t-r{{font-size:11px;font-weight:700}}
.t-r.pos{{color:#3fb950}} .t-r.neg{{color:#f85149}} .t-r.bug{{color:#d29922}}
.t-row2{{display:flex;gap:6px;color:#484f58;font-size:10px;padding-left:28px;margin-top:2px}}
.t-usd{{font-weight:700}}
.t-eq{{color:#8b949e}}
.bt-badge{{font-size:9px;background:rgba(210,153,34,0.15);color:#d29922;border:1px solid rgba(210,153,34,0.3);border-radius:3px;padding:0 4px}}

#main{{flex:1;display:flex;flex-direction:column;overflow:hidden}}
#chart-wrap{{flex:1;position:relative}}
#chart{{width:100%;height:100%}}
#overlay{{position:absolute;top:0;left:0;pointer-events:none;z-index:5}}
#empty{{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#484f58;font-size:13px}}

#info-bar{{background:#161b22;border-top:1px solid #21262d;padding:5px 14px;display:flex;gap:16px;align-items:center;flex-shrink:0;flex-wrap:wrap;min-height:30px}}
.lbl{{color:#484f58;font-size:10px}} .val{{color:#e6edf3;font-size:11px}}
.c-entry{{color:#e6edf3}} .c-stop{{color:#f85149}} .c-target{{color:#3fb950}} .c-rr{{color:#8b949e}}
#ib-hint{{color:#484f58;font-size:11px}}

#setup-panel{{
  position:absolute;top:10px;left:10px;width:215px;
  background:rgba(10,13,20,0.93);border:1px solid #2a3040;border-radius:8px;
  z-index:10;overflow:hidden;display:none;backdrop-filter:blur(8px);
  box-shadow:0 8px 32px rgba(0,0,0,0.6);
}}
#sp-header{{padding:7px 10px 7px 12px;background:linear-gradient(135deg,rgba(56,139,253,0.12),rgba(63,185,80,0.06));border-bottom:1px solid #2a3040;display:flex;align-items:center;gap:6px;cursor:pointer;user-select:none;}}
#sp-header:hover{{background:linear-gradient(135deg,rgba(56,139,253,0.18),rgba(63,185,80,0.1))}}
#sp-icon{{font-size:11px;color:#388bfd}}
#sp-label{{font-size:10px;font-weight:700;color:#c9d1d9;letter-spacing:.7px;text-transform:uppercase;flex:1}}
#sp-score{{font-size:10px;font-weight:700;padding:1px 6px;border-radius:4px;background:rgba(56,139,253,0.15);color:#388bfd;border:1px solid rgba(56,139,253,0.3);}}
#sp-toggle{{font-size:10px;color:#484f58;transition:transform .2s;line-height:1;}}
#sp-body{{overflow:hidden;transition:max-height .25s ease}}
#sp-rows{{padding:5px 0 2px}}
.sp-row{{display:grid;grid-template-columns:1fr auto auto;align-items:center;padding:3px 10px 3px 12px;gap:6px;border-left:2px solid transparent;transition:background .1s;}}
.sp-row:hover{{background:rgba(33,38,45,0.5)}}
.sp-row.ok{{border-left-color:rgba(63,185,80,0.6);background:rgba(63,185,80,0.04)}}
.sp-row.bad{{border-left-color:rgba(248,81,73,0.5);background:rgba(248,81,73,0.04)}}
.sp-name{{color:#8b949e;font-size:10px;white-space:nowrap}}
.sp-row.ok .sp-name{{color:#b5d9b8}} .sp-row.bad .sp-name{{color:#e09090}}
.sp-val{{color:#6e7681;font-size:9px;text-align:right;white-space:nowrap;overflow:hidden;max-width:70px;}}
.sp-row.ok .sp-val{{color:#3fb950}} .sp-row.bad .sp-val{{color:#f85149}}
.sp-icon-cell{{font-size:11px;width:14px;text-align:center;flex-shrink:0}}
.ico-ok{{color:#3fb950}} .ico-bad{{color:#f85149}} .ico-neu{{color:#30363d}}
#sp-sep{{height:1px;background:linear-gradient(90deg,transparent,#2a3040,transparent);margin:4px 8px}}
.sp-ev-wrap{{padding:2px 12px 4px}}
.sp-ev{{font-size:9px;color:#484f58;padding:1px 0;display:flex;gap:4px;align-items:baseline}}
.sp-ev::before{{content:'>';color:#388bfd;font-size:10px;flex-shrink:0}}
#sp-interp{{margin:0 8px 8px;padding:6px 8px;background:rgba(56,139,253,0.07);border:1px solid rgba(56,139,253,0.18);border-radius:5px;font-size:9px;color:#8b949e;line-height:1.5;}}
#sp-interp.good{{background:rgba(63,185,80,0.07);border-color:rgba(63,185,80,0.2);color:#b5d9b8}}
#sp-interp.bad{{background:rgba(248,81,73,0.07);border-color:rgba(248,81,73,0.2);color:#e09090}}
#sp-collapsed-pill{{position:absolute;top:10px;left:10px;background:rgba(10,13,20,0.9);border:1px solid #2a3040;border-radius:6px;padding:4px 10px;font-size:10px;color:#8b949e;cursor:pointer;z-index:10;display:none;backdrop-filter:blur(6px);box-shadow:0 2px 12px rgba(0,0,0,0.5);transition:border-color .15s;}}
#sp-collapsed-pill:hover{{border-color:#388bfd;color:#e6edf3}}

/* ── Stats panel ─────────────────────────────────────────── */
.tab-btn.stats-active{{background:#8957e5;border-color:#8957e5;color:#fff;font-weight:700}}
#stats-panel{{flex:1;overflow-y:auto;padding:16px;display:none;flex-direction:column;gap:14px;background:#0d1117}}
.st-cards{{display:flex;gap:10px;flex-wrap:wrap;flex-shrink:0}}
.st-card{{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:10px 16px;min-width:130px;flex:1}}
.st-lbl{{font-size:10px;color:#484f58;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px}}
.st-val{{font-size:18px;font-weight:700;color:#e6edf3;white-space:nowrap}}
.st-val.green{{color:#3fb950}}.st-val.red{{color:#f85149}}
.st-sec-title{{font-size:10px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px;padding-bottom:4px;border-bottom:1px solid #21262d}}
.st-eq-wrap{{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:12px 14px;flex-shrink:0}}
.st-eq-wrap canvas{{width:100%!important;height:160px!important}}
.st-breaks{{display:flex;gap:10px;flex-wrap:wrap;flex-shrink:0}}
.st-break-block{{flex:1;min-width:180px;background:#161b22;border:1px solid #21262d;border-radius:8px;padding:12px 14px}}
.st-tbl-wrap{{background:#161b22;border:1px solid #21262d;border-radius:8px;padding:12px 14px;flex-shrink:0}}
.st-tbl{{width:100%;border-collapse:collapse;font-size:11px}}
.st-tbl th{{text-align:left;color:#484f58;font-size:10px;padding:3px 6px;border-bottom:1px solid #21262d;white-space:nowrap}}
.st-tbl td{{padding:3px 6px;color:#8b949e;border-bottom:1px solid rgba(33,38,45,0.5)}}
.st-tbl tr:last-child td{{border-bottom:none}}
.st-tbl .td-r{{text-align:right}}.st-tbl .td-sym{{color:#e6edf3;font-weight:700}}
.st-tbl .pos{{color:#3fb950}}.st-tbl .neg{{color:#f85149}}
.st-tbl tbody tr:hover{{background:rgba(33,38,45,0.4)}}
</style>
</head>
<body>

<div id="hdr">
  <h1>RBF · Trade Review</h1>
  <div class="tab-btns">
    <button class="tab-btn active" id="tab-live" onclick="switchTab('live')">Live</button>
    <button class="tab-btn" id="tab-bt" onclick="switchTab('bt')">Backtest</button>
    <button class="tab-btn" id="tab-stats" onclick="switchTab('stats')">Stats</button>
  </div>
  <span class="hsep">|</span>
  <span class="hstat">n=<b id="st-n">-</b></span>
  <span class="hstat">WR=<b id="st-wr">-</b></span>
  <span class="hstat" style="color:#3fb950">W=<b id="st-w">-</b></span>
  <span class="hstat" style="color:#f85149">L=<b id="st-l">-</b></span>
  <span class="hstat">PnL=<b id="st-pnl">-</b></span>
  <span class="hstat">Capital=<b id="st-cap">-</b></span>
  <div class="filters">
    <select id="f-sym" onchange="applyFilters()">
      <option value="">Todos</option>
      <option>BTCUSDT</option><option>ETHUSDT</option><option>BNBUSDT</option>
      <option>SOLUSDT</option><option>XRPUSDT</option>
    </select>
    <select id="f-ses" onchange="applyFilters()">
      <option value="">Sesion</option>
      <option value="London">London</option>
      <option value="LondonNyOverlap">Overlap</option>
      <option value="NewYork">NY</option>
    </select>
    <select id="f-dir" onchange="applyFilters()">
      <option value="">Direccion</option>
      <option value="Short">Short</option><option value="Long">Long</option>
    </select>
    <select id="f-res" onchange="applyFilters()">
      <option value="">Resultado</option>
      <option value="win">WIN</option><option value="loss">LOSS</option>
    </select>
  </div>
</div>

<div id="wrap">
  <div id="sidebar">
    <div id="sb-hdr">
      <span><b id="sb-count">0</b> trades</span>
      <span id="sb-equity" style="color:#3fb950">$50.00</span>
    </div>
    <div id="trade-list"></div>
  </div>
  <div id="main">
    <div id="chart-wrap">
      <div id="chart"></div>
      <canvas id="overlay"></canvas>
      <div id="empty">Selecciona un trade</div>
      <div id="setup-panel">
        <div id="sp-header" onclick="togglePanel()">
          <span id="sp-icon">o</span>
          <span id="sp-label">Setup</span>
          <span id="sp-score"></span>
          <span id="sp-toggle">v</span>
        </div>
        <div id="sp-body">
          <div id="sp-rows"></div>
          <div id="sp-interp"></div>
        </div>
      </div>
      <div id="sp-collapsed-pill" onclick="togglePanel()">Setup</div>
    </div>
    <div id="info-bar">
      <span id="ib-hint">Selecciona un trade para ver detalles</span>
      <span id="ib-content" style="display:none;gap:16px;flex-wrap:wrap;align-items:center">
        <span><span class="lbl">SYM </span><span class="val" id="ib-sym"></span></span>
        <span><span class="lbl">DIR </span><span class="val" id="ib-dir"></span></span>
        <span><span class="lbl">SES </span><span class="val" id="ib-ses"></span></span>
        <span><span class="lbl">ENTRY </span><span class="c-entry" id="ib-ep"></span></span>
        <span><span class="lbl">STOP </span><span class="c-stop" id="ib-sp"></span></span>
        <span><span class="lbl">TARGET </span><span class="c-target" id="ib-tp"></span></span>
        <span><span class="lbl">EXIT </span><span class="val" id="ib-xp"></span></span>
        <span><span class="lbl">RR </span><span class="c-rr" id="ib-rr"></span></span>
        <span><span class="lbl">STOP% </span><span class="c-rr" id="ib-spct"></span></span>
        <span><span class="lbl">RIESGO </span><span class="val" id="ib-risk"></span></span>
        <span><span class="lbl">R </span><span class="val" id="ib-r"></span></span>
        <span><span class="lbl">PnL </span><span class="val" id="ib-usd"></span></span>
        <span><span class="lbl">EQUITY </span><span class="val" id="ib-eq"></span></span>
        <span><span class="lbl">DUR </span><span class="val" style="color:#388bfd" id="ib-dur"></span></span>
      </span>
    </div>
  </div>
</div>

<div id="stats-panel">
  <div class="st-cards">
    <div class="st-card"><div class="st-lbl">Balance</div><div id="stc-bal" class="st-val">-</div></div>
    <div class="st-card"><div class="st-lbl">PnL ($450)</div><div id="stc-pnl" class="st-val">-</div></div>
    <div class="st-card"><div class="st-lbl">Win Rate</div><div id="stc-wr" class="st-val">-</div></div>
    <div class="st-card"><div class="st-lbl">Total R</div><div id="stc-r" class="st-val">-</div></div>
    <div class="st-card"><div class="st-lbl">Max Drawdown</div><div id="stc-dd" class="st-val red">-</div></div>
  </div>
  <div class="st-eq-wrap">
    <div class="st-sec-title">Equity Curve &middot; $450 capital &middot; riesgo fijo $9/trade</div>
    <canvas id="st-eq-canvas"></canvas>
  </div>
  <div class="st-breaks">
    <div class="st-break-block">
      <div class="st-sec-title">Por Sesion</div>
      <table class="st-tbl" id="st-ses-tbl"></table>
    </div>
    <div class="st-break-block">
      <div class="st-sec-title">Por Simbolo</div>
      <table class="st-tbl" id="st-sym-tbl"></table>
    </div>
    <div class="st-break-block">
      <div class="st-sec-title">Por Direccion</div>
      <table class="st-tbl" id="st-dir-tbl"></table>
    </div>
  </div>
  <div class="st-tbl-wrap">
    <div class="st-sec-title">Todos los Trades</div>
    <table class="st-tbl" id="st-trade-tbl"></table>
  </div>
</div>

<script>
const LIVE_TRADES = {LIVE_JSON};
const BT_TRADES   = {BT_JSON};
const LIVE_KLINES = {LIVE_K_JSON};
const BT_KLINES   = {BT_K_JSON};
const INIT_CAP    = {ACCOUNT_JSON};
const POSITION    = {POS_JSON};
const STATS_DATA  = {STATS_JSON};
const STATS_CAP   = {STATS_CAP};

let currentTab = 'live';
let stChart = null;
let currentTrades = LIVE_TRADES;
let currentKlines  = LIVE_KLINES;
let filtered=[], selTrade=null, chart=null, cSeries=null, ov=null;

function switchTab(tab) {{
  currentTab = tab;
  currentTrades = tab==='live' ? LIVE_TRADES : BT_TRADES;
  currentKlines  = tab==='live' ? LIVE_KLINES : BT_KLINES;
  document.getElementById('tab-live').className  = 'tab-btn' + (tab==='live'?' active':'');
  document.getElementById('tab-bt').className    = 'tab-btn' + (tab==='bt'?' bt-active':'');
  document.getElementById('tab-stats').className = 'tab-btn' + (tab==='stats'?' stats-active':'');

  const isStats = tab==='stats';
  document.getElementById('wrap').style.display        = isStats ? 'none' : 'flex';
  document.getElementById('stats-panel').style.display = isStats ? 'flex' : 'none';

  if(isStats){{ renderStats(); return; }}

  // reset filtros
  ['f-sym','f-ses','f-dir','f-res'].forEach(id=>document.getElementById(id).value='');
  selTrade = null;
  if(chart){{ chart.remove(); chart=null; cSeries=null; }}
  document.getElementById('empty').style.display='flex';
  document.getElementById('ib-hint').style.display='block';
  document.getElementById('ib-content').style.display='none';
  document.getElementById('setup-panel').style.display='none';
  document.getElementById('sp-collapsed-pill').style.display='none';
  applyFilters();
}}

function sesLabel(s){{
  if(s==='LondonNyOverlap') return 'Overlap';
  if(s==='NewYork') return 'NY';
  return s||'?';
}}
function fmt(n,prec){{ return (n>=0?'+':'')+n.toFixed(prec); }}
function fmtDur(m){{
  if(m==null) return '-';
  if(m<60) return m+'m';
  return Math.floor(m/60)+'h '+(m%60)+'m';
}}

function applyFilters(){{
  const sym=document.getElementById('f-sym').value;
  const ses=document.getElementById('f-ses').value;
  const dir=document.getElementById('f-dir').value;
  const res=document.getElementById('f-res').value;
  filtered=currentTrades.filter(t=>{{
    if(sym && t.sym!==sym) return false;
    if(ses && t.session!==ses) return false;
    if(dir && t.dir!==dir) return false;
    if(res==='win'  && t.result_r<=0) return false;
    if(res==='loss' && t.result_r>=0) return false;
    return true;
  }});
  updateStats(); renderList();
}}

function updateStats(){{
  const n=filtered.length;
  const wins=filtered.filter(t=>t.result_r>0).length;
  const pnlR=filtered.reduce((s,t)=>s+t.result_r,0);
  const eq=filtered.length ? filtered[filtered.length-1].equity : INIT_CAP;
  document.getElementById('st-n').textContent=n;
  document.getElementById('st-wr').textContent=n?Math.round(wins/n*100)+'%':'-';
  document.getElementById('st-w').textContent=wins;
  document.getElementById('st-l').textContent=n-wins;
  const pnlEl=document.getElementById('st-pnl');
  pnlEl.textContent=fmt(pnlR,2)+'R';
  pnlEl.style.color=pnlR>=0?'#3fb950':'#f85149';
  const capEl=document.getElementById('st-cap');
  capEl.textContent='$'+eq.toFixed(2);
  capEl.style.color=eq>=INIT_CAP?'#3fb950':'#f85149';
  document.getElementById('sb-count').textContent=n;
  const eqEl=document.getElementById('sb-equity');
  eqEl.textContent='$'+eq.toFixed(2);
  eqEl.style.color=eq>=INIT_CAP?'#3fb950':'#f85149';
}}

function renderList(){{
  const list=document.getElementById('trade-list');
  list.innerHTML='';
  filtered.forEach(t=>{{
    const isWin=t.result_r>0;
    const cls=isWin?'win':'loss';
    const dt=new Date(t.ts_ms);
    const dtStr=`${{(dt.getUTCMonth()+1).toString().padStart(2,'0')}}-${{dt.getUTCDate().toString().padStart(2,'0')}} ${{dt.getUTCHours().toString().padStart(2,'0')}}:${{dt.getUTCMinutes().toString().padStart(2,'0')}}`;
    const rStr=(t.result_r>=0?'+':'')+t.result_r.toFixed(2)+'R';
    const usdStr=(t.pnl_usd>=0?'+$':'-$')+Math.abs(t.pnl_usd).toFixed(2);
    const sym=t.sym.replace('USDT','');
    const el=document.createElement('div');
    el.className=`trade-item ${{cls}}${{t===selTrade?' selected':''}}`;
    el.innerHTML=`
      <div class="t-row1">
        <span class="t-num">#${{String(t.idx).padStart(2,'0')}}</span>
        <span class="t-badge badge-${{t.dir.toLowerCase()}}">${{t.dir.toUpperCase()}}</span>
        <span class="t-sym">${{sym}}</span>
        <span class="t-ses">${{sesLabel(t.session)}}</span>
        <span class="t-r ${{isWin?'pos':'neg'}}">${{rStr}}</span>
      </div>
      <div class="t-row2">
        <span>${{dtStr}}</span>
        <span class="t-usd" style="color:${{isWin?'#3fb950':'#f85149'}}">${{usdStr}}</span>
        <span class="t-eq">eq=${{t.equity.toFixed(0)}}</span>
        ${{t.is_backtest?'<span class="bt-badge">BT</span>':''}}
        ${{t.duration_min!=null?`<span style="color:#388bfd">${{fmtDur(t.duration_min)}}</span>`:''}}
      </div>`;
    el.addEventListener('click',()=>selectTrade(t));
    list.appendChild(el);
  }});
}}

function selectTrade(t){{
  selTrade=t; renderList(); updateInfoBar(t); loadChart(t); buildSetupPanel(t);
}}

function updateInfoBar(t){{
  document.getElementById('ib-hint').style.display='none';
  const c=document.getElementById('ib-content');
  c.style.display='flex';
  const prec=t.entry>100?1:t.entry>1?4:6;
  document.getElementById('ib-sym').textContent=t.sym;
  const de=document.getElementById('ib-dir');
  de.textContent=t.dir; de.style.color=t.dir==='Short'?'#f85149':'#3fb950';
  document.getElementById('ib-ses').textContent=sesLabel(t.session);
  document.getElementById('ib-ep').textContent=t.entry.toFixed(prec);
  document.getElementById('ib-sp').textContent=t.stop.toFixed(prec);
  document.getElementById('ib-tp').textContent=t.target.toFixed(prec);
  document.getElementById('ib-xp').textContent=(t.exit||0).toFixed(prec);
  const risk=Math.abs(t.entry-t.stop), rw=Math.abs(t.target-t.entry);
  document.getElementById('ib-rr').textContent=risk>0?(rw/risk).toFixed(2)+':1':'?';
  document.getElementById('ib-spct').textContent=(t.stop_pct||0).toFixed(3)+'%';
  document.getElementById('ib-risk').textContent='$'+(t.risk_usd||0).toFixed(2);
  const re=document.getElementById('ib-r');
  re.textContent=(t.result_r>=0?'+':'')+t.result_r.toFixed(2)+'R · '+t.reason;
  re.style.color=t.result_r>0?'#3fb950':'#f85149';
  const ue=document.getElementById('ib-usd');
  ue.textContent=(t.pnl_usd>=0?'+$':'-$')+Math.abs(t.pnl_usd).toFixed(2);
  ue.style.color=t.pnl_usd>=0?'#3fb950':'#f85149';
  const ee=document.getElementById('ib-eq');
  ee.textContent='$'+t.equity.toFixed(2);
  ee.style.color=t.equity>=INIT_CAP?'#3fb950':'#f85149';
  document.getElementById('ib-dur').textContent=fmtDur(t.duration_min);
}}

function loadChart(t){{
  const wrap=document.getElementById('chart-wrap');
  document.getElementById('empty').style.display='none';
  if(chart){{ chart.remove(); chart=null; cSeries=null; }}
  chart=LightweightCharts.createChart(document.getElementById('chart'),{{
    width:wrap.clientWidth, height:wrap.clientHeight,
    layout:{{background:{{color:'#0d1117'}},textColor:'#8b949e'}},
    grid:{{vertLines:{{color:'#21262d'}},horzLines:{{color:'#21262d'}}}},
    crosshair:{{mode:LightweightCharts.CrosshairMode.Normal}},
    timeScale:{{borderColor:'#30363d',timeVisible:true,secondsVisible:false}},
    rightPriceScale:{{borderColor:'#30363d'}},
  }});
  cSeries=chart.addCandlestickSeries({{
    upColor:'#3fb950',downColor:'#f85149',
    borderUpColor:'#3fb950',borderDownColor:'#f85149',
    wickUpColor:'#3fb950',wickDownColor:'#f85149',
  }});
  const candles=currentKlines[t.idx]||[];
  if(candles.length) cSeries.setData(candles);
  chart.timeScale().setVisibleRange({{from:t.ts-60*60, to:t.ts+120*60}});
  ov=document.getElementById('overlay');
  ov.width=wrap.clientWidth; ov.height=wrap.clientHeight;
  function redraw(){{
    const ctx=ov.getContext('2d');
    ctx.clearRect(0,0,ov.width,ov.height);
    drawTrade(ctx,t);
  }}
  chart.timeScale().subscribeVisibleLogicalRangeChange(redraw);
  chart.subscribeCrosshairMove(redraw);
  redraw();
  new ResizeObserver(()=>{{
    const w=wrap.clientWidth,h=wrap.clientHeight;
    chart.applyOptions({{width:w,height:h}});
    ov.width=w; ov.height=h; redraw();
  }}).observe(wrap);
}}

function drawTrade(ctx,t){{
  if(!chart||!cSeries) return;
  const eX=chart.timeScale().timeToCoordinate(t.ts);
  const eY=cSeries.priceToCoordinate(t.entry);
  const sY=cSeries.priceToCoordinate(t.stop);
  const tY=cSeries.priceToCoordinate(t.target);
  if(eX==null||eY==null||sY==null||tY==null) return;
  let exitTs=t.ts+90*60;
  if(t.closed_at){{
    const ex=Math.floor(new Date(t.closed_at).getTime()/1000);
    if(ex>t.ts && ex<t.ts+180*60) exitTs=ex;
  }}
  let xX=chart.timeScale().timeToCoordinate(exitTs);
  if(xX==null||xX<=eX) xX=eX+Math.max(ov.width*0.25,120);
  const x0=eX,x1=xX,w=Math.max(x1-x0,4);
  const prec=t.entry>100?1:t.entry>1?4:6;
  const sy0=Math.min(eY,sY),sh=Math.abs(sY-eY);
  ctx.fillStyle='rgba(248,81,73,0.30)'; ctx.fillRect(x0,sy0,w,sh);
  ctx.strokeStyle='rgba(248,81,73,0.85)'; ctx.lineWidth=1.5; ctx.setLineDash([]);
  ctx.strokeRect(x0,sy0,w,sh);
  const ty0=Math.min(eY,tY),th=Math.abs(tY-eY);
  ctx.fillStyle='rgba(63,185,80,0.30)'; ctx.fillRect(x0,ty0,w,th);
  ctx.strokeStyle='rgba(63,185,80,0.85)'; ctx.lineWidth=1.5;
  ctx.strokeRect(x0,ty0,w,th);
  ctx.strokeStyle='rgba(230,237,243,0.55)'; ctx.lineWidth=1; ctx.setLineDash([5,4]);
  ctx.beginPath(); ctx.moveTo(x0,eY); ctx.lineTo(x1,eY); ctx.stroke();
  ctx.setLineDash([]);
  ctx.strokeStyle='rgba(88,166,255,0.3)'; ctx.lineWidth=1; ctx.setLineDash([3,5]);
  ctx.beginPath(); ctx.moveTo(x0,0); ctx.lineTo(x0,ov.height); ctx.stroke();
  ctx.setLineDash([]);
  if(t.exit&&t.exit>0){{
    const xpY=cSeries.priceToCoordinate(t.exit);
    if(xpY!=null){{
      ctx.strokeStyle=t.result_r>0?'rgba(63,185,80,0.7)':'rgba(248,81,73,0.7)';
      ctx.lineWidth=1.5; ctx.setLineDash([3,3]);
      ctx.beginPath(); ctx.moveTo(x0,xpY); ctx.lineTo(x1,xpY); ctx.stroke();
      ctx.setLineDash([]);
    }}
  }}
  ctx.font='bold 11px monospace'; ctx.textAlign='left';
  ctx.fillStyle='rgba(230,237,243,0.9)'; ctx.fillText('ENTRY  '+t.entry.toFixed(prec), x1+6, eY-3);
  ctx.fillStyle='rgba(248,81,73,0.95)';  ctx.fillText('SL  '+t.stop.toFixed(prec),     x1+6, sY+4);
  ctx.fillStyle='rgba(63,185,80,0.95)';  ctx.fillText('TP  '+t.target.toFixed(prec),   x1+6, tY+4);
  ctx.font='bold 10px monospace'; ctx.textAlign='center';
  ctx.fillStyle='rgba(248,81,73,0.7)';
  ctx.fillText('STOP LOSS', x0+w/2, sy0+sh/2+4);
  ctx.fillStyle='rgba(63,185,80,0.7)';
  ctx.fillText('TAKE PROFIT', x0+w/2, ty0+th/2+4);
  ctx.textAlign='left';
  const rStr=(t.result_r>=0?'+':'')+t.result_r.toFixed(2)+'R';
  const uStr=(t.pnl_usd>=0?' +$':' -$')+Math.abs(t.pnl_usd).toFixed(2);
  ctx.font='bold 13px monospace';
  ctx.fillStyle=t.result_r>0?'rgba(63,185,80,0.95)':'rgba(248,81,73,0.95)';
  const badgeY=Math.min(sy0,ty0)-8;
  ctx.fillText(rStr+uStr, x0+6, badgeY>16?badgeY:16);
  ctx.font='bold 11px monospace';
  ctx.fillStyle=t.dir==='Short'?'rgba(248,81,73,0.8)':'rgba(63,185,80,0.8)';
  ctx.fillText(t.dir.toUpperCase(), x0+6, eY+(t.dir==='Short'?-16:14));
  if(t.is_backtest){{
    ctx.font='10px monospace'; ctx.fillStyle='rgba(210,153,34,0.8)';
    ctx.fillText('BT · VR '+t.vr_at_breakout+'x · Rng '+t.range_pct+'%', x0+6, eY+(t.dir==='Short'?-30:28));
  }}
}}

let panelOpen=true;
function togglePanel(){{
  panelOpen=!panelOpen;
  const panel=document.getElementById('setup-panel');
  const pill=document.getElementById('sp-collapsed-pill');
  if(panelOpen){{ panel.style.display='block'; pill.style.display='none'; }}
  else{{ panel.style.display='none'; pill.style.display='block'; }}
}}

function buildSetupPanel(t){{
  const panel=document.getElementById('setup-panel');
  const pill=document.getElementById('sp-collapsed-pill');
  const rows=document.getElementById('sp-rows');
  const interp=document.getElementById('sp-interp');
  const scoreEl=document.getElementById('sp-score');
  if(panelOpen){{ panel.style.display='block'; pill.style.display='none'; }}
  else{{ panel.style.display='none'; pill.style.display='block'; }}
  rows.innerHTML='';
  const isShort=t.dir==='Short';
  const n=t.score;
  scoreEl.textContent=n!=null?'s'+n:(t.is_backtest?'BT':'');
  function row(name,valStr,ok){{
    const el=document.createElement('div');
    const cls=ok===true?'ok':ok===false?'bad':'';
    el.className='sp-row'+(cls?' '+cls:'');
    const ico=ok===true?'<span class="sp-icon-cell ico-ok">+</span>'
              :ok===false?'<span class="sp-icon-cell ico-bad">x</span>'
              :'<span class="sp-icon-cell ico-neu">.</span>';
    el.innerHTML=`<span class="sp-name">${{name}}</span><span class="sp-val">${{valStr}}</span>${{ico}}`;
    rows.appendChild(el);
  }}
  const vr=t.vr_at_breakout;
  row('VR Breakout', vr!=null?vr.toFixed(2)+'x':'-', vr!=null?vr>=3:null);
  const cvd=t.cvd_in_range;
  const cvdOk=cvd!=null?(isShort?cvd<0:cvd>0):null;
  row('CVD in Range', cvd!=null?(cvd>0?'+':'')+Math.round(cvd).toLocaleString():'-', cvdOk);
  const rp=t.range_pct;
  row('Range Size', rp!=null?rp.toFixed(3)+'%':'-', rp!=null?(rp>=0.08&&rp<=0.55):null);
  const cs=t.cvd_slope;
  const csOk=cs!=null?(isShort?cs<0:cs>0):null;
  row('CVD Slope', cs!=null?(cs>=0?'+':'')+cs.toFixed(2):'-', csOk);
  const obi=t.obi;
  const obiOk=obi!=null?(isShort?obi<0:obi>0):null;
  row('OBI', obi!=null?(obi>=0?'+':'')+obi.toFixed(3):'-', obiOk);
  if(!t.is_backtest){{
    const regime=t.regime||'';
    const regAligned=regime?(isShort?/bear|down|weak/i.test(regime):/bull|up|strong/i.test(regime)):null;
    row('Macro Regime', regime||'-', regAligned);
    const pv=t.price_vs_vwap_pct;
    const pvOk=pv!=null?(isShort?pv<0:pv>0):null;
    row('vs VWAP', pv!=null?(pv>=0?'+':'')+pv.toFixed(3)+'%':'-', pvOk);
    const fr=t.funding;
    const frOk=fr!=null?(isShort?fr>0.00005:fr<-0.00005):null;
    row('Funding', fr!=null?(fr*100>=0?'+':'')+(fr*100).toFixed(4)+'%':'-', frOk);
    const evArr=Array.isArray(t.evidence)?t.evidence:[];
    if(evArr.length>0){{
      const sep=document.createElement('div'); sep.id='sp-sep'; rows.appendChild(sep);
      const w=document.createElement('div'); w.className='sp-ev-wrap';
      evArr.slice(0,5).forEach(ev=>{{
        const el=document.createElement('div');
        el.className='sp-ev'; el.textContent=ev;
        w.appendChild(el);
      }});
      rows.appendChild(w);
    }}
  }}
  const reason=t.reason||'';
  const good=t.result_r>0;
  interp.textContent=t.is_backtest
    ? `Backtest · ${{t.range_bars}}b rango · VR ${{(vr||0).toFixed(1)}}x · ${{reason}}`
    : (good?'Setup valido · resultado confirmado':'Setup con debilidades');
  interp.className=good?'good':'bad';
}}

function renderStats() {{
  if (!STATS_DATA || !STATS_DATA.length) {{
    document.getElementById('stats-panel').innerHTML='<div style="color:#484f58;padding:32px;font-size:13px">Sin datos (rbf_pnl_450.csv no encontrado)</div>';
    return;
  }}
  const n = STATS_DATA.length;
  const wins = STATS_DATA.filter(t => t.r > 0).length;
  const bal  = STATS_DATA[n-1].bal;
  const pnl  = bal - STATS_CAP;
  const roi  = pnl / STATS_CAP * 100;
  const wr   = wins / n * 100;
  const totalR = STATS_DATA.reduce((s,t) => s+t.r, 0);

  let peak=STATS_CAP, maxDD=0;
  [STATS_CAP, ...STATS_DATA.map(t=>t.bal)].forEach(b=>{{
    if(b>peak) peak=b;
    const dd=(peak-b)/peak*100;
    if(dd>maxDD) maxDD=dd;
  }});

  const fmtS = (v,d=2)=> (v>=0?'+':'')+v.toFixed(d);

  const elBal = document.getElementById('stc-bal');
  elBal.textContent = '$'+bal.toFixed(2);
  elBal.className = 'st-val '+(pnl>=0?'green':'red');

  const elPnl = document.getElementById('stc-pnl');
  elPnl.textContent = fmtS(pnl)+'$ ('+fmtS(roi,1)+'%)';
  elPnl.className = 'st-val '+(pnl>=0?'green':'red');

  document.getElementById('stc-wr').textContent = wr.toFixed(1)+'% ('+wins+'/'+n+')';

  const elR = document.getElementById('stc-r');
  elR.textContent = fmtS(totalR)+'R';
  elR.className = 'st-val '+(totalR>=0?'green':'red');

  document.getElementById('stc-dd').textContent = '-'+maxDD.toFixed(1)+'%';

  // equity curve
  const balArr = [STATS_CAP, ...STATS_DATA.map(t=>t.bal)];
  const lblArr = ['0', ...STATS_DATA.map(t=>'#'+t.num)];
  const ptColors = ['rgba(56,139,253,0.9)', ...STATS_DATA.map(t=> t.r>0?'rgba(63,185,80,0.8)':'rgba(248,81,73,0.8)')];

  if(stChart){{ stChart.destroy(); stChart=null; }}
  const ctx = document.getElementById('st-eq-canvas').getContext('2d');
  stChart = new Chart(ctx, {{
    type: 'line',
    data: {{
      labels: lblArr,
      datasets: [{{
        data: balArr,
        borderColor: '#388bfd',
        borderWidth: 1.5,
        fill: true,
        backgroundColor: (context)=>{{
          const chart=context.chart;
          const {{ctx:c,chartArea}}=chart;
          if(!chartArea) return 'transparent';
          const grad=c.createLinearGradient(0,chartArea.top,0,chartArea.bottom);
          grad.addColorStop(0,'rgba(56,139,253,0.22)');
          grad.addColorStop(1,'rgba(56,139,253,0.01)');
          return grad;
        }},
        pointRadius: 3,
        pointHoverRadius: 5,
        pointBackgroundColor: ptColors,
        pointBorderColor: 'transparent',
        tension: 0.2,
      }}]
    }},
    options: {{
      responsive:true, maintainAspectRatio:false,
      interaction:{{mode:'index',intersect:false}},
      scales:{{
        x:{{ticks:{{font:{{size:9}},color:'#484f58',maxTicksLimit:20}},grid:{{color:'rgba(33,38,45,0.7)'}}}},
        y:{{
          ticks:{{font:{{size:9}},color:'#8b949e',callback:v=>'$'+v.toFixed(0)}},
          grid:{{color:'rgba(33,38,45,0.7)'}},
          suggestedMin: STATS_CAP * 0.85
        }}
      }},
      plugins:{{
        legend:{{display:false}},
        tooltip:{{
          backgroundColor:'rgba(10,13,20,0.95)',
          borderColor:'#21262d',
          borderWidth:1,
          titleColor:'#8b949e',
          bodyColor:'#e6edf3',
          bodyFont:{{size:11,family:'monospace'}},
          callbacks:{{
            title: items => lblArr[items[0].dataIndex],
            label: ctx2=>{{
              const i=ctx2.dataIndex;
              if(i===0) return ' Capital inicial: $'+STATS_CAP.toFixed(2);
              const t=STATS_DATA[i-1];
              const ses=t.ses==='LondonNyOverlap'?'Overlap':t.ses;
              return ` ${{t.sym.replace('USDT','')}} ${{t.dir}} ${{ses}} · ${{fmtS(t.r,2)}}R · ${{fmtS(t.pnl,2)}}$ · $$${{t.bal.toFixed(2)}}`;
            }}
          }}
        }}
      }}
    }}
  }});

  // breakdown helper
  function buildBreak(tblId, key, label) {{
    const grp = {{}};
    STATS_DATA.forEach(t=>{{
      const k=t[key]; if(!grp[k]) grp[k]={{n:0,w:0,r:0,pnl:0}};
      grp[k].n++; if(t.r>0) grp[k].w++; grp[k].r+=t.r; grp[k].pnl+=t.pnl;
    }});
    const tbl=document.getElementById(tblId);
    tbl.innerHTML=`<thead><tr><th>${{label}}</th><th class="td-r">n</th><th class="td-r">WR</th><th class="td-r">R</th><th class="td-r">PnL</th></tr></thead><tbody></tbody>`;
    const tbody=tbl.querySelector('tbody');
    Object.entries(grp).sort((a,b)=>b[1].pnl-a[1].pnl).forEach(([k,v])=>{{
      const wr=(v.w/v.n*100).toFixed(0);
      const kd=k==='LondonNyOverlap'?'Overlap':k;
      const rc=v.r>=0?'pos':'neg'; const pc=v.pnl>=0?'pos':'neg';
      const tr=document.createElement('tr');
      tr.innerHTML=`<td class="td-sym">${{kd}}</td><td class="td-r">${{v.n}}</td><td class="td-r">${{wr}}%</td><td class="td-r ${{rc}}">${{fmtS(v.r)}}R</td><td class="td-r ${{pc}}">${{fmtS(v.pnl)}}$</td>`;
      tbody.appendChild(tr);
    }});
  }}
  buildBreak('st-ses-tbl','ses','Sesion');
  buildBreak('st-sym-tbl','sym','Simbolo');
  buildBreak('st-dir-tbl','dir','Direccion');

  // trade table
  const ttbl=document.getElementById('st-trade-tbl');
  ttbl.innerHTML='<thead><tr><th>#</th><th>Fecha</th><th>Sym</th><th>Dir</th><th>Sesion</th><th class="td-r">R</th><th class="td-r">PnL$</th><th class="td-r">Balance</th><th>Razon</th></tr></thead><tbody></tbody>';
  const tbody2=ttbl.querySelector('tbody');
  STATS_DATA.forEach(t=>{{
    const rc=t.r>0?'pos':'neg'; const pc=t.pnl>0?'pos':'neg';
    const ses=t.ses==='LondonNyOverlap'?'Overlap':t.ses;
    const tr=document.createElement('tr');
    tr.innerHTML=`<td class="td-r">${{t.num}}</td><td style="color:#484f58;font-size:10px">${{t.fecha}}</td><td class="td-sym">${{t.sym.replace('USDT','')}}</td><td class="${{t.dir==='Short'?'neg':'pos'}}">${{t.dir}}</td><td>${{ses}}</td><td class="td-r ${{rc}}">${{fmtS(t.r,2)}}R</td><td class="td-r ${{pc}}">${{fmtS(t.pnl,2)}}$</td><td class="td-r" style="color:#e6edf3">${{t.bal.toFixed(2)}}</td><td style="color:#484f58;font-size:10px">${{t.reason}}</td>`;
    tbody2.appendChild(tr);
  }});
}}

applyFilters();
if(filtered.length) selectTrade(filtered[0]);
</script>
</body>
</html>"""

out = ROOT / 'rbf_review.html'
out.write_text(HTML, encoding='utf-8')
print(f"Generado: {out}")
webbrowser.open(out.as_uri())
print("Listo.")
