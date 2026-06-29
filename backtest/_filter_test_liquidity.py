"""
_filter_test_liquidity.py — test sistemático de filtros para liquidity maker
============================================================================
Protocolo: featurelab.filter_verdict() → regla dura IS+OOS positivo en los 3 activos.
Un filtro PASA si mejora OOS avgR vs base en BTC+ETH+SOL con n_oos >= 12.

Ya probados y cerrados (no repetir):
  - frescura del POC       → no generaliza
  - confluencia >= 2       → no generaliza
  - OBI / CVD / VPIN       → 0 impacto

FASE 1 — Tiempo (solo ts_ms, sin datos extra)
  F1a: excluir sesión Asian (solo 07-21 UTC)
  F1b: excluir fin de semana
  F1c: excluir lunes
  F1d: solo London-NY overlap (12-21 UTC)

FASE 2 — Dirección de llegada al nivel (solo close prices)
  F2a: precio bajando N=3 barras hacia long / subiendo hacia short
  F2b: ídem N=5 barras
  F2c: precio cerca del nivel (< 0.5 ATR de distancia)
  F2d: precio cerca del nivel (< 1.0 ATR)

FASE 3 — Footprint quality (columnas fp_absorb_*)
  F3a: absorción a favor en la barra de entrada
        long → fp_absorb_buy (compradores absorbieron vendedores)
        short → fp_absorb_sell (vendedores absorbieron compradores)
  F3b: fp_poc existe y está cerca del entry (nivel tiene POC tick real)

FASE 4 — Combos de los que pasen
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import featurelab as FL

# ─────────────────────────────────────────────────────────────────────────────
# FASE 1 — TIEMPO
# ─────────────────────────────────────────────────────────────────────────────

def f1a_no_asian(a, bar, side, entry):
    """Excluir sesión Asian 00-07 UTC. Solo London + NY (07-00)."""
    hour = (int(a.ts[bar]) // 3_600_000) % 24
    return hour >= 7

def f1b_no_weekend(a, bar, side, entry):
    """Excluir sábado y domingo (crypto weekends = thin + erratic)."""
    weekday = (int(a.ts[bar]) // 86_400_000 + 3) % 7   # 0=Mon … 6=Sun
    return weekday < 5

def f1c_no_monday(a, bar, side, entry):
    """Excluir lunes (overnight gap + rebalanceo institucional)."""
    weekday = (int(a.ts[bar]) // 86_400_000 + 3) % 7
    return weekday != 0

def f1d_london_ny_overlap(a, bar, side, entry):
    """Solo London-NY overlap: 12-21 UTC (máxima liquidez, menor manipulación)."""
    hour = (int(a.ts[bar]) // 3_600_000) % 24
    return 12 <= hour < 21

# ─────────────────────────────────────────────────────────────────────────────
# FASE 2 — DIRECCIÓN DE LLEGADA
# ─────────────────────────────────────────────────────────────────────────────

def f2a_approach_3(a, bar, side, entry):
    """Precio llegando desde el lado correcto — ventana 3 barras (45 min en M15)."""
    if bar < 3: return True
    slope = float(a.c[bar]) - float(a.c[bar - 3])
    return slope < 0 if side == "long" else slope > 0

def f2b_approach_5(a, bar, side, entry):
    """Ídem ventana 5 barras (75 min)."""
    if bar < 5: return True
    slope = float(a.c[bar]) - float(a.c[bar - 5])
    return slope < 0 if side == "long" else slope > 0

def f2c_dist_half_atr(a, bar, side, entry):
    """Precio a menos de 0.5 ATR del nivel (entrada en zona activa, no rebote lejano)."""
    dist = abs(float(a.c[bar]) - entry)
    return dist < 0.5 * float(a.atr[bar])

def f2d_dist_one_atr(a, bar, side, entry):
    """Versión permisiva: precio a menos de 1.0 ATR del nivel."""
    dist = abs(float(a.c[bar]) - entry)
    return dist < 1.0 * float(a.atr[bar])

def f2e_far_half_atr(a, bar, side, entry):
    """INVERSO de F2c: precio LEJOS del nivel (> 0.5 ATR).
    La intuición: cuando la barra de señal cierra lejos del nivel, el precio
    aún tiene que llegar limpio al nivel → rebote limpio cuando lo alcanza.
    Cuando cierra cerca, el nivel ya fue testeado en esa barra → más ruido."""
    dist = abs(float(a.c[bar]) - entry)
    return dist >= 0.5 * float(a.atr[bar])

def f2f_far_one_atr(a, bar, side, entry):
    """INVERSO de F2d: precio a más de 1.0 ATR del nivel (entradas más tempranas)."""
    dist = abs(float(a.c[bar]) - entry)
    return dist >= 1.0 * float(a.atr[bar])

# ─────────────────────────────────────────────────────────────────────────────
# FASE 3 — FOOTPRINT QUALITY
# ─────────────────────────────────────────────────────────────────────────────

def f3a_absorb(a, bar, side, entry):
    """Absorción confirmada en la barra de entrada.
    long  → fp_absorb_buy  (compradores absorbieron agresión vendedora)
    short → fp_absorb_sell (vendedores absorbieron agresión compradora)
    Si la columna no existe, dejar pasar (no romper)."""
    if side == "long":
        col = getattr(a, "fp_absorb_buy", None)
    else:
        col = getattr(a, "fp_absorb_sell", None)
    if col is None: return True
    return bool(col[bar])

def f3b_fp_poc_near(a, bar, side, entry, tol=0.001):
    """fp_poc (footprint tick real) existe y está cerca del entry (±0.1%).
    Si no hay fp_poc, dejar pasar."""
    fpc = getattr(a, "fp_poc", None)
    if fpc is None: return True
    v = float(fpc[bar])
    if not np.isfinite(v): return True    # sin footprint: no filtrar
    return abs(v - entry) / entry <= tol

# ─────────────────────────────────────────────────────────────────────────────
# COMBOS (se arman después de ver qué pasa)
# ─────────────────────────────────────────────────────────────────────────────

def make_combo(*filts):
    """Combina N filtros con AND."""
    def f(a, bar, side, entry):
        return all(fn(a, bar, side, entry) for fn in filts)
    return f

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    results = {}

    print("\n" + "="*72)
    print("FASE 1 — TIEMPO")
    print("="*72)
    results["F1a no-Asian (07-00 UTC)"]       = FL.filter_verdict("F1a no-Asian (07-00 UTC)",       f1a_no_asian)
    results["F1b no-weekend"]                  = FL.filter_verdict("F1b no-weekend",                  f1b_no_weekend)
    results["F1c no-lunes"]                    = FL.filter_verdict("F1c no-lunes",                    f1c_no_monday)
    results["F1d London-NY overlap (12-21)"]   = FL.filter_verdict("F1d London-NY overlap (12-21)",   f1d_london_ny_overlap)

    print("\n" + "="*72)
    print("FASE 2 — DIRECCIÓN DE LLEGADA")
    print("="*72)
    results["F2a approach N=3 barras"]        = FL.filter_verdict("F2a approach N=3 barras",        f2a_approach_3)
    results["F2b approach N=5 barras"]        = FL.filter_verdict("F2b approach N=5 barras",        f2b_approach_5)
    results["F2c dist < 0.5 ATR"]             = FL.filter_verdict("F2c dist < 0.5 ATR",             f2c_dist_half_atr)
    results["F2d dist < 1.0 ATR"]             = FL.filter_verdict("F2d dist < 1.0 ATR",             f2d_dist_one_atr)
    results["F2e dist > 0.5 ATR (inverso)"]   = FL.filter_verdict("F2e dist > 0.5 ATR (inverso)",   f2e_far_half_atr)
    results["F2f dist > 1.0 ATR (inverso)"]   = FL.filter_verdict("F2f dist > 1.0 ATR (inverso)",   f2f_far_one_atr)

    print("\n" + "="*72)
    print("FASE 3 — FOOTPRINT QUALITY")
    print("="*72)
    results["F3a absorb confirm"]             = FL.filter_verdict("F3a absorb confirm",             f3a_absorb)
    results["F3b fp_poc cerca entry (0.1%)"]  = FL.filter_verdict("F3b fp_poc cerca entry (0.1%)",  f3b_fp_poc_near)

    # ── Resumen ────────────────────────────────────────────────────────────────
    pasaron = [k for k, v in results.items() if v]
    print("\n" + "="*72)
    print("RESUMEN")
    print("="*72)
    for k, v in results.items():
        print(f"  {'✅' if v else '❌'}  {k}")

    print(f"\nPasaron regla dura: {len(pasaron)}/{len(results)}")

    # ── Combos con los que pasaron ─────────────────────────────────────────────
    if len(pasaron) >= 2:
        print("\n" + "="*72)
        print("FASE 4 — COMBOS DE FILTROS QUE PASARON")
        print("="*72)
        filt_map = {
            "F1a no-Asian (07-00 UTC)":      f1a_no_asian,
            "F1b no-weekend":                 f1b_no_weekend,
            "F1c no-lunes":                   f1c_no_monday,
            "F1d London-NY overlap (12-21)":  f1d_london_ny_overlap,
            "F2a approach N=3 barras":        f2a_approach_3,
            "F2b approach N=5 barras":        f2b_approach_5,
            "F2c dist < 0.5 ATR":             f2c_dist_half_atr,
            "F2d dist < 1.0 ATR":             f2d_dist_one_atr,
            "F2e dist > 0.5 ATR (inverso)":   f2e_far_half_atr,
            "F2f dist > 1.0 ATR (inverso)":   f2f_far_one_atr,
            "F3a absorb confirm":             f3a_absorb,
            "F3b fp_poc cerca entry (0.1%)":  f3b_fp_poc_near,
        }
        if len(pasaron) >= 2:
            # Combo de todos los que pasaron
            combo_filts = [filt_map[k] for k in pasaron]
            combo_name = " + ".join(pasaron)
            FL.filter_verdict(f"COMBO: {combo_name}", make_combo(*combo_filts))

        # Si hay >= 3, también probar pares de los más prometedores
        if len(pasaron) >= 3:
            for i in range(len(pasaron)):
                for j in range(i+1, len(pasaron)):
                    ka, kb = pasaron[i], pasaron[j]
                    FL.filter_verdict(f"COMBO par: {ka} + {kb}",
                                      make_combo(filt_map[ka], filt_map[kb]))
