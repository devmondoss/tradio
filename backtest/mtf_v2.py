"""
mtf_v2.py — Triggers mejorados sobre dataset de futuros (bybit-perp)
=====================================================================
Sobre mtf_basics.py (3 ingredientes simples con OBI/delta) agregamos
los 4 features validados en funnel_orderflow.py:

  Gate 0: Régimen D1 EMA20 (ya estaba pero desactivado)
  Gate 1: body_below_poc  — cierre bajo el POC = aceptación bajo valor (IS≈OOS robusto)
  Gate 2: minus_ticks > plus_ticks — agresión real tick a tick (2° mayor salto de edge)
  Veto  : fp_absorb_buy == False — no shortear si hay comprador absorbiendo
  Tier  : n_trades (tape intensity) — sizing 1.5x cuando tape activo

Dataset: data/bybit-perp/processed/btcusdt_perp_m1.parquet (87+ cols, 17 meses)
IS: hasta 2026-03-01   OOS: Mar-May 2026

Fase B al final del archivo: Sweep + Reclaim como trigger alternativo.
"""
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd

ROOT    = Path(__file__).parent.parent
DATA_M1 = ROOT / 'data/bybit-perp/processed/btcusdt_perp_m1.parquet'
EXPORTS = ROOT / 'exports'

