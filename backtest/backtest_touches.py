"""
backtest_touches.py — analisis de toques previos al nivel
Teoria: segundo toque de VAL/VAH tiene mayor WR y avgR que el primero
"""
import pandas as pd, numpy as np, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _listas import OOS_MS, FEE_MAKER, FEE_TAKER
from _audit_mirror import gen_h21_short
import pyarrow.parquet as pq

ROOT   = Path(__file__).parent.parent
MK, TK = FEE_MAKER/2, FEE_TAKER/2
BAR_MS  = 15*60_000
H1_MS   = 60*60_000
H4_MS   = 4*60*60_000

# tolerancia zona = max(0.3% del precio, ATR×0.2)
ZONE_PCT = 0.003

ASSETS = {
    "BTC": dict(
        m1=ROOT/"data/bybit-perp/processed/btcusdt_perp_m1.parquet",
        fp=ROOT/"data/bybit-perp/processed/btcusdt_perp_m15_footprint.parquet",
        margin=2.0, timeout=24*60, use_h5=True,
    ),
    "ETH": dict(
        m1=Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet"),
        fp=Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m15_footprint.parquet"),
        margin=6.0, timeout=24*60, use_h5=True,
    ),
    "SOL": dict(
        m1=Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"),
        fp=Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m15_footprint.parquet"),
        margin=2.0, timeout=6*60, use_h5=False,
    ),
}


def count_zone_touches(ts_arr, h_arr, l_arr, signal_i, signal_ts, lvl,
                        side, zone_pct, lookback_ms):
    """
    Cuenta cuantas veces precio tocó la zona [lvl-tol, lvl+tol]
    en las barras PREVIAS al fill (excluye la barra de señal misma).
    Retorna (touch_count, last_touch_ms_ago, bounce_count)
      bounce_count = veces que precio tocó zona Y volvio en dirección correcta
    """
    tol = lvl * zone_pct
    min_ts = signal_ts - lookback_ms
    count = 0
    last_ago = None
    bounce = 0
    prev_hit = False

    for k in range(signal_i - 1, -1, -1):
        if ts_arr[k] < min_ts:
            break
        hit = (l_arr[k] <= lvl + tol) if side == "long" else (h_arr[k] >= lvl - tol)
        if hit:
            count += 1
            if last_ago is None:
                last_ago = signal_ts - ts_arr[k]
            # bounce = entró a la zona y la barra siguiente cerró en dirección correcta
            if k + 1 < signal_i:
                nxt_close_up = h_arr[k+1] > lvl + tol  # precio subió tras toque long
                nxt_close_dn = l_arr[k+1] < lvl - tol
                if (side == "long" and nxt_close_up) or (side == "short" and nxt_close_dn):
                    bounce += 1
        prev_hit = hit

    return count, (last_ago or 0), bounce


