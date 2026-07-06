"""
backtest_fp_features.py — features footprint multi-timeframe
=============================================================
Para cada trade extrae features del footprint en 3 TFs:

  M5  — barra de 5min justo ANTES del fill (contexto inmediato pre-fill, limpio)
  M15 — barra de señal (puede tener datos post-fill, sesgo conocido)
  H1  — barra de 1h que contiene la señal (contexto amplio)

Features por TF (prefijo m5_, m15_, h1_):
  delta_at_level    buy-sell en el bin exacto del nivel
  absorption_ratio  presion adversa en el bin del nivel
  large_order_z     z-score volumen del bin del nivel vs barra
  stacked_imb       bins consecutivos con desequilibrio direccional
  exhaustion        en el extremo de la barra: delta cambia a favor?
  vol_concentration top3 bins / vol total
  level_near_poc    nuestro nivel coincide con el POC?
  bar_imb_ratio     desequilibrio total de la barra
  delta_norm_bar    delta total / ATR

Activos: BTC, ETH, SOL
"""
import sys, warnings
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _listas import OOS_MS, FEE_MAKER, FEE_TAKER
from _audit_mirror import gen_h21_short

ROOT    = Path(__file__).parent.parent
BAR_MS  = 15 * 60_000
M5_MS   = 5  * 60_000
H1_MS   = 60 * 60_000
MK, TK  = FEE_MAKER / 2, FEE_TAKER / 2

ASSETS = {
    "BTC": dict(
        m1_path = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
        fp = {
            "m5":  ROOT / "data/bybit-perp/processed/btcusdt_perp_m5_footprint.parquet",
            "m15": ROOT / "data/bybit-perp/processed/btcusdt_perp_m15_footprint.parquet",
            "h1":  ROOT / "data/bybit-perp/processed/btcusdt_perp_h1_footprint.parquet",
        },
        margin=2.0, timeout=24*60, use_h5=True, bin=5.0,
    ),
    "ETH": dict(
        m1_path = Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet"),
        fp = {
            "m5":  Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m5_footprint.parquet"),
            "m15": Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m15_footprint.parquet"),
            "h1":  Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_h1_footprint.parquet"),
        },
        margin=6.0, timeout=24*60, use_h5=True, bin=1.0,
    ),
    "SOL": dict(
        m1_path = Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"),
        fp = {
            "m5":  Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m5_footprint.parquet"),
            "m15": Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m15_footprint.parquet"),
            "h1":  Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_h1_footprint.parquet"),
        },
        margin=2.0, timeout=6*60, use_h5=False, bin=0.1,
    ),
}

TF_OFFSETS = {
    "m5":  -M5_MS,    # barra M5 ANTES del fill
    "m15": 0,          # barra M15 donde ocurre el fill
    "h1":  0,          # barra H1 que contiene el fill
}

TF_STEPS = {
    "m5":  M5_MS,
    "m15": BAR_MS,
    "h1":  H1_MS,
}


# ── Motor backtest ────────────────────────────────────────────────────────────

