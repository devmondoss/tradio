"""
_scalp_abs.py — scalper de ABSORCIÓN por EVENTO (no por nivel diario) → frecuencia real.
================================================================================
Cambia el disparador: en vez de esperar a que el precio toque el POC/VAH/VAL del día,
detecta la absorción DONDE OCURRE en el flujo — cualquier barra con:
  · alto volumen (vr)            ← DISPARADOR
  · delta footprint en CONTRA    ← agresión de un lado
  · el precio RECHAZA el extremo (cierra de vuelta, mecha)  ← absorción/fallo
y fadea con límite maker en un pullback. Maneja fill hacia adelante (cola realista).

Perillas (todas las condiciones que pediste):
  DISPARADOR : vr_min, delta_min (|delta|/vol)
  CONFIRMADOR: rej (cierre dentro del rango), wick_min (mecha mínima), rev (vela de giro)
  CONTEXTO   : ext_w (barra en extremo local de W barras)
  BLOQUEADOR : block_trend (no fadear tendencia fuerte), max_consec
  ENTRADA    : depth_atr (pullback del límite), fill_win (barras para llenar)
  TARGET/SAL : rr, stop_atr, mgmt (fixed/fade)

Reporta PER ASSET: n, trades/día, fill%, IS, OOS.
Uso: python backtest/_scalp_abs.py [tf]
"""
import sys, itertools
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
from _scalp import load as sc_load, load_m1_exit as sc_m1exit, stats, FEE_MK, FEE_TK, OOS_MS

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def precompute_anchor(s, k=3, W=80, tol_atr=0.25):
    """Mapa DENSO de niveles causales: pivots (±k barras) recientes + estructura del frame.
    Devuelve sup_near[i]/res_near[i]: ¿el low/high de la barra i está cerca de un soporte/
    resistencia reciente? (pivot low/high de las últimas W barras, o nivel VP/swing/PDH)."""
    n = s.n; atr = s.atr
    piv_lo = np.full(n, np.nan); piv_hi = np.full(n, np.nan)
    for j in range(k, n-k):
        if s.l[j] == np.min(s.l[j-k:j+k+1]): piv_lo[j] = s.l[j]
        if s.h[j] == np.max(s.h[j-k:j+k+1]): piv_hi[j] = s.h[j]
    struct_lo = [getattr(s, a) for a in ("vp_val","vp_poc","swing_low_50","prev_day_low","weekly_low")]
    struct_hi = [getattr(s, a) for a in ("vp_vah","vp_poc","swing_high_50","prev_day_high","weekly_high")]
    sup = np.zeros(n, bool); res = np.zeros(n, bool)
    for i in range(W, n):
        tol = tol_atr*atr[i]
        rl = piv_lo[i-W:i-k]; rl = rl[np.isfinite(rl)]
        rh = piv_hi[i-W:i-k]; rh = rh[np.isfinite(rh)]
        lv_lo = np.concatenate([rl, [a[i] for a in struct_lo if np.isfinite(a[i])]]) if len(rl) or True else rl
        lv_hi = np.concatenate([rh, [a[i] for a in struct_hi if np.isfinite(a[i])]])
        if lv_lo.size and np.min(np.abs(lv_lo - s.l[i])) <= tol: sup[i] = True
        if lv_hi.size and np.min(np.abs(lv_hi - s.h[i])) <= tol: res[i] = True
    return sup, res


