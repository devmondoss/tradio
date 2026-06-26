"""
_nautilus_offset.py — barrido de OFFSET de entrada (optimización de EJECUCIÓN).
==============================================================================
El test que el backtest vectorizado NO podía hacer honesto: poner el límite N bps
más profundo que el nivel mejora el PRECIO de entrada PERO baja el FILL RATIO
(menos órdenes se tocan). Nautilus captura ambos efectos → ¿hay un offset que
mejore el dinero NETO (no el avgR inflado), o el fill ratio se come la ventaja?

Métrica honesta = USD netPnL (qty fija) + fill ratio + n. (El avgR sube con offset
por el artefacto stop-chico → no es la métrica a maximizar.)
Correr: python backtest/_nautilus_offset.py SYMBOL [n_bars]
"""
import sys
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
import _listas2 as L2
from _audit_mirror import gen_h21_short
from _listas import OOS_MS
from _nautilus_real import emit_signals, LiquidityReal, RealConfig, PARQ, INSTR, QTY

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OmsType, AccountType
from nautilus_trader.model.identifiers import TraderId
from nautilus_trader.model.objects import Money
from nautilus_trader.model.currencies import USDT
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.backtest.models import MakerTakerFeeModel

OFFSETS = [0.0, 3.0, 6.0, 10.0]


def run_offset(instrument, venue, bar_type, bars, sig_win, qty, offset, tag):
    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=TraderId(f"OFF-{tag}"), logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(venue, OmsType.NETTING, AccountType.MARGIN,
                     starting_balances=[Money(100_000, USDT)], base_currency=USDT,
                     fee_model=MakerTakerFeeModel())
    engine.add_instrument(instrument)
    engine.add_data(bars)
    strat = LiquidityReal(RealConfig(instrument_id=str(instrument.id), bar_type=str(bar_type),
                                     qty=qty, offset_bps=offset), sig_win)
    engine.add_strategy(strat)
    engine.run()
    pos = engine.trader.generate_positions_report()
    usd = 0.0
    if len(pos):
        usd = pos["realized_pnl"].astype(str).str.split().str[0].astype(float).sum()
    R = np.array(strat.closed_R)
    engine.dispose()
    return strat.n_signals_placed, R, usd


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 90000
    print(f"[off] {symbol} — señales + datos...")
    L2.M1 = PARQ[symbol]
    a = L2.A2(L2.load2(15, start_ms=L2.TICK_MS))
    signals = emit_signals(a, [L2.gen_h5(), L2.gen_h21(), gen_h21_short()])

    df = pd.read_parquet(PARQ[symbol], columns=["ts_ms", "open", "high", "low", "close", "volume"]).tail(n)
    df.index = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    win_lo = int(df["ts_ms"].iloc[0])
    sig_win = {k: v for k, v in signals.items() if k >= win_lo}
    df = df[["open", "high", "low", "close", "volume"]]

    instrument = INSTR[symbol](); venue = instrument.id.venue
    bt = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    bars = BarDataWrangler(bt, instrument).process(df)
    print(f"[off] ventana {len(df)} barras · {sum(len(v) for v in sig_win.values())} señales\n")

    print(f"{'offset_bps':>10} {'colocadas':>9} {'llenadas':>8} {'fill%':>6} {'avgR*':>7} {'netR':>7} {'USD netPnL':>11}")
    for off in OFFSETS:
        placed, R, usd = run_offset(instrument, venue, bt, bars, sig_win, QTY[symbol], off, f"{symbol[:3]}{int(off)}")
        fr = 100 * len(R) / max(placed, 1)
        avgR = R.mean() if len(R) else 0.0
        print(f"{off:>10.0f} {placed:>9} {len(R):>8} {fr:>5.0f}% {avgR:>+7.2f} {R.sum():>+7.1f} {usd:>+11.1f}")
    print("\n* avgR sube con offset por artefacto stop-chico → mirar USD netPnL (qty fija) como verdad de dinero.")


if __name__ == "__main__":
    main()
