#!/usr/bin/env python3
"""
RBF Long Backtest — Long Continuation + Sweep & Reclaim
Usa los mismos datos M1 de Supabase que backtest_script.py.

Setups implementados:
  1. CONTINUATION: close > range_high, VR 3-5x, CVD>0, precio>VWAP
  2. SWEEP_RECLAIM: wick < range_low, close vuelve >range_low, delta<0, obi>0
"""
import json, os, sys, urllib.request, urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).parent.parent
_env = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        _env[k.strip()] = v.strip().strip('"').strip("'")

SUPABASE_URL = _env.get('SUPABASE_URL', os.environ.get('SUPABASE_URL', ''))
SUPABASE_KEY = _env.get('SUPABASE_KEY', os.environ.get('SUPABASE_KEY', ''))

# ─── CONFIG (idéntico al script de shorts donde aplica) ───────────────────────
CAPITAL          = 500.0
RISK_PCT         = 0.02
RISK_USD         = CAPITAL * RISK_PCT
RANGE_WINDOWS    = [15, 20, 30, 45, 60]
RANGE_MIN_PCT    = 0.08
RANGE_MAX_PCT    = 0.55
VR_MIN           = 3.0
VR_MAX           = 5.0
MIN_RANGE_ATR    = 1.5
COOLDOWN_BARS    = 60
SESSIONS_OK      = {'London', 'LondonNyOverlap', 'NewYork'}
RR_LONG          = 2.0
TRAIL_ACTIVATE_R = 1.90
TRAIL_ATR_K      = 1.2
OBI_THRESHOLD    = 0.15

# Sweep & Reclaim: VR mínimo en la barra del sweep
SWEEP_VR_MIN     = 1.5
# Max riesgo permitido en sweep (close - wick_low) como % del precio
SWEEP_MAX_RISK_PCT = 0.003  # 0.3%

TABLES = {
    'BTCUSDT': 'btc_bars', 'ETHUSDT': 'eth_bars', 'BNBUSDT': 'bnb_bars',
    'SOLUSDT': 'sol_bars', 'XRPUSDT': 'xrp_bars'
}

BAR_COLS = (
    'ts_ms,open,high,low,close,volume,bar_delta,vr,atr,session,'
    'cvd_slope,obi_l5,vwap,regime,stacked_imb,absorption,thin_below,thin_above,oi_momentum'
)

# ─── SUPABASE ─────────────────────────────────────────────────────────────────

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
        if len(chunk) < limit:
            break
        offset += limit
    return rows

# ─── SIMULATE LONG ────────────────────────────────────────────────────────────

def simulate_long(bars, entry, stop_p, target, atr):
    """
    Simula long: stop_p < entry < target.
    Mirror del simulate() de shorts pero hacia arriba.
    Trail: best_high - ATR_K * ATR, activa en TRAIL_ACTIVATE_R.
    Lock floor: garantiza mínimo TRAIL_ACTIVATE_R cuando trail activa.
    """
    risk = entry - stop_p
    if risk < 1e-9:
        return 0.0, 'INVALID', 0, 0

    trail_stop  = stop_p
    best_high   = entry
    trailing_on = False

    for k, b in enumerate(bars):
        h, l   = b['high'], b['low']
        atr_b  = b.get('atr') or atr

        if h > best_high:
            best_high = h

        # Activar trailing al alcanzar TRAIL_ACTIVATE_R
        if not trailing_on and (best_high - entry) / risk >= TRAIL_ACTIVATE_R:
            trailing_on = True
            floor = entry + TRAIL_ACTIVATE_R * risk  # precio mínimo garantizado
            if floor > trail_stop:
                trail_stop = floor

        # Mover trailing hacia arriba (candidato sube con best_high)
        if trailing_on and atr_b > 0.0:
            candidate = best_high - TRAIL_ATR_K * atr_b
            if candidate > trail_stop:
                trail_stop = candidate

        # Stop hit: LOW toca o cae bajo trail_stop
        if l <= trail_stop:
            reason = 'TRAILING_STOP' if trailing_on else 'STOP_LOSS'
            r = (trail_stop - entry) / risk
            return round(r, 4), reason, k + 1, b['ts_ms']

        # Target hit
        if h >= target:
            r = (target - entry) / risk
            return round(r, 4), 'TAKE_PROFIT', k + 1, b['ts_ms']

    last_c = bars[-1]['close'] if bars else entry
    return round((last_c - entry) / risk, 4), 'DATA_END', len(bars), (bars[-1]['ts_ms'] if bars else 0)

# ─── SCORING (espejo de score_confluence con is_short=False) ──────────────────

def score_long(b, entry):
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

    obi = b.get('obi_l5') or 0
    if obi > OBI_THRESHOLD:
        score += 1; flags.append('obi_trap')

    return score, flags

# ─── DETECT ───────────────────────────────────────────────────────────────────

