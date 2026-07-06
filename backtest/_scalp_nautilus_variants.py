"""
_scalp_nautilus_variants.py — variantes sc3 en NAUTILUS, CAUSAL, 1 año (sin sesgo de fills).
================================================================================
Testea las mejoras no probadas con fills tick reales y placement CAUSAL (la señal de la barra i
se coloca en la barra i+1 → sin lookahead del cierre). Carga ticks UNA vez por chunk y corre
TODAS las variantes sobre los mismos ticks (eficiente).
  baseline · #3 confirm (vela giro) · #4 div multibar · #5 block contra-tendencia
Uso: python backtest/_scalp_nautilus_variants.py [chunk_days] [max_off]
"""
import sys, glob, datetime
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
import _scalp as SC
import _nautilus_scalp as NS
from _scalp_more import gen_sc3x, ALLK_L, ALLK_S
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OmsType, AccountType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Money
from nautilus_trader.model.currencies import USDT
from nautilus_trader.persistence.wranglers import BarDataWrangler, TradeTickDataWrangler
from nautilus_trader.backtest.models import MakerTakerFeeModel

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
VARIANTS = {
    "sc3":  dict(),     # config CANÓNICA (niveles ampliados + fade). intrabar fill (shift=0).
}
BAR_MS = 5*60_000


def emit_causal(s, sym, extra, shift_bars=1, atr_win=500, max_day=3, cooldown=6, stop_floor_pct=0.15):
    """Señal de barra i → colocada en barra i+shift (CAUSAL). {place_ts: [(side,entry,stop,tp1,tp2,atr)]}.
    Incluye filtro HTF canónico (H1 OR H4 EMA20 OR vr>3) y rr_cap=3.0 de SC3."""
    c = SC.SC3[sym]
    gen0 = gen_sc3x(vr_thr=c["vr_thr"], stop_atr=c["stop_atr"], tol_atr=c["tol_atr"], rr_cap=c["rr_cap"],
                    longk=ALLK_L, shortk=ALLK_S, **extra)
    gen = SC._make_htf_filter(sym, gen0)
    atr_med = pd.Series(s.atr).rolling(atr_win, min_periods=50).median().shift(1).values
    out = {}; cool = 0; dcount = {}
    for i in range(60, s.n-1):
        if i < cool or s.atr[i] <= 0: continue
        if not (np.isfinite(atr_med[i]) and s.atr[i] > atr_med[i]): continue
        d = int(s.day[i])
        if dcount.get(d, 0) >= max_day: continue
        for side, lvl, stop, tp1, tp2, tag in (gen(s, i) or []):
            if not np.isfinite([lvl, stop, tp2]).all(): continue
            ref = s.c[i-1]
            if side == "long" and not (lvl < ref): continue
            if side == "short" and not (lvl > ref): continue
            if stop_floor_pct > 0:
                mr = stop_floor_pct/100.0*lvl
                if abs(lvl-stop) < mr: stop = lvl-mr if side == "long" else lvl+mr
            tp1v = float(tp1) if (tp1 is not None and np.isfinite(tp1)) else np.nan
            key = int(s.ts[i]) + shift_bars*BAR_MS    # CAUSAL: colocar barra siguiente
            out.setdefault(key, []).append((side, float(lvl), float(stop), tp1v, float(tp2), float(s.atr[i])))
            cool = i + cooldown; dcount[d] = dcount.get(d, 0) + 1
            break
    return out


def load_ticks(symbol, files):
    m1p = SC.ASSETS[symbol]["m1"]
    tr = pd.concat([pd.read_parquet(f, columns=["ts_ms","price","size","side"]) for f in files])
    tr = tr[tr["price"] != tr["price"].shift()]
    win_lo = int(tr.ts_ms.iloc[0]); win_hi = int(tr.ts_ms.iloc[-1])
    m1 = pd.read_parquet(m1p, columns=["ts_ms","open","high","low","close","volume"])
    m1 = m1[(m1.ts_ms >= win_lo) & (m1.ts_ms <= win_hi)]
    m1.index = pd.to_datetime(m1.ts_ms, unit="ms", utc=True)
    instrument = NS.INSTR[symbol]()
    bt = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    bars = BarDataWrangler(bt, instrument).process(m1[["open","high","low","close","volume"]])
    tr2 = tr.copy(); tr2.index = pd.to_datetime(tr2.ts_ms, unit="ms", utc=True)
    tr2 = tr2.rename(columns={"size":"quantity"}); tr2["trade_id"] = np.arange(len(tr2)).astype(str)
    ticks = TradeTickDataWrangler(instrument).process(tr2[["price","quantity","side","trade_id"]])
    return instrument, str(bt), bars, ticks, win_lo, win_hi


