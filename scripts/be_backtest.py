#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Backtest Buyer Exhaustion v2 — microestructura extendida + sim capital.

Microestructura derivada de klines (sin endpoints extra):
  - close_location  = (close-low)/(high-low)  → <0.35 = barra bear fuerte
  - bear_body_ratio = |close-open|/(high-low)  → >0.40 = cuerpo dominante
  - upper_wick_ratio= (high-max(o,c))/(h-l)    → <0.25 = poca cola superior
  - cvd_slope_sign  = tendencia del CVD dentro del rango (¿declinando?)

bar_delta real: k[9] taker_buy_base - sell = 2*k[9] - k[5]

Uso:
    python scripts/be_backtest.py [--days 163] [--symbols BTCUSDT ...]
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import json, math, time, urllib.request, urllib.parse, csv, argparse, os
from collections import deque
from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────────────────────────
# Parámetros del detector (DEFAULT calibrado en backtest 90d)
# ─────────────────────────────────────────────────────────────────────────────
RANGE_WINDOWS_DEFAULT  = [8]          # ganador único en 90d
RANGE_MIN_PCT          = 0.0005
RANGE_MAX_PCT          = 0.0070       # relajado de 0.50→0.70% para más señales
CVD_FLIP_MIN_RATIO     = 0.40
PRE_CVD_BARS           = 5
BREAKOUT_VR_MIN        = 2.5
VR_WINDOW              = 50
TIME_STOP_BARS         = 30
MIN_RR                 = 0.40
SIGNAL_COOLDOWN_BARS   = 60
SESSIONS_DEFAULT       = {'London', 'LondonNyOverlap'}

# Nuevos filtros de microestructura (breakout bar)
CLOSE_LOCATION_MAX     = 0.35   # close en el 35% inferior del rango → barra bear fuerte
BEAR_BODY_MIN          = 0.35   # cuerpo bajista mínimo como % del rango
UPPER_WICK_MAX         = 0.30   # cola superior máxima

# ─────────────────────────────────────────────────────────────────────────────
# Clasificación de sesión
# ─────────────────────────────────────────────────────────────────────────────
def classify_session(ts_ms):
    h = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).hour
    if   3  <= h <  8:  return 'Asia'
    elif 8  <= h < 12:  return 'London'
    elif 12 <= h < 16:  return 'LondonNyOverlap'
    elif 16 <= h < 21:  return 'NewYork'
    else:               return 'OffHours'

# ─────────────────────────────────────────────────────────────────────────────
# Binance FAPI
# ─────────────────────────────────────────────────────────────────────────────
FAPI = 'https://fapi.binance.com'

def fetch_klines(symbol, interval, start_ms, end_ms, verbose=True):
    bars, cur = [], start_ms
    while cur < end_ms:
        qs = urllib.parse.urlencode({
            'symbol': symbol, 'interval': interval,
            'startTime': cur, 'endTime': end_ms, 'limit': 1500,
        })
        try:
            resp  = urllib.request.urlopen(f'{FAPI}/fapi/v1/klines?{qs}', timeout=30)
            chunk = json.loads(resp.read())
        except Exception as e:
            print(f'  [binance] error {symbol}: {e}'); break
        if not chunk: break
        for k in chunk:
            bv  = float(k[9])
            vol = float(k[5])
            o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
            rng = h - l
            bars.append({
                'ts_ms':     int(k[0]),
                'open': o, 'high': h, 'low': l, 'close': c,
                'volume':    vol,
                'bar_delta': 2 * bv - vol,
                # microestructura derivada
                'close_loc':   (c - l) / rng if rng > 0 else 0.5,
                'bear_body':   max(o - c, 0) / rng if rng > 0 else 0.0,
                'upper_wick':  (h - max(o, c)) / rng if rng > 0 else 0.0,
            })
        last = int(chunk[-1][0])
        if last <= cur: break
        cur = last + 60_000
        if len(chunk) < 1500: break
        time.sleep(0.05)
    if verbose:
        print(f'  {len(bars):,} barras descargadas')
    return bars

