"""
_swfvg_grid.py — BARRIDO del setup sweep->FVG sobre sus features.
=================================================================
En vez de UNA calibración a mano, barre los ejes que mueven la geometría R:
  entry  : dónde entrar en el hueco  (top / mid / bot)
  stop   : dónde va el stop          (sweep=bajo el barrido · gap=bajo el hueco · atr=fijo)
  min_gap: fuerza del desplazamiento (0.1 / 0.4 / 0.8 · ATR)
  W      : recencia del FVG          (15 / 30 barras)
  mgmt   : gestión                   (fade+1.5R fijo · fade+2R · routed estructural)

Escribe (sym, cfg, IS avgR/n, OOS avgR/n) a un CSV. Correr para los 3 activos y
luego agregar: una config solo vale si es POSITIVA en IS y OOS en LOS 3 activos.

Uso: python backtest/_swfvg_grid.py SYM /ruta/out.csv
"""
import sys, csv, itertools
from pathlib import Path
import numpy as np
sys.path.insert(0, "backtest")
import _listas2 as L2
from _strategy_ab import run_system

PARQ = {"BTCUSDT": "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
        "ETHUSDT": "E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet",
        "SOLUSDT": "E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"}


def gen_swfvg(W, min_gap, entry, stop_mode, buf=0.25):
    def g(a, i):
        atr = a.atr[i]
        if atr <= 0 or i < 7: return []
        ref = a.c[i-1]
        for k in range(i-1, max(5, i-W)-1, -1):
            # bullish
            gb, gt = a.h[k-2], a.l[k]
            if gt - gb > min_gap*atr:
                seg = a.l[k+1:i]
                if (seg.size == 0 or np.nanmin(seg) > gt) and a.l[i] <= gt and gt < ref:
                    win_lo = np.nanmin(a.l[k-3:k+1])
                    liq = np.nanmax([a.swing_low_50[k], a.prev_day_low[k]])
                    if np.isfinite(liq) and win_lo < liq:
                        e = {"top": gt, "mid": (gb+gt)/2, "bot": gb}[entry]
                        stop = {"sweep": win_lo-buf*atr, "gap": gb-buf*atr, "atr": e-1.0*atr}[stop_mode]
                        tp1, tp2 = L2.struct_target(a, i, "long", e)
                        if np.isfinite(tp2) and stop < e < tp2 and e < ref:
                            return [("long", e, stop, tp1, tp2, "swfvg")]
            # bearish
            gt2, gb2 = a.l[k-2], a.h[k]
            if gt2 - gb2 > min_gap*atr:
                seg = a.h[k+1:i]
                if (seg.size == 0 or np.nanmax(seg) < gb2) and a.h[i] >= gb2 and gb2 > ref:
                    win_hi = np.nanmax(a.h[k-3:k+1])
                    liq = np.nanmin([a.swing_high_50[k], a.prev_day_high[k]])
                    if np.isfinite(liq) and win_hi > liq:
                        e = {"top": gb2, "mid": (gb2+gt2)/2, "bot": gt2}[entry]
                        stop = {"sweep": win_hi+buf*atr, "gap": gt2+buf*atr, "atr": e+1.0*atr}[stop_mode]
                        tp1, tp2 = L2.struct_target(a, i, "short", e)
                        if np.isfinite(tp2) and tp2 < e < stop and e > ref:
                            return [("short", e, stop, tp1, tp2, "swfvg")]
        return []
    return g


GRID = dict(
    entry=["top", "mid", "bot"],
    stop=["sweep", "gap", "atr"],
    min_gap=[0.1, 0.4, 0.8],
    W=[15, 30],
    mgmt=[("fade", 1.5), ("fade", 2.0), ("routed", 0.0)],
)


def main():
    sym = sys.argv[1]; out = sys.argv[2]
    L2.M1 = Path(PARQ[sym]); t = L2.load2(15, start_ms=0); a = L2.A2(t); m1 = L2.load_m1_exit(start_ms=0)
    keys = list(GRID); rows = []
    for combo in itertools.product(*[GRID[k] for k in keys]):
        p = dict(zip(keys, combo)); mode, cap = p["mgmt"]
        gen = gen_swfvg(p["W"], p["min_gap"], p["entry"], p["stop"])
        df = run_system(a, [gen], m1, 15, mode=mode, tp2_cap_r=cap,
                        max_day=4, cooldown=3, min_range=0.0)
        if not len(df): continue
        o = df[df.oos]; ii = df[~df.oos]
        cfg = f"e={p['entry']},s={p['stop']},g={p['min_gap']},W={p['W']},m={mode}{cap}"
        rows.append([sym, cfg, len(ii), ii.r.mean() if len(ii) else 0,
                     len(o), o.r.mean() if len(o) else 0])
    write_header = not Path(out).exists()
    with open(out, "a", newline="") as f:
        w = csv.writer(f)
        if write_header: w.writerow(["sym", "cfg", "is_n", "is_avgr", "oos_n", "oos_avgr"])
        w.writerows(rows)
    print(f"{sym}: {len(rows)} configs -> {out}")


if __name__ == "__main__":
    main()
