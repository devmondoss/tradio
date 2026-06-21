"""
_range_fade.py — Delta Range Reversal con Absorción (Delta_Range_Reversal_v3_Unificado.md)
==========================================================================================
Tesis (transcripciones): la ESTRUCTURA es el edge (fade de extremos de un rango intradía hacia
el mid/POC); el ORDERFLOW (absorción/delta/CVD) es la CAPA DE CONFIRMACIÓN que separa los fades
buenos de las rupturas reales. Test: medir el fade base SOLO y luego con orderflow encima, para
ver el aporte incremental del orderflow. Causal, fee real (11 bps RT), IS/OOS 2026-03-01.

Base (estructura): M5. Rango = ventana W previa (high/low), tamaño 0.8-3.5 ATR, sin deriva fuerte.
Toque de extremo (<=tol ATR). Long en low / short en high. Stop = extremo ∓ 0.25 ATR. TP = mid (TP1)
o extremo opuesto (TP2). Solo sesiones líquidas (London 07-10, NY 13-17 UTC).

Orderflow (confirmación, togglable):
  veto ruptura:  close acepta fuera del rango con VR>=4 & |DZ|>=2  → NO fade
  absorción:     DZ contra el nivel (<=-1.5 long / >=+1.5 short) & VR>=2 & cierre de vuelta dentro & reclaim
  DZ = z-score(delta,50) ; VR = volume / SMA(volume,50)

Uso: python backtest/_range_fade.py [--tp mid|opp] [--W 20] [--tol 0.25]
"""
import argparse, os
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
M1 = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"
OOS_MS = int(pd.Timestamp("2026-03-01", tz="UTC").value // 1_000_000)
FEE_RT = 0.0011          # 11 bps round-trip taker
CAP0, RISK = 500.0, 0.01


def atr(h, l, c, n=14):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    out = np.full(len(tr), np.nan); out[n-1] = np.nanmean(tr[:n]); k = 1.0/n
    for i in range(n, len(tr)): out[i] = out[i-1]*(1-k) + tr[i]*k
    return out


def resample_m5(df):
    g = (df["ts_ms"].values // 300_000) * 300_000
    b = pd.DataFrame({"g": g, "o": df["open"].values, "h": df["high"].values, "l": df["low"].values,
                      "c": df["close"].values, "v": df["volume"].values, "d": df["delta"].values})
    a = b.groupby("g").agg(open=("o","first"), high=("h","max"), low=("l","min"), close=("c","last"),
                           volume=("v","sum"), delta=("d","sum")).reset_index()
    return a.rename(columns={"g": "ts_ms"})


def run(df, W, tol, tp_mode, use_veto, use_absorb, minstop_bps=25.0, sessions_only=True):
    t = resample_m5(df)
    ts = t["ts_ms"].values.astype(np.int64)
    o,h,l,c = (t[x].values.astype(float) for x in ("open","high","low","close"))
    vol, delta = t["volume"].values.astype(float), t["delta"].values.astype(float)
    n = len(t); a = atr(h,l,c)
    S = pd.Series
    rhigh = S(h).rolling(W).max().shift(1).values     # rango = ventana PREVIA (causal)
    rlow  = S(l).rolling(W).min().shift(1).values
    rmid  = (rhigh + rlow) / 2.0
    rsize = rhigh - rlow
    drift = np.abs(c - S(c).shift(W).values)          # deriva en la ventana
    vol_sma = S(vol).rolling(50).mean().shift(1).values
    d_mean  = S(delta).rolling(50).mean().shift(1).values
    d_std   = S(delta).rolling(50).std().shift(1).values + 1e-9
    DZ = (delta - d_mean) / d_std
    VR = vol / (vol_sma + 1e-9)
    hm = (ts // 60_000) % 1440
    sess = ((hm >= 7*60) & (hm < 10*60)) | ((hm >= 13*60) & (hm < 17*60))

    range_ok = np.isfinite(a) & (rsize >= 0.8*a) & (rsize <= 3.5*a) & (drift < 0.5*rsize)
    if sessions_only: range_ok &= sess

    trades = []; next_ok = 0
    cap = CAP0; peak = CAP0; dd = 0.0; risk_amt = CAP0*RISK; cur_mo = -1
    timeout = 48  # 4h en M5
    for i in range(W+50, n-1):
        if i < next_ok or not range_ok[i]:
            continue
        mo = pd.Timestamp(ts[i], unit="ms").to_period("M").ordinal
        if mo != cur_mo: risk_amt = cap*RISK; cur_mo = mo
        for side in ("long","short"):
            mins = minstop_bps/1e4 * c[i]   # piso de stop fee-survivable
            if side == "long":
                touch = l[i] <= rlow[i] + tol*a[i]
                if not touch: continue
                if use_veto and (c[i] < rlow[i]) and VR[i] >= 4 and DZ[i] <= -2: continue  # ruptura real
                if use_absorb and not (DZ[i] <= -1.0 and VR[i] >= 1.5 and c[i] > rlow[i]): continue  # absorción (venta agresiva fallida)
                entry = c[i]; stop = min(l[i], rlow[i]) - 0.25*a[i]; stop = min(stop, entry - mins)
                tp = rmid[i] if tp_mode == "mid" else rhigh[i]
                if not (stop < entry < tp): continue
            else:
                touch = h[i] >= rhigh[i] - tol*a[i]
                if not touch: continue
                if use_veto and (c[i] > rhigh[i]) and VR[i] >= 4 and DZ[i] >= 2: continue
                if use_absorb and not (DZ[i] >= 1.0 and VR[i] >= 1.5 and c[i] < rhigh[i]): continue
                entry = c[i]; stop = max(h[i], rhigh[i]) + 0.25*a[i]; stop = max(stop, entry + mins)
                tp = rmid[i] if tp_mode == "mid" else rlow[i]
                if not (tp < entry < stop): continue
            risk_px = abs(entry - stop)
            if risk_px <= 0: continue
            rr = abs(tp - entry) / risk_px
            if rr < 1.0: continue
            # simular salida (path causal)
            res = None
            for j in range(i+1, min(i+1+timeout, n)):
                if side == "long":
                    if l[j] <= stop: res = -1.0; break
                    if h[j] >= tp:   res = rr;  break
                else:
                    if h[j] >= stop: res = -1.0; break
                    if l[j] <= tp:   res = rr;  break
                xi = j
            if res is None:  # timeout: marca a mercado
                px = c[min(i+timeout, n-1)]
                res = ((px-entry) if side=="long" else (entry-px)) / risk_px
            fee_r = FEE_RT*entry/risk_px
            r = res - fee_r
            pnl = risk_amt*r; cap += pnl
            peak = max(peak, cap); dd = max(dd, (peak-cap)/peak)
            trades.append({"ts": int(ts[i]), "side": side, "r": r, "rr": rr,
                           "oos": int(ts[i]) >= OOS_MS, "win": res > 0})
            next_ok = i + 3
            break
    return trades, cap, dd


def report(name, trades):
    if not trades:
        print(f"{name:<28} sin trades"); return
    df = pd.DataFrame(trades)
    span_days = (df.ts.max()-df.ts.min())/86_400_000
    def block(d, tag):
        if len(d)==0: return f"{tag} n=0"
        wr = 100*d.win.mean(); avgr = d.r.mean()
        sharpe = avgr/(d.r.std()+1e-9)*np.sqrt(len(d)/ (span_days/365*252) +1e-9) if len(d)>1 else 0
        return f"{tag} n={len(d):>4} WR={wr:>4.1f}% avgR={avgr:>+.3f} netR={d.r.sum():>+6.1f}"
    di, do = df[~df.oos], df[df.oos]
    print(f"--- {name} ---")
    print(f"   IS  {block(di,'IS ')} | {len(di)/(span_days*0.66+1e-9):.1f} tr/día")
    print(f"   OOS {block(do,'OOS')} | {len(do)/(span_days*0.34+1e-9):.1f} tr/día")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tp", choices=["mid","opp"], default="opp")
    ap.add_argument("--W", type=int, default=20); ap.add_argument("--tol", type=float, default=0.25)
    ap.add_argument("--minstop", type=float, default=25.0, help="piso de stop en bps")
    args = ap.parse_args()
    print("Cargando M1...")
    df = pd.read_parquet(M1, columns=["ts_ms","open","high","low","close","volume","delta"]).sort_values("ts_ms")
    df = df[df.ts_ms >= pd.Timestamp("2025-06-19", tz="UTC").value//10**6].reset_index(drop=True)
    print(f"TP={args.tp} | W={args.W} | tol={args.tol} ATR | fee {FEE_RT*1e4:.0f}bps | cap ${CAP0:.0f} risk {RISK*100:.0f}%\n")
    cfgs = [
        ("A base estructura (sin OF)",       dict(use_veto=False, use_absorb=False)),
        ("B base + veto ruptura (OF)",       dict(use_veto=True,  use_absorb=False)),
        ("C base + absorción full (OF)",     dict(use_veto=True,  use_absorb=True)),
    ]
    for name, kw in cfgs:
        tr, cap, dd = run(df, args.W, args.tol, args.tp, minstop_bps=args.minstop, **kw)
        report(name, tr)
        print(f"   $500 -> ${cap:,.0f} | MaxDD {dd*100:.0f}%\n")


if __name__ == "__main__":
    main()
