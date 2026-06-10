#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
"""
Backfill historico de barras M5 desde Binance Futures a Supabase *_bars.

Calcula exactamente los mismos campos que el monitor en vivo:
  bar_delta = 2*takerBuyVol - totalVol   (exacto — viene del campo 9 de klines)
  vr        = volume / avg_30bars         (volume ratio)
  atr       = EMA14 del True Range
  session   = clasificacion UTC exacta del Rust session_tracker.rs
  vwap      = VWAP acumulado con reset diario UTC 00:00

Campos que quedan NULL (requieren order book o tick data en tiempo real):
  cvd_slope, obi_l5, obi_fast, obi_slow, dz, liq_ratio, spread_ticks,
  stacked_imb, absorption, thin_above, thin_below, bid_wall, ask_wall,
  vpin, oi_momentum, asian_high, asian_low, prev_day_high, prev_day_low,
  swing_high_50, swing_low_50, equal_high, equal_low

Uso:
    python scripts/backfill_bars.py                  # 30 dias por defecto
    python scripts/backfill_bars.py --days 60        # 60 dias
    python scripts/backfill_bars.py --symbol BTCUSDT # solo BTC
    python scripts/backfill_bars.py --dry-run        # ver cuanto fetcha sin insertar
"""
import json, os, time, sys, argparse
import urllib.request, urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# ── credenciales (lee del .env del repo raiz) ─────────────────────────────────

ROOT = Path(__file__).parent.parent
_env = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        _env[k.strip()] = v.strip().strip('"').strip("'")

SUPABASE_URL = _env.get('SUPABASE_URL', os.environ.get('SUPABASE_URL', ''))
SUPABASE_KEY = _env.get('SUPABASE_KEY', os.environ.get('SUPABASE_KEY', ''))

# ── constantes ────────────────────────────────────────────────────────────────

BINANCE_BASE = 'https://fapi.binance.com'
INTERVAL     = '1m'
INTERVAL_MS  = 1 * 60 * 1000
ATR_PERIOD   = 14
VR_PERIOD    = 30
WARMUP_BARS  = VR_PERIOD + ATR_PERIOD   # barras iniciales descartadas (indicadores no calentados)

SYMBOLS = {
    'BTCUSDT': 'btc_bars',
    'ETHUSDT': 'eth_bars',
    'BNBUSDT': 'bnb_bars',
    'SOLUSDT': 'sol_bars',
    'XRPUSDT': 'xrp_bars',
}

# ── session classifier — espejo exacto de session_tracker.rs ─────────────────
# Asia 00:00-09:00 | London 08:00-17:00 | Overlap 13:00-17:00 | NY 13:00-22:00 | OffHours 22:00-24:00

