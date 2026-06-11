#!/usr/bin/env python3
"""
Backtest Buyer Exhaustion — corre como subprocess desde Vite, imprime JSON a stdout.
Uso: python api/be_backtest_script.py --days 14

Fuente de datos:
  - Reciente: Supabase *_bars (bar_delta, session ya calculados por el monitor)
  - Histórico: Binance REST API v3/klines (sin auth, hasta ~2 años atrás)
    bar_delta = 2 * taker_buy_base_vol - volume
    session = clasificado por hora UTC (espejo de session_tracker.rs)

Lógica: espejo de BuyerExhaustionConfig::default()
  - Rango 8 barras M1 con CVD neto > 0 (compradores atrapados)
  - Giro CVD: |sum(últimas 5 deltas)| / range_cvd >= 0.40
  - Ruptura a la baja: VR_50 >= 2.5, bar_delta < 0, close < range_low
  - Micro: close_location <= 0.35, bear_body >= 0.35, upper_wick <= 0.30
"""
import json, os, sys, time, urllib.request, urllib.parse, argparse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent.parent
_env = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        _env[k.strip()] = v.strip().strip('"').strip("'")

SUPABASE_URL = _env.get('SUPABASE_URL', os.environ.get('SUPABASE_URL', ''))
SUPABASE_KEY = _env.get('SUPABASE_KEY', os.environ.get('SUPABASE_KEY', ''))

# ─── Parámetros (espejo de BuyerExhaustionConfig::default()) ─────────────────

RANGE_WINDOW       = 8
RANGE_MIN_PCT      = 0.05
RANGE_MAX_PCT      = 0.50
PRE_CVD_BARS       = 5
CVD_FLIP_RATIO_MIN = 0.40
VR_MIN             = 2.5
VR_WINDOW          = 50      # BuyerExhaustionConfig.vr_window
CLOSE_LOC_MAX      = 0.35
BEAR_BODY_MIN      = 0.35
UPPER_WICK_MAX     = 0.30
TIME_STOP_BARS     = 30
COOLDOWN_BARS      = 60
SESSIONS_OK        = {'London', 'LondonNyOverlap'}
CAPITAL            = 500.0
RISK_USD           = CAPITAL * 0.02

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT']
TABLES  = {s: s.lower().replace('usdt', '_bars') + 'usdt'[:-4]
           for s in SYMBOLS}
# Corrección de nombres:
TABLES = {
    'BTCUSDT': 'btc_bars', 'ETHUSDT': 'eth_bars', 'BNBUSDT': 'bnb_bars',
    'SOLUSDT': 'sol_bars', 'XRPUSDT': 'xrp_bars',
}
SB_COLS = 'ts_ms,open,high,low,close,volume,bar_delta,session'

BINANCE_API = 'https://api.binance.com/api/v3/klines'

# ─── Session classifier (espejo de data::session::session_tracker) ───────────

def classify_session(ts_ms: int) -> str:
    """Clasifica una barra M1 por su open_time UTC."""
    h = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).hour
    m = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).minute
    hm = h + m / 60.0
    if  7.0 <= hm < 12.0: return 'London'
    if 12.0 <= hm < 16.0: return 'LondonNyOverlap'
    if 16.0 <= hm < 21.0: return 'NewYork'
    if  1.0 <= hm <  7.0: return 'Asia'
    return 'Other'

# ─── Supabase fetch ───────────────────────────────────────────────────────────

def sb_earliest_ms() -> int | None:
    """Primer ts_ms con bar_delta en Supabase (entre todos los símbolos)."""
    earliest = None
    for table in TABLES.values():
        qs = urllib.parse.urlencode({
            'select': 'ts_ms', 'bar_delta': 'not.is.null',
            'order': 'ts_ms.asc', 'limit': '1',
        })
        req = urllib.request.Request(
            f'{SUPABASE_URL}/rest/v1/{table}?{qs}',
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
        )
        rows = json.loads(urllib.request.urlopen(req, timeout=15).read())
        if rows:
            ms = rows[0]['ts_ms']
            if earliest is None or ms < earliest:
                earliest = ms
    return earliest

def sb_fetch(table: str, start_ms: int) -> list:
    rows, limit, offset = [], 1000, 0
    while True:
        qs = urllib.parse.urlencode({
            'select': SB_COLS, 'ts_ms': f'gte.{start_ms}',
            'order': 'ts_ms.asc', 'limit': str(limit), 'offset': str(offset),
        })
        req = urllib.request.Request(
            f'{SUPABASE_URL}/rest/v1/{table}?{qs}',
            headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
        )
        chunk = json.loads(urllib.request.urlopen(req, timeout=30).read())
        rows.extend(chunk)
        if len(chunk) < limit: break
        offset += limit
    return rows

# ─── Binance historical klines fetch ─────────────────────────────────────────