def run_with_touches(a, gens, m1, timeout_min=24*60, margin=2.0,
                     trail_atr=4.0, cooldown=6, max_day=2):
    m1ts, m1h, m1l, m1c = m1
    atr_med = pd.Series(a.atr).rolling(500, min_periods=50).median().shift(1).values

    # Pre-construir arrays H1 y H4 desde las barras M15
    # Resample a H1 / H4 usando arrays directos
    h1_ts  = (a.ts // H1_MS) * H1_MS
    h4_ts  = (a.ts // H4_MS) * H4_MS

    # Diccionarios: ts_ini_h1 → (high, low)
    h1_bars: dict = {}
    h4_bars: dict = {}
    for i in range(a.n):
        t1 = int(h1_ts[i]); h, l = float(a.h[i]), float(a.l[i])
        if t1 in h1_bars:
            prev = h1_bars[t1]; h1_bars[t1] = (max(prev[0],h), min(prev[1],l))
        else:
            h1_bars[t1] = (h, l)
        t4 = int(h4_ts[i])
        if t4 in h4_bars:
            prev = h4_bars[t4]; h4_bars[t4] = (max(prev[0],h), min(prev[1],l))
        else:
            h4_bars[t4] = (h, l)

    h1_keys = np.array(sorted(h1_bars.keys()))
    h1_high  = np.array([h1_bars[k][0] for k in h1_keys])
    h1_low   = np.array([h1_bars[k][1] for k in h1_keys])

    h4_keys = np.array(sorted(h4_bars.keys()))
    h4_high  = np.array([h4_bars[k][0] for k in h4_keys])
    h4_low   = np.array([h4_bars[k][1] for k in h4_keys])

    trades = []
    LOOKBACK_M15 = 96   * BAR_MS   # 24h
    LOOKBACK_H1  = 48   * H1_MS    # 2 días
    LOOKBACK_H4  = 20   * H4_MS    # ~80 días

    for g in gens:
        cool = 0; dcount = {}
        for i in range(200, a.n - 1):
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
                if side == "long"  and not (a.l[i] <= lvl*(1-mf)): continue
                if side == "short" and not (a.h[i] >= lvl*(1+mf)): continue
                entry = lvl; atr0 = a.atr[i]
                sf = 0.15/100*entry
                if abs(entry-stop) < sf:
                    stop = entry-sf if side=="long" else entry+sf
                risk = abs(entry - stop)
                if risk <= 0 or abs(tp2-entry)/risk < 1.2: continue
                if tp1 and 100*abs(tp1-entry)/entry < 0.5: continue

                sig_ts = int(a.ts[i])

                # — M15: 24h lookback
                n_m15, last_m15, bounce_m15 = count_zone_touches(
                    a.ts, a.h, a.l, i, sig_ts, lvl, side, ZONE_PCT, LOOKBACK_M15)

                # — H1: 2d lookback (usar arrays h1)
                h1_i = np.searchsorted(h1_keys, sig_ts) - 1
                n_h1, last_h1, bounce_h1 = count_zone_touches(
                    h1_keys, h1_high, h1_low, h1_i, sig_ts, lvl, side, ZONE_PCT, LOOKBACK_H1)

                # — H4: 80d lookback (usar arrays h4)
                h4_i = np.searchsorted(h4_keys, sig_ts) - 1
                n_h4, last_h4, bounce_h4 = count_zone_touches(
                    h4_keys, h4_high, h4_low, h4_i, sig_ts, lvl, side, ZONE_PCT, LOOKBACK_H4)

                # clasificacion zona H4
                if   n_h4 == 0: zona = "virgen"
                elif n_h4 <= 2: zona = "fresca"
                elif n_h4 <= 4: zona = "confirmada"
                else:           zona = "agotada"

                # ──────────────────────────────────────────────────────────────

                chop = str(a.reg[i]).lower() in ("chop","range","balance","consolidation")
                j0   = np.searchsorted(m1ts, a.ts[i]+BAR_MS)
                jend = np.searchsorted(m1ts, a.ts[i]+BAR_MS+timeout_min*60_000)
                res  = None

                if chop:
                    cur=stop; realized=0.0; rem=1.0; f1=False
                    p1=0.5 if tp1 else 0.0; reason="timeout"
                    for j in range(j0, min(jend, len(m1ts))):
                        if side == "long":
                            if m1l[j]<=cur: realized+=rem*((cur-entry)/risk); reason="be" if f1 else "stop"; break
                            if not f1 and tp1 and m1h[j]>=tp1: realized+=p1*((tp1-entry)/risk); rem-=p1; f1=True; cur=entry
                            if m1h[j]>=tp2: realized+=rem*((tp2-entry)/risk); reason="target"; break
                        else:
                            if m1h[j]>=cur: realized+=rem*((entry-cur)/risk); reason="be" if f1 else "stop"; break
                            if not f1 and tp1 and m1l[j]<=tp1: realized+=p1*((entry-tp1)/risk); rem-=p1; f1=True; cur=entry
                            if m1l[j]<=tp2: realized+=rem*((entry-tp2)/risk); reason="target"; break
                    else:
                        jj = min(jend, len(m1ts))-1
                        if jj <= j0: continue
                        px = m1c[jj]; realized += rem*(((px-entry) if side=="long" else (entry-px))/risk)
                    exit_s = MK if reason=="target" else TK
                    fee_r  = (MK + (MK*p1 if f1 else 0) + exit_s*rem)*entry/risk
                    res    = realized - fee_r
                else:
                    fee_r=MK+TK; fee_r*=entry/risk; best=entry; trail=stop
                    for j in range(j0, min(jend, len(m1ts))):
                        if side == "long":
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
                    bar_ts=sig_ts, side=side, lvl=lvl, atr=atr0, r=res,
                    oos=sig_ts>=OOS_MS, kind=kind,
                    regime="chop" if chop else "trend",
                    n_m15=n_m15, last_m15_h=last_m15/(H1_MS) if last_m15 else 0,
                    n_h1=n_h1,  last_h1_h=last_h1/(H1_MS) if last_h1 else 0,
                    n_h4=n_h4,  last_h4_h=last_h4/(H4_MS) if last_h4 else 0,
                    bounce_h1=bounce_h1, bounce_h4=bounce_h4,
                    zona=zona,
                ))
                cool=i+cooldown; dcount[d]=dcount.get(d,0)+1; break
    return pd.DataFrame(trades)


