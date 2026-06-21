#!/usr/bin/env python3
"""
Backtest Buyer Exhaustion — corre como subprocess desde Vite, imprime JSON a stdout.
Uso: python api/be_backtest_script.py --days 14

Fuente de datos:
  - Reciente: Supabase *_bars (bar_delta, session ya calculados por el monitor)
  - Histórico: Binance REST API v3/klines (sin auth, hasta ~2 años atrás)
    bar_delta = 2 * taker_buy_base_vol - volume
    session   = clasificado por hora UTC (espejo de session_tracker.rs)
  - Caché en disco: apps/trade-lab/api/cache/  (1ª vez lento, siguientes ~instante)

Lógica: espejo de BuyerExhaustionConfig::default()
  - Rango 8 barras M1 con CVD neto > 0 (compradores atrapados)
  - Giro CVD: |sum(últimas 5 deltas)| / range_cvd >= 0.40
  - Ruptura a la baja: VR_50 >= 2.5, bar_delta < 0, close < range_low
  - Micro: close_location <= 0.35, bear_body >= 0.35, upper_wick <= 0.30
"""
import json, math, os, sys, time, urllib.request, urllib.parse, argparse
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

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
VR_WINDOW          = 50
CLOSE_LOC_MAX      = 0.35
BEAR_BODY_MIN      = 0.35
UPPER_WICK_MAX     = 0.30
TIME_STOP_BARS     = 60
COOLDOWN_BARS      = 60
SESSIONS_OK        = {'LondonNyOverlap'}   # London WR=24% -0.20R → descartado
CAPITAL            = 500.0
RISK_USD           = CAPITAL * 0.02

TABLES = {
    'BTCUSDT': 'btc_bars', 'ETHUSDT': 'eth_bars', 'BNBUSDT': 'bnb_bars',
    'SOLUSDT': 'sol_bars', 'XRPUSDT': 'xrp_bars',
}
SB_COLS     = 'ts_ms,open,high,low,close,volume,bar_delta,session'
BINANCE_API = 'https://fapi.binance.com/fapi/v1/klines'   # FUTURES — mismo flujo que el bot live
CACHE_DIR   = Path(__file__).parent / 'cache'

# ─── Session classifier — espejo exacto de session_tracker.rs ────────────────
# London 08:00-13:00 · LondonNyOverlap 13:00-17:00 · NewYork 17:00-21:00
# Asia 01:00-08:00 (minutos: LONDON_OPEN=480, OVERLAP_START=780, OVERLAP_END=1020)

def classify_session(ts_ms: int) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    m  = dt.hour * 60 + dt.minute
    if 780 <= m < 1020: return 'LondonNyOverlap'   # 13:00–17:00
    if 480 <= m < 780:  return 'London'             # 08:00–13:00
    if 1020 <= m < 1260: return 'NewYork'           # 17:00–21:00
    if 60  <= m < 480:  return 'Asia'               # 01:00–08:00
    return 'Other'

# ─── Binance fetch (raw, sin caché) ──────────────────────────────────────────

def binance_fetch(symbol: str, start_ms: int, end_ms: int) -> list:
    bars, cur = [], start_ms
    while cur < end_ms:
        qs = urllib.parse.urlencode({
            'symbol': symbol, 'interval': '1m',
            'startTime': cur, 'endTime': min(cur + 1500 * 60_000, end_ms),
            'limit': 1500,
        })
        try:
            data = json.loads(urllib.request.urlopen(
                urllib.request.Request(f'{BINANCE_API}?{qs}'), timeout=30
            ).read())
        except Exception as e:
            print(f'[binance] warn {symbol}: {e}', file=sys.stderr)
            break
        if not data:
            break
        if isinstance(data, dict):          # FAPI devuelve dict en errores: {"code":-1121,...}
            print(f'[binance] FAPI error {symbol}: {data}', file=sys.stderr)
            break
        for k in data:
            open_time = int(k[0])
            if open_time >= end_ms:
                break
            volume    = float(k[5])
            taker_buy = float(k[9])
            bars.append({
                'ts_ms':     open_time,
                'open':      float(k[1]),
                'high':      float(k[2]),
                'low':       float(k[3]),
                'close':     float(k[4]),
                'volume':    volume,
                'bar_delta': 2.0 * taker_buy - volume,
                'session':   classify_session(open_time),
            })
        if len(data) < 1500:                # corregido: era < 1000 con limit=1500
            break
        cur = int(data[-1][0]) + 60_000
        time.sleep(0.05)
    return bars