def classify_session(ts_ms: int) -> str:
    secs = (ts_ms // 1000) % 86400
    m = secs // 60
    if 780 <= m < 1020: return 'LondonNyOverlap'
    if 780 <= m < 1320: return 'NewYork'
    if 480 <= m < 1020: return 'London'
    if   0 <= m <  540: return 'Asia'
    return 'OffHours'

# ── Binance Futures klines ────────────────────────────────────────────────────

def fetch_klines(symbol: str, start_ms: int, end_ms: int) -> list:
    rows = []
    cur  = start_ms
    while cur < end_ms:
        qs = urllib.parse.urlencode({
            'symbol':    symbol,
            'interval':  INTERVAL,
            'startTime': cur,
            'endTime':   min(end_ms, cur + 1500 * INTERVAL_MS),
            'limit':     1500,
        })
        req = urllib.request.Request(
            f'{BINANCE_BASE}/fapi/v1/klines?{qs}',
            headers={'User-Agent': 'backfill-bars/1.0'},
        )
        try:
            chunk = json.loads(urllib.request.urlopen(req, timeout=30).read())
        except Exception as e:
            print(f'  [binance] error: {e}', flush=True)
            break
        if not chunk:
            break
        rows.extend(chunk)
        last_open = int(chunk[-1][0])
        if len(chunk) < 1500:
            break
        cur = last_open + INTERVAL_MS
        time.sleep(0.08)
    return rows

# ── indicadores ───────────────────────────────────────────────────────────────

def compute_rows(symbol: str, klines: list) -> list:
    out        = []
    atr_ema    = None
    prev_close = None
    vol_win    = []
    vwap_pv    = 0.0
    vwap_v     = 0.0
    vwap_day   = -1

    for k in klines:
        ts_ms     = int(k[0])
        o, h, l, c = float(k[1]), float(k[2]), float(k[3]), float(k[4])
        vol       = float(k[5])
        taker_buy = float(k[9])   # takerBuyBaseAssetVolume — campo exacto de Binance

        # bar_delta exacto: compra_agresora - venta_agresora
        bar_delta = 2.0 * taker_buy - vol

        # ATR EMA14
        tr = max(h - l, abs(h - prev_close) if prev_close else 0,
                         abs(l - prev_close) if prev_close else 0)
        atr_ema = tr if atr_ema is None else atr_ema * (ATR_PERIOD - 1) / ATR_PERIOD + tr / ATR_PERIOD
        prev_close = c

        # VR — volume ratio vs media movil 30 barras
        vol_win.append(vol)
        if len(vol_win) > VR_PERIOD:
            vol_win.pop(0)
        avg_vol = sum(vol_win) / len(vol_win) if vol_win else 1.0
        vr = vol / avg_vol if avg_vol > 0 else 0.0

        # VWAP — reset diario UTC 00:00
        dt  = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        day = dt.toordinal()
        if day != vwap_day:
            vwap_pv  = 0.0
            vwap_v   = 0.0
            vwap_day = day
        typ = (h + l + c) / 3.0
        vwap_pv += typ * vol
        vwap_v  += vol
        vwap = vwap_pv / vwap_v if vwap_v > 0 else None

        out.append({
            'symbol':    symbol,
            'ts_ms':     ts_ms,
            'session':   classify_session(ts_ms),
            'open':      round(o,         8),
            'high':      round(h,         8),
            'low':       round(l,         8),
            'close':     round(c,         8),
            'volume':    round(vol,       4),
            'bar_delta': round(bar_delta, 4),
            'vr':        round(vr,        4),
            'atr':       round(atr_ema,   8),
            'vwap':      round(vwap,      8) if vwap else None,
            # campos de order-flow que requieren datos en tiempo real — NULL en backfill
            'cvd_slope':    None,
            'obi_l5':       None,
            'obi_fast':     None,
            'obi_slow':     None,
            'dz':           None,
            'liq_ratio':    None,
            'spread_ticks': None,
            'stacked_imb':  None,
            'absorption':   None,
            'thin_above':   None,
            'thin_below':   None,
            'bid_wall':     None,
            'ask_wall':     None,
            'vpin':         None,
            'oi_momentum':  None,
            'operative':    None,
            'asian_high':   None,
            'asian_low':    None,
            'prev_day_high':None,
            'prev_day_low': None,
            'swing_high_50':None,
            'swing_low_50': None,
            'equal_high':   None,
            'equal_low':    None,
        })

    # descartar warmup — ATR y VR no estan calentados en las primeras barras
    return out[WARMUP_BARS:]

# ── Supabase insert ───────────────────────────────────────────────────────────

def sb_insert(table: str, rows: list, dry_run: bool) -> int:
    if dry_run:
        print(f'  [dry-run] {len(rows)} rows — no se inserta nada', flush=True)
        return len(rows)

    BATCH = 500
    total = 0
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i+BATCH]
        body  = json.dumps(batch).encode('utf-8')
        req   = urllib.request.Request(
            f'{SUPABASE_URL}/rest/v1/{table}',
            data=body,
            headers={
                'apikey':        SUPABASE_KEY,
                'Authorization': f'Bearer {SUPABASE_KEY}',
                'Content-Type':  'application/json',
                'Prefer':        'resolution=merge-duplicates',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                _ = r.read()
        except urllib.error.HTTPError as e:
            msg = e.read()[:200]
            print(f'  [supabase] HTTP {e.code}: {msg}', flush=True)
            continue
        except Exception as e:
            print(f'  [supabase] error: {e}', flush=True)
            continue
        total += len(batch)
        print(f'  batch {i//BATCH + 1}: +{len(batch)} rows ({total}/{len(rows)})', flush=True)
        time.sleep(0.05)
    return total

def get_earliest(table: str):
    qs  = urllib.parse.urlencode({'select': 'ts_ms', 'order': 'ts_ms.asc', 'limit': 1})
    req = urllib.request.Request(
        f'{SUPABASE_URL}/rest/v1/{table}?{qs}',
        headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
    )
    try:
        rows = json.loads(urllib.request.urlopen(req, timeout=15).read())
        return int(rows[0]['ts_ms']) if rows else None
    except Exception as e:
        print(f'  [supabase] get_earliest error: {e}', flush=True)
        return None

def get_first_live_bar(table: str):
    """
    Primer bar escrito por el monitor live (obi_l5 NOT NULL = datos de order book reales).
    Los bars del backfill tienen obi_l5=NULL. Esto separa backfill de datos live.
    """
    qs  = urllib.parse.urlencode({'select': 'ts_ms', 'obi_l5': 'not.is.null', 'order': 'ts_ms.asc', 'limit': 1})
    req = urllib.request.Request(
        f'{SUPABASE_URL}/rest/v1/{table}?{qs}',
        headers={'apikey': SUPABASE_KEY, 'Authorization': f'Bearer {SUPABASE_KEY}'},
    )
    try:
        rows = json.loads(urllib.request.urlopen(req, timeout=15).read())
        return int(rows[0]['ts_ms']) if rows else None
    except Exception as e:
        print(f'  [supabase] get_first_live_bar error: {e}', flush=True)
        return None

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    p = argparse.ArgumentParser(description='Backfill M1 bars from Binance into Supabase')
    p.add_argument('--days',    type=int, default=30,   help='Days of history to backfill (default 30)')
    p.add_argument('--symbol',  type=str, default=None, help='Single symbol (default: all 5)')
    p.add_argument('--dry-run', action='store_true',    help='Fetch from Binance but do not insert')
    p.add_argument('--force',   action='store_true',    help='Re-fetch even if DB already has data (overwrites with merge-duplicates)')
    args = p.parse_args()

    if not SUPABASE_URL or not SUPABASE_KEY:
        print('ERROR: SUPABASE_URL / SUPABASE_KEY no estan configurados')
        sys.exit(1)

    targets = {args.symbol: SYMBOLS[args.symbol]} if args.symbol else SYMBOLS
    now_ms  = int(time.time() * 1000)

    # warmup extra para que ATR y VR esten calentados desde el primer bar util
    extra_warmup = WARMUP_BARS * INTERVAL_MS
    target_start = now_ms - args.days * 86_400_000 - extra_warmup

    print(f'Backfill {args.days}d + {WARMUP_BARS} barras warmup desde Binance Futures M5')
    print(f'Objetivo: {datetime.fromtimestamp(target_start/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}'
          f' → ahora\n')

    for symbol, table in targets.items():
        print(f'── {symbol} → {table} ──────────────────────────────────', flush=True)

        earliest   = get_earliest(table)
        first_live = get_first_live_bar(table)

        if first_live is not None:
            dt_live = datetime.fromtimestamp(first_live/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            print(f'  Primer bar LIVE (obi_l5 not null): {dt_live}', flush=True)

        if earliest is not None:
            dt_e = datetime.fromtimestamp(earliest/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            print(f'  Earliest bar en BD (incl. backfill): {dt_e}', flush=True)

        if args.force:
            # Re-backfilla hasta el primer bar live para no sobreescribir microestructura real
            fetch_end = first_live if first_live else now_ms
            dt_fe = datetime.fromtimestamp(fetch_end/1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
            print(f'  --force: re-backfillando {args.days}d M1 hasta {dt_fe} (merge-duplicates)', flush=True)
        elif earliest is not None:
            if earliest <= target_start + INTERVAL_MS * 10:
                print(f'  Ya cubre los {args.days}d solicitados — saltando (usa --force para re-backfillar)', flush=True)
                continue
            fetch_end = earliest
        else:
            print(f'  Tabla vacia — fetcheando {args.days}d completos', flush=True)
            fetch_end = now_ms

        fetch_start = target_start
        n_days_req  = (fetch_end - fetch_start) / 86_400_000

        dt_s = datetime.fromtimestamp(fetch_start/1000, tz=timezone.utc).strftime('%Y-%m-%d')
        dt_en = datetime.fromtimestamp(fetch_end/1000,  tz=timezone.utc).strftime('%Y-%m-%d %H:%M')
        print(f'  Fetching {n_days_req:.1f}d: {dt_s} → {dt_en}', flush=True)

        klines = fetch_klines(symbol, fetch_start, fetch_end)
        print(f'  Binance: {len(klines)} klines recibidas', flush=True)

        if not klines:
            print(f'  Sin datos, saltando', flush=True)
            continue

        rows = compute_rows(symbol, klines)
        print(f'  Calculadas {len(rows)} barras (descartadas {WARMUP_BARS} warmup)', flush=True)

        inserted = sb_insert(table, rows, args.dry_run)
        print(f'  ✓ {inserted} filas enviadas a Supabase\n', flush=True)

    print('─────────────────────────────────────────────────')
    print('Backfill completo.')

if __name__ == '__main__':
    main()
