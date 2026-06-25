"""
_nautilus_ticks.py — fill DEFINITIVO con trade-ticks reales + queue position.
=============================================================================
El test más realista de fill ratio: alimenta los trades tick-a-tick (raw_trades)
y deja que Nautilus llene los límites SOLO cuando un trade real cruza el nivel,
con modelado de COLA (queue_position=True). bar_execution=False → los fills NO
salen de las barras, solo de los ticks reales.

Ventana corta (días) por el volumen (~2.35M ticks/día). Reusa señales + estrategia
de _nautilus_real. Correr: python backtest/_nautilus_ticks.py [dias]
"""
import sys, glob
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
import _listas2 as L2
from _audit_mirror import gen_h21_short
from _listas import OOS_MS
from _nautilus_real import emit_signals, LiquidityReal, RealConfig

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OmsType, AccountType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Money
from nautilus_trader.model.currencies import USDT
from nautilus_trader.persistence.wranglers import BarDataWrangler, TradeTickDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.backtest.models import MakerTakerFeeModel


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(f"[ticks] señales (reuso _listas2)...")
    L2.M1 = "data/bybit-perp/processed/btcusdt_perp_m1.parquet"
    a = L2.A2(L2.load2(15, start_ms=L2.TICK_MS))
    signals = emit_signals(a, [L2.gen_h5(), L2.gen_h21(), gen_h21_short()])

    # últimos N días de trades tick-a-tick
    files = sorted(glob.glob("data/bybit-perp/raw_trades/*.parquet"))[-days:]
    print(f"[ticks] cargando {len(files)} días de trades: {files[0][-13:-8]}..{files[-1][-13:-8]}")
    tr = pd.concat([pd.read_parquet(f, columns=["ts_ms", "price", "size", "side"]) for f in files])
    raw = len(tr)
    tr = tr[tr["price"] != tr["price"].shift()]   # thin: solo ticks que cambian precio (fill-relevante)
    print(f"[ticks] {raw:,} ticks → {len(tr):,} tras thin de precio")
    win_lo = int(tr["ts_ms"].iloc[0]); win_hi = int(tr["ts_ms"].iloc[-1])

    # M1 bars para decisión/gestión en la misma ventana
    m1 = pd.read_parquet("data/bybit-perp/processed/btcusdt_perp_m1.parquet",
                         columns=["ts_ms", "open", "high", "low", "close", "volume"])
    m1 = m1[(m1.ts_ms >= win_lo) & (m1.ts_ms <= win_hi)]
    m1.index = pd.to_datetime(m1["ts_ms"], unit="ms", utc=True)
    sig_win = {k: v for k, v in signals.items() if win_lo <= k <= win_hi}
    print(f"[ticks] M1 {len(m1)} barras · señales en ventana {sum(len(v) for v in sig_win.values())}")

    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    venue = instrument.id.venue
    bt = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    bars = BarDataWrangler(bt, instrument).process(m1[["open", "high", "low", "close", "volume"]])

    tr2 = tr.copy(); tr2.index = pd.to_datetime(tr2["ts_ms"], unit="ms", utc=True)
    tr2 = tr2.rename(columns={"size": "quantity"})
    tr2["trade_id"] = np.arange(len(tr2)).astype(str)
    ticks = TradeTickDataWrangler(instrument).process(tr2[["price", "quantity", "side", "trade_id"]])
    print(f"[ticks] {len(ticks):,} trade-ticks Nautilus")

    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=TraderId("TICKS-001"), logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(venue, OmsType.NETTING, AccountType.MARGIN,
                     starting_balances=[Money(100_000, USDT)], base_currency=USDT,
                     fee_model=MakerTakerFeeModel(),
                     bar_execution=False, trade_execution=True, queue_position=True)
    engine.add_instrument(instrument)
    engine.add_data(bars)
    engine.add_data(ticks)
    strat = LiquidityReal(RealConfig(instrument_id=str(instrument.id), bar_type=str(bt), qty=0.05), sig_win)
    engine.add_strategy(strat)

    print("[ticks] corriendo (fills por trade real + cola)...")
    engine.run()

    print("\n===== FILL DEFINITIVO (trade-ticks reales + queue position) =====")
    R = np.array(strat.closed_R)
    print(f"órdenes colocadas: {strat.n_signals_placed}  ·  llenadas: {len(R)}  ·  fill ratio REAL: {100*len(R)/max(strat.n_signals_placed,1):.0f}%")
    if len(R):
        print(f"  WR {100*(R>0).mean():.0f}%  avgR {R.mean():+.3f}  netR {R.sum():+.1f}  maxR {R.max():+.1f}")
    engine.dispose()


if __name__ == "__main__":
    main()
