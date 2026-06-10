#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
"""
FASE 0: Bootstrap confidence intervals para señales RBF.

Separa trades "limpios" (datos reales) de "dudosos" (batch-patched o bugs),
calcula CI 95% por sesión, dirección y score para saber qué conclusiones
son estadísticamente sólidas antes de tocar parámetros.

Uso:
    python scripts/rbf_bootstrap_analysis.py
    python scripts/rbf_bootstrap_analysis.py --all    # incluye trades dudosos
    python scripts/rbf_bootstrap_analysis.py --flag   # escribe quality_flag a Supabase
"""

import os
import sys
import json
import random
import urllib.request
import urllib.parse
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ztdhvmcisjjyhbqlgkzm.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# ── Supabase helpers ──────────────────────────────────────────────────────────

def fetch_all(table, params):
    rows = []
    limit = 1000
    offset = 0
    while True:
        p = dict(params)
        p["limit"] = str(limit)
        p["offset"] = str(offset)
        qs = urllib.parse.urlencode(p)
        url = f"{SUPABASE_URL}/rest/v1/{table}?{qs}"
        req = urllib.request.Request(url, headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
        })
        with urllib.request.urlopen(req) as r:
            chunk = json.loads(r.read())
        rows.extend(chunk)
        if len(chunk) < limit:
            break
        offset += limit
    return rows

# ── Data quality classification ───────────────────────────────────────────────

BATCH_PATCH_TIMESTAMP = "2026-06-04T17:"  # todos los #1-12 tienen este prefijo
SESSION_END_BUG_EXPECTED_R = -1.0         # trades #32-33 deberían ser -1R

def classify_trade(t, idx_1based):
    """
    Devuelve (quality, reason):
      'clean'   — datos reales
      'dubious' — batch-patched o bug conocido
    """
    closed_at = t.get("closed_at") or ""
    score = t.get("confluence_score")
    result_r = t.get("result_r")

    # Trades 1-12: score=None + closed_at batch-patched
    if score is None and BATCH_PATCH_TIMESTAMP in closed_at:
        return "dubious", "batch_patched_no_score"

    # Trades 32-33: SESSION_END bug — resultado peor que -1R con exit_reason=SESSION_END
    exit_reason = t.get("exit_reason") or ""
    if (exit_reason == "SESSION_END"
            and result_r is not None
            and result_r < -1.1):
        return "dubious", f"session_end_bug_r={result_r:.2f}"

    return "clean", ""

# ── Bootstrap engine ──────────────────────────────────────────────────────────

def bootstrap_metrics(result_r_list, n_boot=5000, ci=0.95):
    """
    Calcula WR y avg_R con bootstrap CI.
    Devuelve dict con point estimates e intervalos.
    """
    n = len(result_r_list)
    if n == 0:
        return None

    wins = [1 if r > 0 else 0 for r in result_r_list]
    wr_point = sum(wins) / n
    avg_r_point = sum(result_r_list) / n
    total_r_point = sum(result_r_list)

    # Bootstrap
    boot_wr = []
    boot_avg = []
    for _ in range(n_boot):
        sample = random.choices(result_r_list, k=n)
        boot_wr.append(sum(1 for r in sample if r > 0) / n)
        boot_avg.append(sum(sample) / n)

    alpha = (1 - ci) / 2
    lo_idx = int(alpha * n_boot)
    hi_idx = int((1 - alpha) * n_boot)

    boot_wr.sort()
    boot_avg.sort()

    wr_lo, wr_hi = boot_wr[lo_idx], boot_wr[hi_idx]
    avg_lo, avg_hi = boot_avg[lo_idx], boot_avg[hi_idx]

    # Señal de incertidumbre: CI cruza break-even
    wr_uncertain = wr_lo < 0.5 < wr_hi
    avg_uncertain = avg_lo < 0.0 < avg_hi

    return {
        "n": n,
        "wr": wr_point,
        "wr_ci": (wr_lo, wr_hi),
        "wr_uncertain": wr_uncertain,
        "avg_r": avg_r_point,
        "avg_r_ci": (avg_lo, avg_hi),
        "avg_r_uncertain": avg_uncertain,
        "total_r": total_r_point,
    }

