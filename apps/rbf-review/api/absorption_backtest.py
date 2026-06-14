#!/usr/bin/env python3
"""
Backtest Absorption Long — compounding 2% del capital por trade.
Condiciones (mineria retrospectiva 9d, BTC-dominante):
  wick_atr < 0.5    absorcion silenciosa
  bar_delta < 0     presion vendedora
  obi_l5 > 0.2      bid activo absorbiendo
  vwap_dev < -0.3%  precio en zona de valor bajo VWAP
  cvd_slope < -5    CVD cayendo (flujo vendedor agotandose)
Entrada: open de barra i+1 (sin lookahead)
Stop:    wick_low - 0.15*ATR
Target:  2R desde entry | Trail 1.90R
Uso: python api/absorption_backtest.py --days 14
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

CAPITAL_INIT  = 500.0
RISK_PCT      = 0.02
RR            = 2.0
TRAIL_R       = 1.90
TRAIL_ATR_K   = 1.2
COOLDOWN      = 60
FORWARD_BARS  = 120
SESSIONS_ALL  = {'London','LondonNyOverlap','NewYork','Asia','OffHours'}

SYMBOLS  = ['BTCUSDT','BNBUSDT','SOLUSDT','XRPUSDT','ETHUSDT']
TABLES   = {'BTCUSDT':'btc_bars','BNBUSDT':'bnb_bars','SOLUSDT':'sol_bars',
            'XRPUSDT':'xrp_bars','ETHUSDT':'eth_bars'}
MIN_USD  = {'BTCUSDT':15.0,'BNBUSDT':0.5,'SOLUSDT':0.08,'XRPUSDT':0.005,'ETHUSDT':5.0}
TICK_SZ  = {'BTCUSDT':0.1,'BNBUSDT':0.01,'SOLUSDT':0.01,'XRPUSDT':0.0001,'ETHUSDT':0.01}

BAR_COLS = ('ts_ms,open,high,low,close,bar_delta,vr,atr,session,'
            'cvd_slope,obi_l5,vwap,regime,oi_momentum,dz,vpin,bid_wall,stacked_imb,thin_above')

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

def sb_first_micro_ms():
    earliest = None
    for table in TABLES.values():
        qs = urllib.parse.urlencode({'select':'ts_ms','cvd_slope':'not.is.null',
                                     'order':'ts_ms.asc','limit':'1'})
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

def simulate_long(bars, i, entry, stop, target, atr0):
    risk = entry - stop
    if risk <= 0: return -1.0, 'BAD_RISK', 0, 0, entry
    trail_stop = stop; best_high = entry; trailing = False
    for k in range(1, min(FORWARD_BARS, len(bars) - i)):
        b = bars[i + k]
        h, l = b['high'], b['low']
        atr = b.get('atr') or atr0
        if h > best_high: best_high = h
        if not trailing and (best_high - entry) / risk >= TRAIL_R:
            trailing = True
            floor = entry + TRAIL_R * risk
            if floor > trail_stop: trail_stop = floor
        if trailing and atr > 0:
            cand = best_high - TRAIL_ATR_K * atr
            if cand > trail_stop: trail_stop = cand
        if l <= trail_stop:
            reason = 'TRAILING_STOP' if trailing else 'STOP_LOSS'
            return round((trail_stop - entry) / risk, 4), reason, k, b['ts_ms'], trail_stop
        if h >= target:
            return round((target - entry) / risk, 4), 'TAKE_PROFIT', k, b['ts_ms'], target
    last = bars[min(i + FORWARD_BARS - 1, len(bars) - 1)]
    exit_p = last['close']
    return round((exit_p - entry) / risk, 4), 'DATA_END', FORWARD_BARS, last['ts_ms'], exit_p

def detect_signals(sym, bars):
    signals = []
    n = len(bars)
    last_sig = -COOLDOWN
    for i in range(60, n - 2):
        b = bars[i]
        if b.get('cvd_slope') is None: continue
        atr = b.get('atr') or 0
        if atr <= 0: continue
        ses = b.get('session') or ''
        if ses not in SESSIONS_ALL: continue
        if i - last_sig < COOLDOWN: continue

        close = b['close']; low = b['low']
        wick  = close - low
        if wick < MIN_USD.get(sym, 0): continue
        wick_atr = wick / atr
        vwap = b.get('vwap') or 0
        if vwap <= 0: continue
        vwap_dev = (close - vwap) / vwap * 100

        bd  = b.get('bar_delta')  or 0
        obi = b.get('obi_l5')     or 0
        cvd = b.get('cvd_slope')  or 0

        if wick_atr >= 0.5:  continue
        if bd >= 0:          continue
        if obi <= 0.2:       continue
        if vwap_dev >= -0.3: continue
        if cvd >= -5:        continue

        entry  = bars[i + 1]['open']
        stop_p = low - atr * 0.15
        risk   = entry - stop_p
        if risk <= 1e-6: continue
        if risk / entry > 0.006: continue

        signals.append({
            'ts_ms': b['ts_ms'], 'sym': sym, 'i': i, 'bars': bars,
            'entry': entry, 'stop': stop_p, 'atr': atr, 'ses': ses, 'b': b, 'wick': wick,
        })
        last_sig = i
    return signals

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=14)
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print(json.dumps({'error': 'SUPABASE_URL / SUPABASE_KEY no configurados'}))
        sys.exit(1)

    micro_start_ms  = sb_first_micro_ms()
    manual_start    = int((time.time() - args.days * 86400) * 1000)
    start_ms        = max(manual_start, micro_start_ms) if micro_start_ms else manual_start
    micro_start_iso = (datetime.fromtimestamp(micro_start_ms / 1000, tz=timezone.utc)
                       .strftime('%Y-%m-%d %H:%M') if micro_start_ms else 'unknown')

    # Recopilar senales de todos los simbolos
    all_signals = []
    first_bar_ms = None
    for sym in SYMBOLS:
        table = TABLES[sym]
        bars  = sb_fetch(table, start_ms)
        if len(bars) < COOLDOWN + 10: continue
        if bars:
            ms = bars[0]['ts_ms']
            if first_bar_ms is None or ms < first_bar_ms:
                first_bar_ms = ms
        sigs = detect_signals(sym, bars)
        all_signals.extend(sigs)

    # Ordenar cronologicamente para compounding real
    all_signals.sort(key=lambda s: s['ts_ms'])

    trades  = []
    equity  = CAPITAL_INIT

    for sig in all_signals:
        sym    = sig['sym']
        i      = sig['i']
        bars   = sig['bars']
        entry  = sig['entry']
        stop_p = sig['stop']
        atr    = sig['atr']
        ses    = sig['ses']
        b      = sig['b']
        wick   = sig['wick']

        risk_pts = entry - stop_p
        target_p = entry + RR * risk_pts
        risk_usd = equity * RISK_PCT

        r, reason, dur, exit_ms, exit_price = simulate_long(bars, i + 1, entry, stop_p, target_p, atr)

        pnl    = r * risk_usd
        equity = round(equity + pnl, 4)

        tick      = TICK_SZ.get(sym, 0.01)
        move_pts  = exit_price - entry
        move_ticks= round(move_pts / tick)
        risk_ticks= round(risk_pts / tick)
        tgt_ticks = round((target_p - entry) / tick)

        ts_iso   = datetime.fromtimestamp(b['ts_ms'] / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')
        exit_iso = (datetime.fromtimestamp(exit_ms / 1000, tz=timezone.utc).isoformat()
                    if exit_ms else None)
        vwap_v   = b.get('vwap') or 0
        score    = sum([
            1 if (b.get('stacked_imb') or '') in ('Bullish','FBG-Bullish') else 0,
            1 if b.get('bid_wall') else 0,
            1 if b.get('thin_above') else 0,
            1 if (b.get('oi_momentum') is False) else 0,
        ])

        trades.append({
            'idx':          len(trades) + 1,
            'id':           f'abs-{sym}-{b["ts_ms"]}',
            'sym':          sym,
            'dir':          'Long',
            'session':      ses,
            'score':        score,
            'confluenceFlags': [],
            'entry':        round(entry, 6),
            'stop':         round(stop_p, 6),
            'target':       round(target_p, 6),
            'exit':         round(exit_price, 6),
            'resultR':      r,
            'pnlUsd':       round(pnl, 2),
            'riskUsd':      round(risk_usd, 2),
            'stopPct':      round(risk_pts / entry * 100, 3),
            'equity':       round(equity, 2),
            'reason':       reason,
            'tsMs':         b['ts_ms'],
            'ts':           b['ts_ms'] // 1000,
            'closedAt':     exit_iso,
            'durationMin':  dur,
            'moveTicks':    move_ticks,
            'riskTicks':    risk_ticks,
            'targetTicks':  tgt_ticks,
            'wickAtr':      round(wick / atr, 3),
            'obi':          round(b.get('obi_l5') or 0, 3),
            'cvdSlope':     round(b.get('cvd_slope') or 0, 2),
            'vwapDev':      round((entry - vwap_v) / vwap_v * 100, 3) if vwap_v > 0 else None,
            'dz':           b.get('dz') or 0,
            'regime':       b.get('regime') or '',
            'isOpen':       reason == 'DATA_END',
            'isSweepReclaim': False,
            'isPreBreakout':  False,
            # campos requeridos por Trade type React
            'sessionPhase': '', 'evidence': [], 'vetoReason': '',
            'funding': None, 'rangeTouch': None, 'htf': None,
            'rangePct': None, 'rangeBars': None, 'cvdInRange': None,
            'vr': round(b.get('vr') or 0, 2),
        })

    n       = len(trades)
    wins    = sum(1 for t in trades if t['resultR'] > 0)
    closed  = [t for t in trades if not t['isOpen']]
    total_r = sum(t['resultR'] for t in closed)

    actual_days = args.days
    if first_bar_ms:
        elapsed_ms  = int(time.time() * 1000) - first_bar_ms
        actual_days = max(1, round(elapsed_ms / 86_400_000, 1))

    # Stats tick
    win_ticks  = [t['moveTicks'] for t in trades if t['resultR'] > 0]
    loss_ticks = [t['moveTicks'] for t in trades if t['resultR'] <= 0]

    result = {
        'absorption_backtest': True,
        'trades':       trades,
        'capital':      CAPITAL_INIT,
        'risk_pct':     RISK_PCT,
        'days':         args.days,
        'actual_days':  actual_days,
        'micro_start':  micro_start_iso,
        'n':            n,
        'n_closed':     len(closed),
        'wins':         wins,
        'equity':       round(equity, 2),
        'total_r':      round(total_r, 2),
        'avg_r':        round(total_r / len(closed), 3) if closed else 0,
        'wr_pct':       round(wins / n * 100, 1) if n else 0,
        'avg_win_ticks':  round(sum(win_ticks)  / len(win_ticks),  0) if win_ticks  else 0,
        'avg_loss_ticks': round(sum(loss_ticks) / len(loss_ticks), 0) if loss_ticks else 0,
        'params': {
            'wick_atr_max':   0.5,
            'obi_min':        0.2,
            'vwap_dev_max':  -0.3,
            'cvd_slope_max': -5,
            'trail_r':        TRAIL_R,
            'risk_pct':       RISK_PCT,
            'cooldown':       COOLDOWN,
            'sessions':       list(SESSIONS_ALL),
            'symbols':        SYMBOLS,
        },
    }
    print(json.dumps(result, ensure_ascii=False))

    print(f'\n=== ABSORPTION LONG {actual_days}d ===', file=sys.stderr)
    print(f'n={n}  WR={result["wr_pct"]}%  TotR={round(total_r,2)}  Equity=${round(equity,2)}', file=sys.stderr)
    print(f'AvgWinTicks={result["avg_win_ticks"]}  AvgLossTicks={result["avg_loss_ticks"]}', file=sys.stderr)

if __name__ == '__main__':
    main()
