"""
_feature_discriminant.py
Analisis estadistico completo: que features en el momento de entrada
discriminan trades ganadores de perdedores.
No horas — condiciones reales.
"""
import pandas as pd, numpy as np
from collections import defaultdict
from scipy import stats as scipy_stats

CAPITAL=500.0; RISK_PCT=0.02; FEE_RT=0.0007; FORWARD=1200
MIN_STOP=0.003; MAX_STOP=0.0075; H1_MS=3_600_000
OOS_MS=int(pd.Timestamp('2026-03-01',tz='UTC').value//1_000_000)
LEVEL_TOL=0.007

def atr14(h,l,c):
    tr=np.maximum(h-l,np.maximum(np.abs(h-np.roll(c,1)),np.abs(l-np.roll(c,1)))); tr[0]=h[0]-l[0]
    o=np.empty_like(tr); o[:14]=tr[:14].mean(); k=1/14
    for i in range(14,len(tr)): o[i]=o[i-1]*(1-k)+tr[i]*k
    return o

def resamp(df,f):
    df=df.copy(); df['tf']=(df['ts_ms']//f)*f
    return df.groupby('tf',sort=True).agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last')).reset_index().rename(columns={'tf':'ts_ms'})

def session(ts):
    hm=(ts//60_000)%1440
    if  7*60<=hm<12*60: return 'london'
    if 12*60<=hm<16*60: return 'overlap'
    if 16*60<=hm<20*60: return 'ny'
    return ''

def simulate_collect(df):
    """Simula sin bloqueos de hora para capturar TODOS los trades y sus features."""
    h1=resamp(df,H1_MS); h1['atr']=atr14(h1['high'].values,h1['low'].values,h1['close'].values)
    h1_ctx={int(r.ts_ms):(float(r.low),float(r.atr)) for r in h1.itertuples()}
    rows=df.to_dict('records'); trades=[]; in_t=False
    ep=sl=tp=dist=0.0; t_start=t_entry=0; t_lbl=t_sess=''; cvd_streak=0
    cap=CAPITAL; monthly_risk=CAPITAL*RISK_PCT; current_month=-1

    for i,row in enumerate(rows):
        ts=int(row['ts_ms'])
        if in_t:
            obi=float(row.get('obi10_mean') or 0); cs=float(row.get('cvd_slope') or 0)
            cvd_streak=(cvd_streak+1) if cs<0 else 0
            cur_r=(row['close']-ep)/dist; reason=None; exit_px=0.0
            if row['low']<=sl:       reason,exit_px='stop',sl
            elif row['high']>=tp:    reason,exit_px='target',tp
            elif i-t_start>=FORWARD: reason,exit_px='timeout',row['close']
            elif cvd_streak>=3 and obi<-0.15 and cur_r>=1.0: reason,exit_px='cvd_exit',row['close']
            if reason:
                pnl_r=(exit_px-ep)/dist
                pnl_usd=monthly_risk*pnl_r-monthly_risk*FEE_RT
                trades.append({'ts_ms':t_entry,'result_r':round(pnl_r,3),'oos':ts>=OOS_MS,'level':t_lbl})
                cap+=pnl_usd; in_t=False; cvd_streak=0
            continue

        month=pd.Timestamp(ts,unit='ms',tz='UTC').month+pd.Timestamp(ts,unit='ms',tz='UTC').year*12
        if month!=current_month: monthly_risk=cap*RISK_PCT; current_month=month

        sess=session(ts)
        if not sess: continue

        low=row['low']; levels=[]
        for k,col in [('PDL','prev_day_low'),('AL','asian_low'),('WL','weekly_low'),('VAL','vp_val')]:
            v=row.get(col)
            if v and v>0 and abs(low-v)/v<=LEVEL_TOL: levels.append(k)
        if not levels: continue
        lbl='+'.join(levels)
        if 'VAL' not in lbl: continue
        parts=lbl.split('+')
        if len(parts)>=3 and 'PDL' in parts and 'AL' in parts: continue

        c,o,h,l=float(row['close']),float(row['open']),float(row['high']),float(row['low'])
        rng=h-l
        if rng<=0: continue
        if not (0.30<(min(c,o)-l)/rng<0.85) or c<o: continue
        obi=float(row.get('obi10_mean') or 0); delta=float(row.get('delta') or 0)
        if obi<=0.05 and delta<=0: continue
        h1d=h1_ctx.get((ts//H1_MS)*H1_MS)
        if h1d is None: continue
        h1l,h1a=h1d; sl_=h1l-0.40*h1a; d=row['close']-sl_
        if d<=0: continue
        if not (MIN_STOP<=d/row['close']<=MAX_STOP): continue
        reg=str(row.get('regime') or '')
        tgt=1.5 if reg=='Chop' else (3.0 if reg=='Expansion' else 2.0)
        in_t=True; ep=row['close']; sl=sl_; dist=d; tp=ep+tgt*dist
        t_start=i; t_entry=ts; t_lbl=lbl; cvd_streak=0; t_sess=sess

    return trades

print('Cargando M1...')
df=pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
for c in df.columns:
    if df[c].dtype==object: df[c]=df[c].fillna('')
    elif df[c].dtype==float: df[c]=df[c].fillna(0.0)

print('Simulando...')
trades=simulate_collect(df)
print(f'Total trades: {len(trades)}')

# Merge con features de entrada
feat_cols = [
    # OBI
    'obi10_mean','obi10_min','obi10_max','obi_range','obi5_mean','obi20_mean',
    # Flujo
    'delta','buy_vol','sell_vol','vr','cvd_slope','cvd_consec_pos','cvd_consec_neg',
    'prev_bar_delta','big_trade_bullish','big_trade_bearish','cvd_div',
    # Orderbook microestructura
    'ask_wall','bid_wall','thin_above','thin_below','abs_ask','abs_bid',
    'max_ask5','max_bid5','near5_ask','near5_bid','spread_mean','vpin',
    # Volume Profile / estructura
    'stacked_imb','above_poc','body_below_poc','body_below_vwap','dz','vp_lvn_below',
    # Contexto / regimen
    'regime','bars_since_low_vr','tight_range','val_near',
    # Patrones
    'sweep_confirmed','equal_low','ote_rejection','fib_ote','near_bearish_ob',
]
# Solo los que existen
feat_cols = [c for c in feat_cols if c in df.columns]

tdf = pd.DataFrame(trades)
mdf = df[['ts_ms'] + feat_cols].copy()
tdf = tdf.merge(mdf, on='ts_ms', how='left')
tdf['win'] = (tdf['result_r'] > 0).astype(int)
print(f'Merge OK. n={len(tdf)}, win_rate={tdf["win"].mean()*100:.1f}%')

wins = tdf[tdf['win']==1]; loss = tdf[tdf['win']==0]
n_w = len(wins); n_l = len(loss)

print(f'\n{"="*100}')
print(f'ANALISIS DE FEATURES DISCRIMINANTES — Longs ({n_w} wins, {n_l} losses, total={len(tdf)})')
print(f'{"="*100}')

results = []

# ── NUMERICAS ─────────────────────────────────────────────────────────────────
num_cols = [c for c in feat_cols if tdf[c].dtype in [float,'float64','int16','int64','int32']]
for col in num_cols:
    w_vals = wins[col].dropna().values
    l_vals = loss[col].dropna().values
    if len(w_vals)<10 or len(l_vals)<10: continue
    ks, pval = scipy_stats.ks_2samp(w_vals, l_vals)
    w_mean = w_vals.mean(); l_mean = l_vals.mean()
    diff = w_mean - l_mean
    results.append({'col':col,'type':'num','ks':ks,'pval':pval,
                    'w_mean':w_mean,'l_mean':l_mean,'diff':diff})

# ── BOOLEANAS ─────────────────────────────────────────────────────────────────
bool_cols = [c for c in feat_cols if tdf[c].dtype == bool]
for col in bool_cols:
    t_wr  = tdf[tdf[col]==True ]['win'].mean()*100
    f_wr  = tdf[tdf[col]==False]['win'].mean()*100
    n_t   = tdf[col].sum()
    n_f   = (~tdf[col]).sum()
    diff_pp = t_wr - f_wr
    results.append({'col':col,'type':'bool','ks':abs(diff_pp)/100,'pval':0.0,
                    'w_mean':t_wr,'l_mean':f_wr,'diff':diff_pp,
                    'n_true':n_t,'n_false':n_f})

# ── CATEGORICAS ───────────────────────────────────────────────────────────────
cat_cols = [c for c in feat_cols if tdf[c].dtype == object]
for col in cat_cols:
    cats = tdf[col].unique()
    cat_results = []
    for cat in cats:
        sub = tdf[tdf[col]==cat]
        if len(sub)<10: continue
        wr = sub['win'].mean()*100
        cat_results.append((cat, len(sub), wr))
    if len(cat_results)<2: continue
    # Solo reportar si hay variacion
    wrs = [x[2] for x in cat_results]
    rng = max(wrs)-min(wrs)
    if rng>5:
        results.append({'col':col,'type':'cat','ks':rng/100,'pval':0.0,
                        'w_mean':max(wrs),'l_mean':min(wrs),'diff':rng,
                        'cats':cat_results})

# Ordenar por KS (discriminacion)
results.sort(key=lambda x:-x['ks'])

print(f'\n{"Feature":<28} {"Type":<5} {"KS/pp":>6}  {"Win avg":>9}  {"Loss avg":>9}  {"Diff":>8}  Interpretacion')
print('-'*110)
for r in results[:40]:
    col=r['col']; t=r['type']
    if t=='num':
        interp = f'win_mean={r["w_mean"]:+.3f}  loss_mean={r["l_mean"]:+.3f}'
        print(f'{col:<28} {t:<5} {r["ks"]:>6.3f}  {r["w_mean"]:>+9.3f}  {r["l_mean"]:>+9.3f}  {r["diff"]:>+8.3f}')
    elif t=='bool':
        flag = ' *** UTIL' if abs(r['diff'])>8 else ''
        print(f'{col:<28} {t:<5} {r["ks"]:>6.3f}  TRUE={r["w_mean"]:>5.1f}%WR  FALSE={r["l_mean"]:>5.1f}%WR  diff={r["diff"]:>+.1f}pp{flag}')
    elif t=='cat':
        cats_str = '  '.join(f'{c}={wr:.1f}%(n={n})' for c,n,wr in sorted(r['cats'],key=lambda x:-x[2]))
        print(f'{col:<28} {t:<5} {r["ks"]:>6.3f}  {cats_str}')

# ── ANALISIS PROFUNDO DE LAS MEJORES FEATURES ────────────────────────────────
print(f'\n{"="*100}')
print('CUANTILES DE LAS TOP FEATURES NUMERICAS')
print(f'{"="*100}')

top_num = sorted([r for r in results if r['type']=='num'], key=lambda x:-x['ks'])[:12]
for r in top_num:
    col = r['col']
    # quartiles win vs loss
    wq = np.percentile(wins[col].dropna(), [25,50,75])
    lq = np.percentile(loss[col].dropna(), [25,50,75])
    print(f'\n{col}:')
    print(f'  WIN  Q25={wq[0]:+.4f}  Q50={wq[1]:+.4f}  Q75={wq[2]:+.4f}')
    print(f'  LOSS Q25={lq[0]:+.4f}  Q50={lq[1]:+.4f}  Q75={lq[2]:+.4f}')
    # WR por cuartil del dataset completo
    vals = tdf[col].dropna()
    q25,q50,q75 = vals.quantile([0.25,0.5,0.75])
    bins = [(-np.inf,q25),(q25,q50),(q50,q75),(q75,np.inf)]
    labels = [f'Q1(<{q25:.3f})',f'Q2({q25:.3f}-{q50:.3f})',f'Q3({q50:.3f}-{q75:.3f})',f'Q4(>{q75:.3f})']
    print(f'  WR por cuartil:')
    for (lo,hi),lbl in zip(bins,labels):
        sub = tdf[(tdf[col]>lo)&(tdf[col]<=hi)]
        if len(sub)<10: continue
        wr = sub['win'].mean()*100
        flag = ' ***' if wr>60 else (' <<' if wr<44 else '')
        print(f'    {lbl:<28}  n={len(sub):>4}  WR={wr:>5.1f}%{flag}')

# ── TEST DE FILTROS BASADOS EN FEATURES (sin hora) ──────────────────────────
print(f'\n{"="*100}')
print('WIN RATE DE COMBINACIONES DE FEATURES (todos los trades)')
print(f'{"="*100}')

# Los mas prometedores basados en el analisis
combos = []
for col in ['vpin','obi10_min','obi_range','spread_mean','cvd_consec_pos','dz',
            'vr','cvd_consec_neg','prev_bar_delta']:
    if col not in tdf.columns: continue
    vals = tdf[col].dropna()
    q25,q50,q75 = vals.quantile([0.25,0.5,0.75])
    for thresh,label,direction in [
        (q75, f'{col}>Q75({q75:.3f})', 'above'),
        (q50, f'{col}>Q50({q50:.3f})', 'above'),
        (q25, f'{col}<Q25({q25:.3f})', 'below'),
    ]:
        if direction=='above':
            sub = tdf[tdf[col]>thresh]
        else:
            sub = tdf[tdf[col]<thresh]
        if len(sub)<30: continue
        wr = sub['win'].mean()*100
        avg = sub['result_r'].mean()
        if wr>58 or wr<40:
            print(f'  {label:<45}  n={len(sub):>4}  WR={wr:>5.1f}%  AvgR={avg:>+.3f}')

# ── MATRIX: VPIN x OBI ───────────────────────────────────────────────────────
if 'vpin' in tdf.columns:
    print(f'\n{"="*60}')
    print('VPIN x OBI10_MIN (heatmap de WR)')
    vpin_med = tdf['vpin'].median()
    obi_med  = tdf['obi10_min'].median()
    for vp_cond, vp_lbl in [(tdf['vpin']>vpin_med,'VPIN_HIGH'),(tdf['vpin']<=vpin_med,'VPIN_LOW')]:
        for ob_cond, ob_lbl in [(tdf['obi10_min']>obi_med,'OBI_MIN_HIGH'),(tdf['obi10_min']<=obi_med,'OBI_MIN_LOW')]:
            sub = tdf[vp_cond & ob_cond]
            if len(sub)<20: continue
            wr = sub['win'].mean()*100; avg = sub['result_r'].mean()
            flag = ' ***' if wr>60 else (' <<' if wr<42 else '')
            print(f'  {vp_lbl} + {ob_lbl:<15}  n={len(sub):>4}  WR={wr:>5.1f}%  AvgR={avg:>+.3f}{flag}')
