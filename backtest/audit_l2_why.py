"""
audit_l2_why.py — Re-test de OBI/CVD/VPIN/spread como filtros sobre setups VP
==============================================================================
La pregunta NO es "¿OBI predice el retorno de la proxima barra?" (eso ya se sabe: no).
La pregunta correcta es: "¿OBI en el momento del fill mejora el setup VP?"

Si tampoco, diagnosticamos POR QUE:
  - Correlacion feature → R en nuestros trades
  - Distribucion del feature en winners vs losers
  - Timing: ¿el feature es predictivo en las primeras N barras o se degrada?

Uso: python -X utf8 backtest/audit_l2_why.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _listas import OOS_MS
from _audit_mirror import gen_h21_short

ROOT = Path(__file__).parent.parent
TF   = 15
MK, TK = 0.0002 / 2, 0.00055 / 2


def load():
    t = L2.load2(TF)
    # agregar L2 que load2 no incluye
    # obi5_mean, obi10_mean, spread_mean, vpin, cvd_slope ya vienen en load2()
    return t


def run_tagged(a, t, gens, m1, tf_min=15, trail_atr=4.0,
               volfilter=True, timeout_min=24*60,
               cooldown=6, max_day=2, margin=2.0,
               stop_floor_pct=0.15, min_range=0.5):

    m1ts, m1h, m1l, m1c = m1
    bar_ms  = tf_min * 60_000
    atr_med = pd.Series(a.atr).rolling(500, min_periods=50).median().shift(1).values

    # arrays L2
    obi5   = t["obi5_mean"].values.astype(float)
    obi10  = t["obi10_mean"].values.astype(float)
    spread = t["spread_mean"].values.astype(float)
    vpin   = t["vpin"].values.astype(float)
    cvd_sl = t["cvd_slope"].values.astype(float)
    cvd_dv = np.nan_to_num(t["cvd_div_x"].values.astype(float)).astype(bool) if "cvd_div_x" in t.columns else np.zeros(len(t), dtype=bool)

    trades = []
    for g in gens:
        cool = 0; dcount = {}
        for i in range(60, a.n - 1):
            if i < cool or a.atr[i] <= 0: continue
            if volfilter and not (np.isfinite(atr_med[i]) and a.atr[i] > atr_med[i]): continue
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
                if min_range > 0 and tp1 is not None and 100 * abs(tp1 - entry) / entry < min_range: continue

                # ── routing ──────────────────────────────────────────────────
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

                # ── OBI direccional: positivo = a favor del trade ─────────────
                obi_val  = obi5[i]  if np.isfinite(obi5[i])  else 0.0
                obi10_v  = obi10[i] if np.isfinite(obi10[i]) else 0.0
                # para long: OBI positivo es a favor; para short: OBI negativo es a favor
                obi_dir  = obi_val  if side == "long" else -obi_val
                obi10_dir= obi10_v  if side == "long" else -obi10_v

                cvd_sl_dir = cvd_sl[i] if side == "long" else -cvd_sl[i]

                trades.append(dict(
                    ts=int(a.ts[i]), side=side, r=res, gestion=gestion,
                    oos=int(a.ts[i]) >= OOS_MS, kind=kind,
                    obi5=obi_val, obi5_dir=obi_dir,
                    obi10_dir=obi10_dir,
                    spread=spread[i],
                    vpin=vpin[i],
                    cvd_slope_dir=cvd_sl_dir,
                    cvd_div=cvd_dv[i],
                    bar_idx=i,
                ))
                cool = i + cooldown; dcount[d] = dcount.get(d, 0) + 1; break

    return pd.DataFrame(trades)


def blk(df, label, indent=2):
    sp = " " * indent
    if len(df) == 0:
        print(f"{sp}{label:<40} n=0"); return
    oos = df[df.oos]
    wr  = 100 * (df.r > 0).mean()
    oavg = oos.r.mean() if len(oos) else 0
    print(f"{sp}{label:<40} n={len(df):>4}  WR={wr:4.1f}%  avgR={df.r.mean():+.3f}"
          f"  OOS_avgR={oavg:+.3f} (n={len(oos)})")


def section(title):
    print(f"\n{'='*65}")
    print(f"  {title}")
    print(f"{'='*65}")


def main():
    print("Cargando...")
    t  = load()
    a  = L2.A2(t)
    m1 = L2.load_m1_exit()
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]

    print("Corriendo backtest...")
    df = run_tagged(a, t, gens, m1)
    oos = df[df.oos]
    print(f"Total: {len(df)}  OOS: {len(oos)}")

    # ── CORRELACIONES — la diagnostica mas importante ─────────────────────────
    section("1. CORRELACION FEATURE → R en nuestros trades")
    features = ["obi5_dir", "obi10_dir", "spread", "vpin", "cvd_slope_dir"]
    print(f"  {'Feature':<20} {'corr(all)':>10} {'corr(OOS)':>10}  interpretacion")
    print("  " + "-"*70)
    for f in features:
        sub = df[f].dropna()
        if len(sub) < 10: continue
        valid = df.dropna(subset=[f])
        valid_oos = valid[valid.oos]
        c_all = np.corrcoef(valid[f], valid.r)[0,1] if len(valid) > 2 else 0
        c_oos = np.corrcoef(valid_oos[f], valid_oos.r)[0,1] if len(valid_oos) > 2 else 0
        interp = ("SIGNAL fuerte" if abs(c_all) > 0.15 else
                  "debil" if abs(c_all) > 0.05 else "RUIDO PURO")
        print(f"  {f:<20} {c_all:>+10.4f} {c_oos:>+10.4f}  {interp}")

    # ── DISTRIBUCION winners vs losers ────────────────────────────────────────
    section("2. DISTRIBUCION del feature en winners vs losers")
    winners = df[df.r > 0]
    losers  = df[df.r <= 0]
    print(f"  {'Feature':<20} {'winners_mean':>13} {'losers_mean':>13} {'diferencia':>12}")
    print("  " + "-"*60)
    for f in features:
        wm = winners[f].mean() if f in winners else 0
        lm = losers[f].mean()  if f in losers  else 0
        if not (np.isfinite(wm) and np.isfinite(lm)): continue
        print(f"  {f:<20} {wm:>+13.4f} {lm:>+13.4f} {wm-lm:>+12.4f}")

    # ── TEST COMO FILTRO — el modo correcto ───────────────────────────────────
    section("3. OBI COMO FILTRO SOBRE NUESTROS SETUPS (modo correcto)")
    print("  Hipotesis: OBI a favor del trade (positivo para long, negativo para short)")
    print()

    # OBI5
    for thr in [0.0, 0.1, 0.2, 0.3]:
        a_favor = df[df.obi5_dir > thr]
        en_contra = df[df.obi5_dir <= -thr]
        blk(a_favor,   f"OBI5 a favor   > {thr}")
        blk(en_contra, f"OBI5 en contra < -{thr}")
        print()

    # CVD slope
    section("4. CVD SLOPE COMO FILTRO")
    for thr in [0.0, 0.5, 1.0]:
        a_favor = df[df.cvd_slope_dir > thr]
        blk(a_favor, f"CVD slope a favor > {thr}")
    blk(df[df.cvd_slope_dir <= 0], "CVD slope en contra <= 0")

    # Spread
    section("5. SPREAD COMO FILTRO (spread bajo = mercado liquido = mejor fill)")
    spread_med = df.spread.median()
    blk(df[df.spread <= spread_med],  f"Spread bajo (<= mediana {spread_med:.2f}bps)")
    blk(df[df.spread > spread_med],   f"Spread alto  (> mediana {spread_med:.2f}bps)")

    # ── DIAGNOSTICO CLAVE: por que no funciona ────────────────────────────────
    section("6. DIAGNOSTICO — por que L2 no funciona como filtro VP")

    print("\n  A. ¿El OBI es OPUESTO al movimiento que necesitamos?")
    print("     (En niveles VP el precio VIENE en contra del trade -> OBI debe ser negativo al fill)")
    long_trades = df[df.side == "long"]
    short_trades = df[df.side == "short"]

    if len(long_trades) > 0:
        obi_at_fill_long = long_trades.obi5.mean()
        print(f"     OBI5 medio en fills LONG:  {obi_at_fill_long:+.4f}  "
              f"(esperado NEGATIVO — precio cae al nivel)")

    if len(short_trades) > 0:
        obi_at_fill_short = short_trades.obi5.mean()
        print(f"     OBI5 medio en fills SHORT: {obi_at_fill_short:+.4f}  "
              f"(esperado POSITIVO — precio sube al nivel)")

    print("\n  B. Varianza del OBI en nuestros trades vs universo total")
    all_obi = t["obi5_mean"].dropna()
    trade_obi = df["obi5"].dropna()
    print(f"     OBI5 universo: mean={all_obi.mean():+.4f}  std={all_obi.std():.4f}  "
          f"p25={all_obi.quantile(.25):+.4f}  p75={all_obi.quantile(.75):+.4f}")
    print(f"     OBI5 trades:   mean={trade_obi.mean():+.4f}  std={trade_obi.std():.4f}  "
          f"p25={trade_obi.quantile(.25):+.4f}  p75={trade_obi.quantile(.75):+.4f}")

    print("\n  C. ¿VPIN (toxicidad) importa? — mas VPIN = mas informed trading = peor para fade")
    vpin_med = df.vpin.median()
    blk(df[df.vpin <= vpin_med],  f"VPIN bajo  (<= {vpin_med:.3f}) — menos informed")
    blk(df[df.vpin > vpin_med],   f"VPIN alto  (>  {vpin_med:.3f}) — mas informed")

    print("\n  D. Resumen del diagnostico")
    corr_obi = np.corrcoef(df.obi5_dir.fillna(0), df.r)[0,1]
    corr_cvd = np.corrcoef(df.cvd_slope_dir.fillna(0), df.r)[0,1]
    corr_sp  = np.corrcoef(df.spread.fillna(df.spread.median()), df.r)[0,1]
    print(f"     corr(OBI5_dir, R)     = {corr_obi:+.4f}")
    print(f"     corr(CVD_slope, R)    = {corr_cvd:+.4f}")
    print(f"     corr(spread, R)       = {corr_sp:+.4f}")

    if abs(corr_obi) < 0.05:
        print("\n  CONCLUSION: OBI/CVD son RUIDO PURO en el contexto de nuestros setups VP.")
        print("  Mecanismo probable:")
        print("  1. El nivel VP ya SELECCIONA la direccion -> OBI es redundante")
        print("  2. En el momento del fill, OBI refleja el flujo QUE NOS LLENO la orden")
        print("     (precio cae al nivel = OBI negativo para long = no hay info adicional)")
        print("  3. El trade dura horas; OBI de M15 es irrelevante 2h despues")
        print("  4. Manipulacion: MM muestran OBI falso en niveles para baitear retail")


if __name__ == "__main__":
    main()