def confidence_tag(m):
    """★★★ / ★★☆ / ★☆☆ basado en n y si CI cruza 0"""
    if m is None:
        return "—"
    n = m["n"]
    uncertain = m["wr_uncertain"] or m["avg_r_uncertain"]
    if n >= 30 and not uncertain:
        return "★★★"
    if n >= 15:
        return "★★☆" if not uncertain else "★☆☆"
    return "★☆☆"

# ── Report printing ───────────────────────────────────────────────────────────

def fmt_wr(m):
    if m is None:
        return "—"
    lo, hi = m["wr_ci"]
    flag = " ⚠" if m["wr_uncertain"] else ""
    return f"{m['wr']:.0%} [{lo:.0%}–{hi:.0%}]{flag}"

def fmt_avgr(m):
    if m is None:
        return "—"
    lo, hi = m["avg_r_ci"]
    flag = " ⚠" if m["avg_r_uncertain"] else ""
    return f"{m['avg_r']:+.2f}R [{lo:+.2f}–{hi:+.2f}]{flag}"

def print_table(title, groups, trades_map):
    print(f"\n{'='*66}")
    print(f"  {title}")
    print(f"{'='*66}")
    header = f"{'Grupo':<22} {'n':>4}  {'WR (95% CI)':<28}  {'avg_R (95% CI)':<26}  conf"
    print(header)
    print("-"*66)
    for g in groups:
        rs = [t["result_r"] for t in trades_map.get(g, []) if t.get("result_r") is not None]
        if not rs:
            continue
        m = bootstrap_metrics(rs)
        tag = confidence_tag(m)
        total = f"  [{m['total_r']:+.2f}R total]"
        print(f"{g:<22} {m['n']:>4}  {fmt_wr(m):<28}  {fmt_avgr(m):<26}  {tag}{total}")

