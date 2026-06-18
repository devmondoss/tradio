"""
_feature_sizing.py
Idea: en lugar de filtrar trades malos (mata volumen/compounding),
reducir el RIESGO segun la calidad del trade.
  - Q1 sell_vol (poca absorcion):  0.5% risk (1/4 del normal)
  - Q2 sell_vol:                   1.0% risk
  - Q3 sell_vol:                   2.0% risk (normal)
  - Q4 sell_vol (absorcion real):  2.0% risk (o 2.5%?)

Alternativa: score de calidad multi-feature.
"""
import pandas as pd, numpy as np
from collections import defaultdict

CAPITAL=500.0; FEE_RT=0.0007; FORWARD=1200
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

# Percentiles globales del dataset (calculados en el analisis previo)
# sell_vol: Q25=1.43, Q50=2.91, Q75=5.28
# buy_vol:  Q25=2.18, Q50=4.05, Q75=6.89
SV_Q1=1.43; SV_Q2=2.91; SV_Q3=5.28
BV_Q1=2.18; BV_Q2=4.05; BV_Q3=6.89

def quality_score(row):
    """Score 0-4 basado en: sell_vol, buy_vol, delta, vr."""
    sv  = float(row.get('sell_vol') or 0)
    bv  = float(row.get('buy_vol')  or 0)
    dlt = float(row.get('delta')    or 0)
    vr  = float(row.get('vr')       or 0)
    score = 0
    if sv  >= SV_Q2:  score += 1   # absorcion media-alta
    if bv  >= BV_Q2:  score += 1   # volumen comprador medio-alto
    if dlt >  0.89:   score += 1   # delta positivo fuerte
    if vr  >  1.0:    score += 1   # volumen sobre promedio
    return score

def simulate(df,
             block_bid_wall=True,
             sizing_mode='flat',    # flat | sell_vol_tiers | score_tiers | score_boost
             base_risk=0.02,
             ):
    h1=resamp(df,H1_MS); h1['atr']=atr14(h1['high'].values,h1['low'].values,h1['close'].values)
    h1_ctx={int(r.ts_ms):(float(r.low),float(r.atr)) for r in h1.itertuples()}
    rows=df.to_dict('records'); trades=[]; in_t=False
    ep=sl=tp=dist=0.0; t_start=t_entry=0; t_lbl=t_sess=''; cvd_streak=0
    cap=CAPITAL; monthly_risk=CAPITAL*base_risk; current_month=-1
    entry_risk=0.0

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
                pnl_usd=entry_risk*pnl_r-entry_risk*FEE_RT
                trades.append({'ts_ms':t_entry,'result_r':round(pnl_r,3),'oos':ts>=OOS_MS,
                                'level':t_lbl,'sess':t_sess,'risk':entry_risk})
                cap+=pnl_usd; in_t=False; cvd_streak=0
            continue

        month=pd.Timestamp(ts,unit='ms',tz='UTC').month+pd.Timestamp(ts,unit='ms',tz='UTC').year*12
        if month!=current_month: monthly_risk=cap*base_risk; current_month=month
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
        reg=str(row.get('regime') or '')
        tgt=1.5 if reg=='Chop' else (3.0 if reg=='Expansion' else 2.0)

        # ── RISK SIZING ──────────────────────────────────────────────
        sv  = float(row.get('sell_vol') or 0)
        sc  = quality_score(row)

        if sizing_mode == 'flat':
            risk = monthly_risk
        elif sizing_mode == 'sell_vol_tiers':
            if   sv < SV_Q1: risk = monthly_risk * 0.25   # 0.5%
            elif sv < SV_Q2: risk = monthly_risk * 0.50   # 1.0%
            elif sv < SV_Q3: risk = monthly_risk * 1.00   # 2.0%
            else:            risk = monthly_risk * 1.00   # 2.0%
        elif sizing_mode == 'score_tiers':
            # 0=25% 1=50% 2=100% 3=100% 4=100%
            multiplier = [0.25, 0.50, 1.00, 1.00, 1.00][sc]
            risk = monthly_risk * multiplier
        elif sizing_mode == 'score_boost':
            # 0=25% 1=75% 2=100% 3=125% 4=150%
            multiplier = [0.25, 0.75, 1.00, 1.25, 1.50][sc]
            risk = monthly_risk * multiplier
        elif sizing_mode == 'score_boost_aggressive':
            # 0=10% 1=50% 2=100% 3=150% 4=200%
            multiplier = [0.10, 0.50, 1.00, 1.50, 2.00][sc]
            risk = monthly_risk * multiplier
        elif sizing_mode == 'sv_boost':
            # sell_vol directamente proporcional al riesgo
            if   sv < SV_Q1: risk = monthly_risk * 0.25
            elif sv < SV_Q2: risk = monthly_risk * 0.75
            elif sv < SV_Q3: risk = monthly_risk * 1.00
            else:            risk = monthly_risk * 1.25   # Q4 = 2.5%
        else:
            risk = monthly_risk

        in_t=True; ep=row['close']; sl=sl_; dist=d; tp=ep+tgt*dist
        entry_risk=risk; t_start=i; t_entry=ts; t_lbl=lbl; t_sess=sess; cvd_streak=0

    return trades, cap

