"""
backtest_session_filter.py — Test del filtro de sesion en BTC, ETH, SOL
========================================================================
Compara A+B enrutado con y sin filtro Asia en los 3 activos.

Sesion Asia = 00:00-08:00 UTC (baja liquidez institucional, spreads mas amplios,
              VP levels menos respetados porque los market makers de London/NY
              no estan activos aun).

Uso: python -X utf8 backtest/backtest_session_filter.py
"""
import sys, argparse
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))

ROOT = Path(__file__).parent.parent
TF   = 15

PARQUETS = {
    "BTCUSDT": ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
    "ETHUSDT": Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet"),
    "SOLUSDT": Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"),
}

from _listas import OOS_MS, FEE_MAKER, FEE_TAKER
MK, TK = FEE_MAKER / 2, FEE_TAKER / 2


# ── session desde ts_ms si no hay columna (fallback) ─────────────────────────
def ts_to_session(ts_ms_arr):
    hour = (ts_ms_arr // 3_600_000) % 24
    out = []
    for h in hour:
        if   0  <= h < 8:  out.append("Asia")
        elif 8  <= h < 12: out.append("London")
        elif 12 <= h < 17: out.append("NewYork")
        else:               out.append("OffHours")
    return np.array(out)


def load_with_session(path, tf_min):
    import _listas2 as L2
    L2.M1 = path
    t = L2.load2(tf_min, start_ms=0)

    # leer session del parquet original
    extra = pd.read_parquet(path, columns=["ts_ms","session"]).sort_values("ts_ms").reset_index(drop=True)
    # resample al TF
    g = (extra.ts_ms // (tf_min * 60_000)) * (tf_min * 60_000)
    sess_tf = extra.groupby(g)["session"].last().reset_index()
    sess_tf.columns = ["ts_ms", "session"]
    t = t.merge(sess_tf, on="ts_ms", how="left")

    # si faltan, derivar de hora UTC
    if t["session"].isna().any():
        mask = t["session"].isna()
        t.loc[mask, "session"] = ts_to_session(t.loc[mask, "ts_ms"].values)

    # grupo simplificado
    def grp(s):
        s = str(s)
        if "Asia" in s:                        return "Asia"
        if "London" in s:                      return "London"
        if "NewYork" in s or "NY" in s:        return "NY"
        return "OffHours"

    t["sess_grp"] = t["session"].apply(grp)
    return t


# ── motor A+B con chop_mask opcional ─────────────────────────────────────────
def run_ab(a, sess_arr, gens, m1, tf_min=15, trail_atr=4.0,
           volfilter=True, timeout_min=24*60,
           cooldown=6, max_day=2, margin=2.0,
           stop_floor_pct=0.15, min_range=0.5,
           exclude_sessions=None):

    if exclude_sessions is None:
        exclude_sessions = set()

    m1ts, m1h, m1l, m1c = m1
    bar_ms  = tf_min * 60_000
    atr_med = pd.Series(a.atr).rolling(500, min_periods=50).median().shift(1).values
    trades  = []

    for g in gens:
        cool = 0; dcount = {}
        for i in range(60, a.n - 1):
            if i < cool or a.atr[i] <= 0: continue
            if volfilter and not (np.isfinite(atr_med[i]) and a.atr[i] > atr_med[i]): continue
            d = int(a.day[i])
            if dcount.get(d, 0) >= max_day: continue

            # filtro sesion
            if sess_arr[i] in exclude_sessions: continue

            for side, lvl, stop, tp1, tp2, kind in (g(a, i) or []):
                if not np.isfinite([lvl, stop, tp2]).all(): continue
                ref = a.c[i - 1]
                if side == "long"  and not (lvl < ref): continue
                if side == "short" and not (lvl > ref): continue
                if side == "long"  and not (a.l[i] <= lvl - margin / 1e4 * lvl): continue
                if side == "short" and not (a.h[i] >= lvl + margin / 1e4 * lvl): continue
                entry = lvl; atr0 = a.atr[i]
                if stop_floor_pct > 0:
                    mr = stop_floor_pct / 100.0 * entry
                    if abs(entry - stop) < mr:
                        stop = entry - mr if side == "long" else entry + mr
                risk = abs(entry - stop)
                if risk <= 0 or abs(tp2 - entry) / risk < 1.2: continue
                if min_range > 0 and tp1 is not None and 100 * abs(tp1 - entry) / entry < min_range: continue

                chop_here = str(a.reg[i]).lower() in ("chop","range","balance","consolidation")
                use_fade  = chop_here
                j0   = np.searchsorted(m1ts, a.ts[i] + bar_ms)
                jend = np.searchsorted(m1ts, a.ts[i] + bar_ms + timeout_min * 60_000)
                res  = None

                if use_fade:
                    cur = stop; realized = 0.0; rem = 1.0; f1 = False
                    p1 = 0.5 if tp1 else 0.0; reason = "timeout"
                    for j in range(j0, min(jend, len(m1ts))):
                        if side == "long":
                            if m1l[j] <= cur:
                                realized += rem * ((cur - entry) / risk)
                                reason = "be" if f1 else "stop"; break
                            if not f1 and tp1 and m1h[j] >= tp1:
                                realized += p1 * ((tp1 - entry) / risk)
                                rem -= p1; f1 = True; cur = entry
                            if m1h[j] >= tp2:
                                realized += rem * ((tp2 - entry) / risk)
                                reason = "target"; break
                        else:
                            if m1h[j] >= cur:
                                realized += rem * ((entry - cur) / risk)
                                reason = "be" if f1 else "stop"; break
                            if not f1 and tp1 and m1l[j] <= tp1:
                                realized += p1 * ((entry - tp1) / risk)
                                rem -= p1; f1 = True; cur = entry
                            if m1l[j] <= tp2:
                                realized += rem * ((entry - tp2) / risk)
                                reason = "target"; break
                    else:
                        jj = min(jend, len(m1ts)) - 1
                        if jj <= j0: continue
                        px = m1c[jj]
                        realized += rem * (((px - entry) if side == "long" else (entry - px)) / risk)
                    exit_s = MK if reason == "target" else TK
                    fee_r  = (MK + (MK * p1 if f1 else 0.0) + exit_s * rem) * entry / risk
                    res = realized - fee_r; gestion = "fade"
                else:
                    fee_r = (MK + TK) * entry / risk
                    best = entry; trail = stop
                    for j in range(j0, min(jend, len(m1ts))):
                        if side == "long":
                            best  = max(best,  m1h[j])
                            trail = max(trail, best - trail_atr * atr0)
                            if m1l[j] <= trail:
                                res = (trail - entry) / risk - fee_r; break
                        else:
                            best  = min(best,  m1l[j])
                            trail = min(trail, best + trail_atr * atr0)
                            if m1h[j] >= trail:
                                res = (entry - trail) / risk - fee_r; break
                    if res is None:
                        jj = min(jend, len(m1ts)) - 1
                        if jj <= j0: continue
                        px  = m1c[jj]
                        res = ((px - entry) if side == "long" else (entry - px)) / risk - fee_r
                    gestion = "trail"

                trades.append(dict(
                    ts=int(a.ts[i]), side=side, r=res, gestion=gestion,
                    oos=int(a.ts[i]) >= OOS_MS, kind=kind, sess=sess_arr[i],
                ))
                cool = i + cooldown; dcount[d] = dcount.get(d, 0) + 1; break

    return pd.DataFrame(trades)


def stats(df):
    if len(df) == 0:
        return dict(n=0, wr=0, avgR=0, oosA=0, oosN=0, dd=0, sharpe=0)
    o   = df[df.oos]
    cap = 500.0; peak = 500.0; dd = 0.0
    for r in df.sort_values("ts").r.values:
        cap += 5 * r; peak = max(peak, cap); dd = max(dd, (peak - cap) / peak)
    sh = df.r.mean() / (df.r.std() + 1e-9) * np.sqrt(len(df))
    return dict(n=len(df), wr=100*(df.r>0).mean(), avgR=df.r.mean(),
                oosA=o.r.mean() if len(o) else 0,
                oosN=o.r.sum()  if len(o) else 0,
                dd=100*dd, sharpe=sh, n_oos=len(o))


def fmt(label, s, mark=""):
    return (f"  {label:<28} n={s['n']:>4} n_oos={s.get('n_oos',0):>4} | "
            f"WR {s['wr']:4.1f}% | avgR {s['avgR']:+.3f} | "
            f"OOS avgR {s['oosA']:+.3f} | DD {s['dd']:4.1f}% | Sh {s['sharpe']:+.1f}{mark}")


def run_symbol(symbol):
    import _listas2 as L2
    from _audit_mirror import gen_h21_short

    path = PARQUETS[symbol]
    if not path.exists():
        print(f"  SKIP {symbol}: no encontrado"); return None

    print(f"\n{'='*70}")
    print(f"  {symbol}")
    print(f"{'='*70}")

    t    = load_with_session(path, TF)
    L2.M1 = path
    a    = L2.A2(t)
    m1   = L2.load_m1_exit(start_ms=0)
    sess_arr = t["sess_grp"].values
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

    # distribucion de sesiones
    sess_counts = pd.Series(sess_arr).value_counts()
    print(f"  Barras por sesion: {dict(sess_counts)}")

    # sin filtro (baseline)
    df_base = run_ab(a, sess_arr, gens, m1, exclude_sessions=set())

    # filtro Asia
    df_noas = run_ab(a, sess_arr, gens, m1, exclude_sessions={"Asia"})

    # filtro Asia + OffHours
    df_best = run_ab(a, sess_arr, gens, m1, exclude_sessions={"Asia", "OffHours"})

    s_base = stats(df_base)
    s_noas = stats(df_noas)
    s_best = stats(df_best)

    print(f"\n  {'Variante':<28} {'n':>5} {'n_oos':>6} | {'WR':>5} | {'avgR':>7} | {'OOS avgR':>9} | {'DD':>5} | {'Sh':>5}")
    print("  " + "-"*78)
    print(fmt("Baseline (sin filtro)", s_base))
    delta_noas = s_noas['oosA'] - s_base['oosA']
    delta_best = s_best['oosA'] - s_base['oosA']
    print(fmt(f"Sin Asia             ({delta_noas:+.3f})", s_noas,
              f"  <- elimina {s_base['n']-s_noas['n']} trades ({100*(s_base['n']-s_noas['n'])/max(s_base['n'],1):.1f}%)"))
    print(fmt(f"Sin Asia+OffHours    ({delta_best:+.3f})", s_best,
              f"  <- elimina {s_base['n']-s_best['n']} trades ({100*(s_base['n']-s_best['n'])/max(s_base['n'],1):.1f}%)"))

    # breakdown por sesion en baseline
    print(f"\n  Breakdown OOS por sesion:")
    for sess in ["NY", "London", "Asia", "OffHours"]:
        sub = df_base[df_base.sess == sess]
        oos = sub[sub.oos]
        if len(sub) == 0: continue
        print(f"    {sess:<12} n={len(sub):>4} n_oos={len(oos):>4} | "
              f"WR={100*(sub.r>0).mean():4.1f}% | avgR={sub.r.mean():+.3f} | "
              f"OOS avgR={oos.r.mean() if len(oos) else 0:+.3f}")

    return {"base": s_base, "no_asia": s_noas, "no_asia_offhours": s_best,
            "df_base": df_base, "df_noas": df_noas}


def main():
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    results = {}
    for sym in symbols:
        r = run_symbol(sym)
        if r: results[sym] = r

    # resumen final
    if len(results) > 1:
        print(f"\n\n{'='*70}")
        print("  RESUMEN MULTIASSET — impacto del filtro Asia")
        print(f"{'='*70}")
        print(f"  {'Simbolo':<10} {'Variante':<22} {'OOS avgR':>9} {'delta':>7} {'n_oos':>6} {'n eliminados':>13}")
        print("  " + "-"*70)
        for sym, r in results.items():
            b = r["base"]; na = r["no_asia"]
            print(f"  {sym:<10} {'Baseline':<22} {b['oosA']:>+9.3f} {'':>7} {b.get('n_oos',0):>6}")
            elim = b['n'] - na['n']
            print(f"  {sym:<10} {'Sin Asia':<22} {na['oosA']:>+9.3f} {na['oosA']-b['oosA']:>+7.3f} {na.get('n_oos',0):>6} {elim:>13}")
        print()
        print("  Conclusion: si delta > 0 en los 3 activos -> filtro real, no overfit BTC")


if __name__ == "__main__":
    main()