def run_ab(a, gens, m1, timeout_min=24*60, margin=2.0,
           trail_atr=4.0, cooldown=6, max_day=2,
           stop_floor_pct=0.15, min_range=0.5):
    m1ts, m1h, m1l, m1c = m1
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
                mf = margin / 1e4
                if side == "long"  and not (a.l[i] <= lvl * (1.0 - mf)): continue
                if side == "short" and not (a.h[i] >= lvl * (1.0 + mf)): continue
                entry = lvl; atr0 = a.atr[i]
                if stop_floor_pct > 0:
                    mr = stop_floor_pct / 100.0 * entry
                    if abs(entry - stop) < mr:
                        stop = entry - mr if side == "long" else entry + mr
                risk = abs(entry - stop)
                if risk <= 0 or abs(tp2 - entry) / risk < 1.2: continue
                if tp1 is not None and 100 * abs(tp1 - entry) / entry < min_range: continue
                chop = str(a.reg[i]).lower() in ("chop","range","balance","consolidation")
                j0   = np.searchsorted(m1ts, a.ts[i] + BAR_MS)
                jend = np.searchsorted(m1ts, a.ts[i] + BAR_MS + timeout_min * 60_000)
                res  = None
                if chop:
                    cur=stop; realized=0.0; rem=1.0; f1=False
                    p1=0.5 if tp1 else 0.0; reason="timeout"
                    for j in range(j0, min(jend, len(m1ts))):
                        if side=="long":
                            if m1l[j]<=cur: realized+=rem*((cur-entry)/risk); reason="be" if f1 else "stop"; break
                            if not f1 and tp1 and m1h[j]>=tp1: realized+=p1*((tp1-entry)/risk); rem-=p1; f1=True; cur=entry
                            if m1h[j]>=tp2: realized+=rem*((tp2-entry)/risk); reason="target"; break
                        else:
                            if m1h[j]>=cur: realized+=rem*((entry-cur)/risk); reason="be" if f1 else "stop"; break
                            if not f1 and tp1 and m1l[j]<=tp1: realized+=p1*((entry-tp1)/risk); rem-=p1; f1=True; cur=entry
                            if m1l[j]<=tp2: realized+=rem*((entry-tp2)/risk); reason="target"; break
                    else:
                        jj=min(jend,len(m1ts))-1
                        if jj<=j0: continue
                        px=m1c[jj]; realized+=rem*(((px-entry) if side=="long" else (entry-px))/risk)
                    exit_s=MK if reason=="target" else TK
                    fee_r=(MK+(MK*p1 if f1 else 0.0)+exit_s*rem)*entry/risk
                    res=realized-fee_r
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
                trades.append(dict(bar_ts=int(a.ts[i]), side=side, lvl=lvl, atr=atr0,
                                   r=res, oos=int(a.ts[i])>=OOS_MS, kind=kind,
                                   regime="chop" if chop else "trend"))
                cool=i+cooldown; dcount[d]=dcount.get(d,0)+1; break
    return pd.DataFrame(trades)


# ── Footprint index ───────────────────────────────────────────────────────────

def load_fp_index(fp_path):
    df = pq.read_table(fp_path).to_pandas()
    return df.set_index("bar_ts")


# ── Feature extraction (una barra de footprint) ───────────────────────────────

def extract_features_row(row, level, side, atr, fp_bin):
    prices  = np.array(row["prices"], dtype=np.float64)
    buy_v   = np.array(row["buy"],    dtype=np.float64)
    sell_v  = np.array(row["sell"],   dtype=np.float64)
    vol_v   = buy_v + sell_v
    delta_v = buy_v - sell_v

    if len(prices) == 0:
        return None

    level_bin = round(level / fp_bin) * fp_bin
    mask_lvl  = np.abs(prices - level_bin) < fp_bin * 0.5
    if mask_lvl.any():
        b_lvl = float(buy_v[mask_lvl].sum())
        s_lvl = float(sell_v[mask_lvl].sum())
    else:
        idx   = np.argmin(np.abs(prices - level_bin))
        b_lvl = float(buy_v[idx])
        s_lvl = float(sell_v[idx])
    v_lvl = b_lvl + s_lvl

    # 1. delta_at_level
    delta_at_level = b_lvl - s_lvl

    # 2. absorption_ratio — presion adversa en el bin
    if side == "long":
        absorption_ratio = s_lvl / (b_lvl + 1e-9)
    else:
        absorption_ratio = b_lvl / (s_lvl + 1e-9)

    # 3. large_order_z
    mean_vol = float(vol_v.mean())
    std_vol  = float(vol_v.std()) + 1e-9
    large_order_z = (v_lvl - mean_vol) / std_vol

    # 4. stacked_imb — mayor run de bins con desequilibrio direccional
    imb_count = cur_run = 0
    for i in range(len(prices)):
        cond = (sell_v[i] > buy_v[i] * 2.0) if side == "long" else (buy_v[i] > sell_v[i] * 2.0)
        if cond:
            cur_run += 1
            imb_count = max(imb_count, cur_run)
        else:
            cur_run = 0

    # 5. exhaustion — en el extremo de la barra
    if side == "long":
        exhaustion = float(delta_v[np.argmin(prices)])
    else:
        exhaustion = float(-delta_v[np.argmax(prices)])

    # 6. vol_concentration
    top3_vol = float(np.sort(vol_v)[-3:].sum()) if len(vol_v) >= 3 else float(vol_v.sum())
    vol_concentration = top3_vol / (float(vol_v.sum()) + 1e-9)

    # 7. level_near_poc
    poc = float(row["poc"])
    level_near_poc = 1.0 if abs(level_bin - poc) <= fp_bin * 1.5 else 0.0

    # 8. bar_imb_ratio
    bar_imb_ratio = float(row["imb_ratio"])

    # 9. delta_norm_bar
    delta_norm_bar = float(row["delta"]) / atr if atr > 0 else 0.0

    return dict(
        delta_at_level    = round(delta_at_level, 4),
        absorption_ratio  = round(absorption_ratio, 4),
        large_order_z     = round(large_order_z, 4),
        stacked_imb       = imb_count,
        exhaustion        = round(exhaustion, 4),
        vol_concentration = round(vol_concentration, 4),
        level_near_poc    = level_near_poc,
        bar_imb_ratio     = round(bar_imb_ratio, 4),
        delta_norm_bar    = round(delta_norm_bar, 4),
    )


