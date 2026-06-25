"""
_nautilus_real.py — la estrategia REAL (liquidity A+B) ejecutada en Nautilus.
=============================================================================
No reimplementa los niveles: REUSA nuestros generadores validados (_listas2:
gen_h5 + gen_h21 + mirror short + struct_target + filtros de run_system) para
EMITIR las señales, y deja que Nautilus haga la ejecución y los fills maker.

Así comparamos: ¿el edge sobrevive con la ejecución realista de Nautilus
(fills maker post-only a nivel de barra) vs nuestro backtest que asume fill al toque?

Gestión v1 (full position, sin parcial — refinamiento posterior):
  fade  → mueve stop a breakeven al tocar tp1, sale en stop/target
  trail → trailing ATR×4
Correr: python backtest/_nautilus_real.py [n_m1_bars]
"""
import sys
from collections import deque
import numpy as np, pandas as pd
sys.path.insert(0, "backtest")
import _listas2 as L2
from _audit_mirror import gen_h21_short
from _listas import OOS_MS

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OmsType, AccountType, OrderSide, TimeInForce
from nautilus_trader.model.identifiers import TraderId, InstrumentId
from nautilus_trader.model.objects import Money
from nautilus_trader.model.currencies import USDT
from nautilus_trader.trading.strategy import Strategy, StrategyConfig
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.backtest.models import MakerTakerFeeModel

M15_MS = 900_000
FEE_MAKER = 0.0002    # 2 bps/lado (para el R honesto, igual que run_system)
FEE_TAKER = 0.00055   # 5.5 bps/lado


def is_chop(reg):
    return str(reg).lower() in ("chop", "range", "balance", "consolidation")


def emit_signals(a, gens, volfilter=True, cooldown=6, max_day=2, margin=2.0,
                 stop_floor_pct=0.15, min_range=0.5, atr_mult=1.0, atr_win=500):
    """Mismas condiciones de entrada que run_system, pero EMITE la orden (no simula
    el fill — eso lo decide Nautilus). Devuelve {m15_ts: [(side,entry,stop,tp1,tp2,gestion,kind,atr)]}."""
    atr_med = pd.Series(a.atr).rolling(atr_win, min_periods=50).median().shift(1).values
    out = {}
    for g in gens:
        cool = 0; dcount = {}
        for i in range(60, a.n - 1):
            if i < cool or a.atr[i] <= 0: continue
            if volfilter and not (np.isfinite(atr_med[i]) and a.atr[i] > atr_mult * atr_med[i]): continue
            d = int(a.day[i])
            if dcount.get(d, 0) >= max_day: continue
            for side, lvl, stop, tp1, tp2, kind in (g(a, i) or []):
                if not np.isfinite([lvl, stop, tp2]).all(): continue
                ref = a.c[i - 1]
                if side == "long" and not (lvl < ref): continue
                if side == "short" and not (lvl > ref): continue
                entry = lvl
                if stop_floor_pct > 0:
                    mr = stop_floor_pct / 100.0 * entry
                    if abs(entry - stop) < mr: stop = entry - mr if side == "long" else entry + mr
                risk = abs(entry - stop)
                if risk <= 0 or abs(tp2 - entry) / risk < 1.2: continue
                gestion = "fade" if is_chop(a.reg[i]) else "trail"
                if gestion == "fade" and min_range > 0 and tp1 is not None and 100 * abs(tp1 - entry) / entry < min_range:
                    continue
                tp1v = float(tp1) if (tp1 is not None and np.isfinite(tp1)) else np.nan
                out.setdefault(int(a.ts[i]), []).append(
                    (side, float(entry), float(stop), tp1v, float(tp2), gestion, kind, float(a.atr[i])))
                cool = i + cooldown; dcount[d] = dcount.get(d, 0) + 1
                break
    return out


class RealConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    qty: float = 0.05
    trail_atr: float = 4.0


