"""
_session_vp.py - Session Volume Profile: LVN, HVN, Naked POC, CVD reversal exit
=================================================================================
Builder (una sola vez, ~3-5 min):
    python backtest/_session_vp.py --build

Genera en data/bybit-perp/:
    _svp_dayvp.parquet     - POC/VAH/VAL + LVN/HVN por sesion (bins $10 desde ticks)
    _svp_naked_poc.parquet - Naked POC activos al inicio de cada dia

Uso desde el backtest:
    import _session_vp as SVP
    dayvp = SVP.load_dayvp()
    naked = SVP.load_naked_poc()
    m1cvd = SVP.load_m1_cvd()

    tgt, name = SVP.fp_target(dayvp, naked, day_int, entry, side)
    exit_now  = SVP.cvd_exit_check(m1cvd, j0, j, side)
"""
from pathlib import Path
import numpy as np, pandas as pd, glob, json, argparse

ROOT     = Path(__file__).parent.parent
TICK_DIR = ROOT / "data/bybit-perp/raw_trades"
DATA_DIR = ROOT / "data/bybit-perp"
M1_PATH  = ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet"

BIN       = 10.0   # $10 por nivel de VP
VA_PCT    = 0.70   # Value Area 70 %
LVN_PCT   = 25     # percentil -> LVN (Low Volume Node)
HVN_PCT   = 75     # percentil -> HVN (High Volume Node)
NAKED_TOL = 75.0   # +-$75 para "POC visitado"
LOOKBACK  = 15     # dias de lookback para Naked POC


# ── BUILDER ────────────────────────────────────────────────────────────────

