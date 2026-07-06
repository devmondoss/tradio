"""
_ict_diag.py — Panel diagnóstico del modelo ICT sobre la MEJOR config del screen.
Aísla: dirección (long/short), filtro ATR, discount, TF (M15/M5), maker vs taker.
Uso: python backtest/_ict_diag.py BTCUSDT
"""
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import _ict as ICT

BASE = dict(fvg_entry=1.0, pen_atr=0.05, stop_buf_atr=0.35, min_rr=1.5,
            use_delta=True, fvg_min_atr=0.30)


def line(name, tr):
    st = ICT.stats(tr)
    if st["n"] == 0:
        print(f"  {name:<26} SIN TRADES"); return
    oosA = st['oosA'] if st['oosN'] else float('nan')
    print(f"  {name:<26} n={st['n']:>3} avgR={st['avgR']:+.3f} WR={st['wr']:>3.0f} "
          f"netR={st['netR']:>+6.1f} | isA={st['isA']:+.3f} oosN={st['oosN']:>3} oosA={oosA:+.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default="BTCUSDT")
    args = ap.parse_args()
    sym = args.symbol
    P = dict(ICT.DEFAULTS); P.update(BASE)

    print(f"== ICT DIAG {sym} (base={BASE}) ==")
    a15, m1 = ICT.load_symbol(sym, 15)
    print(" -- M15 --")
    line("both route ATRfilt", ICT.run_fast(a15, m1, P, 15, "maker", 480, ("long", "short"), volfilter=True, route=True))
    line("long-only", ICT.run_fast(a15, m1, P, 15, "maker", 480, ("long",), volfilter=True, route=True))
    line("short-only", ICT.run_fast(a15, m1, P, 15, "maker", 480, ("short",), volfilter=True, route=True))
    line("both NO ATRfilt", ICT.run_fast(a15, m1, P, 15, "maker", 480, ("long", "short"), volfilter=False, route=True))
    Pnd = dict(P); Pnd["discount"] = False
    line("both NO discount", ICT.run_fast(a15, m1, Pnd, 15, "maker", 480, ("long", "short"), volfilter=True, route=True))
    line("both taker(CISD close)", ICT.run_fast(a15, m1, P, 15, "taker", 480, ("long", "short"), volfilter=True, route=True))
    line("both fade-only", ICT.run_fast(a15, m1, P, 15, "maker", 480, ("long", "short"), volfilter=True, route=False))

    a5, _ = ICT.load_symbol(sym, 5)
    print(" -- M5 --")
    line("both route ATRfilt", ICT.run_fast(a5, m1, P, 5, "maker", 480, ("long", "short"), volfilter=True, route=True))
    line("both NO ATRfilt", ICT.run_fast(a5, m1, P, 5, "maker", 480, ("long", "short"), volfilter=False, route=True))


if __name__ == "__main__":
    main()
