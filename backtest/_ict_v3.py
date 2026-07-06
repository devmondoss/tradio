"""
_ict_v3.py — Regla dura cross-asset: corre los configs candidatos (PASS en BTC) sobre BTC/ETH/SOL.
Marca [ALL3] si es positivo IS y OOS en los tres. Uso: python backtest/_ict_v3.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import _ict as ICT

E = dict(fvg_entry=1.0, pen_atr=0.05, stop_buf_atr=0.35, use_delta=True, fvg_min_atr=0.30, min_rr=1.2, rr_target=3.0)

# (nombre, bias, kz, timeout, target_mode)
CANDS = [
    ("slope/Lon+NY/rr/480",    "slope", (7, 16), 480,  "rr"),
    ("slope/Lon+NY/rr/1440",   "slope", (7, 16), 1440, "rr"),
    ("sma/24h/struct/480",     "sma",   None,    480,  "struct"),
    ("slope/24h/rr/480",       "slope", None,    480,  "rr"),
    ("slope/24h/struct/1440",  "slope", None,    1440, "struct"),
    ("slope/Lon+NY/struct/480","slope", (7, 16), 480,  "struct"),
]


def main():
    syms = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    data = {}
    for s in syms:
        print(f"[v3] cargando {s}...")
        data[s] = ICT.load_symbol(s, 15)

    results = {}
    for name, bias, kz, to, tm in CANDS:
        results[name] = {}
        for s in syms:
            a, m1 = data[s]
            P = dict(ICT.DEFAULTS); P.update(E); P["target_mode"] = tm
            tr = ICT.run_fast(a, m1, P, 15, "maker", to, ("long", "short"),
                              volfilter=True, route=True, bias=bias, kz=kz)
            results[name][s] = ICT.stats(tr)

    print("\n=== CROSS-ASSET (regla dura: + IS y + OOS en los 3) ===")
    for name, _, _, _, _ in CANDS:
        print(f"\n{name}")
        all3 = True
        for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
            st = results[name][s]
            if st["n"] == 0:
                print(f"  {s}: sin trades"); all3 = False; continue
            oosA = st['oosA'] if st['oosN'] else float('nan')
            ok = (st['isA'] > 0) and (st['oosN'] >= 6 and oosA > 0)
            all3 = all3 and ok
            print(f"  {s}: n={st['n']:>3} avgR={st['avgR']:+.3f} WR={st['wr']:>3.0f} | "
                  f"isA={st['isA']:+.3f} oosN={st['oosN']:>3} oosA={oosA:+.3f} {'ok' if ok else ''}")
        print(f"  => {'[ALL3 PASS]' if all3 else 'no'}")


if __name__ == "__main__":
    main()
