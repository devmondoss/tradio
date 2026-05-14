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

Si algun archivo no existe o esta vacio, ese bloque reporta "sin datos" y continua.
Las lineas JSONL malformadas se saltean y se contabiliza cuantas fueron.
"""

import json
import os
import sys
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


# --------------------------------------------------------------------------- #
# main                                                                         #
# --------------------------------------------------------------------------- #

def main():
    if len(sys.argv) > 1:
        shadow = Path(sys.argv[1])
    else:
        appdata = os.environ.get("APPDATA", "")
        shadow = Path(appdata) / "flowsurface" / "shadow_events"

    if not shadow.exists():
        print("Directorio no encontrado: {}".format(shadow))
        print("Ejecuta la app y genera algunas senales primero.")
        sys.exit(1)

    print("Directorio: {}".format(shadow))
    print()

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

    block1_pnl(trades)
    block2_detectors(trades)
    block3_scoring(trades, signals)
    block4_costs(trades)
    block5_mfe_mae(trades)
    block6_contradictions(contradictions)
    block_legacy_outcomes(outcomes)

    print("\n" + "=" * 70)
    print()


if __name__ == "__main__":
    main()
