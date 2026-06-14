#!/usr/bin/env python3
"""
Long MTF+M1 Backtest
=====================
Capa 1 H4: precio > EMA20 H4 (bull/neutral). Sin H4 bear.
Capa 2 H1: señal de bottoming por orderflow (equal_low, oi_momentum, hammer, stacked_bull).
Capa 3 M1: entrada de precisión en pullback al low H1 (hammer M1, bid absorption).
Stop: H1_low - 0.3*ATR_H1. Target: 2.5R fijo.
Capital $500, risk 2% compounding. Fee 0.07% RT (maker+taker).

Patrones con edge (mineados 2026-06-14):
  ETH: stacked_bull+london, stacked_bull+ny, hammer+dz_buy, eq_low+london+exp, oi+ny
  SOL: hammer+london, stacked_bull+london, hammer+obi_pos, oi+london, eq_low+london
  BTC: sin edge suficiente (n<10 en todos los patrones)
  BNB: sin edge en longs (todos negativos)
  XRP: sin edge en longs (todos negativos)
"""
import json, os, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

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
FEE_RT        = 0.0007
COOLDOWN_M1   = 30       # barras M1 entre señales por símbolo
FORWARD_M1    = 1200     # 20h * 60
MIN_STOP_PCT  = 0.30
MAX_STOP_PCT  = 0.75
TARGET_R      = 2.5

CVD_FLIP_BARS  = 5
OBI_FLIP_THR   = 0.15   # para longs: OBI negativo (<-0.15) = sellers retomando
MIN_PROFIT_CVD = 1.0

SYMBOLS = ['ETHUSDT', 'SOLUSDT']  # BTC/BNB/XRP sin edge en longs
TABLES  = {'ETHUSDT': 'eth_bars', 'SOLUSDT': 'sol_bars'}
STARTS  = {'ETHUSDT': 1780756260000, 'SOLUSDT': 1780756260000}
TICK_SZ = {'ETHUSDT': 0.01, 'SOLUSDT': 0.001}

BAR_COLS = ('ts_ms,open,high,low,close,volume,atr,session,cvd_slope,obi_l5,'
            'vwap,vr,oi_momentum,bar_delta,regime,dz,absorption,'
            'stacked_imb,thin_below,equal_low,obi_fast')

