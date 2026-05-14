#!/usr/bin/env python3
"""
Diagnostico del sistema de estrategia y paper trading de flowsurface.

Lee desde %%APPDATA%%/flowsurface/shadow_events/:
  - paper_trades.jsonl      -- trades cerrados del motor de paper trading (fuente principal)
  - contradictions.jsonl    -- eventos de contradiccion entre detectores
  - strategy_signals.jsonl  -- senales emitidas (distribucion de scores)
  - strategy_outcomes.jsonl -- excursion MFE/MAE del tracker (legacy, mantenido)

Uso:
  python analyze_outcomes.py              # lee desde %%APPDATA%%
  python analyze_outcomes.py /ruta/dir   # lee desde directorio alternativo
  python analyze_outcomes.py --test      # corre con datos sinteticos (verifica que el script funciona)

Si algun archivo no existe o esta vacio, ese bloque reporta "sin datos" y continua.
Las lineas JSONL malformadas se saltean y se contabiliza cuantas fueron.
"""

import json
import os
import sys
import tempfile
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean, median

INITIAL_CAPITAL = float(os.environ.get("PAPER_INITIAL_CAPITAL", "3000.0"))


# --------------------------------------------------------------------------- #
# Carga de archivos                                                            #
# --------------------------------------------------------------------------- #

def load_jsonl(path: Path) -> tuple:
    """Carga un JSONL. Devuelve (registros, lineas_skipeadas).
    No crashea si el archivo no existe."""
    if not path.exists():
        return [], 0
    records = []
    skipped = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                skipped += 1
    return records, skipped


# --------------------------------------------------------------------------- #
# Helpers de formato                                                           #
# --------------------------------------------------------------------------- #

def section(title):
    print("\n" + "=" * 70)
    print("  " + title)
    print("=" * 70)


def no_data(reason="sin datos"):
    print("  [" + reason + "]")


def pct_of(value, total, digits=1):
    """value/total como porcentaje. 'n/a' si total es cero."""
    if abs(total) < 1e-10:
        return "n/a"
    return "{:.{}f}%".format(value / abs(total) * 100, digits)


def r_multiple(net_pnl, entry, stop, size):
    """Net PnL en R-multiples. None si no se puede calcular."""
    if stop is None or size is None or size <= 0:
        return None
    risk_amt = abs(entry - stop) * size
    if risk_amt < 1e-10:
        return None
    return net_pnl / risk_amt


def mfe_mae_in_r(mfe_price, mae_price, entry, stop):
    """MFE y MAE en R-multiples. None si no se puede calcular."""
    if stop is None:
        return None
    risk_unit = abs(entry - stop)
    if risk_unit < 1e-10:
        return None
    return mfe_price / risk_unit, mae_price / risk_unit


# --------------------------------------------------------------------------- #
# BLOQUE 1 -- El sistema gana o pierde?                                       #
# --------------------------------------------------------------------------- #

def block1_pnl(trades):
    section("BLOQUE 1 -- El sistema gana o pierde?")
    if not trades:
        no_data("sin trades cerrados en paper_trades.jsonl")
        return

    total_net = sum(t.get("net_pnl", 0.0) for t in trades)
    total_net_pct = total_net / INITIAL_CAPITAL * 100
    avg_net = total_net / len(trades)
    avg_net_pct = avg_net / INITIAL_CAPITAL * 100

    print("  Trades cerrados         : {}".format(len(trades)))
    print("  PnL neto total          : {:+.2f} USD  ({:+.2f}% sobre {:.0f} USD capital inicial)".format(
        total_net, total_net_pct, INITIAL_CAPITAL))
    print("  PnL neto promedio/trade : {:+.2f} USD  ({:+.3f}%)".format(avg_net, avg_net_pct))

    # Reconstruir equity curve desde trades ordenados por cierre
    sorted_trades = sorted(trades, key=lambda t: t.get("closed_at_ms", 0))
    equity = INITIAL_CAPITAL
    curve = [equity]
    for t in sorted_trades:
        equity += t.get("net_pnl", 0.0)
        curve.append(equity)

    peak_idx = curve.index(max(curve))
    peak = curve[peak_idx]
    trough = min(curve[peak_idx:])
    max_dd = peak - trough
    max_dd_pct = max_dd / peak * 100 if peak > 0 else 0.0

    print("\n  Equity curve (reconstruida desde trades cerrados):")
    print("    Capital inicial        : {:.2f} USD".format(curve[0]))
    print("    Equity maxima          : {:.2f} USD  (tras trade #{})".format(peak, peak_idx))
    print("    Equity minima absoluta : {:.2f} USD  (puede ser antes o despues del pico)".format(min(curve)))
    print("    Equity final           : {:.2f} USD".format(curve[-1]))
    print()
    print("  Drawdown maximo (peor caida desde un pico): {:.2f} USD  ({:.2f}%)".format(
        max_dd, max_dd_pct))
    print("  -- Medido pico->valle en el peor tramo descendente. Diferente al minimo absoluto")
    print("     si la equity cae antes de alcanzar su maximo historico.")


