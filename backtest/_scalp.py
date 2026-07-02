"""
_scalp.py — harness de SCALPING multi-setup, multi-activo, multi-calibración.
================================================================================
Aterriza la lógica de los setups del catálogo de scalping y los backtestea con
disciplina del proyecto: causal, IS<2026-03-01 / OOS, fee honesto POR LADO
(maker 2bps / taker 5.5bps), salida simulada en M1, una fecha máx N trades/día.

Capa de datos UNIFICADA (los 3 activos tienen el mismo M1):
  M1 parquet (OHLC, vwap, vp_*, obi*, near5/max_ask5 muros, thin_*, regime, atr)
  + footprint derivado (_scalp_fp): stk_buy/sell (stacks), poc_frac, fp_delta...

Setups implementados (factory param → grid):
  sc1  delta-divergence fade        (maker, MR)   #1
  sc3  absorción en nivel + reversal (maker, MR)  #3
  sc5  vwap reclaim + delta confirm  (taker, MR)  #5
  sc7  stacked imbalance breakout    (taker, mom) #7
  sc8  lvn breakout thin book        (taker, mom) #8
  sc2  obi momentum burst            (taker, mom) #2
  sc4  wall absorb → breakout        (taker, mom) #4

Uso:
  python backtest/_scalp.py sc1 --symbol BTCUSDT --tf 5         # grid default
  python backtest/_scalp.py all --symbol BTCUSDT --tf 5         # mejor calib de cada uno
  python backtest/_scalp.py sc7 --symbol ETHUSDT --tf 1
"""
import argparse, itertools, warnings
from pathlib import Path
import numpy as np, pandas as pd
import pyarrow.parquet as pq

