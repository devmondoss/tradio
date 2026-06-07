#!/usr/bin/env python3
"""
AMD Backtest — Accumulation · Manipulation · Distribution.

Replica la maquina de estados sobre datos historicos de Binance REST M1.
Usa bar_delta calculado desde taker_buy_vol (disponible en /fapi/v1/klines),
lo que permite correr sobre 14-30 dias sin necesitar los datos de microestructura
en vivo (VPIN y OBI no estan disponibles en datos historicos).

Modo de deteccion:
    Manipulacion : VR >= manip_min_vr  +  CVD diverge (bar_delta contra precio)
    Entry        : cierre de vuelta en rango + VR >= dist_min_vr + cvd_slope confirma
    [VPIN y OBI no disponibles en REST — desactivados]

Uso:
    python scripts/amd_backtest.py
    python scripts/amd_backtest.py --days 14
    python scripts/amd_backtest.py --dir Short
    python scripts/amd_backtest.py --session London
    python scripts/amd_backtest.py --no-slope   # desactivar gate cvd_slope en entry
"""

import json
import os
import sys
import argparse
import time as time_mod
import urllib.request
from collections import deque
from datetime import datetime, timezone

# ── Binance FAPI ───────────────────────────────────────────────────────────────

BINANCE_FAPI = "https://fapi.binance.com"

