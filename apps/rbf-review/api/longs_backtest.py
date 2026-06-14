#!/usr/bin/env python3
"""
Backtest Longs — patrones descubiertos por mineria libre.
BTC: decline>p75 + oi_momentum False
ETH: vwap_dev<p25 + sesion London
BNB: cvd>p90 + ses Overlap  (marginal)
SOL: ses Overlap + vr<p25   (marginal)
XRP: ses London + wick_hi>0.5ATR
Compounding 2% / trade. Cooldown 15 min. Stop estructural + trail 1.90R.
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

CAPITAL_INIT = 500.0
RISK_PCT     = 0.02
TRAIL_R      = 1.90
TRAIL_K      = 1.2
FORWARD      = 240
COOLDOWN     = 15
TP_R         = 2.5        # target subido de 2.0R a 2.5R
SLIPPAGE_PCT = 0.0005     # 0.05% slippage en entrada (market order M1)
FEE_TAKER    = 0.0005     # 0.05% taker por lado (VIP0 sin BNB)
FEE_BNB      = 0.00045    # 0.045% taker con descuento BNB
# Fee round-trip efectiva: entrada + salida (ambas taker en M1)
# Sin BNB: 0.10% total | Con BNB: 0.09% total
# Usamos sin BNB (conservador)
FEE_RT       = FEE_TAKER * 2  # 0.10% round-trip

SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'XRPUSDT']
TABLES  = {'BTCUSDT':'btc_bars','ETHUSDT':'eth_bars','BNBUSDT':'bnb_bars',
           'SOLUSDT':'sol_bars','XRPUSDT':'xrp_bars'}
TICK_SZ = {'BTCUSDT':0.1,'ETHUSDT':0.01,'BNBUSDT':0.01,'SOLUSDT':0.001,'XRPUSDT':0.0001}
NOISE_FLOOR = {'BTCUSDT':0.41,'ETHUSDT':0.45,'BNBUSDT':0.41,'SOLUSDT':0.65,'XRPUSDT':0.65}

PATTERNS = {
    'BTCUSDT': 'decline>p75 + oi_mom=False',
    'ETHUSDT': 'vwap_dev<p25 + London',
    'BNBUSDT': 'cvd>p90 + Overlap',
    'SOLUSDT': 'Overlap + vr<p25',
    'XRPUSDT': 'London + wick_hi>0.5ATR',
}

BAR_COLS = ('ts_ms,open,high,low,close,bar_delta,atr,session,'
            'cvd_slope,obi_l5,vwap,regime,dz,vpin,vr,'
            'bid_wall,stacked_imb,thin_above,oi_momentum')

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

def first_micro_ms(table):
    qs = urllib.parse.urlencode({'select':'ts_ms','cvd_slope':'not.is.null',
         'order':'ts_ms.asc','limit':'1'})
    req = urllib.request.Request(f'{SUPABASE_URL}/rest/v1/{table}?{qs}',
          headers={'apikey':SUPABASE_KEY,'Authorization':f'Bearer {SUPABASE_KEY}'})
    rows = json.loads(urllib.request.urlopen(req,timeout=15).read())
    return rows[0]['ts_ms'] if rows else None

def calc_th(bars):
    cvds = sorted(b.get('cvd_slope') or 0 for b in bars if b.get('cvd_slope') is not None)
    vds  = sorted((b['close']-(b.get('vwap') or b['close']))/(b.get('vwap') or b['close'])*100
                  for b in bars if b.get('vwap'))
    vrs  = sorted(b.get('vr') or 0 for b in bars)
    nc,nv,nr = len(cvds),len(vds),len(vrs)
    declines = []
    for i in range(30, min(2000, len(bars))):
        h = max(bars[j]['high'] for j in range(max(0,i-30),i+1))
        declines.append((h - bars[i]['close'])/h*100 if h else 0)
    ds = sorted(declines)
    return {
        'cvd_p90': cvds[int(nc*0.90)] if nc else 0,
        'vd_p25':  vds[int(nv*0.25)]  if nv else 0,
        'vr_p25':  vrs[int(nr*0.25)]  if nr else 0,
        'dec_p75': ds[int(len(ds)*0.75)] if ds else 0,
    }

def local_high_decline(bars, i, w=30):
    h = max(bars[j]['high'] for j in range(max(0,i-w),i+1))
    return (h - bars[i]['close'])/h*100 if h else 0

def swing_low(bars, i, w=10):
    return min(bars[j]['low'] for j in range(max(0,i-w),i+1))

def detect(sym, bars, i, th):
    b = bars[i]
    atr = b.get('atr') or 0
    ses = b.get('session') or ''
    if sym == 'BTCUSDT':
        if b.get('oi_momentum') is not False: return False
        return local_high_decline(bars, i, 30) > th['dec_p75']
    elif sym == 'ETHUSDT':
        if ses not in ('London','LondonNyOverlap'): return False
        vwap = b.get('vwap') or 0
        if vwap <= 0: return False
        return (b['close']-vwap)/vwap*100 < th['vd_p25']
    elif sym == 'BNBUSDT':
        if ses not in ('LondonNyOverlap',): return False
        return (b.get('cvd_slope') or 0) > th['cvd_p90']
    elif sym == 'SOLUSDT':
        if ses not in ('LondonNyOverlap',): return False
        return (b.get('vr') or 0) < th['vr_p25']
    elif sym == 'XRPUSDT':
        if ses not in ('London','LondonNyOverlap'): return False
        if atr <= 0: return False
        return (b['high']-b['close'])/atr > 0.5
    return False

def simulate(bars, start_i, entry_raw, stop, risk_raw):
    # Aplica slippage en entrada (peor precio real)
    entry  = entry_raw * (1 + SLIPPAGE_PCT)
    risk   = entry - stop
    if risk <= 0: return -1.0, 'BAD_RISK', 0, bars[start_i]['ts_ms'], entry

    trail_stop = stop; best = entry; trailing = False
    n = len(bars)
    target = entry + TP_R * risk
    for k in range(1, min(FORWARD, n-start_i)):
        b = bars[start_i+k]
        h, l = b['high'], b['low']
        atr = b.get('atr') or risk
        if h > best: best = h
        if not trailing and (best-entry)/risk >= TRAIL_R:
            trailing = True
            fl = entry + TRAIL_R*risk
            if fl > trail_stop: trail_stop = fl
        if trailing and atr > 0:
            c = best - TRAIL_K*atr
            if c > trail_stop: trail_stop = c
        if l <= trail_stop:
            reason = 'TRAILING_STOP' if trailing else 'STOP_LOSS'
            gross_r = (trail_stop - entry) / risk
            # Fee round-trip en % del notional, convertida a R
            fee_r = FEE_RT * entry / risk
            net_r = gross_r - fee_r
            return round(net_r, 4), reason, k, b['ts_ms'], trail_stop
        if h >= target:
            gross_r = TP_R
            fee_r = FEE_RT * entry / risk
            net_r = gross_r - fee_r
            return round(net_r, 4), 'TAKE_PROFIT', k, b['ts_ms'], target
    last = bars[min(start_i+FORWARD-1, n-1)]
    gross_r = (last['close'] - entry) / risk
    fee_r = FEE_RT * entry / risk
    return round(gross_r - fee_r, 4), 'DATA_END', FORWARD, last['ts_ms'], last['close']

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=14)
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print(json.dumps({'error': 'SUPABASE_URL / SUPABASE_KEY no configurados'}))
        sys.exit(1)

    all_trades = []
    equity     = CAPITAL_INIT
    first_bar_ms = None
    sym_results  = {}

    for sym in SYMBOLS:
        table = TABLES[sym]
        micro_ms = first_micro_ms(table)
        if not micro_ms: continue
        manual_ms = int((time.time() - args.days*86400)*1000)
        start_ms  = max(manual_ms, micro_ms)

        bars = sb_fetch(table, start_ms)
        if len(bars) < 100: continue
        if bars and (first_bar_ms is None or bars[0]['ts_ms'] < first_bar_ms):
            first_bar_ms = bars[0]['ts_ms']

        th = calc_th(bars)
        n  = len(bars)
        sym_trades = []
        last_i = -COOLDOWN

        for i in range(30, n - FORWARD - 2):
            if i - last_i < COOLDOWN: continue
            b = bars[i]
            if b.get('cvd_slope') is None: continue
            atr = b.get('atr') or 0
            if atr <= 0: continue
            if not detect(sym, bars, i, th): continue

            sl       = swing_low(bars, i, 10)
            stop     = sl - atr*0.15
            entry_raw = bars[i+1]['open'] if i+1 < n else b['close']
            risk_raw  = entry_raw - stop
            if risk_raw <= 0 or risk_raw/entry_raw > 0.03: continue

            # entry real con slippage (lo que usa simulate)
            entry = entry_raw * (1 + SLIPPAGE_PCT)
            risk  = entry - stop

            r, reason, dur, exit_ms, exit_p = simulate(bars, i+1, entry_raw, stop, risk_raw)

            risk_usd = equity * RISK_PCT
            pnl      = r * risk_usd
            equity   = round(equity + pnl, 4)

            # fee en R para mostrar en UI
            fee_r   = round(FEE_RT * entry / max(risk, 1e-9), 4)
            fee_usd = round(FEE_RT * entry_raw * (risk_usd / max(risk_raw, 1e-9)), 4)

            tick = TICK_SZ.get(sym, 0.01)
            b_ts = b['ts_ms']
            exit_iso = datetime.fromtimestamp(exit_ms/1000, tz=timezone.utc).isoformat() if exit_ms else None
            vwap_v = b.get('vwap') or 0

            trade = {
                'idx':        len(all_trades) + 1,
                'id':         f'lng-{sym}-{b_ts}',
                'sym':        sym,
                'dir':        'Long',
                'session':    b.get('session') or '',
                'score':      0,
                'confluenceFlags': [],
                'entry':      round(entry, 6),
                'stop':       round(stop, 6),
                'target':     round(entry + TP_R*risk, 6),
                'exit':       round(exit_p, 6),
                'resultR':    r,
                'pnlUsd':     round(pnl, 2),
                'riskUsd':    round(risk_usd, 2),
                'stopPct':    round(risk/entry*100, 3),
                'equity':     equity,
                'reason':     reason,
                'tsMs':       b_ts,
                'ts':         b_ts // 1000,
                'closedAt':   exit_iso,
                'durationMin': dur,
                'moveTicks':  round((exit_p-entry)/tick),
                'riskTicks':  round(risk/tick),
                'targetTicks':round(TP_R*risk/tick),
                'feeR':       fee_r,
                'feeUsd':     fee_usd,
                'slippagePct': round(SLIPPAGE_PCT*100, 3),
                'pattern':    PATTERNS[sym],
                'obi':        round(b.get('obi_l5') or 0, 3),
                'cvdSlope':   round(b.get('cvd_slope') or 0, 2),
                'vwapDev':    round((entry-vwap_v)/vwap_v*100, 3) if vwap_v else None,
                'dz':         b.get('dz') or 0,
                'regime':     b.get('regime') or '',
                'isOpen':     reason == 'DATA_END',
                'isSweepReclaim': False,
                'isPreBreakout':  False,
                'sessionPhase':'','evidence':[],'vetoReason':'',
                'funding':None,'rangeTouch':None,'htf':None,
                'rangePct':None,'rangeBars':None,'cvdInRange':None,
                'vr': round(b.get('vr') or 0, 2),
            }
            all_trades.append(trade)
            sym_trades.append(trade)
            last_i = i

        if sym_trades:
            closed = [t for t in sym_trades if not t['isOpen']]
            wins   = sum(1 for t in sym_trades if t['resultR'] > 0)
            tot_r  = sum(t['resultR'] for t in closed)
            sym_results[sym] = {
                'n': len(sym_trades), 'wins': wins,
                'wr': round(wins/len(sym_trades)*100, 1),
                'total_r': round(tot_r, 2),
                'avg_r': round(tot_r/len(closed), 3) if closed else 0,
                'pattern': PATTERNS[sym],
            }

    n_total = len(all_trades)
    closed  = [t for t in all_trades if not t['isOpen']]
    wins    = sum(1 for t in all_trades if t['resultR'] > 0)
    total_r = sum(t['resultR'] for t in closed)

    actual_days = args.days
    if first_bar_ms:
        elapsed_ms  = int(time.time()*1000) - first_bar_ms
        actual_days = max(1, round(elapsed_ms/86_400_000, 1))

    result = {
        'longs_backtest': True,
        'trades':       all_trades,
        'capital':      CAPITAL_INIT,
        'risk_pct':     RISK_PCT,
        'days':         args.days,
        'actual_days':  actual_days,
        'n':            n_total,
        'n_closed':     len(closed),
        'wins':         wins,
        'equity':       round(equity, 2),
        'total_r':      round(total_r, 2),
        'avg_r':        round(total_r/len(closed), 3) if closed else 0,
        'wr_pct':       round(wins/n_total*100, 1) if n_total else 0,
        'sym_results':  sym_results,
        'total_fees_usd': round(sum(t.get('feeUsd',0) for t in all_trades), 2),
        'params': {
            'cooldown':     COOLDOWN,
            'trail_r':      TRAIL_R,
            'tp_r':         TP_R,
            'risk_pct':     RISK_PCT,
            'fee_rt_pct':   round(FEE_RT*100, 3),
            'slippage_pct': round(SLIPPAGE_PCT*100, 3),
            'symbols':      SYMBOLS,
            'patterns':     PATTERNS,
        },
    }
    print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    main()