class LiquidityReal(Strategy):
    def __init__(self, config: RealConfig, signals: dict):
        super().__init__(config)
        self.bar_type = BarType.from_str(config.bar_type)
        self.signals = signals
        self.params = {}      # client_order_id -> (side,entry,stop,tp1,tp2,gestion,kind,atr)
        self.active = None    # dict de la posición viva
        self.closed_R = []    # R de cada trade cerrado (comparable al backtest)
        self.n_signals_placed = 0

    def on_start(self):
        self.iid = InstrumentId.from_str(self.config.instrument_id)
        self.instrument = self.cache.instrument(self.iid)
        self.subscribe_bars(self.bar_type)

    def _reduce_half(self, side):
        closing = OrderSide.SELL if side == "long" else OrderSide.BUY
        o = self.order_factory.market(self.iid, closing,
            self.instrument.make_qty(self.config.qty * 0.5), reduce_only=True)
        self.submit_order(o)

    def on_order_filled(self, event):
        p = self.params.get(event.client_order_id)
        if p is None or self.active is not None:
            return
        side, entry, stop, tp1, tp2, gestion, kind, atr = p
        risk = abs(entry - stop)
        self.active = dict(side=side, entry=entry, cur_stop=stop, tp1=tp1, tp2=tp2,
                           gestion=gestion, atr=atr, risk=risk,
                           realized=0.0, rem=1.0, filled1=False,
                           best=entry, trail=stop)
        self.cancel_all_orders(self.iid)   # matar la orden opuesta

    def on_bar(self, bar):
        h, l, c = float(bar.high), float(bar.low), float(bar.close)
        ts_ms = bar.ts_event // 1_000_000

        # ── gestión de la posición viva (en M1) — paridad con run_system ──
        if self.active is not None:
            a = self.active; risk = a["risk"]; ee = a["entry"]; done = False; reason = None
            if a["gestion"] == "trail":
                fee_r = (FEE_MAKER + FEE_TAKER) * ee / risk
                if a["side"] == "long":
                    a["best"] = max(a["best"], h); a["trail"] = max(a["trail"], a["best"] - self.config.trail_atr * a["atr"])
                    if l <= a["trail"]:
                        a["realized"] = (a["trail"] - ee) / risk - fee_r; done = True; reason = "trail"
                else:
                    a["best"] = min(a["best"], l); a["trail"] = min(a["trail"], a["best"] + self.config.trail_atr * a["atr"])
                    if h >= a["trail"]:
                        a["realized"] = (ee - a["trail"]) / risk - fee_r; done = True; reason = "trail"
            else:  # fade: parcial 50% en tp1 → breakeven → target
                if a["side"] == "long":
                    if l <= a["cur_stop"]:
                        a["realized"] += a["rem"] * ((a["cur_stop"] - ee) / risk); reason = "be" if a["filled1"] else "stop"; done = True
                    elif not a["filled1"] and not np.isnan(a["tp1"]) and h >= a["tp1"]:
                        a["realized"] += 0.5 * ((a["tp1"] - ee) / risk); a["rem"] = 0.5; a["filled1"] = True; a["cur_stop"] = ee
                        self._reduce_half(a["side"])
                    if not done and h >= a["tp2"]:
                        a["realized"] += a["rem"] * ((a["tp2"] - ee) / risk); reason = "target"; done = True
                else:
                    if h >= a["cur_stop"]:
                        a["realized"] += a["rem"] * ((ee - a["cur_stop"]) / risk); reason = "be" if a["filled1"] else "stop"; done = True
                    elif not a["filled1"] and not np.isnan(a["tp1"]) and l <= a["tp1"]:
                        a["realized"] += 0.5 * ((ee - a["tp1"]) / risk); a["rem"] = 0.5; a["filled1"] = True; a["cur_stop"] = ee
                        self._reduce_half(a["side"])
                    if not done and l <= a["tp2"]:
                        a["realized"] += a["rem"] * ((ee - a["tp2"]) / risk); reason = "target"; done = True
                if done:
                    exit_fee = FEE_MAKER if reason == "target" else FEE_TAKER
                    partial_fee = FEE_MAKER * 0.5 if a["filled1"] else 0.0
                    a["realized"] -= (FEE_MAKER + partial_fee + exit_fee * a["rem"]) * ee / risk
            if done:
                pos = self.cache.positions_open(instrument_id=self.iid)
                if pos: self.close_position(pos[0])
                self.closed_R.append(a["realized"])
                self.active = None
            return

        # ── colocar señales en el cierre de cada barra M15 ──
        if ts_ms % M15_MS != 0:
            return
        self.cancel_all_orders(self.iid); self.params.clear()
        for sig in self.signals.get(ts_ms, []):
            side, entry, stop, tp1, tp2, gestion, kind, atr = sig
            # sólo límites del lado correcto del mercado actual
            if side == "long" and not entry < c: continue
            if side == "short" and not entry > c: continue
            o = self.order_factory.limit(
                self.iid, OrderSide.BUY if side == "long" else OrderSide.SELL,
                self.instrument.make_qty(self.config.qty),
                self.instrument.make_price(entry), TimeInForce.GTC, post_only=True)
            self.params[o.client_order_id] = sig
            self.submit_order(o); self.n_signals_placed += 1


