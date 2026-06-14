#!/usr/bin/env python3
"""
Backtest aislado para Sweep & Reclaim Long.
Corre independiente del backtest RBF (no comparte cooldown con shorts).

Uso:  python api/sweep_backtest.py --days 14
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

# ── CONFIG ──────────────────────────────────────────────────────────────────
CAPITAL          = 500.0
RISK_PCT         = 0.02
RISK_USD         = CAPITAL * RISK_PCT

RANGE_WINDOWS    = [15, 20, 30, 45, 60]
RANGE_MIN_PCT    = 0.08
RANGE_MAX_PCT    = 0.55
MIN_RANGE_ATR    = 1.5
COOLDOWN_BARS    = 60
SESSIONS_OK      = {'London', 'LondonNyOverlap', 'NewYork'}

# Sweep params (idénticos al detector Rust post-fix)
SWEEP_VR_MIN       = 1.5
SWEEP_MAX_RISK_PCT = 0.003   # (close - wick_low) / close <= 0.3%
RR_LONG            = 2.0
TRAIL_ACTIVATE_R   = 1.90
TRAIL_ATR_K        = 1.2
EXCLUDE_SYMS       = {'ETHUSDT', 'XRPUSDT'}

# Mínimo de riesgo en USD por símbolo — filtra wicks de ruido/spread
# BTC mueve ~$30-150 en M1 → wick real mínimo $15
# BNB mueve ~$0.5-2 en M1  → wick real mínimo $0.5
# SOL mueve ~$0.1-0.5 en M1 → wick real mínimo $0.08
SWEEP_MIN_RISK_USD = {
    'BTCUSDT': 15.0,
    'BNBUSDT':  0.5,
    'SOLUSDT':  0.08,
}

TABLES = {
    'BTCUSDT': 'btc_bars', 'ETHUSDT': 'eth_bars', 'BNBUSDT': 'bnb_bars',
    'SOLUSDT': 'sol_bars', 'XRPUSDT': 'xrp_bars',
}
BAR_COLS = 'ts_ms,open,high,low,close,volume,bar_delta,vr,atr,session,cvd_slope,obi_l5,vwap,regime,stacked_imb,absorption,thin_above,oi_momentum'

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
        qs = urllib.parse.urlencode({
            'select': 'ts_ms', 'cvd_slope': 'not.is.null',
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

def simulate_long(bars, entry, stop, target, atr):
    risk        = abs(stop - entry)
    trail_stop  = stop
    best_high   = entry
    trailing_on = False

    for k, b in enumerate(bars):
        h, l  = b['high'], b['low']
        atr_b = b.get('atr') or atr

        if h > best_high:
            best_high = h

        if not trailing_on and (best_high - entry) / risk >= TRAIL_ACTIVATE_R:
            trailing_on = True
            floor = entry + TRAIL_ACTIVATE_R * risk
            if floor > trail_stop:
                trail_stop = floor

        if trailing_on and atr_b > 0.0:
            candidate = best_high - TRAIL_ATR_K * atr_b
            if candidate > trail_stop:
                trail_stop = candidate

        if l <= trail_stop:
            reason = 'TRAILING_STOP' if trailing_on else 'STOP_LOSS'
            r = (trail_stop - entry) / risk
            return round(r, 4), reason, k + 1, b['ts_ms']

        if h >= target:
            r = (target - entry) / risk
            return round(r, 4), 'TAKE_PROFIT', k + 1, b['ts_ms']

    last_c = bars[-1]['close'] if bars else entry
    return round((last_c - entry) / risk, 4), 'DATA_END', len(bars), (bars[-1]['ts_ms'] if bars else 0)

def score_confluence_long(b, entry):
    """Scoring para long: flags bullish equivalentes al short."""
    score, flags = 0, []
    stk = b.get('stacked_imb') or ''
    if stk in ('Bullish', 'FBG-Bullish'):
        score += 1; flags.append('stacked_imbalance')
    abso = b.get('absorption') or ''
    if 'Bid' in abso or 'Bullish' in abso:
        score += 1; flags.append('absorption')
    if b.get('thin_above'):
        score += 1; flags.append('lvn_thin')
    vwap = b.get('vwap')
    if vwap and vwap > 0 and entry > vwap:
        score += 1; flags.append('vwap_bias')
    if b.get('oi_momentum') is True:
        score += 1; flags.append('oi_momentum')
    obi = b.get('obi_l5') or 0.0
    if obi < -0.15:
        score += 1; flags.append('obi_trap')
    return score, flags

def detect_sweep(sym, bars, idx_off, equity_start):
    trades   = []
    equity   = equity_start
    last_sig = -COOLDOWN_BARS
    n        = len(bars)
    # pending_sweep: estado de detección esperando confirmación en barra i+1
    # Estructura: {range_low, sweep_low, rw, rp, deltas, atr, b_detect, ses, vr, obi}
    pending  = None

    for i in range(COOLDOWN_BARS, n):
        b   = bars[i]
        ses = b.get('session') or ''
        atr = b.get('atr') or 0

        # ── Confirmación (Opción B): barra i es la barra de confirmación ────────
        # pending fue detectado en barra i-1; aquí verificamos que close > range_low
        # y entramos en bars[i+1].open (barra i+2 en términos de la detección)
        if pending is not None:
            conf_close = b['close']
            conf_low   = b['low']
            range_low_p = pending['range_low']
            # Cancelar si: el precio volvió a caer por debajo del range_low (no hubo hold)
            # o si ya no hay barra siguiente para entrar
            if conf_close <= range_low_p or conf_low < pending['sweep_low']:
                pending = None
            elif i + 1 < n:
                # Confirmación válida: entrada en open de barra i+1
                entry_bar = bars[i + 1]
                entry     = entry_bar['open']
                sweep_low = pending['sweep_low']
                # Stop: sweep_low con buffer ATR para no estar en el ruido
                atr_buf   = (pending['atr'] or atr) * 0.15
                stop_p    = sweep_low - atr_buf
                sweep_risk = entry - stop_p
                if sweep_risk <= 1e-6:
                    pending = None
                    continue
                # Rechazar si el riesgo resultante excede límite porcentual
                if sweep_risk / entry > SWEEP_MAX_RISK_PCT * 2:
                    pending = None
                    continue
                target = entry + RR_LONG * sweep_risk
                sim    = bars[i + 2:i + 2 + 300]
                r, reason, dur, exit_ms = simulate_long(sim, entry, stop_p, target, pending['atr'] or atr)

                equity = round(equity + r * RISK_USD, 2)
                bd     = pending['b_detect']
                sc, cf = score_confluence_long(bd, entry)
                closed_iso = datetime.fromtimestamp(exit_ms / 1000, tz=timezone.utc).isoformat() if exit_ms else None
                vwap_v = bd.get('vwap')
                exit_p = round(entry + r * sweep_risk, 6)

                trades.append({
                    'idx':            idx_off + len(trades) + 1,
                    'id':             f'sw-{sym}-{bd["ts_ms"]}',
                    'sym':            sym,
                    'dir':            'Long',
                    'session':        pending['ses'],
                    'score':          sc,
                    'confluenceFlags': cf,
                    'entry':          round(entry, 6),
                    'stop':           round(stop_p, 6),
                    'target':         round(target, 6),
                    'exit':           exit_p,
                    'resultR':        round(r, 4),
                    'pnlUsd':         round(r * RISK_USD, 2),
                    'riskUsd':        RISK_USD,
                    'stopPct':        round(sweep_risk / entry * 100, 3),
                    'equity':         round(equity, 2),
                    'reason':         reason,
                    'tsMs':           bd['ts_ms'],
                    'ts':             bd['ts_ms'] // 1000,
                    'closedAt':       closed_iso,
                    'durationMin':    dur,
                    'rangePct':       round(pending['rp'], 4),
                    'rangeBars':      pending['rw'],
                    'cvdInRange':     round(sum(d or 0 for d in pending['deltas']), 2),
                    'vr':             round(pending['vr'], 2),
                    'obi':            round(pending['obi'], 4),
                    'sweepRiskPct':   round(sweep_risk / entry * 100, 4),
                    'priceVsVwap':    round((entry - vwap_v) / vwap_v * 100, 3) if (vwap_v and vwap_v > 0) else None,
                    'isOpen':         reason == 'DATA_END',
                    'isSweepReclaim': True,
                    'isPreBreakout':  False,
                    'regime': bd.get('regime') or '', 'sessionPhase': '',
                    'evidence': [], 'vetoReason': '', 'funding': None,
                    'rangeTouch': None, 'htf': None,
                    'cvdSlope': bd.get('cvd_slope'), 'dz': None,
                })
                last_sig = i
                pending  = None
                continue
            else:
                pending = None

        if ses not in SESSIONS_OK: continue
        if atr <= 0: continue
        if b.get('cvd_slope') is None: continue
        if i - last_sig < COOLDOWN_BARS: continue

        close     = b['close']
        low       = b['low']
        vr        = b.get('vr') or 0
        bar_delta = b.get('bar_delta') or 0
        obi       = b.get('obi_l5') or 0

        if vr < SWEEP_VR_MIN: continue

        # ── DETECCIÓN: barra i es la barra del sweep ─────────────────────────
        # No entramos aquí — guardamos pending para confirmar en barra i+1
        for rw in RANGE_WINDOWS:
            if i < rw + 1: continue
            win = bars[i - rw:i]
            hi  = max(x['high'] for x in win)
            lo  = min(x['low']  for x in win)
            rng = hi - lo
            rp  = rng / close * 100.0
            if rp < RANGE_MIN_PCT or rp > RANGE_MAX_PCT: continue
            if rng < MIN_RANGE_ATR * atr: continue

            if not (low < lo and close > lo): continue
            if bar_delta >= 0: continue
            if obi <= 0: continue

            sweep_risk_detect = close - low
            if sweep_risk_detect <= 1e-6: continue
            if sweep_risk_detect / close > SWEEP_MAX_RISK_PCT: continue
            min_usd = SWEEP_MIN_RISK_USD.get(sym, 0)
            if sweep_risk_detect < min_usd: continue

            deltas = [x.get('bar_delta') for x in win]
            pending = {
                'range_low': lo,
                'sweep_low': low,
                'rw':        rw,
                'rp':        rp,
                'deltas':    deltas,
                'atr':       atr,
                'b_detect':  b,
                'ses':       ses,
                'vr':        vr,
                'obi':       obi,
            }
            break

    return trades

def stats_by(trades, key):
    groups = {}
    for t in trades:
        k = t.get(key) or 'unknown'
        groups.setdefault(k, []).append(t)
    out = {}
    for k, ts in sorted(groups.items()):
        wins = sum(1 for t in ts if t['resultR'] > 0)
        total_r = sum(t['resultR'] for t in ts)
        out[k] = {
            'n':      len(ts),
            'wins':   wins,
            'wr_pct': round(wins / len(ts) * 100, 1),
            'total_r': round(total_r, 2),
            'avg_r':   round(total_r / len(ts), 3),
        }
    return out

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=14)
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print(json.dumps({'error': 'SUPABASE_URL / SUPABASE_KEY no configurados'}))
        sys.exit(1)

    micro_start_ms = sb_first_micro_ms()
    manual_start   = int((time.time() - args.days * 86400) * 1000)
    start_ms       = max(manual_start, micro_start_ms) if micro_start_ms else manual_start
    micro_start_iso = datetime.fromtimestamp(micro_start_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M') if micro_start_ms else 'unknown'

    all_trades = []
    first_bar_ms = None

    for sym, table in TABLES.items():
        if sym in EXCLUDE_SYMS:
            continue
        bars = sb_fetch(table, start_ms)
        if len(bars) < COOLDOWN_BARS + 10:
            continue
        if bars:
            ms = bars[0]['ts_ms']
            if first_bar_ms is None or ms < first_bar_ms:
                first_bar_ms = ms
        ts = detect_sweep(sym, bars, len(all_trades), CAPITAL)
        all_trades.extend(ts)

    all_trades.sort(key=lambda t: t['tsMs'])
    eq = CAPITAL
    for i, t in enumerate(all_trades):
        t['idx'] = i + 1
        eq = round(eq + t['pnlUsd'], 2)
        t['equity'] = eq

    n      = len(all_trades)
    wins   = sum(1 for t in all_trades if t['resultR'] > 0)
    closed = [t for t in all_trades if not t['isOpen']]
    total_r = sum(t['resultR'] for t in closed)

    actual_days = args.days
    if first_bar_ms:
        elapsed_ms  = int(time.time() * 1000) - first_bar_ms
        actual_days = max(1, round(elapsed_ms / 86_400_000, 1))

    # Stats desglosadas
    by_sym     = stats_by(all_trades, 'sym')
    by_session = stats_by(all_trades, 'session')
    by_reason  = stats_by(all_trades, 'reason')

    # Salida a stdout (compatible con la app React si se integra)
    result = {
        'sweep_backtest': True,
        'trades':         all_trades,
        'capital':        CAPITAL,
        'risk_usd':       RISK_USD,
        'days':           args.days,
        'actual_days':    actual_days,
        'micro_start':    micro_start_iso,
        'n':              n,
        'n_closed':       len(closed),
        'wins':           wins,
        'equity':         eq,
        'total_r':        round(total_r, 2),
        'avg_r':          round(total_r / len(closed), 3) if closed else 0,
        'wr_pct':         round(wins / n * 100, 1) if n else 0,
        'by_sym':         by_sym,
        'by_session':     by_session,
        'by_reason':      by_reason,
        'params': {
            'sweep_vr_min':       SWEEP_VR_MIN,
            'sweep_max_risk_pct': SWEEP_MAX_RISK_PCT,
            'rr_long':            RR_LONG,
            'trail_activate_r':   TRAIL_ACTIVATE_R,
            'trail_atr_k':        TRAIL_ATR_K,
            'excluded_syms':      list(EXCLUDE_SYMS),
            'sessions':           list(SESSIONS_OK),
            'cooldown_bars':      COOLDOWN_BARS,
        },
    }
    print(json.dumps(result, ensure_ascii=False))

    # Resumen legible en stderr para debug rápido
    print(f'\n=== SWEEP & RECLAIM LONG — {actual_days}d ===', file=sys.stderr)
    print(f'Trades: {n}  Wins: {wins}  WR: {round(wins/n*100,1) if n else 0}%  TotalR: {round(total_r,2)}  AvgR: {round(total_r/len(closed),3) if closed else 0}  Equity: ${eq}', file=sys.stderr)
    print(f'\nPor símbolo:', file=sys.stderr)
    for sym, s in by_sym.items():
        print(f'  {sym:8s}  n={s["n"]}  WR={s["wr_pct"]}%  TotR={s["total_r"]}  AvgR={s["avg_r"]}', file=sys.stderr)
    print(f'\nPor sesión:', file=sys.stderr)
    for ses, s in by_session.items():
        print(f'  {ses:20s}  n={s["n"]}  WR={s["wr_pct"]}%  TotR={s["total_r"]}  AvgR={s["avg_r"]}', file=sys.stderr)
    print(f'\nPor exit reason:', file=sys.stderr)
    for reason, s in by_reason.items():
        print(f'  {reason:20s}  n={s["n"]}  AvgR={s["avg_r"]}  TotR={s["total_r"]}', file=sys.stderr)

if __name__ == '__main__':
    main()
