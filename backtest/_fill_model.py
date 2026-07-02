"""
_fill_model.py — Modelo de fill condicional con datos reales de paper (liquidity fade-only)
===========================================================================================
1. Baja events place/fill + trades de Supabase (paper Railway desde 2026-06-22).
2. Dedup places por episodio (symbol, side, kind, precio), etiqueta filled=0/1.
3. P(fill | contexto): buckets con IC Wilson + logística (IRLS numpy, sin sklearn).
4. Selección adversa: P(fill) estimada vs result_r realizado en los trades.
5. Reponderación del backtest fade (run_system) por P(fill|contexto)/P(fill) media.

Uso:
    python backtest/_fill_model.py --stage fetch      # baja y cachea datos
    python backtest/_fill_model.py --stage model      # dataset fills + buckets + logit + adversa
    python backtest/_fill_model.py --stage backtest   # reponderación backtest 3 símbolos
    python backtest/_fill_model.py                    # todo (usa cache si existe)
"""
import sys, os, json, time, argparse
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
ROOT = Path(__file__).parent.parent

CACHE = Path(os.environ.get("FILL_MODEL_CACHE",
    r"C:\Users\inkam\AppData\Local\Temp\claude\c--Users-inkam-Documents-flow-surface-tradio\3054322d-d406-463c-8e88-2b2e6b8f6c19\scratchpad")) / "fill_model"
CACHE.mkdir(parents=True, exist_ok=True)