def _sol_perp():
    """SOL no tiene test-instrument en Nautilus → lo construimos (≈ Binance SOLUSDT-PERP)."""
    from decimal import Decimal
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Currency, Price, Quantity, Money
    from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
    SOL = Currency.from_str("SOL")
    return CryptoPerpetual(
        instrument_id=InstrumentId(Symbol("SOLUSDT-PERP"), Venue("BINANCE")),
        raw_symbol=Symbol("SOLUSDT"), base_currency=SOL, quote_currency=USDT,
        settlement_currency=USDT, is_inverse=False,
        price_precision=3, size_precision=1,
        price_increment=Price.from_str("0.001"), size_increment=Quantity.from_str("0.1"),
        max_quantity=Quantity.from_str("100000.0"), min_quantity=Quantity.from_str("0.1"),
        max_notional=None, min_notional=Money(5.00, USDT),
        max_price=Price.from_str("10000.000"), min_price=Price.from_str("0.010"),
        margin_init=Decimal("1.00"), margin_maint=Decimal("0.35"),
        maker_fee=Decimal("0.0002"), taker_fee=Decimal("0.00055"),
        ts_event=0, ts_init=0)


PARQ = {"BTCUSDT": "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
        "ETHUSDT": "E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet",
        "SOLUSDT": "E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"}
INSTR = {"BTCUSDT": TestInstrumentProvider.btcusdt_perp_binance,
         "ETHUSDT": TestInstrumentProvider.ethusdt_perp_binance,
         "SOLUSDT": _sol_perp}
QTY = {"BTCUSDT": 0.05, "ETHUSDT": 1.0, "SOLUSDT": 20.0}


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40000
    symbol = sys.argv[2] if len(sys.argv) > 2 else "BTCUSDT"
    print(f"[real] {symbol} — cargando M15 features + generando señales (reuso _listas2)...")
    L2.M1 = PARQ[symbol]
    t = L2.load2(15, start_ms=L2.TICK_MS); a = L2.A2(t)
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
    signals = emit_signals(a, gens)
    nsig = sum(len(v) for v in signals.values())
    print(f"[real] {nsig} señales emitidas en {len(signals)} barras M15")

    df = pd.read_parquet(PARQ[symbol],
                         columns=["ts_ms", "open", "high", "low", "close", "volume"]).tail(n)
    df.index = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    win_lo = int(df["ts_ms"].iloc[0])
    sig_win = {k: v for k, v in signals.items() if k >= win_lo}
    oos_in_win = sum(1 for k in sig_win if k >= OOS_MS)
    print(f"[real] ventana M1: {len(df)} barras · señales en ventana: {sum(len(v) for v in sig_win.values())} (OOS bars {oos_in_win})")
    df = df[["open", "high", "low", "close", "volume"]]

    instrument = INSTR[symbol]()
    venue = instrument.id.venue
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    bars = BarDataWrangler(bar_type, instrument).process(df)

    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=TraderId("REAL-001"), logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(venue, OmsType.NETTING, AccountType.MARGIN,
                     starting_balances=[Money(100_000, USDT)], base_currency=USDT,
                     fee_model=MakerTakerFeeModel())
    engine.add_instrument(instrument)
    engine.add_data(bars)
    strat = LiquidityReal(RealConfig(instrument_id=str(instrument.id), bar_type=str(bar_type), qty=QTY[symbol]), sig_win)
    engine.add_strategy(strat)

    print("[real] corriendo backtest Nautilus...")
    engine.run()

    print("\n===== RESULTADO (estrategia REAL en Nautilus) =====")
    pos = engine.trader.generate_positions_report()
    placed = strat.n_signals_placed
    R = np.array(strat.closed_R)
    print(f"órdenes colocadas: {placed}  ·  trades llenados: {len(R)}  ·  fill ratio: {100*len(R)/max(placed,1):.0f}%")
    if len(R):
        wr = 100 * (R > 0).mean()
        print(f"  WR {wr:.0f}%  avgR {R.mean():+.3f}  netR {R.sum():+.1f}  maxR {R.max():+.1f}  minR {R.min():+.1f}")
        print(f"  (referencia backtest BTC OOS A+B: avgR +1.82)")
    if len(pos):
        pnl = pos["realized_pnl"].astype(str).str.split().str[0].astype(float)
        print(f"  cross-check Nautilus USD: netPnL {pnl.sum():+.1f}  (fills {len(pos)} posiciones, fees maker/taker aplicados)")
    engine.dispose()


if __name__ == "__main__":
    main()
