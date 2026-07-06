"""
_scalp_btcguard.py — ¿el movimiento concurrente de BTC predice los fades de alts? (ETH/SOL)
================================================================================
Tesis (estructura de mercado): BTC lidera. Fadear un LONG de SOL/ETH mientras BTC cae fuerte
es peligroso — el nivel lo barre una venta dirigida por BTC. Medimos el retorno de BTC en la
ventana previa al trade de la alt y vemos si predice/filtra el resultado.
  btc_align = retorno BTC × signo del trade (long=+1/short=-1):
     >0 = BTC se mueve A FAVOR del fade (tailwind) · <0 = BTC EN CONTRA (peligro)
Uso: python backtest/_scalp_btcguard.py
"""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
import _scalp as SC


def main():
    btc = SC.load("BTCUSDT", 5)
    # retornos BTC en ventanas previas (causal): 15m (3 barras) y 1h (12 barras)
    bt = btc.ts; bc = btc.c
    ret15 = np.full(btc.n, np.nan); ret1h = np.full(btc.n, np.nan)
    for i in range(12, btc.n):
        ret15[i] = bc[i]/bc[i-3]-1
        ret1h[i] = bc[i]/bc[i-12]-1
    bts = bt.astype(np.int64)

    def lookup(ts, arr):
        k = np.searchsorted(bts, ts, side="right")-1
        return arr[k] if 0 <= k < len(arr) else np.nan

    allrows = []
    for sym in ("ETHUSDT", "SOLUSDT"):
        df, _ = SC.run_sc3(sym)
        for r in df.itertuples():
            sgn = 1.0 if r.side == "long" else -1.0
            r15 = lookup(int(r.ts), ret15); r1h = lookup(int(r.ts), ret1h)
            allrows.append((sym[:3], r.r, r.oos, r.side, sgn*r15, sgn*r1h, r15, r1h))
    a = pd.DataFrame(allrows, columns=["sym","r","oos","side","align15","align1h","btc15","btc1h"]).dropna()
    print(f"ETH+SOL: {len(a)} trades · OOS base avgR {a[a.oos].r.mean():+.3f}\n")

    def buckets(col):
        print(f"  {col}: Pearson(.,r)={a[[col,'r']].corr().iloc[0,1]:+.3f}")
        a["q"] = pd.qcut(a[col], 5, labels=["Q1↓","Q2","Q3","Q4","Q5↑"], duplicates="drop")
        for q, g in a.groupby("q", observed=True):
            go = g[g.oos]
            print(f"    {q} [{g[col].min():+.3%}..{g[col].max():+.3%}]  avgR {g.r.mean():+.3f}  "
                  f"OOS {go.r.mean() if len(go) else float('nan'):+.3f}  WR {100*(g.r>0).mean():3.0f}%  n {len(g)}")

    print("── BTC alineado al fade, ventana 15m (align15 = retBTC×signo) ──"); buckets("align15")
    print("\n── BTC alineado, ventana 1h ──"); buckets("align1h")

    # filtro: descartar fades con BTC FUERTEMENTE en contra
    print("\n── FILTROS (OOS): descartar BTC en contra ──")
    base = a[a.oos].r.mean()
    for name, thr, col in [("BTC contra >0.3% 15m", -0.003, "align15"),
                            ("BTC contra >0.5% 15m", -0.005, "align15"),
                            ("BTC contra >0.5% 1h", -0.005, "align1h"),
                            ("BTC contra >1.0% 1h", -0.010, "align1h")]:
        keep = a[(a[col] >= thr) & a.oos]; drop = a[(a[col] < thr) & a.oos]
        print(f"  {name:<22} keep n {len(keep):>4} OOS {keep.r.mean():+.3f} (Δ {keep.r.mean()-base:+.3f}) | "
              f"descartados n {len(drop):>3} avgR {drop.r.mean():+.3f}")
    # por activo el mejor filtro
    print("\n── por activo (filtro align1h≥−0.5%) ──")
    for sym in ("ETH", "SOL"):
        s = a[a.sym == sym]; base_s = s[s.oos].r.mean()
        keep = s[(s.align1h >= -0.005) & s.oos]
        print(f"  {sym}: base OOS {base_s:+.3f} → filtrado {keep.r.mean():+.3f} (Δ {keep.r.mean()-base_s:+.3f}, n {len(keep)})")


if __name__ == "__main__":
    main()