# --------------------------------------------------------------------------- #
# BLOQUE 2 -- Que detector funciona?                                          #
# --------------------------------------------------------------------------- #

def block2_detectors(trades):
    section("BLOQUE 2 -- Que detector funciona?")
    if not trades:
        no_data("sin trades cerrados")
        return

    by_det = defaultdict(list)
    for t in trades:
        det = t.get("strategy_id") or "unknown"
        by_det[det].append(t)

    COL = 32
    hdr = "  {:<{}} {:>4}  {:>8}  {:>6}  {:>6}  {:>6}  {:>6}  {:>10}".format(
        "Detector", COL, "N", "WinRate", "R-avg", "TARGET", "STOP", "TTL", "PnL neto")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    for det in sorted(by_det.keys()):
        group = by_det[det]
        n = len(group)
        wins = sum(1 for t in group if t.get("close_reason") == "TARGET_HIT")
        win_rate = wins / n if n else 0.0
        flag = "  [muestra chica]" if n < 10 else ""

        r_vals = []
        for t in group:
            v = r_multiple(
                t.get("net_pnl", 0.0),
                t.get("entry_price", 0.0),
                t.get("stop_price"),
                t.get("size", 0.0),
            )
            if v is not None:
                r_vals.append(v)

        r_avg = mean(r_vals) if r_vals else None

        by_reason = defaultdict(int)
        for t in group:
            by_reason[t.get("close_reason", "?")] += 1

        net_total = sum(t.get("net_pnl", 0.0) for t in group)
        r_str = "{:+.2f}".format(r_avg) if r_avg is not None else "  n/a"

        print("  {:<{}} {:>4}  {:>8.1%}  {:>6}  {:>6}  {:>6}  {:>6}  {:>+10.2f}{}".format(
            det, COL, n, win_rate, r_str,
            by_reason["TARGET_HIT"], by_reason["STOP_HIT"],
            by_reason["TTL_EXPIRED"], net_total, flag))

    print("\n  WinRate = TARGET_HIT / N.  R-avg = net_pnl / risk_amount.")
    print("  [muestra chica] = N < 10 trades -- no concluir nada estadistico.")


# --------------------------------------------------------------------------- #
# BLOQUE 3 -- El scoring discrimina?                                          #
# --------------------------------------------------------------------------- #

def block3_scoring(trades, signals):
    section("BLOQUE 3 -- El scoring discrimina?")

    # Distribucion de scores de senales emitidas
    all_scores = [s["score"] for s in signals
                  if isinstance(s.get("score"), (int, float))]

    if all_scores:
        print("  Senales emitidas: {}".format(len(all_scores)))
        print("  Score -- min: {:.3f}  max: {:.3f}  media: {:.3f}  mediana: {:.3f}".format(
            min(all_scores), max(all_scores), mean(all_scores), median(all_scores)))

        # Histograma por buckets de 0.05
        edges = [i / 100 for i in range(50, 100, 5)]  # 0.50 ... 0.95
        counts = [sum(1 for s in all_scores if lo <= s < lo + 0.05) for lo in edges]
        max_c = max(counts) if counts else 1
        if max_c == 0:
            max_c = 1
        print("\n  Histograma de scores (senales emitidas):")
        for lo, c in zip(edges, counts):
            bar = "#" * int(c / max_c * 30)
            print("    [{:.2f}-{:.2f}) {:>4}  {}".format(lo, lo + 0.05, c, bar))
    else:
        no_data("sin datos en strategy_signals.jsonl")

    # Rendimiento por bucket de score desde paper_trades
    if not trades:
        print("\n  Trades: sin datos para correlacion score -> resultado")
        return

    bucket_size = 0.05
    trade_buckets = defaultdict(list)
    for t in trades:
        score = t.get("score")
        if isinstance(score, (int, float)):
            bucket = round(int(score / bucket_size) * bucket_size, 2)
            trade_buckets[bucket].append(t)

    if not trade_buckets:
        print("\n  Trades sin campo 'score' -- no se puede correlacionar")
        return

    print("\n  Rendimiento por bucket de score (paper_trades):")
    print("  {:14} {:>4}  {:>8}  {:>7}".format("Bucket", "N", "WinRate", "R-avg"))
    for lo in sorted(trade_buckets.keys()):
        group = trade_buckets[lo]
        wins = sum(1 for t in group if t.get("close_reason") == "TARGET_HIT")
        wr = wins / len(group) if group else 0.0
        r_vals = []
        for t in group:
            v = r_multiple(
                t.get("net_pnl", 0.0),
                t.get("entry_price", 0.0),
                t.get("stop_price"),
                t.get("size", 0.0),
            )
            if v is not None:
                r_vals.append(v)
        r_avg = mean(r_vals) if r_vals else None
        r_str = "{:+.2f}".format(r_avg) if r_avg is not None else "  n/a"
        print("  [{:.2f}-{:.2f}) {:>4}  {:>8.1%}  {:>7}".format(
            lo, lo + bucket_size, len(group), wr, r_str))


