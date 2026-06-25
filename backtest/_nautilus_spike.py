"""
_nautilus_spike.py — prueba de concepto: NUESTRA data BTC en un backtest Nautilus.
==================================================================================
Objetivo del spike: validar el pipeline end-to-end (parquet propio → objetos
Nautilus → fills maker simulados → PnL/stats). NO es la estrategia final — es un
fade simple (limit post-only en swing high/low, stop+target por ATR) para ver que
el motor de ejecución de Nautilus corre con nuestros datos.

Correr: python backtest/_nautilus_spike.py [n_bars]
"""
import sys
from collections import deque
import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OmsType, AccountType, OrderSide, TimeInForce
from nautilus_trader.model.identifiers import Venue, TraderId
from nautilus_trader.model.objects import Money, Quantity
from nautilus_trader.model.currencies import USDT
from nautilus_trader.trading.strategy import Strategy, StrategyConfig
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider


# ── Estrategia: fade maker simple ─────────────────────────────────────────────
class FadeConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    lookback: int = 50
    atr_n: int = 14
    stop_atr: float = 0.6
    rr: float = 2.0
    qty: float = 0.05


class LiquidityFade(Strategy):
    def __init__(self, config: FadeConfig):
        super().__init__(config)
        self.instrument_id = None
        self.bar_type = BarType.from_str(config.bar_type)
        self.highs = deque(maxlen=config.lookback)
        self.lows = deque(maxlen=config.lookback)
        self.trs = deque(maxlen=config.atr_n)
        self.prev_close = None
        self.entry = None
        self.side = None
        self.stop_px = 0.0
        self.target_px = 0.0

    def on_start(self):
        from nautilus_trader.model.identifiers import InstrumentId
        self.instrument_id = InstrumentId.from_str(self.config.instrument_id)
        self.instrument = self.cache.instrument(self.instrument_id)
        self.subscribe_bars(self.bar_type)

    def _atr(self):
        return sum(self.trs) / len(self.trs) if self.trs else 0.0

    def on_bar(self, bar):
        h, l, c = float(bar.high), float(bar.low), float(bar.close)
        if self.prev_close is not None:
            tr = max(h - l, abs(h - self.prev_close), abs(l - self.prev_close))
            self.trs.append(tr)
        self.prev_close = c
        self.highs.append(h); self.lows.append(l)
        if len(self.highs) < self.config.lookback or self._atr() <= 0:
            return

        flat = self.portfolio.is_flat(self.instrument_id)

        if not flat:
            pos = self.cache.positions_open(instrument_id=self.instrument_id)
            if not pos:
                return
            pos = pos[0]
            if self.entry is None:   # recién abierta → fijar stop/target y cancelar el opuesto
                self.entry = float(pos.avg_px_open)
                self.side = "long" if pos.is_long else "short"
                atr = self._atr()
                risk = self.config.stop_atr * atr
                if self.side == "long":
                    self.stop_px = self.entry - risk; self.target_px = self.entry + self.config.rr * risk
                else:
                    self.stop_px = self.entry + risk; self.target_px = self.entry - self.config.rr * risk
                self.cancel_all_orders(self.instrument_id)
            # gestión de salida con el OHLC de la barra
            hit = None
            if self.side == "long":
                if l <= self.stop_px: hit = "stop"
                elif h >= self.target_px: hit = "target"
            else:
                if h >= self.stop_px: hit = "stop"
                elif l <= self.target_px: hit = "target"
            if hit:
                self.close_position(pos)
            return

        # flat → refrescar órdenes límite en swing high/low
        self.entry = None
        self.cancel_all_orders(self.instrument_id)
        swing_low = min(self.lows); swing_high = max(self.highs)
        qty = self.instrument.make_qty(self.config.qty)
        # buy-limit por debajo (maker), sell-limit por encima (maker)
        if swing_low < c:
            self.submit_order(self.order_factory.limit(
                self.instrument_id, OrderSide.BUY, qty,
                self.instrument.make_price(swing_low), TimeInForce.GTC, post_only=True))
        if swing_high > c:
            self.submit_order(self.order_factory.limit(
                self.instrument_id, OrderSide.SELL, qty,
                self.instrument.make_price(swing_high), TimeInForce.GTC, post_only=True))


# ── Runner ────────────────────────────────────────────────────────────────────
def main():
    n_bars = int(sys.argv[1]) if len(sys.argv) > 1 else 20000
    print(f"[spike] cargando {n_bars} barras BTC M1...")
    df = pd.read_parquet("data/bybit-perp/processed/btcusdt_perp_m1.parquet",
                         columns=["ts_ms", "open", "high", "low", "close", "volume"]).tail(n_bars)
    df.index = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    df = df[["open", "high", "low", "close", "volume"]]

    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    venue = instrument.id.venue
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    bars = BarDataWrangler(bar_type, instrument).process(df)
    print(f"[spike] {len(bars)} barras Nautilus generadas")

    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=TraderId("SPIKE-001"),
        logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(venue, OmsType.NETTING, AccountType.MARGIN,
                     starting_balances=[Money(100_000, USDT)], base_currency=USDT)
    engine.add_instrument(instrument)
    engine.add_data(bars)
    engine.add_strategy(LiquidityFade(FadeConfig(
        instrument_id=str(instrument.id), bar_type=str(bar_type))))

    print("[spike] corriendo backtest...")
    engine.run()

    print("\n===== RESULTADO =====")
    acct = engine.trader.generate_account_report(venue)
    fills = engine.trader.generate_order_fills_report()
    positions = engine.trader.generate_positions_report()
    print(f"fills: {len(fills)}  posiciones cerradas: {len(positions)}")
    if len(positions):
        pnl = positions["realized_pnl"].astype(str).str.split().str[0].astype(float)
        wins = (pnl > 0).sum()
        print(f"WR: {100*wins/len(positions):.0f}%  netPnL: {pnl.sum():+.2f} USDT  "
              f"avg: {pnl.mean():+.2f}  best {pnl.max():+.1f}  worst {pnl.min():+.1f}")
        cols = [c for c in ["entry", "side", "avg_px_open", "avg_px_close", "realized_pnl", "duration_ns"] if c in positions.columns]
        print("últimas posiciones:\n" + positions[cols].tail(5).to_string())
    print(f"balance final: {acct['total'].iloc[-1] if len(acct) else 'n/a'}")
    engine.dispose()


if __name__ == "__main__":
    main()
