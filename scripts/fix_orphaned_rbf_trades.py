"""
Cierra los trades RBF huerfanos (is_active=False, result_r IS NULL).
Para cada uno, descarga los bars M1 desde Supabase y simula el outcome
con la misma logica del paper trader (stop, target, trailing, time stop).
"""
import json, sys, urllib.request, urllib.parse
from datetime import datetime, timezone

URL = 'https://ztdhvmcisjjyhbqlgkzm.supabase.co'
KEY = (
    'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9'
    '.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inp0ZGh2bWNpc2pqeWhicWxna3ptIiwicm9sZSI6'
    'InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc3ODk0MTc1MiwiZXhwIjoyMDk0NTE3NzUyfQ'
    '.sqMh9Jcxrxyg-ZBYWPaNN8DB9kf-KkC7ARPLucItN1Y'
)
HDRS = {'apikey': KEY, 'Authorization': f'Bearer {KEY}', 'Content-Type': 'application/json'}

SYM_TABLE = {
    'BTCUSDT': 'btc_bars', 'ETHUSDT': 'eth_bars',
    'BNBUSDT': 'bnb_bars', 'SOLUSDT': 'sol_bars', 'XRPUSDT': 'xrp_bars',
}

# ── sim params (mirrors rbf_paper.rs) ─────────────────────────────────────────
TRAIL_ACTIVATE_R = 1.5
TRAIL_ATR_K      = 1.2
TIME_STOP_BARS   = 15


def sb_get(path):
    req = urllib.request.Request(URL + path, headers=HDRS)
    return json.loads(urllib.request.urlopen(req).read())


def sb_patch(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        URL + path, data=data, method='PATCH',
        headers={**HDRS, 'Prefer': 'return=minimal'},
    )
    urllib.request.urlopen(req)


def fetch_bars(sym, from_ms, limit=120):
    table = SYM_TABLE.get(sym)
    if not table:
        return []
    path = (
        f'/rest/v1/{table}?ts_ms=gte.{from_ms}'
        f'&order=ts_ms.asc&limit={limit}'
        f'&select=ts_ms,open,high,low,close,atr'
    )
    return sb_get(path)


def simulate(bars, entry, stop, target, atr_entry, direction):
    """Returns (result_r, exit_price, exit_reason, exit_ms)"""
    is_short    = direction == 'Short'
    risk        = abs(stop - entry)
    best        = entry
    trail_on    = False
    trail_stop  = stop
    current_atr = atr_entry

    for k, b in enumerate(bars):
        hi = b['high']; lo = b['low']; cl = b['close']
        bar_atr = b.get('atr') or 0.0
        if bar_atr > 0:
            current_atr = bar_atr

        eff_stop = trail_stop if trail_on else stop

        # stop check (Short: high >= stop, Long: low <= stop)
        stop_hit   = hi >= eff_stop if is_short else lo <= eff_stop
        target_hit = lo <= target   if is_short else hi >= target

        if stop_hit or target_hit:
            if stop_hit:
                exit_p  = eff_stop
                reason  = 'TRAILING_STOP' if trail_on else 'STOP'
            else:
                exit_p  = target
                reason  = 'TARGET'
            pnl    = (entry - exit_p) if is_short else (exit_p - entry)
            return pnl / risk, exit_p, reason, b['ts_ms']

        # update best extreme
        if is_short:
            if lo < best:
                best = lo
            fav_r = (entry - best) / risk
        else:
            if hi > best:
                best = hi
            fav_r = (best - entry) / risk

        if fav_r >= TRAIL_ACTIVATE_R and not trail_on:
            trail_on = True

        if trail_on and current_atr > 0:
            if is_short:
                candidate = best + TRAIL_ATR_K * current_atr
                if candidate < trail_stop:
                    trail_stop = candidate
            else:
                candidate = best - TRAIL_ATR_K * current_atr
                if candidate > trail_stop:
                    trail_stop = candidate

        # time stop: bar 15+ in loss -> close at close price
        if k + 1 >= TIME_STOP_BARS:
            current_pnl = (entry - cl) if is_short else (cl - entry)
            if current_pnl < 0:
                pnl = (entry - cl) if is_short else (cl - entry)
                return pnl / risk, cl, 'TIME_STOP', b['ts_ms']

    return None, None, 'NO_DATA', None


def main(dry_run=True):
    print(f"{'[DRY RUN] ' if dry_run else ''}Cargando trades huerfanos...")

    orphans = sb_get(
        '/rest/v1/rbf_signals?is_active=eq.false&result_r=is.null'
        '&status=eq.OPEN&order=timestamp_ms.asc'
        '&select=id,symbol,direction,timestamp_ms,entry_price,stop_price,target_price'
    )
    print(f"Encontrados: {len(orphans)} huerfanos\n")

    fixed = 0
    no_data = 0

    for t in orphans:
        tid     = t['id']
        sym     = t['symbol']
        dir_    = t['direction']
        ts_ms   = t['timestamp_ms']
        entry   = t['entry_price']
        stop    = t['stop_price']
        target  = t['target_price']
        atr_est = abs(stop - entry)   # ATR_STOP_K=1.0, so atr = |stop-entry|

        ts_str = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime('%m-%d %H:%M')
        age_h  = (datetime.now(timezone.utc).timestamp() - ts_ms / 1000) / 3600

        bars = fetch_bars(sym, ts_ms + 60_000)   # +1 min = primer bar POST-entrada

        if not bars:
            print(f"  id={tid} {ts_str} {sym} {dir_:5} age={age_h:.0f}h  -> SIN DATOS (demasiado antiguo?)")
            no_data += 1
            continue

        result_r, exit_p, reason, exit_ms = simulate(bars, entry, stop, target, atr_est, dir_)

        if reason == 'NO_DATA':
            print(f"  id={tid} {ts_str} {sym} {dir_:5} age={age_h:.0f}h  -> SIN SUFICIENTES BARS")
            no_data += 1
            continue

        exit_dt  = datetime.fromtimestamp(exit_ms / 1000, tz=timezone.utc).isoformat()
        rr_str   = f"{result_r:+.3f}R"
        print(f"  id={tid} {ts_str} {sym} {dir_:5} age={age_h:.0f}h  -> {reason:14} exit={exit_p:.4f} {rr_str}")

        if not dry_run:
            sb_patch(
                f'/rest/v1/rbf_signals?id=eq.{tid}',
                {
                    'result_r':   round(result_r, 6),
                    'exit_price': exit_p,
                    'exit_reason': reason,
                    'closed_at':  exit_dt,
                    'status':     'CLOSED',
                }
            )
            fixed += 1

    print(f"\n{'Simulados' if dry_run else 'Cerrados'}: {len(orphans) - no_data}/{len(orphans)}")
    if no_data:
        print(f"Sin datos (muy antiguos): {no_data}")
    if dry_run:
        print("\nEjecuta con --apply para escribir en Supabase.")


if __name__ == '__main__':
    dry = '--apply' not in sys.argv
    main(dry_run=dry)