# --------------------------------------------------------------------------- #
# BLOQUE 4 -- Cuanto cuestan los costos?                                      #
# --------------------------------------------------------------------------- #

def block4_costs(trades):
    section("BLOQUE 4 -- Cuanto cuestan los costos?")
    if not trades:
        no_data("sin trades cerrados")
        return

    total_gross = sum(t.get("gross_pnl", 0.0) for t in trades)
    total_fees  = sum(t.get("fees_paid", 0.0) for t in trades)
    total_fund  = sum(t.get("funding_paid", 0.0) for t in trades)
    total_slip  = sum(
        abs(t.get("entry_price", 0.0) - t.get("intended_entry", 0.0)) * t.get("size", 0.0)
        for t in trades
    )
    total_costs = total_fees + total_fund + total_slip

    print("  PnL bruto total   : {:+.4f} USD".format(total_gross))
    print("  Fees totales      : {:>10.4f} USD  ({} del bruto)".format(
        total_fees, pct_of(total_fees, total_gross)))
    print("  Funding total     : {:>10.4f} USD  ({} del bruto)  "
          "[positivo=Long pago, negativo=Short cobro]".format(
        total_fund, pct_of(total_fund, total_gross)))
    print("  Slippage total    : {:>10.4f} USD  ({} del bruto)  "
          "[estimado como |entry_fill - intended| x size]".format(
        total_slip, pct_of(total_slip, total_gross)))
    print("\n  --> Los costos se comieron el {} del PnL bruto ({:.4f} USD de {:.4f} USD bruto)".format(
        pct_of(total_costs, total_gross), total_costs, abs(total_gross)))
    if abs(total_gross) > 1e-10:
        net_ratio = (total_gross - total_costs) / abs(total_gross) * 100
        print("      PnL neto / PnL bruto = {:.1f}%".format(net_ratio))


# --------------------------------------------------------------------------- #
# BLOQUE 5 -- Distribucion MFE/MAE                                            #
# --------------------------------------------------------------------------- #

def block5_mfe_mae(trades):
    section("BLOQUE 5 -- Distribucion MFE / MAE")
    if not trades:
        no_data("sin trades cerrados")
        return

    mfe_r_vals = []
    mae_r_vals = []
    for t in trades:
        result = mfe_mae_in_r(
            t.get("mfe", 0.0),
            t.get("mae", 0.0),
            t.get("entry_price", 0.0),
            t.get("stop_price"),
        )
        if result is not None:
            mfe_r, mae_r = result
            mfe_r_vals.append(mfe_r)
            mae_r_vals.append(mae_r)

    def fmt_dist(vals, label):
        if not vals:
            print("  {}: sin datos".format(label))
            return
        print("  {}:".format(label))
        print("    media: {:.2f}R   mediana: {:.2f}R   min: {:.2f}R   max: {:.2f}R   N={}".format(
            mean(vals), median(vals), min(vals), max(vals), len(vals)))

    fmt_dist(mfe_r_vals, "MFE (excursion favorable, R-multiples)")
    fmt_dist(mae_r_vals, "MAE (excursion adversa,   R-multiples)")

    if mfe_r_vals and mae_r_vals and mean(mae_r_vals) > 0:
        ratio = mean(mfe_r_vals) / mean(mae_r_vals)
        print("\n  MFE/MAE promedio: {:.2f}".format(ratio))
        print("  Nota: MFE/MAE > 1 => el setup llega mas lejos a favor que en contra.")
        print("        MFE mediano alto + MAE pequeno => candidato a trailing stop en D2.")


# --------------------------------------------------------------------------- #
# BLOQUE 6 -- Contradicciones entre detectores                                #
# --------------------------------------------------------------------------- #

def block6_contradictions(contradictions):
    section("BLOQUE 6 -- Contradicciones entre detectores")
    total = len(contradictions)
    if total == 0:
        print("  0 contradicciones registradas.")
        print("  (Esperado con MAX_CONCURRENT_POSITIONS=1 y datos iniciales escasos.)")
        return

    print("  Total de ContradictionEvents: {}".format(total))

    by_symbol = defaultdict(int)
    pairs = defaultdict(int)
    for c in contradictions:
        by_symbol[c.get("symbol", "?")] += 1
        a = c.get("existing_strategy", "?")
        b = c.get("incoming_strategy", "?")
        key = tuple(sorted([a, b]))
        pairs[key] += 1

    print("\n  Por simbolo:")
    for sym, count in sorted(by_symbol.items(), key=lambda x: -x[1]):
        print("    {}: {}".format(sym, count))

    print("\n  Por par de detectores:")
    for (a, b), count in sorted(pairs.items(), key=lambda x: -x[1]):
        print("    {} <-> {}: {}".format(a, b, count))

    resolutions = defaultdict(int)
    for c in contradictions:
        resolutions[c.get("resolution", "?")] += 1
    print("\n  Resoluciones:")
    for res, count in sorted(resolutions.items(), key=lambda x: -x[1]):
        print("    {}: {}".format(res, count))


