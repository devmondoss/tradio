import pandas as pd, numpy as np
from pathlib import Path

CAPITAL = 500.0; RISK_PCT = 0.02; FEE_RT = 0.0007
TARGET_R = 2.5; FORWARD = 1200; MIN_STOP = 0.003; MAX_STOP = 0.0075
H1_MS = 3_600_000; OOS_MS = int(pd.Timestamp('2026-03-01', tz='UTC').value // 1_000_000)
LEVEL_TOL = 0.004

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
    if 12*60<=hm<16*60: return 'overlap'
    if 16*60<=hm<20*60: return 'ny'
    return ''

def active_level(row):
    high=row['high']; levels=[]
    for k,col in [('PDH','prev_day_high'),('AH','asian_high'),('WH','weekly_high'),('VAH','vp_vah')]:
        v=row.get(col)
        if v and v>0 and abs(high-v)/v<=LEVEL_TOL: levels.append(k)
    return (True,'+'.join(levels)) if levels else (False,'')

def rejection(row):
    c,o,h,l=float(row['close']),float(row['open']),float(row['high']),float(row['low'])
    rng=h-l
    if rng<=0: return False
    return (h-max(c,o))/rng>0.30 and c<=o

def flow_bearish(row):
    return float(row.get('obi10_mean') or 0)<-0.05 or float(row.get('delta') or 0)<0

def simulate(df, require_no_fvg=False):
    h1=resamp(df,H1_MS); h1['atr']=atr14(h1['high'].values,h1['low'].values,h1['close'].values)
    h1_ctx={int(r.ts_ms):(float(r.high),float(r.atr)) for r in h1.itertuples()}
    rows=df.to_dict('records'); trades=[]; in_t=False
    ep=sl=tp=dist=0.0; t_start=t_entry=0; mfe=mae=cvd_streak=0; cap=CAPITAL

    for i,row in enumerate(rows):
        ts=int(row['ts_ms'])
        if in_t:
            mfe=max(mfe,(ep-row['low'])/dist); mae=max(mae,(row['high']-ep)/dist)
            obi=float(row.get('obi10_mean') or 0); cs=float(row.get('cvd_slope') or 0)
            cvd_streak=(cvd_streak+1) if cs>0 else 0
            cur_r=(ep-row['close'])/dist; reason=None; exit_px=0.0
            if row['high']>=sl: reason,exit_px='stop',sl
            elif row['low']<=tp: reason,exit_px='target',tp
            elif i-t_start>=FORWARD: reason,exit_px='timeout',row['close']
            elif cvd_streak>=5 and obi>0.15 and cur_r>=1.0: reason,exit_px='cvd_exit',row['close']
            if reason:
                pnl_r=(ep-exit_px)/dist; risk_usd=cap*RISK_PCT
                pnl_usd=risk_usd*pnl_r-cap*RISK_PCT*FEE_RT
                trades.append({'ts_ms':t_entry,'result_r':round(pnl_r,3),'pnl_usd':round(pnl_usd,2),'oos':ts>=OOS_MS,'reason':reason})
                cap+=pnl_usd; in_t=False; cvd_streak=0
            continue
        sess=session(ts)
        if not sess: continue
        if require_no_fvg and row.get('bearish_fvg_active'): continue
        ok_level,lbl=active_level(row)
        if not ok_level or 'VAH' not in lbl: continue
        parts=lbl.split('+')
        if len(parts)>=3 and 'PDH' in parts and 'AH' in parts: continue
        if not rejection(row) or not flow_bearish(row): continue
        h1d=h1_ctx.get((ts//H1_MS)*H1_MS)
        if h1d is None: continue
        h1h,h1a=h1d; sl_=h1h+0.30*h1a; d=sl_-row['close']
        if d<=0: continue
        sp=d/row['close']
        if not (MIN_STOP<=sp<=MAX_STOP): continue
        in_t=True; ep=row['close']; sl=sl_; dist=d; tp=ep-TARGET_R*dist
        t_start=i; t_entry=ts; mfe=mae=cvd_streak=0
    return trades, cap

df = pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
for c in df.columns:
    if df[c].dtype==object: df[c]=df[c].fillna('')
    elif df[c].dtype==float: df[c]=df[c].fillna(0.0)

print(f"{'Config':<22}  {'n_IS':>5}  {'WR_IS':>6}  {'AvgR_IS':>8}  {'n_OOS':>6}  {'WR_OOS':>7}  {'AvgR_OOS':>9}  {'Capital':>10}")
print('-'*90)
for label, nofvg in [('Sin filtro FVG', False), ('bearish_fvg=False', True)]:
    trades, cap = simulate(df, require_no_fvg=nofvg)
    ist=[t for t in trades if not t['oos']]; oot=[t for t in trades if t['oos']]
    def s(ts):
        if not ts: return 0,0.0,0.0
        n=len(ts); w=sum(1 for t in ts if t['result_r']>0)
        return n, w/n*100, sum(t['result_r'] for t in ts)/n
    ni,wi,ai=s(ist); no,wo,ao=s(oot)
    print(f"{label:<22}  {ni:>5}  {wi:>5.1f}%  {ai:>+.3f}     {no:>5}  {wo:>5.1f}%  {ao:>+.3f}      ${cap:>9,.0f}")
