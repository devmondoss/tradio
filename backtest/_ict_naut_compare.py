"""Corre el mejor config ICT en Nautilus (fills maker reales) sobre BTC/ETH/SOL."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import _nautilus_ict as NI
from _ict import DEFAULTS

BASE = dict(fvg_entry=1.0, pen_atr=0.05, stop_buf_atr=0.35, min_rr=1.5,
            use_delta=True, fvg_min_atr=0.30)

def main():
    P = dict(DEFAULTS); P.update(BASE)
    print(f"=== ICT en Nautilus (fills maker reales) — base={BASE} ===")
    print("sym     placed fill%  n   WR   avgR    netR")
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        try:
            r = NI.run(sym, 900000, P, tf_min=15, trail_atr=6.0, volfilter=True,
                       sides=("long", "short"), verbose=False)
        except Exception as e:
            print(f"{sym}: ERR {e}"); continue
        if r.get("filled"):
            print(f"{sym:7} {r['placed']:>5} {r['fill']:>4.0f}  {r['filled']:>3} {r['wr']:>3.0f}  "
                  f"{r['avgR']:+.3f}  {r['netR']:>+6.1f}")
        else:
            print(f"{sym:7} {r['placed']:>5} {r['fill']:>4.0f}   0  sin fills")

if __name__ == "__main__":
    main()
