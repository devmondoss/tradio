#!/usr/bin/env python3
"""
Backtest Buyer Exhaustion — corre como subprocess desde Vite, imprime JSON a stdout.
Uso: python api/be_backtest_script.py --days 14

Lógica idéntica a strategies/buyer_exhaustion/src/detector.rs:
  - Rango 8 barras M1 con CVD neto > 0 (compradores atrapados)
  - Giro CVD: últimas 5 barras | pre_cvd | / range_cvd >= 0.40
  - Ruptura a la baja: VR >= 2.5, bar_delta < 0, close < range_low
  - Micro-confirmación: close_location <= 0.35, bear_body >= 0.35, upper_wick <= 0.30
  - Stop = range_high, target = range_low - (range_high - range_low)
  - Sessions: London + LondonNyOverlap
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

RANGE_WINDOW       = 8       # ventana fija (range_windows = [8])
RANGE_MIN_PCT      = 0.05    # (high-low)/price % mínimo
RANGE_MAX_PCT      = 0.50    # (high-low)/price % máximo
PRE_CVD_BARS       = 5       # últimas N barras para medir giro
CVD_FLIP_RATIO_MIN = 0.40    # |pre_cvd| / range_cvd >= ratio
VR_MIN             = 2.5     # VR de la barra de ruptura
CLOSE_LOC_MAX      = 0.35    # (close - low) / (high - low) <= 0.35
BEAR_BODY_MIN      = 0.35    # |close - open| / (high - low) >= 0.35
UPPER_WICK_MAX     = 0.30    # (high - max(o,c)) / (high - low) <= 0.30
TIME_STOP_BARS     = 30
COOLDOWN_BARS      = 60
SESSIONS_OK        = {'London', 'LondonNyOverlap'}
CAPITAL            = 500.0
RISK_USD           = CAPITAL * 0.02  # $10 fijo por trade

TABLES = {
    'BTCUSDT': 'btc_bars', 'ETHUSDT': 'eth_bars', 'BNBUSDT': 'bnb_bars',
    'SOLUSDT': 'sol_bars', 'XRPUSDT': 'xrp_bars',
}
BAR_COLS = 'ts_ms,open,high,low,close,volume,bar_delta,vr,atr,session,cvd_slope'

# ─── Supabase helpers ─────────────────────────────────────────────────────────

def sb_first_bar_ms():
    """Devuelve el ts_ms del primer bar con bar_delta NOT NULL (datos reales)."""
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

def sb_fetch(table, start_ms):
    rows, limit, offset = [], 1000, 0
    while True:
        qs = urllib.parse.urlencode({
            'select': BAR_COLS, 'ts_ms': f'gte.{start_ms}',
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

# ─── Simulación de trade ──────────────────────────────────────────────────────

def simulate(bars, entry, stop, target):
    for k, b in enumerate(bars):
        h, l = b['high'], b['low']
        if h >= stop:
            r = (entry - stop) / abs(stop - entry)
            return r, 'STOP', k + 1, b['ts_ms']
        if l <= target:
            r = (entry - target) / abs(stop - entry)
            return r, 'TARGET', k + 1, b['ts_ms']
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

        # Necesitamos bar_delta para calcular CVD del rango
        if b.get('bar_delta') is None: continue
        if b.get('vr') is None: continue
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
        # Condición clave: compradores atrapados (CVD positivo en rango)
        if range_cvd <= 0: continue

        # Giro de CVD en últimas N barras del rango
        pre_deltas = deltas[-PRE_CVD_BARS:]
        pre_cvd    = sum(pre_deltas)
        if pre_cvd >= 0: continue  # giro debe ser negativo
        if abs(pre_cvd) / range_cvd < CVD_FLIP_RATIO_MIN: continue

        # Confirmación del breakout
        close = b['close']
        if close >= lo: continue  # debe cerrar por debajo del rango

        vr = b.get('vr') or 0
        if vr < VR_MIN: continue

        bd = b.get('bar_delta') or 0
        if bd >= 0: continue  # delta negativo obligatorio

        # Micro-confirmación de la barra de ruptura
        bh, bl, bo = b['high'], b['low'], b['open']
        bar_rng = bh - bl
        if bar_rng > 0:
            cl_loc    = (close - bl) / bar_rng
            bear_body = abs(close - bo) / bar_rng
            upper_wick = (bh - max(bo, close)) / bar_rng
            if cl_loc    > CLOSE_LOC_MAX:  continue
            if bear_body < BEAR_BODY_MIN:  continue
            if upper_wick > UPPER_WICK_MAX: continue

        stop_p  = hi
        target  = lo - rng
        risk    = stop_p - close
        if risk < 1e-8: continue
        rr = (close - target) / risk
        if rr < 0.40: continue

        sim = bars[i + 1 : i + 1 + TIME_STOP_BARS + 10]
        r, reason, dur, exit_ms = simulate(sim, close, stop_p, target)

        pnl     = round(r * RISK_USD, 2)
        equity  = round(equity + pnl, 2)
        closed  = datetime.fromtimestamp(exit_ms / 1000, tz=timezone.utc).isoformat() if exit_ms else None

        if reason == 'TIME_STOP' and dur <= len(sim):
            exit_p = round(sim[dur - 1]['close'], 6)
        elif reason == 'TARGET':
            exit_p = round(target, 6)
        else:
            exit_p = round(stop_p, 6)

        trades.append({
            'idx':           idx_off + len(trades) + 1,
            'id':            f'be-{sym}-{b["ts_ms"]}',
            'sym':           sym,
            'dir':           'Short',
            'session':       ses,
            'score':         None,
            'entry':         round(close, 6),
            'stop':          round(stop_p, 6),
            'target':        round(target, 6),
            'exit':          exit_p,
            'resultR':       round(r, 4),
            'pnlUsd':        pnl,
            'riskUsd':       RISK_USD,
            'stopPct':       round(risk / close * 100, 3),
            'equity':        equity,
            'reason':        reason,
            'tsMs':          b['ts_ms'],
            'ts':            b['ts_ms'] // 1000,
            'closedAt':      closed,
            'durationMin':   dur,
            'rangePct':      round(rp, 4),
            'rangeBars':     RANGE_WINDOW,
            'cvdInRange':    round(range_cvd, 2),
            'vr':            round(vr, 2),
            'cvdSlope':      b.get('cvd_slope'),
            'obi':           None,
            'dz':            None,
            'priceVsVwap':   None,
            'funding':       None,
            'isOpen':        reason == 'DATA_END',
            # requeridos por Trade type React
            'regime': '', 'sessionPhase': '', 'evidence': [], 'confluenceFlags': [],
            'vetoReason': '', 'rangeTouch': None,
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

    first_real_ms  = sb_first_bar_ms()
    manual_start   = int((time.time() - args.days * 86400) * 1000)
    start_ms       = max(manual_start, first_real_ms) if first_real_ms else manual_start

    all_trades    = []
    first_bar_ms  = None

    for sym, table in TABLES.items():
        bars = sb_fetch(table, start_ms)
        if len(bars) < COOLDOWN_BARS + 10:
            continue
        if bars and (first_bar_ms is None or bars[0]['ts_ms'] < first_bar_ms):
            first_bar_ms = bars[0]['ts_ms']
        ts = detect(sym, bars, len(all_trades), CAPITAL)
        all_trades.extend(ts)

    all_trades.sort(key=lambda t: t['tsMs'])
    eq = CAPITAL
    for i, t in enumerate(all_trades):
        t['idx']   = i + 1
        eq         = round(eq + t['pnlUsd'], 2)
        t['equity'] = eq

    wins       = sum(1 for t in all_trades if t['resultR'] > 0)
    n          = len(all_trades)
    actual_days = args.days
    if first_bar_ms is not None:
        elapsed_ms  = int(time.time() * 1000) - first_bar_ms
        actual_days = max(1, round(elapsed_ms / 86_400_000, 1))

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
