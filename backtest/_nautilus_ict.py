"""
_nautilus_ict.py — El modelo ICT (_ict.py) ejecutado en Nautilus con fills maker reales.
==========================================================================================
Reusa la Strategy validada (LiquidityReal/RealConfig de _nautilus_real): misma gestión
fade(parcial→BE→target) / trail(ATR×k) enrutada por régimen, mismos fees y fill maker
post-only a nivel de barra. SÓLO cambia el emisor de señales: aquí salen del gen_ict.

Así medimos si el edge ICT sobrevive ejecución realista (fill ratio incluido).

Uso:  python backtest/_nautilus_ict.py [n_m1] [BTCUSDT] [--fe 0.5 --pen 0.15 ...]
"""
import sys, argparse
import numpy as np, pandas as pd
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import _ict as ICT
from _ict import gen_ict, DEFAULTS, load_ict, A, PARQ
from _listas import OOS_MS, TICK_MS

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OmsType, AccountType, OrderSide, TimeInForce
from nautilus_trader.model.identifiers import TraderId, InstrumentId
from nautilus_trader.model.objects import Money
from nautilus_trader.model.currencies import USDT
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.backtest.models import MakerTakerFeeModel

# reuso la Strategy y los helpers de instrumentos/qty del runner validado
from _nautilus_real import (LiquidityReal, RealConfig, _sol_perp, is_chop,
                            INSTR, QTY, M15_MS)


def emit_ict(a, P, sides=("long", "short"), volfilter=True, atr_mult=1.0, atr_win=500,
             cooldown=6, max_day=3, stop_floor_pct=0.15, min_range=0.0):
    """Emite señales ICT para Nautilus. gestion = fade en chop / trail en tendencia
    (paridad con run_system). Filtro ATR opcional (palanca validada del proyecto)."""
    atr_med = pd.Series(a.atr).rolling(atr_win, min_periods=50).median().shift(1).values
    gen = gen_ict(sides=sides, **P)
    out = {}
    cool = 0; dcount = {}
    for i in range(60, a.n - 1):
        if i < cool or a.atr[i] <= 0:
            continue
        if volfilter and not (np.isfinite(atr_med[i]) and a.atr[i] > atr_mult * atr_med[i]):
            continue
        d = int(a.day[i])
        if dcount.get(d, 0) >= max_day:
            continue
        for side, entry, stop, tp1, tp2, kind in (gen(a, i) or []):
            if not np.isfinite([entry, stop, tp2]).all():
                continue
            if stop_floor_pct > 0:
                mr = stop_floor_pct / 100.0 * entry
                if abs(entry - stop) < mr:
                    stop = entry - mr if side == "long" else entry + mr
            risk = abs(entry - stop)
            if risk <= 0 or abs(tp2 - entry) / risk < 1.2:
                continue
            gestion = "fade" if is_chop(a.reg[i]) else "trail"
            tp1v = float(tp1) if (tp1 is not None and np.isfinite(tp1)) else np.nan
            out.setdefault(int(a.ts[i]), []).append(
                (side, float(entry), float(stop), tp1v, float(tp2), gestion, kind, float(a.atr[i])))
            cool = i + cooldown; dcount[d] = dcount.get(d, 0) + 1
            break
    return out


def run(symbol, n, P, tf_min=15, trail_atr=6.0, volfilter=True, sides=("long", "short"), verbose=True):
    ICT.L2.M1 = PARQ[symbol]
    a = A(load_ict(PARQ[symbol], tf_min))
    signals = emit_ict(a, P, sides=sides, volfilter=volfilter)
    nsig = sum(len(v) for v in signals.values())
    if verbose:
        print(f"[ict-naut] {symbol}: {nsig} señales emitidas (volfilter={volfilter})")

    df = pd.read_parquet(PARQ[symbol], columns=["ts_ms", "open", "high", "low", "close", "volume"]).tail(n)
    df.index = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    win_lo = int(df["ts_ms"].iloc[0])
    sig_win = {k: v for k, v in signals.items() if k >= win_lo}
    df = df[["open", "high", "low", "close", "volume"]]

    instrument = INSTR[symbol]()
    venue = instrument.id.venue
    bar_type = BarType.from_str(f"{instrument.id}-1-MINUTE-LAST-EXTERNAL")
    bars = BarDataWrangler(bar_type, instrument).process(df)

    engine = BacktestEngine(config=BacktestEngineConfig(
        trader_id=TraderId("ICT-001"), logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(venue, OmsType.NETTING, AccountType.MARGIN,
                     starting_balances=[Money(100_000, USDT)], base_currency=USDT,
                     fee_model=MakerTakerFeeModel())
    engine.add_instrument(instrument)
    engine.add_data(bars)
    strat = LiquidityReal(RealConfig(instrument_id=str(instrument.id), bar_type=str(bar_type),
                                     qty=QTY[symbol], trail_atr=trail_atr), sig_win)
    engine.add_strategy(strat)
    engine.run()

    placed = strat.n_signals_placed
    R = np.array(strat.closed_R)
    res = dict(symbol=symbol, placed=placed, filled=len(R),
               fill=100 * len(R) / max(placed, 1))
    if len(R):
        res.update(avgR=float(R.mean()), wr=float(100 * (R > 0).mean()),
                   netR=float(R.sum()), maxR=float(R.max()), minR=float(R.min()))
    engine.dispose()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("n", nargs="?", type=int, default=400000)
    ap.add_argument("symbol", nargs="?", default="BTCUSDT")
    ap.add_argument("--tf", type=int, default=15)
    ap.add_argument("--trail", type=float, default=6.0)
    ap.add_argument("--novol", action="store_true")
    ap.add_argument("--sides", default="both", choices=["both", "long", "short"])
    for k, v in DEFAULTS.items():
        if isinstance(v, bool):
            ap.add_argument(f"--{k}", action="store_true", default=None)
        else:
            ap.add_argument(f"--{k}", type=type(v), default=None)
    args = ap.parse_args()
    P = dict(DEFAULTS)
    for k in DEFAULTS:
        v = getattr(args, k)
        if v is not None:
            P[k] = v
    sides = ("long", "short") if args.sides == "both" else (args.sides,)

    res = run(args.symbol, args.n, P, args.tf, args.trail, not args.novol, sides)
    print(f"\n===== ICT en Nautilus — {res['symbol']} =====")
    print(f"colocadas: {res['placed']}  llenadas: {res['filled']}  fill: {res['fill']:.0f}%")
    if res.get("filled"):
        print(f"  WR {res['wr']:.0f}%  avgR {res['avgR']:+.3f}  netR {res['netR']:+.1f}  "
              f"maxR {res['maxR']:+.1f}  minR {res['minR']:+.1f}")


if __name__ == "__main__":
    main()