def load_fp(path):
    return pq.read_table(path).to_pandas().set_index("bar_ts")


def analyze(sym, df, fp_idx=None):
    oos = df[df.oos]
    fp_bin_size = {"BTC":5,"ETH":1,"SOL":0.1}.get(sym,5)

    print(f"\n{'='*70}")
    print(f"  {sym}  total={len(df)}  OOS={len(oos)}")
    print(f"{'='*70}")

    # 1. Por clasificacion de zona H4
    print(f"\n  ZONA H4 (80d lookback, 0.3% ancho):")
    print(f"  {'Zona':>12} | {'n':>5} | {'n_oos':>5} | {'avgR_all':>9} | {'OOS avgR':>9} | {'WR_all':>7} | {'OOS WR':>7} | {'OOS DD':>8}")
    print("  "+"-"*75)
    for zona in ["virgen","fresca","confirmada","agotada"]:
        g=df[df.zona==zona]; go=oos[oos.zona==zona]
        if len(g)<3: continue
        oa=go.r.mean() if len(go)>2 else float('nan')
        ow=100*(go.r>0).mean() if len(go)>2 else float('nan')
        dd=(go.r.cumsum()-go.r.cumsum().cummax()).min() if len(go)>2 else float('nan')
        print(f"  {zona:>12} | {len(g):>5} | {len(go):>5} | {g.r.mean():>+9.3f} | {oa:>+9.3f} | {100*(g.r>0).mean():>6.1f}% | {ow:>6.1f}% | {dd:>+8.2f}R")

    # 2. Toques H4 granular
    print(f"\n  TOQUES H4 granular:")
    print(f"  {'n_h4':>6} | {'n_oos':>5} | {'OOS avgR':>9} | {'WR':>7}")
    print("  "+"-"*35)
    for tc in range(0, min(8, int(df.n_h4.max())+1)):
        go=oos[oos.n_h4==tc]
        if len(go)<3: continue
        print(f"  {tc:>6} | {len(go):>5} | {go.r.mean():>+9.3f} | {100*(go.r>0).mean():>6.1f}%")

    # 3. Toques H1 (2d lookback)
    print(f"\n  TOQUES H1 (2d lookback):")
    print(f"  {'n_h1':>6} | {'n_oos':>5} | {'OOS avgR':>9} | {'WR':>7}")
    print("  "+"-"*35)
    for tc in range(0, min(8, int(df.n_h1.max())+1)):
        go=oos[oos.n_h1==tc]
        if len(go)<3: continue
        print(f"  {tc:>6} | {len(go):>5} | {go.r.mean():>+9.3f} | {100*(go.r>0).mean():>6.1f}%")

    # 4. Recencia ultimo toque H4
    print(f"\n  RECENCIA ultimo toque H4 (barras H4 atras):")
    print(f"  {'Hace cuanto':>14} | {'n_oos':>5} | {'OOS avgR':>9} | {'WR':>7}")
    print("  "+"-"*40)
    for lo,hi,lbl in [(0,0,"nunca (virgen)"),(1,1,"1 barra H4=4h"),(2,3,"2-3 H4=8-12h"),
                       (4,6,"4-6 H4=1d"),(7,20,"7-20 H4=3-4d"),(21,999,"21+ H4=4d+")]:
        g=oos[(oos.last_h4_h>=lo*4)&(oos.last_h4_h<=hi*4 if hi<999 else True)]
        if lo==0: g=oos[oos.n_h4==0]
        if len(g)<3: continue
        print(f"  {lbl:>14} | {len(g):>5} | {g.r.mean():>+9.3f} | {100*(g.r>0).mean():>6.1f}%")

    # 5. Bounce count H4 (cuantos rebotes previos hubo en la zona)
    print(f"\n  REBOTES previos en zona H4 (confirmaciones):")
    print(f"  {'bounce_h4':>10} | {'n_oos':>5} | {'OOS avgR':>9} | {'WR':>7}")
    print("  "+"-"*38)
    for tc in range(0, min(5, int(df.bounce_h4.max())+1)):
        go=oos[oos.bounce_h4==tc]
        if len(go)<3: continue
        print(f"  {tc:>10} | {len(go):>5} | {go.r.mean():>+9.3f} | {100*(go.r>0).mean():>6.1f}%")

    # 6. Footprint en el toque por zona H4
    if fp_idx is not None:
        print(f"\n  Absorcion footprint por ZONA H4:")
        print(f"  {'Zona':>12} | {'n_fp':>5} | {'abs_ratio':>10} | {'delta_bin':>10}")
        print("  "+"-"*45)
        for zona in ["virgen","fresca","confirmada","agotada"]:
            g=oos[oos.zona==zona]; abs_vals=[]; dv=[]
            for row in g.itertuples():
                if row.bar_ts not in fp_idx.index: continue
                fp = fp_idx.loc[row.bar_ts]
                pxs=np.array(fp["prices"]); bv=np.array(fp["buy"]); sv=np.array(fp["sell"])
                bin_px=round(row.lvl/fp_bin_size)*fp_bin_size
                m=np.abs(pxs-bin_px)<fp_bin_size*1.5
                if not m.any(): continue
                b=float(bv[m].sum()); s=float(sv[m].sum())
                ar=s/(b+1e-9) if row.side=="long" else b/(s+1e-9)
                abs_vals.append(ar)
                dv.append(b-s if row.side=="long" else s-b)
            if len(abs_vals)<2: continue
            print(f"  {zona:>12} | {len(abs_vals):>5} | {np.mean(abs_vals):>+10.3f} | {np.mean(dv):>+10.3f}")

    return df


def main():
    for sym, cfg in ASSETS.items():
        if not cfg["m1"].exists(): print(f"{sym}: m1 no existe"); continue
        print(f"\nCargando {sym}...", flush=True)
        L2.M1 = cfg["m1"]
        t=L2.load2(15,start_ms=0); a=L2.A2(t); m1=L2.load_m1_exit(start_ms=0)
        gens=[]
        if cfg["use_h5"]: gens.append(L2.gen_h5())
        gens+=[L2.gen_h21(), gen_h21_short()]
        df = run_with_touches(a, gens, m1, timeout_min=cfg["timeout"],
                              margin=cfg["margin"])
        fp_idx = load_fp(cfg["fp"]) if cfg["fp"].exists() else None
        df = analyze(sym, df, fp_idx)
        df.to_csv(ROOT/f"backtest/touches_{sym.lower()}.csv", index=False)
        print(f"  Guardado: touches_{sym.lower()}.csv")


if __name__ == "__main__":
    main()