def detect_longs(sym, bars):
    trades = []
    last_sig = -COOLDOWN_BARS

    vol_window = 50
    def mean_vol(i):
        start = max(0, i - vol_window)
        vols = [bars[j].get('volume') or 0 for j in range(start, i)]
        return sum(vols) / len(vols) if vols else 1.0

    for i in range(1, len(bars)):
        b = bars[i]
        if b.get('cvd_slope') is None:
            continue
        ses = b.get('session') or ''
        if ses not in SESSIONS_OK:
            continue
        if i - last_sig < COOLDOWN_BARS:
            continue

        close = b['close']
        atr   = b.get('atr') or (close * 0.001)
        mv    = mean_vol(i)
        vr    = (b.get('volume') or 0) / mv if mv > 0 else 0.0
        vwap  = b.get('vwap')
        fired = False

        for rw in RANGE_WINDOWS:
            if i < rw + 1:
                continue
            win    = bars[i - rw:i]
            hi     = max(x['high'] for x in win)
            lo     = min(x['low']  for x in win)
            rng    = hi - lo
            rp     = rng / close * 100.0
            if rp < RANGE_MIN_PCT or rp > RANGE_MAX_PCT:
                continue
            if rng < MIN_RANGE_ATR * atr:
                continue

            deltas       = [x.get('bar_delta') for x in win]
            cvd_in_range = sum(d or 0 for d in deltas)
            pre5         = sum(d or 0 for d in deltas[-5:])

            # ── LONG CONTINUATION ─────────────────────────────────────────
            if VR_MIN <= vr <= VR_MAX and close > hi:
                # CVD del rango debe ser comprador
                if cvd_in_range <= 0:
                    continue
                # Últimas 5 barras del rango: si sellers dominaron, skip
                # (presión vendedora fresca = posible fakeout hacia arriba)
                if pre5 < 0:
                    continue
                # VWAP: precio debe estar por encima
                if vwap and vwap > 0 and close < vwap:
                    continue

                score, flags = score_long(b, close)
                if score == 4:
                    continue  # score=4 vetado igual que en shorts

                entry  = close
                stop_p = lo
                risk   = entry - stop_p
                if risk < 1e-6:
                    continue
                target = entry + RR_LONG * risk
                sim    = bars[i + 1: i + 1 + 300]
                if not sim:
                    continue
                r, reason, dur, _ = simulate_long(sim, entry, stop_p, target, atr)
                if reason == 'DATA_END':
                    continue
                trades.append({
                    'sym': sym, 'type': 'CONTINUATION', 'session': ses,
                    'ts': datetime.fromtimestamp(b['ts_ms'] / 1000, tz=timezone.utc).strftime('%m-%d %H:%M'),
                    'ts_ms': b['ts_ms'],
                    'vr': round(vr, 2), 'rangePct': round(rp, 3), 'rangeBars': rw,
                    'cvdInRange': round(cvd_in_range, 0),
                    'obi': round(b.get('obi_l5') or 0, 3),
                    'score': score, 'flags': flags,
                    'resultR': r, 'reason': reason, 'durationMin': dur,
                })
                last_sig = i
                fired = True
                break

            # ── SWEEP & RECLAIM ───────────────────────────────────────────
            if not fired and b['low'] < lo and close > lo:
                bar_delta = b.get('bar_delta') or 0
                obi       = b.get('obi_l5') or 0

                if bar_delta >= 0:
                    continue  # esta barra no tuvo selling real
                if obi <= 0:
                    continue  # sin soporte comprador en el libro
                if vr < SWEEP_VR_MIN:
                    continue  # volumen insuficiente en la vela sweep

                score, flags = score_long(b, close)
                if score == 4:
                    continue

                entry  = close
                stop_p = b['low']  # stop en el wick low
                risk   = entry - stop_p
                if risk < 1e-6:
                    continue
                if risk / entry > SWEEP_MAX_RISK_PCT:
                    continue  # wick demasiado largo = stop muy amplio
                target = entry + RR_LONG * risk
                sim    = bars[i + 1: i + 1 + 300]
                if not sim:
                    continue
                r, reason, dur, _ = simulate_long(sim, entry, stop_p, target, atr)
                if reason == 'DATA_END':
                    continue
                trades.append({
                    'sym': sym, 'type': 'SWEEP_RECLAIM', 'session': ses,
                    'ts': datetime.fromtimestamp(b['ts_ms'] / 1000, tz=timezone.utc).strftime('%m-%d %H:%M'),
                    'ts_ms': b['ts_ms'],
                    'vr': round(vr, 2), 'rangePct': round(rp, 3), 'rangeBars': rw,
                    'cvdInRange': round(cvd_in_range, 0),
                    'obi': round(obi, 3),
                    'score': score, 'flags': flags,
                    'resultR': r, 'reason': reason, 'durationMin': dur,
                })
                last_sig = i
                fired = True
                break

    return trades

