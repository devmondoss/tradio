"""
_audit_missed.py — Autopsia de los MOVIMIENTOS PERDIDOS
======================================================
Detecta cada movimiento grande y limpio (≥2%) en los 365d (zigzag sobre M15) y, por cada uno,
diagnostica con las VARIABLES de la estrategia qué pasó:
  CAUGHT   — la estrategia entró y corrió al target (capturó el movimiento)
  CRUMB    — entró pero salió temprano (parcial/breakeven/stop) → dejó el movimiento en la mesa
  NO_FILL  — había nivel/candidato pero el precio no tocó el límite (no se llenó)
  BLOCK_VOL— candidato bloqueado por el filtro de volatilidad (ATR<=mediana)
  BLOCK_RR — candidato con RR<1.2 o lado equivocado del mercado
  BLOCK_CAP— candidato válido pero ya se usó el cupo del día / cooldown (aprox)
  NO_SETUP — NINGÚN generador produjo candidato aquí → la estrategia estructuralmente no ve esto

Esto convierte "vi mejores movimientos que no tomó" en datos: por qué se escaparon.
"""
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).parent))
import _listas2 as L2
from _audit_edge import run_audit
from _audit_mirror import gen_h21_short

TF = 15
MOVE_PCT = 2.0   # umbral de "movimiento grande"


def zigzag(c, pct):
    """Pivots de swing: alterna high/low cuando el precio revierte >= pct% desde el último extremo."""
    piv = []; lp_i = 0; lp_px = c[0]; direction = 0
    for i in range(1, len(c)):
        ch = (c[i]-lp_px)/lp_px*100
        if direction >= 0 and ch <= -pct:
            piv.append((lp_i, lp_px, 'high')); direction = -1; lp_i = i; lp_px = c[i]
        elif direction <= 0 and ch >= pct:
            piv.append((lp_i, lp_px, 'low')); direction = 1; lp_i = i; lp_px = c[i]
        else:
            if direction >= 0 and c[i] > lp_px: lp_i = i; lp_px = c[i]
            if direction <= 0 and c[i] < lp_px: lp_i = i; lp_px = c[i]
    return piv