SB_URL = "https://jubpovmsfvaqfnidozfh.supabase.co"
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
KL_START = int(pd.Timestamp("2026-06-01", tz="UTC").value // 1_000_000)


def _sb_key():
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("SUPABASE_KEY="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("SUPABASE_KEY no encontrada en .env")


# ------------------------------------------------------------------ fetch
def fetch_supabase():
    import httpx
    key = _sb_key()
    hdr = {"apikey": key, "Authorization": f"Bearer {key}"}
    out = {}
    for table, order in [("liquidity_paper_events", "at"), ("liquidity_paper_trades", "opened_at")]:
        rows, off = [], 0
        while True:
            r = httpx.get(f"{SB_URL}/rest/v1/{table}",
                          params={"limit": 1000, "offset": off, "order": f"{order}.asc"},
                          headers=hdr, timeout=60)
            r.raise_for_status()
            page = r.json()
            rows += page
            if len(page) < 1000:
                break
            off += 1000
        df = pd.DataFrame(rows)
        df.to_parquet(CACHE / f"{table}.parquet")
        out[table] = df
        print(f"  {table}: {len(df)} filas")
    return out


def fetch_klines():
    import httpx
    now_ms = int(time.time() * 1000)
    for sym in SYMS:
        for iv in ["15", "60", "240"]:
            rows, end = [], now_ms
            while True:
                r = httpx.get("https://api.bybit.com/v5/market/kline",
                              params={"category": "linear", "symbol": sym, "interval": iv,
                                      "start": KL_START, "end": end, "limit": 1000}, timeout=60)
                lst = r.json()["result"]["list"]
                if not lst:
                    break
                rows += lst
                oldest = int(lst[-1][0])
                if oldest <= KL_START or len(lst) < 1000:
                    break
                end = oldest - 1
            df = pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v", "turn"]).astype(float)
            df["ts"] = df["ts"].astype(np.int64)
            df = df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
            df.to_parquet(CACHE / f"kl_{sym}_{iv}.parquet")
            print(f"  {sym} M{iv}: {len(df)} velas  {pd.Timestamp(df.ts.iloc[0],unit='ms')} -> {pd.Timestamp(df.ts.iloc[-1],unit='ms')}")


# ------------------------------------------------------------------ features (compartidas paper/backtest)
def atr14(h, l, c):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).rolling(14).mean().values


def ema(x, n):
    return pd.Series(x).ewm(span=n, adjust=False).mean().values


def session_of(hour):
    if 7 <= hour < 13: return "london"
    if 13 <= hour < 21: return "ny"
    return "asia"


class Ctx:
    """Contexto de mercado por símbolo para calcular features en un ts arbitrario."""
    def __init__(self, m15, h1, h4):
        self.ts15, self.c15 = m15.ts.values, m15.c.values
        self.atr = atr14(m15.h.values, m15.l.values, m15.c.values)
        self.atr_med = pd.Series(self.atr).rolling(500, min_periods=100).median().shift(1).values
        self.ts60, self.e60 = h1.ts.values, ema(h1.c.values, 20)
        self.c60 = h1.c.values
        self.ts240, self.e240 = h4.ts.values, ema(h4.c.values, 20)
        self.c240 = h4.c.values

    def feats(self, ts_ms, side, level_price):
        i = np.searchsorted(self.ts15, ts_ms, "right") - 1   # última barra M15 abierta o cerrada en ts
        i = max(i - 1, 20)                                    # usar solo la última CERRADA
        atr, c = self.atr[i], self.c15[i]
        if not np.isfinite(atr) or atr <= 0:
            return None
        j1 = np.searchsorted(self.ts60, ts_ms, "right") - 2   # última H1 cerrada
        j4 = np.searchsorted(self.ts240, ts_ms, "right") - 2
        if j1 < 1 or j4 < 1:
            return None
        sgn = 1.0 if side == "long" else -1.0
        return dict(
            atr_ratio=atr / self.atr_med[i] if np.isfinite(self.atr_med[i]) else np.nan,
            dist_atr=abs(level_price - c) / atr,
            side_mom4=sgn * (c - self.c15[i - 4]) / atr,      # + = precio alejándose del nivel
            side_mom24=sgn * (c - self.c15[i - 24]) / atr,
            align_h1=sgn * np.sign(self.c60[j1] - self.e60[j1]),
            align_h4=sgn * np.sign(self.c240[j4] - self.e240[j4]),
            hour=int(pd.Timestamp(ts_ms, unit="ms").hour),
        )


def load_ctx():
    return {s: Ctx(pd.read_parquet(CACHE / f"kl_{s}_15.parquet"),
                   pd.read_parquet(CACHE / f"kl_{s}_60.parquet"),
                   pd.read_parquet(CACHE / f"kl_{s}_240.parquet")) for s in SYMS}


# ------------------------------------------------------------------ dataset places -> filled
def build_places():
    ev = pd.read_parquet(CACHE / "liquidity_paper_events.parquet")
    ev["at_ms"] = pd.to_datetime(ev["at"], utc=True, format="mixed").astype(np.int64) // 1_000_000
    ev = ev.sort_values("at_ms")
    places = ev[ev.event_type == "place"].copy()
    fills = ev[ev.event_type == "fill"].copy()

    # dedup por episodio: mismo (symbol, side, kind) y precio ±0.1% -> mismo episodio
    # si pasa >90 min sin re-place o el precio cambia, episodio nuevo
    eps = []
    for (sym, side, kind), g in places.groupby(["symbol", "side", "kind"]):
        g = g.sort_values("at_ms")
        cur = None
        for _, r in g.iterrows():
            if (cur is None or r.at_ms - cur["last_ms"] > 90 * 60_000
                    or abs(r.price - cur["price"]) / cur["price"] > 0.001):
                if cur: eps.append(cur)
                cur = dict(symbol=sym, side=side, kind=kind, price=r.price,
                           at_ms=r.at_ms, vol_regime=r.vol_regime, regime=r.regime,
                           gestion=r.gestion, last_ms=r.at_ms, n_places=1)
            else:
                cur["last_ms"] = r.at_ms; cur["n_places"] += 1
        if cur: eps.append(cur)
    df = pd.DataFrame(eps)

    # etiqueta: fill mismo symbol+side+kind, precio ±0.3%, entre inicio y last+24h
    fl = {k: g[["at_ms", "price"]].values for k, g in fills.groupby(["symbol", "side", "kind"])}
    def is_filled(r):
        arr = fl.get((r.symbol, r.side, r.kind))
        if arr is None: return 0
        m = (arr[:, 0] >= r.at_ms) & (arr[:, 0] <= r.last_ms + 24 * 3600_000) \
            & (np.abs(arr[:, 1] - r.price) / r.price < 0.003)
        return int(m.any())
    df["filled"] = df.apply(is_filled, axis=1)
    return df


def add_feats(df, ctx, ts_col="at_ms", price_col="price"):
    rows = []
    for _, r in df.iterrows():
        f = ctx[r.symbol].feats(int(r[ts_col]), r.side, r[price_col])
        rows.append(f or {})
    fdf = pd.DataFrame(rows, index=df.index)
    out = pd.concat([df, fdf], axis=1)
    out["session"] = out.hour.map(lambda h: session_of(int(h)) if np.isfinite(h) else None)
    return out


# ------------------------------------------------------------------ estadística
def wilson(k, n, z=1.96):
    if n == 0: return (0.0, 0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    ctr = (p + z * z / (2 * n)) / d
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, ctr - half), min(1.0, ctr + half)


def bucket_table(df, col, bins=None, labels=None):
    d = df.dropna(subset=[col]).copy()
    key = pd.cut(d[col], bins, labels=labels) if bins is not None else d[col]
    rows = []
    for b, g in d.groupby(key, observed=True):
        p, lo, hi = wilson(g.filled.sum(), len(g))
        rows.append(dict(bucket=str(b), n=len(g), fills=int(g.filled.sum()),
                         p_fill=round(p, 3), ci=f"[{lo:.3f},{hi:.3f}]"))
    return pd.DataFrame(rows)


FEATS = ["atr_ratio", "dist_atr", "side_mom4", "side_mom24", "align_h1", "align_h4"]


def fit_logit(df, feats=FEATS, lam=1.0):
    d = df.dropna(subset=feats + ["filled"])
    X = d[feats].values.astype(float)
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Xs = np.column_stack([np.ones(len(d)), (X - mu) / sd])
    y = d.filled.values.astype(float)
    w = np.zeros(Xs.shape[1])
    for _ in range(50):
        p = 1 / (1 + np.exp(-Xs @ w))
        W = p * (1 - p) + 1e-9
        R = np.eye(len(w)) * lam; R[0, 0] = 0
        H = Xs.T @ (Xs * W[:, None]) + R
        g = Xs.T @ (y - p) - R @ w
        step = np.linalg.solve(H, g)
        w += step
        if np.abs(step).max() < 1e-8: break
    return dict(w=w, mu=mu, sd=sd, feats=feats, n=len(d), idx=d.index)


def predict(model, df):
    X = df[model["feats"]].values.astype(float)
    Xs = np.column_stack([np.ones(len(df)), (X - model["mu"]) / model["sd"]])
    return 1 / (1 + np.exp(-Xs @ model["w"]))


# ------------------------------------------------------------------ stages
def stage_model():
    ctx = load_ctx()
    df = build_places()
    df = add_feats(df, ctx)
    df.to_parquet(CACHE / "places_labeled.parquet")
    n, k = len(df), df.filled.sum()
    print(f"\nEpisodios place dedup: {n}  |  fills: {k}  |  P(fill) global = {k/n:.3f}")
    print(f"(eventos place crudos: {df.n_places.sum()})")

    print("\n--- P(fill) por bucket ---")
    for name, col, bins, labels in [
        ("vol_regime", "vol_regime", None, None),
        ("kind", "kind", None, None),
        ("session", "session", None, None),
        ("symbol", "symbol", None, None),
        ("side", "side", None, None),
        ("atr_ratio", "atr_ratio", [0, 0.8, 1.0, 1.3, 10], ["<0.8", "0.8-1", "1-1.3", ">1.3"]),
        ("dist_atr", "dist_atr", [0, 0.5, 1.0, 2.0, 4.0, 100], ["<0.5", "0.5-1", "1-2", "2-4", ">4"]),
        ("side_mom4", "side_mom4", [-100, -1, 0, 1, 100], ["<-1", "-1-0", "0-1", ">1"]),
        ("side_mom24", "side_mom24", [-100, -2, 0, 2, 100], ["<-2", "-2-0", "0-2", ">2"]),
        ("align_h1", "align_h1", None, None),
        ("align_h4", "align_h4", None, None),
    ]:
        print(f"\n[{name}]")
        print(bucket_table(df, col, bins, labels).to_string(index=False))

    model = fit_logit(df)
    np.save(CACHE / "logit.npy", np.array([model], dtype=object), allow_pickle=True)
    print(f"\n--- Logística (n={model['n']}, ridge lam=1) coefs estandarizados ---")
    for f, c in zip(["intercept"] + model["feats"], model["w"]):
        print(f"  {f:<12} {c:+.3f}")
    d = df.dropna(subset=FEATS)
    p = predict(model, d)
    print(f"  P(fill) pred: media {p.mean():.3f}  rango [{p.min():.3f}, {p.max():.3f}]")
    # calibración por tercil
    q = pd.qcut(p, 3, labels=["bajo", "medio", "alto"], duplicates="drop")
    for b, g in d.groupby(q, observed=True):
        pp, lo, hi = wilson(g.filled.sum(), len(g))
        print(f"  tercil {b}: pred {p[q == b].mean():.3f}  real {pp:.3f} [{lo:.3f},{hi:.3f}]  n={len(g)}")

    # ---------------- selección adversa: trades reales
    tr = pd.read_parquet(CACHE / "liquidity_paper_trades.parquet")
    tr = tr[(tr.reconstructed != True) & tr.result_r.notna() & tr.placed_ts.notna()].copy()
    tr["placed_ts"] = tr.placed_ts.astype(np.int64)
    tr = add_feats(tr, ctx, ts_col="placed_ts", price_col="entry")
    tr = tr.dropna(subset=FEATS)
    tr["p_fill"] = predict(model, tr)
    tr.to_parquet(CACHE / "trades_scored.parquet")
    print(f"\n--- SELECCIÓN ADVERSA: trades reales (n={len(tr)}, excl. reconstructed) ---")
    print(f"  avgR global: {tr.result_r.mean():+.3f}")
    tr["p_bucket"] = pd.qcut(tr.p_fill, 3, labels=["P baja", "P media", "P alta"], duplicates="drop")
    g = tr.groupby("p_bucket", observed=True).agg(
        n=("result_r", "size"), avgR=("result_r", "mean"),
        wr=("win", "mean"), p_fill=("p_fill", "mean"), mfe=("mfe_r", "mean"))
    print(g.round(3).to_string())
    # correlación directa contexto -> resultado
    for f in FEATS + ["p_fill"]:
        c = np.corrcoef(tr[f], tr.result_r)[0, 1]
        print(f"  corr({f:<11}, result_r) = {c:+.3f}")
    return df, model


def stage_backtest():
    import _listas2 as L2
    from _strategy_ab import run_system, stats
    from _listas import OOS_MS
    from _audit_mirror import gen_h21_short

    model = np.load(CACHE / "logit.npy", allow_pickle=True)[0]
    PARQUETS = {
        "BTCUSDT": ROOT / "data/bybit-perp/processed/btcusdt_perp_m1.parquet",
        "ETHUSDT": Path("E:/bybit-data/bybit-perp-eth/processed/ethusdt_perp_m1.parquet"),
        "SOLUSDT": Path("E:/bybit-data/bybit-perp-sol/processed/solusdt_perp_m1.parquet"),
    }
    summary = []
    for sym in SYMS:
        if not PARQUETS[sym].exists():
            print(f"SKIP {sym}"); continue
        L2.M1 = PARQUETS[sym]
        t = L2.load2(15, start_ms=0); a = L2.A2(t)
        m1 = L2.load_m1_exit(start_ms=0)
        gens = [L2.gen_h5(), L2.gen_h21(), gen_h21_short()]
        df = run_system(a, gens, m1, 15, mode="fade", max_day=4, cooldown=3)

        # features de contexto por trade (mismas definiciones que el paper)
        atr_med = pd.Series(a.atr).rolling(500, min_periods=100).median().shift(1).values
        # H1/H4 sintéticos desde M15
        s15 = pd.Series(a.c, index=pd.to_datetime(a.ts, unit="ms"))
        h1c = s15.resample("1h").last().dropna(); h4c = s15.resample("4h").last().dropna()
        e60 = ema(h1c.values, 20); e240 = ema(h4c.values, 20)
        ts60 = h1c.index.asi8 // 1_000_000; ts240 = h4c.index.asi8 // 1_000_000

        rows = []
        for _, r in df.iterrows():
            i = int(r.bar); sgn = 1.0 if r.side == "long" else -1.0
            j1 = np.searchsorted(ts60, r.ts, "right") - 2
            j4 = np.searchsorted(ts240, r.ts, "right") - 2
            am = atr_med[i]
            rows.append(dict(
                atr_ratio=a.atr[i] / am if np.isfinite(am) and am > 0 else np.nan,
                dist_atr=abs(r.entry - a.c[i - 1]) / a.atr[i],
                side_mom4=sgn * (a.c[i - 1] - a.c[i - 5]) / a.atr[i],
                side_mom24=sgn * (a.c[i - 1] - a.c[i - 25]) / a.atr[i],
                align_h1=sgn * np.sign(h1c.values[j1] - e60[j1]) if j1 >= 1 else np.nan,
                align_h4=sgn * np.sign(h4c.values[j4] - e240[j4]) if j4 >= 1 else np.nan,
            ))
        df = pd.concat([df.reset_index(drop=True), pd.DataFrame(rows)], axis=1).dropna(subset=FEATS)
        df["p_fill"] = predict(model, df)
        df["w"] = df.p_fill / df.p_fill.mean()
        df.to_parquet(CACHE / f"bt_fade_{sym}.parquet")

        for tag, m in [("IS", df.ts < OOS_MS), ("OOS", df.ts >= OOS_MS)]:
            d = df[m]
            raw = d.r.mean()
            wgt = (d.r * d.w).sum() / d.w.sum()
            summary.append(dict(symbol=sym, period=tag, n=len(d), avgR_raw=raw,
                                avgR_pfill=wgt, delta=wgt - raw,
                                p_fill_mean=d.p_fill.mean()))
            print(f"  {sym} {tag:<3} n={len(d):>4}  avgR {raw:+.3f}  ->  ponderado P(fill) {wgt:+.3f}  (delta {wgt-raw:+.3f})")
        # avgR por tercil de p_fill (¿dónde vive el edge condicional a fill?)
        df["pb"] = pd.qcut(df.p_fill, 3, labels=["P baja", "P media", "P alta"], duplicates="drop")
        print(df.groupby(["pb"], observed=True).agg(n=("r", "size"), avgR=("r", "mean"),
              avgR_oos=("r", lambda x: df.loc[x.index][df.loc[x.index].oos].r.mean())).round(3).to_string())
    s = pd.DataFrame(summary)
    s.to_csv(CACHE / "bt_reweighted.csv", index=False)
    print("\n=== RESUMEN reponderación ===")
    print(s.round(3).to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["fetch", "model", "backtest", "all"], default="all")
    args = ap.parse_args()
    if args.stage in ("fetch", "all"):
        print("== FETCH supabase =="); fetch_supabase()
        print("== FETCH klines =="); fetch_klines()
    if args.stage in ("model", "all"):
        print("\n== MODEL =="); stage_model()
    if args.stage in ("backtest", "all"):
        print("\n== BACKTEST reponderado =="); stage_backtest()


if __name__ == "__main__":
    main()
