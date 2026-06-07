#!/usr/bin/env python3
"""
RBF Backtest — replica el detector RangeBreakoutFlow sobre btc_bars de Supabase.

Lee los datos ya grabados por Railway, corre el mismo algoritmo de detección
y simula el outcome de cada señal (TARGET / STOP / OPEN).

Uso:
    python scripts/rbf_backtest.py
    python scripts/rbf_backtest.py ETHUSDT
    python scripts/rbf_backtest.py BTCUSDT --dir Short   # solo una dirección
    python scripts/rbf_backtest.py BTCUSDT --session London

Requiere:
    SUPABASE_URL y SUPABASE_KEY como variables de entorno.

Limitaciones vs detector live:
    - HVN veto desactivado: btc_bars no tiene los niveles HVN del Volume Profile.
    - Flag 5 (LVN/thin): solo usa thin_zone, no lvn_nearby (mismo motivo).
    - Señal puede divergir 1-2 barras vs live si Railway tuvo lag al arrancar.
"""

import json
import os
import sys
import time as time_mod
import urllib.request
from collections import deque
from datetime import datetime, timezone

# -- Conexión Supabase ----------------------------------------------------------

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ztdhvmcisjjyhbqlgkzm.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")


def fetch_all(table, params):
    """Paginación automática — devuelve todos los registros."""
    rows = []
    limit = 1000
    offset = 0
    while True:
        url = f"{SUPABASE_URL}/rest/v1/{table}?{params}&limit={limit}&offset={offset}"
        req = urllib.request.Request(url, headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        })
        with urllib.request.urlopen(req) as resp:
            page = json.loads(resp.read().decode())
        rows.extend(page)
        if len(page) < limit:
            break
        offset += limit
    return rows


def fetch_bars(symbol):
    print(f"[fetch] descargando btc_bars para {symbol}...")
    params = (
        f"symbol=eq.{symbol}&order=ts_ms.asc"
        f"&select=ts_ms,session,open,high,low,close,volume,bar_delta,"
        f"cvd_slope,obi_l5,dz,vr,stacked_imb,absorption,"
        f"thin_above,thin_below,vpin,oi_momentum,vwap,regime,atr"
    )
    rows = fetch_all("btc_bars", params)
    if not rows:
        sys.exit(f"Sin datos en btc_bars para {symbol}")
    rows.sort(key=lambda r: r["ts_ms"])
    t0 = ms_to_str(rows[0]["ts_ms"])
    t1 = ms_to_str(rows[-1]["ts_ms"])
    print(f"[fetch] {len(rows)} barras: {t0} -> {t1}")
    return rows


# -- Utilidades -----------------------------------------------------------------

