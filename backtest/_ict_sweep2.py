"""
_ict_sweep2.py — Screen con PALANCAS (filtro ATR + routing fade/trail) e IS/OOS.
Grid enfocado en la región prometedora del phase-1. Aísla el efecto del routing.

Uso: python backtest/_ict_sweep2.py BTCUSDT
"""
import argparse, itertools, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import _ict as ICT


def stat_line(tr):
    return ICT.stats(tr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default="BTCUSDT")
    ap.add_argument("--tf", type=int, default=15)
    args = ap.parse_args()

    print(f"[s2] cargando {args.symbol} M{args.tf}...")
    a, m1 = ICT.load_symbol(args.symbol, args.tf)
    print(f"[s2] {a.n} barras\n")

    axes = dict(
        fvg_entry=[0.0, 1.0],
        pen_atr=[0.05, 0.15],
        stop_buf_atr=[0.10, 0.35],
        min_rr=[1.5, 2.5],
        use_delta=[True],
        fvg_min_atr=[0.30],
    )
    keys = list(axes)
    cfgs = [dict(zip(keys, c)) for c in itertools.product(*[axes[k] for k in keys])]

    rows = []
    for P in cfgs:
        full = dict(ICT.DEFAULTS); full.update(P)
        for route, trail, tag in [(True, 6.0, "fade/trail"), (False, 6.0, "fade-only")]:
            tr = ICT.run_fast(a, m1, full, args.tf, "maker", 480, ("long", "short"),
                              volfilter=True, route=route, trail_atr=trail)
            st = stat_line(tr)
            if st["n"] >= 25:
                rows.append((P, tag, st))

    rows.sort(key=lambda r: r[2]["avgR"], reverse=True)
    print(f"=== {args.symbol} M{args.tf} con FILTRO ATR + routing (orden por avgR global) ===")
    print("fe  pen  sbuf rr  fmin | route       n   avgR    WR  netR  DD  Sh  | isA    oosN oosA  oosWR")
    for P, tag, st in rows:
        oosA = st['oosA'] if st['oosN'] else float('nan')
        print(f"{P['fvg_entry']:.2f} {P['pen_atr']:.2f} {P['stop_buf_atr']:.2f} {P['min_rr']:.1f} {P['fvg_min_atr']:.2f} | "
              f"{tag:<10} {st['n']:>3} {st['avgR']:+.3f} {st['wr']:>3.0f} {st['netR']:>+6.1f} {st['dd']:>3.0f} {st['sharpe']:>+4.1f} | "
              f"{st['isA']:+.3f} {st['oosN']:>3} {oosA:+.3f} {st['oosWR']:>4.0f}")


if __name__ == "__main__":
    main()
