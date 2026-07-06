"""
_ict_sweep.py — Barrido de calibraciones del modelo ICT (_ict.py).
==================================================================
Carga (a, m1) UNA vez por símbolo y corre un grid de configs con DECIMALES.
Reporta tabla ordenada por OOS avgR. Regla dura: positivo en IS y OOS.

Uso:  python backtest/_ict_sweep.py BTCUSDT --tf 15 --mode maker
      python backtest/_ict_sweep.py BTCUSDT --refine     (grid fino)
"""
import argparse, itertools, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import _ict as ICT


def grid_phase1():
    axes = dict(
        fvg_entry=[0.0, 0.5, 1.0],
        pen_atr=[0.05, 0.15],
        stop_buf_atr=[0.10, 0.35],
        min_rr=[1.5, 2.5],
        use_delta=[False, True],
        fvg_min_atr=[0.10, 0.30],
    )
    keys = list(axes)
    for combo in itertools.product(*[axes[k] for k in keys]):
        yield dict(zip(keys, combo))


def grid_refine(base):
    axes = dict(
        fvg_entry=[base["fvg_entry"]-0.2, base["fvg_entry"], base["fvg_entry"]+0.2],
        stop_buf_atr=[0.10, 0.25, 0.5, 0.75],
        min_rr=[1.5, 2.0, 2.5, 3.0],
        max_age=[4, 8, 12],
        discount=[True, False],
    )
    keys = list(axes)
    for combo in itertools.product(*[axes[k] for k in keys]):
        c = dict(base);
        for k, v in zip(keys, combo):
            c[k] = v
        if 0.0 <= c["fvg_entry"] <= 1.0:
            yield c
    return


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default="BTCUSDT")
    ap.add_argument("--tf", type=int, default=15)
    ap.add_argument("--mode", default="maker")
    ap.add_argument("--timeout", type=int, default=480)
    ap.add_argument("--sides", default="both", choices=["both", "long", "short"])
    ap.add_argument("--minn", type=int, default=30)
    ap.add_argument("--top", type=int, default=25)
    args = ap.parse_args()
    sides = ("long", "short") if args.sides == "both" else (args.sides,)

    print(f"[sweep] cargando {args.symbol} M{args.tf}...")
    a, m1 = ICT.load_symbol(args.symbol, args.tf)
    print(f"[sweep] {a.n} barras. Corriendo grid...")

    rows = []
    cfgs = list(grid_phase1())
    for k, P in enumerate(cfgs):
        full = dict(ICT.DEFAULTS); full.update(P)
        st, _ = ICT.run_on(a, m1, full, args.tf, args.mode, args.timeout, sides)
        if st["n"] >= args.minn:
            rows.append((P, st))
        if (k + 1) % 16 == 0:
            print(f"  ...{k+1}/{len(cfgs)}")

    rows.sort(key=lambda r: (r[1].get("oosA", -9) if r[1]["oosN"] >= 8 else -9), reverse=True)
    print(f"\n=== TOP {args.top} de {len(rows)} configs (n>={args.minn}) — {args.symbol} M{args.tf} {args.mode} ===")
    hdr = "fe  pen  sbuf rr   delt fmin | n    avgR    WR  netR   DD   Sh  | oosN oosA   oosWR isA"
    print(hdr)
    for P, st in rows[:args.top]:
        print(f"{P['fvg_entry']:.2f} {P['pen_atr']:.2f} {P['stop_buf_atr']:.2f} {P['min_rr']:.1f}  "
              f"{int(P['use_delta'])}    {P['fvg_min_atr']:.2f} | "
              f"{st['n']:>4} {st['avgR']:+.3f} {st['wr']:>3.0f} {st['netR']:>+6.1f} {st['dd']:>4.0f} {st['sharpe']:>+4.1f} | "
              f"{st['oosN']:>4} {st['oosA']:+.3f} {st['oosWR']:>4.0f}  {st['isA']:+.3f}")


if __name__ == "__main__":
    main()
