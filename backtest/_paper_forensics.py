"""
_paper_forensics.py — autopsia de CADA paper trade con klines históricas de Bybit.
==================================================================================
Para cada trade: baja M1/M5 de Bybit alrededor del trade y mide:
  - PRE  : cómo llegó el precio al nivel (retorno 1h antes, vela de llegada vs rango)
  - DURING: MFE_R (máx a favor) y MAE_R (máx en contra) en múltiplos de riesgo
  - clasifica: ¿lo barrieron directo (mfe bajo) o llegó cerca del parcial y revirtió?
Además resume events / footprint / snapshots / open_pos.

No usa nada del repo — solo Supabase (lee trades) + Bybit REST (klines). Correr local
(no geo-bloqueado). python backtest/_paper_forensics.py
"""
import os, json, time, urllib.request, urllib.parse
from datetime import datetime, timezone
from collections import Counter, defaultdict

SUPA = os.environ["SUPABASE_URL"].rstrip("/"); KEY = os.environ["SUPABASE_KEY"]
BYBIT = "https://api.bybit.com"
FIX_MS = int(datetime(2026, 6, 24, 19, 56, tzinfo=timezone.utc).timestamp()*1000)  # último fix


def supa(path):
    req = urllib.request.Request(f"{SUPA}/rest/v1/{path}",
        headers={"apikey": KEY, "Authorization": f"Bearer {KEY}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def klines(symbol, start_ms, end_ms, interval):
    q = urllib.parse.urlencode({"category": "linear", "symbol": symbol,
        "interval": interval, "start": start_ms, "end": end_ms, "limit": 1000})
    req = urllib.request.Request(f"{BYBIT}/v5/market/kline?{q}")
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.load(r)
            lst = d.get("result", {}).get("list", [])
            # [ts, o, h, l, c, vol, turnover] strings, newest first
            return [(int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])) for x in reversed(lst)]
        except Exception:
            time.sleep(1)
    return []


def ms(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()*1000)


def analyze(t):
    sym, side = t["symbol"], t["side"]
    entry, stop = float(t["entry"]), float(t["stop"])
    risk = abs(entry - stop) or 1e-9
    o_ms, c_ms = ms(t["opened_at"]), ms(t["closed_at"])
    dur_min = (c_ms - o_ms)/60000
    span_min = (c_ms - o_ms)/60000 + 120
    iv = "1" if span_min <= 950 else ("5" if span_min <= 4500 else "15")
    ks = klines(sym, o_ms - 60*60000, c_ms + 5*60000, iv)
    if not ks: return None
    pre  = [k for k in ks if k[0] < o_ms]
    dur  = [k for k in ks if o_ms <= k[0] <= c_ms]
    if not dur: dur = ks[-3:]
    # PRE: retorno 1h antes hacia el nivel + tamaño de la vela de llegada
    ret_1h = (entry - pre[0][4])/entry*100 if pre else 0.0      # % (negativo = vino cayendo)
    arr = pre[-1] if pre else dur[0]
    arr_range_pct = (arr[2]-arr[3])/entry*100                    # vela de llegada (% del precio)
    # DURING: MFE / MAE en R
    hi = max(k[2] for k in dur); lo = min(k[3] for k in dur)
    if side == "long":
        mfe = (hi - entry)/risk; mae = (entry - lo)/risk
    else:
        mfe = (entry - lo)/risk; mae = (hi - entry)/risk
    return dict(mfe=mfe, mae=mae, ret_1h=ret_1h, arr_range_pct=arr_range_pct,
                dur_min=dur_min, post=o_ms >= FIX_MS, bars=len(dur))