def fetch_binance_klines(symbol, interval, start_ms, end_ms):
    """Fetchea klines M1 de Binance FAPI con paginacion automatica."""
    all_bars = []
    limit = 1500
    cur = start_ms
    print(f"[fetch] {symbol} {interval} desde {ms_to_str(start_ms)} hasta {ms_to_str(end_ms)}...")
    while cur < end_ms:
        url = (f"{BINANCE_FAPI}/fapi/v1/klines"
               f"?symbol={symbol}&interval={interval}"
               f"&startTime={cur}&endTime={end_ms}&limit={limit}")
        req = urllib.request.Request(url, headers={"User-Agent": "flowsurface-backtest/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                page = json.loads(resp.read().decode())
        except Exception as e:
            print(f"[warn] Error en fetch: {e}. Reintentando...")
            time_mod.sleep(2)
            continue
        if not page:
            break
        all_bars.extend(page)
        cur = int(page[-1][0]) + 60_000
        if len(page) < limit:
            break
        time_mod.sleep(0.1)
    print(f"[fetch] {len(all_bars)} barras descargadas")
    return all_bars


def parse_klines(raw):
    """
    Convierte el formato crudo de Binance a dicts.
    Binance kline: [open_time, o, h, l, c, vol, close_time, quote_vol,
                    trades, taker_buy_base, taker_buy_quote, ignore]
    bar_delta = 2 * taker_buy_base - volume  (misma formula que el monitor)
    """
    bars = []
    for k in raw:
        open_time  = int(k[0])
        high       = float(k[2])
        low        = float(k[3])
        close      = float(k[4])
        volume     = float(k[5])
        taker_buy  = float(k[9])
        bar_delta  = 2.0 * taker_buy - volume
        bars.append({
            "ts_ms":     open_time,
            "high":      high,
            "low":       low,
            "close":     close,
            "volume":    volume,
            "bar_delta": bar_delta,
        })
    return bars


# ── Utilidades ─────────────────────────────────────────────────────────────────

def ms_to_str(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def f(v, default=0.0):
    return float(v) if v is not None else default

def classify_session(ts_ms):
    h = (ts_ms // 3_600_000) % 24
    if   8  <= h < 13: return "London"
    elif 13 <= h < 17: return "LondonNyOverlap"
    elif 17 <= h < 22: return "NewYork"
    else:              return "Asia"


# ── Parametros ─────────────────────────────────────────────────────────────────

CFG = {
    # Acumulacion
    "accum_range_min_pct":       0.06,
    "accum_range_max_pct":       0.45,
    "accum_min_bars":            15,
    "accum_max_bars":            50,
    # Manipulacion (VPIN desactivado — no disponible en REST)
    "manip_min_vr":              2.0,
    # Distribucion/entry (OBI desactivado — no disponible en REST)
    "dist_min_vr":               1.5,   # bajado de 2.5 — la barra de entry no siempre es de alto VR
    "dist_cvd_slope":            10.0,  # |cvd_slope| minimo para confirmar
    # Trade
    "stop_buffer_pct":           0.08,  # % sobre spike extreme
    "min_rr":                    2.0,
    "cooldown_bars":             45,
    # Timeout
    "max_wait_bars_after_spike": 10,
    # Simulacion
    "max_hold_bars":             120,   # 2h maximo antes de OPEN
    "warmup_bars":               60,
    # CVD slope para calcular internamente
    "cvd_slope_win":             20,    # ventana OLS para cvd_slope
    "vr_window":                 50,
}


# ── Maquina de estados AMD ─────────────────────────────────────────────────────

class AmdState:
    def __init__(self, use_slope_gate=True, require_diverge=True):
        self.use_slope_gate  = use_slope_gate
        self.require_diverge = require_diverge
        self.reset_state()
        self.vol_hist     = deque(maxlen=CFG["vr_window"] + 5)
        self.history      = deque(maxlen=CFG["accum_max_bars"] + 10)
        self.cvd_running  = 0.0
        self.cvd_acc_hist = deque(maxlen=CFG["cvd_slope_win"] + 5)
        self.bars_seen    = 0
        self.last_signal_bar = 0

    def reset_state(self):
        self.phase = "IDLE"
        self.range_high = self.range_low = None
        self.cvd_sum = 0.0
        self.accum_bars = 0
        self.spike_extreme = self.spike_dir = None
        self.vr_at_spike = self.bar_delta_at_spike = None
        self.m_range_high = self.m_range_low = None
        self.m_range_bars = self.m_cvd = None
        self.bars_since_spike = 0

    def compute_vr(self, volume):
        if len(self.vol_hist) < 5:
            return 1.0
        mean = sum(self.vol_hist) / len(self.vol_hist)
        return volume / mean if mean > 0 else 1.0

    def compute_cvd_slope(self):
        """OLS slope del CVD acumulado — misma formula que el monitor Rust."""
        n = len(self.cvd_acc_hist)
        if n < 5:
            return None
        xs = list(range(n))
        ys = list(self.cvd_acc_hist)
        mx = sum(xs) / n
        my = sum(ys) / n
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        den = sum((x - mx) ** 2 for x in xs)
        return num / den if abs(den) > 1e-10 else None

    def try_enter_accumulation(self):
        n = CFG["accum_min_bars"]
        if len(self.history) < n:
            return
        w = list(self.history)[-n:]
        rh = max(b["high"] for b in w)
        rl = min(b["low"]  for b in w)
        close = w[-1]["close"]
        pct = (rh - rl) / close * 100.0
        if pct < CFG["accum_range_min_pct"] or pct > CFG["accum_range_max_pct"]:
            return
        self.phase      = "ACCUMULATING"
        self.range_high = rh
        self.range_low  = rl
        self.cvd_sum    = sum(b["delta"] for b in w)
        self.accum_bars = n

    def on_bar(self, row):
        high   = f(row["high"])
        low    = f(row["low"])
        close  = f(row["close"])
        volume = f(row["volume"])
        delta  = f(row["bar_delta"])
        ts_ms  = row["ts_ms"]

        self.bars_seen += 1
        self.vol_hist.append(volume)
        self.history.append({"high": high, "low": low, "close": close, "delta": delta})
        self.cvd_running += delta
        self.cvd_acc_hist.append(self.cvd_running)

        if self.bars_seen < CFG["warmup_bars"]:
            return None
        if self.bars_seen - self.last_signal_bar < CFG["cooldown_bars"]:
            return None

        vr        = self.compute_vr(volume)
        cvd_slope = self.compute_cvd_slope()

        # ── IDLE ──────────────────────────────────────────────────────────────
        if self.phase == "IDLE":
            self.try_enter_accumulation()
            return None

        # ── ACCUMULATING ──────────────────────────────────────────────────────
        if self.phase == "ACCUMULATING":
            if close > self.range_low and close < self.range_high:
                new_rh = max(self.range_high, high)
                new_rl = min(self.range_low,  low)
                if (new_rh - new_rl) / close * 100.0 > CFG["accum_range_max_pct"]:
                    self.reset_state(); return None
                self.range_high  = new_rh
                self.range_low   = new_rl
                self.accum_bars += 1
                self.cvd_sum    += delta
                if self.accum_bars > CFG["accum_max_bars"]:
                    self.reset_state()
                return None

            # Precio cerro FUERA del rango
            pct = (self.range_high - self.range_low) / close * 100.0
            if pct < CFG["accum_range_min_pct"] or pct > CFG["accum_range_max_pct"] \
                    or self.accum_bars < CFG["accum_min_bars"]:
                self.reset_state(); return None

            if vr < CFG["manip_min_vr"]:
                self.reset_state(); return None

            spike_dir = "Up" if close > self.range_high else "Down"
            spike_ext = high if spike_dir == "Up" else low

            # CVD diverge del precio (firma de manipulacion) — opcional segun flag
            cvd_div = (spike_dir == "Up" and delta < 0.0) or \
                      (spike_dir == "Down" and delta > 0.0)
            if self.require_diverge and not cvd_div:
                self.reset_state(); return None

            self.phase              = "MANIPULATION_DETECTED"
            self.spike_extreme      = spike_ext
            self.spike_dir          = spike_dir
            self.vr_at_spike        = vr
            self.bar_delta_at_spike = delta
            self.m_range_high       = self.range_high
            self.m_range_low        = self.range_low
            self.m_range_bars       = self.accum_bars
            self.m_cvd              = self.cvd_sum
            self.bars_since_spike   = 0
            return None

        # ── MANIPULATION_DETECTED ─────────────────────────────────────────────
        if self.phase == "MANIPULATION_DETECTED":
            if self.bars_since_spike >= CFG["max_wait_bars_after_spike"]:
                self.reset_state(); return None
            self.bars_since_spike += 1

            dist_dir = "Short" if self.spike_dir == "Up" else "Long"

            # Cierra de vuelta dentro/debajo del nivel roto
            closes_right = (
                (dist_dir == "Short" and close < self.m_range_high) or
                (dist_dir == "Long"  and close > self.m_range_low)
            )
            if not closes_right:
                return None

            if vr < CFG["dist_min_vr"]:
                return None

            # CVD slope confirma (si el gate esta activado)
            if self.use_slope_gate:
                if cvd_slope is None:
                    return None
                cs_ok = (dist_dir == "Short" and cvd_slope < -CFG["dist_cvd_slope"]) or \
                        (dist_dir == "Long"  and cvd_slope >  CFG["dist_cvd_slope"])
                if not cs_ok:
                    return None

            entry = close
            buf   = CFG["stop_buffer_pct"] / 100.0
            stop  = self.spike_extreme * (1.0 + buf) if dist_dir == "Short" \
                    else self.spike_extreme * (1.0 - buf)
            risk  = abs(stop - entry)
            if risk < 1.0:
                return None

            target = entry - risk * 2.0 if dist_dir == "Short" else entry + risk * 2.0
            rr     = abs(target - entry) / risk
            if rr < CFG["min_rr"]:
                return None

            pct = (self.m_range_high - self.m_range_low) / entry * 100.0
            sig = {
                "timestamp_ms":         ts_ms,
                "direction":            dist_dir,
                "entry_price":          entry,
                "stop_price":           stop,
                "target_price":         target,
                "rr":                   round(rr, 3),
                "range_high":           self.m_range_high,
                "range_low":            self.m_range_low,
                "range_pct":            round(pct, 4),
                "range_bars":           self.m_range_bars,
                "cvd_in_range":         round(self.m_cvd, 2),
                "spike_extreme":        self.spike_extreme,
                "spike_direction":      self.spike_dir,
                "vr_at_spike":          round(self.vr_at_spike, 3),
                "bar_delta_at_spike":   round(self.bar_delta_at_spike, 2),
                "vr_at_entry":          round(vr, 3),
                "cvd_slope_at_entry":   round(cvd_slope, 2) if cvd_slope else None,
                "session":              classify_session(ts_ms),
                "exit":                 None,
                "result_r":             None,
                "exit_bars":            None,
            }
            self.last_signal_bar = self.bars_seen
            self.reset_state()
            return sig

        return None


# ── Simulacion de outcomes ─────────────────────────────────────────────────────

def simulate_outcomes(signals, bars):
    idx_map = {r["ts_ms"]: i for i, r in enumerate(bars)}
    for sig in signals:
        i0 = idx_map.get(sig["timestamp_ms"])
        if i0 is None:
            sig["exit"] = "OPEN"; continue
        d = sig["direction"]
        stop = sig["stop_price"]; target = sig["target_price"]
        for k in range(1, CFG["max_hold_bars"] + 1):
            if i0 + k >= len(bars): break
            b = bars[i0 + k]
            h = f(b["high"]); l = f(b["low"])
            if d == "Short":
                if l <= target:
                    sig["exit"] = "TARGET"; sig["result_r"] = sig["rr"]; sig["exit_bars"] = k; break
                if h >= stop:
                    sig["exit"] = "STOP";   sig["result_r"] = -1.0;      sig["exit_bars"] = k; break
            else:
                if h >= target:
                    sig["exit"] = "TARGET"; sig["result_r"] = sig["rr"]; sig["exit_bars"] = k; break
                if l <= stop:
                    sig["exit"] = "STOP";   sig["result_r"] = -1.0;      sig["exit_bars"] = k; break
        else:
            sig["exit"] = "OPEN"
    return signals


# ── Analisis ───────────────────────────────────────────────────────────────────

def print_table(title, groups):
    print(f"\n{title}")
    print(f"{'':25} {'n':>4} {'WR%':>6} {'avgR':>7} {'wins':>5} {'losses':>6}")
    print("-" * 60)
    for key, sigs in sorted(groups.items(), key=lambda x: -len(x[1])):
        closed = [s for s in sigs if s["exit"] in ("TARGET", "STOP")]
        if not closed: continue
        n = len(closed)
        wins = sum(1 for s in closed if s["result_r"] > 0)
        losses = sum(1 for s in closed if s["result_r"] < 0)
        avg = sum(s["result_r"] for s in closed) / n
        print(f"  {str(key):23} {n:>4} {wins/n*100:>6.1f}% {avg:>+7.3f} {wins:>5} {losses:>6}")


def analyze(signals, use_slope_gate):
    mode = "slope gate ON" if use_slope_gate else "slope gate OFF (solo VR + CVD diverge)"
    closed = [s for s in signals if s["exit"] in ("TARGET", "STOP")]
    opened = [s for s in signals if s["exit"] == "OPEN"]

    print(f"\n{'='*65}")
    print(f"AMD Backtest [{mode}]")
    print(f"{'='*65}")
    print(f"Total senales   : {len(signals)}")
    print(f"Cerradas        : {len(closed)}")
    print(f"Abiertas (>2h)  : {len(opened)}")

    if not closed:
        print("Sin senales cerradas para analizar.")
        return

    wins   = sum(1 for s in closed if s["result_r"] > 0)
    losses = sum(1 for s in closed if s["result_r"] < 0)
    avg_r  = sum(s["result_r"] for s in closed) / len(closed)
    avg_bars = sum(s["exit_bars"] for s in closed if s["exit_bars"]) / max(len(closed), 1)

    print(f"\n-- Generales (n={len(closed)}) ------------------------------------------------")
    print(f"  Win rate   : {wins/len(closed)*100:.1f}%  ({wins}W / {losses}L)")
    print(f"  Avg R      : {avg_r:+.3f}")
    print(f"  Avg min    : {avg_bars:.1f}")

    by_session = {}
    for s in closed: by_session.setdefault(s["session"], []).append(s)
    print_table("-- Por sesion --", by_session)

    by_dir = {}
    for s in closed: by_dir.setdefault(s["direction"], []).append(s)
    print_table("-- Por direccion --", by_dir)

    by_sd = {}
    for s in closed:
        k = f"{s['session']} {s['direction']}"
        by_sd.setdefault(k, []).append(s)
    print_table("-- Por sesion + direccion --", by_sd)

    by_spike = {}
    for s in closed:
        k = f"Spike {s['spike_direction']} -> {s['direction']}"
        by_spike.setdefault(k, []).append(s)
    print_table("-- Por spike direction --", by_spike)

    # Tabla individual
    limit = min(len(signals), 60)
    print(f"\n-- Senales individuales (mostrando {limit}) --------------------------------")
    print(f"{'Timestamp':20} {'Dir':6} {'Ses':15} {'Spk':4} {'VRsp':5} {'dlt_sp':7} {'VRen':5} {'slp_en':8} {'exit':7} {'R':>6}")
    print("-" * 90)
    for s in signals[:limit]:
        ts    = ms_to_str(s["timestamp_ms"])
        slope = f"{s['cvd_slope_at_entry']:+.1f}" if s["cvd_slope_at_entry"] is not None else "--"
        r_str = f"{s['result_r']:+.2f}" if s["result_r"] is not None else "OPEN"
        print(
            f"{ts:20} {s['direction']:6} {s['session']:15} "
            f"{s['spike_direction']:4} {s['vr_at_spike']:5.2f}x "
            f"{s['bar_delta_at_spike']:+7.0f} {s['vr_at_entry']:5.2f}x "
            f"{slope:8} {s['exit']:7} {r_str:>6}"
        )


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AMD Backtest (Binance REST M1)")
    parser.add_argument("symbol",     nargs="?", default="BTCUSDT")
    parser.add_argument("--days",     type=int,  default=14, help="Ultimos N dias (default 14)")
    parser.add_argument("--dir",      choices=["Short", "Long"], default=None)
    parser.add_argument("--session",  default=None)
    parser.add_argument("--no-slope", action="store_true",
                        help="Desactivar gate cvd_slope en entry (mas senales, menos filtro)")
    parser.add_argument("--no-diverge", action="store_true",
                        help="No exigir CVD diverge en el spike (solo VR >= 2.0)")
    parser.add_argument("--min-vr-spike", type=float, default=2.0,
                        help="VR minimo en el spike (default 2.0)")
    parser.add_argument("--min-vr-entry", type=float, default=1.5,
                        help="VR minimo en la barra de entry (default 1.5)")
    args = parser.parse_args()

    now_ms    = int(time_mod.time() * 1000)
    start_ms  = now_ms - args.days * 86400 * 1000

    raw  = fetch_binance_klines(args.symbol, "1m", start_ms, now_ms)
    bars = parse_klines(raw)

    if not bars:
        sys.exit("[error] Sin barras descargadas de Binance")

    t0 = ms_to_str(bars[0]["ts_ms"]); t1 = ms_to_str(bars[-1]["ts_ms"])
    print(f"[parse] {len(bars)} barras M1: {t0} -> {t1}")

    use_slope = not args.no_slope
    CFG["manip_min_vr"] = args.min_vr_spike
    CFG["dist_min_vr"]  = args.min_vr_entry
    state = AmdState(use_slope_gate=use_slope, require_diverge=not args.no_diverge)
    signals = []

    for row in bars:
        sig = state.on_bar(row)
        if sig is None: continue
        if args.dir and sig["direction"] != args.dir: continue
        if args.session and sig["session"] != args.session: continue
        signals.append(sig)

    print(f"[detector] {len(signals)} senales AMD generadas")

    if not signals:
        print("Sin senales. Prueba con --no-slope para relajar el gate de CVD slope.")
        return

    signals = simulate_outcomes(signals, bars)
    analyze(signals, use_slope_gate=use_slope)

    # Guardar CSV
    import csv
    out = f"amd_backtest_{args.symbol}_{args.days}d.csv"
    with open(out, "w", newline="") as fc:
        w = csv.DictWriter(fc, fieldnames=list(signals[0].keys()))
        w.writeheader(); w.writerows(signals)
    print(f"\n[output] {out} guardado ({len(signals)} filas)")


if __name__ == "__main__":
    main()