# ─────────────────────────────────────────────────────────────────────────────
# Detector BE
# ─────────────────────────────────────────────────────────────────────────────
class BEDetector:
    def __init__(self, windows=None, sessions=None,
                 vr_min=BREAKOUT_VR_MIN, flip_min=CVD_FLIP_MIN_RATIO,
                 close_loc_max=None, bear_body_min=None, upper_wick_max=None,
                 cooldown=SIGNAL_COOLDOWN_BARS, range_max_pct=RANGE_MAX_PCT):
        self.windows      = windows  or RANGE_WINDOWS_DEFAULT
        self.sessions     = sessions or SESSIONS_DEFAULT
        self.vr_min       = vr_min
        self.flip_min     = flip_min
        self.close_loc_max   = close_loc_max    # None = sin filtro
        self.bear_body_min   = bear_body_min    # None = sin filtro
        self.upper_wick_max  = upper_wick_max   # None = sin filtro
        self.cooldown        = cooldown
        self.range_max_pct   = range_max_pct
        self.history         = []
        self.vol_hist        = deque(maxlen=VR_WINDOW + 1)
        self.bars_seen       = 0
        self.last_sig_bar    = -9999

    def reset(self):
        self.history.clear(); self.vol_hist.clear()
        self.bars_seen = 0; self.last_sig_bar = -9999

    def reset_cooldown(self):
        self.last_sig_bar = -9999

    def on_bar(self, bar, session):
        self.history.append(bar)
        self.vol_hist.append(bar['volume'])
        self.bars_seen += 1

        warmup = max(self.windows) + PRE_CVD_BARS + VR_WINDOW + 5
        if self.bars_seen < warmup:                          return None
        if session not in self.sessions:                     return None
        if self.bars_seen - self.last_sig_bar < self.cooldown: return None

        cur    = bar
        vols   = list(self.vol_hist)[:-1]
        if not vols: return None
        slice_ = vols[-VR_WINDOW:] if len(vols) >= VR_WINDOW else vols
        avg_vol = sum(slice_) / len(slice_)
        if avg_vol < 1e-10: return None
        vr = cur['volume'] / avg_vol

        if vr < self.vr_min:            return None
        if cur['bar_delta'] >= 0:       return None

        # filtros de microestructura de la barra de ruptura
        if self.close_loc_max  is not None and cur['close_loc']  > self.close_loc_max:  return None
        if self.bear_body_min  is not None and cur['bear_body']   < self.bear_body_min:  return None
        if self.upper_wick_max is not None and cur['upper_wick']  > self.upper_wick_max: return None

        hist_len = len(self.history)
        for win in self.windows:
            win_start = hist_len - win - 1
            if win_start < 0: continue
            window = self.history[win_start : win_start + win]
            if len(window) < win: continue

            range_high = max(b['high'] for b in window)
            range_low  = min(b['low']  for b in window)
            range_h    = range_high - range_low
            if range_h < 1e-10: continue

            ref       = window[-1]['close']
            range_pct = range_h / ref
            if range_pct < RANGE_MIN_PCT or range_pct > self.range_max_pct: continue

            range_cvd = sum(b['bar_delta'] for b in window)
            if range_cvd <= 0: continue

            pre_start = win_start - PRE_CVD_BARS
            if pre_start < 0: continue
            pre_bars = self.history[pre_start : win_start]
            if len(pre_bars) < PRE_CVD_BARS: continue
            pre_cvd = sum(b['bar_delta'] for b in pre_bars)
            if pre_cvd >= 0: continue

            flip_ratio = abs(pre_cvd) / range_cvd
            if flip_ratio < self.flip_min: continue
            if cur['close'] >= range_low:  continue

            stop   = range_high
            target = range_low - range_h
            risk   = stop - cur['close']
            if risk < 1e-10: continue
            rr = (cur['close'] - target) / risk
            if rr < MIN_RR: continue

            self.last_sig_bar = self.bars_seen
            return {
                'ts_ms': cur['ts_ms'], 'entry_price': cur['close'],
                'stop_price': stop, 'target_price': target, 'rr': rr,
                'range_high': range_high, 'range_low': range_low,
                'range_pct': range_pct, 'range_bars': win,
                'range_cvd': range_cvd, 'pre_cvd_flip': pre_cvd,
                'cvd_flip_ratio': flip_ratio, 'vr_at_breakout': vr,
                'breakout_delta': cur['bar_delta'],
                'close_loc': round(cur['close_loc'], 3),
                'bear_body': round(cur['bear_body'], 3),
                'upper_wick': round(cur['upper_wick'], 3),
                'session': session,
            }
        return None