# --------------------------------------------------------------------------- #
# LEGACY -- strategy_outcomes.jsonl (tracker de excursion MFE/MAE)           #
# --------------------------------------------------------------------------- #

def block_legacy_outcomes(records):
    section("LEGACY -- strategy_outcomes.jsonl (tracker de excursion MFE/MAE)")
    if not records:
        no_data("sin datos en strategy_outcomes.jsonl")
        return

    by_det = defaultdict(list)
    for r in records:
        det = r.get("strategy") or r.get("detector") or "unknown"
        by_det[det].append(r)

    COL = 28
    print("  {:<{}} {:>5}  {:>8}  {:>7}  {:>7}  {:>8}".format(
        "Detector", COL, "N", "WinRate", "MFE_r", "MAE_r", "MFE/MAE"))
    print("  " + "-" * (COL + 45))

    all_records = []
    for det in sorted(by_det.keys()):
        group = by_det[det]
        all_records.extend(group)
        n = len(group)

        mfe_vals = [r.get("mfe_r", r.get("mfe")) for r in group]
        mae_vals = [r.get("mae_r", r.get("mae")) for r in group]
        mfe_vals = [v for v in mfe_vals if v is not None]
        mae_vals = [v for v in mae_vals if v is not None]

        wins = sum(
            1 for r in group
            if (r.get("mfe_r") or r.get("mfe", -1)) > (r.get("mae_r") or r.get("mae", 999))
        )
        wr = "{:.1%}".format(wins / n) if n else "n/a"
        m_mfe = "{:.3f}".format(mean(mfe_vals)) if mfe_vals else "  n/a"
        m_mae = "{:.3f}".format(mean(mae_vals)) if mae_vals else "  n/a"
        ratio = ("{:.2f}".format(mean(mfe_vals) / mean(mae_vals))
                 if mfe_vals and mae_vals and mean(mae_vals) > 0 else "  n/a")
        print("  {:<{}} {:>5}  {:>8}  {:>7}  {:>7}  {:>8}".format(
            det, COL, n, wr, m_mfe, m_mae, ratio))

    print("  " + "-" * (COL + 45))
    print("  {:<{}} {:>5}".format("TOTAL", COL, len(all_records)))


# =========================================================================== #
# D2 -- ANALISIS CONDICIONAL (Bloques 7-11)                                   #
# =========================================================================== #

def _safe_r(t):
    """R-multiple de un trade. None si faltan campos."""
    return r_multiple(
        t.get("net_pnl", 0.0),
        t.get("entry_price", 0.0),
        t.get("stop_price"),
        t.get("size", 0.0),
    )


def _small(n):
    """Marca de muestra chica (N < 5)."""
    return "  [muestra chica]" if n < 5 else ""


def _win_rate_r_row(group):
    """(n, win_rate, r_avg_or_None) para un grupo de trades."""
    n = len(group)
    wins = sum(1 for t in group if t.get("close_reason") == "TARGET_HIT")
    wr = wins / n if n else 0.0
    r_vals = [v for v in (_safe_r(t) for t in group) if v is not None]
    r_avg = mean(r_vals) if r_vals else None
    return n, wr, r_avg


# --------------------------------------------------------------------------- #
# BLOQUE 7 -- Detectores por regimen de mercado                               #
# --------------------------------------------------------------------------- #

def block7_regime(trades):
    section("BLOQUE 7 -- Detectores por regimen de mercado")
    if not trades:
        no_data("sin trades cerrados")
        return

    trades_with_regime = [t for t in trades if t.get("regime")]
    if not trades_with_regime:
        no_data("campo 'regime' no encontrado en ningún trade")
        return

    by_det = defaultdict(lambda: defaultdict(list))
    for t in trades_with_regime:
        det = t.get("strategy_id") or "unknown"
        regime = t.get("regime", "Unknown")
        by_det[det][regime].append(t)

    for det in sorted(by_det.keys()):
        print("\n  [Detector: {}]".format(det))
        print("  {:18} {:>5}  {:>9}  {:>12}".format("Regime", "N", "Win rate", "R-mult avg"))
        print("  " + "-" * 50)
        for regime in sorted(by_det[det].keys()):
            group = by_det[det][regime]
            n, wr, r_avg = _win_rate_r_row(group)
            r_str = "{:+.2f}".format(r_avg) if r_avg is not None else "   n/a"
            print("  {:18} {:>5}  {:>9.1%}  {:>12}{}".format(
                regime, n, wr, r_str, _small(n)))

    print("\n  Responde: que detectores funcionan en que regimen de mercado.")
    print("  [muestra chica] = N < 5 -- no concluir nada estadistico.")


# --------------------------------------------------------------------------- #
# BLOQUE 8 -- Discriminacion por score (D2: buckets finos + Pearson)         #
# --------------------------------------------------------------------------- #

