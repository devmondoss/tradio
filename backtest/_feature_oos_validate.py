"""
_feature_oos_validate.py
Validacion OOS limpia del position sizing por score.
Thresholds calculados SOLO en IS (Jun25-Feb26),
aplicados sin cambios en OOS (Mar26-May26).
Tambien: analizar drawdown y curva de capital mensual.
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

def simulate(df, sizing_mode='flat', base_risk=0.02, block_bid_wall=True,
             use_hour_blocks=True,
             sv_q50=2.91, sv_q75=5.28,
             bv_q50=4.05, bv_q75=6.89,
             dlt_q50=0.89, vr_q50=1.0):

    def score(row):
        sv  = float(row.get('sell_vol') or 0)
        bv  = float(row.get('buy_vol')  or 0)
        dlt = float(row.get('delta')    or 0)
        vr  = float(row.get('vr')       or 0)
        return sum([sv>=sv_q50, bv>=bv_q50, dlt>dlt_q50, vr>vr_q50])

    h1=resamp(df,H1_MS); h1['atr']=atr14(h1['high'].values,h1['low'].values,h1['close'].values)
    h1_ctx={int(r.ts_ms):(float(r.low),float(r.atr)) for r in h1.itertuples()}
    rows=df.to_dict('records'); trades=[]; in_t=False
    ep=sl=tp=dist=0.0; t_start=t_entry=0; t_lbl=t_sess=''; cvd_streak=0
    cap=CAPITAL; monthly_risk=CAPITAL*base_risk; current_month=-1; entry_risk=0.0
    peak_cap=CAPITAL; max_dd=0.0
    monthly_caps=[]

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
                                'level':t_lbl,'risk':entry_risk})
                cap+=pnl_usd
                if cap>peak_cap: peak_cap=cap
                dd=(peak_cap-cap)/peak_cap*100
                if dd>max_dd: max_dd=dd
                in_t=False; cvd_streak=0
            continue

        month=pd.Timestamp(ts,unit='ms',tz='UTC').month+pd.Timestamp(ts,unit='ms',tz='UTC').year*12
        if month!=current_month:
            monthly_risk=cap*base_risk; current_month=month
            monthly_caps.append((ts,cap))

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
        if use_hour_blocks:
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

        sc = score(row)
        if   sizing_mode=='flat':    risk=monthly_risk
        elif sizing_mode=='boost':   risk=monthly_risk*[0.5,0.75,1.0,1.25,1.5][sc]
        elif sizing_mode=='boost_a': risk=monthly_risk*[0.2,0.5,1.0,1.5,2.0][sc]
        elif sizing_mode=='boost_b': risk=monthly_risk*[0.3,0.6,1.0,1.4,1.8][sc]
        elif sizing_mode=='boost_c': risk=monthly_risk*[0.4,0.7,1.0,1.3,1.6][sc]
        else: risk=monthly_risk

        in_t=True; ep=row['close']; sl=sl_; dist=d; tp=ep+tgt*dist
        entry_risk=risk; t_start=i; t_entry=ts; t_lbl=lbl; cvd_streak=0; t_sess=sess

    return trades, cap, max_dd, monthly_caps

print('Cargando M1...')
df=pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
for c in df.columns:
    if df[c].dtype==object: df[c]=df[c].fillna('')
    elif df[c].dtype==float: df[c]=df[c].fillna(0.0)

# ── Calcular thresholds SOLO en IS ───────────────────────────────────────────
print('Calculando thresholds IS (Jun25-Feb26)...')
IS_df = df[df['ts_ms'] < OOS_MS].copy()
# Simular IS para obtener trades IS
t_is, _, _, _ = simulate(IS_df, sizing_mode='flat', block_bid_wall=False, use_hour_blocks=False)
tdf_is = pd.DataFrame(t_is)
tdf_is = tdf_is.merge(df[['ts_ms','sell_vol','buy_vol','delta','vr']], on='ts_ms', how='left')

SV_Q50_IS = tdf_is['sell_vol'].quantile(0.50)
SV_Q75_IS = tdf_is['sell_vol'].quantile(0.75)
BV_Q50_IS = tdf_is['buy_vol'].quantile(0.50)
BV_Q75_IS = tdf_is['buy_vol'].quantile(0.75)
DLT_Q50_IS = tdf_is['delta'].quantile(0.50)
VR_Q50_IS  = tdf_is['vr'].quantile(0.50)

print(f'  IS thresholds:')
print(f'  sell_vol Q50={SV_Q50_IS:.3f} Q75={SV_Q75_IS:.3f}')
print(f'  buy_vol  Q50={BV_Q50_IS:.3f} Q75={BV_Q75_IS:.3f}')
print(f'  delta    Q50={DLT_Q50_IS:.3f}')
print(f'  vr       Q50={VR_Q50_IS:.3f}')

def s(ts):
    if not ts: return 0,0.0,0.0
    n=len(ts); w=sum(1 for t in ts if t['result_r']>0)
    return n,w/n*100,sum(t['result_r'] for t in ts)/n

kwargs_thresh = dict(sv_q50=SV_Q50_IS, sv_q75=SV_Q75_IS, bv_q50=BV_Q50_IS,
                     bv_q75=BV_Q75_IS, dlt_q50=DLT_Q50_IS, vr_q50=VR_Q50_IS)

print(f'\n{"Config":<62}  {"n_IS":>4}  {"WR_IS":>6}  {"n_OOS":>4}  {"WR_OOS":>6}  {"AvgR_OOS":>9}  {"Capital":>10}  {"MaxDD":>8}')
print('-'*127)

def run(label, **kwargs):
    merged = {**kwargs_thresh, **kwargs}
    t,cap,dd,mc=simulate(df,**merged)
    ist=[x for x in t if not x['oos']]; oot=[x for x in t if x['oos']]
    ni,wi,_=s(ist); no,wo,ao=s(oot)
    flag = ' ***' if cap>50000 else (' **' if cap>30000 else (' !!' if cap>26357 else ''))
    print(f'{label:<62}  {ni:>4}  {wi:>5.1f}%  {no:>4}  {wo:>5.1f}%  {ao:>+.3f}      ${cap:>9,.0f}  {dd:>7.1f}%{flag}')
    return t,cap,dd

# Referencias
run('FLAT 2% + hora + bid_wall',                  sizing_mode='flat',    use_hour_blocks=True)
run('FLAT 2% sin hora blocks',                    sizing_mode='flat',    use_hour_blocks=False)
print()
# Score boost con thresholds IS
run('BOOST c (0.4 0.7 1.0 1.3 1.6) + hora',      sizing_mode='boost_c', use_hour_blocks=True)
run('BOOST b (0.3 0.6 1.0 1.4 1.8) + hora',      sizing_mode='boost_b', use_hour_blocks=True)
run('BOOST   (0.5 0.75 1.0 1.25 1.5) + hora',    sizing_mode='boost',   use_hour_blocks=True)
run('BOOST a (0.2 0.5 1.0 1.5 2.0) + hora',      sizing_mode='boost_a', use_hour_blocks=True)
print()
# Sin hora blocks, solo features
run('BOOST c SIN hora blocks',                     sizing_mode='boost_c', use_hour_blocks=False)
run('BOOST b SIN hora blocks',                     sizing_mode='boost_b', use_hour_blocks=False)
run('BOOST   SIN hora blocks',                     sizing_mode='boost',   use_hour_blocks=False)
run('BOOST a SIN hora blocks',                     sizing_mode='boost_a', use_hour_blocks=False)

# ── Curva mensual del mejor ───────────────────────────────────────────────────
print('\n=== Curva mensual: BOOST a + hora (IS thresholds) ===')
t_best, _, _, mc = simulate(df, sizing_mode='boost_a', use_hour_blocks=True, **kwargs_thresh)
for ts_m, cap_m in mc:
    dt = pd.Timestamp(ts_m, unit='ms', tz='UTC').strftime('%Y-%m')
    is_label = 'IS ' if ts_m < OOS_MS else 'OOS'
    bar = '#' * int(cap_m/500)
    print(f'  {dt} {is_label}  ${cap_m:>8,.0f}  {bar}')