warnings.filterwarnings("ignore")
ROOT = Path(__file__).parent.parent
SCALP_HOME = Path("E:/bybit-data/_scalp")   # backup anti-rebuild de reconstrucciones
OOS_MS = int(pd.Timestamp("2026-03-01", tz="UTC").value // 1_000_000)
FEE_MK = 0.0002    # 2 bps / lado
FEE_TK = 0.00055   # 5.5 bps / lado
TF_LABEL = {1:"m1",5:"m5",15:"m15",60:"h1"}

ASSETS = {
    "BTCUSDT": dict(m1=ROOT/"data/bybit-perp/processed/btcusdt_perp_m1.parquet",
                    fp_dir=ROOT/"data/bybit-perp/processed", pre="btcusdt"),
    "ETHUSDT": dict(m1=Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet"),
                    fp_dir=Path("E:/bybit-data/bybit-perp-eth/processed"), pre="ethusdt"),
    "SOLUSDT": dict(m1=Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"),
                    fp_dir=Path("E:/bybit-data/bybit-perp-sol/processed"), pre="solusdt"),
}

# ── CONFIG CANÓNICA sc3 (per-asset, validada IS+OOS, fee honesto) — fuente única ──
# Gestión por activo: BTC TRAIL (trendea → deja correr); ETH/SOL FADE (mean-reversion).
# mapa ampliado de niveles (VP + estructura) — más niveles MAYORES = +avgR y +frecuencia
# Gestión FADE en los 3 (suave, WR alto, DD bajo, robusto IS≥OOS). El trail de BTC daba más
# avgR (+0.88) pero WR 26% / DD 33% (fragil, depende de tendencia) → descartado para deploy.
# BTC trail queda como variante opcional de alto-riesgo/alto-retorno (ver VERDICT).
SC3 = {  # confluence=0 (más niveles); subir a 2 = más robusto IS, menos N
    "BTCUSDT": dict(vr_thr=1.5, stop_atr=0.5, tol_atr=0.6, rr_cap=3.0, mgmt="fade", trail_atr=6.0, confluence=0),
    "ETHUSDT": dict(vr_thr=1.5, stop_atr=0.5, tol_atr=0.6, rr_cap=3.0, mgmt="fade", trail_atr=4.0, confluence=0),
    "SOLUSDT": dict(vr_thr=1.5, stop_atr=0.5, tol_atr=0.6, rr_cap=3.0, mgmt="fade", trail_atr=4.0, confluence=0),
}
# Filtro HTF canónico validado: H1 EMA20 OR H4 EMA20 alineado con el trade, OR vr>3
# Efecto: peorOOS baseline +0.232 → +0.522 (+125%). rr_cap=3.0 > 2.5 validado sweep completo.
SC3_HTF_RULE = "h1_or_h4_or_vr3"


def run_sc3(symbol, tf=5, timeout_min=240):
    """Corre sc3 con la config canónica per-asset (niveles ampliados + gestión per-asset)."""
    from _scalp_more import gen_sc3x, ALLK_L, ALLK_S   # lazy: evita import circular
    c = SC3[symbol]
    s = load(symbol, tf); m1 = load_m1_exit(symbol)
    g = gen_sc3x(vr_thr=c["vr_thr"], stop_atr=c["stop_atr"], tol_atr=c["tol_atr"], rr_cap=c["rr_cap"],
                 longk=ALLK_L, shortk=ALLK_S, confluence=c.get("confluence", 0))
    df = run_setup(s, g, m1, tf, entry_mode="maker", mgmt=c["mgmt"], trail_atr=c["trail_atr"],
                   timeout_min=timeout_min)
    return df, stats(df)


def _load_htf(symbol, tf_min):
    """Carga H1 o H4: parquets BTC, resampleo M1 para ETH/SOL. Causal por diseño."""
    if symbol == "BTCUSDT":
        label = {60: "h1", 240: "h4"}[tf_min]
        h = pq.read_table(f"data/bybit-perp/processed/btcusdt_perp_{label}.parquet",
                          columns=["ts_ms", "close"]).to_pandas()
    else:
        m1 = pq.read_table(ASSETS[symbol]["m1"], columns=["ts_ms", "close"]).to_pandas()
        m1.index = pd.to_datetime(m1.ts_ms, unit="ms", utc=True)
        r = m1.resample(f"{tf_min}min").agg({"close": "last"}).dropna()
        r["ts_ms"] = r.index.astype(np.int64) // 1_000_000
        h = r.reset_index(drop=True)[["ts_ms", "close"]]
    h["ema20"] = h["close"].ewm(span=20).mean()
    return h.ts_ms.values.astype(np.int64), h.close.values, h.ema20.values


def _make_htf_filter(symbol, base_gen, rule=SC3_HTF_RULE):
    """Envuelve un generador con el filtro HTF canónico (H1 OR H4 EMA20 OR vr>3)."""
    h1_ts, h1_c, h1_e = _load_htf(symbol, 60)
    h4_ts, h4_c, h4_e = _load_htf(symbol, 240)

    def gen_f(s, i):
        sigs = base_gen(s, i) or []
        if not sigs:
            return []
        t = s.ts_ms[i]
        i1 = np.searchsorted(h1_ts, t, "right") - 1
        i4 = np.searchsorted(h4_ts, t, "right") - 1
        if i1 < 0 or i4 < 0:
            return []
        h1_bull = h1_c[i1] > h1_e[i1]
        h4_bull = h4_c[i4] > h4_e[i4]
        out = []
        for sig in sigs:
            side = sig[0]; vr = float(s.vr[i])
            h1_ok = (h1_bull if side == "long" else not h1_bull)
            h4_ok = (h4_bull if side == "long" else not h4_bull)
            if rule == "h1_or_h4_or_vr3":
                keep = h1_ok or h4_ok or vr > 3
            elif rule == "h1_or_h4":
                keep = h1_ok or h4_ok
            elif rule == "h1":
                keep = h1_ok
            else:
                keep = True
            if keep:
                out.append(sig)
        return out
    return gen_f


def run_sc3_htf(symbol, tf=5, timeout_min=240, rule=SC3_HTF_RULE,
                entry_offset_atr=0.0, fill_stats=None):
    """Config canónica sc3 + filtro HTF validado (H1 OR H4 OR vr>3).
    Resultado portfolio: peorOOS +0.410, 3.6/d. Estable Q3-25→Q2-26.
    """
    from _scalp_more import gen_sc3x, ALLK_L, ALLK_S
    c = SC3[symbol]
    s = load(symbol, tf); m1 = load_m1_exit(symbol)
    g0 = gen_sc3x(vr_thr=c["vr_thr"], stop_atr=c["stop_atr"], tol_atr=c["tol_atr"], rr_cap=c["rr_cap"],
                  longk=ALLK_L, shortk=ALLK_S, confluence=c.get("confluence", 0))
    g = _make_htf_filter(symbol, g0, rule=rule)
    df = run_setup(s, g, m1, tf, entry_mode="maker", mgmt=c["mgmt"], trail_atr=c["trail_atr"],
                   timeout_min=timeout_min, entry_offset_atr=entry_offset_atr, fill_stats=fill_stats)
    return df, stats(df)


M1_COLS = ["ts_ms","open","high","low","close","volume","buy_vol","sell_vol","delta","cvd",
           "vwap","ema20","atr14","regime","vr","dz","cvd_slope","cvd_div",
           "vp_poc","vp_vah","vp_val","vp_lvn_below","swing_high_50","swing_low_50",
           "prev_day_high","prev_day_low","weekly_high","weekly_low",
           "obi5_mean","obi10_mean","near5_ask","near5_bid","max_ask5","max_bid5",
           "thin_above","thin_below","ask_wall","bid_wall","stacked_imb","abs_ask","abs_bid"]


def _atr(h, l, c, n=14):
    pc = np.empty_like(c); pc[0] = c[0]; pc[1:] = c[:-1]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1/n, adjust=False).mean().values