def block8_score_discrimination(trades):
    section("BLOQUE 8 -- Discriminacion por score (analisis condicional D2)")
    if not trades:
        no_data("sin trades cerrados")
        return

    bucket_size = 0.05
    # Buckets desde 0.60 (umbral minimo de ejecucion asumido)
    edges = [i / 100 for i in range(60, 100, 5)]

    bucket_trades = defaultdict(list)
    score_pnl_pairs = []

    for t in trades:
        score = t.get("score")
        if not isinstance(score, (int, float)):
            continue
        lo = round(int(score / bucket_size) * bucket_size, 2)
        bucket_trades[lo].append(t)
        pnl_pct = t.get("net_pnl_pct")
        if isinstance(pnl_pct, (int, float)):
            score_pnl_pairs.append((score, pnl_pct))

    filled = {lo: g for lo, g in bucket_trades.items() if lo in edges}
    if not filled:
        no_data("trades sin campo 'score' o todos fuera del rango 0.60-1.00")
        return

    print("  {:15} {:>4}  {:>9}  {:>11}".format("Score bucket", "N", "Win rate", "R-mult avg"))
    print("  " + "-" * 45)
    for lo in edges:
        group = bucket_trades.get(lo, [])
        if not group:
            continue
        n, wr, r_avg = _win_rate_r_row(group)
        r_str = "{:+.2f}".format(r_avg) if r_avg is not None else "    n/a"
        print("  [{:.2f} - {:.2f})  {:>4}  {:>9.1%}  {:>11}{}".format(
            lo, lo + bucket_size, n, wr, r_str, _small(n)))

    # Pearson r (score vs net_pnl_pct) si hay suficientes datos
    if len(score_pnl_pairs) >= 30:
        xs = [p[0] for p in score_pnl_pairs]
        ys = [p[1] for p in score_pnl_pairs]
        mx, my = mean(xs), mean(ys)
        num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
        r = num / den if den > 1e-10 else 0.0
        print("\n  Correlacion de Pearson (score vs net_pnl_pct, N={}): r = {:.3f}".format(
            len(score_pnl_pairs), r))
        if r >= 0.3:
            print("  -> Score alto predice mejor resultado (correlacion positiva moderada/fuerte).")
        elif r > 0.05:
            print("  -> Correlacion debil positiva -- score discrimina poco.")
        elif r >= -0.05:
            print("  -> Correlacion casi nula -- el score no discrimina.")
        else:
            print("  -> Correlacion negativa -- score alto NO predice mejor resultado.")
    else:
        print("\n  Pearson omitido (requiere >=30 trades con net_pnl_pct; disponibles: {}).".format(
            len(score_pnl_pairs)))


# --------------------------------------------------------------------------- #
# BLOQUE 9 -- Condiciones de microestructura                                  #
# --------------------------------------------------------------------------- #

def block9_microstructure(trades):
    section("BLOQUE 9 -- Condiciones de microestructura")

    # --- 9A: VPIN ---
    print("\n  9A) VPIN -- toxicidad del flujo en el momento de la senal")
    vpin_trades = [t for t in trades if isinstance(t.get("vpin"), (int, float))]
    if not vpin_trades:
        no_data("campo 'vpin' no encontrado en trades")
    else:
        vpin_bands = [
            ("Limpio  (VPIN < 0.30)",    lambda v: v < 0.30),
            ("Neutro  (0.30 - 0.75)",    lambda v: 0.30 <= v <= 0.75),
            ("Toxico  (VPIN > 0.75)",    lambda v: v > 0.75),
        ]
        print("  {:35} {:>4}  {:>9}  {:>11}".format("Banda VPIN", "N", "Win rate", "R-mult avg"))
        print("  " + "-" * 64)
        for label, pred in vpin_bands:
            group = [t for t in vpin_trades if pred(t["vpin"])]
            n = len(group)
            if n == 0:
                print("  {:35} {:>4}".format(label, 0))
                continue
            _, wr, r_avg = _win_rate_r_row(group)
            r_str = "{:+.2f}".format(r_avg) if r_avg is not None else "    n/a"
            extra = _small(n)
            if "Toxico" in label and n > 0:
                extra += "  <- gate fallo, revisar"
            print("  {:35} {:>4}  {:>9.1%}  {:>11}{}".format(label, n, wr, r_str, extra))

    # --- 9B: Spread ---
    print("\n  9B) Spread -- costo de ejecucion en el momento de la senal")
    spread_trades = [t for t in trades if isinstance(t.get("spread_bps"), (int, float))]
    if not spread_trades:
        no_data("campo 'spread_bps' no encontrado en trades")
    else:
        spread_bands = [
            ("Tight  (< 1.0 bps)",       lambda s: s < 1.0),
            ("Medio  (1.0 - 1.5 bps)",   lambda s: 1.0 <= s <= 1.5),
            ("Wide   (> 1.5 bps)",        lambda s: s > 1.5),
        ]
        print("  {:28} {:>4}  {:>9}  {:>11}".format("Spread", "N", "Win rate", "R-mult avg"))
        print("  " + "-" * 57)
        for label, pred in spread_bands:
            group = [t for t in spread_trades if pred(t["spread_bps"])]
            n = len(group)
            if n == 0:
                print("  {:28} {:>4}".format(label, 0))
                continue
            _, wr, r_avg = _win_rate_r_row(group)
            r_str = "{:+.2f}".format(r_avg) if r_avg is not None else "    n/a"
            print("  {:28} {:>4}  {:>9.1%}  {:>11}{}".format(label, n, wr, r_str, _small(n)))

    # --- 9C: Delta alineado ---
    print("\n  9C) Alineacion de delta con la direccion de la senal")
    delta_trades = [
        t for t in trades
        if isinstance(t.get("delta"), (int, float)) and isinstance(t.get("side"), str)
    ]
    if not delta_trades:
        no_data("campo 'delta' no encontrado en trades")
    else:
        aligned, not_aligned = [], []
        for t in delta_trades:
            delta = t["delta"]
            side = t["side"].lower()
            if (side == "long" and delta > 0) or (side == "short" and delta < 0):
                aligned.append(t)
            else:
                not_aligned.append(t)

        print("  {:22} {:>4}  {:>9}  {:>11}".format("Alineacion", "N", "Win rate", "R-mult avg"))
        print("  " + "-" * 51)
        for label, group in [("Alineado (delta ok)", aligned),
                              ("No alineado",         not_aligned)]:
            n = len(group)
            if n == 0:
                print("  {:22} {:>4}".format(label, 0))
                continue
            _, wr, r_avg = _win_rate_r_row(group)
            r_str = "{:+.2f}".format(r_avg) if r_avg is not None else "    n/a"
            print("  {:22} {:>4}  {:>9.1%}  {:>11}{}".format(label, n, wr, r_str, _small(n)))

        print()
        print("  Alineado: delta>0 para Long, delta<0 para Short en el momento de la senal.")


