#!/usr/bin/env python3
"""
MTF Audit — extrae mtf_trades de Supabase y genera breakdown completo.
Uso: python scripts/mtf_audit.py [--days 30]
"""
import json, os, sys, urllib.request, argparse
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).parent.parent
_env = {}
for line in (ROOT / '.env').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        _env[k.strip()] = v.strip().strip('"').strip("'")

SUPABASE_URL = _env.get('SUPABASE_URL', os.environ.get('SUPABASE_URL', ''))
SUPABASE_KEY = _env.get('SUPABASE_KEY', os.environ.get('SUPABASE_KEY', ''))

COLS = ('id,symbol,sig,session,direction,d1_trend,entry,stop,target,stop_pct,'
        'is_open,result_r,gross_r,fee_r,reason,exit_price,duration_bars,'
        'entry_at,closed_at,obi_entry,cvd_slope_entry,dz_score,stacked_imb,equal_low')

def fetch(days: int) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    import urllib.parse
    params = urllib.parse.urlencode({
        'select': COLS,
        'entry_at': f'gte.{since}',
        'order': 'entry_at.asc',
        'limit': '2000',
    })
    url = f'{SUPABASE_URL}/rest/v1/mtf_trades?{params}'
    req = urllib.request.Request(url, headers={
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
    })
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())

def stats(rows: list[dict]) -> dict:
    if not rows:
        return {'n': 0, 'wr': 0.0, 'avgR': 0.0, 'totalR': 0.0, 'minR': 0.0, 'maxR': 0.0}
    n     = len(rows)
    rs    = [r['result_r'] for r in rows]
    wins  = [v for v in rs if v is not None and v > 0]
    total = sum(v for v in rs if v is not None)
    return {
        'n':      n,
        'wr':     round(len(wins) / n * 100, 1),
        'avgR':   round(total / n, 3),
        'totalR': round(total, 2),
        'minR':   round(min((v for v in rs if v is not None), default=0), 3),
        'maxR':   round(max((v for v in rs if v is not None), default=0), 3),
    }

def sep(title: str):
    print(f'\n{"="*64}')
    print(f'  {title}')
    print('='*64)

def tbl(headers: list, rows: list, widths: list):
    fmt = '  ' + '  '.join(f'{{:<{w}}}' for w in widths)
    print(fmt.format(*headers))
    print('  ' + '  '.join('-'*w for w in widths))
    for row in rows:
        print(fmt.format(*[str(x) for x in row]))