def binance_fetch(symbol: str, start_ms: int, end_ms: int) -> list:
    """
    Descarga M1 klines de Binance para el período [start_ms, end_ms).
    Devuelve lista con mismos campos que Supabase (excepto 'atr', que no necesitamos).
    bar_delta = 2 * taker_buy_base_vol - total_volume
    """
    bars = []
    cur  = start_ms
    while cur < end_ms:
        qs = urllib.parse.urlencode({
            'symbol': symbol, 'interval': '1m',
            'startTime': cur, 'endTime': min(cur + 1000 * 60000, end_ms),
            'limit': 1000,
        })
        try:
            req  = urllib.request.Request(f'{BINANCE_API}?{qs}')
            data = json.loads(urllib.request.urlopen(req, timeout=30).read())
        except Exception as e:
            print(f'[binance] warn {symbol}: {e}', file=sys.stderr)
            break
        if not data:
            break
        for k in data:
            open_time  = int(k[0])
            if open_time >= end_ms:
                break
            volume     = float(k[5])
            taker_buy  = float(k[9])
            bar_delta  = 2.0 * taker_buy - volume
            bars.append({
                'ts_ms':     open_time,
                'open':      float(k[1]),
                'high':      float(k[2]),
                'low':       float(k[3]),
                'close':     float(k[4]),
                'volume':    volume,
                'bar_delta': bar_delta,
                'session':   classify_session(open_time),
            })
        if len(data) < 1000:
            break
        cur = int(data[-1][0]) + 60_000  # avanzar 1 min después de la última barra
        time.sleep(0.08)   # respetar rate limit: ~12 req/s (límite Binance: 1200/min)
    return bars

# ─── Fusión de fuentes ────────────────────────────────────────────────────────

def load_bars(sym: str, table: str, start_ms: int, sb_first_ms: int | None) -> list:
    """
    Devuelve todas las barras del símbolo desde start_ms.
    - [start_ms, sb_first_ms) → Binance (histórico)
    - [sb_first_ms, now)       → Supabase (con session pre-clasificado por monitor)
    Si start_ms >= sb_first_ms, solo Supabase.
    """
    sb_start = sb_first_ms if sb_first_ms else start_ms

    binance_bars: list = []
    if start_ms < sb_start:
        print(f'[{sym}] Binance {_iso(start_ms)} → {_iso(sb_start)} ...', file=sys.stderr)
        binance_bars = binance_fetch(sym, start_ms, sb_start)
        print(f'[{sym}] Binance: {len(binance_bars)} bars', file=sys.stderr)

    sb_bars = sb_fetch(table, max(start_ms, sb_start))
    print(f'[{sym}] Supabase: {len(sb_bars)} bars', file=sys.stderr)

    all_bars = binance_bars + sb_bars
    all_bars.sort(key=lambda b: b['ts_ms'])
    # Eliminar posibles duplicados por solapamiento
    seen = set()
    unique = []
    for b in all_bars:
        if b['ts_ms'] not in seen:
            seen.add(b['ts_ms'])
            unique.append(b)
    return unique

def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d')

# ─── Simulación de trade ──────────────────────────────────────────────────────

def simulate(bars, entry, stop, target):
    for k, b in enumerate(bars):
        h, l = b['high'], b['low']
        if h >= stop:
            return (entry - stop) / abs(stop - entry), 'STOP', k + 1, b['ts_ms']
        if l <= target:
            return (entry - target) / abs(stop - entry), 'TARGET', k + 1, b['ts_ms']
        if k + 1 >= TIME_STOP_BARS:
            c = b['close']
            return (entry - c) / abs(stop - entry), 'TIME_STOP', k + 1, b['ts_ms']
    return 0.0, 'DATA_END', len(bars), (bars[-1]['ts_ms'] if bars else 0)

# ─── Detección + simulación por símbolo ──────────────────────────────────────

