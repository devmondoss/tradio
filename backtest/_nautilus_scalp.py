"""
_nautilus_scalp.py — valida el ganador sc3 (absorción maker) con fills REALES tick-a-tick.
============================================================================================
El scalp sc3 es de WR alto + target chico (rr_cap) → MÁXIMA sensibilidad al fill.
Este script lo somete a la prueba dura: límites maker post-only llenados SOLO cuando un
trade real cruza el nivel, con modelado de COLA (queue_position=True). Así medimos:
  · fill ratio real de los límites maker en niveles VP
  · avgR real vs backtest (que asume fill exacto en el nivel)

Reusa el frame y el generador de _scalp (gen_sc3) para emitir señales; Nautilus ejecuta.
Correr: python backtest/_nautilus_scalp.py [dias] [SYMBOL] [tf]
"""
import sys
import numpy as np, pandas as pd, glob
sys.path.insert(0, "backtest")
import _scalp as SC

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OmsType, AccountType, OrderSide, TimeInForce
from nautilus_trader.model.identifiers import TraderId, InstrumentId
from nautilus_trader.model.objects import Money
from nautilus_trader.model.currencies import USDT
from nautilus_trader.trading.strategy import Strategy, StrategyConfig
from nautilus_trader.persistence.wranglers import BarDataWrangler, TradeTickDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.backtest.models import MakerTakerFeeModel

FEE_MK, FEE_TK = 0.0002, 0.00055

# config unificada ganadora (sc3) — misma para los 3 activos
UNI = dict(vr_thr=2.5, stop_atr=0.5, tol_atr=0.4, rr_cap=1.5, poc_frac_thr=0.0)
# por-asset vr_thr óptimo (resto igual)
VR = {"BTCUSDT": 2.5, "ETHUSDT": 1.5, "SOLUSDT": 2.5}


def emit_sc3(s, tf, config, atr_win=500):
    """Emite señales sc3 con filtro ATR (igual que run_setup). {bar_ts: [(side,entry,stop,tp1,tp2,atr)]}."""
    gen = SC.gen_sc3(**config)
    atr_med = pd.Series(s.atr).rolling(atr_win, min_periods=50).median().shift(1).values
    out = {}
    cool = 0; dcount = {}
    for i in range(60, s.n-1):
        if i < cool or s.atr[i] <= 0: continue
        if not (np.isfinite(atr_med[i]) and s.atr[i] > atr_med[i]): continue
        d = int(s.day[i])
        if dcount.get(d, 0) >= 3: continue
        for side, lvl, stop, tp1, tp2, tag in (gen(s, i) or []):
            if not np.isfinite([lvl, stop, tp2]).all(): continue
            ref = s.c[i-1]
            if side == "long" and not (lvl < ref): continue
            if side == "short" and not (lvl > ref): continue
            mr = 0.15/100.0*lvl
            if abs(lvl-stop) < mr: stop = lvl-mr if side == "long" else lvl+mr
            tp1v = float(tp1) if (tp1 is not None and np.isfinite(tp1)) else np.nan
            out.setdefault(int(s.ts[i]), []).append((side, float(lvl), float(stop), tp1v, float(tp2), float(s.atr[i])))
            cool = i + 6; dcount[d] = dcount.get(d, 0) + 1
            break
    return out


class ScalpConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    qty: float
    bar_ms: int
    offset_bps: float = 0.0   # >0 = límite MENOS profundo (más cerca del mercado) → más fills, peor precio