# ─── REPORT ───────────────────────────────────────────────────────────────────

def report(trades):
    closed = [t for t in trades if t['reason'] != 'DATA_END']
    if not closed:
        print("  Sin trades cerrados.")
        return

    wins  = [t for t in closed if t['resultR'] > 0]
    tot_r = sum(t['resultR'] for t in closed)

    equity = CAPITAL
    for t in closed:
        equity = round(equity + t['resultR'] * RISK_USD, 2)

    print(f"  Trades cerrados: {len(closed)}")
    print(f"  Wins/Losses:     {len(wins)}/{len(closed)-len(wins)}")
    print(f"  Win Rate:        {len(wins)/len(closed)*100:.1f}%")
    print(f"  Total R:         {tot_r:+.2f}R")
    print(f"  Avg R/trade:     {tot_r/len(closed):+.3f}R")
    print(f"  Equity final:    ${equity:.2f} ({(equity-CAPITAL)/CAPITAL*100:+.1f}%)")
    print()

    for tipo in ['CONTINUATION', 'SWEEP_RECLAIM']:
        sub = [t for t in closed if t['type'] == tipo]
        if not sub:
            continue
        sw  = [t for t in sub if t['resultR'] > 0]
        tot = sum(t['resultR'] for t in sub)
        print(f"  {tipo:15}: n={len(sub):2d}  WR={len(sw)/len(sub)*100:.0f}%  TotR={tot:+.2f}R  AvgR={tot/len(sub):+.3f}R")
    print()

    print("  POR SESION:")
    for ses in ['London', 'LondonNyOverlap', 'NewYork']:
        sub = [t for t in closed if t['session'] == ses]
        if not sub: continue
        sw  = [t for t in sub if t['resultR'] > 0]
        tot = sum(t['resultR'] for t in sub)
        print(f"    {ses:20}: n={len(sub):2d}  WR={len(sw)/len(sub)*100:.0f}%  TotR={tot:+.2f}R  AvgR={tot/len(sub):+.3f}R")
    print()

    print("  POR SIMBOLO:")
    for sym in TABLES:
        sub = [t for t in closed if t['sym'] == sym]
        if not sub: continue
        sw  = [t for t in sub if t['resultR'] > 0]
        tot = sum(t['resultR'] for t in sub)
        print(f"    {sym:10}: n={len(sub):2d}  WR={len(sw)/len(sub)*100:.0f}%  TotR={tot:+.2f}R  AvgR={tot/len(sub):+.3f}R")
    print()

    print("  EXIT REASONS:")
    for reason in ['STOP_LOSS', 'TRAILING_STOP', 'TAKE_PROFIT']:
        sub = [t for t in closed if t['reason'] == reason]
        if not sub: continue
        avg = sum(t['resultR'] for t in sub) / len(sub)
        print(f"    {reason:16}: n={len(sub):2d}  avg={avg:+.3f}R")
    print()

    print(f"  {'#':>3} {'Fecha':12} {'Sym':8} {'Tipo':14} {'Ses':8} {'VR':>5} {'Rng%':>6} {'CVD':>8} {'OBI':>6} {'Sc':>3} {'Reason':14} {'R':>7}")
    print("  " + "-" * 97)
    for idx, t in enumerate(closed, 1):
        ses = {'LondonNyOverlap': 'Overlap', 'NewYork': 'NY'}.get(t['session'], t['session'])
        print(f"  {idx:>3} {t['ts']:12} {t['sym']:8} {t['type']:14} {ses:8} {t['vr']:>5.1f} "
              f"{t['rangePct']:>6.3f} {t['cvdInRange']:>8.0f} {t['obi']:>+6.3f} {t['score']:>3} "
              f"{t['reason']:14} {t['resultR']:>+7.3f}")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=60)
    args = ap.parse_args()

    micro_start = sb_first_micro_ms()
    if not micro_start:
        sys.exit("No se encontró micro_start (cvd_slope NOT NULL)")

    now_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = max(micro_start, now_ms - args.days * 86400 * 1000)

    dt_start = datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)
    dt_micro = datetime.fromtimestamp(micro_start / 1000, tz=timezone.utc)

    print("=== RBF LONG BACKTEST ===")
    print(f"  micro_start : {dt_micro.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  start usado : {dt_start.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  actual_days : {(now_ms - micro_start) / 86400000:.1f} dias desde micro_start")
    print()

    all_trades = []
    for sym, table in TABLES.items():
        print(f"  Cargando {sym}...", file=sys.stderr)
        bars   = sb_fetch(table, start_ms)
        trades = detect_longs(sym, bars)
        all_trades.extend(trades)
        print(f"  {sym}: {len(bars)} bars → {len(trades)} longs detectados", file=sys.stderr)

    all_trades.sort(key=lambda t: t['ts_ms'])
    report(all_trades)

if __name__ == '__main__':
    main()
