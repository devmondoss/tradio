"""
_npoc_poorhl.py — Market Profile: NPOC (naked daily VPOC) + Poor vs Excess high/low
====================================================================================
TEST 1 — NPOC:
  a) VPOC diario (perfil de volumen del dia completo, barras M15, bins ~10bps).
     Un NPOC esta "naked" si desde el fin de su dia de origen NINGUNA barra lo toco
     (h>=npoc>=l). Lookback max 30 dias. Sin lookahead: nivel de dias COMPLETOS
     anteriores; vivo en la barra i si first_touch >= i (barras hasta i-1 no lo tocaron).
  b) Test A: gen que fadea el NPOC vivo mas cercano (long si esta debajo del precio,
     short si arriba), stop 0.5*ATR lado opuesto, target estructural (otros NPOCs +
     VAH/VAL). Standalone con filtros v3 (H1+dist) y anadido a los gens v3.
  c) Test B (iman): trades v3 con/sin NPOC vivo entre entry y tp2.

TEST 2 — Poor vs Excess PDH/PDL:
  Clasifica el extremo del dia anterior COMPLETO: EXCESS si <=2 barras M15 en el
  quintil extremo del rango del dia, POOR si >=4, NEUTRAL si 3. Estadistica de
  revisita/ruptura al dia siguiente + backtest gen dedicado fade PDH/PDL
  (all / excess-only / poor-only) con mecanica casa + filtros v3.

Uso:
  python backtest/_npoc_poorhl.py npoc   --sym BTCUSDT
  python backtest/_npoc_poorhl.py poorhl --sym BTCUSDT
"""
import sys, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import numpy as np, pandas as pd
import _listas2 as L2
from _listas2 import struct_target
from _strategy_ab import run_system
from _audit_mirror import gen_h21_short
from _ict_ifvg import gen_ifvg
from _v3_parity import load, make_filter, wrap, SYMS, STD, TF

FILT = make_filter(True, True)          # H1 slope + dist>0.5ATR (filtros v3)
V3KW = dict(mode='fade', stop_scale=0.8, **STD)

# ────────────────────────────────────────────────────────────── util comun
def full_days(a, min_bars=90):
    """(day, start_idx, end_idx) solo de dias COMPLETOS (>=90 barras M15 de 96)."""
    days, starts, cnts = np.unique(a.day, return_index=True, return_counts=True)
    return [(int(days[k]), int(starts[k]), int(starts[k] + cnts[k]))
            for k in range(len(days)) if cnts[k] >= min_bars]

def r2(df):
    if len(df) == 0:
        return dict(n_is=0, avgR_is=0, wr_is=0, netR_is=0, n_oos=0, avgR_oos=0, wr_oos=0, netR_oos=0, dd=0)
    i, o = df[~df.oos], df[df.oos]
    cap = peak = 500.0; dd = 0.0
    for r in df.sort_values("ts").r.values:
        cap += 5 * r; peak = max(peak, cap); dd = max(dd, (peak - cap) / peak)
    def blk(d, pre):
        return {f"n_{pre}": len(d), f"avgR_{pre}": d.r.mean() if len(d) else 0,
                f"wr_{pre}": 100 * (d.r > 0).mean() if len(d) else 0,
                f"netR_{pre}": d.r.sum() if len(d) else 0}
    return {**blk(i, "is"), **blk(o, "oos"), "dd": 100 * dd}

def prow(label, r):
    print(f"  {label:<34} IS  n={r['n_is']:>4} avgR={r['avgR_is']:+.3f} WR={r['wr_is']:3.0f}% netR={r['netR_is']:+7.1f}"
          f" | OOS n={r['n_oos']:>4} avgR={r['avgR_oos']:+.3f} WR={r['wr_oos']:3.0f}% netR={r['netR_oos']:+7.1f}"
          f" | DD={r['dd']:.1f}%")

def record_wrap(gens, rec):
    """Registra (bar, side, lvl) -> (tp1, tp2) de cada candidato que el motor ve."""
    out = []
    for g in gens:
        def mk(gg):
            def w(a, i):
                r = gg(a, i)
                if r:
                    for t in r:
                        rec[(i, t[0], round(float(t[1]), 6))] = (t[3], t[4])
                return r
            return w
        out.append(mk(g))
    return out