def extract_all_tfs(bar_ts, level, side, atr, fp_indexes, fp_bin):
    """Extrae features para M5 (barra previa), M15 (señal), H1 (contexto)."""
    result = {}
    for tf, idx in fp_indexes.items():
        step = TF_STEPS[tf]
        # M5: barra inmediatamente ANTES del fill (bar_ts - M5_MS)
        # M15: la barra de señal misma (bar_ts)
        # H1: la barra H1 que contiene bar_ts
        if tf == "m5":
            lookup_ts = ((bar_ts - 1) // step) * step  # barra M5 previa al fill
        else:
            lookup_ts = (bar_ts // step) * step

        if lookup_ts not in idx.index:
            return None  # si falta cualquier TF descartamos el trade

        row   = idx.loc[lookup_ts]
        feats = extract_features_row(row, level, side, atr, fp_bin)
        if feats is None:
            return None
        for k, v in feats.items():
            result[f"{tf}_{k}"] = v

    return result


# ── Analisis por TF ───────────────────────────────────────────────────────────

FEAT_NAMES = ["delta_at_level","absorption_ratio","large_order_z",
              "stacked_imb","exhaustion","vol_concentration",
              "level_near_poc","bar_imb_ratio","delta_norm_bar"]

def pearson_oos(df, feat):
    oos = df[df.oos]
    if len(oos) < 10 or feat not in df.columns:
        return np.nan
    return oos[[feat, "r"]].corr().iloc[0, 1]


def analyze_mtf(sym, df):
    print(f"\n{'='*65}")
    print(f"  {sym}  n_total={len(df)}  n_oos={df.oos.sum()}")
    print(f"{'='*65}")

    tfs = ["m5", "m15", "h1"]

    # ── Tabla comparativa: correlacion OOS por feature y TF ───────────────
    print(f"\n  Pearson OOS por feature y timeframe:")
    print(f"  {'Feature':<22}", end="")
    for tf in tfs:
        print(f"  {tf.upper():>8}", end="")
    print()
    print("  " + "-" * (22 + 11 * len(tfs)))
    for feat in FEAT_NAMES:
        print(f"  {feat:<22}", end="")
        for tf in tfs:
            col = f"{tf}_{feat}"
            c   = pearson_oos(df, col)
            tag = "*" if abs(c) > 0.10 else " "
            print(f"  {c:>+7.4f}{tag}", end="")
        print()

    # ── Por TF: top feature y cuartiles ───────────────────────────────────
    oos = df[df.oos]
    train = df[~df.oos]

    print(f"\n  Top feature por TF (mayor |Pearson OOS|):")
    top_by_tf = {}
    for tf in tfs:
        cols_tf = [f"{tf}_{f}" for f in FEAT_NAMES if f"{tf}_{f}" in df.columns]
        corrs   = [(c, pearson_oos(df, c)) for c in cols_tf]
        corrs   = [(c, v) for c, v in corrs if not np.isnan(v)]
        if not corrs: continue
        best_col, best_c = max(corrs, key=lambda x: abs(x[1]))
        top_by_tf[tf]    = (best_col, best_c)
        fname = best_col.replace(f"{tf}_", "")
        sign  = 1 if best_c > 0 else -1
        direc = "HIGH" if sign > 0 else "LOW"
        thresh = train[best_col].median() * sign

        # Q4 vs Q1
        sorted_vals = df[best_col] * sign
        q25 = sorted_vals.quantile(0.25)
        q75 = sorted_vals.quantile(0.75)
        q4  = oos[oos[best_col] * sign >= q75]
        q1  = oos[oos[best_col] * sign <= q25]

        print(f"\n  [{tf.upper()}] {fname}  r={best_c:+.4f}  dir={direc}")
        print(f"       Q4(mejor) n_oos={len(q4)}  avgR={q4.r.mean():+.3f}")
        print(f"       Q1(peor)  n_oos={len(q1)}  avgR={q1.r.mean():+.3f}")
        print(f"       Delta Q4-Q1 = {q4.r.mean() - q1.r.mean():+.3f}R")

    # ── Ranking de TFs: cual tiene mayor efecto Q4-Q1 promedio ────────────
    print(f"\n  Ranking de TFs por impacto promedio Q4-Q1:")
    tf_impacts = []
    for tf in tfs:
        cols_tf = [f"{tf}_{f}" for f in FEAT_NAMES if f"{tf}_{f}" in df.columns]
        deltas  = []
        for col in cols_tf:
            c = pearson_oos(df, col)
            if np.isnan(c): continue
            sign = 1 if c > 0 else -1
            sv   = oos[col] * sign
            q4   = oos[sv >= sv.quantile(0.75)]
            q1   = oos[sv <= sv.quantile(0.25)]
            if len(q4) > 3 and len(q1) > 3:
                deltas.append(q4.r.mean() - q1.r.mean())
        avg_delta = np.mean(deltas) if deltas else 0.0
        tf_impacts.append((tf, avg_delta))
        print(f"  {tf.upper()}: delta_promedio_Q4-Q1 = {avg_delta:+.3f}R")

    best_tf = max(tf_impacts, key=lambda x: x[1])[0]
    print(f"\n  => Mejor TF para features footprint: {best_tf.upper()}")

    # ── Filtro rapido con top feature del mejor TF ────────────────────────
    if best_tf in top_by_tf:
        best_col, best_c = top_by_tf[best_tf]
        sign  = 1 if best_c > 0 else -1
        thresh = (train[best_col] * sign).median()

        filt     = df[(df[best_col] * sign) >= thresh]
        filt_oos = filt[filt.oos]
        base_oos = oos

        print(f"\n  Filtro {best_tf.upper()} top-feature (mitad superior):")
        print(f"  {'':20} {'n_oos':>7} {'avgR':>8} {'WR%':>7} {'Sharpe':>8}")
        print("  " + "-"*52)
        for label, sub in [("Baseline", base_oos), ("Con filtro", filt_oos)]:
            if len(sub) < 3: continue
            sh = sub.r.mean() / (sub.r.std() + 1e-9) * np.sqrt(252)
            print(f"  {label:<20} {len(sub):>7} {sub.r.mean():>+8.3f} "
                  f"{100*(sub.r>0).mean():>6.1f}% {sh:>8.2f}")

    return df


# ── Main ──────────────────────────────────────────────────────────────────────

def run_symbol(sym, cfg):
    # Verificar footprints disponibles
    missing = [tf for tf, p in cfg["fp"].items() if not p.exists()]
    if missing:
        print(f"\n{sym}: faltan footprints {missing}")
        return None

    print(f"\nCargando {sym}...", flush=True)
    L2.M1 = cfg["m1_path"]
    t  = L2.load2(15, start_ms=0)
    a  = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=0)

    gens = []
    if cfg["use_h5"]: gens.append(L2.gen_h5())
    gens += [L2.gen_h21(), gen_h21_short()]

    df = run_ab(a, gens, m1, timeout_min=cfg["timeout"], margin=cfg["margin"])

    print(f"  Trades: {len(df)}  OOS: {df.oos.sum()}")
    print(f"  Cargando footprint indexes (M5, M15, H1)...", flush=True)

    fp_indexes = {tf: load_fp_index(p) for tf, p in cfg["fp"].items()}

    print(f"  Extrayendo features multi-TF...", flush=True)
    feat_rows = []
    missing_fp = 0
    for row in df.itertuples():
        feats = extract_all_tfs(row.bar_ts, row.lvl, row.side,
                                row.atr, fp_indexes, cfg["bin"])
        if feats:
            feat_rows.append(feats)
        else:
            feat_rows.append({})
            missing_fp += 1

    df = pd.concat([df.reset_index(drop=True), pd.DataFrame(feat_rows)], axis=1)
    df = df.dropna(subset=[f"m15_{FEAT_NAMES[0]}"])
    print(f"  Features OK: {len(df)}  sin footprint completo: {missing_fp}")

    df = analyze_mtf(sym, df)

    out = ROOT / "backtest" / f"fp_mtf_{sym.lower()}.csv"
    df.to_csv(out, index=False)
    print(f"\n  Guardado: {out.name}")
    return df


def main():
    for sym, cfg in ASSETS.items():
        run_symbol(sym, cfg)


if __name__ == "__main__":
    main()