def gen_abs(vr_min=1.5, delta_min=0.15, rej=0.6, wick_min=0.0, rev=False,
            ext_w=0, block_trend=False, depth_atr=0.1, stop_atr=0.5, rr=1.5,
            sup=None, res=None):
    """Evento de absorción → candidato (side, entry_px, stop, tp). Causal: todo en barra i.
    sup/res: si se pasan, exige que el rechazo esté EN un nivel (mapa denso de pivots)."""
    TREND = ("trendup", "trenddown", "expansion")
    def g(s, i):
        out = []
        h, l, c, o = s.h[i], s.l[i], s.c[i], s.o[i]
        atr = s.atr[i]; rng = h - l
        if rng <= 0 or atr <= 0 or s.vr[i] < vr_min: return out
        if block_trend and str(s.reg[i]).lower() in TREND: return out
        fpd = getattr(s, "fp_delta", None); vol = getattr(s, "fp_vol", None)
        if fpd is None or not np.isfinite(fpd[i]): return out
        v = vol[i] if (vol is not None and np.isfinite(vol[i]) and vol[i] > 0) else (s.volume[i] + 1e-9)
        dn = fpd[i] / v                       # delta normalizado (−1=todo venta, +1=todo compra)
        clo = (c - l) / rng                   # posición del cierre (0=low, 1=high)
        lo_wick = (min(o, c) - l) / rng; hi_wick = (h - max(o, c)) / rng
        # LONG: venta agresora (dn<0) ABSORBIDA → low rechazado (cierre arriba, mecha inferior)
        long_ok = dn <= -delta_min and clo >= rej and lo_wick >= wick_min
        if rev: long_ok = long_ok and c > o
        if ext_w > 0 and i >= ext_w: long_ok = long_ok and l <= np.nanmin(s.l[i-ext_w:i])
        if sup is not None: long_ok = long_ok and sup[i]
        if long_ok:
            entry = c - depth_atr*atr; stop = l - stop_atr*atr; risk = entry - stop
            if risk > 0: out.append(("long", entry, stop, entry + rr*risk))
        short_ok = dn >= delta_min and clo <= 1 - rej and hi_wick >= wick_min
        if rev: short_ok = short_ok and c < o
        if ext_w > 0 and i >= ext_w: short_ok = short_ok and h >= np.nanmax(s.h[i-ext_w:i])
        if res is not None: short_ok = short_ok and res[i]
        if short_ok:
            entry = c + depth_atr*atr; stop = h + stop_atr*atr; risk = stop - entry
            if risk > 0: out.append(("short", entry, stop, entry - rr*risk))
        return out
    return g


def run_abs(s, gen, m1, tf, fill_win=3, timeout_min=120, max_day=99, cooldown=1,
            mgmt="fixed", atr_filter=True, atr_win=500):
    m1ts, m1h, m1l, m1c = m1; bar_ms = tf*60_000
    atr_med = pd.Series(s.atr).rolling(atr_win, min_periods=50).median().shift(1).values
    trades = []; placed = 0; cool = 0; dcount = {}
    for i in range(60, s.n-1):
        if i < cool or s.atr[i] <= 0: continue
        if atr_filter and not (np.isfinite(atr_med[i]) and s.atr[i] > atr_med[i]): continue
        d = int(s.day[i])
        if dcount.get(d, 0) >= max_day: continue
        for side, entry, stop, tp in (gen(s, i) or []):
            if not np.isfinite([entry, stop, tp]).all(): continue
            placed += 1
            # fill maker hacia adelante (próximas fill_win barras del TF)
            fi = None
            for k in range(i+1, min(i+1+fill_win, s.n)):
                if side == "long" and s.l[k] <= entry: fi = k; break
                if side == "short" and s.h[k] >= entry: fi = k; break
            if fi is None: continue
            risk = abs(entry-stop)
            if risk <= 0: continue
            j0 = np.searchsorted(m1ts, s.ts[fi]); jend = np.searchsorted(m1ts, s.ts[fi]+timeout_min*60_000)
            r = _exit(side, entry, stop, tp, mgmt, m1ts, m1h, m1l, m1c, j0, min(jend, len(m1ts)))
            if r is None: continue
            trades.append(dict(ts=int(s.ts[i]), side=side, r=r, oos=int(s.ts[i]) >= OOS_MS))
            cool = i+cooldown; dcount[d] = dcount.get(d, 0)+1; break
    return pd.DataFrame(trades), placed