# ────────────────────────────────────────────────────────────── TEST 1: NPOC
def build_npocs(a, bin_bps=10.0):
    """[(day, vpoc, first_touch_idx)] por dia completo. first_touch = primer indice de
    barra POSTERIOR al dia de origen con h>=vpoc>=l (10**9 = nunca tocado)."""
    npocs = []
    for d, s, e in full_days(a):
        l, h, v = a.l[s:e], a.h[s:e], a.volume[s:e]
        lo, hi = l.min(), h.max()
        bw = np.median(a.c[s:e]) * bin_bps / 1e4
        nb = max(1, int(np.ceil((hi - lo) / bw)))
        vol = np.zeros(nb)
        b0 = np.clip(((l - lo) // bw).astype(int), 0, nb - 1)
        b1 = np.clip(((h - lo) // bw).astype(int), 0, nb - 1)
        for j in range(len(v)):                      # volumen repartido uniforme en [l,h]
            vol[b0[j]:b1[j] + 1] += v[j] / (b1[j] - b0[j] + 1)
        p = lo + (int(np.argmax(vol)) + 0.5) * bw
        m = (a.h[e:] >= p) & (a.l[e:] <= p)
        ft = e + int(np.argmax(m)) if m.any() else 10**9
        npocs.append((d, float(p), ft))
    return npocs

def make_alive(npocs, max_back=30):
    nd = np.array([x[0] for x in npocs])
    def alive(i, day_i):
        k0 = np.searchsorted(nd, day_i - max_back)
        k1 = np.searchsorted(nd, day_i)              # solo dias ESTRICTAMENTE anteriores
        return [(npocs[k][1], npocs[k][2]) for k in range(k0, k1) if npocs[k][2] >= i]
    return alive

def gen_npoc(alive_fn):
    def g(a, i):
        al = alive_fn(i, int(a.day[i]))
        if not al: return
        price = float(a.c[i]); atr = float(a.atr[i])
        pxs = [p for p, _ in al]
        out = []
        below = [p for p in pxs if p < price]
        above = [p for p in pxs if p > price]
        if below:                                    # fade long en el NPOC mas cercano debajo
            lvl = max(below)
            cands = [p for p in pxs if p > lvl * 1.001]
            if np.isfinite(a.vp_vah[i]) and a.vp_vah[i] > lvl * 1.001: cands.append(float(a.vp_vah[i]))
            if cands:
                tp1, tp2 = min(cands), max(cands)
                if abs(tp1 - tp2) < 1e-9: tp1 = None
                out.append(("long", lvl, lvl - 0.5 * atr, tp1, tp2, "NPOC"))
        if above:                                    # fade short en el NPOC mas cercano arriba
            lvl = min(above)
            cands = [p for p in pxs if p < lvl * 0.999]
            if np.isfinite(a.vp_val[i]) and a.vp_val[i] < lvl * 0.999: cands.append(float(a.vp_val[i]))
            if cands:
                tp1, tp2 = max(cands), min(cands)
                if abs(tp1 - tp2) < 1e-9: tp1 = None
                out.append(("short", lvl, lvl + 0.5 * atr, tp1, tp2, "NPOC"))
        out.sort(key=lambda t: abs(price - t[1]))
        return out
    return g

def v3_gens():
    return [L2.gen_h5(), L2.gen_h21(), gen_h21_short(), gen_ifvg()]

def npoc_stats(a, npocs):
    tot = len(npocs)
    touched = [x for x in npocs if x[2] < 10**9]
    d2t = []
    for d, p, ft in touched:
        d2t.append(int(a.day[ft]) - d)
    d2t = np.array(d2t)
    within30 = (d2t <= 30).sum()
    # promedio de NPOCs vivos por barra (muestreo cada 8 barras)
    alive = make_alive(npocs)
    samp = range(96 * 35, a.n, 8)                    # despues del warmup de 35 dias
    counts = [len(alive(i, int(a.day[i]))) for i in samp]
    print(f"  NPOCs diarios: {tot} | tocados alguna vez: {len(touched)} ({100*len(touched)/tot:.0f}%)"
          f" | tocados <=30d: {within30} ({100*within30/tot:.0f}%)")
    if len(d2t):
        print(f"  dias hasta el toque: mediana={np.median(d2t):.0f} p75={np.percentile(d2t,75):.0f} p90={np.percentile(d2t,90):.0f}")
    print(f"  NPOCs vivos por barra (lookback 30d): media={np.mean(counts):.1f} max={np.max(counts)}")

def task_npoc(sym):
    print(f"\n{'='*100}\nTEST 1 — NPOC · {sym}\n{'='*100}")
    a, m1 = load(sym)
    npocs = build_npocs(a)
    npoc_stats(a, npocs)
    alive = make_alive(npocs)

    # A1: standalone con filtros v3 (H1+dist) y sin filtros (diagnostico de n)
    df_sa = run_system(a, wrap([gen_npoc(alive)], FILT), m1, TF, **V3KW)
    prow("NPOC standalone (H1+dist, fade)", r2(df_sa))
    df_nf = run_system(a, [gen_npoc(alive)], m1, TF, **V3KW)
    prow("NPOC standalone SIN filtros", r2(df_nf))

    # baseline v3 (con recorder para el test B)
    rec = {}
    df_v3 = run_system(a, record_wrap(wrap(v3_gens(), FILT), rec), m1, TF, **V3KW)
    prow("v3 baseline (H1+dist+IFVG)", r2(df_v3))

    # A2: v3 + gen NPOC anadido
    df_plus = run_system(a, wrap(v3_gens() + [gen_npoc(alive)], FILT), m1, TF, **V3KW)
    prow("v3 + NPOC anadido", r2(df_plus))

    # B: iman — NPOC vivo entre entry y tp2 en los trades v3
    rows = []
    for t in df_v3.itertuples():
        key = (t.bar, t.side, round(float(t.entry), 6))
        if key not in rec: continue
        tp1, tp2 = rec[key]
        al = alive(t.bar, int(a.day[t.bar]))
        if t.side == 'long': has = any(t.entry < p < tp2 for p, _ in al)
        else:                has = any(tp2 < p < t.entry for p, _ in al)
        rows.append(dict(ts=t.ts, r=t.r, oos=t.oos, has=has))
    b = pd.DataFrame(rows)
    print(f"\n  TEST B — iman (trades v3 mapeados: {len(b)}/{len(df_v3)})")
    for lbl, sub in [("CON NPOC en el camino", b[b.has]), ("SIN NPOC en el camino", b[~b.has])]:
        i, o = sub[~sub.oos], sub[sub.oos]
        print(f"  {lbl:<24} IS  n={len(i):>4} avgR={i.r.mean() if len(i) else 0:+.3f}"
              f" | OOS n={len(o):>4} avgR={o.r.mean() if len(o) else 0:+.3f}")

# ─────────────────────────────────────────────────── TEST 2: Poor vs Excess
def classify_days(a):
    """day -> pdh/pdl + clase del extremo (EXCESS <=2 barras M15 en el quintil, POOR >=4).
    Solo usa barras del propio dia (causal para el dia siguiente)."""
    cls = {}
    fd = full_days(a)
    for d, s, e in fd:
        h, l, c, v = a.h[s:e], a.l[s:e], a.c[s:e], a.volume[s:e]
        H, L = h.max(), l.min(); rng = H - L
        if rng <= 0: continue
        top, bot = H - 0.2 * rng, L + 0.2 * rng
        nbh, nbl = int((h >= top).sum()), int((l <= bot).sum())
        vt = v.sum()
        cat = lambda nb: 'EXCESS' if nb <= 2 else ('POOR' if nb >= 4 else 'NEUTRAL')
        cls[d] = dict(pdh=float(H), pdl=float(L), ch=cat(nbh), cl=cat(nbl), nbh=nbh, nbl=nbl,
                      vsh=float(v[h >= top].sum() / vt) if vt > 0 else 0,
                      vsl=float(v[l <= bot].sum() / vt) if vt > 0 else 0,
                      s=s, e=e)
    return cls

def poorhl_stats(a, cls):
    days = sorted(cls)
    dist_h = pd.Series([cls[d]['ch'] for d in days]).value_counts()
    dist_l = pd.Series([cls[d]['cl'] for d in days]).value_counts()
    print(f"  dias completos={len(days)}")
    print(f"  HIGHS: {dict(dist_h)} | barras en quintil: media={np.mean([cls[d]['nbh'] for d in days]):.1f}"
          f" | vol share quintil: media={100*np.mean([cls[d]['vsh'] for d in days]):.1f}%")
    print(f"  LOWS : {dict(dist_l)} | barras en quintil: media={np.mean([cls[d]['nbl'] for d in days]):.1f}"
          f" | vol share quintil: media={100*np.mean([cls[d]['vsl'] for d in days]):.1f}%")
    # revisita/ruptura al dia siguiente
    res = {k: dict(n=0, touch=0, brk=0) for k in
           ['H_EXCESS', 'H_POOR', 'H_NEUTRAL', 'L_EXCESS', 'L_POOR', 'L_NEUTRAL']}
    for d in days:
        if d + 1 not in cls: continue
        nx = cls[d + 1]; s, e = nx['s'], nx['e']
        kh = 'H_' + cls[d]['ch']
        res[kh]['n'] += 1
        if nx['pdh'] >= cls[d]['pdh']: res[kh]['touch'] += 1
        if (a.c[s:e] > cls[d]['pdh']).any(): res[kh]['brk'] += 1
        kl = 'L_' + cls[d]['cl']
        res[kl]['n'] += 1
        if nx['pdl'] <= cls[d]['pdl']: res[kl]['touch'] += 1
        if (a.c[s:e] < cls[d]['pdl']).any(): res[kl]['brk'] += 1
    print("  revisita/ruptura al dia siguiente:")
    for k, v in res.items():
        if v['n'] == 0: continue
        print(f"    {k:<10} n={v['n']:>3} | revisita={100*v['touch']/v['n']:4.1f}% | ruptura(close)={100*v['brk']/v['n']:4.1f}%")

def gen_pdhl(cls, only=None):
    """Fade PDH (short) / PDL (long) del dia anterior COMPLETO, mecanica casa.
    only: None=todos, 'EXCESS' o 'POOR' filtra por la clase del extremo."""
    def g(a, i):
        info = cls.get(int(a.day[i]) - 1)
        if info is None: return
        atr = float(a.atr[i]); out = []
        if only is None or info['ch'] == only:
            lvl = info['pdh']
            tp1, tp2 = struct_target(a, i, 'short', lvl)
            if np.isfinite(tp2): out.append(("short", lvl, lvl + 0.5 * atr, tp1, tp2, "PDH"))
        if only is None or info['cl'] == only:
            lvl = info['pdl']
            tp1, tp2 = struct_target(a, i, 'long', lvl)
            if np.isfinite(tp2): out.append(("long", lvl, lvl - 0.5 * atr, tp1, tp2, "PDL"))
        return out
    return g

def task_poorhl(sym):
    print(f"\n{'='*100}\nTEST 2 — POOR vs EXCESS PDH/PDL · {sym}\n{'='*100}")
    a, m1 = load(sym)
    cls = classify_days(a)
    poorhl_stats(a, cls)
    print()
    for lbl, only in [("PDH/PDL fade TODOS", None), ("PDH/PDL fade EXCESS-only", 'EXCESS'),
                      ("PDH/PDL fade POOR-only", 'POOR')]:
        df = run_system(a, wrap([gen_pdhl(cls, only)], FILT), m1, TF, **V3KW)
        prow(lbl, r2(df))

# ──────────────────────────────────────────────────────────────────── main
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks", nargs="+", choices=["npoc", "poorhl"])
    ap.add_argument("--sym", default=None)
    args = ap.parse_args()
    syms = [args.sym] if args.sym else SYMS
    for sym in syms:
        for t in args.tasks:
            if t == "npoc": task_npoc(sym)
            elif t == "poorhl": task_poorhl(sym)
    print("\nDONE")
