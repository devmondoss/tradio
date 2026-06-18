"""
_feature_depth.py
Los filtros numericos matan volumen => matan capital.
Nueva hipotesis: las features no son para filtrar (menos trades)
sino para AJUSTAR el comportamiento del trade.
  - Mejor target en trades de alta calidad
  - Diferentes condiciones de salida CVD
  - Identificar cuando el stop deberia ser mas ajustado

Tambien: explorar features que afecten POCOS trades con gran impacto
(como bid_wall), no features que afecten muchos.
"""
import pandas as pd, numpy as np
from collections import defaultdict

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

BLOCK_VAL_HRS = {9,10,11}
BLOCK_AL_HRS  = {7,11,12,17,19}
BLOCK_PDL_HRS = {8,11,12,14}

def simulate(df,
             block_bid_wall=True,     # ya sabemos que ayuda
             # Target dinamico mejorado
             high_quality_boost=False, # 3R target si hay senal de alta calidad
             low_quality_reduce=False, # 1R target si senal debil
             # CVD exit ajustado
             cvd_req_base=3,
             cvd_req_qual=None,        # CVD requerido diferente si alta calidad
             # Features como calidad
             quality_features=None,   # lista de (col, operator, thresh) que suman puntos
             quality_thresh=0,
             ):
    h1=resamp(df,H1_MS); h1['atr']=atr14(h1['high'].values,h1['low'].values,h1['close'].values)
    h1_ctx={int(r.ts_ms):(float(r.low),float(r.atr)) for r in h1.itertuples()}
    rows=df.to_dict('records'); trades=[]; in_t=False
    ep=sl=tp=dist=0.0; t_start=t_entry=0; t_lbl=t_sess=''; cvd_streak=0
    cap=CAPITAL; monthly_risk=CAPITAL*RISK_PCT; current_month=-1
    cvd_req_entry=cvd_req_base

    for i,row in enumerate(rows):
        ts=int(row['ts_ms'])
        if in_t:
            obi=float(row.get('obi10_mean') or 0); cs=float(row.get('cvd_slope') or 0)
            cvd_streak=(cvd_streak+1) if cs<0 else 0
            cur_r=(row['close']-ep)/dist; reason=None; exit_px=0.0
            if row['low']<=sl:       reason,exit_px='stop',sl
            elif row['high']>=tp:    reason,exit_px='target',tp
            elif i-t_start>=FORWARD: reason,exit_px='timeout',row['close']
            elif cvd_streak>=cvd_req_entry and obi<-0.15 and cur_r>=1.0:
                reason,exit_px='cvd_exit',row['close']
            if reason:
                pnl_r=(exit_px-ep)/dist
                pnl_usd=monthly_risk*pnl_r-monthly_risk*FEE_RT
                trades.append({'ts_ms':t_entry,'result_r':round(pnl_r,3),'oos':ts>=OOS_MS,'level':t_lbl,'sess':t_sess})
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

        hr=(ts//3_600_000)%24
        if lbl=='VAL'    and hr in BLOCK_VAL_HRS: continue
        if lbl=='AL+VAL' and hr in BLOCK_AL_HRS:  continue
        if lbl=='PDL+VAL' and hr in BLOCK_PDL_HRS: continue

        c,o,h,l=float(row['close']),float(row['open']),float(row['high']),float(row['low'])
        rng=h-l
        if rng<=0: continue
        if not (0.30<(min(c,o)-l)/rng<0.85) or c<o: continue
        obi=float(row.get('obi10_mean') or 0); delta=float(row.get('delta') or 0)
        if obi<=0.05 and delta<=0: continue

        if block_bid_wall and bool(row.get('bid_wall')): continue

        h1d=h1_ctx.get((ts//H1_MS)*H1_MS)
        if h1d is None: continue
        h1l,h1a=h1d; sl_=h1l-0.40*h1a; d=row['close']-sl_
        if d<=0: continue
        if not (MIN_STOP<=d/row['close']<=MAX_STOP): continue

        reg = str(row.get('regime') or '')

        # ── Score de calidad basado en features ──────────────────────
        score = 0
        if quality_features:
            for feat_col, op, thresh in quality_features:
                val = float(row.get(feat_col) or 0)
                if   op == '>'  and val > thresh:  score += 1
                elif op == '<'  and val < thresh:  score += 1
                elif op == '>=' and val >= thresh:  score += 1
                elif op == '<=' and val <= thresh:  score += 1

        # ── Target dinamico ──────────────────────────────────────────
        base_tgt = 1.5 if reg=='Chop' else (3.0 if reg=='Expansion' else 2.0)
        if high_quality_boost and score >= quality_thresh:
            tgt = min(base_tgt + 1.0, 4.0)  # boost +1R si alta calidad
        elif low_quality_reduce and score < quality_thresh:
            tgt = max(base_tgt - 0.5, 1.0)  # reducir -0.5R si baja calidad
        else:
            tgt = base_tgt

        # ── CVD requirement ──────────────────────────────────────────
        if cvd_req_qual is not None and score >= quality_thresh:
            cvd_req_entry = cvd_req_qual
        else:
            cvd_req_entry = cvd_req_base

        in_t=True; ep=row['close']; sl=sl_; dist=d; tp=ep+tgt*dist
        t_start=i; t_entry=ts; t_lbl=lbl; t_sess=sess; cvd_streak=0

    return trades, cap

print('Cargando M1...')
df=pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
for c in df.columns:
    if df[c].dtype==object: df[c]=df[c].fillna('')
    elif df[c].dtype==float: df[c]=df[c].fillna(0.0)

def s(ts):
    if not ts: return 0,0.0,0.0
    n=len(ts); w=sum(1 for t in ts if t['result_r']>0)
    return n,w/n*100,sum(t['result_r'] for t in ts)/n

print(f'\n{"Config":<65}  {"n_IS":>4}  {"WR_IS":>6}  {"n_OOS":>4}  {"WR_OOS":>6}  {"AvgR_OOS":>9}  {"Capital":>10}')
print('-'*120)

def run(label, **kwargs):
    t,cap=simulate(df,**kwargs)
    ist=[x for x in t if not x['oos']]; oot=[x for x in t if x['oos']]
    ni,wi,_=s(ist); no,wo,ao=s(oot)
    flag = ' ***' if cap>28000 else (' **' if cap>26357 else (' !!' if cap>25745 else ''))
    print(f'{label:<65}  {ni:>4}  {wi:>5.1f}%  {no:>4}  {wo:>5.1f}%  {ao:>+.3f}      ${cap:>9,.0f}{flag}')
    return no,wo,ao,cap

# Referencia
run('BEST: hora v2 + bid_wall [REFERENCIA]')

print()
print('-- Features como SCORE de calidad para target dinamico --')
# Score = numero de features de calidad presentes
# sell_vol alto + buy_vol alto + obi10_min muy negativo + delta alto
QUAL_FEATS_A = [
    ('sell_vol', '>',  2.98),   # Q50 sell volume
    ('buy_vol',  '>',  4.06),   # Q50 buy volume
    ('delta',    '>',  2.27),   # Q75 delta
    ('obi_range','>',  1.79),   # Q50 obi_range
]
QUAL_FEATS_B = [
    ('sell_vol', '>',  2.98),
    ('buy_vol',  '>',  4.06),
    ('vr',       '>',  1.48),   # Q75 volume ratio
]
QUAL_FEATS_C = [
    ('sell_vol', '>',  5.46),   # Q75 — gran absorcion
    ('buy_vol',  '>',  7.09),   # Q75
]

for score_thresh in [1,2]:
    run(f'TARGET +1R si score>={score_thresh}/4 feats (A)',
        high_quality_boost=True, quality_features=QUAL_FEATS_A, quality_thresh=score_thresh)
for score_thresh in [1,2]:
    run(f'TARGET +1R si score>={score_thresh}/3 feats (B)',
        high_quality_boost=True, quality_features=QUAL_FEATS_B, quality_thresh=score_thresh)

print()
print('-- CVD mas permisivo (2 barras) en trades de alta calidad --')
for score_thresh in [1,2]:
    run(f'CVD=2 si score>={score_thresh}/4 feats (A)',
        cvd_req_qual=2, quality_features=QUAL_FEATS_A, quality_thresh=score_thresh)

print()
print('-- Target reducido en baja calidad (score=0) --')
run('TARGET -0.5R si score=0/4 feats (A)',
    low_quality_reduce=True, quality_features=QUAL_FEATS_A, quality_thresh=1)

print()
print('-- ANALISIS DIRECTO: trades con sell_vol alto vs bajo --')
# Ver MFE (max favorable excursion) segun sell_vol
all_t, _ = simulate(df, block_bid_wall=True)
tdf = pd.DataFrame(all_t)
m1_sub = df[['ts_ms','sell_vol','buy_vol','delta','obi10_min','vr','obi_range',
             'spread_mean','bid_wall','thin_below','cvd_consec_pos']].copy()
tdf = tdf.merge(m1_sub, on='ts_ms', how='left')
tdf['win'] = (tdf['result_r']>0).astype(int)

print(f'\n  Total trades analizados: {len(tdf)}')
for col, bins_n in [('sell_vol',4),('buy_vol',4),('delta',4),('vr',4),('obi10_min',4)]:
    if col not in tdf.columns: continue
    vals = tdf[col].dropna()
    qs = np.percentile(vals,[25,50,75])
    print(f'\n  {col} (Q25={qs[0]:.2f} Q50={qs[1]:.2f} Q75={qs[2]:.2f}):')
    # Por cuartil: WR, AvgR, % de trades
    boundaries = [(-np.inf,qs[0]),(qs[0],qs[1]),(qs[1],qs[2]),(qs[2],np.inf)]
    labels = ['Q1','Q2','Q3','Q4']
    for (lo,hi),lbl in zip(boundaries,labels):
        sub = tdf[(tdf[col]>lo)&(tdf[col]<=hi)]
        if len(sub)<10: continue
        n=len(sub); w=sub['win'].mean()*100; avg=sub['result_r'].mean()
        pct = n/len(tdf)*100
        flag = ' ***' if w>58 else (' <<' if w<44 else '')
        print(f'    {lbl}  n={n:>4}({pct:.0f}%)  WR={w:>5.1f}%  AvgR={avg:>+.3f}{flag}')