# ─────────────────────────────────────────────────────────────────────────────
# Simulación
# ─────────────────────────────────────────────────────────────────────────────
def simulate(sig, bars_after):
    entry  = sig['entry_price']
    stop   = sig['stop_price']
    target = sig['target_price']
    risk   = stop - entry
    if risk < 1e-10:
        return None, 'ZERO_RISK', 0, entry

    bars_held = 0
    for bar in bars_after[:600]:
        h, l, c = bar['high'], bar['low'], bar['close']
        bars_held += 1
        stop_hit   = h >= stop
        target_hit = l <= target
        if stop_hit and target_hit:
            return (entry - stop) / risk, 'STOP', bars_held, stop
        elif target_hit:
            return (entry - target) / risk, 'TARGET', bars_held, target
        elif stop_hit:
            return (entry - stop) / risk, 'STOP', bars_held, stop
        if bars_held >= TIME_STOP_BARS:
            pnl_now = (entry - c) / risk
            if pnl_now < 0:
                return pnl_now, 'TIME_STOP', bars_held, c

    return None, 'TIMEOUT', bars_held, bars_after[-1]['close'] if bars_after else entry

# ─────────────────────────────────────────────────────────────────────────────
# Capital simulation
# ─────────────────────────────────────────────────────────────────────────────
def capital_sim(signals, capital=500.0, risk_pct=0.02):
    """
    Simula equity curve con risk% fijo.
    Retorna lista de puntos + estadísticas.
    """
    balance = capital
    peak    = capital
    max_dd  = 0.0
    equity  = [capital]

    for s in sorted(signals, key=lambda x: x['ts_ms']):
        risk_usd  = balance * risk_pct
        pnl       = s['result_r'] * risk_usd
        balance  += pnl
        peak      = max(peak, balance)
        dd        = (peak - balance) / peak * 100
        max_dd    = max(max_dd, dd)
        equity.append(balance)
        s['balance_after'] = round(balance, 2)
        s['pnl_usd']       = round(pnl, 2)

    total_trades = len(signals)
    wins         = sum(1 for s in signals if s['result_r'] > 0)
    total_r      = sum(s['result_r'] for s in signals)

    return {
        'equity': equity,
        'final':  round(balance, 2),
        'gain':   round(balance - capital, 2),
        'gain_pct': round((balance - capital) / capital * 100, 1),
        'max_dd': round(max_dd, 2),
        'wr':     round(wins / total_trades * 100, 1) if total_trades else 0,
        'total_r': round(total_r, 2),
        'avg_r':  round(total_r / total_trades, 3) if total_trades else 0,
        'n':      total_trades,
        'wins':   wins,
    }

# ─────────────────────────────────────────────────────────────────────────────
# Stats helper
# ─────────────────────────────────────────────────────────────────────────────
def stats(subset, label, capital=500.0, risk_pct=0.02):
    if not subset: return {}
    rs   = [float(r['result_r']) for r in subset]
    wins = sum(1 for r in rs if r > 0)
    n    = len(rs)
    tot  = sum(rs)
    wr   = wins / n * 100
    avg  = tot / n
    # rough equity sim (no compound here, just for quick display)
    pnl_usd = tot * capital * risk_pct
    print(f'{label:<52} n={n:4d}  WR={wr:.0f}%  avg={avg:+.3f}R  total={tot:+.1f}R  ~${pnl_usd:+.0f}')
    return {'n': n, 'wr': wr, 'avg_r': avg, 'total_r': tot}