def run_chunk(symbol, instrument, bt, bars, ticks, sig_win):
    venue = instrument.id.venue
    engine = BacktestEngine(config=BacktestEngineConfig(trader_id=TraderId("VAR-001"), logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(venue, OmsType.NETTING, AccountType.MARGIN, starting_balances=[Money(100_000, USDT)],
                     base_currency=USDT, fee_model=MakerTakerFeeModel(),
                     bar_execution=False, trade_execution=True, queue_position=True)
    engine.add_instrument(instrument); engine.add_data(bars); engine.add_data(ticks)
    strat = NS.ScalpStrat(NS.ScalpConfig(instrument_id=str(instrument.id), bar_type=bt, qty=NS.QTY[symbol], bar_ms=BAR_MS), sig_win)
    engine.add_strategy(strat); engine.run()
    out = list(strat.trades)   # (entry_ts, R)
    engine.dispose()
    return out


def main():
    chunk = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    max_off = int(sys.argv[2]) if len(sys.argv) > 2 else 380
    OOS = SC.OOS_MS
    results = {v: [] for v in VARIANTS}   # v -> list of (ts,R)
    for sym in SYMS:
        s = SC.load(sym, 5)
        sig = {v: emit_causal(s, sym, ex, shift_bars=0) for v, ex in VARIANTS.items()}  # intrabar (modelo correcto)
        for v in VARIANTS: print(f"[{sym[:3]}] {v}: {sum(len(x) for x in sig[v].values())} señales", flush=True)
        m1max = int(pd.read_parquet(SC.ASSETS[sym]["m1"], columns=["ts_ms"]).ts_ms.max())
        mday = datetime.datetime.fromtimestamp(m1max/1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
        allf = [f for f in sorted(glob.glob(f"{NS.TICKDIR[sym]}/*.parquet")) if f[-18:-8] <= mday]
        off = 0
        while off <= max_off:
            end_i = len(allf)-off; files = allf[max(0, end_i-chunk):end_i]
            if not files: break
            try:
                instrument, bt, bars, ticks, lo, hi = load_ticks(sym, files)
            except Exception as e:
                print(f"[{sym[:3]} off{off}] load err {e}", flush=True); off += chunk; continue
            for v in VARIANTS:
                sw = {k: val for k, val in sig[v].items() if lo <= k <= hi}
                tr = run_chunk(sym, instrument, bt, bars, ticks, sw)
                results[v].extend((sym[:3], t, r) for t, r in tr)
            print(f"[{sym[:3]} {files[0][-18:-8]}..{files[-1][-18:-8]}] " +
                  " ".join(f"{v}:{len(results[v])}" for v in VARIANTS), flush=True)
            del ticks, bars; import gc; gc.collect()
            off += chunk

    print(f"\n{'='*72}\n  sc3 CANÓNICA — NAUTILUS año completo, 3 activos (fills tick reales intrabar)\n{'='*72}")
    print(f"  {'activo':<8} | {'n':>5} {'WR':>5} {'avgR':>7} {'netR':>7} | {'n_oos':>5} {'OOS avgR':>9} {'OOS WR':>7}")
    d = pd.DataFrame(results["sc3"], columns=["sym","ts","r"]).drop_duplicates(["sym","ts"])
    for sym in ["BTC","ETH","SOL"]:
        g = d[d.sym == sym]
        if not len(g): print(f"  {sym:<8} | sin trades"); continue
        o = g[g.ts >= OOS]
        print(f"  {sym:<8} | {len(g):>5} {100*(g.r>0).mean():>4.0f}% {g.r.mean():>+7.3f} {g.r.sum():>+7.1f} | "
              f"{len(o):>5} {o.r.mean() if len(o) else float('nan'):>+9.3f} {100*(o.r>0).mean() if len(o) else 0:>6.0f}%")
    o = d[d.ts >= OOS]
    cap = 500.0
    for r in d.sort_values("ts").r.values: cap += 5*r
    print(f"  {'PORTF':<8} | {len(d):>5} {100*(d.r>0).mean():>4.0f}% {d.r.mean():>+7.3f} {d.r.sum():>+7.1f} | "
          f"{len(o):>5} {o.r.mean() if len(o) else float('nan'):>+9.3f}")
    print(f"\n  Equity $500 (riesgo fijo $5): ${cap:,.0f}  ·  OOS empieza 2026-03-01")


if __name__ == "__main__":
    main()
