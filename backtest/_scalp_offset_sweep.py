"""
_scalp_offset_sweep.py — frontera OFFSET → (fill ratio, avgR) con fills reales tick.
================================================================================
Mide el trade-off de placement: posar el límite maker MÁS o MENOS profundo que el nivel.
  offset_bps = 0  → en el nivel exacto (mejor precio, menos fills)
  offset_bps > 0  → menos profundo, hacia el mercado (más fills, peor precio)
Carga ticks UNA vez por activo y corre todos los offsets sobre los mismos ticks.
Objetivo: maximizar fill_ratio × avgR (R esperado por orden COLOCADA).

Uso: python backtest/_scalp_offset_sweep.py [window_days]
"""
import sys, glob, datetime
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
import _scalp as SC
import _nautilus_scalp as NS
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OmsType, AccountType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Money
from nautilus_trader.model.currencies import USDT
from nautilus_trader.persistence.wranglers import BarDataWrangler, TradeTickDataWrangler
from nautilus_trader.backtest.models import MakerTakerFeeModel

OFFSETS = [0.0, 2.0, 5.0, 10.0]   # bps menos profundo
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def load_asset(symbol, tf, days):
    cfg = dict(NS.UNI); cfg["vr_thr"] = NS.VR[symbol]
    s = SC.load(symbol, tf)
    signals = NS.emit_sc3(s, tf, cfg)
    m1p = SC.ASSETS[symbol]["m1"]
    m1_max = int(pd.read_parquet(m1p, columns=["ts_ms"]).ts_ms.max())
    m1_max_day = datetime.datetime.fromtimestamp(m1_max/1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    allf = [f for f in sorted(glob.glob(f"{NS.TICKDIR[symbol]}/*.parquet")) if f[-18:-8] <= m1_max_day][-days:]
    tr = pd.concat([pd.read_parquet(f, columns=["ts_ms","price","size","side"]) for f in allf])
    tr = tr[tr["price"] != tr["price"].shift()]
    win_lo = int(tr.ts_ms.iloc[0]); win_hi = int(tr.ts_ms.iloc[-1])
    m1 = pd.read_parquet(m1p, columns=["ts_ms","open","high","low","close","volume"])
    m1 = m1[(m1.ts_ms >= win_lo) & (m1.ts_ms <= win_hi)]
    m1.index = pd.to_datetime(m1.ts_ms, unit="ms", utc=True)
    sig_win = {k: v for k, v in signals.items() if win_lo <= k <= win_hi}
    instrument = NS.INSTR[symbol]()
    bt = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    bars = BarDataWrangler(bt, instrument).process(m1[["open","high","low","close","volume"]])
    tr2 = tr.copy(); tr2.index = pd.to_datetime(tr2.ts_ms, unit="ms", utc=True)
    tr2 = tr2.rename(columns={"size":"quantity"}); tr2["trade_id"] = np.arange(len(tr2)).astype(str)
    ticks = TradeTickDataWrangler(instrument).process(tr2[["price","quantity","side","trade_id"]])
    span = (win_hi-win_lo)/86_400_000
    print(f"  {symbol[:3]}: {allf[0][-18:-8]}..{allf[-1][-18:-8]} ({span:.0f}d) · {len(ticks):,} ticks · "
          f"{sum(len(v) for v in sig_win.values())} señales", flush=True)
    return instrument, str(bt), bars, ticks, sig_win


def run_offset(symbol, instrument, bt, bars, ticks, sig_win, tf, offset_bps):
    venue = instrument.id.venue
    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=TraderId("OFF-001"), logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(venue, OmsType.NETTING, AccountType.MARGIN,
                     starting_balances=[Money(100_000, USDT)], base_currency=USDT,
                     fee_model=MakerTakerFeeModel(),
                     bar_execution=False, trade_execution=True, queue_position=True)
    engine.add_instrument(instrument); engine.add_data(bars); engine.add_data(ticks)
    strat = NS.ScalpStrat(NS.ScalpConfig(instrument_id=str(instrument.id), bar_type=bt,
                          qty=NS.QTY[symbol], bar_ms=tf*60_000, offset_bps=offset_bps), sig_win)
    engine.add_strategy(strat); engine.run()
    R = np.array(strat.closed_R)
    placed = strat.n_placed; filled = len(R)
    fr = filled/max(placed,1)
    avgR = R.mean() if filled else 0.0
    exp_per_placed = fr*avgR   # R esperado por orden colocada
    engine.dispose()
    return dict(placed=placed, filled=filled, fr=100*fr, avgR=avgR, netR=R.sum() if filled else 0.0,
                wr=100*(R>0).mean() if filled else 0.0, exp=exp_per_placed)


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    tf = 5
    print(f"FRONTERA OFFSET (ventana {days}d, fills tick reales + cola)\n")
    print("Cargando activos (ticks una vez c/u)...", flush=True)
    rows = []
    for sym in SYMS:
        instrument, bt, bars, ticks, sig_win = load_asset(sym, tf, days)
        for off in OFFSETS:
            r = run_offset(sym, instrument, bt, bars, ticks, sig_win, tf, off)
            r["sym"] = sym[:3]; r["off"] = off; rows.append(r)
            print(f"    off {off:>4.1f}bps: colocadas {r['placed']:>3} llenadas {r['filled']:>3} "
                  f"fill {r['fr']:>3.0f}% WR {r['wr']:>3.0f}% avgR {r['avgR']:+.3f} "
                  f"exp/orden {r['exp']:+.3f}", flush=True)
        del ticks, bars
        import gc; gc.collect()

    df = pd.DataFrame(rows)
    print(f"\n{'='*72}\n  RESUMEN — R esperado por orden COLOCADA (fill_ratio × avgR)\n{'='*72}")
    print(f"  {'offset':>7} | {'BTC':>16} {'ETH':>16} {'SOL':>16} | {'media exp':>9}")
    for off in OFFSETS:
        cells = []
        for sym in SYMS:
            r = df[(df.sym == sym[:3]) & (df.off == off)].iloc[0]
            cells.append(f"{r['exp']:+.3f}(f{r['fr']:.0f})")
        mean_exp = df[df.off == off].exp.mean()
        print(f"  {off:>5.1f}bps | {cells[0]:>16} {cells[1]:>16} {cells[2]:>16} | {mean_exp:>+9.3f}")
    best = df.groupby("off").exp.mean().idxmax()
    print(f"\n  → mejor offset medio: {best}bps (maximiza R esperado por orden colocada)")
    print("  celda = exp/orden(fill%). exp alto = más R capturado por señal emitida.")


if __name__ == "__main__":
    main()