# ─────────────────────────────────────────────────────────────────────────────
# HTML generator
# ─────────────────────────────────────────────────────────────────────────────
def generate_html(signals, sim_stats, days, label=''):
    equity   = sim_stats['equity']
    eq_js    = ','.join(f'{v:.2f}' for v in equity)
    eq_dates = [datetime.fromtimestamp(s['ts_ms']/1000, tz=timezone.utc).strftime('%m/%d %H:%M')
                for s in signals]

    rows = ''
    for i, s in enumerate(signals):
        r   = s['result_r']
        clr = '#2ecc71' if r > 0 else '#e74c3c' if r < 0 else '#95a5a6'
        dt  = datetime.fromtimestamp(s['ts_ms']/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M')
        cl  = s.get('close_loc', '')
        bb  = s.get('bear_body', '')
        uw  = s.get('upper_wick', '')
        bal = s.get('balance_after', '')
        pnl = s.get('pnl_usd', '')
        pnl_clr = '#2ecc71' if isinstance(pnl, (int,float)) and pnl > 0 else '#e74c3c'
        rows += f"""<tr>
            <td>{dt}</td><td>{s['symbol']}</td><td>{s['session']}</td>
            <td>{s['range_bars']}</td><td>{s['range_pct']*100:.3f}%</td>
            <td>{s['range_cvd']:.0f}</td><td>{s['pre_cvd_flip']:.0f}</td>
            <td>{s['cvd_flip_ratio']:.2f}</td><td>{s['vr_at_breakout']:.2f}×</td>
            <td>{cl}</td><td>{bb}</td><td>{uw}</td>
            <td>{s['entry_price']:.4f}</td><td>{s['stop_price']:.4f}</td>
            <td>{s['target_price']:.4f}</td><td>{s['rr']:.2f}</td>
            <td style="color:{clr};font-weight:bold">{r:+.3f}R</td>
            <td>{s['exit_reason']}</td><td>{s['bars_held']}</td>
            <td style="color:{pnl_clr}">${pnl}</td>
            <td>${bal}</td>
        </tr>"""

    title = f'BE Backtest {label} — {days}d'
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
  body{{background:#0d0d0d;color:#e0e0e0;font-family:monospace;padding:20px;margin:0}}
  h1{{color:#f39c12;margin-bottom:12px}}
  .stats{{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:20px}}
  .stat{{background:#1a1a2e;border:1px solid #2980b9;padding:12px 18px;border-radius:6px;min-width:100px;text-align:center}}
  .stat-val{{font-size:1.6em;font-weight:bold;color:#3498db}}
  .stat-val.pos{{color:#2ecc71}}.stat-val.neg{{color:#e74c3c}}
  .stat-lbl{{font-size:.7em;color:#aaa;margin-top:3px}}
  .chart-wrap{{background:#111;border:1px solid #222;border-radius:6px;padding:12px;margin-bottom:20px;max-width:900px}}
  table{{width:100%;border-collapse:collapse;font-size:.75em;margin-top:10px}}
  th{{background:#1a1a2e;color:#3498db;padding:5px 7px;text-align:left;position:sticky;top:0;z-index:1;white-space:nowrap}}
  td{{padding:3px 7px;border-bottom:1px solid #1a1a1a;white-space:nowrap}}
  tr:hover td{{background:#1a1a2e}}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="stats">
  <div class="stat"><div class="stat-val">{sim_stats['n']}</div><div class="stat-lbl">Señales</div></div>
  <div class="stat"><div class="stat-val">{sim_stats['wr']}%</div><div class="stat-lbl">Win Rate</div></div>
  <div class="stat"><div class="stat-val">{sim_stats['avg_r']:+.3f}R</div><div class="stat-lbl">Avg R</div></div>
  <div class="stat"><div class="stat-val">{sim_stats['total_r']:+.1f}R</div><div class="stat-lbl">Total R</div></div>
  <div class="stat"><div class="stat-val {'pos' if sim_stats['gain']>0 else 'neg'}">${sim_stats['gain']:+.0f}</div><div class="stat-lbl">P&amp;L $500</div></div>
  <div class="stat"><div class="stat-val {'pos' if sim_stats['gain_pct']>0 else 'neg'}">{sim_stats['gain_pct']:+.1f}%</div><div class="stat-lbl">Retorno</div></div>
  <div class="stat"><div class="stat-val neg">-{sim_stats['max_dd']:.1f}%</div><div class="stat-lbl">Max DD</div></div>
  <div class="stat"><div class="stat-val">${sim_stats['final']:.0f}</div><div class="stat-lbl">Balance final</div></div>
</div>
<div class="chart-wrap">
  <canvas id="eq" height="80"></canvas>
</div>
<table>
<thead>
  <tr><th>Fecha UTC</th><th>Symbol</th><th>Sesión</th>
  <th>Win</th><th>Rng%</th><th>CVD+</th><th>preCVD</th><th>flip</th><th>VR</th>
  <th>cLoc</th><th>body</th><th>uWick</th>
  <th>Entry</th><th>Stop</th><th>Target</th><th>RR</th>
  <th>R</th><th>Razón</th><th>Bars</th><th>PnL</th><th>Balance</th>
  </tr>
</thead>
<tbody>{rows}</tbody>
</table>
<script>
const labels = {json.dumps(eq_dates[:len(equity)])};
const data   = [{eq_js}];
new Chart(document.getElementById('eq'),{{
  type:'line',
  data:{{labels,datasets:[{{label:'Equity ($)',data,borderColor:'#3498db',
    backgroundColor:'rgba(52,152,219,0.05)',pointRadius:0,borderWidth:1.5,tension:0.3}}]}},
  options:{{plugins:{{legend:{{display:false}}}},
    scales:{{x:{{ticks:{{color:'#555',maxTicksLimit:12}},grid:{{color:'#1a1a1a'}}}},
             y:{{ticks:{{color:'#aaa'}},grid:{{color:'#1a1a1a'}}}}}}}}
}});
</script>
</body>
</html>"""

    fname = f'scripts/be_backtest.html'
    with open(fname, 'w', encoding='utf-8') as f:
        f.write(html)

# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days',    type=int, default=163,
                    help='Días de historia (default 163 = desde ene 2026)')
    ap.add_argument('--symbols', nargs='+',
                    default=['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'XRPUSDT'])
    ap.add_argument('--capital', type=float, default=500.0)
    ap.add_argument('--risk',    type=float, default=0.02,  help='Riesgo por trade (0.02 = 2%%)')
    args = ap.parse_args()

    now_ms   = int(time.time() * 1000)
    start_ms = now_ms - args.days * 24 * 3600 * 1000

    CAPITAL  = args.capital
    RISK_PCT = args.risk

    # ── Config a testear ──────────────────────────────────────────────────────
    # BASE: el calibrado de 90d (ventana 8, VR≥2.5, flip≥0.40, sin NY)
    config_base = dict(windows=[8], vr_min=2.5, flip_min=0.40,
                       sessions={'London','LondonNyOverlap'})

    # MICRO: base + filtros de barra de ruptura
    config_micro = dict(**config_base,
                        close_loc_max=0.35, bear_body_min=0.35, upper_wick_max=0.30)

    # MAS SEÑALES: ventanas [8,15,20], VR adaptado, rango más amplio
    config_wide  = dict(windows=[8,15,20], vr_min=2.5, flip_min=0.40,
                        sessions={'London','LondonNyOverlap'}, range_max_pct=0.007)

    # MAS SEÑALES + MICRO
    config_wide_micro = dict(**config_wide,
                             close_loc_max=0.35, bear_body_min=0.35, upper_wick_max=0.30)

    # NY SELECTIVO: solo win=8 + VR≥3.5 + micro en NY
    config_ny_strict = dict(windows=[8], vr_min=3.5, flip_min=0.50,
                            sessions={'London','LondonNyOverlap','NewYork'},
                            close_loc_max=0.30, bear_body_min=0.40, upper_wick_max=0.20)

    all_bars_by_sym = {}

    print(f'Descargando {args.days}d M1 ({args.days//30:.0f} meses)...')
    for sym in args.symbols:
        print(f'\n[{sym}]')
        bars = fetch_klines(sym, '1m', start_ms, now_ms)
        all_bars_by_sym[sym] = bars

    # ── Correr detector en cada config ───────────────────────────────────────
    configs = {
        'BASE (win=8, VR≥2.5, flip≥0.40, L+LO)':         config_base,
        'BASE+MICRO (+ cLoc<0.35, body>0.35, uw<0.30)':   config_micro,
        'WIDE (win=8/15/20, VR≥2.5, L+LO)':               config_wide,
        'WIDE+MICRO':                                       config_wide_micro,
        'NY_STRICT (win=8, VR≥3.5, flip≥0.50, todo)':     config_ny_strict,
    }

    best_sigs = None
    best_label = ''
    best_avg   = -999

    print('\n' + '='*80)
    print(f'{"Config":<52} {"n":>4}  {"WR":>5}  {"avg_R":>8}  {"total_R":>8}  {"~PnL $"+str(int(CAPITAL)):>10}')
    print('='*80)

    for label, cfg in configs.items():
        det = BEDetector(
            windows        = cfg.get('windows'),
            sessions       = cfg.get('sessions'),
            vr_min         = cfg.get('vr_min', BREAKOUT_VR_MIN),
            flip_min       = cfg.get('flip_min', CVD_FLIP_MIN_RATIO),
            close_loc_max  = cfg.get('close_loc_max'),
            bear_body_min  = cfg.get('bear_body_min'),
            upper_wick_max = cfg.get('upper_wick_max'),
            range_max_pct  = cfg.get('range_max_pct', RANGE_MAX_PCT),
        )
        sigs = []
        for sym, bars in all_bars_by_sym.items():
            det.reset()
            for i, bar in enumerate(bars):
                sig = det.on_bar(bar, classify_session(bar['ts_ms']))
                if sig:
                    sig['symbol']    = sym
                    sig['bar_index'] = i
                    sigs.append(sig)

        for sig in sigs:
            bars  = all_bars_by_sym[sig['symbol']]
            idx   = sig['bar_index'] + 1
            r, reason, bh, exit_p = simulate(sig, bars[idx:idx+600])
            sig['result_r']    = round(r if r is not None else 0.0, 3)
            sig['exit_reason'] = reason
            sig['bars_held']   = bh
            sig['exit_price']  = round(exit_p, 4)

        st = stats(sigs, label, CAPITAL, RISK_PCT)
        if st and st.get('avg_r', -999) > best_avg:
            best_avg   = st['avg_r']
            best_sigs  = sigs
            best_label = label

    print('='*80)

    # ── Desglose detallado del mejor config ───────────────────────────────────
    print(f'\n── Desglose "{best_label}" ──')

    print('\nPor sesión:')
    for sess in ['London','LondonNyOverlap','NewYork']:
        subset = [s for s in best_sigs if s['session'] == sess]
        if subset: stats(subset, f'  {sess}', CAPITAL, RISK_PCT)

    print('\nPor símbolo:')
    for sym in args.symbols:
        subset = [s for s in best_sigs if s['symbol'] == sym]
        if subset: stats(subset, f'  {sym}', CAPITAL, RISK_PCT)

    print('\nPor ventana:')
    for win in sorted({s['range_bars'] for s in best_sigs}):
        subset = [s for s in best_sigs if s['range_bars'] == win]
        stats(subset, f'  win={win}', CAPITAL, RISK_PCT)

    print('\nPor razón de salida:')
    for reason in ['TARGET','STOP','TIME_STOP','TIMEOUT']:
        subset = [s for s in best_sigs if s['exit_reason'] == reason]
        if subset: stats(subset, f'  {reason}', CAPITAL, RISK_PCT)

    # Señales por día
    days_count = len({datetime.fromtimestamp(s['ts_ms']/1000, tz=timezone.utc).date()
                      for s in best_sigs})
    n_per_day  = len(best_sigs) / days_count if days_count else 0
    print(f'\n  Señales por día activo: {n_per_day:.2f}')

    # ── Capital simulation del mejor config ───────────────────────────────────
    sim_data = sorted(best_sigs, key=lambda x: x['ts_ms'])
    sim_st   = capital_sim(sim_data, CAPITAL, RISK_PCT)

    print(f'\n── Simulación capital ${CAPITAL:.0f} ({RISK_PCT*100:.0f}% riesgo/trade) ──')
    print(f'  Balance final:  ${sim_st["final"]:.2f}')
    print(f'  Ganancia:       ${sim_st["gain"]:+.2f}  ({sim_st["gain_pct"]:+.1f}%)')
    print(f'  Max Drawdown:   -{sim_st["max_dd"]:.2f}%')
    print(f'  Win Rate:       {sim_st["wr"]}%')
    print(f'  Trades:         {sim_st["n"]}')

    # ── Guardar CSV con timestamp ──────────────────────────────────────────────
    ts_str  = datetime.now().strftime('%Y%m%d_%H%M')
    out_csv = f'scripts/be_backtest_{ts_str}.csv'
    cols = ['num','fecha','symbol','session','range_bars','range_pct',
            'range_cvd','pre_cvd_flip','cvd_flip_ratio',
            'vr_at_breakout','breakout_delta','close_loc','bear_body','upper_wick',
            'entry_price','stop_price','target_price','rr',
            'result_r','exit_reason','bars_held','exit_price','pnl_usd','balance_after']
    with open(out_csv, 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        for i, s in enumerate(sim_data, 1):
            s['num']       = i
            s['fecha']     = datetime.fromtimestamp(s['ts_ms']/1000,tz=timezone.utc).strftime('%Y-%m-%d %H:%M')
            s['range_pct'] = s['range_pct']  # ya es fracción
            w.writerow(s)

    # también copia sin timestamp para referencia fácil
    import shutil
    shutil.copy(out_csv, 'scripts/be_backtest_results.csv')
    print(f'\nCSV guardado: {out_csv}')
    print(f'CSV latest:   scripts/be_backtest_results.csv')

    # ── HTML ──────────────────────────────────────────────────────────────────
    for s in sim_data:
        s['range_pct'] = s['range_pct']  # ya fracción
    generate_html(sim_data, sim_st, args.days, best_label)
    print('HTML:         scripts/be_backtest.html')


if __name__ == '__main__':
    main()