def _exit(side, entry, stop, tp, mgmt, m1ts, m1h, m1l, m1c, j0, end):
    if end <= j0: return None
    fee_in = FEE_MK
    if mgmt == "fade":   # parcial 50% a mitad de camino → BE → tp
        tp1 = entry + 0.5*(tp-entry); cur = stop; realized = 0.0; rem = 1.0; f1 = False; reason = "to"
        for j in range(j0, end):
            if side == "long":
                if m1l[j] <= cur: realized += rem*((cur-entry)/abs(entry-stop)); reason = "be" if f1 else "stop"; break
                if not f1 and m1h[j] >= tp1: realized += 0.5*((tp1-entry)/abs(entry-stop)); rem = 0.5; f1 = True; cur = entry
                if m1h[j] >= tp: realized += rem*((tp-entry)/abs(entry-stop)); reason = "tp"; break
            else:
                if m1h[j] >= cur: realized += rem*((entry-cur)/abs(entry-stop)); reason = "be" if f1 else "stop"; break
                if not f1 and m1l[j] <= tp1: realized += 0.5*((entry-tp1)/abs(entry-stop)); rem = 0.5; f1 = True; cur = entry
                if m1l[j] <= tp: realized += rem*((entry-tp)/abs(entry-stop)); reason = "tp"; break
        else:
            px = m1c[end-1]; realized += rem*(((px-entry) if side == "long" else (entry-px))/abs(entry-stop))
        exit_s = FEE_MK if reason == "tp" else FEE_TK
        return realized - (fee_in + (FEE_MK*0.5 if f1 else 0) + exit_s*rem)*entry/abs(entry-stop)
    # fixed
    risk = abs(entry-stop); fee_r = (fee_in+FEE_TK)*entry/risk
    for j in range(j0, end):
        if side == "long":
            if m1l[j] <= stop: return (stop-entry)/risk-fee_r
            if m1h[j] >= tp:  return (tp-entry)/risk - (fee_in+FEE_MK)*entry/risk
        else:
            if m1h[j] >= stop: return (entry-stop)/risk-fee_r
            if m1l[j] <= tp:  return (entry-tp)/risk - (fee_in+FEE_MK)*entry/risk
    px = m1c[end-1]; return ((px-entry) if side == "long" else (entry-px))/risk-fee_r


# ── sweep enfocado en FRECUENCIA por activo ───────────────────────────────────
def main():
    tf = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print(f"Cargando 3 activos M{tf} + precompute de niveles densos...", flush=True)
    data = {}
    for sym in SYMS:
        s = sc_load(sym, tf); m1 = sc_m1exit(sym)
        sup, res = precompute_anchor(s, k=3, W=80, tol_atr=0.25)
        data[sym] = (s, m1, sup, res)
        print(f"  {sym[:3]} ok (sup {100*sup.mean():.0f}% / res {100*res.mean():.0f}% de barras en nivel)", flush=True)
    spans = {sym: (data[sym][0].ts.max()-data[sym][0].ts.min())/86_400_000 for sym in SYMS}

    # base de absorción frecuente; comparamos SIN ancla vs CON ancla a niveles densos
    base = dict(vr_min=1.4, delta_min=0.12, rej=0.55)
    base_laxo = dict(vr_min=1.2, delta_min=0.08, rej=0.50)
    VARIANTS = [
        ("SIN ancla — frecuente",  base,      False, dict(max_day=99, cooldown=1)),
        ("SIN ancla — scalp laxo", base_laxo, False, dict(max_day=99, cooldown=0)),
        ("CON ancla densa — frec", base,      True,  dict(max_day=99, cooldown=1)),
        ("CON ancla densa — laxo", base_laxo, True,  dict(max_day=99, cooldown=0)),
        ("CON ancla + rev+wick.1", dict(**base, rev=True, wick_min=0.1), True, dict(max_day=99, cooldown=1)),
    ]
    for mgmt in ("fade",):
        print(f"\n{'='*104}\n  MGMT={mgmt} · entrada maker pullback · fill fwd 3 · fee honesto · POR ACTIVO\n{'='*104}")
        print(f"  {'variante':<26} | " + " ".join(f"{sym[:3]:>22}" for sym in SYMS))
        print(f"  {'':<26} |  (n/día · fill% · IS/OOS avgR)")
        print("  " + "-"*92)
        for label, gkw, anchor, rkw in VARIANTS:
            row = []
            for sym in SYMS:
                s, m1, sup, res = data[sym]
                g = gen_abs(**gkw, sup=(sup if anchor else None), res=(res if anchor else None))
                df, placed = run_abs(s, g, m1, tf, mgmt=mgmt, **rkw)
                st = stats(df); npd = st["n"]/spans[sym]
                row.append(f"{npd:4.1f}/d f{100*len(df)/max(placed,1):2.0f} {st['isA']:+.2f}/{st['oosA']:+.2f}")
            print(f"  {label:<26} | " + " ".join(f"{c:>22}" for c in row))
    print("\n  celda = trades/día · fill% · IS/OOS avgR (POR ACTIVO). Ancla densa = rechazo en pivot/nivel reciente.")


if __name__ == "__main__":
    main()