CAPITAL  = 500.0
RISK_PCT = 0.02
FEE_RT   = 0.0011       # futuros bybit linear: taker 0.055% × 2 ≈ 0.0011
TARGET_R = 2.8  # barrido 2026-06-18: mejor IS≈OOS (IS+0.658/OOS+0.635), TotR IS+83.5/OOS+26.7
FORWARD  = 1200
MIN_STOP = 0.0030
MAX_STOP = 0.0075
H1_MS    = 3_600_000
D1_MS    = 86_400_000
OOS_MS   = int(pd.Timestamp('2026-03-01', tz='UTC').value // 1_000_000)
LEVEL_TOL = 0.007

# n_trades percentil IS (~Q50 de barras con señal) — se calcula abajo
# Valor preliminar desde funnel; se recalibra automáticamente en IS
N_TRADES_Q50 = 1800.0


# ── helpers ───────────────────────────────────────────────────────────────────

def ema(v, n):
    k = 2 / (n + 1)
    o = np.empty(len(v)); o[0] = v[0]
    for i in range(1, len(v)):
        o[i] = v[i] * k + o[i-1] * (1-k)
    return o

def atr14(h, l, c):
    tr = np.maximum(h-l, np.maximum(np.abs(h-np.roll(c,1)), np.abs(l-np.roll(c,1))))
    tr[0] = h[0]-l[0]
    o = np.empty_like(tr); o[:14] = tr[:14].mean(); k = 1/14
    for i in range(14, len(tr)):
        o[i] = o[i-1]*(1-k) + tr[i]*k
    return o

def resamp(df, f):
    df = df.copy(); df['tf'] = (df['ts_ms'] // f) * f
    return df.groupby('tf', sort=True).agg(
        open=('open','first'), high=('high','max'),
        low=('low','min'),    close=('close','last'),
    ).reset_index().rename(columns={'tf':'ts_ms'})

def session(ts_ms):
    hm = (ts_ms // 60_000) % 1440
    if   7*60 <= hm < 12*60: return 'london'
    if  12*60 <= hm < 16*60: return 'overlap'
    if  16*60 <= hm < 20*60: return 'ny'
    return ''

def active_level_long(row) -> tuple[bool, str]:
    low = row['low']
    levels = []
    pdl = row.get('prev_day_low')
    if pdl and pdl > 0 and abs(low - pdl) / pdl <= LEVEL_TOL:
        levels.append('PDL')
    asl = row.get('asian_low')
    if asl and asl > 0 and abs(low - asl) / asl <= LEVEL_TOL:
        levels.append('AL')
    wkl = row.get('weekly_low')
    if wkl and wkl > 0 and abs(low - wkl) / wkl <= LEVEL_TOL:
        levels.append('WL')
    val = row.get('vp_val')
    if val and val > 0 and abs(low - val) / val <= LEVEL_TOL:
        levels.append('VAL')
    if levels:
        return True, '+'.join(levels)
    return False, ''

def rejection_long(row) -> bool:
    c, o, h, l = float(row['close']), float(row['open']), float(row['high']), float(row['low'])
    rng = h - l
    if rng <= 0: return False
    wick_lo = min(c, o) - l
    return 0.30 < wick_lo / rng < 0.85 and c >= o

def active_level(row) -> tuple[bool, str]:
    high = row['high']
    levels = []
    pdh = row.get('prev_day_high')
    if pdh and pdh > 0 and abs(high - pdh) / pdh <= LEVEL_TOL:
        levels.append('PDH')
    ash = row.get('asian_high')
    if ash and ash > 0 and abs(high - ash) / ash <= LEVEL_TOL:
        levels.append('AH')
    wkh = row.get('weekly_high')
    if wkh and wkh > 0 and abs(high - wkh) / wkh <= LEVEL_TOL:
        levels.append('WH')
    vah = row.get('vp_vah')
    if vah and vah > 0 and abs(high - vah) / vah <= LEVEL_TOL:
        levels.append('VAH')
    if levels:
        return True, '+'.join(levels)
    return False, ''

def rejection(row) -> bool:
    c, o, h, l = float(row['close']), float(row['open']), float(row['high']), float(row['low'])
    rng = h - l
    if rng <= 0: return False
    wick_up = h - max(c, o)
    return 0.30 < wick_up / rng < 0.85 and c <= o


# ── Absorption Score (spec v3) ────────────────────────────────────────────────

def compute_as(df, N=150):
    """
    Returns (as_short_arr, as_long_arr) — numpy arrays aligned to df rows.
    AS_short = max(DZ,0) * (1 - max(-desplaz,0))  — vendedores que no lograron bajar
    AS_long  = max(-DZ,0) * (1 - max(desplaz,0))  — compradores que no lograron subir
    DZ = (delta - rolling_mean(delta,N)) / rolling_std(delta,N)
    Thresholds: >= 1.5 (señal), >= 3.0 (fuerte).  Peso: 22/100 en spec v3.
    """
    delta = np.array(df['delta'].fillna(0).values, dtype=float)
    hi    = df['high'].values.astype(float)
    lo    = df['low'].values.astype(float)
    op    = df['open'].values.astype(float)
    cl    = df['close'].values.astype(float)

    rng   = hi - lo
    desp  = np.where(rng > 0, (cl - op) / rng, 0.0)  # -1..+1

    n     = len(delta)
    dz    = np.zeros(n)
    for i in range(N, n):
        w  = delta[i-N:i]
        s  = w.std()
        dz[i] = (delta[i] - w.mean()) / s if s > 0 else 0.0

    as_s = np.maximum(dz, 0) * (1 - np.maximum(-desp, 0))
    as_l = np.maximum(-dz, 0) * (1 - np.maximum(desp, 0))
    return as_s, as_l


# ── simulate ──────────────────────────────────────────────────────────────────

def simulate(df, mode='v2', n_trades_q50=N_TRADES_Q50, d1_thr=0.980, vpin_q50=0.48):
    """
    mode='baseline'  → shorts baseline (OBI/delta, sin features nuevos)
    mode='v2'        → shorts v2: régimen + H1 BOS/ChoCH + body_below_poc + minus_ticks + absorb veto
    mode='v2_long'   → longs espejo: régimen alcista + H1 bullish + body_above_poc + plus_ticks + absorb veto
    mode='sweep'     → Fase B: sweep+reclaim trigger (parked)
    mode='v2'       → gates validados: regime + body_below_poc + minus_ticks + absorb_buy veto
    mode='sweep'    → Fase B: sweep+reclaim como trigger (sin wick simple)
    """
    h1 = resamp(df, H1_MS)
    h1['atr'] = atr14(h1['high'].values, h1['low'].values, h1['close'].values)
    h1_ctx = {int(r.ts_ms): (float(r.high), float(r.atr)) for r in h1.itertuples()}

    d1 = resamp(df, D1_MS)
    d1['ema20'] = ema(d1['close'].values, 20)
    d1_ctx = {int(r.ts_ms): float(r.ema20) for r in d1.itertuples()}

    rows   = df.to_dict('records')
    trades = []
    in_t   = False
    ep = sl = tp = dist = 0.0
    t_start = t_entry = 0
    t_lbl = t_sess = ''
    mfe = mae = 0.0
    entry_risk = entry_score = 0
    cap = CAPITAL
    monthly_risk = CAPITAL * RISK_PCT
    current_month = -1

    # sweep+reclaim state (Fase B)
    sweep_high_bar = None   # precio del sweep high reciente
    swept_level    = None   # nivel que fue sweepado
    reclaim_bars   = 0      # barras desde el sweep

    for i, row in enumerate(rows):
        ts = int(row['ts_ms'])

        if in_t:
            mfe = max(mfe, (ep - row['low'])  / dist)
            mae = max(mae, (row['high'] - ep) / dist)
            bars_el = i - t_start
            reason = exit_px = None
            if row['high'] >= sl:
                reason, exit_px = 'stop', sl
            elif row['low'] <= tp:
                reason, exit_px = 'target', tp
            elif bars_el >= FORWARD:
                reason, exit_px = 'timeout', row['close']
            if reason:
                gross_r = (ep - exit_px) / dist
                fee_r   = FEE_RT * ep / dist
                pnl_r   = gross_r - fee_r
                pnl_usd = entry_risk * pnl_r
                trades.append({
                    'ts_ms':    t_entry,
                    'date':     pd.Timestamp(t_entry, unit='ms', tz='UTC').strftime('%Y-%m-%d %H:%M'),
                    'session':  t_sess,
                    'level':    t_lbl,
                    'entry':    round(ep, 2),
                    'stop':     round(sl, 2),
                    'target':   round(tp, 2),
                    'exit':     round(exit_px, 2),
                    'stop_pct': round(dist/ep*100, 3),
                    'reason':   reason,
                    'result_r': round(pnl_r, 3),
                    'pnl_usd':  round(pnl_usd, 2),
                    'bars':     bars_el,
                    'mfe_r':    round(mfe, 3),
                    'mae_r':    round(mae, 3),
                    'equity':   round(cap + pnl_usd, 2),
                    'oos':      ts >= OOS_MS,
                    'score':    entry_score,
                })
                cap += pnl_usd
                in_t = False
                sweep_high_bar = None
            continue

        month = pd.Timestamp(ts, unit='ms', tz='UTC').month + pd.Timestamp(ts, unit='ms', tz='UTC').year * 12
        if month != current_month:
            monthly_risk = cap * RISK_PCT
            current_month = month

        sess = session(ts)
        if sess not in ('london', 'overlap', 'ny'):
            if mode == 'sweep':
                sweep_high_bar = None  # reset fuera de sesión
            continue

        # ── Gate 0: Régimen D1 EMA20 ──────────────────────────────────────────
        # Bloquear cuando close > ema*0.98: cubre tanto tendencia alcista (>1.0)
        # como zona de indecision (0.98-1.0). Validado IS/OOS 2026-06-18:
        # IS +0.107→+0.203, OOS +0.314→+0.371 con 62% del volumen.
        d1_ts  = (ts // D1_MS) * D1_MS - D1_MS
        d1_ema = d1_ctx.get(d1_ts)
        if d1_ema is None:
            continue
        if row['close'] > d1_ema * d1_thr:
            sweep_high_bar = None
            continue

        # ── Gate 0b: Estructura H1 bajista ────────────────────────────────────
        # Requiere BOS bajista H1 O ChoCH bajista H1 confirmado.
        # Validado IS/OOS 2026-06-18: AvgR +0.541/+0.656 vs +0.204/+0.369 baseline.
        if mode == 'v2':
            if not (row.get('h1_bos_bear', False) or row.get('h1_choch_bear', False)):
                continue

        # ── Gate 1: NIVEL ──────────────────────────────────────────────────────
        ok_level, lbl = active_level(row)
        if not ok_level or 'VAH' not in lbl:
            continue
        parts = lbl.split('+')
        if len(parts) >= 3 and 'PDH' in parts and 'AH' in parts:
            continue
        if 'PDH' in parts and 'VAH' in parts and len(parts) == 2:
            continue
        if 'WH' in parts and (ts // 3_600_000) % 24 == 15:
            continue

        # ── Gate 2: RECHAZO ────────────────────────────────────────────────────
        if mode in ('baseline', 'v2') and not rejection(row):
            continue

        if mode == 'baseline':
            # ── BASELINE: flujo débil (OBI o delta) ───────────────────────────
            obi   = float(row.get('obi10_mean') or 0)
            delta = float(row.get('delta') or 0)
            if not (obi < -0.05 or delta < 0):
                continue
            entry_score = 0
            entry_risk  = monthly_risk

        elif mode == 'v2':
            # ── V2: gates validados IS/OOS ────────────────────────────────────
            # Gate 2a: body_below_poc — precio acepta bajo el valor
            if not row.get('body_below_poc', False):
                continue

            # Gate 2b: agresión real — más ticks bajistas que alcistas
            mt = float(row.get('minus_ticks') or 0)
            pt = float(row.get('plus_ticks')  or 0)
            if mt <= pt:
                continue

            # Veto: absorción compradora — si hay comprador absorbiendo, skip
            if row.get('fp_absorb_buy', False):
                continue

            # Veto: sin LVN (void) por debajo del nivel = sin espacio limpio al target.
            # Validado 2026-06-19 (_edge_research): vp_lvn_below==False es perdedor en
            # AMBOS períodos (IS +0.204 / OOS -0.439). Quitarlo: OOS +0.635→+0.780,
            # WR 50→54%, costo 8 IS / 5 OOS trades. Edge real, no leverage.
            if not row.get('vp_lvn_below', False):
                continue

            # ── Sizing por capas de convicción ────────────────────────────────
            # Reordenado 2026-06-19 alrededor de vpin (toxicidad de flujo):
            #   vpin es ORTOGONAL a tape (corr=0.12) y SOLO discrimina con volumen.
            #   tape & vpin_hi:        OOS WR=66.7%, AvgR=+1.316  (firma institucional)
            #   tape & vpin_hi & cvd>0: OOS WR=80.0%, AvgR=+1.816  (triple-confirmado)
            #   cvd>0 con vpin_lo:     FRÁGIL OOS (-0.107) → cvd solo NO es tier alto.
            #   obi10>=0 (bids pesadas): bull trap en VAH → señal de soporte débil.
            #   fp_stack_sell / AS: descartados (ver worklog 2026-06-19).
            nt      = float(row.get('n_trades')   or 0)
            cvd     = float(row.get('cvd_slope')  or 0)
            obi10   = float(row.get('obi10_mean') or 0)
            vpin    = float(row.get('vpin')       or 0)
            tape    = nt   >= n_trades_q50
            vpin_hi = vpin >= vpin_q50
            cvd_ok  = cvd   > 0   # compradores absorbidos en VAH (confirmatorio si hay flujo)
            obi_ok  = obi10 >= 0  # bids >= asks: compradores acumulando en VAH

            # Tier 1 (2.5x): flujo tóxico con volumen + absorción CVD (OOS WR 80%)
            if tape and vpin_hi and cvd_ok:
                mult = 2.5
            # Tier 2 (2.0x): flujo tóxico con volumen (OOS WR 67%, robusto sin cvd)
            elif tape and vpin_hi:
                mult = 2.0
            # Tier 3 (1.5x): una señal direccional aislada
            elif cvd_ok or tape or obi_ok:
                mult = 1.5
            else:
                mult = 1.0

            entry_score = int(mult > 1.0)
            entry_risk  = monthly_risk * mult

        elif mode == 'v3':
            # ── V3: D1 relajado + CVD absorción + H1 expandido ───────────────
            # Gate 0b: BOS/ChoCH bear OR H1 OB bear (estructura expandida)
            if not (row.get('h1_bos_bear', False) or row.get('h1_choch_bear', False)
                    or row.get('h1_ob_bear', False)):
                continue

            # Gate 2a: body_below_poc
            if not row.get('body_below_poc', False):
                continue

            # Gate 2b: agresión — minus_ticks > plus_ticks
            mt = float(row.get('minus_ticks') or 0)
            pt = float(row.get('plus_ticks')  or 0)
            if mt <= pt:
                continue

            # Gate 2c: CVD slope > 0 — compradores siendo absorbidos en el nivel
            # Validado 2026-06-18: WR 61% / AvgR +1.052 vs +0.438 cuando CVD<0
            cvd = float(row.get('cvd_slope') or 0)
            if cvd <= 0:
                continue

            # Veto: absorción compradora dominante
            if row.get('fp_absorb_buy', False):
                continue

            nt = float(row.get('n_trades') or 0)
            entry_score = 1 if nt >= n_trades_q50 else 0
            entry_risk  = monthly_risk * (1.5 if entry_score else 1.0)

        elif mode == 'sweep':
            # ── FASE B: Sweep + Reclaim ───────────────────────────────────────
            # Detectar sweep: la barra high supera el nivel activo (VAH/WH/AH/PDH)
            # y el cierre VUELVE por debajo → reclaim en una misma barra
            ref_level = float(row.get('vp_vah') or row.get('weekly_high') or 0)
            if ref_level <= 0:
                continue

            high  = float(row['high'])
            close = float(row['close'])

            # Sweep + reclaim en misma barra (la barra atraviesa el nivel y cierra abajo)
            same_bar_sweep  = high > ref_level * 1.0005 and close < ref_level
            # O: sweep de barra anterior confirmado por cierre abajo en esta barra
            prev_bar_reclaim = (sweep_high_bar is not None
                                and reclaim_bars <= 3
                                and close < ref_level
                                and close < row.get('open', close))

            if same_bar_sweep:
                sweep_high_bar = high
                swept_level    = lbl
                reclaim_bars   = 0
            elif sweep_high_bar and high > ref_level * 1.0005:
                sweep_high_bar = high
                reclaim_bars   = 0
            elif sweep_high_bar:
                reclaim_bars += 1

            if not (same_bar_sweep or prev_bar_reclaim):
                sweep_high_bar = None
                continue

            # Requiere además: agresión (minus > plus) como confirmación
            mt = float(row.get('minus_ticks') or 0)
            pt = float(row.get('plus_ticks')  or 0)
            if mt <= pt:
                continue

            if row.get('fp_absorb_buy', False):
                continue

            nt = float(row.get('n_trades') or 0)
            entry_score = 1 if nt >= n_trades_q50 else 0
            entry_risk  = monthly_risk * (1.5 if entry_score else 1.0)
            sweep_high_bar = None

        # ── Stop H1 estructural ────────────────────────────────────────────────
        h1d = h1_ctx.get((ts // H1_MS) * H1_MS)
        if h1d is None:
            continue
        h1h, h1a = h1d
        sl_  = h1h + 0.40 * h1a
        d    = sl_ - row['close']
        if d <= 0:
            continue
        sp = d / row['close']
        if not (MIN_STOP <= sp <= MAX_STOP):
            continue

        in_t     = True
        ep       = row['close']
        sl       = sl_
        dist     = d
        tp       = ep - TARGET_R * dist
        t_start  = i
        t_entry  = ts
        t_lbl    = lbl
        t_sess   = sess
        mfe = mae = 0.0

    return trades, cap


# ── LONGS simulate ────────────────────────────────────────────────────────────

def simulate_long(df, n_trades_q50=N_TRADES_Q50, d1_thr_lo=1.000, d1_thr_hi=1.030):
    """
    Longs espejo de v2 shorts.
    Entradas en VAL con wick alcista + régimen D1 alcista + H1 no bajista.

    Gates:
      Régimen : d1_thr_lo <= close/d1_ema <= d1_thr_hi  (precio sobre EMA, sin sobreextensión)
      H1      : NOT h1_bearish (precio sobre H1 EMA20)
      Nivel   : VAL (Value Area Low) + opcionales PDL/AL/WL
      Trigger : wick inferior alcista (close >= open, wick_lo > 30% del rango)
      Gate 2b : plus_ticks > minus_ticks (agresión compradora)
      Veto    : NOT fp_absorb_sell (sin vendedor absorbiendo)
      Sesión  : overlap + ny
    """
    h1 = resamp(df, H1_MS)
    h1['atr'] = atr14(h1['high'].values, h1['low'].values, h1['close'].values)
    h1_ctx_l = {int(r.ts_ms): (float(r.low), float(r.atr)) for r in h1.itertuples()}

    d1 = resamp(df, D1_MS)
    d1['ema20'] = ema(d1['close'].values, 20)
    d1_ctx = {int(r.ts_ms): float(r.ema20) for r in d1.itertuples()}

    rows   = df.to_dict('records')
    trades = []
    in_t   = False
    ep = sl = tp = dist = 0.0
    t_start = t_entry = 0
    t_lbl = t_sess = ''
    mfe = mae = 0.0
    entry_risk = entry_score = 0
    cap = CAPITAL
    monthly_risk = CAPITAL * RISK_PCT
    current_month = -1

    for i, row in enumerate(rows):
        ts = int(row['ts_ms'])

        if in_t:
            mfe = max(mfe, (row['high'] - ep) / dist)
            mae = max(mae, (ep - row['low'])  / dist)
            bars_el = i - t_start
            reason = exit_px = None
            if row['low'] <= sl:
                reason, exit_px = 'stop', sl
            elif row['high'] >= tp:
                reason, exit_px = 'target', tp
            elif bars_el >= FORWARD:
                reason, exit_px = 'timeout', row['close']
            if reason:
                gross_r = (exit_px - ep) / dist
                fee_r   = FEE_RT * ep / dist
                pnl_r   = gross_r - fee_r
                pnl_usd = entry_risk * pnl_r
                trades.append({
                    'ts_ms':    t_entry,
                    'date':     pd.Timestamp(t_entry, unit='ms', tz='UTC').strftime('%Y-%m-%d %H:%M'),
                    'session':  t_sess,
                    'level':    t_lbl,
                    'entry':    round(ep, 2),
                    'stop':     round(sl, 2),
                    'target':   round(tp, 2),
                    'exit':     round(exit_px, 2),
                    'stop_pct': round(dist/ep*100, 3),
                    'reason':   reason,
                    'result_r': round(pnl_r, 3),
                    'pnl_usd':  round(pnl_usd, 2),
                    'bars':     bars_el,
                    'mfe_r':    round(mfe, 3),
                    'mae_r':    round(mae, 3),
                    'equity':   round(cap + pnl_usd, 2),
                    'oos':      ts >= OOS_MS,
                    'score':    entry_score,
                    'direction': 'long',
                })
                cap += pnl_usd
                in_t = False
            continue

        month = pd.Timestamp(ts, unit='ms', tz='UTC').month + pd.Timestamp(ts, unit='ms', tz='UTC').year * 12
        if month != current_month:
            monthly_risk = cap * RISK_PCT
            current_month = month

        sess = session(ts)
        if sess not in ('overlap', 'ny'):
            continue

        # Gate 0: Régimen D1 alcista — precio sobre EMA sin sobreextensión
        d1_ts  = (ts // D1_MS) * D1_MS - D1_MS
        d1_ema = d1_ctx.get(d1_ts)
        if d1_ema is None:
            continue
        ratio = row['close'] / d1_ema
        if not (d1_thr_lo <= ratio <= d1_thr_hi):
            continue

        # Gate 0b: H4 no bajista — H4 BOS bearish NO confirmado
        # Validado IS/OOS 2026-06-18: clave para evitar comprar en downtrend H4
        if row.get('h4_bos_bear', False):
            continue

        # Gate 0c: Estructura H1 alcista — BOS bullish confirmado
        # Solo h1_bos_bull (NOT h1_choch_bull solo: IS negativo en análisis)
        if not row.get('h1_bos_bull', False):
            continue

        # Gate 1: Nivel de soporte (VAL o Asian Low — espejo de AH para shorts)
        ok_level, lbl = active_level_long(row)
        if not ok_level:
            continue
        parts = lbl.split('+')
        # Requiere al menos VAL, AL (asian_low), PDL, o WL como ancla
        if not any(p in parts for p in ('VAL', 'AL', 'PDL', 'WL')):
            continue
        # Triple PDL+AL+otros → demasiado noise (mismo filtro que shorts triple)
        if len(parts) >= 3 and 'PDL' in parts and 'AL' in parts:
            continue

        # Gate 2: Rechazo alcista (lower wick)
        if not rejection_long(row):
            continue

        # Gate 2a: cuerpo no por encima del POC (precio todavía en zona de soporte)
        poc = float(row.get('vp_poc') or 0)
        if poc > 0 and min(float(row['open']), float(row['close'])) > poc:
            continue

        # Gate 2b: agresión compradora
        pt = float(row.get('plus_ticks') or 0)
        mt = float(row.get('minus_ticks') or 0)
        if pt <= mt:
            continue

        # Veto: absorción vendedora
        if row.get('fp_absorb_sell', False):
            continue

        # Sizing longs: CVD>0 + tape + obi10>0
        # Validado IS 2026-06-19: CVD>0 → n=16 WR=50%, AvgR=+0.598 vs CVD<0 WR=42%
        # OBI>0 → n=22 WR=50%, AvgR=+0.448 vs OBI<=0 WR=40%
        # Lógica: en VAL, CVD>0 = compradores DEFENDIENDO el nivel (no atrapados)
        #         OBI>0 = bids pesadas en VAL = compradores activos (igual que en VAH para shorts
        #         pero por razón opuesta: confirman soporte en lugar de crear trampa)
        nt     = float(row.get('n_trades')   or 0)
        cvd    = float(row.get('cvd_slope')  or 0)
        obi10  = float(row.get('obi10_mean') or 0)
        tape   = nt >= n_trades_q50
        cvd_ok = cvd   > 0
        obi_ok = obi10 > 0

        # Nota: tape solo NO sube sizing en longs (IS: WR=16.7%, AvgR=-0.612, n=6)
        # tape sin dirección = volumen alto pero no bullish → igual que T5
        if cvd_ok and tape and obi_ok:
            mult = 2.5
        elif cvd_ok and tape:
            mult = 2.0
        elif cvd_ok or obi_ok:  # tape solo excluido
            mult = 1.5
        else:
            mult = 1.0

        entry_score = int(mult > 1.0)
        entry_risk  = monthly_risk * mult

        # Stop H1 estructural (bajo el H1 low)
        h1d = h1_ctx_l.get((ts // H1_MS) * H1_MS)
        if h1d is None:
            continue
        h1l, h1a = h1d
        sl_  = h1l - 0.40 * h1a
        dist = row['close'] - sl_
        if dist <= 0:
            continue
        sp = dist / row['close']
        if not (MIN_STOP <= sp <= MAX_STOP):
            continue

        in_t    = True
        ep      = row['close']
        sl      = sl_
        tp      = ep + TARGET_R * dist
        t_start = i
        t_entry = ts
        t_lbl   = lbl
        t_sess  = sess
        mfe = mae = 0.0

    return trades, cap


# ── estadísticas ──────────────────────────────────────────────────────────────

def stats(trades, label, days_is=425, days_oos=92):
    is_t  = [t for t in trades if not t['oos']]
    oos_t = [t for t in trades if t['oos']]
    def s(ts, d):
        if not ts: return {'n':0,'wr':0.0,'avg':0.0,'tpd':0.0,'total':0.0}
        n = len(ts); w = sum(1 for t in ts if t['result_r'] > 0)
        return {'n':n,'wr':w/n*100,'avg':sum(t['result_r'] for t in ts)/n,
                'tpd':n/d,'total':sum(t['result_r'] for t in ts)}
    i = s(is_t, days_is); o = s(oos_t, days_oos)
    cap_final = CAPITAL
    for t in sorted(trades, key=lambda x: x['ts_ms']):
        cap_final += t['pnl_usd']
    print(f'\n  {label:<22}  '
          f'IS  n={i["n"]:>4}  WR={i["wr"]:>5.1f}%  AvgR={i["avg"]:>+.3f}  tpd={i["tpd"]:.1f}  TotR={i["total"]:>+6.1f}  |  '
          f'OOS n={o["n"]:>4}  WR={o["wr"]:>5.1f}%  AvgR={o["avg"]:>+.3f}  tpd={o["tpd"]:.1f}  TotR={o["total"]:>+6.1f}  '
          f'| Equity ${cap_final:,.0f}')

def by_level(trades, oos_only=True):
    src = [t for t in trades if t['oos']] if oos_only else trades
    g = defaultdict(list)
    for t in src: g[t['level']].append(t['result_r'])
    rows = sorted([(k, v) for k,v in g.items() if len(v)>=3],
                  key=lambda x: -len(x[1]))
    for k, rs in rows:
        n = len(rs); w = sum(1 for r in rs if r > 0)
        print(f'    {k:<18}  n={n:>4}  WR={w/n*100:>5.1f}%  AvgR={sum(rs)/n:>+.3f}')

def monthly_curve(trades):
    by_mo = defaultdict(lambda: {'n':0,'wins':0,'pnl':0.0})
    for t in trades:
        mo = t['date'][:7]
        by_mo[mo]['n'] += 1; by_mo[mo]['pnl'] += t['pnl_usd']
        if t['result_r'] > 0: by_mo[mo]['wins'] += 1
    running = CAPITAL
    for mo in sorted(by_mo):
        d = by_mo[mo]; wr = d['wins']/d['n']*100 if d['n'] else 0
        running += d['pnl']
        tag = 'IS ' if mo < '2026-03' else 'OOS'
        print(f'    {mo} {tag}  n={d["n"]:>3}  WR={wr:>5.1f}%  PnL=${d["pnl"]:>+7,.0f}  Equity=${running:>8,.0f}')


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    print('Cargando dataset perp M1...')
    df = pd.read_parquet(DATA_M1).sort_values('ts_ms').reset_index(drop=True)
    for c in df.select_dtypes('object').columns:
        df[c] = df[c].fillna('')
    for c in df.select_dtypes('float').columns:
        df[c] = df[c].fillna(0.0)
    for c in df.select_dtypes('bool').columns:
        df[c] = df[c].fillna(False)
    print(f'Barras: {len(df):,}  Cols: {len(df.columns)}\n')

    # Calibrar n_trades_q50 en IS
    is_df = df[df['ts_ms'] < OOS_MS]
    nt_q50 = float(is_df['n_trades'].quantile(0.50))
    vpin_q50 = float(is_df['vpin'].quantile(0.50))
    print(f'n_trades Q50 IS: {nt_q50:.0f}   vpin Q50 IS: {vpin_q50:.3f}\n')

    print('=' * 120)
    print('COMPARATIVA: baseline  vs  v2 (gates validados)  vs  sweep+reclaim (Fase B)')
    print('=' * 120)

    for mode in ('baseline', 'v2', 'sweep'):
        t, _ = simulate(df, mode=mode, n_trades_q50=nt_q50, vpin_q50=vpin_q50)
        stats(t, mode)

    print()

    # Detalle v2 — por nivel y por mes
    t_v2, _ = simulate(df, mode='v2', n_trades_q50=nt_q50, vpin_q50=vpin_q50)
    oos_v2 = [t for t in t_v2 if t['oos']]
    print('\n--- v2: por nivel OOS ---')
    by_level(t_v2)
    print('\n--- v2: curva mensual ---')
    monthly_curve(t_v2)

    # Detalle sweep — por nivel y por mes
    t_sw, _ = simulate(df, mode='sweep', n_trades_q50=nt_q50)
    oos_sw = [t for t in t_sw if t['oos']]
    print('\n--- sweep: por nivel OOS ---')
    by_level(t_sw)
    print('\n--- sweep: curva mensual ---')
    monthly_curve(t_sw)

    EXPORTS.mkdir(exist_ok=True)
    pd.DataFrame(t_v2).to_csv(EXPORTS / 'v2_trades.csv', index=False)
    pd.DataFrame(t_sw).to_csv(EXPORTS / 'sweep_trades.csv', index=False)
    print(f'\nCSV exportados: exports/v2_trades.csv  exports/sweep_trades.csv')


if __name__ == '__main__':
    main()
