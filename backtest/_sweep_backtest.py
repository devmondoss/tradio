"""
_sweep_backtest.py — Stop Sweep Fade como estrategia independiente
Señal: equal_high_sweep (igual que ICT liquidity sweep)
  = equal_high reciente + wick barrió swing high + cerró debajo
  → SHORT: el stop hunt fallo, vendedores absorben compradores atrapados

Entrada: taker en close de la barra sweep
Stop:    high de la barra + 0.15% piso (maximo del wick del sweep)
Target:  VP VAL / prev_day_low / swing_low_50 (nivel estructural real)
"""
import sys, numpy as np, pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _listas import OOS_MS, FEE_TAKER, TICK_MS

ROOT = Path(__file__).parent.parent

# ─── Cargar datos ─────────────────────────────────────────────────────────────
cols = ['ts_ms','open','high','low','close','volume','delta',
        'equal_high_sweep','sweep_confirmed','pdh_sweep',
        'abs_ask','abs_bid','vr','atr14','cvd_slope','regime',
        'vp_poc','vp_vah','vp_val','prev_day_high','prev_day_low',
        'swing_high_50','swing_low_50','weekly_low','asian_low']

m1 = pd.read_parquet(ROOT / 'data/bybit-perp/processed/btcusdt_perp_m1.parquet', columns=cols)
m1 = m1[m1.ts_ms >= TICK_MS].sort_values('ts_ms').reset_index(drop=True)

# ─── ATR filter ───────────────────────────────────────────────────────────────
m1['atr_med'] = m1.atr14.rolling(500*15, min_periods=100).median()
m1['vol_ok']  = m1.atr14 > m1.atr_med

# ─── Parametros ───────────────────────────────────────────────────────────────
STOP_FLOOR_PCT  = 0.0015   # 0.15% mínimo de riesgo
MIN_RR          = 1.5      # ratio mínimo entry→target / entry→stop para tomar el trade
TIMEOUT_BARS    = 4*60     # 4h timeout en barras M1
COOLDOWN        = 30       # no repetir señal en 30 barras (~30min)
FEE             = FEE_TAKER / 2  # per side

# ─── Backtest ─────────────────────────────────────────────────────────────────
ts   = m1.ts_ms.values
op   = m1.open.values
hi   = m1.high.values
lo   = m1.low.values
cl   = m1.close.values
atr  = m1.atr14.values
vol  = m1.vol_ok.values

eq_sw  = m1.equal_high_sweep.values.astype(bool)
abs_ask= m1.abs_ask.values.astype(bool)
vr_arr = m1.vr.values
regime = m1.regime.values

# Targets estructurales para SHORT
vp_val  = m1.vp_val.values
pdl     = m1.prev_day_low.values
sl50    = m1.swing_low_50.values
wk_lo   = m1.weekly_low.values
as_lo   = m1.asian_low.values

trades = []
cool   = 0

for i in range(60, len(m1) - TIMEOUT_BARS - 1):
    if i < cool: continue
    if not eq_sw[i]: continue
    if not vol[i]: continue           # ATR filter

    # Entrada: SHORT en close de la barra sweep (taker)
    entry = cl[i]
    stop  = hi[i]                     # alto del wick del sweep
    stop_floor = entry * (1 + STOP_FLOOR_PCT)
    if stop < stop_floor:
        stop = stop_floor             # piso mínimo

    risk = stop - entry
    if risk <= 0: continue

    # Target: nivel estructural más cercano POR DEBAJO de entry
    candidates = {
        'vp_val':  vp_val[i],
        'pdl':     pdl[i],
        'sl50':    sl50[i],
        'wk_lo':   wk_lo[i],
        'as_lo':   as_lo[i],
    }
    valid_tgts = {k: v for k, v in candidates.items()
                  if np.isfinite(v) and v < entry}
    if not valid_tgts:
        continue
    # El más cercano (pero no demasiado cerca — mínimo MIN_RR)
    tgt_name = min(valid_tgts, key=lambda k: abs(valid_tgts[k] - entry))
    tgt = valid_tgts[tgt_name]
    rr = (entry - tgt) / risk
    if rr < MIN_RR:
        # Buscar siguiente más lejano que cumpla MIN_RR
        far = {k: v for k, v in valid_tgts.items() if (entry - v) / risk >= MIN_RR}
        if not far: continue
        tgt_name = min(far, key=lambda k: abs(far[k] - entry))
        tgt = far[tgt_name]
        rr = (entry - tgt) / risk

    # Simular salida en M1
    jend = min(i + 1 + TIMEOUT_BARS, len(m1))
    exit_px = None
    reason  = 'timeout'
    for j in range(i + 1, jend):
        if hi[j] >= stop:
            exit_px = stop; reason = 'stop'; break
        if lo[j] <= tgt:
            exit_px = tgt; reason = 'target'; break
    if exit_px is None:
        exit_px = cl[jend - 1]; reason = 'timeout'

    # R neto con fees (taker entrada + salida)
    fee_r = FEE * 2 * entry / risk
    r = (entry - exit_px) / risk - fee_r
    oos = ts[i] >= OOS_MS

    trades.append(dict(
        ts=ts[i], entry=entry, stop=stop, tgt=tgt, tgt_name=tgt_name,
        exit=exit_px, reason=reason, r=r, rr=rr, risk_pct=100*risk/entry,
        oos=oos, vr=vr_arr[i], abs_ask=bool(abs_ask[i]), regime=regime[i],
    ))
    cool = i + COOLDOWN

df = pd.DataFrame(trades)
print(f'Total trades: {len(df)} | OOS: {df.oos.sum()}')
print()

def stats(sub, label):
    if len(sub) == 0:
        print(f'{label}: sin trades'); return
    wr = (sub.r > 0).mean()
    avg = sub.r.mean()
    dd = 0; peak = 0; eq = 0
    for r in sub.r:
        eq += r; peak = max(peak, eq); dd = max(dd, peak - eq)
    n = len(sub)
    days = (sub.ts.max() - sub.ts.min()) / 86_400_000
    tpd = n / max(days, 1)
    print(f'{label:<35} n={n:>4}  avgR={avg:>+.3f}  WR={100*wr:.0f}%  DD={dd:.1f}R  {tpd:.2f}t/d')

stats(df,           'ALL')
stats(df[~df.oos],  'IS  (pre OOS_MS)')
stats(df[df.oos],   'OOS (post OOS_MS)')

print()
print('--- Filtros adicionales ---')
stats(df[df.oos & df.abs_ask],          'OOS + abs_ask')
stats(df[df.oos & (df.vr > 1.5)],       'OOS + vr>1.5')
stats(df[df.oos & df.abs_ask & (df.vr>1.5)], 'OOS + abs_ask + vr>1.5')

print()
print('--- Por reason ---')
for r, g in df[df.oos].groupby('reason'):
    print(f'  {r:<10} n={len(g):>3}  avgR={g.r.mean():>+.3f}  WR={100*(g.r>0).mean():.0f}%')

print()
print('--- Por target name ---')
for t, g in df[df.oos].groupby('tgt_name'):
    print(f'  {t:<10} n={len(g):>3}  avgR={g.r.mean():>+.3f}  WR={100*(g.r>0).mean():.0f}%')

print()
print('--- Mensual OOS ---')
oos = df[df.oos].copy()
oos['month'] = pd.to_datetime(oos.ts, unit='ms').dt.to_period('M')
for m, g in oos.groupby('month'):
    wr = (g.r > 0).mean()
    print(f'  {m}  n={len(g):>3}  avgR={g.r.mean():>+.3f}  WR={100*wr:.0f}%')
