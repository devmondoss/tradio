"""
_ict_v2.py — Pilares ICT no explorados: sesgo HTF + killzones + target liquidez-opuesta + timeout largo.
Marca [PASS] lo que sea positivo en IS y OOS. Uso: python backtest/_ict_v2.py BTCUSDT
"""
import argparse, itertools, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import _ict as ICT

BASE = dict(fvg_entry=1.0, pen_atr=0.05, stop_buf_atr=0.35, use_delta=True,
            fvg_min_atr=0.30, min_rr=1.2, rr_target=3.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol", nargs="?", default="BTCUSDT")
    ap.add_argument("--tf", type=int, default=15)
    args = ap.parse_args()
    print(f"[v2] cargando {args.symbol} M{args.tf}...")
    a, m1 = ICT.load_symbol(args.symbol, args.tf)
    print(f"[v2] {a.n} barras\n")

    biases = [None, "sma", "slope"]
    kzs = [(None, "24h"), ((7, 16), "Lon+NY")]
    tmodes = ["struct", "range", "rr"]
    timeouts = [480, 1440]

    rows = []
    for bias, (kz, kzn), tm, to in itertools.product(biases, kzs, tmodes, timeouts):
        P = dict(ICT.DEFAULTS); P.update(BASE); P["target_mode"] = tm
        tr = ICT.run_fast(a, m1, P, args.tf, "maker", to, ("long", "short"),
                          volfilter=True, route=True, bias=bias, kz=kz)
        st = ICT.stats(tr)
        if st["n"] >= 20:
            rows.append((bias, kzn, tm, to, st))

    rows.sort(key=lambda r: r[4]["avgR"], reverse=True)
    print(f"=== {args.symbol} M{args.tf} — pilares ICT (orden por avgR) ===")
    print("bias  kz      tmode  to    n   avgR    WR  netR  DD   | isA    oosN oosA   PASS")
    for bias, kzn, tm, to, st in rows:
        oosA = st['oosA'] if st['oosN'] else float('nan')
        pid = st['isA'] > 0 and (st['oosN'] >= 8 and oosA > 0)
        print(f"{str(bias):<5} {kzn:<7} {tm:<5} {to:>4} {st['n']:>4} {st['avgR']:+.3f} {st['wr']:>3.0f} "
              f"{st['netR']:>+6.1f} {st['dd']:>3.0f}  | {st['isA']:+.3f} {st['oosN']:>3} {oosA:+.3f}  "
              f"{'[PASS]' if pid else ''}")


if __name__ == "__main__":
    main()