def ms_to_str(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def f(v, default=0.0):
    """Convierte valor a float, usando default si es None."""
    return float(v) if v is not None else default


def b(v):
    """Bool seguro."""
    return bool(v) if v is not None else False


def classify_session(ts_ms):
    h = (ts_ms // 3_600_000) % 24
    if   8  <= h < 13: return "London"
    elif 13 <= h < 17: return "LondonNyOverlap"
    elif 17 <= h < 22: return "NewYork"
    else:              return "OffHours"


# -- Parámetros del detector (mirrors strategy.toml) ---------------------------

RANGE_WINDOWS   = [15, 20, 30, 45, 60]
RANGE_MIN_PCT   = 0.08
RANGE_MAX_PCT   = 0.55
VR_MIN          = 2.0
DZ_MIN          = 0.5
DZ_MAX          = 3.0
CVD_SLOPE_GATE  = True
OBI_THRESHOLD   = 0.15    # Flag 2, escala [-1,1]
STOP_PCT        = 0.0025
TARGET_S_PCT    = 0.0050
TARGET_L_PCT    = 0.0045
MIN_RR          = 1.5
COOLDOWN_BARS   = 60
WARMUP_BARS     = 110     # VR_WINDOW(50) + max_history(60)
CVD_SLOPE_THR   = 15.0
BEAR_LONG_SCORE = 5
MIN_SCORE       = 1
OPERATIVE       = {"London", "LondonNyOverlap"}
MAX_HOLD_BARS   = 240     # 4h máximo antes de marcar OPEN


# -- Score de confluencia -------------------------------------------------------

def score_confluence(direction, regime, cvd_slope, obi, vwap, entry, target, row):
    """
    Replica score_confluence() de range_breakout_flow.rs.
    HVN veto desactivado: no tenemos los niveles en btc_bars.
    """
    # Veto VPIN
    vpin = f(row.get("vpin"), None) if row.get("vpin") is not None else None
    if vpin is not None and vpin > 0.65:
        return 0, [], "vpin_toxic"

    score = 0
    flags = []

    # Flag 1: CVD slope
    if cvd_slope is not None:
        aligned = cvd_slope < -CVD_SLOPE_THR if direction == "Short" else cvd_slope > CVD_SLOPE_THR
        if aligned:
            score += 1
            flags.append("cvd_slope")

    # Flag 2: OBI L5
    obi_aligned = obi < -OBI_THRESHOLD if direction == "Short" else obi > OBI_THRESHOLD
    if obi_aligned:
        score += 1
        flags.append("obi")

    # Flag 3: Stacked imbalance
    stacked_imb = str(row.get("stacked_imb") or "")
    stacked = stacked_imb == "Bearish" if direction == "Short" else stacked_imb == "Bullish"
    if stacked:
        score += 1
        flags.append("stacked_imbalance")

    # Flag 4: Absorción footprint
    absorption = str(row.get("absorption") or "")
    absorbed = absorption == "Ask" if direction == "Short" else absorption == "Bid"
    if absorbed:
        score += 1
        flags.append("absorption")

    # Flag 5: LVN / thin zone (solo thin — lvn_nearby no está en btc_bars)
    thin = b(row.get("thin_below")) if direction == "Short" else b(row.get("thin_above"))
    if thin:
        score += 1
        flags.append("lvn_thin")

    # Flag 6: VWAP bias
    vwap_val = f(row.get("vwap"), 0.0)
    if vwap_val > 0.0:
        above = entry > vwap_val
        aligned = not above if direction == "Short" else above
        if aligned:
            score += 1
            flags.append("vwap_bias")

    # Flag 7: OI momentum
    if row.get("oi_momentum") is True:
        score += 1
        flags.append("oi_momentum")

    # Veto Long contra Bear sin confluencia máxima
    if direction == "Long" and regime in ("Bear", "BearPullback") and score < BEAR_LONG_SCORE:
        return score, flags, "long_bear_low_score"

    return score, flags, None


# -- Detector principal --------------------------------------------------------

def run_detector(bars, filter_dir=None, filter_session=None):
    """
    Corre el detector RBF sobre la lista de barras.
    Devuelve lista de señales (dicts).
    """
    history = deque(maxlen=max(RANGE_WINDOWS) + 2)
    bars_seen = 0
    last_signal_bar = 0
    signals = []

    for row in bars:
        ts_ms   = int(row["ts_ms"])
        high    = f(row.get("high"))
        low     = f(row.get("low"))
        close   = f(row.get("close"))
        volume  = f(row.get("volume"))
        delta   = f(row.get("bar_delta"))
        session = row.get("session") or "OffHours"
        regime  = row.get("regime") or "Unknown"
        dz      = f(row.get("dz"))
        vr      = f(row.get("vr"))
        cvd_s   = float(row["cvd_slope"]) if row.get("cvd_slope") is not None else None
        obi     = f(row.get("obi_l5"))
        vwap    = f(row.get("vwap"), 0.0)
        atr     = f(row.get("atr"))

        history.append({
            "high": high, "low": low, "close": close,
            "volume": volume, "delta": delta,
        })
        bars_seen += 1

        if bars_seen < WARMUP_BARS:
            continue
        if bars_seen - last_signal_bar < COOLDOWN_BARS:
            continue
        if session not in OPERATIVE:
            continue
        if vr < VR_MIN:
            continue
        if filter_session and session != filter_session:
            continue

        hist_list = list(history)
        hist_len  = len(hist_list)

        for window_bars in RANGE_WINDOWS:
            if hist_len < window_bars + 1:
                continue

            window   = hist_list[-(window_bars + 1):-1]
            rng_high = max(b_["high"] for b_ in window)
            rng_low  = min(b_["low"]  for b_ in window)
            rng_pct  = (rng_high - rng_low) / close * 100.0

            if not (RANGE_MIN_PCT <= rng_pct <= RANGE_MAX_PCT):
                continue

            cvd_in_range = sum(b_["delta"] for b_ in window)

            breaks_down = close < rng_low
            breaks_up   = close > rng_high
            if not breaks_down and not breaks_up:
                continue

            direction = "Short" if breaks_down else "Long"
            sign      = 1.0 if breaks_down else -1.0

            # CVD alineado
            cvd_ok = cvd_in_range < 0 if direction == "Short" else cvd_in_range > 0
            if not cvd_ok:
                continue

            # CVD slope gate
            if CVD_SLOPE_GATE and cvd_s is not None:
                if sign * (-cvd_s) <= 0.0:
                    continue

            # dz gate
            dz_dir = sign * (-dz)
            if not (DZ_MIN <= dz_dir <= DZ_MAX):
                continue

            if filter_dir and direction != filter_dir:
                continue

            # Precios
            s_pct = STOP_PCT
            t_pct = TARGET_S_PCT if direction == "Short" else TARGET_L_PCT
            if direction == "Short":
                stop_price   = close * (1.0 + s_pct)
                target_price = close * (1.0 - t_pct)
            else:
                stop_price   = close * (1.0 - s_pct)
                target_price = close * (1.0 + t_pct)

            risk   = abs(close - stop_price)
            reward = abs(target_price - close)
            rr     = reward / risk if risk > 1e-10 else 0.0
            if rr < MIN_RR:
                continue

            # Score confluencia
            score, flag_list, veto = score_confluence(
                direction, regime, cvd_s, obi,
                vwap if vwap > 0 else None,
                close, target_price, row,
            )

            tradeable = veto is None and score >= MIN_SCORE

            signals.append({
                "ts_ms":          ts_ms,
                "ts_str":         ms_to_str(ts_ms),
                "direction":      direction,
                "session":        session,
                "regime":         regime,
                "entry":          close,
                "stop":           stop_price,
                "target":         target_price,
                "rr":             rr,
                "range_pct":      rng_pct,
                "range_bars":     window_bars,
                "cvd_in_range":   cvd_in_range,
                "vr":             vr,
                "dz_dir":         dz_dir,
                "cvd_slope":      cvd_s,
                "obi":            obi,
                "atr":            atr,
                "score":          score,
                "flags":          flag_list,
                "veto":           veto,
                "tradeable":      tradeable,
                # outcome — se rellena en simulate_outcomes()
                "exit_reason":    None,
                "result_r":       None,
                "bars_to_exit":   None,
            })
            last_signal_bar = bars_seen
            break  # un solo range por barra (primero que encaje)

    return signals


# -- Simulación de outcomes -----------------------------------------------------

def simulate_outcomes(signals, bars):
    """
    Para cada señal, busca hacia adelante el primer bar que toca target o stop.
    Marca OPEN si nadie lo toca dentro de MAX_HOLD_BARS.
    """
    ts_to_idx = {int(r["ts_ms"]): i for i, r in enumerate(bars)}

    for sig in signals:
        start_i = ts_to_idx.get(sig["ts_ms"])
        if start_i is None:
            sig["exit_reason"] = "NO_DATA"
            continue

        is_long = sig["direction"] == "Long"
        entry   = sig["entry"]
        stop    = sig["stop"]
        target  = sig["target"]
        risk    = abs(entry - stop)

        for j in range(start_i + 1, min(start_i + MAX_HOLD_BARS + 1, len(bars))):
            row = bars[j]
            h   = f(row.get("high"))
            lo  = f(row.get("low"))

            if is_long:
                if lo <= stop:
                    sig["exit_reason"]  = "STOP"
                    sig["result_r"]     = -1.0
                    sig["bars_to_exit"] = j - start_i
                    break
                if h >= target:
                    sig["exit_reason"]  = "TARGET"
                    sig["result_r"]     = (target - entry) / risk
                    sig["bars_to_exit"] = j - start_i
                    break
            else:
                if h >= stop:
                    sig["exit_reason"]  = "STOP"
                    sig["result_r"]     = -1.0
                    sig["bars_to_exit"] = j - start_i
                    break
                if lo <= target:
                    sig["exit_reason"]  = "TARGET"
                    sig["result_r"]     = (entry - target) / risk
                    sig["bars_to_exit"] = j - start_i
                    break

        if sig["exit_reason"] is None:
            sig["exit_reason"] = "OPEN"


# -- Reporte -------------------------------------------------------------------

def report(signals, symbol):
    total     = len(signals)
    tradeable = [s for s in signals if s["tradeable"]]
    vetoed    = [s for s in signals if not s["tradeable"]]

    print(f"\n{'='*72}")
    print(f"RBF BACKTEST — {symbol}   total={total}  tradeable={len(tradeable)}  vetadas={len(vetoed)}")
    print(f"{'='*72}")

    if not signals:
        print("Sin señales.")
        return

    # -- Tabla de señales ------------------------------------------------------
    hdr = f"{'Timestamp':20} {'Dir':5} {'Ses':18} {'Reg':12} {'Sc':3} {'VR':5} {'Rng%':6} {'CVD':7} {'Flags':28} {'Veto/Exit':16} {'R':6}"
    print(f"\n{hdr}")
    print("-" * len(hdr))

    for s in signals:
        flags_str = ",".join(s["flags"]) if s["flags"] else "-"
        veto_exit = s["veto"] or s["exit_reason"] or "?"
        r_str = f"{s['result_r']:+.2f}" if s["result_r"] is not None else "-"
        tradeable_tag = " " if s["tradeable"] else "[V]"
        print(
            f"{s['ts_str']:20} {s['direction']:5} {s['session']:18} {s['regime']:12} "
            f"{s['score']:3} {s['vr']:5.2f} {s['range_pct']:6.3f} "
            f"{s['cvd_in_range']:7.0f} {flags_str:28} {veto_exit:16} {r_str:6} {tradeable_tag}"
        )

    # -- Stats de tradeables ---------------------------------------------------
    if not tradeable:
        print("\nSin señales tradeables.")
        return

    closed = [s for s in tradeable if s["exit_reason"] in ("TARGET", "STOP")]
    open_  = [s for s in tradeable if s["exit_reason"] == "OPEN"]

    print(f"\n{'-'*55}")
    print(f"TRADEABLES: {len(tradeable)}  cerradas={len(closed)}  abiertas={len(open_)}")

    if not closed:
        print("Sin señales cerradas aún (todas OPEN).")
        return

    wins   = [s for s in closed if s["exit_reason"] == "TARGET"]
    losses = [s for s in closed if s["exit_reason"] == "STOP"]
    avg_r  = sum(s["result_r"] for s in closed) / len(closed)
    wr     = len(wins) / len(closed) * 100

    print(f"Win rate: {wr:.1f}%  ({len(wins)}W / {len(losses)}L)")
    print(f"Avg R:    {avg_r:+.3f}")
    avg_bars = sum(s["bars_to_exit"] for s in closed if s["bars_to_exit"]) / len(closed)
    print(f"Avg bars to exit: {avg_bars:.1f}")

    # Por sesión
    _stats_by(closed, "session", ["London", "LondonNyOverlap"])

    # Por dirección
    _stats_by(closed, "direction", ["Short", "Long"])

    # Por régimen
    regimes = sorted(set(s["regime"] for s in closed))
    _stats_by(closed, "regime", regimes)

    # Por score
    print(f"\n  Score  |  n  |  WR%  |  avgR")
    print(f"  -------+-----+-------+------")
    for sc in sorted(set(s["score"] for s in closed)):
        sc_sigs = [s for s in closed if s["score"] == sc]
        sc_wins = sum(1 for s in sc_sigs if s["exit_reason"] == "TARGET")
        sc_avg  = sum(s["result_r"] for s in sc_sigs) / len(sc_sigs)
        sc_wr   = sc_wins / len(sc_sigs) * 100
        print(f"  {sc:5}  | {len(sc_sigs):3} | {sc_wr:5.1f} | {sc_avg:+.3f}")

    # Flags más frecuentes en winners vs losers
    print(f"\n  Flag              | en winners | en losers | diff")
    print(f"  ------------------+------------+-----------+------")
    all_flags = sorted(set(f for s in closed for f in s["flags"]))
    for flag in all_flags:
        w_pct = sum(1 for s in wins   if flag in s["flags"]) / max(len(wins), 1) * 100
        l_pct = sum(1 for s in losses if flag in s["flags"]) / max(len(losses), 1) * 100
        print(f"  {flag:18} | {w_pct:9.1f}% | {l_pct:8.1f}% | {w_pct-l_pct:+.1f}pp")

    # Vetos: qué bloquearon
    if vetoed:
        print(f"\n  Vetos ({len(vetoed)} señales bloqueadas):")
        veto_counts = {}
        for s in vetoed:
            veto_counts[s["veto"]] = veto_counts.get(s["veto"], 0) + 1
        for reason, cnt in sorted(veto_counts.items(), key=lambda x: -x[1]):
            print(f"    {reason}: {cnt}")


def _stats_by(closed, key, values):
    print(f"\n  {'-'*52}")
    print(f"  {key:20} |  n  |  WR%  |  avgR")
    print(f"  {'-'*20}-+-----+-------+------")
    for v in values:
        sigs = [s for s in closed if s[key] == v]
        if not sigs:
            continue
        wins = sum(1 for s in sigs if s["exit_reason"] == "TARGET")
        avg  = sum(s["result_r"] for s in sigs) / len(sigs)
        wr   = wins / len(sigs) * 100
        print(f"  {v:20} | {len(sigs):3} | {wr:5.1f} | {avg:+.3f}")


# -- Extended backtest: Binance REST + microestructura Supabase ----------------

def fetch_binance_klines(symbol, days=14):
    """Descarga M1 OHLCV de Binance FAPI yendo hacia atrás en bloques de 1500."""
    target   = days * 24 * 60
    bars     = []
    end_time = int(time_mod.time() * 1000)

    print(f"[binance] descargando ~{target} barras M1 ({days} dias)...")
    while len(bars) < target:
        url = (
            f"https://fapi.binance.com/fapi/v1/klines"
            f"?symbol={symbol}&interval=1m&limit=1500&endTime={end_time}"
        )
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req) as resp:
            page = json.loads(resp.read().decode())
        if not page:
            break
        parsed = []
        for k in page:
            vol       = float(k[5])
            taker_buy = float(k[9])
            parsed.append({
                "ts_ms":     int(k[0]),
                "open":      float(k[1]),
                "high":      float(k[2]),
                "low":       float(k[3]),
                "close":     float(k[4]),
                "volume":    vol,
                "bar_delta": 2.0 * taker_buy - vol,
            })
        bars     = parsed + bars
        end_time = int(page[0][0]) - 1
        if len(page) < 1500:
            break

    # Descartar la vela abierta actual si está en los datos
    now_ms = int(time_mod.time() * 1000)
    bars   = [b for b in bars if b["ts_ms"] < now_ms - 90_000]
    bars   = sorted(bars, key=lambda b: b["ts_ms"])[-target:]
    print(f"[binance] {len(bars)} barras: {ms_to_str(bars[0]['ts_ms'])} -> {ms_to_str(bars[-1]['ts_ms'])}")
    return bars


def fetch_micro_map(symbol):
    """
    Construye dict ts_ms -> campos de microestructura combinando
    scalping_bars (Jun 1-5) y btc_bars (Jun 5+). btc_bars tiene prioridad.
    """
    micro = {}

    # scalping_bars -- tiene obi_l5 y absorption pero no stacked/thin/vpin
    print("[micro] cargando scalping_bars...")
    try:
        params = (
            f"symbol=eq.{symbol}&order=ts_ms.asc"
            f"&select=ts_ms,obi_l5,absorption_long,absorption_short,cvd_slope"
        )
        rows = fetch_all("scalping_bars", params)
        for r in rows:
            ts       = int(r["ts_ms"])
            abs_long = bool(r.get("absorption_long"))
            abs_sht  = bool(r.get("absorption_short"))
            # absorption_long = bids absorbing selling = señal alcista = Bid
            # absorption_short = asks absorbing buying = señal bajista = Ask
            absorption = "Bid" if abs_long else ("Ask" if abs_sht else "None")
            micro[ts] = {
                "obi_l5":      r.get("obi_l5"),
                "absorption":  absorption,
                "stacked_imb": "None",
                "thin_above":  False,
                "thin_below":  False,
                "vpin":        None,
                "oi_momentum": None,
                "vwap_db":     None,
                "source":      "scalping",
            }
        print(f"[micro]   scalping_bars: {len(rows)} barras")
    except Exception as e:
        print(f"[micro]   scalping_bars error: {e}")

    # btc_bars -- cobertura completa, sobreescribe scalping_bars
    table = f"{symbol[:3].lower()}_bars"
    print(f"[micro] cargando {table}...")
    try:
        params = (
            f"symbol=eq.{symbol}&order=ts_ms.asc"
            f"&select=ts_ms,obi_l5,stacked_imb,absorption,"
            f"thin_above,thin_below,vpin,oi_momentum,vwap"
        )
        rows = fetch_all(table, params)
        for r in rows:
            ts = int(r["ts_ms"])
            micro[ts] = {
                "obi_l5":      r.get("obi_l5"),
                "absorption":  r.get("absorption") or "None",
                "stacked_imb": r.get("stacked_imb") or "None",
                "thin_above":  bool(r.get("thin_above")),
                "thin_below":  bool(r.get("thin_below")),
                "vpin":        r.get("vpin"),
                "oi_momentum": r.get("oi_momentum"),
                "vwap_db":     r.get("vwap"),
                "source":      "btc_bars",
            }
        print(f"[micro]   {table}: {len(rows)} barras")
    except Exception as e:
        print(f"[micro]   {table} error: {e}")

    covered = sum(1 for v in micro.values() if v["source"] == "btc_bars")
    print(f"[micro] total: {len(micro)} barras con microestructura ({covered} de btc_bars)")
    return micro


def _ols_slope(seq):
    """Slope OLS sobre una secuencia. Devuelve None si hay menos de 5 puntos."""
    n = len(seq)
    if n < 5:
        return None
    sx  = sum(range(n))
    sy  = sum(seq)
    sxy = sum(i * y for i, y in enumerate(seq))
    sx2 = sum(i * i for i in range(n))
    den = n * sx2 - sx * sx
    return (n * sxy - sx * sy) / den if abs(den) > 1e-10 else None


def run_detector_extended(binance_bars, micro_map,
                          filter_dir=None, filter_session=None):
    """
    Backtest extendido: OHLCV de Binance + microestructura de Supabase donde exista.
    Computa dz / vr / cvd_slope / EMA480 / VWAP internamente para todos los periodos.
    """
    history      = deque(maxlen=max(RANGE_WINDOWS) + 2)
    vol_hist     = deque(maxlen=50)
    delta_hist   = deque(maxlen=50)
    cvd_acc_hist = deque(maxlen=20)

    ema480 = ema480_prev = 0.0
    ema480_ok = False
    EMA_K = 2.0 / 481.0

    vwap_day = -1
    vwap_pv  = vwap_vol = 0.0

    cvd_running  = 0.0
    bars_seen    = 0
    last_sig_bar = 0
    signals      = []

    for bar in binance_bars:
        ts_ms     = bar["ts_ms"]
        high      = bar["high"]
        low       = bar["low"]
        close     = bar["close"]
        volume    = bar["volume"]
        bar_delta = bar["bar_delta"]

        # -- Indicadores internos --
        if not ema480_ok:
            ema480 = ema480_prev = close
            ema480_ok = True
        else:
            ema480_prev = ema480
            ema480      = close * EMA_K + ema480 * (1.0 - EMA_K)

        above  = close > ema480
        rising = ema480 > ema480_prev
        if above and rising:          regime = "TrendUp"
        elif not above and not rising: regime = "TrendDown"
        elif above and not rising:    regime = "BullPullback"
        else:                          regime = "BearPullback"

        day = ts_ms // 86_400_000
        if day != vwap_day:
            vwap_pv = vwap_vol = 0.0
            vwap_day = day
        typical  = (high + low + close) / 3.0
        vwap_pv += typical * volume
        vwap_vol += volume
        vwap_int = vwap_pv / vwap_vol if vwap_vol > 0 else 0.0

        vol_hist.append(volume)
        delta_hist.append(bar_delta)
        mean_vol = sum(vol_hist) / len(vol_hist) if vol_hist else 1.0
        vr       = volume / mean_vol if mean_vol > 0 else 0.0

        n_d  = len(delta_hist)
        if n_d >= 5:
            mean_d = sum(delta_hist) / n_d
            var_d  = sum((d - mean_d) ** 2 for d in delta_hist) / n_d
            std_d  = var_d ** 0.5
            dz     = (bar_delta - mean_d) / std_d if std_d > 1e-8 else 0.0
        else:
            dz = 0.0

        cvd_running += bar_delta
        cvd_acc_hist.append(cvd_running)
        cvd_slope = _ols_slope(list(cvd_acc_hist))

        session = classify_session(ts_ms)

        # -- Microestructura de Supabase (si existe para este ts_ms) --
        m = micro_map.get(ts_ms, {})
        obi      = f(m.get("obi_l5"), 0.0)
        vwap     = f(m.get("vwap_db"), 0.0) or vwap_int
        stk_imb  = m.get("stacked_imb", "None") or "None"
        absorb   = m.get("absorption", "None") or "None"
        thin_ab  = bool(m.get("thin_above", False))
        thin_bl  = bool(m.get("thin_below", False))
        vpin     = m.get("vpin")
        oi_mom   = m.get("oi_momentum")
        has_micro = bool(m)

        history.append({"high": high, "low": low, "close": close,
                        "volume": volume, "delta": bar_delta})
        bars_seen += 1

        if bars_seen < WARMUP_BARS:
            continue
        if bars_seen - last_sig_bar < COOLDOWN_BARS:
            continue
        if session not in OPERATIVE:
            continue
        if vr < VR_MIN:
            continue
        if filter_session and session != filter_session:
            continue

        hist_list = list(history)
        hist_len  = len(hist_list)

        for window_bars in RANGE_WINDOWS:
            if hist_len < window_bars + 1:
                continue

            window   = hist_list[-(window_bars + 1):-1]
            rng_high = max(b_["high"]  for b_ in window)
            rng_low  = min(b_["low"]   for b_ in window)
            rng_pct  = (rng_high - rng_low) / close * 100.0

            if not (RANGE_MIN_PCT <= rng_pct <= RANGE_MAX_PCT):
                continue

            cvd_in_range = sum(b_["delta"] for b_ in window)
            breaks_down  = close < rng_low
            breaks_up    = close > rng_high
            if not breaks_down and not breaks_up:
                continue

            direction = "Short" if breaks_down else "Long"
            sign      = 1.0    if breaks_down else -1.0

            cvd_ok = cvd_in_range < 0 if direction == "Short" else cvd_in_range > 0
            if not cvd_ok:
                continue

            if CVD_SLOPE_GATE and cvd_slope is not None:
                if sign * (-cvd_slope) <= 0.0:
                    continue

            dz_dir = sign * (-dz)
            if not (DZ_MIN <= dz_dir <= DZ_MAX):
                continue

            if filter_dir and direction != filter_dir:
                continue

            s_pct = STOP_PCT
            t_pct = TARGET_S_PCT if direction == "Short" else TARGET_L_PCT
            if direction == "Short":
                stop_price   = close * (1.0 + s_pct)
                target_price = close * (1.0 - t_pct)
            else:
                stop_price   = close * (1.0 - s_pct)
                target_price = close * (1.0 + t_pct)

            risk   = abs(close - stop_price)
            reward = abs(target_price - close)
            rr     = reward / risk if risk > 1e-10 else 0.0
            if rr < MIN_RR:
                continue

            # Score con microestructura enriquecida
            mock_row = {
                "obi_l5":      obi,
                "stacked_imb": stk_imb,
                "absorption":  absorb,
                "thin_above":  thin_ab,
                "thin_below":  thin_bl,
                "vpin":        vpin,
                "oi_momentum": oi_mom,
                "vwap":        vwap if vwap > 0 else None,
            }
            score, flag_list, veto = score_confluence(
                direction, regime, cvd_slope, obi,
                vwap if vwap > 0 else None,
                close, target_price, mock_row,
            )
            tradeable = veto is None and score >= MIN_SCORE

            signals.append({
                "ts_ms":        ts_ms,
                "ts_str":       ms_to_str(ts_ms),
                "direction":    direction,
                "session":      session,
                "regime":       regime,
                "entry":        close,
                "stop":         stop_price,
                "target":       target_price,
                "rr":           rr,
                "range_pct":    rng_pct,
                "range_bars":   window_bars,
                "cvd_in_range": cvd_in_range,
                "vr":           vr,
                "dz_dir":       dz_dir,
                "cvd_slope":    cvd_slope,
                "obi":          obi,
                "atr":          0.0,
                "score":        score,
                "flags":        flag_list,
                "veto":         veto,
                "tradeable":    tradeable,
                "has_micro":    has_micro,  # indica si tenia microestructura de DB
                "exit_reason":  None,
                "result_r":     None,
                "bars_to_exit": None,
            })
            last_sig_bar = bars_seen
            break

    return signals


# -- Entry point ---------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RBF Backtest sobre btc_bars")
    parser.add_argument("symbol",     nargs="?", default="BTCUSDT")
    parser.add_argument("--dir",      choices=["Short", "Long"], help="Filtrar direction")
    parser.add_argument("--session",  choices=["London", "LondonNyOverlap"], help="Filtrar sesion")
    parser.add_argument("--extended", action="store_true",
                        help="Modo extendido: OHLCV de Binance REST + micro de Supabase")
    parser.add_argument("--days",     type=int, default=14,
                        help="Dias de historia Binance (solo con --extended, default 14)")
    args = parser.parse_args()

    if not SUPABASE_KEY:
        sys.exit("ERROR: SUPABASE_KEY no esta seteada")

    if args.extended:
        binance_bars = fetch_binance_klines(args.symbol, days=args.days)
        micro_map    = fetch_micro_map(args.symbol)
        signals      = run_detector_extended(
            binance_bars, micro_map,
            filter_dir=args.dir, filter_session=args.session,
        )
        simulate_outcomes(signals, binance_bars)
        report(signals, f"{args.symbol} extended {args.days}d")
    else:
        bars    = fetch_bars(args.symbol)
        signals = run_detector(bars, filter_dir=args.dir, filter_session=args.session)
        simulate_outcomes(signals, bars)
        report(signals, args.symbol)
