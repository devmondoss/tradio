"""
_multivp_m5_test.py — Test 1 + Test 3
======================================
Test 1: VP multi-horizonte — agregar niveles de VP con ventanas 60 y 600 barras
         (actuales = 300 barras). Más variedad de niveles sin duplicar lógica.
Test 3: M5 como TF de señal — mismos generadores, barra de señal M5 (3× más señales).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np
import pandas as pd
import featurelab as FL
import _listas2 as L2
from _listas import FEE_MAKER, FEE_TAKER, OOS_MS
from _audit_mirror import gen_h21_short
from _strategy_ab import run_system, stats

# ── VP on-the-fly ────────────────────────────────────────────────────────────

def compute_vp(closes, volumes, window, n_bins=50):
    """Compute POC / VAH / VAL arrays para una ventana dada."""
    n = len(closes)
    poc = np.full(n, np.nan)
    vah = np.full(n, np.nan)
    val = np.full(n, np.nan)
    for i in range(window, n):
        wc = closes[i - window:i]; wv = volumes[i - window:i]
        lo, hi = wc.min(), wc.max()
        if hi - lo < 1e-6: continue
        bins = np.linspace(lo, hi, n_bins + 1)
        hist, _ = np.histogram(wc, bins=bins, weights=wv)
        pi = int(np.argmax(hist))
        poc[i] = (bins[pi] + bins[pi + 1]) / 2
        total = hist.sum(); target = total * 0.70
        lo_i = hi_i = pi; acc = hist[pi]
        while acc < target and (lo_i > 0 or hi_i < n_bins - 1):
            al = hist[lo_i - 1] if lo_i > 0 else -1
            ah = hist[hi_i + 1] if hi_i < n_bins - 1 else -1
            if ah >= al: hi_i += 1; acc += ah
            else:        lo_i -= 1; acc += al
        val[i] = (bins[lo_i] + bins[lo_i + 1]) / 2
        vah[i] = (bins[hi_i] + bins[hi_i + 1]) / 2
    return poc, vah, val


def inject_vp(a, closes, volumes, window):
    """Inyecta vp_poc/vah/val extra en el objeto a con sufijo _w{window}."""
    poc, vah, val = compute_vp(closes, volumes, window)
    setattr(a, f'vp_poc_{window}', poc)
    setattr(a, f'vp_vah_{window}', vah)
    setattr(a, f'vp_val_{window}', val)


# ── Generadores multi-VP ─────────────────────────────────────────────────────

def gen_multivp(window_suffix, K=15, tol=0.002):
    """
    Versión de gen_h21 que usa VP de ventana alternativa.
    Entrada en POC defendido >=2 veces, target en VAH/VAL de la misma ventana.
    """
    poc_attr = f'vp_poc_{window_suffix}'
    vah_attr = f'vp_vah_{window_suffix}'
    val_attr = f'vp_val_{window_suffix}'

    def _long(a, i):
        poc_arr = getattr(a, poc_attr, None)
        if poc_arr is None: return None
        win = poc_arr[max(0, i - K):i]
        win = win[np.isfinite(win)]
        if len(win) < 3: return None
        poc = poc_arr[i]
        if not np.isfinite(poc): return None
        near = np.abs(win - poc) / (poc + 1e-9) < tol
        if near.sum() < 2: return None
        if not (a.l[i] <= poc <= a.h[i]): return None
        vah = getattr(a, vah_attr)[i]
        stop = poc - 0.5 * a.atr[i]
        if not (np.isfinite(vah) and vah > poc): return None
        return [("long", poc, stop, None, vah, f"mVP{window_suffix}L")]

    def _short(a, i):
        poc_arr = getattr(a, poc_attr, None)
        if poc_arr is None: return None
        win = poc_arr[max(0, i - K):i]
        win = win[np.isfinite(win)]
        if len(win) < 3: return None
        poc = poc_arr[i]
        if not np.isfinite(poc): return None
        near = np.abs(win - poc) / (poc + 1e-9) < tol
        if near.sum() < 2: return None
        if not (a.l[i] <= poc <= a.h[i]): return None
        val = getattr(a, val_attr)[i]
        stop = poc + 0.5 * a.atr[i]
        if not (np.isfinite(val) and val < poc): return None
        return [("short", poc, stop, None, val, f"mVP{window_suffix}S")]

    def gen(a, i):
        r = []
        l = _long(a, i)
        s = _short(a, i)
        if l: r += l
        if s: r += s
        return r or None

    return gen


# ── Loader M5 ────────────────────────────────────────────────────────────────

M5_PATHS = {
    'BTCUSDT': 'data/bybit-perp/processed/btcusdt_perp_m5.parquet',
}

def load_m5(sym):
    """Carga M5 parquet como objeto A2-like para usar con run_system(tf_min=5)."""
    path = M5_PATHS.get(sym)
    if not path or not Path(path).exists():
        return None, None

    needed = ["ts_ms", "open", "high", "low", "close", "volume", "delta", "cvd",
              "cvd_div", "cvd_slope", "dz", "vr", "regime", "vp_poc", "vp_vah",
              "vp_val", "vp_lvn_below", "prev_day_high", "prev_day_low",
              "big_trade_bullish", "big_trade_bearish", "abs_bid", "abs_ask",
              "buy_vol", "sell_vol", "swing_high_50", "swing_low_50",
              "equal_high", "equal_low", "fp_poc", "fp_absorb_buy",
              "fp_absorb_sell", "weekly_high", "weekly_low",
              "asian_high", "asian_low", "spread_mean", "obi5_mean", "obi10_mean",
              "ask_wall", "bid_wall", "thin_above", "thin_below", "vpin",
              "h4_bearish", "h1_bos_bear", "h1_bos_bull",
              "h1_choch_bear", "h1_choch_bull", "bearish_fvg_active",
              "near_bearish_ob", "ote_62", "fib_ote", "displacement_bear"]

    avail = pd.read_parquet(path, columns=["ts_ms"]).columns.tolist()
    cols = [c for c in needed if c in pd.read_parquet(path, columns=["ts_ms"]).columns
            ] if False else needed  # just try all

    df = pd.read_parquet(path).sort_values("ts_ms").reset_index(drop=True)
    # ATR manual
    h, l, c = df.high.values, df.low.values, df.close.values
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    k = 2 / 15
    atr = np.zeros(len(df)); atr[0] = tr[0]
    for i in range(1, len(df)): atr[i] = tr[i] * k + atr[i-1] * (1 - k)
    df['atr'] = atr
    df['day'] = (df.ts_ms // 86_400_000).astype(int)
    if 'max_trade' not in df.columns: df['max_trade'] = 0.0
    if 'h4_bos_bear' not in df.columns: df['h4_bos_bear'] = False

    # Rellenar columnas opcionales que falten
    for col in ['h1_bos_bear','h1_bos_bull','h1_choch_bear','h1_choch_bull',
                'displacement_bear','near_bearish_ob','ote_62','fib_ote']:
        if col not in df.columns: df[col] = False

    class A5:
        pass
    a5 = A5()
    for col in df.columns:
        setattr(a5, col, df[col].values)
    a5.n   = len(df)
    a5.ts  = df.ts_ms.values.astype(np.int64)
    a5.o   = df.open.values.astype(float)
    a5.h   = df.high.values.astype(float)
    a5.l   = df.low.values.astype(float)
    a5.c   = df.close.values.astype(float)
    a5.reg = df.regime.astype(str).values
    a5.day = df.day.values

    # M1 exits (reutilizamos BTC M1)
    m1_path = 'data/bybit-perp/processed/btcusdt_perp_m1.parquet'
    m1df = pd.read_parquet(m1_path, columns=['ts_ms','high','low','close']).sort_values('ts_ms')
    m1 = (m1df.ts_ms.values.astype(np.int64), m1df.high.values.astype(float),
          m1df.low.values.astype(float), m1df.close.values.astype(float))
    return a5, m1


# ── Main ─────────────────────────────────────────────────────────────────────

BASE_GENS = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
PARAMS = dict(trail_atr=6.0, stop_scale=0.8, mode="routed", volfilter=True,
              cooldown=6, max_day=2, tp2_cap_r=0.0, use_partial=True, p1_frac=0.5,
              timeout_min=24*60, stop_floor_pct=0.15, min_range=0.5)


def report(label, rows, base_rows):
    print(f"\n{'─'*70}")
    print(f"  {label}")
    print(f"{'sym':<8} {'n':>5} {'n_oos':>6} {'IS_avgR':>8} {'OOS_avgR':>9} {'WR':>5} {'DD':>5}  Δn_oos  ΔOOS_R")
    for r, b in zip(rows, base_rows):
        dn = r['n_oos'] - b['n_oos']; dr = r['oosR'] - b['oosR']
        ok = "✓" if r['oosR'] >= b['oosR'] - 0.05 else "✗"
        print(f"{r['sym']:<8} {r['n']:>5} {r['n_oos']:>6} {r['avgR']:>+8.3f} {r['oosR']:>+9.3f} "
              f"{r['wr']:>4.0f}% {r['dd']:>4.1f}%  {dn:>+6}  {dr:>+6.3f}  {ok}")
    avg = sum(r['oosR'] for r in rows) / len(rows)
    bavg = sum(b['oosR'] for b in base_rows) / len(base_rows)
    print(f"  Portfolio OOS: {avg:>+.3f}  (base: {bavg:>+.3f}  Δ={avg-bavg:>+.3f})")


if __name__ == "__main__":
    # ── BASE ─────────────────────────────────────────────────────────────────
    print("Calculando base M15 (cd=6, md=2, gens: h5+h21+h21s)...")
    base_rows = []
    for sym in FL.ASSETS:
        if not Path(FL.ASSETS[sym]).exists(): continue
        a, m1 = FL.load(sym)
        df = run_system(a, BASE_GENS, m1, tf_min=15, **PARAMS)
        s = stats(df); o = df[df.oos]
        base_rows.append(dict(sym=sym, n=s['n'], n_oos=len(o), avgR=s['avgR'],
                              oosR=s['oosA'], wr=s['wr'], dd=s['dd']))
    for r in base_rows: print(f"  {r['sym']}: n_oos={r['n_oos']} oosR={r['oosR']:+.3f}")

    # ── TEST 1a: VP-60 (ventana corta ~15h) añadida como nivel extra ─────────
    print("\nPrecomputando VP w=60 y w=600...")
    vp60_rows = []
    for sym in FL.ASSETS:
        if not Path(FL.ASSETS[sym]).exists(): continue
        a, m1 = FL.load(sym)
        inject_vp(a, a.c, a.volume, 60)
        inject_vp(a, a.c, a.volume, 600)
        gens_60 = BASE_GENS + [gen_multivp(60)]
        df = run_system(a, gens_60, m1, tf_min=15, **PARAMS)
        s = stats(df); o = df[df.oos]
        vp60_rows.append(dict(sym=sym, n=s['n'], n_oos=len(o), avgR=s['avgR'],
                              oosR=s['oosA'], wr=s['wr'], dd=s['dd']))

    report("TEST 1a: Base + VP-60 (ventana 15h corta)", vp60_rows, base_rows)

    # ── TEST 1b: VP-600 (ventana larga ~6d) ─────────────────────────────────
    vp600_rows = []
    for sym in FL.ASSETS:
        if not Path(FL.ASSETS[sym]).exists(): continue
        a, m1 = FL.load(sym)
        inject_vp(a, a.c, a.volume, 600)
        gens_600 = BASE_GENS + [gen_multivp(600)]
        df = run_system(a, gens_600, m1, tf_min=15, **PARAMS)
        s = stats(df); o = df[df.oos]
        vp600_rows.append(dict(sym=sym, n=s['n'], n_oos=len(o), avgR=s['avgR'],
                               oosR=s['oosA'], wr=s['wr'], dd=s['dd']))

    report("TEST 1b: Base + VP-600 (ventana 6d larga)", vp600_rows, base_rows)

    # ── TEST 1c: VP-60 + VP-600 juntos ──────────────────────────────────────
    vp_both_rows = []
    for sym in FL.ASSETS:
        if not Path(FL.ASSETS[sym]).exists(): continue
        a, m1 = FL.load(sym)
        inject_vp(a, a.c, a.volume, 60)
        inject_vp(a, a.c, a.volume, 600)
        gens_both = BASE_GENS + [gen_multivp(60), gen_multivp(600)]
        df = run_system(a, gens_both, m1, tf_min=15, **PARAMS)
        s = stats(df); o = df[df.oos]
        vp_both_rows.append(dict(sym=sym, n=s['n'], n_oos=len(o), avgR=s['avgR'],
                                 oosR=s['oosA'], wr=s['wr'], dd=s['dd']))

    report("TEST 1c: Base + VP-60 + VP-600 (ambos)", vp_both_rows, base_rows)

    # ── TEST 3: M5 como TF de señal (solo BTC por ahora) ────────────────────
    print("\n" + "=" * 70)
    print("TEST 3: M5 como TF de señal (BTC)")
    a5, m1_5 = load_m5('BTCUSDT')
    if a5 is not None:
        df5 = run_system(a5, BASE_GENS, m1_5, tf_min=5, **PARAMS)
        s5 = stats(df5); o5 = df5[df5.oos]
        b = base_rows[0]  # BTC base
        print(f"\n{'':8} {'n':>5} {'n_oos':>6} {'IS_avgR':>8} {'OOS_avgR':>9} {'WR':>5} {'DD':>5}")
        print(f"M15 base {'':0} {b['n']:>5} {b['n_oos']:>6} {b['avgR']:>+8.3f} {b['oosR']:>+9.3f} {b['wr']:>4.0f}% {b['dd']:>4.1f}%")
        print(f"M5  señal {'':0} {s5['n']:>5} {len(o5):>6} {s5['avgR']:>+8.3f} {s5['oosA']:>+9.3f} {s5['wr']:>4.0f}% {s5['dd']:>4.1f}%")
        print(f"  Δ señales OOS: {len(o5)-b['n_oos']:>+5}")
        print(f"  Δ avgR OOS:    {s5['oosA']-b['oosR']:>+.3f}")
    else:
        print("  M5 parquet no disponible para BTC")