# --------------------------------------------------------------------------- #
# BLOQUE 10 -- Interpretacion de razon de cierre por detector                 #
# --------------------------------------------------------------------------- #

def block10_close_reason_interpretation(trades):
    section("BLOQUE 10 -- Interpretacion de razon de cierre por detector")
    if not trades:
        no_data("sin trades cerrados")
        return

    by_det = defaultdict(list)
    for t in trades:
        det = t.get("strategy_id") or "unknown"
        by_det[det].append(t)

    for det in sorted(by_det.keys()):
        group = by_det[det]
        n = len(group)
        by_reason = defaultdict(int)
        for t in group:
            by_reason[t.get("close_reason", "?")] += 1

        target = by_reason["TARGET_HIT"]
        stop   = by_reason["STOP_HIT"]
        ttl    = by_reason["TTL_EXPIRED"]
        other  = n - target - stop - ttl

        target_pct = target / n if n else 0.0
        stop_pct   = stop   / n if n else 0.0
        ttl_pct    = ttl    / n if n else 0.0

        print("\n  {}  [N={}  TARGET={} ({:.0%})  STOP={} ({:.0%})  TTL={} ({:.0%})]{}".format(
            det, n,
            target, target_pct,
            stop,   stop_pct,
            ttl,    ttl_pct,
            "  [muestra chica]" if n < 5 else ""))

        if ttl_pct > 0.50:
            print("  [!] Mayoria de trades expiran sin resolver (TTL {:.0%}).".format(ttl_pct))
            print("      -> Targets probablemente muy ambiciosos o senales prematuras.")
            print("         Considerar reducir target o agregar TTL mas corto.")
        elif stop_pct > 0.60:
            print("  [!] Mayoria cierra en stop (STOP {:.0%}).".format(stop_pct))
            print("      -> Posible problema de deteccion o stops demasiado ajustados.")
            print("         Revisar contexto de regimen y condicion de entrada.")
        elif 0.38 <= target_pct <= 0.65 and stop_pct <= 0.50:
            print("  [ok] Distribucion de cierres balanceada.")
        else:
            print("  [~] Patron mixto -- revisar manualmente si el N es suficiente.")

        if other > 0:
            print("     Nota: {} trades con close_reason desconocida.".format(other))


# --------------------------------------------------------------------------- #
# BLOQUE 11 -- MFE/MAE vs distancia target/stop por detector                 #
# --------------------------------------------------------------------------- #