def print_quality_report(all_trades):
    clean = [t for t in all_trades if t["_quality"] == "clean"]
    dubious = [t for t in all_trades if t["_quality"] == "dubious"]

    print("\n" + "="*66)
    print("  CALIDAD DE DATOS")
    print("="*66)
    print(f"  Total señales cerradas: {len(all_trades)}")
    print(f"  ✅ Limpias (análisis principal): {len(clean)}")
    print(f"  ⚠️  Dudosas (excluidas):         {len(dubious)}")
    if dubious:
        print("\n  Trades excluidos:")
        for t in dubious:
            sid = str(t.get('id','?'))[:8]
            print(f"    [{sid}] {t.get('direction','?')} {t.get('session','?')} "
                  f"r={t.get('result_r','?')} — {t['_reason']}")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    include_all = "--all" in sys.argv
    flag_mode   = "--flag" in sys.argv

    if not SUPABASE_KEY:
        print("ERROR: SUPABASE_KEY no está en env vars")
        sys.exit(1)

    print("Fetching rbf_signals from Supabase...")
    rows = fetch_all("rbf_signals", {
        "select": "id,direction,session,result_r,confluence_score,confluence_flags,"
                  "exit_reason,closed_at,symbol,entry_price",
        "result_r": "not.is.null",
        "order": "created_at.asc",
    })
    print(f"  {len(rows)} señales con result_r")

    # Clasificar
    for i, t in enumerate(rows):
        q, reason = classify_trade(t, i + 1)
        t["_quality"] = q
        t["_reason"] = reason

    print_quality_report(rows)

    if include_all:
        clean = rows
        print("\n  ⚠️  Modo --all: usando todos los trades (incluyendo dudosos)")
    else:
        clean = [t for t in rows if t["_quality"] == "clean"]

    if not clean:
        print("\nSin datos limpios suficientes para análisis.")
        return

    # ── Análisis por sesión ────────────────────────────────────────────────
    sessions = sorted(set(t.get("session", "Unknown") for t in clean))
    session_map = {s: [t for t in clean if t.get("session") == s] for s in sessions}
    print_table("POR SESIÓN", sessions, session_map)

    # ── Análisis por dirección ─────────────────────────────────────────────
    dirs = ["Long", "Short"]
    dir_map = {d: [t for t in clean if t.get("direction") == d] for d in dirs}
    print_table("POR DIRECCIÓN", dirs, dir_map)

    # ── Sesión × Dirección ─────────────────────────────────────────────────
    combos = sorted(set(
        (t.get("session", "?"), t.get("direction", "?")) for t in clean
    ))
    combo_keys = [f"{s}/{d}" for s, d in combos]
    combo_map = {
        f"{s}/{d}": [t for t in clean if t.get("session") == s and t.get("direction") == d]
        for s, d in combos
    }
    print_table("SESIÓN × DIRECCIÓN", combo_keys, combo_map)

    # ── Análisis por confluence score ──────────────────────────────────────
    scores = sorted(set(t.get("confluence_score") for t in clean if t.get("confluence_score") is not None))
    score_keys = [str(s) for s in scores]
    score_map = {str(s): [t for t in clean if t.get("confluence_score") == s] for s in scores}
    print_table("POR CONFLUENCE SCORE", score_keys, score_map)

    # ── Score × Sesión (para ver si score 4 es problema universal) ─────────
    score_session_combos = sorted(set(
        (str(t.get("confluence_score", "?")), t.get("session", "?")) for t in clean
        if t.get("confluence_score") is not None
    ))
    ss_keys = [f"score{s}/{sess}" for s, sess in score_session_combos]
    ss_map = {
        f"score{s}/{sess}": [
            t for t in clean
            if str(t.get("confluence_score")) == s and t.get("session") == sess
        ]
        for s, sess in score_session_combos
    }
    # Solo mostrar si hay grupos con n≥3
    ss_keys_filtered = [k for k in ss_keys if len(ss_map[k]) >= 3]
    if ss_keys_filtered:
        print_table("SCORE × SESIÓN (n≥3)", ss_keys_filtered, ss_map)

    # ── Por símbolo ───────────────────────────────────────────────────────
    symbols = sorted(set(t.get("symbol", "?") for t in clean))
    sym_map = {s: [t for t in clean if t.get("symbol") == s] for s in symbols}
    print_table("POR SÍMBOLO", symbols, sym_map)

    # ── Resumen estadístico global ─────────────────────────────────────────
    all_rs = [t["result_r"] for t in clean if t.get("result_r") is not None]
    m_all = bootstrap_metrics(all_rs)
    print(f"\n{'='*66}")
    print(f"  TOTAL LIMPIO — n={m_all['n']}")
    print(f"{'='*66}")
    print(f"  WR:     {fmt_wr(m_all)}")
    print(f"  avg_R:  {fmt_avgr(m_all)}")
    print(f"  total:  {m_all['total_r']:+.2f}R")
    print(f"  conf:   {confidence_tag(m_all)}")

    # ── Interpretación ─────────────────────────────────────────────────────
    print(f"\n{'='*66}")
    print("  INTERPRETACIÓN DE CONFIANZA")
    print("="*66)
    print("  ★★★  n≥30 y CI no cruza break-even → conclusión sólida")
    print("  ★★☆  n≥15 → tendencia clara pero aún mejorable")
    print("  ★☆☆  n<15 o CI cruza 0 → ⚠️ NO tomar decisiones de parámetros")
    print()
    print("  ⚠  = IC 95% cruza 50% WR o 0R avg → resultado puede ser ruido")
    print()

    # Advertencias automáticas
    warnings = []
    for group_name, trades_list in {**session_map, **dir_map}.items():
        rs = [t["result_r"] for t in trades_list if t.get("result_r") is not None]
        if not rs:
            continue
        m = bootstrap_metrics(rs)
        if m and m["n"] < 15:
            warnings.append(f"  ⚠ {group_name}: n={m['n']} — demasiado pequeño para conclusiones ({fmt_wr(m)})")
        elif m and (m["wr_uncertain"] or m["avg_r_uncertain"]):
            warnings.append(f"  ⚠ {group_name}: n={m['n']} — CI cruza break-even → no ajustar parámetros aún")

    if warnings:
        print("  GRUPOS CON DATOS INSUFICIENTES (NO ajustar parámetros):")
        for w in warnings:
            print(w)
    else:
        print("  Todos los grupos tienen suficiente certeza estadística.")

    print()

if __name__ == "__main__":
    random.seed(42)
    main()