def _session_vp(price, size):
    """VP de una sesion completa. Returns (poc, vah, val, lvn_list, hvn_list)."""
    lo   = np.floor(price.min() / BIN) * BIN
    idx  = ((price - lo) // BIN).astype(int)
    vols = np.bincount(idx, weights=size).astype(float)
    bins = lo + np.arange(len(vols)) * BIN

    if vols.sum() == 0:
        return np.nan, np.nan, np.nan, [], []

    poc = float(bins[vols.argmax()])

    order = np.argsort(vols)[::-1]; tot = vols.sum(); cum = 0.0; sel = []
    for k in order:
        sel.append(k); cum += vols[k]
        if cum >= VA_PCT * tot:
            break
    va = bins[sel]; vah, val = float(va.max()), float(va.min())

    nz_mask = vols > 0; nz_v = vols[nz_mask]; nz_b = bins[nz_mask]
    if len(nz_v) >= 6:
        lo_thr = np.percentile(nz_v, LVN_PCT)
        hi_thr = np.percentile(nz_v, HVN_PCT)
        lvn = sorted(float(x) for x in nz_b[nz_v <= lo_thr])
        hvn = sorted(float(x) for x in nz_b[nz_v >= hi_thr])
    else:
        lvn, hvn = [], []

    return poc, vah, val, lvn, hvn


def build():
    files = sorted(glob.glob(str(TICK_DIR / "*.parquet")))
    if not files:
        print("ERROR: no hay archivos en", TICK_DIR)
        return

    rows = []
    for fp in files:
        d     = pd.read_parquet(fp, columns=["ts_ms", "price", "size"])
        price = d.price.values.astype(float)
        size  = d["size"].values.astype(float)
        day_ms = int(d.ts_ms.values[0] // 86_400_000) * 86_400_000
        poc, vah, val, lvn, hvn = _session_vp(price, size)
        rows.append({
            "day_ms": day_ms,
            "poc": poc, "vah": vah, "val": val,
            "lvn": json.dumps(lvn),
            "hvn": json.dumps(hvn),
        })
        print(".", end="", flush=True)

    print()
    df = pd.DataFrame(rows).sort_values("day_ms").reset_index(drop=True)
    df.to_parquet(DATA_DIR / "_svp_dayvp.parquet", index=False)
    print(f"VP: {len(df)} sesiones -> _svp_dayvp.parquet")
    _build_naked_poc(df)


def _build_naked_poc(dayvp_df):
    """Naked POC: POC de sesiones previas no revisitadas hasta el dia actual."""
    m1 = pd.read_parquet(M1_PATH, columns=["ts_ms", "high", "low"])
    m1["day"] = m1.ts_ms // 86_400_000
    daily_hl  = m1.groupby("day").agg(h=("high","max"), l=("low","min")).to_dict("index")

    days_int = (dayvp_df.day_ms // 86_400_000).astype(int).tolist()
    pocs     = dayvp_df.poc.tolist()

    rows = []
    for i, current_day in enumerate(days_int):
        naked = []
        for j in range(max(0, i - LOOKBACK), i):
            past_poc = pocs[j]
            if not np.isfinite(past_poc):
                continue
            past_day = days_int[j]
            visited  = False
            for k in range(j + 1, i):
                hl = daily_hl.get(days_int[k])
                if hl and hl["l"] <= past_poc + NAKED_TOL and hl["h"] >= past_poc - NAKED_TOL:
                    visited = True; break
            if not visited:
                naked.append(past_poc)
        rows.append({"day_ms": current_day * 86_400_000, "naked_pocs": json.dumps(sorted(naked))})

    df2 = pd.DataFrame(rows)
    df2.to_parquet(DATA_DIR / "_svp_naked_poc.parquet", index=False)
    print(f"Naked POC: {len(df2)} dias -> _svp_naked_poc.parquet")


# ── LOADERS ────────────────────────────────────────────────────────────────

def load_dayvp():
    """dict[day_int -> {poc, vah, val, lvn: list[float], hvn: list[float]}]"""
    path = DATA_DIR / "_svp_dayvp.parquet"
    if not path.exists():
        return {}
    df  = pd.read_parquet(path)
    out = {}
    for _, row in df.iterrows():
        d = int(row.day_ms // 86_400_000)
        out[d] = {
            "poc": float(row.poc), "vah": float(row.vah), "val": float(row.val),
            "lvn": json.loads(row.lvn), "hvn": json.loads(row.hvn),
        }
    return out


def load_naked_poc():
    """dict[day_int -> list[float]]"""
    path = DATA_DIR / "_svp_naked_poc.parquet"
    if not path.exists():
        return {}
    df  = pd.read_parquet(path)
    out = {}
    for _, row in df.iterrows():
        d = int(row.day_ms // 86_400_000)
        out[d] = json.loads(row.naked_pocs)
    return out


def load_m1_cvd(start_ms=None):
    """CVD acumulado M1 como np.array (mismo largo/orden que load_m1_exit)."""
    df = pd.read_parquet(M1_PATH, columns=["ts_ms","cvd"]).sort_values("ts_ms").reset_index(drop=True)
    if start_ms is not None:
        df = df[df.ts_ms >= start_ms].reset_index(drop=True)
    return df.cvd.values.astype(float)


# ── ESTRATEGIA ─────────────────────────────────────────────────────────────

def fp_target(dayvp, naked, day_int, entry, side, max_dist=2500.0):
    """
    Target desde footprint en la direccion del trade.

    Prioridad:
      1) Naked POC mas cercano (iman estructural: POC de sesion previa no visitado)
      2) HVN de la sesion actual (zona de alto volumen = liquidez real)

    Para SHORT: busca niveles < entry.
    Para LONG:  busca niveles > entry.

    Returns (price: float, name: str) o (None, None).
    """
    best = None; best_name = None

    # 1) Naked POC
    pocs = naked.get(day_int, [])
    if pocs:
        if side == "short":
            cands = [p for p in pocs if p < entry - 30]
            if cands:
                c = max(cands)
                if entry - c <= max_dist:
                    best = c; best_name = "naked_poc"
        else:
            cands = [p for p in pocs if p > entry + 30]
            if cands:
                c = min(cands)
                if c - entry <= max_dist:
                    best = c; best_name = "naked_poc"

    # 2) HVN de sesion (si no hay naked POC disponible)
    if best is None:
        vp  = dayvp.get(day_int, {})
        hvn = vp.get("hvn", [])
        if hvn:
            if side == "short":
                cands = [h for h in hvn if h < entry - 30]
                if cands:
                    c = max(cands)
                    if entry - c <= max_dist:
                        best = c; best_name = "hvn"
            else:
                cands = [h for h in hvn if h > entry + 30]
                if cands:
                    c = min(cands)
                    if c - entry <= max_dist:
                        best = c; best_name = "hvn"

    return best, best_name


def cvd_exit_check(m1_cvd, j0, j, side, reversal_pct=0.35, min_swing=150.0):
    """
    Detecta agotamiento de momentum via CVD.

    Para LONG:  CVD debe subir (compradores presentes). Si cae del pico >= reversal_pct -> salida.
    Para SHORT: CVD debe bajar (vendedores presentes). Si sube del valle >= reversal_pct -> salida.

    min_swing: swing minimo en CVD para filtrar rangos sin direccion.
    No se activa en las primeras 3 barras M1.
    """
    if j <= j0 + 3:
        return False
    if j0 >= len(m1_cvd) or j >= len(m1_cvd):
        return False

    seg  = m1_cvd[j0:j+1]
    cvd0 = seg[0]

    if side == "long":
        extreme = float(np.max(seg))
        swing   = extreme - cvd0
        if swing < min_swing:
            return False
        return (extreme - float(seg[-1])) / swing >= reversal_pct
    else:
        extreme = float(np.min(seg))
        swing   = cvd0 - extreme
        if swing < min_swing:
            return False
        return (float(seg[-1]) - extreme) / swing >= reversal_pct


def lvn_in_path(dayvp, day_int, entry, target):
    """True si hay >=1 LVN entre entry y target (precio viaja rapido por zona de bajo volumen)."""
    vp  = dayvp.get(day_int, {})
    lvn = vp.get("lvn", [])
    if not lvn:
        return False
    lo, hi = min(entry, target), max(entry, target)
    return any(lo < l < hi for l in lvn)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Builder de Session Volume Profile")
    ap.add_argument("--build", action="store_true", help="Genera parquets desde raw_trades")
    args = ap.parse_args()
    if args.build:
        build()
    else:
        dayvp = load_dayvp()
        naked = load_naked_poc()
        print(f"dayvp cacheado: {len(dayvp)} sesiones")
        print(f"naked_poc:      {len(naked)} dias")
        if not dayvp:
            print("\nSin datos. Ejecuta: python backtest/_session_vp.py --build")
        else:
            last = max(dayvp.keys())
            vp   = dayvp[last]
            np_  = naked.get(last, [])
            print(f"\nUltima sesion (day={last}): POC={vp['poc']:.0f}  VAH={vp['vah']:.0f}  VAL={vp['val']:.0f}")
            print(f"  LVN ({len(vp['lvn'])} zones): {vp['lvn'][:5]}")
            print(f"  HVN ({len(vp['hvn'])} zones): {vp['hvn'][:5]}")
            print(f"  Naked POC activos: {len(np_)} -> {sorted(np_)[:5]}")
