"""
backtest_footprint_delta.py — ¿predice el bar_delta_at_fill el outcome del trade?
==================================================================================
Solo BTC (únicos ticks disponibles en data/bybit-perp/raw_trades/).
Cobertura de ticks: 2025-06-19→2026-06-18 (365d) → overlap con OOS del backtest.

Para cada trade del backtest:
  1. Cargar ticks de la barra M15 (15 min desde bar_ts)
  2. Encontrar primer tick que cruza nivel ± margin → fill tick
  3. Acumular delta (buy_size - sell_size) desde inicio hasta fill tick
  4. Analizar: ¿sign/magnitud del delta predice result_r?

Uso: python -X utf8 backtest/backtest_footprint_delta.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _listas import OOS_MS, FEE_MAKER, FEE_TAKER
from _audit_mirror import gen_h21_short

ROOT   = Path(__file__).parent.parent
TF     = 15
BAR_MS = TF * 60_000
MK, TK = FEE_MAKER / 2, FEE_TAKER / 2
MARGIN = 2.0  # bps — mismo que BTC config

TICK_DIR = ROOT / "data/bybit-perp/raw_trades"
M1_PATH  = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"

# ── Motor de backtest (igual que _strategy_ab.py pero devuelve ts de barra) ──

def run_ab_with_ts(a, gens, m1, tf_min=15, trail_atr=4.0,
                   timeout_min=24*60, cooldown=6, max_day=2,
                   margin=MARGIN, stop_floor_pct=0.15, min_range=0.5):
    m1ts, m1h, m1l, m1c = m1
    bar_ms  = tf_min * 60_000
    atr_med = pd.Series(a.atr).rolling(500, min_periods=50).median().shift(1).values
    trades  = []

    for g in gens:
        cool = 0; dcount = {}
        for i in range(60, a.n - 1):
            if i < cool or a.atr[i] <= 0: continue
            if not (np.isfinite(atr_med[i]) and a.atr[i] > atr_med[i]): continue
            d = int(a.day[i])
            if dcount.get(d, 0) >= max_day: continue
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
                if tp1 is not None and 100 * abs(tp1 - entry) / entry < min_range: continue
                chop_here = str(a.reg[i]).lower() in ("chop","range","balance","consolidation")
                j0   = np.searchsorted(m1ts, a.ts[i] + bar_ms)
                jend = np.searchsorted(m1ts, a.ts[i] + bar_ms + timeout_min * 60_000)
                res  = None
                if chop_here:
                    cur = stop; realized = 0.0; rem = 1.0; f1 = False
                    p1 = 0.5 if tp1 else 0.0; reason = "timeout"
                    for j in range(j0, min(jend, len(m1ts))):
                        if side == "long":
                            if m1l[j] <= cur: realized += rem*((cur-entry)/risk); reason="be" if f1 else "stop"; break
                            if not f1 and tp1 and m1h[j]>=tp1: realized+=p1*((tp1-entry)/risk); rem-=p1; f1=True; cur=entry
                            if m1h[j]>=tp2: realized+=rem*((tp2-entry)/risk); reason="target"; break
                        else:
                            if m1h[j]>=cur: realized+=rem*((entry-cur)/risk); reason="be" if f1 else "stop"; break
                            if not f1 and tp1 and m1l[j]<=tp1: realized+=p1*((entry-tp1)/risk); rem-=p1; f1=True; cur=entry
                            if m1l[j]<=tp2: realized+=rem*((entry-tp2)/risk); reason="target"; break
                    else:
                        jj = min(jend, len(m1ts)) - 1
                        if jj <= j0: continue
                        px = m1c[jj]; realized+=rem*(((px-entry) if side=="long" else (entry-px))/risk)
                    exit_s = MK if reason=="target" else TK
                    fee_r  = (MK+(MK*p1 if f1 else 0.0)+exit_s*rem)*entry/risk
                    res = realized - fee_r
                else:
                    fee_r=(MK+TK)*entry/risk; best=entry; trail=stop
                    for j in range(j0, min(jend, len(m1ts))):
                        if side=="long":
                            best=max(best,m1h[j]); trail=max(trail,best-trail_atr*atr0)
                            if m1l[j]<=trail: res=(trail-entry)/risk-fee_r; break
                        else:
                            best=min(best,m1l[j]); trail=min(trail,best+trail_atr*atr0)
                            if m1h[j]>=trail: res=(entry-trail)/risk-fee_r; break
                    if res is None:
                        jj=min(jend,len(m1ts))-1
                        if jj<=j0: continue
                        px=m1c[jj]; res=((px-entry) if side=="long" else (entry-px))/risk-fee_r
                trades.append(dict(
                    bar_ts=int(a.ts[i]),   # timestamp inicio de barra M15
                    side=side, lvl=lvl, atr=atr0,
                    r=res, oos=int(a.ts[i])>=OOS_MS, kind=kind,
                    regime="chop" if chop_here else "trend",
                ))
                cool=i+cooldown; dcount[d]=dcount.get(d,0)+1; break
    return pd.DataFrame(trades)


# ── Cargar ticks por fecha ───────────────────────────────────────────────────

_tick_cache: dict[str, pd.DataFrame] = {}

def load_ticks_for_date(date_str: str) -> pd.DataFrame:
    """Carga un parquet diario de ticks. Cache en memoria."""
    if date_str in _tick_cache:
        return _tick_cache[date_str]
    path = TICK_DIR / f"{date_str}.parquet"
    if not path.exists():
        _tick_cache[date_str] = pd.DataFrame()
        return _tick_cache[date_str]
    df = pq.read_table(path, columns=["ts_ms","price","size","side"]).to_pandas()
    df["size"] = df["size"].astype(float)
    df["is_buy"] = (df["side"] == "Buy")
    _tick_cache[date_str] = df
    return df


def compute_bar_delta(bar_ts_ms: int, level: float, side: str, atr: float) -> dict:
    """
    Calcula el delta acumulado desde el inicio de la barra M15 hasta el tick de fill.

    Returns dict con:
      - delta_at_fill: buy_vol - sell_vol hasta el fill tick
      - delta_norm:    delta_at_fill / atr (normalizado)
      - fill_found:    True si se encontró el tick de fill
      - ticks_until_fill: número de ticks hasta fill
      - fill_frac:    fracción de la barra transcurrida hasta fill (0→1)
    """
    bar_end_ms = bar_ts_ms + BAR_MS
    margin_frac = MARGIN / 10_000.0

    # Fechas que puede tocar esta barra (puede cruzar medianoche)
    import datetime
    d0 = datetime.datetime.utcfromtimestamp(bar_ts_ms / 1000).strftime("%Y-%m-%d")
    d1 = datetime.datetime.utcfromtimestamp(bar_end_ms / 1000).strftime("%Y-%m-%d")

    ticks_today = load_ticks_for_date(d0)
    ticks = ticks_today if d0 == d1 else pd.concat(
        [ticks_today, load_ticks_for_date(d1)], ignore_index=True
    )

    if len(ticks) == 0:
        return dict(delta_at_fill=np.nan, delta_norm=np.nan,
                    fill_found=False, ticks_until_fill=0, fill_frac=np.nan)

    # Filtrar la ventana de la barra
    mask = (ticks["ts_ms"] >= bar_ts_ms) & (ticks["ts_ms"] < bar_end_ms)
    bar_ticks = ticks[mask].reset_index(drop=True)

    if len(bar_ticks) == 0:
        return dict(delta_at_fill=np.nan, delta_norm=np.nan,
                    fill_found=False, ticks_until_fill=0, fill_frac=np.nan)

    # Encontrar primer tick que cruza el nivel (fill)
    px = bar_ticks["price"].values
    if side == "long":
        fill_threshold = level * (1.0 - margin_frac)
        fill_mask = px <= fill_threshold
    else:
        fill_threshold = level * (1.0 + margin_frac)
        fill_mask = px >= fill_threshold

    fill_idx = int(np.argmax(fill_mask)) if fill_mask.any() else -1
    fill_found = fill_mask.any()

    if not fill_found:
        # Fill no encontrado en ticks — usar toda la barra como proxy
        fill_idx = len(bar_ticks) - 1

    ticks_sub = bar_ticks.iloc[:fill_idx + 1]
    buy_vol  = ticks_sub.loc[ticks_sub["is_buy"],  "size"].sum()
    sell_vol = ticks_sub.loc[~ticks_sub["is_buy"], "size"].sum()
    delta = buy_vol - sell_vol

    fill_frac = (fill_idx + 1) / len(bar_ticks) if len(bar_ticks) > 0 else np.nan

    return dict(
        delta_at_fill   = round(delta, 4),
        delta_norm      = round(delta / atr, 4) if atr > 0 else np.nan,
        fill_found      = fill_found,
        ticks_until_fill= fill_idx + 1,
        fill_frac       = round(fill_frac, 3),
    )


# ── Análisis principal ───────────────────────────────────────────────────────

def main():
    print("Cargando BTC M15...")
    L2.M1 = M1_PATH
    t  = L2.load2(TF, start_ms=0)
    a  = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=0)

    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
    print("Corriendo backtest...")
    df = run_ab_with_ts(a, gens, m1)
    print(f"  {len(df)} trades totales  OOS={df.oos.sum()}")

    # Solo trades con tick data disponible (2025-06-19+)
    tick_start_ms = int(pd.Timestamp("2025-06-19").timestamp() * 1000)
    df_tick = df[df.bar_ts >= tick_start_ms].copy()
    print(f"  {len(df_tick)} trades con tick data disponible")

    print(f"\nComputando bar_delta_at_fill para {len(df_tick)} trades...")
    results = []
    for i, row in enumerate(df_tick.itertuples(), 1):
        if i % 50 == 0:
            print(f"  {i}/{len(df_tick)}...", flush=True)
        r = compute_bar_delta(row.bar_ts, row.lvl, row.side, row.atr)
        results.append(r)

    df_tick = df_tick.reset_index(drop=True)
    df_res  = pd.DataFrame(results)
    df_tick = pd.concat([df_tick, df_res], axis=1)

    # Separar fill_found vs no
    df_ok  = df_tick[df_tick.fill_found].copy()
    df_nok = df_tick[~df_tick.fill_found].copy()
    print(f"\n  fill_found en ticks: {len(df_ok)}/{len(df_tick)} ({100*len(df_ok)/len(df_tick):.0f}%)")
    print(f"  fill NOT found (proxy barra completa): {len(df_nok)}")

    # ── Análisis 1: signo del delta ──────────────────────────────────────────
    print("\n" + "="*60)
    print("  ANÁLISIS 1 — signo del delta en el fill (fill_found only)")
    print("="*60)

    # Para long: esperamos que delta positivo = absorción = mejor resultado
    # Para short: esperamos que delta negativo = absorción = mejor resultado
    df_ok["delta_favor"] = np.where(
        df_ok["side"] == "long",
        df_ok["delta_at_fill"] > 0,     # long + delta+ = buyers absorbiendo vendedores
        df_ok["delta_at_fill"] < 0,     # short + delta- = sellers absorbiendo compradores
    )

    g = df_ok.groupby("delta_favor")["r"]
    print(f"\n  {'Favor delta':<15} {'n':>5} {'WR%':>7} {'avgR':>8} {'OOS avgR':>10}")
    print("  " + "-"*50)
    for fav, grp in df_ok.groupby("delta_favor"):
        oos = grp[grp.oos]
        label = "Favorable" if fav else "Adverso"
        print(f"  {label:<15} {len(grp):>5} {100*(grp.r>0).mean():>6.1f}% {grp.r.mean():>+8.3f} {oos.r.mean() if len(oos) else np.nan:>+10.3f}")

    # ── Análisis 2: quintiles de delta_norm ─────────────────────────────────
    print("\n" + "="*60)
    print("  ANÁLISIS 2 — quintiles de delta_norm (normalizado por ATR)")
    print("="*60)
    df_ok["q"] = pd.qcut(df_ok["delta_norm"], 5, labels=["Q1(negativo)","Q2","Q3","Q4","Q5(positivo)"])
    print(f"\n  {'Quintil':<20} {'n':>5} {'WR%':>7} {'avgR':>8}")
    print("  " + "-"*45)
    for q, grp in df_ok.groupby("q", observed=True):
        print(f"  {str(q):<20} {len(grp):>5} {100*(grp.r>0).mean():>6.1f}% {grp.r.mean():>+8.3f}")

    # ── Análisis 3: por side ─────────────────────────────────────────────────
    print("\n" + "="*60)
    print("  ANÁLISIS 3 — long vs short separados")
    print("="*60)
    for side_val in ["long", "short"]:
        sub = df_ok[df_ok.side == side_val]
        if len(sub) < 10: continue
        print(f"\n  {side_val.upper()}  (n={len(sub)})  avgR baseline={sub.r.mean():+.3f}")
        sign_col = "delta_at_fill"
        fav = sub[sub[sign_col] > 0] if side_val == "long" else sub[sub[sign_col] < 0]
        adv = sub[sub[sign_col] <= 0] if side_val == "long" else sub[sub[sign_col] >= 0]
        print(f"    delta favorable (n={len(fav)}): avgR={fav.r.mean():+.3f}  WR={100*(fav.r>0).mean():.1f}%")
        print(f"    delta adverso   (n={len(adv)}): avgR={adv.r.mean():+.3f}  WR={100*(adv.r>0).mean():.1f}%")

    # ── Análisis 4: fill_frac (¿cuándo en la barra ocurre el fill?) ─────────
    print("\n" + "="*60)
    print("  ANÁLISIS 4 — fill_frac: momento del fill en la barra")
    print("="*60)
    print(f"  Mediana fill_frac: {df_ok['fill_frac'].median():.2f}  (0=inicio, 1=fin barra)")
    bins = [0, 0.25, 0.5, 0.75, 1.01]
    labels = ["0-25%", "25-50%", "50-75%", "75-100%"]
    df_ok["ff_bin"] = pd.cut(df_ok["fill_frac"], bins=bins, labels=labels)
    print(f"\n  {'Momento fill':<15} {'n':>5} {'WR%':>7} {'avgR':>8}")
    for fb, grp in df_ok.groupby("ff_bin", observed=True):
        print(f"  {str(fb):<15} {len(grp):>5} {100*(grp.r>0).mean():>6.1f}% {grp.r.mean():>+8.3f}")

    # ── Correlación ──────────────────────────────────────────────────────────
    corr = df_ok[["delta_norm", "r"]].corr().iloc[0, 1]
    print(f"\n  Correlación Pearson delta_norm vs result_r: {corr:+.4f}")

    # ── Guardar CSV para análisis posterior ──────────────────────────────────
    out = ROOT / "backtest" / "footprint_delta_btc.csv"
    df_tick.to_csv(out, index=False)
    print(f"\n  CSV guardado: {out.name}  ({len(df_tick)} filas)")


if __name__ == "__main__":
    main()
