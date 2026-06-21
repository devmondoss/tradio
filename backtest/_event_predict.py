"""
_event_predict.py — ¿predice cada EVENTO de microestructura el retorno futuro? (fee-aware, IS/OOS)
==================================================================================================
Lee data/bybit-perp/events.parquet (de build_events.py) y mide, por tipo de evento:
  - retorno futuro SIGNADO por la hipótesis (dir_hint · fwd) a 1/5/15 min, IS y OOS
    → si dir_hint=+1 (alcista) y el precio sube, signed>0. Una señal sirve si signed > costo.
  - hit rate (% con signo correcto), MFE/MAE medios
  - baseline ≈ 0 (mid es casi-martingala intradía; confirmado en el estudio M1)

Costo RT futuros = 11 bps. ✓ = IS y OOS mismo signo positivo y signed_OOS(5m o 15m) ≥ 6 bps con n_oos≥50.

Uso: python backtest/_event_predict.py
"""
import sys, os
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
PERP = Path(os.environ.get("TRADIO_PERP", str(ROOT / "data/bybit-perp")))
EV = PERP / "events.parquet"
RT_BPS = 11.0


def main():
    if not EV.exists(): sys.exit("Falta events.parquet — corré build_events.py")
    ev = pd.read_parquet(EV)
    n_is = int((~ev["oos"]).sum()); n_oos = int(ev["oos"].sum())
    span = (pd.Timestamp(ev.ts_ms.max(), unit='ms') - pd.Timestamp(ev.ts_ms.min(), unit='ms')).days
    print(f"Eventos: {len(ev):,} | IS {n_is:,} / OOS {n_oos:,} | span {span} días | costo RT {RT_BPS:.0f} bps")
    if n_is == 0 or n_oos == 0:
        print("⚠ Sin split IS/OOS aún (faltan días pre/post 2026-03-01). Mostrando POOLED.\n")

    hdr = f'{"evento":<17} {"hint":>4} {"n_is":>6} {"n_oos":>6}'
    for h in ("1m", "5m", "15m"): hdr += f' | {h} signed IS/OOS'
    hdr += ' | hit15 IS/OOS | MFE/MAE'
    print(hdr); print('-' * len(hdr))

    def agg(d, col, hint):
        if len(d) == 0: return np.nan
        return float(np.nanmean(d[col].values * (hint if hint != 0 else 1)))

    rows = []
    for name, g in ev.groupby("event"):
        hint = int(g["dir_hint"].iloc[0])
        gi, go = g[~g["oos"]], g[g["oos"]]
        line = f'{name:<17} {hint:>+4} {len(gi):>6} {len(go):>6}'
        flag = ''
        for h, col in (("1m", "fwd_1m"), ("5m", "fwd_5m"), ("15m", "fwd_15m")):
            si, so = agg(gi, col, hint), agg(go, col, hint)
            line += f' | {si:>+6.1f}/{so:>+6.1f}'
            if h in ("5m", "15m") and np.isfinite(si) and np.isfinite(so) \
               and si > 0 and so >= 6 and len(go) >= 50:
                flag = ' ✓'
        # hit rate (signo correcto) a 15m
        def hit(d):
            if len(d) == 0 or hint == 0: return np.nan
            return 100 * float(np.mean(np.sign(d["fwd_15m"].values) == hint))
        hi, ho = hit(gi), hit(go)
        mfe = float(np.nanmean(g["mfe_15m"])); mae = float(np.nanmean(g["mae_15m"]))
        line += f' | {hi:>4.0f}/{ho:>4.0f} | {mfe:>+5.1f}/{mae:>+5.1f}'
        rows.append((name, so if np.isfinite(so) else -999))
        print(line + flag)

    print(f'\nLeyenda: signed = dir_hint·retorno_fwd (bps). Para ser tradeable necesita > ~{RT_BPS:.0f} bps.')
    print('hint=0 (vol_spike/spread_spike) no es direccional → signed = retorno crudo.')


if __name__ == "__main__":
    main()