def main():
    trades = supa("liquidity_paper_trades?select=*&order=opened_at.asc&limit=2000")
    print(f"TRADES: {len(trades)}\n" + "="*120)
    print(f"{'#':>2} {'sym':<4} {'sd':<5} {'sys':<5} {'reason':<9} {'R':>6} {'MFE_R':>6} {'MAE_R':>6} "
          f"{'ret1h%':>7} {'velaLleg%':>9} {'dur_m':>6} {'fix':>4} {'kind':<8} {'vol':<5}")
    rows = []
    for i, t in enumerate(trades, 1):
        a = analyze(t)
        if a is None:
            print(f"{i:>2} {t['symbol'][:4]:<4} sin klines"); continue
        rows.append((t, a))
        print(f"{i:>2} {t['symbol'][:4]:<4} {t['side']:<5} {t['system']:<5} {t['reason']:<9} "
              f"{t['result_r']:>+6.2f} {a['mfe']:>6.2f} {a['mae']:>6.2f} {a['ret_1h']:>+7.2f} "
              f"{a['arr_range_pct']:>9.2f} {a['dur_min']:>6.0f} {'POST' if a['post'] else 'pre':>4} "
              f"{str(t.get('kind'))[:8]:<8} {str(t.get('vol_regime')):<5}")

    # ── agregados ──
    def agg(name, sel):
        rs = [(t, a) for t, a in rows if sel(t, a)]
        if not rs: print(f"  {name:<28} -"); return
        R = [t["result_r"] for t, _ in rs]; wr = 100*sum(1 for t, _ in rs if t["win"])/len(rs)
        mfe = sum(a["mfe"] for _, a in rs)/len(rs); mae = sum(a["mae"] for _, a in rs)/len(rs)
        print(f"  {name:<28} n={len(rs):>3} WR{wr:4.0f}% avgR{sum(R)/len(R):+.2f} | MFE_R {mfe:+.2f} MAE_R {mae:+.2f}")

    print("\n" + "="*120 + "\nDIAGNÓSTICO — MFE_R = cuánto llegó A FAVOR (en R) · clave para entender los stops")
    print("\n[stops: ¿los barrieron directo o llegaron cerca del parcial?]")
    agg("stops (todos)", lambda t,a: t["reason"]=="stop")
    agg("  stops PRE-fix", lambda t,a: t["reason"]=="stop" and not a["post"])
    agg("  stops POST-fix", lambda t,a: t["reason"]=="stop" and a["post"])
    print("\n[por ventana temporal]")
    agg("PRE-fix (infra rota)", lambda t,a: not a["post"])
    agg("POST-fix (sano)", lambda t,a: a["post"])
    print("\n[por símbolo]")
    for s in ["BTCUSDT","ETHUSDT","SOLUSDT"]: agg(s, lambda t,a,s=s: t["symbol"]==s)
    print("\n[por gestión]")
    for g in ["fade","trail"]: agg(g, lambda t,a,g=g: t.get("gestion")==g)

    # cuántos stops murieron con MFE casi nulo (entrada mala) vs MFE alto (gestión)
    stops = [(t,a) for t,a in rows if t["reason"]=="stop"]
    if stops:
        straight = sum(1 for _,a in stops if a["mfe"] < 0.3)
        nearmiss = sum(1 for _,a in stops if a["mfe"] >= 1.0)
        print(f"\n[anatomía de los {len(stops)} stops]")
        print(f"  barridos directo (MFE_R<0.3, entrada en nivel malo): {straight} ({100*straight//len(stops)}%)")
        print(f"  llegaron a favor (MFE_R>=1.0, casi parcial y revirtió): {nearmiss} ({100*nearmiss//len(stops)}%)")

    # ── el resto de lo guardado ──
    print("\n" + "="*120 + "\nRESTO DE DATOS GUARDADOS")
    for tbl in ["liquidity_paper_events","liquidity_paper_footprint","liquidity_paper_snapshots",
                "liquidity_paper_open_pos","liquidity_liquidations"]:
        try:
            n = supa(f"{tbl}?select=*&limit=1")
            # count via header
            req = urllib.request.Request(f"{SUPA}/rest/v1/{tbl}?select=id",
                headers={"apikey": KEY, "Authorization": f"Bearer {KEY}", "Prefer": "count=exact", "Range":"0-0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                cr = r.headers.get("content-range","?")
            print(f"  {tbl:<32} filas={cr}  ej={ (n[0] if n else {}) and list((n[0] if n else {}).keys()) }")
        except Exception as e:
            print(f"  {tbl:<32} {e}")

    # events: fill ratio real
    try:
        ev = supa("liquidity_paper_events?select=symbol,kind,event,vol_regime&limit=5000")
        c = Counter(e.get("event") for e in ev)
        print(f"\n  events por tipo: {dict(c)}")
    except Exception as e:
        print(f"  events err: {e}")


if __name__ == "__main__":
    main()