class ScalpStrat(Strategy):
    def __init__(self, config, signals):
        super().__init__(config)
        self.bar_type = BarType.from_str(config.bar_type)
        self.signals = signals; self.bar_ms = config.bar_ms
        self.params = {}; self.active = None
        self.closed_R = []; self.n_placed = 0; self.trades = []   # (entry_ts_ms, R)

    def on_start(self):
        self.iid = InstrumentId.from_str(self.config.instrument_id)
        self.instrument = self.cache.instrument(self.iid)
        self.subscribe_bars(self.bar_type)

    def _reduce_half(self, side):
        closing = OrderSide.SELL if side == "long" else OrderSide.BUY
        self.submit_order(self.order_factory.market(
            self.iid, closing, self.instrument.make_qty(self.config.qty*0.5), reduce_only=True))

    def on_order_filled(self, event):
        p = self.params.get(event.client_order_id)
        if p is None or self.active is not None: return
        side, _lvl, stop, tp1, tp2, atr = p
        entry = float(event.last_px); risk = abs(entry-stop)
        if risk <= 0: return
        self.active = dict(side=side, entry=entry, cur=stop, tp1=tp1, tp2=tp2,
                           risk=risk, realized=0.0, rem=1.0, f1=False,
                           ets=event.ts_event // 1_000_000)
        self.cancel_all_orders(self.iid)

    def on_bar(self, bar):
        h, l, c = float(bar.high), float(bar.low), float(bar.close)
        ts_ms = bar.ts_event // 1_000_000
        if self.active is not None:
            a = self.active; risk = a["risk"]; ee = a["entry"]; done = False; reason = None
            if a["side"] == "long":
                if l <= a["cur"]: a["realized"] += a["rem"]*((a["cur"]-ee)/risk); reason = "be" if a["f1"] else "stop"; done = True
                elif not a["f1"] and not np.isnan(a["tp1"]) and h >= a["tp1"]:
                    a["realized"] += 0.5*((a["tp1"]-ee)/risk); a["rem"] = 0.5; a["f1"] = True; a["cur"] = ee; self._reduce_half("long")
                if not done and h >= a["tp2"]: a["realized"] += a["rem"]*((a["tp2"]-ee)/risk); reason = "target"; done = True
            else:
                if h >= a["cur"]: a["realized"] += a["rem"]*((ee-a["cur"])/risk); reason = "be" if a["f1"] else "stop"; done = True
                elif not a["f1"] and not np.isnan(a["tp1"]) and l <= a["tp1"]:
                    a["realized"] += 0.5*((ee-a["tp1"])/risk); a["rem"] = 0.5; a["f1"] = True; a["cur"] = ee; self._reduce_half("short")
                if not done and l <= a["tp2"]: a["realized"] += a["rem"]*((ee-a["tp2"])/risk); reason = "target"; done = True
            if done:
                exit_s = FEE_MK if reason == "target" else FEE_TK
                partial_fee = FEE_MK*0.5 if a["f1"] else 0.0
                a["realized"] -= (FEE_MK + partial_fee + exit_s*a["rem"])*ee/risk
                pos = self.cache.positions_open(instrument_id=self.iid)
                if pos: self.close_position(pos[0])
                self.closed_R.append(a["realized"]); self.trades.append((a["ets"], a["realized"]))
                self.active = None
            return
        if ts_ms % self.bar_ms != 0: return
        self.cancel_all_orders(self.iid); self.params.clear()
        off = self.config.offset_bps / 1e4
        for sig in self.signals.get(ts_ms, []):
            side, entry, stop, tp1, tp2, atr = sig
            px = entry*(1+off) if side == "long" else entry*(1-off)   # menos profundo hacia el mercado
            if side == "long" and not px < c: continue
            if side == "short" and not px > c: continue
            o = self.order_factory.limit(
                self.iid, OrderSide.BUY if side == "long" else OrderSide.SELL,
                self.instrument.make_qty(self.config.qty),
                self.instrument.make_price(px), TimeInForce.GTC, post_only=True)
            self.params[o.client_order_id] = sig
            self.submit_order(o); self.n_placed += 1


def _sol_perp():
    from decimal import Decimal
    from nautilus_trader.model.instruments import CryptoPerpetual
    from nautilus_trader.model.objects import Currency, Price, Quantity
    from nautilus_trader.model.identifiers import Symbol, Venue
    SOL = Currency.from_str("SOL")
    return CryptoPerpetual(
        instrument_id=InstrumentId(Symbol("SOLUSDT-PERP"), Venue("BINANCE")),
        raw_symbol=Symbol("SOLUSDT"), base_currency=SOL, quote_currency=USDT,
        settlement_currency=USDT, is_inverse=False, price_precision=3, size_precision=1,
        price_increment=Price.from_str("0.001"), size_increment=Quantity.from_str("0.1"),
        max_quantity=Quantity.from_str("100000.0"), min_quantity=Quantity.from_str("0.1"),
        max_notional=None, min_notional=Money(5.00, USDT),
        max_price=Price.from_str("10000.000"), min_price=Price.from_str("0.010"),
        margin_init=Decimal("1.00"), margin_maint=Decimal("0.35"),
        maker_fee=Decimal("0.0002"), taker_fee=Decimal("0.00055"), ts_event=0, ts_init=0)


TICKDIR = {"BTCUSDT": str(SC.ROOT/"data/bybit-perp/raw_trades"),
           "ETHUSDT": "E:/bybit-data/bybit-perp-eth/raw_trades",
           "SOLUSDT": "E:/bybit-data/bybit-perp-sol/raw_trades"}
INSTR = {"BTCUSDT": TestInstrumentProvider.btcusdt_perp_binance,
         "ETHUSDT": TestInstrumentProvider.ethusdt_perp_binance, "SOLUSDT": _sol_perp}
QTY = {"BTCUSDT": 0.05, "ETHUSDT": 1.0, "SOLUSDT": 20.0}


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    symbol = sys.argv[2] if len(sys.argv) > 2 else "BTCUSDT"
    tf = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    offset = int(sys.argv[4]) if len(sys.argv) > 4 else 0   # días-back donde TERMINA el chunk
    cfg = dict(UNI); cfg["vr_thr"] = VR[symbol]
    print(f"[scalp-nautilus] {symbol} M{tf} sc3 {cfg}")
    s = SC.load(symbol, tf)
    signals = emit_sc3(s, tf, cfg)
    nsig = sum(len(v) for v in signals.values())
    print(f"[scalp-nautilus] {nsig} señales sc3 emitidas")

    # cobertura del M1 procesado (los ticks suelen ir más lejos que el M1)
    m1p = SC.ASSETS[symbol]["m1"]
    m1_full = pd.read_parquet(m1p, columns=["ts_ms"])
    m1_max = int(m1_full.ts_ms.max())
    import datetime
    m1_max_day = datetime.datetime.fromtimestamp(m1_max/1000, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
    allf = sorted(glob.glob(f"{TICKDIR[symbol]}/*.parquet"))
    covf = [f for f in allf if f[-18:-8] <= m1_max_day]
    end_i = len(covf) - offset
    files = covf[max(0, end_i-days):end_i]
    if not files:
        print(f"[scalp-nautilus] chunk vacío (offset {offset})"); return
    print(f"[scalp-nautilus] {len(files)} días de ticks: {files[0][-18:-8]}..{files[-1][-18:-8]} (M1 hasta {m1_max_day})")
    tr = pd.concat([pd.read_parquet(f, columns=["ts_ms","price","size","side"]) for f in files])
    raw = len(tr); tr = tr[tr["price"] != tr["price"].shift()]
    win_lo = int(tr["ts_ms"].iloc[0]); win_hi = int(tr["ts_ms"].iloc[-1])
    print(f"[scalp-nautilus] {raw:,} ticks → {len(tr):,} tras thin")

    m1p = SC.ASSETS[symbol]["m1"]
    m1 = pd.read_parquet(m1p, columns=["ts_ms","open","high","low","close","volume"])
    m1 = m1[(m1.ts_ms >= win_lo) & (m1.ts_ms <= win_hi)]
    m1.index = pd.to_datetime(m1["ts_ms"], unit="ms", utc=True)
    sig_win = {k: v for k, v in signals.items() if win_lo <= k <= win_hi}
    oos = sum(1 for k in sig_win if k >= SC.OOS_MS)
    print(f"[scalp-nautilus] M1 {len(m1)} barras · señales en ventana {sum(len(v) for v in sig_win.values())} (OOS {oos})")

    instrument = INSTR[symbol](); venue = instrument.id.venue
    bt = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    bars = BarDataWrangler(bt, instrument).process(m1[["open","high","low","close","volume"]])
    tr2 = tr.copy(); tr2.index = pd.to_datetime(tr2["ts_ms"], unit="ms", utc=True)
    tr2 = tr2.rename(columns={"size": "quantity"}); tr2["trade_id"] = np.arange(len(tr2)).astype(str)
    ticks = TradeTickDataWrangler(instrument).process(tr2[["price","quantity","side","trade_id"]])

    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=TraderId("SCALP-001"), logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(venue, OmsType.NETTING, AccountType.MARGIN,
                     starting_balances=[Money(100_000, USDT)], base_currency=USDT,
                     fee_model=MakerTakerFeeModel(),
                     bar_execution=False, trade_execution=True, queue_position=True)
    engine.add_instrument(instrument); engine.add_data(bars); engine.add_data(ticks)
    strat = ScalpStrat(ScalpConfig(instrument_id=str(instrument.id), bar_type=str(bt),
                                   qty=QTY[symbol], bar_ms=tf*60_000), sig_win)
    engine.add_strategy(strat)
    print("[scalp-nautilus] corriendo (fills tick reales + cola)...")
    engine.run()

    R = np.array(strat.closed_R)
    print(f"\n===== sc3 FILL REAL ({symbol} M{tf}, {days}d) =====")
    print(f"colocadas {strat.n_placed} · llenadas {len(R)} · fill ratio {100*len(R)/max(strat.n_placed,1):.0f}%")
    if len(R):
        print(f"  WR {100*(R>0).mean():.0f}%  avgR {R.mean():+.3f}  netR {R.sum():+.1f}  maxR {R.max():+.1f}  minR {R.min():+.1f}")
    # volcar trades (entry_ts, R) para construir equity multi-chunk
    if strat.trades:
        import os
        outdir = "E:/bybit-data/_scalp/results"; os.makedirs(outdir, exist_ok=True)
        d0 = files[0][-18:-8]; d1 = files[-1][-18:-8]
        out = f"{outdir}/nautilus_{symbol[:3]}_{d0}_{d1}.csv"
        pd.DataFrame(strat.trades, columns=["ts","r"]).to_csv(out, index=False)
        print(f"  trades → {out}")
    engine.dispose()


if __name__ == "__main__":
    main()
