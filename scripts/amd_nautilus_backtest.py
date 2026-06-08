#!/usr/bin/env python3
"""
AMD Backtest usando NautilusTrader BacktestEngine.

Descarga klines M1 de Binance FAPI, porta la misma maquina de estados AMD
y simula fills con el matching engine de Nautilus.

Uso:
    python scripts/amd_nautilus_backtest.py
    python scripts/amd_nautilus_backtest.py --days 30
    python scripts/amd_nautilus_backtest.py --no-slope
    python scripts/amd_nautilus_backtest.py --symbol ETHUSDT --days 14
    python scripts/amd_nautilus_backtest.py --load-parquet  # usa dataset/ si existe
"""

import argparse
import json
import time as time_mod
import urllib.request
from collections import deque
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd

# ── NautilusTrader imports ─────────────────────────────────────────────────────
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import BTC, USDT
from nautilus_trader.model.data import Bar, BarSpecification, BarType
from nautilus_trader.model.enums import (
    AccountType,
    AggregationSource,
    BarAggregation,
    OmsType,
    OrderSide,
    PriceType,
    TimeInForce,
)
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import CryptoPerpetual
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.trading.strategy import Strategy
from nautilus_trader.config import StrategyConfig

DATASET_DIR = Path(__file__).parent.parent / "dataset"
FAPI = "https://fapi.binance.com"

# ── Parametros AMD (mismos que amd_backtest.py) ────────────────────────────────
AMD_CFG = {
    "accum_range_min_pct":       0.06,
    "accum_range_max_pct":       0.45,
    "accum_min_bars":            8,
    "accum_max_bars":            50,
    "manip_min_vr":              1.2,
    "dist_min_vr":               1.5,
    "dist_cvd_slope":            10.0,
    "stop_buffer_pct":           0.08,
    "min_rr":                    2.0,
    "cooldown_bars":             45,
    "max_wait_bars_after_spike": 10,
    "max_hold_bars":             120,
    "warmup_bars":               60,
    "cvd_slope_win":             20,
    "vr_window":                 50,
}


# ── Descarga Binance ───────────────────────────────────────────────────────────

