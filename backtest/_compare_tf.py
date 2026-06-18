import pandas as pd, numpy as np

CAPITAL = 500.0; RISK_PCT = 0.02; FEE_RT = 0.0007
TARGET_R = 2.0; MIN_STOP = 0.003; MAX_STOP = 0.0075
H1_MS = 3_600_000; OOS_MS = int(pd.Timestamp('2026-03-01', tz='UTC').value // 1_000_000)
LEVEL_TOL = 0.007

ROOT = 'data/bybit-spot/processed/'

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

def simulate(df, bar_ms, forward_bars, cvd_streak_req=5):
    h1=resamp(df,H1_MS); h1['atr']=atr14(h1['high'].values,h1['low'].values,h1['close'].values)
    h1_ctx={int(r.ts_ms):(float(r.high),float(r.atr)) for r in h1.itertuples()}
    rows=df.to_dict('records'); trades=[]; in_t=False
    ep=sl=tp=dist=0.0; t_start=t_entry=0; t_lbl=''; mfe=mae=cvd_streak=0; cap=CAPITAL

    for i,row in enumerate(rows):
        ts=int(row['ts_ms'])
        if in_t:
            mfe=max(mfe,(ep-row['low'])/dist); mae=max(mae,(row['high']-ep)/dist)
            obi=float(row.get('obi10_mean') or 0); cs=float(row.get('cvd_slope') or 0)
            cvd_streak=(cvd_streak+1) if cs>0 else 0
            cur_r=(ep-row['close'])/dist; reason=None; exit_px=0.0
            if row['high']>=sl: reason,exit_px='stop',sl
            elif row['low']<=tp: reason,exit_px='target',tp
            elif i-t_start>=forward_bars: reason,exit_px='timeout',row['close']
            elif cvd_streak>=cvd_streak_req and obi>0.15 and cur_r>=1.0: reason,exit_px='cvd_exit',row['close']
            if reason:
                pnl_r=(ep-exit_px)/dist; risk_usd=cap*RISK_PCT
                pnl_usd=risk_usd*pnl_r-cap*RISK_PCT*FEE_RT
                trades.append({'ts_ms':t_entry,'result_r':round(pnl_r,3),'oos':ts>=OOS_MS,'level':t_lbl,'reason':reason})
                cap+=pnl_usd; in_t=False; cvd_streak=0
            continue

        sess=session(ts)
        if not sess: continue

        high=row['high']; levels=[]
        for k,col in [('PDH','prev_day_high'),('AH','asian_high'),('WH','weekly_high'),('VAH','vp_vah')]:
            v=row.get(col)
            if v and v>0 and abs(high-v)/v<=LEVEL_TOL: levels.append(k)
        if not levels: continue
        lbl='+'.join(levels)
        if 'VAH' not in lbl: continue
        parts=lbl.split('+')
        if len(parts)>=3 and 'PDH' in parts and 'AH' in parts: continue
        if 'PDH' in parts and 'VAH' in parts and len(parts)==2: continue
        if 'WH' in parts and (ts//3_600_000)%24==15: continue
        if lbl=='AH+VAH' and float(row.get('obi10_mean') or 0)<-0.15: continue

        c,o,h,l=float(row['close']),float(row['open']),float(row['high']),float(row['low'])
        rng=h-l
        if rng<=0: continue
        wick_pct=(h-max(c,o))/rng
        if not (0.30 < wick_pct < 0.85) or c>o: continue
        obi=float(row.get('obi10_mean') or 0); delta=float(row.get('delta') or 0)
        if obi>=-0.05 and delta>=0: continue
        h1d=h1_ctx.get((ts//H1_MS)*H1_MS)
        if h1d is None: continue
        h1h,h1a=h1d; sl_=h1h+0.40*h1a; d=sl_-row['close']
        if d<=0: continue
        sp=d/row['close']
        if not (MIN_STOP<=sp<=MAX_STOP): continue
        in_t=True; ep=row['close']; sl=sl_; dist=d; tp=ep-TARGET_R*dist
        t_start=i; t_entry=ts; t_lbl=lbl; mfe=mae=cvd_streak=0
    return trades, cap

# cargar datasets
print('Cargando datos...')
m1  = pd.read_parquet(ROOT+'btcusdt_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
m5  = pd.read_parquet(ROOT+'btcusdt_m5.parquet').sort_values('ts_ms').reset_index(drop=True)
m15 = pd.read_parquet(ROOT+'btcusdt_m15.parquet').sort_values('ts_ms').reset_index(drop=True)

for df in [m1, m5, m15]:
    for c in df.columns:
        if df[c].dtype==object: df[c]=df[c].fillna('')
        elif df[c].dtype==float: df[c]=df[c].fillna(0.0)

print(f"M1:  {len(m1):,} barras | M5: {len(m5):,} barras | M15: {len(m15):,} barras\n")

print(f"{'TF':<6}  {'n_tot':>6}  {'n_IS':>5}  {'WR_IS':>6}  {'AvgR_IS':>8}  {'n_OOS':>5}  {'WR_OOS':>6}  {'AvgR_OOS':>9}  {'tpd':>5}  {'Capital':>10}")
print('-'*98)

configs = [
    (m1,  60_000,      1200, 5,  'M1  (1200 bars=20h, cvd=5)'),
    (m5,  300_000,     240,  5,  'M5  (240 bars=20h,  cvd=5)'),
    (m5,  300_000,     240,  3,  'M5  (240 bars=20h,  cvd=3)'),
    (m15, 900_000,     80,   3,  'M15 (80 bars=20h,   cvd=3)'),
    (m15, 900_000,     80,   2,  'M15 (80 bars=20h,   cvd=2)'),
]

for df, bms, fwd, cvd, label in configs:
    trades, cap = simulate(df, bar_ms=bms, forward_bars=fwd, cvd_streak_req=cvd)
    ist=[t for t in trades if not t['oos']]; oot=[t for t in trades if t['oos']]
    def s(ts, days):
        if not ts: return 0,0.0,0.0
        n=len(ts); w=sum(1 for t in ts if t['result_r']>0)
        return n,w/n*100,sum(t['result_r'] for t in ts)/n
    ni,wi,ai=s(ist,260); no,wo,ao=s(oot,90)
    ntot=ni+no; tpd=no/90
    print(f"{label:<36}  {ntot:>6}  {ni:>5}  {wi:>5.1f}%  {ai:>+.3f}     {no:>5}  {wo:>5.1f}%  {ao:>+.3f}      {tpd:>4.1f}  ${cap:>9,.0f}")