def session_label(s: str) -> str:
    return {'London': 'London', 'LondonNyOverlap': 'Overlap',
            'NewYork': 'NY', 'Asia': 'Asia', 'OffHours': 'Off'}.get(s, s)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=30)
    args = ap.parse_args()

    print(f'Extrayendo mtf_trades de los últimos {args.days} días…')
    rows = fetch(args.days)
    print(f'  Total filas: {len(rows)}')

    closed = [r for r in rows if not r['is_open'] and r['result_r'] is not None]
    open_  = [r for r in rows if r['is_open']]

    s_all = stats(closed)
    shorts = [r for r in closed if (r.get('direction') or 'Short') == 'Short']
    longs  = [r for r in closed if (r.get('direction') or 'Short') == 'Long']

    sep('RESUMEN GLOBAL')
    print(f'  Cerrados: {s_all["n"]}   Abiertos: {len(open_)}')
    print(f'  WR:       {s_all["wr"]}%')
    print(f'  Avg R:    {s_all["avgR"]:+.3f}R')
    print(f'  Total R:  {s_all["totalR"]:+.2f}R')
    ss = stats(shorts); sl = stats(longs)
    print(f'  Shorts:   n={ss["n"]}  WR={ss["wr"]}%  AvgR={ss["avgR"]:+.3f}R  Total={ss["totalR"]:+.2f}R')
    print(f'  Longs:    n={sl["n"]}  WR={sl["wr"]}%  AvgR={sl["avgR"]:+.3f}R  Total={sl["totalR"]:+.2f}R')

    # ── 1. Por dirección × sesión ────────────────────────────────────────────
    sep('1. DIRECCIÓN × SESIÓN')
    by_dir_ses: dict = defaultdict(list)
    for r in closed:
        key = f'{(r.get("direction") or "Short"):<6} × {session_label(r.get("session","?"))}'
        by_dir_ses[key].append(r)
    rows_t = []
    for key, ts in sorted(by_dir_ses.items(), key=lambda x: -stats(x[1])['totalR']):
        s = stats(ts)
        rows_t.append([key, s['n'], f'{s["wr"]}%', f'{s["avgR"]:+.3f}R', f'{s["totalR"]:+.2f}R'])
    tbl(['Dirección × Sesión','n','WR%','AvgR','TotalR'], rows_t, [22,4,6,8,8])

    # ── 2. Por símbolo ───────────────────────────────────────────────────────
    sep('2. POR SÍMBOLO')
    by_sym: dict = defaultdict(list)
    for r in closed:
        by_sym[r['symbol'].replace('USDT','')].append(r)
    rows_t = []
    for sym, ts in sorted(by_sym.items(), key=lambda x: -stats(x[1])['totalR']):
        s = stats(ts)
        rows_t.append([sym, s['n'], f'{s["wr"]}%', f'{s["avgR"]:+.3f}R', f'{s["totalR"]:+.2f}R',
                       f'{s["minR"]:+.3f}', f'{s["maxR"]:+.3f}'])
    tbl(['Símbolo','n','WR%','AvgR','TotalR','MinR','MaxR'], rows_t, [6,4,6,8,8,8,8])

    # ── 3. Por patrón (sig) ──────────────────────────────────────────────────
    sep('3. POR PATRÓN (sig)')
    by_sig: dict = defaultdict(list)
    for r in closed:
        by_sig[r['sig'] or '?'].append(r)
    rows_t = []
    for sig, ts in sorted(by_sig.items(), key=lambda x: -stats(x[1])['totalR']):
        s = stats(ts)
        if s['n'] < 2:
            continue
        rows_t.append([sig, s['n'], f'{s["wr"]}%', f'{s["avgR"]:+.3f}R', f'{s["totalR"]:+.2f}R'])
    tbl(['Patrón','n','WR%','AvgR','TotalR'], rows_t, [28,4,6,8,8])

    # ── 4. Por hora UTC ──────────────────────────────────────────────────────
    sep('4. POR HORA UTC DE ENTRADA')
    by_hr: dict = defaultdict(list)
    for r in closed:
        try:
            h = datetime.fromisoformat(r['entry_at'].replace('Z','+00:00')).hour
        except Exception:
            h = -1
        by_hr[h].append(r)
    rows_t = []
    for h in sorted(by_hr):
        ts = by_hr[h]
        s  = stats(ts)
        ses = 'London' if 7<=h<12 else 'NY' if 13<=h<17 else 'Overlap' if 12<=h<13 else 'Other'
        rows_t.append([f'{h:02d}:xx', ses, s['n'], f'{s["wr"]}%', f'{s["avgR"]:+.3f}R', f'{s["totalR"]:+.2f}R'])
    tbl(['Hora UTC','Sesión','n','WR%','AvgR','TotalR'], rows_t, [8,8,4,6,8,8])

    # ── 5. Por exit reason ───────────────────────────────────────────────────
    sep('5. EXIT REASONS')
    by_rsn: dict = defaultdict(list)
    for r in closed:
        by_rsn[r.get('reason') or '?'].append(r)
    rows_t = []
    for rsn, ts in sorted(by_rsn.items(), key=lambda x: -len(x[1])):
        s = stats(ts)
        rows_t.append([rsn, s['n'], f'{s["n"]/len(closed)*100:.1f}%',
                       f'{s["avgR"]:+.3f}R', f'{s["totalR"]:+.2f}R'])
    tbl(['Reason','n','%total','AvgR','TotalR'], rows_t, [18,4,7,8,8])

    # ── 6. Por bucket de stop_pct ────────────────────────────────────────────
    sep('6. STOP_PCT BUCKETS')
    buckets = [('0.30–0.50%', 0.30, 0.50),
               ('0.50–0.60%', 0.50, 0.60),
               ('0.60–0.75%', 0.60, 0.75)]
    rows_t = []
    for label, lo, hi in buckets:
        ts = [r for r in closed if r.get('stop_pct') is not None and lo <= r['stop_pct'] < hi]
        if not ts:
            continue
        s = stats(ts)
        rows_t.append([label, s['n'], f'{s["wr"]}%', f'{s["avgR"]:+.3f}R', f'{s["totalR"]:+.2f}R'])
    tbl(['Bucket','n','WR%','AvgR','TotalR'], rows_t, [12,4,6,8,8])

    # ── 7. Drawdown / rachas ─────────────────────────────────────────────────
    sep('7. DRAWDOWN Y RACHAS')
    sorted_closed = sorted(closed, key=lambda r: r['entry_at'])
    equity = 0.0
    peak   = 0.0
    max_dd = 0.0
    streak = 0
    max_loss_streak = 0
    cur_streak = 0
    cur_dir = None
    for r in sorted_closed:
        rv = r['result_r'] or 0.0
        equity += rv
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
        # racha
        d = 'W' if rv > 0 else 'L'
        if d == cur_dir:
            cur_streak += 1
        else:
            cur_dir = d
            cur_streak = 1
        if d == 'L' and cur_streak > max_loss_streak:
            max_loss_streak = cur_streak

    print(f'  Max drawdown:       {max_dd:.2f}R')
    print(f'  Max racha perdedora: {max_loss_streak} trades')
    print(f'  Equity final (R):   {equity:+.2f}R')

    # ── 8. OBI / CVD / DZ en ganadores vs perdedores ────────────────────────
    sep('8. MICROESTRUCTURA — ganadores vs perdedores')
    def mean(lst):
        return round(sum(lst)/len(lst), 3) if lst else None
    def pct_true(lst):
        return round(sum(1 for v in lst if v)/len(lst)*100, 1) if lst else None

    wins_c  = [r for r in closed if r['result_r'] is not None and r['result_r'] > 0]
    loss_c  = [r for r in closed if r['result_r'] is not None and r['result_r'] <= 0]

    for label, group in [('WINS', wins_c), ('LOSS', loss_c)]:
        obi  = [r['obi_entry']       for r in group if r.get('obi_entry')       is not None]
        cvd  = [r['cvd_slope_entry'] for r in group if r.get('cvd_slope_entry') is not None]
        dz   = [r['dz_score']        for r in group if r.get('dz_score')        is not None]
        eql  = [r['equal_low']       for r in group if r.get('equal_low')       is not None]
        print(f'\n  {label} (n={len(group)}):')
        print(f'    obi_entry avg:       {mean(obi)}')
        print(f'    cvd_slope avg:       {mean(cvd)}')
        print(f'    dz_score avg:        {mean(dz)}')
        print(f'    equal_low rate:      {pct_true(eql)}%')

    # ── 9. Trade listing reciente ────────────────────────────────────────────
    sep('9. ÚLTIMOS 20 TRADES (más recientes primero)')
    recent = sorted(closed, key=lambda r: r['entry_at'], reverse=True)[:20]
    rows_t = []
    for r in recent:
        dt = r['entry_at'][:16].replace('T',' ')
        rv = r['result_r'] or 0.0
        rows_t.append([
            dt,
            r['symbol'].replace('USDT',''),
            (r.get('direction') or 'S')[0],
            r['sig'] or '?',
            session_label(r.get('session','')),
            f'{rv:+.3f}R',
            r.get('reason','?')[:12],
        ])
    tbl(['Entrada UTC','Sym','D','Patrón','Ses','R','Reason'],
        rows_t, [16,4,1,28,7,8,14])

    print()

if __name__ == '__main__':
    main()