def fetch_klines(symbol: str, start_ms: int, end_ms: int) -> list:
    rows, limit, cur = [], 1500, start_ms
    print(f"[fetch] {symbol} 1m {_ms(start_ms)} -> {_ms(end_ms)}")
    while cur < end_ms:
        url = (f"{FAPI}/fapi/v1/klines?symbol={symbol}&interval=1m"
               f"&startTime={cur}&endTime={end_ms}&limit={limit}")
        req = urllib.request.Request(url, headers={"User-Agent": "flowsurface/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                page = json.loads(r.read().decode())
        except Exception as e:
            print(f"  warn: {e}, reintentando...")
            time_mod.sleep(2)
            continue
        if not page:
            break
        rows.extend(page)
        cur = int(page[-1][0]) + 60_000
        if len(page) < limit:
            break
        time_mod.sleep(0.08)
    print(f"  {len(rows)} barras descargadas")
    return rows


def _ms(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")


def klines_to_df(raw: list, symbol: str) -> pd.DataFrame:
    cols = ["ts_open","open","high","low","close","volume","ts_close",
            "quote_vol","trades","taker_buy_base","taker_buy_quote","_"]
    df = pd.DataFrame(raw, columns=cols).drop(columns=["_"])
    for c in ["open","high","low","close","volume","taker_buy_base"]:
        df[c] = df[c].astype(float)
    df["ts_ms"] = df["ts_open"].astype("int64")
    df["bar_delta"] = 2.0 * df["taker_buy_base"] - df["volume"]
    df["symbol"] = symbol
    return df


# ── Maquina de estados AMD ─────────────────────────────────────────────────────

def _f(v): return float(v) if v is not None else 0.0


class AmdState:
    def __init__(self, use_slope_gate=True, require_diverge=True, cfg=None):
        self.cfg = cfg or AMD_CFG
        self.use_slope_gate  = use_slope_gate
        self.require_diverge = require_diverge
        self.vol_hist     = deque(maxlen=self.cfg["vr_window"] + 5)
        self.history      = deque(maxlen=self.cfg["accum_max_bars"] + 10)
        self.cvd_acc_hist = deque(maxlen=self.cfg["cvd_slope_win"] + 5)
        self.cvd_running  = 0.0
        self.bars_seen    = 0
        self.last_signal_bar = 0
        self._reset()
        # Contadores de diagnóstico
        self.diag = {
            "accum_range_fail": 0,
            "accum_max_bars_exceeded": 0,
            "spike_vr_fail": 0,
            "spike_cvd_div_fail": 0,
            "entry_closes_wrong": 0,
            "entry_vr_fail": 0,
            "entry_slope_fail": 0,
            "entry_risk_too_small": 0,
            "entry_rr_fail": 0,
            "signals_emitted": 0,
            "signals_vetoed": 0,
        }

    def _reset(self):
        self.phase = "IDLE"
        self.range_high = self.range_low = None
        self.cvd_sum = 0.0
        self.accum_bars = 0
        self.spike_extreme = self.spike_dir = None
        self.vr_at_spike = self.bar_delta_at_spike = None
        self.m_range_high = self.m_range_low = None
        self.m_range_bars = self.m_cvd = None
        self.bars_since_spike = 0

    def _vr(self, vol):
        if len(self.vol_hist) < 5:
            return 1.0
        m = sum(self.vol_hist) / len(self.vol_hist)
        return vol / m if m > 0 else 1.0

    def _slope(self):
        n = len(self.cvd_acc_hist)
        if n < 5:
            return None
        xs = list(range(n)); ys = list(self.cvd_acc_hist)
        mx = sum(xs)/n; my = sum(ys)/n
        num = sum((x-mx)*(y-my) for x,y in zip(xs,ys))
        den = sum((x-mx)**2 for x in xs)
        return num/den if abs(den) > 1e-10 else None

    def _try_accum(self):
        n = self.cfg["accum_min_bars"]
        if len(self.history) < n:
            return
        w = list(self.history)[-n:]
        rh = max(b["h"] for b in w); rl = min(b["l"] for b in w)
        c  = w[-1]["c"]
        pct = (rh - rl) / c * 100.0
        if not (self.cfg["accum_range_min_pct"] <= pct <= self.cfg["accum_range_max_pct"]):
            return
        self.phase = "ACCUMULATING"
        self.range_high = rh; self.range_low = rl
        self.cvd_sum = sum(b["d"] for b in w)
        self.accum_bars = n

    def on_bar(self, ts_ms, high, low, close, volume, bar_delta):
        self.bars_seen += 1
        self.vol_hist.append(volume)
        self.history.append({"h": high, "l": low, "c": close, "d": bar_delta})
        self.cvd_running += bar_delta
        self.cvd_acc_hist.append(self.cvd_running)

        if self.bars_seen < self.cfg["warmup_bars"]:
            return None

        vr    = self._vr(volume)
        slope = self._slope()

        if self.phase == "IDLE":
            self._try_accum()
            return None

        if self.phase == "ACCUMULATING":
            if self.range_low < close < self.range_high:
                new_rh = max(self.range_high, high)
                new_rl = min(self.range_low, low)
                if (new_rh - new_rl) / close * 100.0 > self.cfg["accum_range_max_pct"]:
                    self.diag["accum_range_fail"] += 1
                    self._reset(); return None
                self.range_high = new_rh; self.range_low = new_rl
                self.accum_bars += 1; self.cvd_sum += bar_delta
                if self.accum_bars > self.cfg["accum_max_bars"]:
                    self.diag["accum_max_bars_exceeded"] += 1
                    self._reset()
                return None

            pct = (self.range_high - self.range_low) / close * 100.0
            if not (self.cfg["accum_range_min_pct"] <= pct <= self.cfg["accum_range_max_pct"]) \
                    or self.accum_bars < self.cfg["accum_min_bars"]:
                self.diag["accum_range_fail"] += 1
                self._reset(); return None
            if vr < self.cfg["manip_min_vr"]:
                self.diag["spike_vr_fail"] += 1
                self._reset(); return None

            spike_dir = "Up" if close > self.range_high else "Down"
            spike_ext = high if spike_dir == "Up" else low
            cvd_div = (spike_dir == "Up" and bar_delta < 0) or \
                      (spike_dir == "Down" and bar_delta > 0)
            if self.require_diverge and not cvd_div:
                self.diag["spike_cvd_div_fail"] += 1
                self._reset(); return None

            self.phase = "MANIPULATION_DETECTED"
            self.spike_extreme = spike_ext; self.spike_dir = spike_dir
            self.vr_at_spike = vr; self.bar_delta_at_spike = bar_delta
            self.m_range_high = self.range_high; self.m_range_low = self.range_low
            self.m_range_bars = self.accum_bars; self.m_cvd = self.cvd_sum
            self.bars_since_spike = 0
            return None

        if self.phase == "MANIPULATION_DETECTED":
            if self.bars_since_spike >= self.cfg["max_wait_bars_after_spike"]:
                self._reset(); return None
            self.bars_since_spike += 1

            dist_dir = "Short" if self.spike_dir == "Up" else "Long"
            closes_right = (dist_dir == "Short" and close < self.m_range_high) or \
                           (dist_dir == "Long"  and close > self.m_range_low)
            if not closes_right:
                self.diag["entry_closes_wrong"] += 1
                return None
            if vr < self.cfg["dist_min_vr"]:
                self.diag["entry_vr_fail"] += 1
                return None
            if self.use_slope_gate:
                if slope is None:
                    self.diag["entry_slope_fail"] += 1
                    return None
                # Filtro direccional de signo — Long necesita slope > 0, Short slope < 0
                ok = (dist_dir == "Long"  and slope > 0) or \
                     (dist_dir == "Short" and slope < 0)
                if not ok:
                    self.diag["entry_slope_fail"] += 1
                    return None

            entry = close
            buf   = self.cfg["stop_buffer_pct"] / 100.0
            stop  = self.spike_extreme * (1 + buf) if dist_dir == "Short" \
                    else self.spike_extreme * (1 - buf)
            risk  = abs(stop - entry)
            if risk < 1.0:
                self.diag["entry_risk_too_small"] += 1
                return None
            target = entry - risk * 2.0 if dist_dir == "Short" else entry + risk * 2.0
            rr = abs(target - entry) / risk
            if rr < self.cfg["min_rr"]:
                self.diag["entry_rr_fail"] += 1
                return None

            session = _session(ts_ms)

            veto_reason = None

            sig = {
                "ts_ms": ts_ms,
                "direction": dist_dir,
                "entry": entry, "stop": stop, "target": target,
                "rr": round(rr, 3),
                "range_high": self.m_range_high, "range_low": self.m_range_low,
                "range_bars": self.m_range_bars,
                "spike_dir": self.spike_dir,
                "vr_spike": round(self.vr_at_spike, 3),
                "vr_entry": round(vr, 3),
                "delta_spike": round(self.bar_delta_at_spike, 2),
                "cvd_slope": round(slope, 2) if slope else None,
                "session": session,
                "veto_reason": veto_reason,
            }
            self._reset()
            if veto_reason:
                self.diag["signals_vetoed"] += 1
                sig["exit"] = "VETOED"
                sig["result_r"] = None
            else:
                self.diag["signals_emitted"] += 1
            return sig

        return None


def _session(ts_ms):
    h = (ts_ms // 3_600_000) % 24
    if   8  <= h < 13: return "London"
    elif 13 <= h < 17: return "LondonNY"
    elif 17 <= h < 22: return "NewYork"
    return "Asia"


# ── NautilusTrader Strategy ────────────────────────────────────────────────────

class AmdStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: str
    use_slope_gate: bool = True
    require_diverge: bool = True
    max_hold_bars: int = 120


class AmdStrategy(Strategy):
    def __init__(self, config: AmdStrategyConfig):
        super().__init__(config)
        self.amd = AmdState(
            use_slope_gate=config.use_slope_gate,
            require_diverge=config.require_diverge,
        )
        self._bar_type: BarType | None = None    # inyectado antes de run()
        self._delta_map: dict[int, float] = {}   # ts_ns -> bar_delta
        self._open_trades: list[dict] = []
        self.signals: list[dict] = []

    def set_bar_type(self, bar_type: BarType):
        self._bar_type = bar_type

    def set_delta_map(self, delta_map: dict):
        self._delta_map = delta_map

    def on_start(self):
        self.subscribe_bars(self._bar_type)

    def on_bar(self, bar: Bar):
        ts_ms = bar.ts_event // 1_000_000
        bar_delta = self._delta_map.get(bar.ts_event, 0.0)

        high  = float(bar.high)
        low   = float(bar.low)
        close = float(bar.close)
        vol   = float(bar.volume)

        # Evaluar trades abiertos
        closed_idx = []
        for i, trade in enumerate(self._open_trades):
            d = trade["direction"]
            if d == "Short":
                if low <= trade["target"]:
                    trade["exit"] = "TARGET"
                    trade["result_r"] = trade["rr"]
                    trade["exit_bar"] = self.amd.bars_seen
                    closed_idx.append(i)
                elif high >= trade["stop"]:
                    trade["exit"] = "STOP"
                    trade["result_r"] = -1.0
                    trade["exit_bar"] = self.amd.bars_seen
                    closed_idx.append(i)
                elif self.amd.bars_seen - trade["open_bar"] >= self.config.max_hold_bars:
                    trade["exit"] = "TIMEOUT"
                    trade["result_r"] = 0.0
                    trade["exit_bar"] = self.amd.bars_seen
                    closed_idx.append(i)
            else:
                if high >= trade["target"]:
                    trade["exit"] = "TARGET"
                    trade["result_r"] = trade["rr"]
                    trade["exit_bar"] = self.amd.bars_seen
                    closed_idx.append(i)
                elif low <= trade["stop"]:
                    trade["exit"] = "STOP"
                    trade["result_r"] = -1.0
                    trade["exit_bar"] = self.amd.bars_seen
                    closed_idx.append(i)
                elif self.amd.bars_seen - trade["open_bar"] >= self.config.max_hold_bars:
                    trade["exit"] = "TIMEOUT"
                    trade["result_r"] = 0.0
                    trade["exit_bar"] = self.amd.bars_seen
                    closed_idx.append(i)

        for i in reversed(closed_idx):
            self.signals.append(self._open_trades.pop(i))

        # Correr detector AMD
        sig = self.amd.on_bar(ts_ms, high, low, close, vol, bar_delta)
        if sig is not None:
            sig.setdefault("open_bar", self.amd.bars_seen)
            sig.setdefault("exit_bar", None)
            if sig.get("exit") == "VETOED":
                # Registrar pero no abrir trade
                self.signals.append(sig)
            else:
                sig["exit"] = "OPEN"
                sig["result_r"] = None
                self._open_trades.append(sig)

    def on_stop(self):
        # Cerrar trades abiertos al final
        for trade in self._open_trades:
            trade["exit"] = "OPEN"
            trade["result_r"] = None
            self.signals.append(trade)
        self._open_trades.clear()


# ── Construccion del instrumento ───────────────────────────────────────────────

def make_instrument(symbol: str, venue_str: str = "BINANCE") -> CryptoPerpetual:
    base_str = symbol.replace("USDT", "")
    venue = Venue(venue_str)
    instrument_id = InstrumentId(Symbol(f"{symbol}-PERP"), venue)

    # Precisiones tipicas de Binance para BTC/ETH/SOL/BNB
    price_precision_map = {"BTC": 1, "ETH": 2, "BNB": 2, "SOL": 3}
    size_precision_map  = {"BTC": 3, "ETH": 2, "BNB": 2, "SOL": 0}
    tick_map  = {"BTC": "0.1", "ETH": "0.01", "BNB": "0.01", "SOL": "0.001"}
    step_map  = {"BTC": "0.001", "ETH": "0.01", "BNB": "0.01", "SOL": "1"}

    base_sym = base_str.upper()
    from nautilus_trader.model.currencies import Currency
    try:
        base_ccy = Currency.from_str(base_sym)
    except Exception:
        base_ccy = BTC  # fallback

    return CryptoPerpetual(
        instrument_id=instrument_id,
        raw_symbol=Symbol(symbol),
        base_currency=base_ccy,
        quote_currency=USDT,
        settlement_currency=USDT,
        is_inverse=False,
        price_precision=price_precision_map.get(base_sym, 2),
        size_precision=size_precision_map.get(base_sym, 3),
        price_increment=Price.from_str(tick_map.get(base_sym, "0.1")),
        size_increment=Quantity.from_str(step_map.get(base_sym, "0.001")),
        max_quantity=Quantity.from_str("1000"),
        min_quantity=Quantity.from_str("0.001"),
        max_notional=None,
        min_notional=Money(Decimal("5"), USDT),
        max_price=Price.from_str("10000000"),
        min_price=Price.from_str("0.001"),
        margin_init=Decimal("0.01"),
        margin_maint=Decimal("0.005"),
        maker_fee=Decimal("0.0002"),
        taker_fee=Decimal("0.0004"),
        ts_event=0,
        ts_init=0,
    )


# ── Analisis de resultados ─────────────────────────────────────────────────────

def print_results(signals: list, use_slope_gate: bool, days: int):
    vetoed   = [s for s in signals if s.get("exit") == "VETOED"]
    tradeable = [s for s in signals if s.get("exit") != "VETOED"]
    closed   = [s for s in tradeable if s["exit"] in ("TARGET", "STOP")]
    timeouts = [s for s in tradeable if s["exit"] == "TIMEOUT"]
    open_    = [s for s in tradeable if s["exit"] == "OPEN"]
    weeks    = days / 7

    mode = "slope direccional ON + veto Asia Long" if use_slope_gate else "slope OFF + veto Asia Long"
    print(f"\n{'='*70}")
    print(f"  AMD Backtest NautilusTrader [{mode}]")
    print(f"{'='*70}")
    print(f"  Periodo       : {days} dias ({weeks:.1f} semanas)")
    print(f"  Senales totales generadas : {len(signals)}  ({len(signals)/weeks:.1f}/semana)")
    print(f"  Vetadas (Asia Long)       : {len(vetoed)}  ({len(vetoed)/weeks:.1f}/semana)")
    print(f"  Tradeadas                 : {len(tradeable)}  ({len(tradeable)/weeks:.1f}/semana)")
    print(f"    Cerradas  : {len(closed)}")
    print(f"    Timeout   : {len(timeouts)}")
    print(f"    Abiertas  : {len(open_)}")

    if not closed:
        print("  Sin senales cerradas.")
        return

    wins   = [s for s in closed if s["result_r"] > 0]
    losses = [s for s in closed if s["result_r"] < 0]
    avg_r  = sum(s["result_r"] for s in closed) / len(closed)

    print(f"\n  -- General (n={len(closed)}) --")
    print(f"  Win rate  : {len(wins)/len(closed)*100:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"  Avg R     : {avg_r:+.3f}")
    print(f"  Expectancy: {avg_r:.3f}R por trade")

    def group_table(title, key_fn, include_vetoed=False):
        pool = signals if include_vetoed else closed
        groups = {}
        for s in pool:
            if not include_vetoed and s.get("exit") not in ("TARGET", "STOP"):
                continue
            k = key_fn(s)
            groups.setdefault(k, []).append(s)

        print(f"\n  -- {title} --")
        if include_vetoed:
            print(f"  {'':22} {'total':>6} {'trad/sem':>9} {'n_closed':>9} {'WR%':>6} {'avgR':>7}")
            print(f"  {'-'*62}")
            # Para include_vetoed mostramos todas las señales con sus stats
            all_groups: dict = {}
            for s in signals:
                k = key_fn(s)
                all_groups.setdefault(k, []).append(s)
            for k, ss in sorted(all_groups.items(), key=lambda x: -len(x[1])):
                total = len(ss)
                cl = [s for s in ss if s["exit"] in ("TARGET","STOP")]
                n = len(cl)
                trad_sem = (total - sum(1 for s in ss if s.get("exit")=="VETOED")) / weeks
                wr  = sum(1 for s in cl if s["result_r"] > 0) / n * 100 if n else 0
                avg = sum(s["result_r"] for s in cl) / n if n else 0
                veto_tag = " [VETOED]" if all(s.get("exit")=="VETOED" for s in ss) else ""
                print(f"  {str(k)+veto_tag:22} {total:>6} {trad_sem:>9.2f} {n:>9} {wr:>6.1f}% {avg:>+7.3f}")
        else:
            print(f"  {'':22} {'n':>4} {'sig/sem':>8} {'WR%':>6} {'avgR':>7}")
            print(f"  {'-'*50}")
            for k, ss in sorted(groups.items(), key=lambda x: -len(x[1])):
                n = len(ss)
                sig_sem = n / weeks
                w = sum(1 for s in ss if s["result_r"] > 0)
                r = sum(s["result_r"] for s in ss) / n
                print(f"  {str(k):22} {n:>4} {sig_sem:>8.2f} {w/n*100:>6.1f}% {r:>+7.3f}")

    group_table("Por sesion+dir (todas, incl vetadas)", lambda s: f"{s['session']} {s['direction']}", include_vetoed=True)
    group_table("Por sesion (tradeadas)",    lambda s: s["session"])
    group_table("Por direccion (tradeadas)", lambda s: s["direction"])
    group_table("Por spike direction",       lambda s: f"Spike {s['spike_dir']} -> {s['direction']}")

    print(f"\n  -- Ultimas {min(30, len(tradeable))} senales tradeadas --")
    print(f"  {'Fecha':16} {'Dir':6} {'Ses':9} {'slope':>7} {'exit':>7} {'R':>6}")
    print(f"  {'-'*58}")
    for s in tradeable[-30:]:
        ts  = datetime.fromtimestamp(s["ts_ms"]/1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        r   = f"{s['result_r']:+.2f}" if s["result_r"] is not None else "OPEN"
        slp = f"{s['cvd_slope']:+.1f}" if s.get("cvd_slope") is not None else "--"
        print(f"  {ts:16} {s['direction']:6} {s['session']:9} {slp:>7} {s['exit']:>7} {r:>6}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol",       default="BTCUSDT")
    parser.add_argument("--days",         type=int, default=30)
    parser.add_argument("--no-slope",     action="store_true")
    parser.add_argument("--diverge",       action="store_true",
                        help="Activar gate CVD diverge en spike (default OFF)")
    parser.add_argument("--load-parquet", action="store_true",
                        help="Cargar desde dataset/*.parquet si existe")
    args = parser.parse_args()

    # ── 1. Datos ───────────────────────────────────────────────────────────────
    parquet_path = DATASET_DIR / f"{args.symbol.lower()}_{args.days}m.parquet"
    if args.load_parquet and parquet_path.exists():
        print(f"[data] Cargando {parquet_path}")
        df = pd.read_parquet(parquet_path)
    else:
        now_ms   = int(time_mod.time() * 1000)
        start_ms = now_ms - args.days * 86_400_000
        raw = fetch_klines(args.symbol, start_ms, now_ms)
        if not raw:
            print("Error: sin datos de Binance")
            return
        df = klines_to_df(raw, args.symbol)

    df = df.sort_values("ts_ms").reset_index(drop=True)
    print(f"[data] {len(df):,} barras M1  {df['ts_ms'].iloc[0]} -> {df['ts_ms'].iloc[-1]}")

    # ── 2. Instrumento Nautilus ────────────────────────────────────────────────
    venue_str = "BINANCE"
    instrument = make_instrument(args.symbol, venue_str)
    bar_type = BarType(
        instrument_id=instrument.id,
        bar_spec=BarSpecification(1, BarAggregation.MINUTE, PriceType.LAST),
        aggregation_source=AggregationSource.EXTERNAL,
    )
    bar_type_str = str(bar_type)

    # ── 3. Wrangling de barras ─────────────────────────────────────────────────
    print("[wrangler] Convirtiendo dataframe -> Nautilus Bars...")
    wrangler = BarDataWrangler(bar_type=bar_type, instrument=instrument)

    df_nautilus = pd.DataFrame({
        "open":      df["open"].astype(float),
        "high":      df["high"].astype(float),
        "low":       df["low"].astype(float),
        "close":     df["close"].astype(float),
        "volume":    df["volume"].astype(float),
        "timestamp": pd.to_datetime(df["ts_ms"], unit="ms", utc=True),
    }).set_index("timestamp")

    bars: list[Bar] = wrangler.process(df_nautilus)
    print(f"  {len(bars):,} Bar objects creados")

    # delta_map: ts_ns -> bar_delta (para lookup en on_bar)
    delta_map = {
        int(ts * 1_000_000): float(d)
        for ts, d in zip(df["ts_ms"].values, df["bar_delta"].values)
    }

    # ── 4. BacktestEngine ──────────────────────────────────────────────────────
    print("[engine] Configurando NautilusTrader BacktestEngine...")
    engine = BacktestEngine(
        config=BacktestEngineConfig(
            logging=LoggingConfig(log_level="ERROR"),  # silenciar logs internos
        )
    )

    engine.add_venue(
        venue=Venue(venue_str),
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USDT,
        starting_balances=[Money(Decimal("100000"), USDT)],
    )
    engine.add_instrument(instrument)
    engine.add_data(bars)

    # ── 5. Estrategia AMD ──────────────────────────────────────────────────────
    strategy_cfg = AmdStrategyConfig(
        instrument_id=str(instrument.id),
        use_slope_gate=not args.no_slope,
        require_diverge=args.diverge,
    )
    strategy = AmdStrategy(config=strategy_cfg)
    strategy.set_bar_type(bar_type)
    strategy.set_delta_map(delta_map)
    engine.add_strategy(strategy)

    # ── 6. Run ─────────────────────────────────────────────────────────────────
    print("[run] Corriendo backtest...")
    t0 = time_mod.time()
    engine.run()
    elapsed = time_mod.time() - t0
    print(f"  Completado en {elapsed:.2f}s")

    # ── 7. Resultados ──────────────────────────────────────────────────────────
    all_signals = strategy.signals + list(strategy._open_trades)
    print_results(all_signals, use_slope_gate=not args.no_slope, days=args.days)

    # Diagnóstico de filtros
    d = strategy.amd.diag
    total_rejected = sum(v for k, v in d.items() if k not in ("signals_emitted", "signals_vetoed"))
    print(f"\n{'='*50}")
    print(f"  DIAGNOSTICO DE FILTROS")
    print(f"{'='*50}")
    print(f"  {'Gate':<30} {'bloqueadas':>10}")
    print(f"  {'-'*42}")
    labels = [
        ("accum_range_fail",         "rango acum invalido"),
        ("accum_max_bars_exceeded",  "accum > max_bars"),
        ("spike_vr_fail",            "spike VR < 2.0"),
        ("spike_cvd_div_fail",       "spike CVD no diverge"),
        ("entry_closes_wrong",       "price no retrocede"),
        ("entry_vr_fail",            "entry VR < 1.5"),
        ("entry_slope_fail",         "slope direccional fail"),
        ("entry_risk_too_small",     "risk < $1"),
        ("entry_rr_fail",            "RR < 2.0"),
    ]
    for key, label in labels:
        v = d[key]
        if v > 0:
            print(f"  {label:<30} {v:>10,}")
    print(f"  {'-'*42}")
    print(f"  {'TOTAL BLOQUEADAS':<30} {total_rejected:>10,}")
    print(f"  {'Emitidas (tradeadas)':<30} {d['signals_emitted']:>10}")
    print(f"  {'Emitidas (vetadas)':<30} {d['signals_vetoed']:>10}")

    # Guardar CSV
    if all_signals:
        import csv
        out = DATASET_DIR / f"amd_signals_{args.symbol}_{args.days}d.csv"
        DATASET_DIR.mkdir(exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(all_signals[0].keys()))
            w.writeheader(); w.writerows(all_signals)
        print(f"\n[output] {out}  ({len(all_signals)} senales)")

    engine.dispose()


if __name__ == "__main__":
    main()