# ─── Caché en disco (Binance historical) ─────────────────────────────────────

def _cache_path(sym: str) -> Path:
    return CACHE_DIR / f'fapi_{sym}.json'   # fapi_ evita usar caché vieja de spot

def _load_cache(sym: str) -> list:
    p = _cache_path(sym)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_bytes())
    except Exception:
        return []

def _save_cache(sym: str, bars: list):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # separators compactos: ahorra ~30% vs default
    _cache_path(sym).write_text(
        json.dumps(bars, separators=(',', ':')), encoding='utf-8'
    )

def binance_fetch_cached(symbol: str, start_ms: int, end_ms: int) -> list:
    """
    Descarga barras de Binance con caché en disco.
    - 1ª ejecución: descarga todo (lento, una sola vez).
    - Siguientes: sólo descarga el delta (barras nuevas desde last_cached_ts).
    - Si se amplía el rango hacia atrás: agrega las barras faltantes.
    """
    cached = _load_cache(symbol)
    dirty  = False

    if not cached:
        print(f'[{symbol}] Binance 1ª descarga {_iso(start_ms)}→{_iso(end_ms)}…', file=sys.stderr)
        cached = binance_fetch(symbol, start_ms, end_ms)
        dirty  = True
    else:
        first_ts = cached[0]['ts_ms']
        last_ts  = cached[-1]['ts_ms']

        # Ampliar hacia atrás si el usuario pide más historia
        if first_ts > start_ms:
            print(f'[{symbol}] Binance retroceso {_iso(start_ms)}→{_iso(first_ts)}…', file=sys.stderr)
            older  = binance_fetch(symbol, start_ms, first_ts)
            cached = older + cached
            dirty  = True
            last_ts = cached[-1]['ts_ms']

        # Actualizar hacia adelante (sólo el delta)
        if last_ts + 60_000 < end_ms:
            fetch_from = last_ts + 60_000
            print(f'[{symbol}] Binance delta {_iso(fetch_from)}→{_iso(end_ms)}…', file=sys.stderr)
            new_bars = binance_fetch(symbol, fetch_from, end_ms)
            if new_bars:
                cached.extend(new_bars)
                dirty = True
        else:
            print(f'[{symbol}] Binance cache HIT ({len(cached):,} bars)', file=sys.stderr)

    if dirty:
        # Dedup + ordenar antes de guardar
        seen = {}
        for b in cached:
            seen[b['ts_ms']] = b
        cached = sorted(seen.values(), key=lambda b: b['ts_ms'])
        _save_cache(symbol, cached)
        print(f'[{symbol}] cache guardado: {len(cached):,} bars', file=sys.stderr)

    return [b for b in cached if start_ms <= b['ts_ms'] < end_ms]

# ─── Supabase ─────────────────────────────────────────────────────────────────

def sb_earliest_ms_for(table: str) -> int | None:
    qs = urllib.parse.urlencode({
        'select': 'ts_ms', 'bar_delta': 'not.is.null',
        'order': 'ts_ms.asc', 'limit': '1',
    })
    req  = urllib.request.Request(
        f'{SUPABASE_URL}/rest/v1/{table}?{qs}',
        headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'}
    )
    rows = json.loads(urllib.request.urlopen(req, timeout=15).read())
    return rows[0]['ts_ms'] if rows else None

def sb_earliest_ms() -> int | None:
    """Primer ts_ms con bar_delta (entre todos los símbolos), en paralelo."""
    earliest = None
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(sb_earliest_ms_for, t): t for t in TABLES.values()}
        for fut in as_completed(futs):
            ms = fut.result()
            if ms is not None and (earliest is None or ms < earliest):
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
        if len(chunk) < limit:
            break
        offset += limit
    return rows

# ─── Fusión de fuentes ────────────────────────────────────────────────────────

