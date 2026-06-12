#!/usr/bin/env python3
"""
Backtest RBF — corre como subprocess desde Vite, imprime JSON a stdout.
Uso: python api/backtest_script.py --days 14

Exit logic (idéntica al sistema live rbf_paper.rs):
  - Target 2R, trailing ATR×1.2 activado en 1.75R favorable
  - Sin time stop — el SL limita el riesgo máximo
  - Config centralizada arriba del archivo (sin hardcoding disperso)
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

# ── CONFIG (todos los params en un solo lugar) ──────────────────────────────
CAPITAL          = 500.0
RISK_PCT         = 0.02          # 2% por trade
RISK_USD         = CAPITAL * RISK_PCT

# Detección de rango
RANGE_WINDOWS    = [15, 20, 30, 45, 60]
RANGE_MIN_PCT    = 0.08          # % mínimo del rango
RANGE_MAX_PCT    = 0.55          # % máximo del rango
VR_MIN           = 3.0           # volumen ratio mínimo para breakout
MIN_RANGE_ATR    = 1.5           # rango mínimo en ATRs
COOLDOWN_BARS    = 60            # barras entre señales del mismo símbolo
SESSIONS_OK      = {'London', 'LondonNyOverlap', 'NewYork'}

# Exits: igual que el sistema live (rbf_paper.rs)
RR_SHORT         = 2.0           # target en R (live usa 2R)
TRAIL_ACTIVATE_R = 1.90          # activa trailing cuando el precio bajó 1.90R (sweep: 1.90 es óptimo)
TRAIL_ATR_K      = 1.2           # trailing_stop = best_low + 1.2 * ATR

# Pre-breakout
PRE_VR_MIN       = 1.5
PRE_ZONE_PCT     = 0.001
PRE_RR           = 3.0
PRE_OI_MAX       = 3    # oi_mom_bars_recent <= 3 → WR=58% (idéntico a live pre_breakout_oi_max)

TABLES = {
    'BTCUSDT': 'btc_bars', 'ETHUSDT': 'eth_bars', 'BNBUSDT': 'bnb_bars',
    'SOLUSDT': 'sol_bars', 'XRPUSDT': 'xrp_bars',
}
BAR_COLS = 'ts_ms,open,high,low,close,volume,bar_delta,vr,atr,session,cvd_slope,obi_l5,vwap,regime,stacked_imb,absorption,thin_below,oi_momentum'

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

def simulate(bars, entry, stop, target, atr):
    """
    Simula un trade Short — lógica idéntica a rbf_paper.rs.
    Exit logic:
      1. STOP_LOSS     — HIGH >= stop original (sin trailing)
      2. TRAILING_STOP — HIGH >= trailing_stop (una vez activo a TRAIL_ACTIVATE_R)
      3. TAKE_PROFIT   — LOW  <= target
      Trailing: best_low + TRAIL_ATR_K * ATR_barra, activa en TRAIL_ACTIVATE_R
    """
    risk         = abs(stop - entry)
    trail_stop   = stop        # stop dinámico, empieza en stop original
    best_low     = entry       # mejor precio (Short: más bajo alcanzado)
    trailing_on  = False

    for k, b in enumerate(bars):
        h, l = b['high'], b['low']
        atr_b = b.get('atr') or atr    # ATR de esta barra, fallback al de entry

        # Actualizar mejor low favorable
        if l < best_low:
            best_low = l

        # Activar trailing cuando el precio bajó TRAIL_ACTIVATE_R
        if not trailing_on and (entry - best_low) / risk >= TRAIL_ACTIVATE_R:
            trailing_on = True
            # Lock floor: trail_stop salta a exactamente 1.75R garantizados
            floor = entry - TRAIL_ACTIVATE_R * risk
            if floor < trail_stop:
                trail_stop = floor

        # Mover trailing stop hacia abajo siguiendo al precio
        if trailing_on and atr_b > 0.0:
            candidate = best_low + TRAIL_ATR_K * atr_b
            if candidate < trail_stop:
                trail_stop = candidate

        # 1. Stop hit (original o trailing)
        if h >= trail_stop:
            reason = 'TRAILING_STOP' if trailing_on else 'STOP_LOSS'
            r = (entry - trail_stop) / risk
            return round(r, 4), reason, k+1, b['ts_ms']

        # 2. Target hit
        if l <= target:
            r = (entry - target) / risk
            return round(r, 4), 'TAKE_PROFIT', k+1, b['ts_ms']

    last_c = bars[-1]['close'] if bars else entry
    return round((entry - last_c) / risk, 4), 'DATA_END', len(bars), (bars[-1]['ts_ms'] if bars else 0)

OBI_THRESHOLD = 0.15  # debe coincidir con obi_threshold en strategy.toml

def score_confluence(b, entry, is_short=True):
    """
    Scoring v2 — 6 flags, principio "participantes atrapados + estructura limpia".
    Coincide con ConfluenceFlag enum en range_breakout_flow.rs (refactor Jun 2026).
    Max: 6 puntos.
    """
    score = 0
    flags = []

    # [+1] Stacked imbalance en dirección del breakout
    stk = b.get('stacked_imb') or ''
    if is_short and stk in ('Bearish', 'FBG-Bearish'):
        score += 1; flags.append('stacked_imbalance')
    elif not is_short and stk in ('Bullish', 'FBG-Bullish'):
        score += 1; flags.append('stacked_imbalance')

    # [+1] Absorción de footprint
    abso = b.get('absorption') or ''
    if is_short and ('Ask' in abso or 'Bearish' in abso):
        score += 1; flags.append('absorption')
    elif not is_short and ('Bid' in abso or 'Bullish' in abso):
        score += 1; flags.append('absorption')

    # [+1] LVN / thin zone en dirección del target
    if is_short and b.get('thin_below'):
        score += 1; flags.append('lvn_thin')
    elif not is_short and b.get('thin_above'):
        score += 1; flags.append('lvn_thin')

    # [+1] VWAP bias — precio en lado correcto del VWAP
    vwap = b.get('vwap')
    if vwap and vwap > 0:
        if is_short and entry < vwap:
            score += 1; flags.append('vwap_bias')
        elif not is_short and entry > vwap:
            score += 1; flags.append('vwap_bias')

    # [+1] OI momentum — OI expandiendo = nuevas posiciones = convicción
    oi_mom = b.get('oi_momentum')
    if oi_mom is True:
        score += 1; flags.append('oi_momentum')

    # [+1] OBI trap — OBI en dirección CONTRARIA = participantes atrapados
    # Short: obi_l5 > threshold (compradores en libro) → van a ser squeezed abajo
    # Long:  obi_l5 < -threshold (vendedores en libro) → van a ser squeezed arriba
    obi = b.get('obi_l5') or 0.0
    if is_short and obi > OBI_THRESHOLD:
        score += 1; flags.append('obi_trap')
    elif not is_short and obi < -OBI_THRESHOLD:
        score += 1; flags.append('obi_trap')

    return score, flags

def build_trade(sym, b, entry, stop_p, target, rr, rw, rp, deltas, sim_bars, reason, r, dur, exit_ms, equity, idx_off, count, is_pre):
    closed = datetime.fromtimestamp(exit_ms/1000, tz=timezone.utc).isoformat() if exit_ms else None
    vwap   = b.get('vwap')
    # exit_p calculado desde r para ser exacto sin importar el tipo de salida
    risk   = abs(stop_p - entry)
    exit_p = round(entry - r * risk, 6)
    pnl = round(r * RISK_USD, 2)
    sc, cf = score_confluence(b, entry, is_short=True)
    return {
        'idx':            idx_off + count,
        'id':             f'bt-{sym}-{b["ts_ms"]}',
        'sym':            sym,
        'dir':            'Short',
        'session':        b.get('session') or '',
        'score':          sc,
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
        'regime': '', 'sessionPhase': '', 'evidence': [], 'confluenceFlags': cf,
        'vetoReason': '', 'funding': None, 'rangeTouch': None, 'htf': None,
    }

def detect(sym, bars, idx_off, equity_start):
    trades, equity, last_sig = [], equity_start, -COOLDOWN_BARS
    last_pre_sig = -COOLDOWN_BARS
    n = len(bars)
    for i in range(COOLDOWN_BARS, n):
        b   = bars[i]
        ses = b.get('session') or ''
        vr  = b.get('vr') or 0
        atr = b.get('atr') or 0
        if ses not in SESSIONS_OK: continue
        # ETH London skip (live n=11: WR=14% London → excluir)
        if sym == 'ETHUSDT' and ses == 'London': continue
        if atr <= 0: continue
        # Solo barras con microestructura real (monitor live)
        if b.get('cvd_slope') is None or b.get('vwap') is None: continue

        # expansion_bars_recent: cuenta barras Expansion en las 25 anteriores.
        # SOL/XRP: bypass (correlación invertida / muestra insuficiente).
        # BTC/ETH/BNB: filtro ≤1 (análisis 72 trades: ≤1→WR=54.5% vs ≤3→48.8%).
        if sym not in ('SOLUSDT', 'XRPUSDT'):
            exp_window = bars[max(0, i-25):i]
            exp_count  = sum(1 for x in exp_window if x.get('regime') == 'Expansion')
            if exp_count > 1: continue
        if i - last_sig < COOLDOWN_BARS: continue
        vwap = b.get('vwap')
        # vswap_gate DESACTIVADO (strategy.toml 2026-06-11): recolectando datos live

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
                # breakout_ext_gate DESACTIVADO (strategy.toml 2026-06-11): recolectando datos live
                if not cvd_ok: continue
                # CVD London gate (análisis 72 trades): ganadores CVD=-597 vs perdedores=+1979.
                # En London, si la presión compradora fue fuerte durante el rango → fakeout.
                cvd_sum_win = sum(d or 0 for d in deltas)
                if ses == 'London' and cvd_sum_win > 200: continue
                # Pre-CVD gate: últimas 5 barras del rango.
                # Autopsia 39 trades: pre_cvd_5b > 0 → WR=0% (n=7). Compradores no capitularon.
                # "En su lugar": no activamos fired/last_sig → la barra siguiente re-escanea
                # el mismo rango; si CVD giró negativo, entra 1-2 barras después (entrada diferida).
                pre5 = sum(d or 0 for d in deltas[-5:])
                if pre5 > 0: continue
                # ETH gates (calibración 2026-06-10, live n=11):
                #   cvd_in_range < -700 → agotamiento vendedor, fakeout (WR=14%)
                #   obi_l5 > 0.10       → compradores dominan, breakout resistido
                if sym == 'ETHUSDT':
                    cvd_sum = sum(d or 0 for d in deltas)
                    if cvd_sum < -700: continue
                    if (b.get('obi_l5') or 0) > 0.10: continue
                entry  = close
                stop_p = hi
                target = entry - RR_SHORT * (stop_p - entry)
                sim    = bars[i+1:i+1+300]
                r, reason, dur, exit_ms = simulate(sim, entry, stop_p, target, atr)
                equity = round(equity + r * RISK_USD, 2)
                trades.append(build_trade(sym, b, entry, stop_p, target, RR_SHORT, rw, rp,
                                          deltas, sim, reason, r, dur, exit_ms, equity - r*RISK_USD,
                                          idx_off, len(trades)+1, False))
                last_sig = i
                fired = True
                break

            # ── MODO PRE-BREAKOUT (entrada anticipada) ────────────────────────
            # Gate OI: idéntico al live (main.rs:2168) — contar barras true en últimas 25.
            # Calibración n=30 Shorts: oi_mom_bars_recent<=3 → WR=58%.
            if (not fired
                    and vr >= PRE_VR_MIN
                    and close <= lo * (1.0 + PRE_ZONE_PCT)
                    and cvd_ok
                    and i - last_pre_sig >= COOLDOWN_BARS):
                oi_mom_recent = sum(1 for x in bars[max(0, i-25):i] if x.get('oi_momentum') is True)
                if oi_mom_recent > PRE_OI_MAX: continue
                # Pre-CVD gate: si compradores activos en últimas 5 barras → no entrar.
                # Autopsia 39 trades: pre_cvd_5b > 0 → WR=0% (n=7). Entrada diferida:
                # no seteamos fired/last_sig → barra siguiente re-escanea el mismo rango.
                pre5 = sum(d or 0 for d in deltas[-5:])
                if pre5 > 0: continue
                entry  = close
                stop_p = hi
                risk   = stop_p - entry
                if risk < 1e-6: continue
                target = entry - PRE_RR * risk
                if (entry - target) / risk < 1.5: continue
                sim    = bars[i+1:i+1+300]
                r, reason, dur, exit_ms = simulate(sim, entry, stop_p, target, atr)
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