print('Cargando M1...')
df=pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
for c in df.columns:
    if df[c].dtype==object: df[c]=df[c].fillna('')
    elif df[c].dtype==float: df[c]=df[c].fillna(0.0)

# Calcular percentiles reales del dataset de trades
print('Calculando percentiles reales...')
all_t, _ = simulate(df, sizing_mode='flat')
tdf = pd.DataFrame(all_t)
m1s = df[['ts_ms','sell_vol','buy_vol','delta','vr']].copy()
tdf = tdf.merge(m1s, on='ts_ms', how='left')
q25_sv, q50_sv, q75_sv = tdf['sell_vol'].quantile([0.25,0.5,0.75])
print(f'  sell_vol: Q25={q25_sv:.2f}  Q50={q50_sv:.2f}  Q75={q75_sv:.2f}')
SV_Q1=q25_sv; SV_Q2=q50_sv; SV_Q3=q75_sv

def s(ts):
    if not ts: return 0,0.0,0.0
    n=len(ts); w=sum(1 for t in ts if t['result_r']>0)
    return n,w/n*100,sum(t['result_r'] for t in ts)/n

print(f'\n{"Config":<55}  {"n_IS":>4}  {"WR_IS":>6}  {"n_OOS":>4}  {"WR_OOS":>6}  {"AvgR_OOS":>9}  {"Capital":>10}')
print('-'*109)

def run(label, **kwargs):
    t,cap=simulate(df,**kwargs)
    ist=[x for x in t if not x['oos']]; oot=[x for x in t if x['oos']]
    ni,wi,_=s(ist); no,wo,ao=s(oot)
    flag = ' ***' if cap>30000 else (' **' if cap>26357 else (' !!' if cap>25745 else ''))
    print(f'{label:<55}  {ni:>4}  {wi:>5.1f}%  {no:>4}  {wo:>5.1f}%  {ao:>+.3f}      ${cap:>9,.0f}{flag}')
    return no,wo,ao,cap

run('REFERENCIA: flat 2% + hora + bid_wall',          sizing_mode='flat')
print()
run('sell_vol tiers (Q1=0.5% Q2=1% Q3-Q4=2%)',       sizing_mode='sell_vol_tiers')
run('score tiers (0=0.5% 1=1% 2-4=2%)',               sizing_mode='score_tiers')
run('score BOOST (0=0.5% 1=1.5% 2=2% 3=2.5% 4=3%)',  sizing_mode='score_boost')
run('score BOOST agresivo (0=0.2% 1=1% 2=2% 3=3% 4=4%)', sizing_mode='score_boost_aggressive')
run('sv BOOST (Q1=0.5% Q2=1.5% Q3=2% Q4=2.5%)',      sizing_mode='sv_boost')
print()
# Combinacion: sin hora blocks, solo feature sizing
run('SIN hora blocks + score tiers',                   sizing_mode='score_tiers',        block_bid_wall=False)
run('SIN hora + score BOOST agresivo',                 sizing_mode='score_boost_aggressive', block_bid_wall=False)

# ── Analisis del score por trades ─────────────────────────────────────────────
print('\n=== Score distribution de trades OOS (flat) ===')
t_flat,_ = simulate(df, sizing_mode='flat')
tdf2=pd.DataFrame(t_flat).merge(m1s,on='ts_ms',how='left')
tdf2['score']=tdf2.apply(lambda r: quality_score(r), axis=1)
oot2=[x for x in t_flat if x['oos']]
tdf_oos=pd.DataFrame(oot2).merge(m1s,on='ts_ms',how='left')
tdf_oos['score']=tdf_oos.apply(lambda r: quality_score(r), axis=1)
for sc in range(5):
    sub=tdf_oos[tdf_oos['score']==sc]
    if len(sub)==0: continue
    n=len(sub); w=(sub['result_r']>0).mean()*100; avg=sub['result_r'].mean()
    pct=n/len(tdf_oos)*100
    flag = ' ***' if w>58 else (' <<' if w<44 else '')
    print(f'  Score {sc}/4  n={n:>4} ({pct:.0f}%)  WR={w:>5.1f}%  AvgR={avg:>+.3f}{flag}')