def main():
    t = L2.load2(TF, start_ms=L2.TICK_MS); a = L2.A2(t)
    m1 = L2.load_m1_exit(start_ms=L2.TICK_MS)
    gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
    atr_med = pd.Series(a.atr).rolling(500, min_periods=50).median().shift(1).values

    # --- movimientos grandes ---
    piv = zigzag(a.c, MOVE_PCT)
    moves = []
    for k in range(len(piv)-1):
        i0, p0, _ = piv[k]; i1, p1, _ = piv[k+1]
        size = abs(p1-p0)/p0*100
        if size < MOVE_PCT or i0 < 60: continue
        side = 'long' if p1 > p0 else 'short'   # subida = oportunidad long desde el pivot bajo
        moves.append(dict(start=i0, end=i1, side=side, size=size, dur_bars=i1-i0))
    print(f"Movimientos limpios >= {MOVE_PCT}% en {(a.ts.max()-a.ts.min())/86_400_000:.0f}d: {len(moves)}")
    print(f"  tamaño: med={np.median([m['size'] for m in moves]):.1f}%  máx={max(m['size'] for m in moves):.1f}%\n")

    # --- lo que la estrategia hizo de verdad ---
    trades = run_audit(a, gens, m1, TF, honest_fee=True)
    ts_to_i = {int(a.ts[i]): i for i in range(a.n)}
    trades['bar'] = trades.ts.map(ts_to_i)

    def candidate_gate(i, side):
        """¿Algún generador produce candidato en `side` en la barra i? Devuelve qué compuerta falla."""
        for g in gens:
            for cand in (g(a, i) or []):
                cs, lvl, stop, tp1, tp2, kind = cand
                if cs != side or not np.isfinite([lvl, stop, tp2]).all(): continue
                ref = a.c[i-1]; risk = abs(lvl-stop)
                maker_ok = (lvl < ref) if side == 'long' else (lvl > ref)
                filled = (a.l[i] <= lvl-2/1e4*lvl) if side == 'long' else (a.h[i] >= lvl+2/1e4*lvl)
                rr_ok = risk > 0 and abs(tp2-lvl)/risk >= 1.2
                vol_ok = bool(np.isfinite(atr_med[i]) and a.atr[i] > atr_med[i])
                return dict(vol=vol_ok, maker=maker_ok, filled=filled, rr=rr_ok)
        return None

    # --- clasificar cada movimiento ---
    cats = {}
    rows = []
    for mv in moves:
        s, e = mv['start'], mv['start']+6
        tr = trades[(trades.bar >= s-1) & (trades.bar <= e) & (trades.side == mv['side'])]
        if len(tr):
            cat = 'CAUGHT' if (tr.reason == 'target').any() else 'CRUMB'
        else:
            cat = 'NO_SETUP'
            for i in range(max(60, s-2), min(a.n-1, e+1)):
                g = candidate_gate(i, mv['side'])
                if g is None: continue
                cat = ('BLOCK_VOL' if not g['vol'] else 'NO_FILL' if not g['filled']
                       else 'BLOCK_RR' if (not g['maker'] or not g['rr']) else 'BLOCK_CAP')
                break
        cats[cat] = cats.get(cat, 0)+1
        rows.append({**mv, 'cat': cat})

    df = pd.DataFrame(rows)
    print("=== ¿Qué hizo la estrategia con cada movimiento grande? ===")
    order = ['CAUGHT', 'CRUMB', 'NO_FILL', 'BLOCK_VOL', 'BLOCK_RR', 'BLOCK_CAP', 'NO_SETUP']
    labels = {'CAUGHT': 'capturó (corrió al target)', 'CRUMB': 'entró y soltó temprano (migaja)',
              'NO_FILL': 'nivel ok pero no se llenó (no tocó)', 'BLOCK_VOL': 'bloqueó filtro volatilidad',
              'BLOCK_RR': 'bloqueó RR<1.2 / lado', 'BLOCK_CAP': 'cupo diario / cooldown',
              'NO_SETUP': 'NO había setup (no lo ve)'}
    for c in order:
        n = cats.get(c, 0)
        if n: print(f"  {labels[c]:<38} {n:>4}  ({100*n/len(moves):>3.0f}%)")

    print("\n=== Por tamaño de movimiento ===")
    for lo, hi in [(2, 3), (3, 5), (5, 100)]:
        sub = df[(df['size'] >= lo) & (df['size'] < hi)]
        if not len(sub): continue
        caught = 100*(sub.cat == 'CAUGHT').mean(); crumb = 100*(sub.cat == 'CRUMB').mean()
        missed = 100*sub.cat.isin(['NO_FILL', 'BLOCK_VOL', 'BLOCK_RR', 'BLOCK_CAP', 'NO_SETUP']).mean()
        print(f"  {lo}-{hi if hi<100 else '+'}%  n={len(sub):>3} | capturó {caught:>3.0f}% | migaja {crumb:>3.0f}% | perdió {missed:>3.0f}%")

    print("\n=== Lectura ===")
    nosetup = cats.get('NO_SETUP', 0); crumb = cats.get('CRUMB', 0); caught = cats.get('CAUGHT', 0)
    print(f"  De {len(moves)} movimientos grandes, capturó completos {caught} ({100*caught/len(moves):.0f}%).")
    print(f"  Entró y soltó temprano (migaja) en {crumb} ({100*crumb/len(moves):.0f}%) → dejó el movimiento en la mesa.")
    print(f"  No tenía setup en {nosetup} ({100*nosetup/len(moves):.0f}%) → estructuralmente no ve esos movimientos.")


if __name__ == "__main__":
    main()