def load_bars(sym: str, table: str, start_ms: int, sb_first_ms: int | None) -> list:
    sb_start = sb_first_ms if sb_first_ms else start_ms

    binance_bars: list = []
    if start_ms < sb_start:
        binance_bars = binance_fetch_cached(sym, start_ms, sb_start)

    sb_bars = sb_fetch(table, max(start_ms, sb_start))
    print(f'[{sym}] Supabase: {len(sb_bars):,} bars', file=sys.stderr)

    all_bars = binance_bars + sb_bars
    all_bars.sort(key=lambda b: b['ts_ms'])
    seen, unique = set(), []
    for b in all_bars:
        if b['ts_ms'] not in seen:
            seen.add(b['ts_ms'])
            unique.append(b)
    return unique

def _iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d')

# ─── Simulación de trade ──────────────────────────────────────────────────────

def simulate(bars, entry, stop, target, entry_session: str):
    """
    Simula un trade Short espejo exacto del Rust BePaperTrader.on_bar_close().

    Prioridades por barra (igual que el paper trader):
      1. Stop    (high >= stop_price)
      2. Target  (low  <= target_price)
      3. SessionEnd — si la sesión cambió respecto a la entrada, cierra al close.
                      Espejo de be_paper.close_session() llamado en main.rs cuando
                      session.session != self.be_last_session.
      4. TimeStop   — sólo si bars_held >= 30 Y posición en pérdida (close >= entry).

    SessionEnd es el bug que faltaba: en live el trade se cierra al final de la
    sesión (London→Overlap a las 12:00 UTC, Overlap→NY a las 16:00 UTC). Sin esto,
    el backtest dejaba correr trades ganadores hasta revertir al stop (-1R).
    """
    prev_session = entry_session
    for k, b in enumerate(bars):
        h, l, c = b['high'], b['low'], b['close']
        cur_session = b.get('session') or prev_session

        # 1. Stop (prioridad máxima — igual que Rust on_bar_close)
        if h >= stop:
            return (entry - stop) / abs(stop - entry), 'STOP', k + 1, b['ts_ms']
        # 2. Target
        if l <= target:
            return (entry - target) / abs(stop - entry), 'TARGET', k + 1, b['ts_ms']
        # 3. SessionEnd — primera barra con sesión distinta a la entrada
        if cur_session != prev_session and prev_session in SESSIONS_OK:
            return (entry - c) / abs(stop - entry), 'SESSION_END', k + 1, b['ts_ms']
        # TimeStop desactivado: SESSION_END es el límite natural.
        # Con 2R target y solo Overlap, el time stop no aporta valor (WR=0% -0.38R/trade).

        prev_session = cur_session
    return (entry - bars[-1]['close']) / abs(stop - entry) if bars else 0.0, 'DATA_END', len(bars), (bars[-1]['ts_ms'] if bars else 0)

# ─── Detección + simulación por símbolo ──────────────────────────────────────