def block11_mfe_mae_by_detector(trades):
    section("BLOQUE 11 -- MFE/MAE vs distancia target/stop por detector")
    if not trades:
        no_data("sin trades cerrados")
        return

    by_det = defaultdict(list)
    for t in trades:
        det = t.get("strategy_id") or "unknown"
        by_det[det].append(t)

    COL = 24
    print("  {:<{}} {:>4}  {:>7}  {:>7}  {:>9}  {:>14}".format(
        "Detector", COL, "N", "MFE-R", "MAE-R", "Target-R", "MFE alcanza?"))
    print("  " + "-" * (COL + 50))

    all_notes = []

    for det in sorted(by_det.keys()):
        group = by_det[det]
        n = len(group)

        mfe_r_vals, mae_r_vals, target_r_vals = [], [], []

        for t in group:
            entry  = t.get("entry_price")
            stop   = t.get("stop_price")
            target = t.get("target_price")
            mfe    = t.get("mfe")
            mae    = t.get("mae")

            if not isinstance(entry, (int, float)) or not isinstance(stop, (int, float)):
                continue
            risk_unit = abs(entry - stop)
            if risk_unit < 1e-10:
                continue

            if isinstance(mfe, (int, float)):
                mfe_r_vals.append(mfe / risk_unit)
            if isinstance(mae, (int, float)):
                # MAE puede ser negativo (excursion adversa); tomamos magnitud
                mae_r_vals.append(abs(mae) / risk_unit)
            if isinstance(target, (int, float)):
                target_r_vals.append(abs(target - entry) / risk_unit)

        mfe_avg    = mean(mfe_r_vals)    if mfe_r_vals    else None
        mae_avg    = mean(mae_r_vals)    if mae_r_vals    else None
        target_avg = mean(target_r_vals) if target_r_vals else None

        mfe_str = "{:.2f}R".format(mfe_avg)    if mfe_avg    is not None else "   n/a"
        mae_str = "{:.2f}R".format(mae_avg)    if mae_avg    is not None else "   n/a"
        tgt_str = "{:.2f}R".format(target_avg) if target_avg is not None else "   n/a"

        if mfe_avg is not None and target_avg is not None and target_avg > 1e-10:
            reach_pct = mfe_avg / target_avg * 100
            reach_str = "Si  (~{:.0f}%)".format(reach_pct) if reach_pct >= 90 else \
                        "No  (~{:.0f}%)".format(reach_pct)
            if reach_pct >= 80 and reach_pct < 90:
                all_notes.append((det, reach_pct, mfe_avg, target_avg))
        else:
            reach_str = "n/a"

        print("  {:<{}} {:>4}  {:>7}  {:>7}  {:>9}  {:>14}{}".format(
            det, COL, n, mfe_str, mae_str, tgt_str, reach_str, _small(n)))

    print()
    print("  MFE-R    : excursion maxima favorable en R-multiples.")
    print("  MAE-R    : excursion maxima adversa en R-multiples (magnitud).")
    print("  Target-R : distancia del target desde entry en R-multiples.")
    print("  Stop-R   : siempre 1.00R por definicion.")
    print("  MFE alcanza target? : Si >= 90% del camino al target, No si menos.")

    if all_notes:
        print()
        for det, pct, mfe_avg, tgt_avg in all_notes:
            print("  ~ {}: MFE llega al {:.0f}% del target ({:.2f}R de {:.2f}R).".format(
                det, pct, mfe_avg, tgt_avg))
            print("    Candidato a trailing stop o reducir target ligeramente.")


# =========================================================================== #
# Datos sinteticos para verificacion (--test)                                 #
# =========================================================================== #

def generate_synthetic_trades(n=20, seed=42):
    """Genera trades sinteticos con variedad de detectores, regimenes y outcomes."""
    random.seed(seed)
    detectors = ["LvnBreakout", "VwapPullback", "ValueAreaFailedAuction"]
    regimes   = ["TrendUp", "Expansion", "Chop", "TrendDown"]
    reasons   = ["TARGET_HIT", "STOP_HIT", "TTL_EXPIRED"]
    # Pesos: TrendUp mas TARGET, Chop mas STOP/TTL
    reason_weights = {
        "TrendUp":   [0.60, 0.25, 0.15],
        "Expansion": [0.50, 0.30, 0.20],
        "Chop":      [0.25, 0.45, 0.30],
        "TrendDown": [0.30, 0.50, 0.20],
    }

    trades = []
    for i in range(n):
        det    = random.choice(detectors)
        regime = random.choice(regimes)
        score  = round(random.uniform(0.60, 0.95), 3)
        vpin   = round(random.uniform(0.10, 0.78), 3)
        spread = round(random.uniform(0.4, 2.2), 2)
        side   = random.choice(["Long", "Short"])
        delta  = round(random.uniform(-500, 500), 1)

        entry  = 50000.0
        stop   = entry - 400 if side == "Long" else entry + 400
        target = entry + 800 if side == "Long" else entry - 800

        weights = reason_weights[regime]
        reason  = random.choices(reasons, weights=weights)[0]

        risk_unit = abs(entry - stop)   # 400
        size      = 0.01

        if reason == "TARGET_HIT":
            net_pnl  = (target - entry) * size if side == "Long" else (entry - target) * size
            net_pnl -= round(random.uniform(0.3, 0.8), 2)   # fees
            mfe      = abs(target - entry) * random.uniform(1.0, 1.1)
            mae      = abs(entry - stop)  * random.uniform(0.1, 0.4)
        elif reason == "STOP_HIT":
            net_pnl  = (stop - entry) * size if side == "Long" else (entry - stop) * size
            net_pnl -= round(random.uniform(0.1, 0.4), 2)
            mfe      = abs(target - entry) * random.uniform(0.1, 0.5)
            mae      = abs(entry - stop)  * random.uniform(0.7, 1.0)
        else:  # TTL_EXPIRED
            net_pnl  = round(random.uniform(-2.5, 1.5), 2)
            mfe      = abs(target - entry) * random.uniform(0.2, 0.7)
            mae      = abs(entry - stop)  * random.uniform(0.2, 0.6)

        net_pnl_pct = net_pnl / INITIAL_CAPITAL * 100

        trades.append({
            "strategy_id":    det,
            "regime":         regime,
            "score":          score,
            "vpin":           vpin,
            "spread_bps":     spread,
            "side":           side,
            "delta":          delta,
            "close_reason":   reason,
            "entry_price":    entry,
            "stop_price":     stop,
            "target_price":   target,
            "size":           size,
            "net_pnl":        round(net_pnl, 4),
            "net_pnl_pct":    round(net_pnl_pct, 6),
            "gross_pnl":      round(net_pnl + 0.5, 4),
            "fees_paid":      0.5,
            "funding_paid":   0.0,
            "intended_entry": entry + random.uniform(-1, 1),
            "mfe":            round(mfe, 2),
            "mae":            round(mae, 2),
            "closed_at_ms":   1_700_000_000_000 + i * 3_600_000,
        })
    return trades