class S:
    """Frame de scalp: arrays alineados al TF de señal + footprint derivado."""
    def __init__(self, t):
        for c in t.columns:
            v = t[c].values
            setattr(self, c, v)
        self.ts = t.ts_ms.values.astype(np.int64)
        self.o = t.open.values.astype(float); self.h = t.high.values.astype(float)
        self.l = t.low.values.astype(float);  self.c = t.close.values.astype(float)
        self.n = len(self.ts); self.day = self.ts // 86_400_000
        self.reg = t.regime.astype(str).values
        self.atr = t["atr_tf"].values if "atr_tf" in t.columns else _atr(self.h, self.l, self.c)
        for b in ("cvd_div","thin_above","thin_below","ask_wall","bid_wall",
                  "stacked_imb","abs_ask","abs_bid"):
            if hasattr(self, b):
                v = pd.to_numeric(pd.Series(getattr(self, b)), errors="coerce").fillna(0).values
                setattr(self, b, v.astype(bool))


def load(symbol, tf):
    cfg = ASSETS[symbol]
    cols = [c for c in M1_COLS]
    df = pq.read_table(cfg["m1"], columns=cols).to_pandas().sort_values("ts_ms").reset_index(drop=True)
    for fc in ("cvd_div","thin_above","thin_below","ask_wall","bid_wall","stacked_imb","abs_ask","abs_bid"):
        if fc in df.columns:
            df[fc] = pd.to_numeric(df[fc], errors="coerce").fillna(0.0)
    if tf != 1:
        step = tf*60_000; g = (df.ts_ms//step)*step
        last = ["close","vwap","ema20","regime","cvd","cvd_slope","vp_poc","vp_vah","vp_val",
                "vp_lvn_below","swing_high_50","swing_low_50","prev_day_high","prev_day_low",
                "weekly_high","weekly_low","obi5_mean","obi10_mean","near5_ask","near5_bid",
                "max_ask5","max_bid5"]
        flags = ["cvd_div","thin_above","thin_below","ask_wall","bid_wall","stacked_imb","abs_ask","abs_bid"]
        agg = {"ts_ms":("ts_ms","first"),"open":("open","first"),"high":("high","max"),
               "low":("low","min"),"volume":("volume","sum"),"buy_vol":("buy_vol","sum"),
               "sell_vol":("sell_vol","sum"),"delta":("delta","sum")}
        for c in last: agg[c] = (c,"last")
        for c in flags: agg[c] = (c,"max")
        df = df.groupby(g).agg(**agg).reset_index(drop=True)
    df["atr_tf"] = _atr(df.high.values, df.low.values, df.close.values)
    df["vr"] = df.volume/(df.volume.rolling(50).mean().shift(1)+1e-9)
    df["dz"] = (df.delta-df.delta.rolling(50).mean().shift(1))/(df.delta.rolling(50).std().shift(1)+1e-9)
    # merge footprint derivado
    lbl = TF_LABEL.get(tf, f"m{tf}")
    fp = cfg["fp_dir"]/f"{cfg['pre']}_scalp_fp_{lbl}.parquet"
    if not fp.exists():   # fallback al backup en E (anti-rebuild)
        fp = SCALP_HOME/"reconstructions/scalp_fp"/f"{cfg['pre']}_scalp_fp_{lbl}.parquet"
    if fp.exists():
        f = pq.read_table(fp).to_pandas().rename(columns={"bar_ts":"ts_ms"})
        df = df.merge(f, on="ts_ms", how="left")
    else:
        print(f"  [warn] sin footprint derivado {cfg['pre']}_scalp_fp_{lbl} — corré _scalp_fp.py")
    return S(df)


def load_m1_exit(symbol):
    df = pq.read_table(ASSETS[symbol]["m1"], columns=["ts_ms","high","low","close"]).to_pandas()
    df = df.sort_values("ts_ms").reset_index(drop=True)
    return (df.ts_ms.values.astype(np.int64), df.high.values.astype(float),
            df.low.values.astype(float), df.close.values.astype(float))


# ── simulador genérico ────────────────────────────────────────────────────────
def run_setup(s, gen, m1, tf, entry_mode="maker", mgmt="fade", cooldown=6, max_day=3,
              timeout_min=120, trail_atr=4.0, stop_floor_pct=0.15, min_rr=1.2,
              fill_margin_bps=2.0, atr_filter=True, atr_win=500,
              entry_offset_atr=0.0, fill_stats=None):
    m1ts, m1h, m1l, m1c = m1
    bar_ms = tf*60_000
    maker = entry_mode == "maker"
    fee_in = FEE_MK if maker else FEE_TK
    atr_med = pd.Series(s.atr).rolling(atr_win, min_periods=50).median().shift(1).values
    trades = []; cool = 0; dcount = {}
    for i in range(60, s.n-1):
        if i < cool or s.atr[i] <= 0: continue
        if atr_filter and not (np.isfinite(atr_med[i]) and s.atr[i] > atr_med[i]): continue
        d = int(s.day[i])
        if dcount.get(d, 0) >= max_day: continue
        for cand in (gen(s, i) or []):
            side, lvl, stop, tp1, tp2, tag = cand
            if not np.isfinite([lvl, stop, tp2]).all(): continue
            # fill
            if maker:
                ref = s.c[i-1]
                if side == "long" and not (lvl < ref): continue
                if side == "short" and not (lvl > ref): continue
                # offset hacia el precio: colocar la límite un poco antes del nivel mejora
                # el fill (el precio la barre al aproximarse) a costa de peor entry/RR.
                atr_i = s.atr[i]
                entry = (lvl + entry_offset_atr*atr_i) if side == "long" else (lvl - entry_offset_atr*atr_i)
                if fill_stats is not None: fill_stats["attempt"] = fill_stats.get("attempt", 0) + 1
                mf = fill_margin_bps/1e4
                if side == "long" and not (s.l[i] <= entry*(1-mf)): continue
                if side == "short" and not (s.h[i] >= entry*(1+mf)): continue
                if fill_stats is not None: fill_stats["fill"] = fill_stats.get("fill", 0) + 1
            else:
                entry = s.c[i]
            atr0 = s.atr[i]
            if stop_floor_pct > 0:
                mr = stop_floor_pct/100.0*entry
                if abs(entry-stop) < mr: stop = entry-mr if side == "long" else entry+mr
            risk = abs(entry-stop)
            if risk <= 0: continue
            if side == "long" and not (stop < entry < tp2): continue
            if side == "short" and not (tp2 < entry < stop): continue
            if abs(tp2-entry)/risk < min_rr: continue
            j0 = np.searchsorted(m1ts, s.ts[i]+bar_ms)
            jend = np.searchsorted(m1ts, s.ts[i]+bar_ms+timeout_min*60_000)
            eff = mgmt
            if mgmt == "route":   # A+B: trail en tendencia, fade en chop (por barra)
                eff = "trail" if str(s.reg[i]).lower() in ("trendup","trenddown","expansion") else "fade"
            r = _sim_exit(side, entry, stop, tp1, tp2, eff, fee_in, atr0, trail_atr,
                          m1ts, m1h, m1l, m1c, j0, jend, risk)
            if r is None: continue
            trades.append(dict(ts=int(s.ts[i]), side=side, entry=float(entry), risk=float(risk),
                               stop=float(stop), tp2=float(tp2), r=r,
                               oos=int(s.ts[i]) >= OOS_MS, tag=tag, regime=str(s.reg[i])))
            cool = i+cooldown; dcount[d] = dcount.get(d, 0)+1
            break
    return pd.DataFrame(trades)


def _sim_exit(side, entry, stop, tp1, tp2, mgmt, fee_in, atr0, trail_atr,
              m1ts, m1h, m1l, m1c, j0, jend, risk):
    end = min(jend, len(m1ts))
    if end <= j0: return None
    if mgmt == "trail":
        fee_r = (fee_in+FEE_TK)*entry/risk; best = entry; trail = stop
        for j in range(j0, end):
            if side == "long":
                best = max(best, m1h[j]); trail = max(trail, best-trail_atr*atr0)
                if m1l[j] <= trail: return (trail-entry)/risk-fee_r
            else:
                best = min(best, m1l[j]); trail = min(trail, best+trail_atr*atr0)
                if m1h[j] >= trail: return (entry-trail)/risk-fee_r
        px = m1c[end-1]; return ((px-entry) if side == "long" else (entry-px))/risk-fee_r
    if mgmt == "fixed":
        fee_r = (fee_in+FEE_TK)*entry/risk
        for j in range(j0, end):
            if side == "long":
                if m1l[j] <= stop: return (stop-entry)/risk-fee_r
                if m1h[j] >= tp2:  return (tp2-entry)/risk - (fee_in+FEE_MK)*entry/risk
            else:
                if m1h[j] >= stop: return (entry-stop)/risk-fee_r
                if m1l[j] <= tp2:  return (entry-tp2)/risk - (fee_in+FEE_MK)*entry/risk
        px = m1c[end-1]; return ((px-entry) if side == "long" else (entry-px))/risk-fee_r
    # fade: parcial 50% tp1 → breakeven → tp2
    cur = stop; realized = 0.0; rem = 1.0; f1 = False
    p1 = 0.5 if (tp1 is not None and np.isfinite(tp1)) else 0.0; reason = "timeout"
    for j in range(j0, end):
        if side == "long":
            if m1l[j] <= cur: realized += rem*((cur-entry)/risk); reason = "be" if f1 else "stop"; break
            if not f1 and p1 and m1h[j] >= tp1: realized += p1*((tp1-entry)/risk); rem -= p1; f1 = True; cur = entry
            if m1h[j] >= tp2: realized += rem*((tp2-entry)/risk); reason = "target"; break
        else:
            if m1h[j] >= cur: realized += rem*((entry-cur)/risk); reason = "be" if f1 else "stop"; break
            if not f1 and p1 and m1l[j] <= tp1: realized += p1*((entry-tp1)/risk); rem -= p1; f1 = True; cur = entry
            if m1l[j] <= tp2: realized += rem*((entry-tp2)/risk); reason = "target"; break
    else:
        px = m1c[end-1]; realized += rem*(((px-entry) if side == "long" else (entry-px))/risk)
    exit_s = FEE_MK if reason == "target" else FEE_TK
    fee_r = (fee_in + (FEE_MK*p1 if f1 else 0.0) + exit_s*rem)*entry/risk
    return realized-fee_r


# ── stats ─────────────────────────────────────────────────────────────────────
def stats(df, span_days=365):
    if df is None or len(df) == 0:
        return dict(n=0, n_oos=0, wr=0, avgR=0, isA=0, oosA=0, dd=0, sharpe=0, npd=0)
    o = df[df.oos]; iss = df[~df.oos]
    cap = 500.0; peak = 500.0; dd = 0.0
    for r in df.sort_values("ts").r.values:
        cap += 5*r; peak = max(peak, cap); dd = max(dd, (peak-cap)/peak)
    days = max(df.ts.nunique() and (df.ts.max()-df.ts.min())/86_400_000, 1)
    sh = (o.r.mean()/(o.r.std()+1e-9))*np.sqrt(max(len(o)/ (days*0.34/365*252+1e-9),1e-9)) if len(o) > 1 else 0
    return dict(n=len(df), n_oos=len(o), wr=100*(df.r > 0).mean(), avgR=df.r.mean(),
                isA=iss.r.mean() if len(iss) else 0, oosA=o.r.mean() if len(o) else 0,
                dd=100*dd, sharpe=sh, npd=len(df)/max(days,1))


def fmt(label, st):
    return (f"  {label:<46} n={st['n']:>5} oos={st['n_oos']:>4} | WR {st['wr']:4.1f}% | "
            f"IS {st['isA']:+.3f} | OOS {st['oosA']:+.3f} | DD {st['dd']:4.1f}% | "
            f"Sh {st['sharpe']:+4.1f} | {st['npd']:.1f}/d")


# ── generadores (factories param → gen) ──────────────────────────────────────
def near_level(s, i, side, tol_atr):
    """Nivel VP/estructura más cercano del lado correcto para entrada maker."""
    if side == "long":
        cands = [s.vp_val[i], s.vp_poc[i], s.swing_low_50[i], s.prev_day_low[i]]
        cands = [c for c in cands if np.isfinite(c) and c < s.c[i] and abs(s.c[i]-c) <= tol_atr*s.atr[i]]
        return max(cands) if cands else np.nan
    else:
        cands = [s.vp_vah[i], s.vp_poc[i], s.swing_high_50[i], s.prev_day_high[i]]
        cands = [c for c in cands if np.isfinite(c) and c > s.c[i] and abs(c-s.c[i]) <= tol_atr*s.atr[i]]
        return min(cands) if cands else np.nan


def struct_tp(s, i, side, entry):
    if side == "long":
        cands = [c for c in (s.vp_vah[i], s.swing_high_50[i], s.prev_day_high[i], s.weekly_high[i])
                 if np.isfinite(c) and c > entry*1.001]
        if not cands: return None, np.nan
        return min(cands), max(cands)
    else:
        cands = [c for c in (s.vp_val[i], s.swing_low_50[i], s.prev_day_low[i], s.weekly_low[i])
                 if np.isfinite(c) and c < entry*0.999]
        if not cands: return None, np.nan
        return max(cands), min(cands)


def gen_sc1(W=15, stop_atr=0.5, tol_atr=1.5):
    """#1 delta-divergence fade. Precio hace nuevo extremo W pero footprint delta no confirma."""
    def g(s, i):
        out = []
        if i < W: return out
        fpd = getattr(s, "fp_delta", None)
        if fpd is None or not np.isfinite(fpd[i]): return out
        newhi = s.h[i] >= np.nanmax(s.h[i-W:i])
        newlo = s.l[i] <= np.nanmin(s.l[i-W:i])
        if newhi and fpd[i] <= 0:    # nuevo high sin compra agresiva → fade short
            lvl = near_level(s, i, "short", tol_atr)
            if np.isfinite(lvl):
                stop = max(s.h[i], lvl)+stop_atr*s.atr[i]
                tp1, tp2 = struct_tp(s, i, "short", lvl)
                if np.isfinite(tp2): out.append(("short", lvl, stop, tp1, tp2, "sc1"))
        if newlo and fpd[i] >= 0:
            lvl = near_level(s, i, "long", tol_atr)
            if np.isfinite(lvl):
                stop = min(s.l[i], lvl)-stop_atr*s.atr[i]
                tp1, tp2 = struct_tp(s, i, "long", lvl)
                if np.isfinite(tp2): out.append(("long", lvl, stop, tp1, tp2, "sc1"))
        return out
    return g


def _clip_rr(s, i, side, entry, stop, tp1, tp2, rr_cap):
    """Recorta el target estructural a un RR máximo (más scalp). rr_cap=None → estructural puro."""
    if rr_cap is None or not np.isfinite(tp2): return tp1, tp2
    risk = abs(entry-stop)
    if risk <= 0: return tp1, tp2
    rr = abs(tp2-entry)/risk
    if rr <= rr_cap: return tp1, tp2
    tp2c = entry+rr_cap*risk if side == "long" else entry-rr_cap*risk
    if tp1 is not None and not (min(entry, tp2c) < tp1 < max(entry, tp2c)): tp1 = None
    return tp1, tp2c


def gen_sc3(vr_thr=2.0, poc_frac_thr=0.0, stop_atr=0.5, tol_atr=0.6, rr_cap=None, delta_gate=True):
    """#3 absorción en nivel + reversal. Precio en nivel VP, alto volumen agresor de UN lado
    pero el precio no rompe (cierra de vuelta) → contraparte absorbe → fade maker en el nivel.
    rr_cap: clip del target a RR máx (scalp). delta_gate: exige footprint delta en contra (absorción)."""
    def g(s, i):
        out = []
        fpd = getattr(s, "fp_delta", None); pf = getattr(s, "poc_frac", None)
        if s.vr[i] < vr_thr: return out
        if poc_frac_thr > 0 and (pf is None or not np.isfinite(pf[i]) or pf[i] < poc_frac_thr): return out
        for lvlv in (s.vp_val[i], s.vp_poc[i]):
            if not np.isfinite(lvlv): continue
            if abs(s.l[i]-lvlv) <= tol_atr*s.atr[i] and s.c[i] > lvlv and (not delta_gate or fpd is None or fpd[i] < 0):
                stop = lvlv-stop_atr*s.atr[i]; tp1, tp2 = struct_tp(s, i, "long", lvlv)
                tp1, tp2 = _clip_rr(s, i, "long", lvlv, stop, tp1, tp2, rr_cap)
                if np.isfinite(tp2): out.append(("long", lvlv, stop, tp1, tp2, "sc3")); break
        for lvlv in (s.vp_vah[i], s.vp_poc[i]):
            if not np.isfinite(lvlv): continue
            if abs(s.h[i]-lvlv) <= tol_atr*s.atr[i] and s.c[i] < lvlv and (not delta_gate or fpd is None or fpd[i] > 0):
                stop = lvlv+stop_atr*s.atr[i]; tp1, tp2 = struct_tp(s, i, "short", lvlv)
                tp1, tp2 = _clip_rr(s, i, "short", lvlv, stop, tp1, tp2, rr_cap)
                if np.isfinite(tp2): out.append(("short", lvlv, stop, tp1, tp2, "sc3")); break
        return out
    return g


def gen_sc5(vr_thr=1.2, stop_atr=0.5):
    """#5 vwap reclaim + delta confirm. Cierra de vuelta sobre/bajo vwap con delta a favor y volumen."""
    def g(s, i):
        out = []
        vw = s.vwap[i]
        if not np.isfinite(vw) or s.vr[i] < vr_thr: return out
        # reclaim alcista: barra previa bajo vwap, esta cierra sobre vwap con cuerpo y delta+
        if s.c[i-1] < s.vwap[i-1] and s.c[i] > vw and s.c[i] > s.o[i] and s.delta[i] > 0:
            stop = min(s.l[i], vw)-stop_atr*s.atr[i]; tp1, tp2 = struct_tp(s, i, "long", s.c[i])
            if np.isfinite(tp2): out.append(("long", s.c[i], stop, tp1, tp2, "sc5"))
        if s.c[i-1] > s.vwap[i-1] and s.c[i] < vw and s.c[i] < s.o[i] and s.delta[i] < 0:
            stop = max(s.h[i], vw)+stop_atr*s.atr[i]; tp1, tp2 = struct_tp(s, i, "short", s.c[i])
            if np.isfinite(tp2): out.append(("short", s.c[i], stop, tp1, tp2, "sc5"))
        return out
    return g


def gen_sc7(stk_min=3, vol_mult=1.5, stop_atr=0.3, rr=2.0):
    """#7 stacked imbalance breakout. >=N celdas imbalance apiladas + rotura de zona + volumen."""
    def g(s, i):
        out = []
        bl = getattr(s, "stk_buy_len", None); sl = getattr(s, "stk_sell_len", None)
        if bl is None: return out
        if s.vr[i] < vol_mult: return out
        # stacked buy → rotura al alza
        if np.isfinite(bl[i]) and bl[i] >= stk_min and np.isfinite(s.stk_buy_hi[i]):
            zhi = s.stk_buy_hi[i]; zlo = s.stk_buy_lo[i]
            if s.c[i] > zhi:    # confirmó breakout
                entry = s.c[i]; stop = min(zlo, s.l[i])-stop_atr*s.atr[i]; risk = entry-stop
                if risk > 0: out.append(("long", entry, stop, None, entry+rr*risk, "sc7"))
        if np.isfinite(sl[i]) and sl[i] >= stk_min and np.isfinite(s.stk_sell_lo[i]):
            zhi = s.stk_sell_hi[i]; zlo = s.stk_sell_lo[i]
            if s.c[i] < zlo:
                entry = s.c[i]; stop = max(zhi, s.h[i])+stop_atr*s.atr[i]; risk = stop-entry
                if risk > 0: out.append(("short", entry, stop, None, entry-rr*risk, "sc7"))
        return out
    return g


def gen_sc8(stop_atr=0.5, rr=2.0):
    """#8 lvn breakout thin book. Precio en LVN/zona fina → empuje hacia el vacío, TP en HVN (poc)."""
    def g(s, i):
        out = []
        # vacío arriba (thin_above) → breakout long hacia poc/vah; vacío abajo → short
        if s.thin_above[i] and s.c[i] > s.c[i-1]:
            tgt = s.vp_vah[i] if np.isfinite(s.vp_vah[i]) and s.vp_vah[i] > s.c[i] else s.c[i]+rr*stop_atr*s.atr[i]
            entry = s.c[i]; stop = s.c[i]-stop_atr*s.atr[i]
            out.append(("long", entry, stop, None, tgt, "sc8"))
        if s.thin_below[i] and s.c[i] < s.c[i-1]:
            tgt = s.vp_val[i] if np.isfinite(s.vp_val[i]) and s.vp_val[i] < s.c[i] else s.c[i]-rr*stop_atr*s.atr[i]
            entry = s.c[i]; stop = s.c[i]+stop_atr*s.atr[i]
            out.append(("short", entry, stop, None, tgt, "sc8"))
        return out
    return g


def gen_sc2(obi_thr=0.3, stop_atr=0.6, rr=1.5):
    """#2 obi momentum burst. OBI sostenido + volumen → momentum en dirección del imbalance."""
    def g(s, i):
        out = []
        ob = s.obi5_mean[i]
        if not np.isfinite(ob) or s.vr[i] < 1.2: return out
        if ob >= obi_thr and s.obi5_mean[i-1] >= obi_thr*0.7:
            entry = s.c[i]; stop = entry-stop_atr*s.atr[i]
            out.append(("long", entry, stop, None, entry+rr*stop_atr*s.atr[i], "sc2"))
        if ob <= -obi_thr and s.obi5_mean[i-1] <= -obi_thr*0.7:
            entry = s.c[i]; stop = entry+stop_atr*s.atr[i]
            out.append(("short", entry, stop, None, entry-rr*stop_atr*s.atr[i], "sc2"))
        return out
    return g


def gen_sc4(wall_mult=3.0, stop_atr=0.4, rr=2.0):
    """#4 wall absorb → breakout. Muro grande en top-5 (max_ask5 >> near5) consumido sin reponer → breakout."""
    def g(s, i):
        out = []
        ma = getattr(s, "max_ask5", None); mb = getattr(s, "max_bid5", None)
        na = getattr(s, "near5_ask", None); nb = getattr(s, "near5_bid", None)
        if ma is None: return out
        # muro ask en i-1 (resistencia), consumido en i (max_ask5 cae) y precio sube → breakout long
        if (np.isfinite(ma[i-1]) and np.isfinite(na[i-1]) and ma[i-1] >= wall_mult*na[i-1]/5
                and ma[i] < ma[i-1]*0.6 and s.c[i] > s.c[i-1]):
            entry = s.c[i]; stop = entry-stop_atr*s.atr[i]
            out.append(("long", entry, stop, None, entry+rr*stop_atr*s.atr[i], "sc4"))
        if (np.isfinite(mb[i-1]) and np.isfinite(nb[i-1]) and mb[i-1] >= wall_mult*nb[i-1]/5
                and mb[i] < mb[i-1]*0.6 and s.c[i] < s.c[i-1]):
            entry = s.c[i]; stop = entry+stop_atr*s.atr[i]
            out.append(("short", entry, stop, None, entry-rr*stop_atr*s.atr[i], "sc4"))
        return out
    return g


# ── configuración de cada setup: gen factory, entry_mode, mgmt, grid ──────────
SETUPS = {
    "sc1": dict(gen=gen_sc1, entry="maker", mgmt="fade", timeout=240,
                grid=dict(W=[10,15,20], stop_atr=[0.4,0.5,0.6], tol_atr=[1.0,1.5,2.0])),
    "sc3": dict(gen=gen_sc3, entry="maker", mgmt="fade", timeout=240,
                grid=dict(vr_thr=[1.5,2.0,2.5], poc_frac_thr=[0.0,0.15], stop_atr=[0.4,0.5], tol_atr=[0.4,0.6])),
    "sc5": dict(gen=gen_sc5, entry="taker", mgmt="fade", timeout=240,
                grid=dict(vr_thr=[1.0,1.2,1.5], stop_atr=[0.4,0.5,0.6])),
    "sc7": dict(gen=gen_sc7, entry="taker", mgmt="trail", timeout=120,
                grid=dict(stk_min=[3,4], vol_mult=[1.2,1.5,2.0], stop_atr=[0.2,0.3,0.4], rr=[2.0])),
    "sc8": dict(gen=gen_sc8, entry="taker", mgmt="trail", timeout=120,
                grid=dict(stop_atr=[0.3,0.5,0.7], rr=[1.5,2.0,2.5])),
    "sc2": dict(gen=gen_sc2, entry="taker", mgmt="trail", timeout=120,
                grid=dict(obi_thr=[0.2,0.3,0.4], stop_atr=[0.5,0.6], rr=[1.5,2.0])),
    "sc4": dict(gen=gen_sc4, entry="taker", mgmt="trail", timeout=120,
                grid=dict(wall_mult=[2.0,3.0,4.0], stop_atr=[0.3,0.4], rr=[1.5,2.0])),
}


def grid_iter(grid):
    keys = list(grid);
    for combo in itertools.product(*[grid[k] for k in keys]):
        yield dict(zip(keys, combo))


def run_grid(symbol, setup, tf, top=12):
    cfg = SETUPS[setup]
    print(f"\nCargando {symbol} M{tf}...", flush=True)
    s = load(symbol, tf); m1 = load_m1_exit(symbol)
    span = (s.ts.max()-s.ts.min())/86_400_000
    print(f"{symbol} {setup} | M{tf} span={span:.0f}d entry={cfg['entry']} mgmt={cfg['mgmt']} "
          f"timeout={cfg['timeout']}m | grid={sum(1 for _ in grid_iter(cfg['grid']))} combos\n")
    rows = []
    for p in grid_iter(cfg["grid"]):
        gen = cfg["gen"](**p)
        df = run_setup(s, gen, m1, tf, entry_mode=cfg["entry"], mgmt=cfg["mgmt"],
                       timeout_min=cfg["timeout"])
        st = stats(df, span)
        rows.append((p, st))
    rows.sort(key=lambda x: (x[1]["oosA"] if x[1]["n_oos"] >= 15 else -99, x[1]["n"]), reverse=True)
    print(f"  TOP por OOS avgR (mín 15 trades OOS):")
    for p, st in rows[:top]:
        lab = ",".join(f"{k}={v}" for k, v in p.items())
        print(fmt(lab, st))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("which", nargs="?", default="sc1")
    ap.add_argument("--symbol", default="BTCUSDT", choices=list(ASSETS))
    ap.add_argument("--tf", type=int, default=5)
    args = ap.parse_args()
    todo = list(SETUPS) if args.which == "all" else [args.which]
    for k in todo:
        if k not in SETUPS: print(f"setup {k} desconocido"); continue
        run_grid(args.symbol, k, args.tf)


if __name__ == "__main__":
    main()