def detect(sym, bars, idx_off, equity_start):
    trades, equity, last_sig = [], equity_start, -COOLDOWN_BARS
    n = len(bars)

    for i in range(COOLDOWN_BARS, n - TIME_STOP_BARS):
        b   = bars[i]
        ses = b.get('session') or ''
        if ses not in SESSIONS_OK: continue
        if b.get('bar_delta') is None: continue
        if i - last_sig < COOLDOWN_BARS: continue

        win = bars[i - RANGE_WINDOW : i]
        if len(win) < RANGE_WINDOW: continue

        hi   = max(x['high'] for x in win)
        lo   = min(x['low']  for x in win)
        rng  = hi - lo
        rp   = rng / b['close'] * 100.0
        if rp < RANGE_MIN_PCT or rp > RANGE_MAX_PCT: continue

        deltas = [x.get('bar_delta') for x in win]
        if any(d is None for d in deltas): continue

        range_cvd = sum(deltas)
        if range_cvd <= 0: continue

        pre_cvd = sum(deltas[-PRE_CVD_BARS:])
        if pre_cvd >= 0: continue
        if abs(pre_cvd) / range_cvd < CVD_FLIP_RATIO_MIN: continue

        close = b['close']
        if close >= lo: continue

        # VR auto-calculado con ventana 50 (igual que BuyerExhaustionConfig.vr_window)
        vol_hist = [bars[j]['volume'] for j in range(max(0, i - VR_WINDOW), i)]
        avg_vol  = sum(vol_hist) / len(vol_hist) if vol_hist else 1.0
        vr       = b['volume'] / avg_vol if avg_vol > 0 else 0.0
        if vr < VR_MIN: continue

        bd = b.get('bar_delta') or 0
        if bd >= 0: continue

        bh, bl, bo = b['high'], b['low'], b['open']
        bar_rng = bh - bl
        if bar_rng > 0:
            cl_loc     = (close - bl) / bar_rng
            bear_body  = abs(close - bo) / bar_rng
            upper_wick = (bh - max(bo, close)) / bar_rng
            if cl_loc    > CLOSE_LOC_MAX:  continue
            if bear_body < BEAR_BODY_MIN:  continue
            if upper_wick > UPPER_WICK_MAX: continue

        stop_p = hi
        target = lo - rng
        risk   = stop_p - close
        if risk < 1e-8: continue
        if (close - target) / risk < 0.40: continue

        sim = bars[i + 1 : i + 1 + TIME_STOP_BARS + 10]
        r, reason, dur, exit_ms = simulate(sim, close, stop_p, target)

        pnl    = round(r * RISK_USD, 2)
        equity = round(equity + pnl, 2)
        closed = datetime.fromtimestamp(exit_ms / 1000, tz=timezone.utc).isoformat() if exit_ms else None

        if reason == 'TIME_STOP' and dur <= len(sim):
            exit_p = round(sim[dur - 1]['close'], 6)
        elif reason == 'TARGET':
            exit_p = round(target, 6)
        else:
            exit_p = round(stop_p, 6)

        trades.append({
            'idx': idx_off + len(trades) + 1,
            'id':  f'be-{sym}-{b["ts_ms"]}',
            'sym': sym, 'dir': 'Short', 'session': ses, 'score': None,
            'entry':  round(close,  6), 'stop':   round(stop_p, 6),
            'target': round(target, 6), 'exit':   exit_p,
            'resultR':    round(r, 4),
            'pnlUsd':     pnl,
            'riskUsd':    RISK_USD,
            'stopPct':    round(risk / close * 100, 3),
            'equity':     equity,
            'reason':     reason,
            'tsMs':       b['ts_ms'],
            'ts':         b['ts_ms'] // 1000,
            'closedAt':   closed,
            'durationMin': dur,
            'rangePct':   round(rp, 4),
            'rangeBars':  RANGE_WINDOW,
            'cvdInRange': round(range_cvd, 2),
            'vr':         round(vr, 2),
            'cvdSlope':   None, 'obi': None, 'dz': None,
            'priceVsVwap': None, 'funding': None,
            'isOpen': reason == 'DATA_END',
            'regime': '', 'sessionPhase': '', 'evidence': [],
            'confluenceFlags': [], 'vetoReason': '', 'rangeTouch': None,
        })
        last_sig = i

    return trades


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=14)
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print(json.dumps({'error': 'SUPABASE_URL / SUPABASE_KEY no configurados'}))
        sys.exit(1)

    now_ms       = int(time.time() * 1000)
    start_ms     = int((time.time() - args.days * 86400) * 1000)
    sb_first     = sb_earliest_ms()

    all_trades  = []
    first_bar   = None

    for sym, table in TABLES.items():
        bars = load_bars(sym, table, start_ms, sb_first)
        if len(bars) < COOLDOWN_BARS + 10:
            continue
        if bars and (first_bar is None or bars[0]['ts_ms'] < first_bar):
            first_bar = bars[0]['ts_ms']
        ts = detect(sym, bars, len(all_trades), CAPITAL)
        all_trades.extend(ts)

    all_trades.sort(key=lambda t: t['tsMs'])
    eq = CAPITAL
    for i, t in enumerate(all_trades):
        t['idx']    = i + 1
        eq          = round(eq + t['pnlUsd'], 2)
        t['equity'] = eq

    wins        = sum(1 for t in all_trades if t['resultR'] > 0)
    n           = len(all_trades)
    actual_days = args.days
    if first_bar is not None:
        actual_days = max(1, round((now_ms - first_bar) / 86_400_000, 1))

    print(json.dumps({
        'trades':      all_trades,
        'capital':     CAPITAL,
        'risk_usd':    RISK_USD,
        'days':        args.days,
        'actual_days': actual_days,
        'micro_start': None,
        'n':           n,
        'wins':        wins,
        'equity':      eq,
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