def sb_fetch(table, start_ms):
    rows, limit, offset = [], 1000, 0
    while True:
        qs = urllib.parse.urlencode({'select': BAR_COLS, 'ts_ms': f'gte.{start_ms}',
             'order': 'ts_ms.asc', 'limit': str(limit), 'offset': str(offset)})
        req = urllib.request.Request(f'{SUPABASE_URL}/rest/v1/{table}?{qs}',
              headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'})
        chunk = json.loads(urllib.request.urlopen(req, timeout=30).read())
        rows.extend(chunk)
        if len(chunk) < limit: break
        offset += limit
    return rows

def ema(vals, p):
    k = 2/(p+1); r = [vals[0]]
    for v in vals[1:]: r.append(v*k+r[-1]*(1-k))
    return r

def atr_series(bars, period=14):
    trs = []
    for i, b in enumerate(bars):
        pc = bars[i-1]['close'] if i > 0 else b['open']
        trs.append(max(b['high']-b['low'], abs(b['high']-pc), abs(b['low']-pc)))
    atrs = [sum(trs[:period])/period]*period
    k = 1/period
    for i in range(period, len(trs)):
        atrs.append(atrs[-1]*(1-k)+trs[i]*k)
    return atrs

def build_h1(m1):
    buckets = defaultdict(list)
    for b in m1:
        buckets[(b['ts_ms']//3_600_000)*3_600_000].append(b)
    h1 = []
    for ts in sorted(buckets):
        bs = buckets[ts]
        h1.append({
            'ts_ms':  ts,
            'open':   bs[0]['open'],
            'high':   max(b['high'] for b in bs),
            'low':    min(b['low']  for b in bs),
            'close':  bs[-1]['close'],
        })
    return h1

def build_h4(m1):
    """H4 = bucket de 4h UTC."""
    buckets = defaultdict(list)
    for b in m1:
        h4_ts = (b['ts_ms'] // (4*3_600_000)) * (4*3_600_000)
        buckets[h4_ts].append(b)
    h4 = []
    for ts in sorted(buckets):
        bs = buckets[ts]
        h4.append({'ts_ms': ts, 'open': bs[0]['open'],
                   'high': max(b['high'] for b in bs),
                   'low':  min(b['low']  for b in bs),
                   'close': bs[-1]['close']})
    return h4

def h4_trend_series(m1):
    """Devuelve dict ts_ms_m1 -> trend H4 ('bull'/'bear'/'neutral')."""
    h4 = build_h4(m1)
    if len(h4) < 20:
        return {}
    closes = [b['close'] for b in h4]
    h4_ema = ema(closes, 20)
    h4_map = {}
    for i, b in enumerate(h4):
        c, e = b['close'], h4_ema[i]
        if c > e*1.005:    h4_map[b['ts_ms']] = 'bull'
        elif c < e*0.995:  h4_map[b['ts_ms']] = 'bear'
        else:              h4_map[b['ts_ms']] = 'neutral'
    result = {}
    for b in m1:
        h4_ts = (b['ts_ms'] // (4*3_600_000)) * (4*3_600_000)
        result[b['ts_ms']] = h4_map.get(h4_ts, 'unknown')
    return result

def detect_m1_signal_long(sym, m1, i):
    b    = m1[i]
    ses  = b.get('session', '')
    oi   = b.get('oi_momentum')
    obi  = b.get('obi_l5') or 0
    obif = b.get('obi_fast') or 0
    dz   = b.get('dz') or 0
    reg  = b.get('regime', '')
    abso = b.get('absorption', '')
    stk  = b.get('stacked_imb', '')
    eq   = b.get('equal_low')

    rng      = (b['high'] - b['low']) or 1
    body     = abs(b['close'] - b['open'])
    wick_lo  = min(b['close'], b['open']) - b['low']

    is_hammer    = (wick_lo/rng > 0.45) and (body/rng < 0.40)
    is_stk_bull  = stk == 'Bullish'
    is_london    = ses in ('London', 'LondonNyOverlap')
    is_ny        = ses == 'NewYork'
    is_exp       = reg == 'Expansion'
    obi_pos      = obi > 0.2
    dz_buy       = dz > 0.5
    oi_true      = oi is True or str(oi).lower() == 'true'
    eq_true      = eq is True or str(eq).lower() == 'true'
    abs_bid      = abso == 'Bid'

    if ses in ('OffHours', 'Asia'):
        return False, ''

    if sym == 'ETHUSDT':
        if is_stk_bull and is_london:               return True, 'eth:stacked_bull+london'
        if is_stk_bull and is_ny:                   return True, 'eth:stacked_bull+ny'
        if is_hammer and dz_buy:                    return True, 'eth:hammer+dz_buy'
        if eq_true and is_london and is_exp:        return True, 'eth:eq_low+london+exp'
        if oi_true and is_ny:                       return True, 'eth:oi+ny'

    elif sym == 'SOLUSDT':
        if is_hammer and is_london:                 return True, 'sol:hammer+london'
        if is_stk_bull and is_london:               return True, 'sol:stacked_bull+london'
        if is_hammer and obi_pos:                   return True, 'sol:hammer+obi_pos'
        if oi_true and is_london:                   return True, 'sol:oi+london'
        if eq_true and is_london:                   return True, 'sol:eq_low+london'

    return False, ''

def calc_confluence_long(b):
    """Confluence score alcista (espejo de shorts)."""
    score = 0
    flags = []
    if b.get('stacked_imb') == 'Bullish':
        score += 1; flags.append('stacked_bull')
    if str(b.get('thin_below') or '').lower() == 'false':
        score += 1; flags.append('no_thin_below')
    try:
        if float(b.get('bar_delta') or 0) > 50:
            score += 1; flags.append('delta_pos')
    except (ValueError, TypeError):
        pass
    try:
        if float(b.get('obi_l5') or 0) > 0.2:
            score += 1; flags.append('obi_pos')
    except (ValueError, TypeError):
        pass
    try:
        if float(b.get('dz') or 0) > 0.5:
            score += 1; flags.append('dz_buy')
    except (ValueError, TypeError):
        pass
    if b.get('oi_momentum') is True or str(b.get('oi_momentum')).lower() == 'true':
        score += 1; flags.append('oi_aligned')
    return score, flags

def simulate_long(m1, entry_i, entry, stop, risk, target_r):
    target_price   = entry + target_r * risk
    n              = len(m1)
    cvd_neg_streak = 0
    obi_neg_streak = 0

    for k in range(1, min(FORWARD_M1, n - entry_i)):
        mb   = m1[entry_i + k]
        h, l = mb['high'], mb['low']

        if h >= target_price:
            fee_r = FEE_RT * entry / risk
            return round(target_r - fee_r, 4), 'TAKE_PROFIT', k, mb['ts_ms'], target_price
        if l <= stop:
            fee_r = FEE_RT * entry / risk
            return round(-(1.0 + fee_r), 4), 'STOP_LOSS', k, mb['ts_ms'], stop

        curr_r    = (mb['close'] - entry) / risk
        cvd_slope = mb.get('cvd_slope') or 0
        obi_fast  = mb.get('obi_fast') or mb.get('obi_l5') or 0

        # CVD exhaustion para longs: sellers retomando (CVD < 0, OBI negativo)
        if cvd_slope < 0: cvd_neg_streak += 1
        else:             cvd_neg_streak  = 0
        if obi_fast < -OBI_FLIP_THR: obi_neg_streak += 1
        else:                         obi_neg_streak  = 0

        if (cvd_neg_streak >= CVD_FLIP_BARS
                and obi_neg_streak >= 1
                and curr_r >= MIN_PROFIT_CVD):
            exit_px = mb['close']
            fee_r   = FEE_RT * entry / risk
            return round((exit_px - entry) / risk - fee_r, 4), 'CVD_EXHAUSTION', k, mb['ts_ms'], exit_px

    last  = m1[min(entry_i + FORWARD_M1 - 1, n - 1)]
    fee_r = FEE_RT * entry / risk
    return round((last['close'] - entry) / risk - fee_r, 4), 'EXPIRED', FORWARD_M1, last['ts_ms'], last['close']

def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print(json.dumps({'error': 'SUPABASE credentials missing'})); sys.exit(1)

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=14)
    args = parser.parse_args()

    all_trades   = []
    equity       = CAPITAL_INIT
    first_bar_ms = None

    for sym in SYMBOLS:
        table    = TABLES[sym]
        start_ms = max(STARTS[sym], int((time.time()-args.days*86400)*1000))

        print(f'\n{"="*60}', file=sys.stderr)
        print(f'  {sym}', file=sys.stderr)
        print(f'{"="*60}', file=sys.stderr)

        m1 = sb_fetch(table, start_ms)
        if len(m1) < 200:
            print(f'  Insuficientes barras: {len(m1)}', file=sys.stderr)
            continue
        print(f'  {len(m1)} barras cargadas', file=sys.stderr)
        if not first_bar_ms or m1[0]['ts_ms'] < first_bar_ms:
            first_bar_ms = m1[0]['ts_ms']

        h1      = build_h1(m1)
        h1_atrs = atr_series(h1)
        h4_map  = h4_trend_series(m1)
        tick    = TICK_SZ.get(sym, 0.01)

        bull_bars = sum(1 for b in m1 if h4_map.get(b['ts_ms']) in ('bull', 'neutral'))
        bear_bars = sum(1 for b in m1 if h4_map.get(b['ts_ms']) == 'bear')
        print(f'  H4 bull/neutral: {bull_bars} ({100*bull_bars//(bull_bars+bear_bars)}%) | bear: {bear_bars}', file=sys.stderr)

        last_sig_i = -COOLDOWN_M1

        for i in range(10, len(m1) - FORWARD_M1 - 1):
            if i - last_sig_i < COOLDOWN_M1:
                continue

            b = m1[i]
            if not b.get('atr'):
                continue

            h4t = h4_map.get(b['ts_ms'], 'unknown')
            if h4t == 'bear':
                continue

            sig, sig_name = detect_m1_signal_long(sym, m1, i)
            if not sig:
                continue

            h1_ts = (b['ts_ms'] // 3_600_000) * 3_600_000
            h1_i  = next((j for j, hb in enumerate(h1) if hb['ts_ms'] == h1_ts), None)
            if h1_i is None:
                continue
            h1b    = h1[h1_i]
            h1_atr = h1_atrs[h1_i] if h1_i < len(h1_atrs) else 0
            if h1_atr <= 0:
                continue

            # Stop debajo del low H1 - 0.3*ATR (espejo del short)
            stop_price  = h1b['low'] - 0.3 * h1_atr
            entry_price = b['close']

            risk = entry_price - stop_price
            if risk <= 0:
                continue
            stop_pct = risk / entry_price * 100
            if stop_pct < MIN_STOP_PCT or stop_pct > MAX_STOP_PCT:
                continue

            conf_score, conf_flags = calc_confluence_long(b)
            target_price = entry_price + TARGET_R * risk

            net_r, reason, dur, exit_ms, exit_px = simulate_long(
                m1, i, entry_price, stop_price, risk, TARGET_R)

            risk_usd = equity * RISK_PCT
            pnl      = net_r * risk_usd
            equity   = round(equity + pnl, 4)

            fee_r    = round(FEE_RT * entry_price / risk, 4)
            exit_iso = datetime.fromtimestamp(exit_ms/1000, tz=timezone.utc).isoformat()

            trade = {
                'idx':        len(all_trades)+1,
                'id':         f'lng-{sym}-{b["ts_ms"]}',
                'sym':        sym,
                'dir':        'Long',
                'session':    b.get('session') or '',
                'sig':        sig_name,
                'h4_trend':   h4t,
                'entry':      round(entry_price, 6),
                'stop':       round(stop_price, 6),
                'target':     round(target_price, 6),
                'exit':       round(exit_px, 6),
                'resultR':    net_r,
                'grossR':     round(net_r + fee_r, 4),
                'feeR':       fee_r,
                'pnlUsd':     round(pnl, 2),
                'riskUsd':    round(risk_usd, 2),
                'stopPct':    round(stop_pct, 3),
                'targetR':    TARGET_R,
                'equity':     equity,
                'reason':     reason,
                'tsMs':       b['ts_ms'],
                'ts':         b['ts_ms'] // 1000,
                'closedAt':   exit_iso,
                'durationMin': dur,
                'moveTicks':  round((exit_px - entry_price) / tick),
                'riskTicks':  round(risk / tick),
                'isOpen':     False,
                'score':      conf_score,
                'confluenceFlags': conf_flags,
                'evidence':   [sig_name],
                'obi':        round(b.get('obi_l5') or 0, 3),
                'cvdSlope':   round(b.get('cvd_slope') or 0, 2),
                'dz':         b.get('dz') or 0,
                'regime':     b.get('regime') or '',
                'vr':         round(b.get('vr') or 0, 2),
                'oi_momentum': b.get('oi_momentum'),
            }
            all_trades.append(trade)
            last_sig_i = i

    # ── Stats ────────────────────────────────────────────────────────────────
    n_total = len(all_trades)
    closed  = [t for t in all_trades if not t['isOpen']]
    wins    = sum(1 for t in all_trades if t['resultR'] > 0)
    total_r = sum(t['resultR'] for t in closed)

    actual_days = args.days
    if first_bar_ms:
        elapsed_ms  = int(time.time()*1000) - first_bar_ms
        actual_days = max(1, round(elapsed_ms/86_400_000, 1))

    result = {
        'longs_mtf_backtest': True,
        'trades':      all_trades,
        'capital':     CAPITAL_INIT,
        'risk_pct':    RISK_PCT,
        'days':        args.days,
        'actual_days': actual_days,
        'n':           n_total,
        'n_closed':    len(closed),
        'wins':        wins,
        'equity':      round(equity, 2),
        'total_r':     round(total_r, 2),
        'avg_r':       round(total_r/len(closed), 3) if closed else 0,
        'wr_pct':      round(wins/n_total*100, 1) if n_total else 0,
        'params': {
            'symbols':      SYMBOLS,
            'min_stop_pct': MIN_STOP_PCT,
            'max_stop_pct': MAX_STOP_PCT,
            'target_r':     TARGET_R,
            'h4_filter':    'bull_neutral_only',
        },
    }

    # ── Human summary por símbolo ─────────────────────────────────────────────
    by_sym = defaultdict(list)
    for t in all_trades:
        by_sym[t['sym']].append(t)

    print('\n\n' + '='*60, file=sys.stderr)
    print('  RESUMEN GLOBAL', file=sys.stderr)
    print('='*60, file=sys.stderr)
    for s, ts in by_sym.items():
        wr  = sum(1 for t in ts if t['resultR'] > 0) / len(ts) * 100
        avg = sum(t['resultR'] for t in ts) / len(ts)
        ttl = sum(t['resultR'] for t in ts)
        print(f'  {s:10s}  n={len(ts):3d}  WR={wr:.1f}%  AvgR={avg:+.3f}R  TotalR={ttl:+.2f}R', file=sys.stderr)

    tw  = sum(1 for t in all_trades if t['resultR'] > 0)
    tr  = sum(t['resultR'] for t in all_trades)
    eq  = all_trades[-1]['equity'] if all_trades else CAPITAL_INIT
    print(f'\n  TOTAL  n={n_total}  WR={100*tw/n_total:.1f}%  AvgR={tr/n_total:+.3f}R  TotalR={tr:+.2f}R  Equity=${eq:.0f}', file=sys.stderr)

    print('\n  Por sesión:', file=sys.stderr)
    by_ses = defaultdict(list)
    for t in all_trades:
        by_ses[t['session']].append(t)
    for ses in sorted(by_ses, key=lambda s: -len(by_ses[s])):
        ts  = by_ses[ses]
        wr  = sum(1 for t in ts if t['resultR'] > 0) / len(ts) * 100
        avg = sum(t['resultR'] for t in ts) / len(ts)
        print(f'    {ses:20s}  n={len(ts):3d}  WR={wr:.1f}%  AvgR={avg:+.3f}R', file=sys.stderr)

    print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    main()