def detect(sym, bars):
    trades, last_sig = [], -COOLDOWN_BARS
    n = len(bars)

    for i in range(COOLDOWN_BARS, n - TIME_STOP_BARS):
        b   = bars[i]
        ses = b.get('session') or ''
        if ses not in SESSIONS_OK:        continue
        if b.get('bar_delta') is None:    continue
        if i - last_sig < COOLDOWN_BARS:  continue

        win = bars[i - RANGE_WINDOW : i]
        if len(win) < RANGE_WINDOW:       continue

        hi  = max(x['high'] for x in win)
        lo  = min(x['low']  for x in win)
        rng = hi - lo
        rp  = rng / b['close'] * 100.0
        if rp < RANGE_MIN_PCT or rp > RANGE_MAX_PCT: continue

        deltas = [x.get('bar_delta') for x in win]
        if any(d is None for d in deltas): continue

        range_cvd = sum(deltas)
        if range_cvd <= 0: continue

        pre_cvd = sum(deltas[-PRE_CVD_BARS:])
        if pre_cvd >= 0: continue
        if abs(pre_cvd) / range_cvd < CVD_FLIP_RATIO_MIN: continue
        flip_ratio = abs(pre_cvd) / range_cvd

        close = b['close']
        if close >= lo: continue
        ext_pct = (lo - close) / rng
        if ext_pct > 0.50: continue   # breakout extendido — entrada sucia (chasing)

        vol_hist = [bars[j]['volume'] for j in range(max(0, i - VR_WINDOW), i)]
        avg_vol  = sum(vol_hist) / len(vol_hist) if vol_hist else 1.0
        vr       = b['volume'] / avg_vol if avg_vol > 0 else 0.0
        if vr < VR_MIN: continue

        if (b.get('bar_delta') or 0) >= 0: continue

        bh, bl, bo = b['high'], b['low'], b['open']
        bar_rng = bh - bl
        if bar_rng > 0:
            if (close - bl)           / bar_rng > CLOSE_LOC_MAX:  continue
            if abs(close - bo)        / bar_rng < BEAR_BODY_MIN:  continue
            if (bh - max(bo, close))  / bar_rng > UPPER_WICK_MAX: continue

        # ── Filtros features nuevas (edge analysis 730d 2026-06-11) ─────────────
        # F1: compresion — avg bar range vs range total
        total_br = sum(x['high'] - x['low'] for x in win)
        comp_r   = (total_br / RANGE_WINDOW) / rng if rng > 1e-10 else 0.5

        # F2: bull_bias — % barras cerrando en mitad superior del rango
        rng_mid = (hi + lo) / 2.0
        bull_b  = sum(1 for x in win if x['close'] > rng_mid) / RANGE_WINDOW

        # F3: posicion del rango vs 50 barras previas
        lb = bars[max(0, i - RANGE_WINDOW - 50) : i - RANGE_WINDOW]
        if len(lb) >= 10:
            lb_hi = max(x['high'] for x in lb)
            lb_lo = min(x['low']  for x in lb)
            span  = lb_hi - lb_lo
            pos   = min(max((hi - lb_lo) / span if span > 1e-10 else 0.5, 0.0), 1.0)
        else:
            pos = 0.5

        # F4: cvd_z — magnitud del CVD vs historico 100 barras
        hd = [bars[j].get('bar_delta') for j in range(max(0, i - RANGE_WINDOW - 100), i - RANGE_WINDOW)]
        hd = [d for d in hd if d is not None]
        avg_abs_h = sum(abs(d) for d in hd) / len(hd) if hd else 1.0
        cvd_z     = range_cvd / (avg_abs_h * math.sqrt(RANGE_WINDOW) + 1e-10)

        # F5: cvd_slope — pendiente del CVD acumulado durante el rango
        cum_d, acc = [], 0.0
        for d in deltas:
            acc += d
            cum_d.append(acc)
        n_cd   = len(cum_d)
        xm_cd  = (n_cd - 1) / 2.0
        ym_cd  = sum(cum_d) / n_cd
        cov_cd = sum((k - xm_cd) * (v - ym_cd) for k, v in enumerate(cum_d))
        var_cd = sum((k - xm_cd) ** 2 for k in range(n_cd))
        raw_sl = cov_cd / var_cd if var_cd > 0 else 0.0
        avg_dabs    = sum(abs(d) for d in deltas) / RANGE_WINDOW
        cvd_slope_n = raw_sl / (avg_dabs + 1e-10)

        # ── Filtros per-symbol (Overlap 730d 2026-06-11) ─────────────────────
        if sym == 'BTCUSDT':
            # vrbin=mid: WR=0% EV=-1.000 n=6 — VR 3.5-5.5x sin fuerza real en Overlap
            if 3.5 <= vr < 5.5: continue
        elif sym == 'ETHUSDT':
            # fbin=lo: WR=0% EV=-1.000 n=5 — flip debil no confirma distribucion
            if flip_ratio < 0.60: continue
            # posbin=bot: WR=17% EV=-0.500 n=6 — BE en minimo relativo = sin trampa
            if pos < 0.40: continue
        elif sym == 'BNBUSDT':
            # compbin=tight: WR=60% EV=+0.800 n=10 — solo rangos comprimidos
            if comp_r >= 0.30: continue
        elif sym == 'SOLUSDT':
            # cvsbin=falling: WR=0% EV=-1.000 n=6 — CVD cayendo todo el rango
            if cvd_slope_n < -0.3: continue
            # biasbin=mid: WR=14% EV=-0.571 n=7 — rango sin sesgo = compradores indecisos
            if 0.40 < bull_b <= 0.60: continue
        # XRPUSDT: sin filtro (WR=47% EV=+0.400 — mejor par)

        stop_p = max(hi, b['high'])   # stop en el HIGH real visto (rango + barra de entrada)
        risk   = stop_p - close
        if risk < 1e-8: continue
        target = close - 2 * risk                      # 2R fijo: reward siempre ≥ 1R
        if b['low'] <= target: continue                # el move ya pasó durante la barra de entrada

        # 350 barras: cubre sesión completa (London = 300 min, Overlap = 240 min).
        # SESSION_END cierra al cambio de sesión; TIME_STOP cierra a los 30 min si en pérdida.
        sim = bars[i + 1 : i + 1 + 350]
        r, reason, dur, exit_ms = simulate(sim, close, stop_p, target, ses)

        pnl    = round(r * RISK_USD, 2)
        closed = datetime.fromtimestamp(exit_ms / 1000, tz=timezone.utc).isoformat() if exit_ms else None

        if reason in ('TIME_STOP', 'SESSION_END', 'DATA_END') and dur <= len(sim):
            exit_p = round(sim[dur - 1]['close'], 6)
        elif reason == 'TARGET':
            exit_p = round(target, 6)
        else:
            exit_p = round(stop_p, 6)

        trades.append({
            'id':  f'be-{sym}-{b["ts_ms"]}',
            'sym': sym, 'dir': 'Short', 'session': ses, 'score': None,
            'entry':  round(close,  6), 'stop':   round(stop_p, 6),
            'target': round(target, 6), 'exit':   exit_p,
            'resultR':    round(r, 4),
            'pnlUsd':     pnl,
            'riskUsd':    RISK_USD,
            'stopPct':    round(risk / close * 100, 3),
            'equity':     0.0,  # recalculado en main
            'reason':     reason,
            'tsMs':       b['ts_ms'],
            'ts':         b['ts_ms'] // 1000,
            'closedAt':   closed,
            'durationMin': dur,
            'rangePct':   round(rp, 4),
            'rangeBars':  RANGE_WINDOW,
            'cvdInRange': round(range_cvd, 2),
            'vr':         round(vr, 2),
            'cvdSlope': None, 'obi': None, 'dz': None,
            'priceVsVwap': None, 'funding': None,
            'isOpen': reason == 'DATA_END' and r == 0.0,
            'regime': '', 'sessionPhase': '', 'evidence': [],
            'confluenceFlags': [], 'vetoReason': '', 'rangeTouch': None,
        })
        last_sig = i

    return trades

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=14)
    args = parser.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print(json.dumps({'error': 'SUPABASE_URL / SUPABASE_KEY no configurados'}))
        sys.exit(1)

    now_ms   = int(time.time() * 1000)
    start_ms = int((time.time() - args.days * 86400) * 1000)

    # sb_earliest_ms en paralelo (5 tablas simultáneas)
    sb_first = sb_earliest_ms()

    # ── Procesar los 5 símbolos en paralelo ───────────────────────────────────
    # load_bars: Supabase fetch + Binance caché (I/O-bound → thread pool ideal)
    # detect: CPU puro pero ligero (~ms por símbolo)
    def process(sym_table):
        sym, table = sym_table
        bars = load_bars(sym, table, start_ms, sb_first)
        if len(bars) < COOLDOWN_BARS + 10:
            return [], None
        return detect(sym, bars), bars[0]['ts_ms']

    all_trades: list = []
    first_bar: int | None = None

    # max_workers=5: un thread por símbolo. Binance caché es I/O-bound.
    # Si algún símbolo necesita descarga Binance (1ª vez) corre dentro de su thread;
    # los sleeps de rate-limit se solapan con el resto → sin bloqueo total.
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(process, item): item[0] for item in TABLES.items()}
        for fut in as_completed(futs):
            trades, first_ts = fut.result()
            all_trades.extend(trades)
            if first_ts is not None and (first_bar is None or first_ts < first_bar):
                first_bar = first_ts

    # Ordenar por tiempo y recalcular equity global
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

    # Desglose por razón de salida (para diagnóstico)
    from collections import Counter
    reason_counts = Counter(t['reason'] for t in all_trades)
    reason_wr     = {}
    for reason in reason_counts:
        group = [t for t in all_trades if t['reason'] == reason]
        reason_wr[reason] = {
            'n':    len(group),
            'wins': sum(1 for t in group if t['resultR'] > 0),
            'avgR': round(sum(t['resultR'] for t in group) / len(group), 3),
        }

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
        'exit_reasons': reason_wr,
    }, ensure_ascii=False))


if __name__ == '__main__':
    main()