def generate_synthetic_signals(trades):
    """Genera senales sinteticas consistentes con los trades."""
    signals = []
    for t in trades:
        signals.append({"score": t["score"], "strategy_id": t["strategy_id"]})
    # Agregar senales adicionales que no generaron trade
    for _ in range(10):
        signals.append({"score": round(random.uniform(0.55, 0.95), 3)})
    return signals


def run_test_mode():
    """Crea datos sinteticos en un directorio temporal y corre el analisis completo."""
    print("=" * 70)
    print("  MODO TEST -- datos sinteticos (N=20 trades, seed=42)")
    print("=" * 70)

    trades  = generate_synthetic_trades(n=20, seed=42)
    signals = generate_synthetic_signals(trades)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        with open(tmp / "paper_trades.jsonl", "w") as f:
            for t in trades:
                f.write(json.dumps(t) + "\n")

        with open(tmp / "strategy_signals.jsonl", "w") as f:
            for s in signals:
                f.write(json.dumps(s) + "\n")

        # Archivos opcionales vacios
        (tmp / "contradictions.jsonl").touch()
        (tmp / "strategy_outcomes.jsonl").touch()

        print("Directorio temporal: {}".format(tmp))
        print()
        print("  paper_trades.jsonl          {} registros".format(len(trades)))
        print("  strategy_signals.jsonl      {} registros".format(len(signals)))
        print("  contradictions.jsonl        vacio")
        print("  strategy_outcomes.jsonl     vacio")

        _run_all_blocks(tmp)


# --------------------------------------------------------------------------- #
# main                                                                         #
# --------------------------------------------------------------------------- #

def _run_all_blocks(shadow: Path):
    """Carga archivos y ejecuta todos los bloques D1 + D2."""
    def load(name):
        records, skipped = load_jsonl(shadow / name)
        if not records and not (shadow / name).exists():
            tag = "archivo no encontrado"
        elif not records:
            tag = "vacio"
        else:
            tag = "{} registros".format(len(records))
        if skipped:
            tag += "  ({} lineas malformadas skipeadas)".format(skipped)
        print("  {:<40} {}".format(name, tag))
        return records

    trades         = load("paper_trades.jsonl")
    contradictions = load("contradictions.jsonl")
    signals        = load("strategy_signals.jsonl")
    outcomes       = load("strategy_outcomes.jsonl")

    # D1
    block1_pnl(trades)
    block2_detectors(trades)
    block3_scoring(trades, signals)
    block4_costs(trades)
    block5_mfe_mae(trades)
    block6_contradictions(contradictions)
    block_legacy_outcomes(outcomes)

    # D2
    block7_regime(trades)
    block8_score_discrimination(trades)
    block9_microstructure(trades)
    block10_close_reason_interpretation(trades)
    block11_mfe_mae_by_detector(trades)

    print("\n" + "=" * 70)
    print()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        run_test_mode()
        return

    if len(sys.argv) > 1:
        shadow = Path(sys.argv[1])
    else:
        appdata = os.environ.get("APPDATA", "")
        shadow = Path(appdata) / "flowsurface" / "shadow_events"

    if not shadow.exists():
        print("Directorio no encontrado: {}".format(shadow))
        print("Ejecuta la app y genera algunas senales primero.")
        print("O usa: python analyze_outcomes.py --test")
        sys.exit(1)

    print("Directorio: {}".format(shadow))
    print()
    _run_all_blocks(shadow)


if __name__ == "__main__":
    main()
