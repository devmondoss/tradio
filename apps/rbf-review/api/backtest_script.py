#!/usr/bin/env python3
"""
Backtest RBF — corre como subprocess desde Vite, imprime JSON a stdout.
Uso: python api/backtest_script.py --days 14

Calibraciones activas (2026-06-10):
  - TRAIL_ACTIVATE_R_SHORT = 1.75  (antes 1.5 — calibrado con 72 trades live)
  - Modo pre-breakout: entry en range_low antes del VR>=3x, target=3R
  - expansion_bars_recent: NO simulable en backtest (tablas históricas no tienen columna regime)
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

RANGE_WINDOWS    = [15, 20, 30, 45, 60]
RANGE_MIN_PCT    = 0.08
RANGE_MAX_PCT    = 0.55
VR_MIN           = 3.0
MIN_RANGE_ATR    = 1.5
TRAIL_ATR_K      = 1.2
TIME_STOP_BARS   = 30
COOLDOWN_BARS    = 60
RR_SHORT         = 2.0
SESSIONS_OK      = {'London', 'LondonNyOverlap', 'NewYork'}
BREAKOUT_EXT_MIN = 0.001
VSWAP_MAX_DEV    = 0.003
CAPITAL          = 500.0
RISK_USD         = CAPITAL * 0.02  # $10 fijo por trade

# Calibración 2026-06-10: dirección-aware (antes ambos en 1.5)
TRAIL_ACTIVATE_R_SHORT = 1.75
TRAIL_ACTIVATE_R_LONG  = 1.5

# Pre-breakout: entra antes del VR>=3x, en el borde del rango
PRE_VR_MIN       = 1.5
PRE_ZONE_PCT     = 0.001   # close <= range_low * 1.001
PRE_RR           = 3.0
# Nota: oi_mom_bars_recent gate NO se puede simular en backtest (no hay OI en tablas históricas)
# En live el gate filtra a WR=58% — el backtest es sin ese filtro, por tanto más ruidoso.

TABLES = {
    'BTCUSDT': 'btc_bars', 'ETHUSDT': 'eth_bars', 'BNBUSDT': 'bnb_bars',
    'SOLUSDT': 'sol_bars', 'XRPUSDT': 'xrp_bars',
}
BAR_COLS = 'ts_ms,open,high,low,close,volume,bar_delta,vr,atr,session,cvd_slope,obi_l5,vwap,regime'

def sb_first_micro_ms():
    """Devuelve el ts_ms del primer bar con microestructura real (cvd_slope NOT NULL)."""
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

PRE_BREAKOUT_TIME_STOP_BARS = 15  # si en 15 min no rompió, tesis fallida

def simulate(bars, entry, stop, target, atr, trail_activate_r, is_pre=False):
    """Simula un trade Short desde entry hasta stop/target/time_stop."""
    time_stop = PRE_BREAKOUT_TIME_STOP_BARS if is_pre else TIME_STOP_BARS
    best, trailing, trail_stop = entry, False, stop
    for k, b in enumerate(bars):
        eff = trail_stop if trailing else stop
        h, l, c = b['high'], b['low'], b['close']
        if h >= eff:
            r = (entry - eff) / abs(stop - entry)
            return r, ('TRAILING_STOP' if trailing else 'STOP_LOSS'), k+1, b['ts_ms']
        if l <= target:
            r = (entry - target) / abs(stop - entry)
            return r, 'TAKE_PROFIT', k+1, b['ts_ms']
        if l < best: best = l
        if (entry - best) / abs(stop - entry) >= trail_activate_r and not trailing:
            trailing = True
        if trailing and atr > 0:
            cand = best + TRAIL_ATR_K * atr
            if cand < trail_stop: trail_stop = cand
        if k+1 >= time_stop and (entry - c) < 0:
            return (entry - c) / abs(stop - entry), 'TIME_STOP', k+1, b['ts_ms']
    return 0.0, 'DATA_END', len(bars), (bars[-1]['ts_ms'] if bars else 0)

def build_trade(sym, b, entry, stop_p, target, rr, rw, rp, deltas, sim_bars, reason, r, dur, exit_ms, equity, idx_off, count, is_pre):
    closed = datetime.fromtimestamp(exit_ms/1000, tz=timezone.utc).isoformat() if exit_ms else None
    vwap   = b.get('vwap')
    if reason == 'TIME_STOP' and dur <= len(sim_bars):
        exit_p = round(sim_bars[dur-1]['close'], 6)
    elif reason == 'TAKE_PROFIT':
        exit_p = round(target, 6)
    else:
        exit_p = round(stop_p, 6)
    pnl = round(r * RISK_USD, 2)
    return {
        'idx':            idx_off + count,
        'id':             f'bt-{sym}-{b["ts_ms"]}',
        'sym':            sym,
        'dir':            'Short',
        'session':        b.get('session') or '',
        'score':          None,
        'entry':          round(entry, 6),
        'stop':           round(stop_p, 6),
        'target':         round(target, 6),
        'exit':           exit_p,
        'resultR':        round(r, 4),
        'pnlUsd':         pnl,
        'riskUsd':        RISK_USD,
        'stopPct':        round(abs(stop_p - entry) / entry * 100, 3),
        'equity':         round(equity + pnl, 2),
        'reason':         reason,
        'tsMs':           b['ts_ms'],
        'ts':             b['ts_ms'] // 1000,
        'closedAt':       closed,
        'durationMin':    dur,
        'rangePct':       round(rp, 4),
        'rangeBars':      rw,
        'cvdInRange':     round(sum(deltas), 2) if all(d is not None for d in deltas) else None,
        'vr':             round(b.get('vr') or 0, 2),
        'cvdSlope':       b.get('cvd_slope'),
        'obi':            b.get('obi_l5'),
        'dz':             None,
        'priceVsVwap':    round((entry - vwap) / vwap * 100, 3) if (vwap and vwap > 0) else None,
        'isOpen':         reason == 'DATA_END',
        'isPreBreakout':  is_pre,
        # campos requeridos por Trade type en React
        'regime': '', 'sessionPhase': '', 'evidence': [], 'confluenceFlags': [],
        'vetoReason': '', 'funding': None, 'rangeTouch': None, 'htf': None,
    }

def detect(sym, bars, idx_off, equity_start):
    trades, equity, last_sig = [], equity_start, -COOLDOWN_BARS
    last_pre_sig = -COOLDOWN_BARS
    n = len(bars)
    for i in range(COOLDOWN_BARS, n - TIME_STOP_BARS):
        b   = bars[i]
        ses = b.get('session') or ''
        vr  = b.get('vr') or 0
        atr = b.get('atr') or 0
        if ses not in SESSIONS_OK: continue
        if atr <= 0: continue
        # Solo barras con microestructura real (monitor live)
        if b.get('cvd_slope') is None or b.get('vwap') is None: continue

        # expansion_bars_recent: cuenta barras Expansion en las 25 anteriores.
        # SOL/XRP: bypass (correlación invertida / muestra insuficiente).
        # BTC/ETH/BNB: filtro ≤3 calibrado (WR 40%→60%).
        if sym not in ('SOLUSDT', 'XRPUSDT'):
            exp_window = bars[max(0, i-25):i]
            exp_count  = sum(1 for x in exp_window if x.get('regime') == 'Expansion')
            if exp_count > 3: continue
        if i - last_sig < COOLDOWN_BARS: continue
        vwap = b.get('vwap')
        if vwap and vwap > 0 and (b['close'] - vwap) / vwap < -VSWAP_MAX_DEV: continue

        # cum_delta_25b gate por símbolo (calibración 2026-06-10):
        # BNB: wins avg -78, losses -1357 → rechazar si < -500
        # BTC: losses cum_delta = +377 → rechazar si > +200 (compradores agresivos = fakeout)
        delta_win = bars[max(0, i-25):i]
        cum_d25   = sum(x.get('bar_delta') or 0 for x in delta_win)
        if sym == 'BNBUSDT' and cum_d25 < -500: continue
        if sym == 'BTCUSDT' and cum_d25 > 200:  continue

        fired = False

        for rw in RANGE_WINDOWS:
            if i < rw + 1: continue
            win    = bars[i-rw:i]
            hi     = max(x['high'] for x in win)
            lo     = min(x['low']  for x in win)
            rng    = hi - lo
            rp     = rng / b['close'] * 100.0
            if rp < RANGE_MIN_PCT or rp > RANGE_MAX_PCT: continue
            if rng < MIN_RANGE_ATR * atr: continue
            deltas = [x.get('bar_delta') for x in win]
            cvd_ok = all(d is not None for d in deltas) and sum(deltas) < 0

            close  = b['close']

            # ── MODO POST-BREAKOUT (normal) ──────────────────────────────────
            if vr >= VR_MIN and close < lo:
                ext = (lo - close) / lo
                if ext < BREAKOUT_EXT_MIN: continue
                if not cvd_ok: continue
                entry  = close
                stop_p = hi
                target = entry - RR_SHORT * (stop_p - entry)
                sim    = bars[i+1:i+1+TIME_STOP_BARS+30]
                r, reason, dur, exit_ms = simulate(sim, entry, stop_p, target, atr, TRAIL_ACTIVATE_R_SHORT, is_pre=False)
                equity = round(equity + r * RISK_USD, 2)
                trades.append(build_trade(sym, b, entry, stop_p, target, RR_SHORT, rw, rp,
                                          deltas, sim, reason, r, dur, exit_ms, equity - r*RISK_USD,
                                          idx_off, len(trades)+1, False))
                last_sig = i
                fired = True
                break

            # ── MODO PRE-BREAKOUT (entrada anticipada) ────────────────────────
            # Nota: sin gate oi_mom_bars_recent (no hay OI en barras históricas)
            # → resultados más ruidosos que live donde el gate filtra a WR=58%
            if (not fired
                    and vr >= PRE_VR_MIN
                    and close <= lo * (1.0 + PRE_ZONE_PCT)
                    and cvd_ok
                    and i - last_pre_sig >= COOLDOWN_BARS):
                entry  = close
                stop_p = hi
                risk   = stop_p - entry
                if risk < 1e-6: continue
                target = entry - PRE_RR * risk
                if (entry - target) / risk < 1.5: continue
                sim    = bars[i+1:i+1+TIME_STOP_BARS+30]
                r, reason, dur, exit_ms = simulate(sim, entry, stop_p, target, atr, TRAIL_ACTIVATE_R_SHORT, is_pre=True)
                equity = round(equity + r * RISK_USD, 2)
                trades.append(build_trade(sym, b, entry, stop_p, target, PRE_RR, rw, rp,
                                          deltas, sim, reason, r, dur, exit_ms, equity - r*RISK_USD,
                                          idx_off, len(trades)+1, True))
                last_sig     = i
                last_pre_sig = i
                fired = True
                break

        if fired: continue
    return trades

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=14)
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print(json.dumps({'error': 'SUPABASE_URL / SUPABASE_KEY no configurados'}))
        sys.exit(1)

    # Auto-detectar inicio de microestructura real (primer bar con cvd_slope NOT NULL)
    micro_start_ms = sb_first_micro_ms()
    manual_start   = int((time.time() - args.days * 86400) * 1000)
    # Usar el más reciente: no retroceder antes de que tengamos datos reales
    start_ms = max(manual_start, micro_start_ms) if micro_start_ms else manual_start
    micro_start_iso = datetime.fromtimestamp(micro_start_ms/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M') if micro_start_ms else 'unknown'
    all_trades = []
    first_bar_ms = [None]

    for sym, table in TABLES.items():
        bars = sb_fetch(table, start_ms)
        if len(bars) < COOLDOWN_BARS + 10:
            continue
        if bars and (first_bar_ms[0] is None or bars[0]['ts_ms'] < first_bar_ms[0]):
            first_bar_ms[0] = bars[0]['ts_ms']
        ts = detect(sym, bars, len(all_trades), CAPITAL)
        all_trades.extend(ts)

    all_trades.sort(key=lambda t: t['tsMs'])
    eq = CAPITAL
    for i, t in enumerate(all_trades):
        t['idx'] = i + 1
        eq = round(eq + t['pnlUsd'], 2)
        t['equity'] = eq

    wins      = sum(1 for t in all_trades if t['resultR'] > 0)
    n         = len(all_trades)
    pre_trades = [t for t in all_trades if t.get('isPreBreakout')]
    pre_wins   = sum(1 for t in pre_trades if t['resultR'] > 0)

    actual_days = args.days
    if first_bar_ms[0] is not None:
        elapsed_ms  = int(time.time() * 1000) - first_bar_ms[0]
        actual_days = max(1, round(elapsed_ms / 86_400_000, 1))

    print(json.dumps({
        'trades':           all_trades,
        'capital':          CAPITAL,
        'risk_usd':         RISK_USD,
        'days':             args.days,
        'actual_days':      actual_days,
        'micro_start':      micro_start_iso,
        'n':                n,
        'wins':             wins,
        'equity':           eq,
        'n_pre':            len(pre_trades),
        'wins_pre':         pre_wins,
        'calibration_note': (
            'expansion_bars_recent filter NOT simulated (no regime col in historical bars). '
            'Pre-breakout oi_mom gate NOT simulated (no OI in historical bars). '
            'trailing Short = 1.75R (calibrated 2026-06-10).'
        ),
    }, ensure_ascii=False))

if __name__ == '__main__':
    main()
