"""
_deriv_predict.py — ¿predice la capa de DERIVADOS (OI/funding) el retorno multi-hora?
======================================================================================
La micro de precio/libro no predice > fee (probado). OI/funding son ORTOGONALES y operan en
horizonte de HORAS, no minutos. Test: panel horario (precio + OI + funding), señales clásicas
de posicionamiento, retorno futuro a 1/4/12/24h, fee-aware (11 bps), IS/OOS.

Señales (dir_hint = +1 alcista / -1 bajista esperado):
  funding_pos_extreme (longs crowded → revierte abajo) / funding_neg_extreme
  price↑+OI↑ (longs nuevos) / price↑+OI↓ (short cover) / price↓+OI↑ (shorts nuevos) / price↓+OI↓ (long liq)
  oi_surge / oi_drop (Δ OI 4h por cuantil)

Uso: TRADIO_PERP=E:/tradio-data/bybit-perp python backtest/_deriv_predict.py
"""
import os, sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
PERP = Path(os.environ.get("TRADIO_PERP", str(ROOT / "data/bybit-perp")))
OOS_MS = int(pd.Timestamp("2026-03-01", tz="UTC").value // 1_000_000)
RT_BPS = 11.0
H = [1, 4, 12, 24]   # horizontes en horas


def hourly_price():
    files = sorted((PERP / "ob_1s").glob("*.parquet"))
    out = []
    for f in files:
        d = pd.read_parquet(f, columns=["ts_ms", "mid"])
        d["h"] = (d["ts_ms"] // 3_600_000) * 3_600_000
        out.append(d.groupby("h")["mid"].last())
    s = pd.concat(out).sort_index()
    return pd.DataFrame({"ts_ms": s.index.astype("int64"), "price": s.values})


def main():
    px = hourly_price()
    oi = pd.read_parquet(PERP / "oi_5m.parquet").sort_values("ts_ms")
    fund = pd.read_parquet(PERP / "funding.parquet").sort_values("ts_ms")

    p = pd.merge_asof(px, oi, on="ts_ms", direction="backward")
    p = pd.merge_asof(p, fund, on="ts_ms", direction="backward")
    p = p.dropna(subset=["price", "open_interest"]).reset_index(drop=True)
    n = len(p)
    price = p["price"].values; oiv = p["open_interest"].values; fnd = p["funding_rate"].values
    is_m = p["ts_ms"].values < OOS_MS

    # forward returns (bps)
    fwd = {}
    for h in H:
        f = np.full(n, np.nan); f[:n-h] = (price[h:] / price[:n-h] - 1) * 1e4; fwd[h] = f

    # features (causales)
    pc1 = np.r_[np.nan, np.diff(price) / price[:-1]]
    oic1 = np.r_[np.nan, np.diff(oiv) / oiv[:-1]]
    def chg(x, k):
        o = np.full(n, np.nan); o[k:] = x[k:] / x[:-k] - 1; return o
    pc4 = chg(price, 4); oic4 = chg(oiv, 4)
    # umbrales por cuantil IS
    def qIS(x, q): return float(np.nanquantile(x[is_m], q))
    f_hi, f_lo = qIS(fnd, 0.90), qIS(fnd, 0.10)
    oi_hi, oi_lo = qIS(oic4, 0.85), qIS(oic4, 0.15)

    sig = {
        "funding_pos_extreme": (fnd >= f_hi, -1),
        "funding_neg_extreme": (fnd <= f_lo, +1),
        "px_up+OI_up":   ((pc4 > 0) & (oic4 > 0), +1),
        "px_up+OI_down": ((pc4 > 0) & (oic4 < 0), -1),
        "px_dn+OI_up":   ((pc4 < 0) & (oic4 > 0), -1),
        "px_dn+OI_down": ((pc4 < 0) & (oic4 < 0), +1),
        "oi_surge_4h":   (oic4 >= oi_hi, 0),
        "oi_drop_4h":    (oic4 <= oi_lo, 0),
    }

    print(f"Panel horario: {n:,} h | IS {int(is_m.sum()):,} / OOS {int((~is_m).sum()):,} | fee {RT_BPS:.0f} bps")
    print(f"funding Q90/Q10 IS: {f_hi:.5f}/{f_lo:.5f} | ΔOI4h Q85/Q15: {oi_hi*100:.2f}%/{oi_lo*100:.2f}%")
    hdr = f'{"señal":<20} {"hint":>4} {"n_is":>6} {"n_oos":>6}'
    for h in H: hdr += f' | {h}h IS/OOS bps'
    print(hdr); print('-'*len(hdr))
    for name, (mask, hint) in sig.items():
        mi, mo = mask & is_m, mask & ~is_m
        line = f'{name:<20} {hint:>+4} {int(mi.sum()):>6} {int(mo.sum()):>6}'
        flag = ''
        for h in H:
            f = fwd[h]
            si = np.nanmean(f[mi] * (hint or 1)) if mi.sum() else np.nan
            so = np.nanmean(f[mo] * (hint or 1)) if mo.sum() else np.nan
            line += f' | {si:>+7.1f}/{so:>+7.1f}'
            if hint != 0 and h in (4, 12) and np.isfinite(si) and np.isfinite(so) and si > 0 and so >= RT_BPS and mo.sum() >= 50:
                flag = ' ✓'
        print(line + flag)
    print(f'\nsigned = dir_hint·retorno_fwd (bps). ✓ = IS/OOS+ y OOS(4h/12h) ≥ {RT_BPS:.0f} bps. hint=0 = retorno crudo.')


if __name__ == "__main__":
    main()
