"""
_calibrate_longs.py
Calibracion del sistema de longs — espejo de shorts.
Proceso identico al de shorts v1->v5:
  1. Analisis por nivel, hora, sesion
  2. Identificar combos/horas toxicas
  3. Bloquear y medir impacto

Longs base: VAL requerido, wick inferior 30-85%, cierre alcista,
            OBI > +0.05 OR delta > 0, stop H1_low - 0.40xATR
"""
import pandas as pd, numpy as np
from collections import defaultdict

CAPITAL = 500.0; RISK_PCT = 0.02; FEE_RT = 0.0007
FORWARD = 1200; MIN_STOP = 0.003; MAX_STOP = 0.0075
H1_MS = 3_600_000
OOS_MS = int(pd.Timestamp('2026-03-01', tz='UTC').value // 1_000_000)
LEVEL_TOL = 0.007

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

def simulate(df,
             block_pdl_val=False,    # PDL+VAL falsa confluencia
             block_pdl_al_val=False, # PDL+AL+VAL triple
             block_wl_hour=None,     # bloquear hora UTC para WL trades
             al_val_obi_thresh=None, # bloquear AL+VAL cuando OBI > thresh
             london=True,
             cvd_req=3, regime_target=True):

    h1=resamp(df,H1_MS); h1['atr']=atr14(h1['high'].values,h1['low'].values,h1['close'].values)
    h1_ctx={int(r.ts_ms):(float(r.low),float(r.atr)) for r in h1.itertuples()}
    rows=df.to_dict('records'); trades=[]; in_t=False
    ep=sl=tp=dist=0.0; t_start=t_entry=0; t_lbl=t_sess=''; cvd_streak=0
    cap=CAPITAL; monthly_risk=CAPITAL*RISK_PCT; current_month=-1

    for i,row in enumerate(rows):
        ts=int(row['ts_ms'])
        if in_t:
            obi=float(row.get('obi10_mean') or 0); cs=float(row.get('cvd_slope') or 0)
            cvd_streak=(cvd_streak+1) if cs<0 else 0  # longs: CVD negativo = reversión
            cur_r=(row['close']-ep)/dist; reason=None; exit_px=0.0
            if row['low']<=sl:         reason,exit_px='stop',sl
            elif row['high']>=tp:      reason,exit_px='target',tp
            elif i-t_start>=FORWARD:   reason,exit_px='timeout',row['close']
            elif cvd_streak>=cvd_req and obi<-0.15 and cur_r>=1.0:
                reason,exit_px='cvd_exit',row['close']
            if reason:
                pnl_r=(exit_px-ep)/dist
                pnl_usd=monthly_risk*pnl_r-monthly_risk*FEE_RT
                trades.append({'ts_ms':t_entry,'result_r':round(pnl_r,3),
                                'oos':ts>=OOS_MS,'level':t_lbl,'sess':t_sess,'reason':reason})
                cap+=pnl_usd; in_t=False; cvd_streak=0
            continue

        month=pd.Timestamp(ts,unit='ms',tz='UTC').month+pd.Timestamp(ts,unit='ms',tz='UTC').year*12
        if month!=current_month: monthly_risk=cap*RISK_PCT; current_month=month

        sess=session(ts)
        allowed=['overlap','ny']
        if london: allowed.append('london')
        if sess not in allowed: continue

        low=row['low']; levels=[]
        for k,col in [('PDL','prev_day_low'),('AL','asian_low'),('WL','weekly_low'),('VAL','vp_val')]:
            v=row.get(col)
            if v and v>0 and abs(low-v)/v<=LEVEL_TOL: levels.append(k)
        if not levels: continue
        lbl='+'.join(levels)
        if 'VAL' not in lbl: continue
        parts=lbl.split('+')

        if block_pdl_al_val and len(parts)>=3 and 'PDL' in parts and 'AL' in parts: continue
        if block_pdl_val and 'PDL' in parts and 'VAL' in parts and len(parts)==2: continue
        if block_wl_hour and 'WL' in parts and (ts//3_600_000)%24==block_wl_hour: continue
        if al_val_obi_thresh and lbl=='AL+VAL' and float(row.get('obi10_mean') or 0)>al_val_obi_thresh: continue

        c,o,h,l=float(row['close']),float(row['open']),float(row['high']),float(row['low'])
        rng=h-l
        if rng<=0: continue
        wick_dn=(min(c,o)-l)/rng
        if not (0.30<wick_dn<0.85) or c<o: continue

        obi=float(row.get('obi10_mean') or 0); delta=float(row.get('delta') or 0)
        if obi<=0.05 and delta<=0: continue

        h1d=h1_ctx.get((ts//H1_MS)*H1_MS)
        if h1d is None: continue
        h1l,h1a=h1d; sl_=h1l-0.40*h1a; d=row['close']-sl_
        if d<=0: continue
        if not (MIN_STOP<=d/row['close']<=MAX_STOP): continue

        reg=str(row.get('regime') or '')
        tgt=1.5 if (regime_target and reg=='Chop') else (3.0 if (regime_target and reg=='Expansion') else 2.0)

        in_t=True; ep=row['close']; sl=sl_; dist=d; tp=ep+tgt*dist
        t_start=i; t_entry=ts; t_lbl=lbl; t_sess=sess; cvd_streak=0

    return trades, cap

print('Cargando M1...')
df=pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
for c in df.columns:
    if df[c].dtype==object: df[c]=df[c].fillna('')
    elif df[c].dtype==float: df[c]=df[c].fillna(0.0)

# ── ANALISIS BASE ─────────────────────────────────────────────────────────────
trades_base, _ = simulate(df, cvd_req=3, regime_target=True)
all_t  = trades_base
oot    = [t for t in all_t if t['oos']]
ist    = [t for t in all_t if not t['oos']]

def wr_avg(ts):
    if not ts: return 0,0.0,0.0
    n=len(ts); w=sum(1 for t in ts if t['result_r']>0)
    return n,w/n*100,sum(t['result_r'] for t in ts)/n

ni,wi,ai=wr_avg(ist); no,wo,ao=wr_avg(oot)
print(f"\nLONGS BASE (cvd=3, regime target)")
print(f"  IS : n={ni}  WR={wi:.1f}%  AvgR={ai:+.3f}  tpd={ni/260:.1f}")
print(f"  OOS: n={no}  WR={wo:.1f}%  AvgR={ao:+.3f}  tpd={no/90:.1f}")

print(f"\n-- Por nivel (IS+OOS) -------------------------------------------")
by_lbl=defaultdict(list)
for t in all_t: by_lbl[t['level']].append(t['result_r'])
for lbl in sorted(by_lbl,key=lambda x:-len(by_lbl[x])):
    rs=by_lbl[lbl]; n=len(rs); w=sum(1 for r in rs if r>0)
    flag=' <<' if w/n<0.42 else (' **' if w/n>0.55 else '')
    print(f"  {lbl:<22}  n={n:>4}  WR={w/n*100:>5.1f}%  AvgR={sum(rs)/n:>+.3f}{flag}")

print(f"\n-- Por sesion OOS -----------------------------------------------")
by_sess=defaultdict(list)
for t in oot: by_sess[t['sess']].append(t['result_r'])
for sess in ['london','overlap','ny']:
    rs=by_sess[sess]; n=len(rs)
    if n<5: continue
    w=sum(1 for r in rs if r>0)
    flag=' <<' if w/n<0.44 else (' **' if w/n>0.55 else '')
    print(f"  {sess:<10}  n={n:>3}  WR={w/n*100:>5.1f}%  AvgR={sum(rs)/n:>+.3f}{flag}")

print(f"\n-- WL trades por hora UTC (todos) ------------------------------")
wl_trades=[t for t in all_t if 'WL' in t['level']]
by_hr=defaultdict(list)
for t in wl_trades:
    hr=(t['ts_ms']//3_600_000)%24; by_hr[hr].append(t['result_r'])
for hr in sorted(by_hr):
    rs=by_hr[hr]; n=len(rs); w=sum(1 for r in rs if r>0)
    if n<3: continue
    flag=' <<' if w/n<0.40 else (' **' if w/n>0.60 else '')
    print(f"  {hr:02d}h UTC  n={n:>3}  WR={w/n*100:>5.1f}%  AvgR={sum(rs)/n:>+.3f}{flag}")

print(f"\n-- AL+VAL por OBI bins (todos) ---------------------------------")
al_val=[t for t in all_t if t['level']=='AL+VAL']
m1_sub=df[['ts_ms','obi10_mean']].copy()
al_df=pd.DataFrame(al_val).merge(m1_sub,on='ts_ms',how='left')
bins=[-1,0,0.10,0.20,0.30,1]; lbls=['<0','0-0.10','0.10-0.20','0.20-0.30','>0.30']
al_df['ob']=pd.cut(al_df['obi10_mean'].fillna(0),bins=bins,labels=lbls)
for b in lbls:
    sub=al_df[al_df['ob']==b]
    if len(sub)<5: continue
    w=sub['win'].mean()*100 if 'win' in sub else (sub['result_r']>0).mean()*100
    print(f"  OBI {b:<12}  n={len(sub):>3}  WR={w:>5.1f}%  AvgR={sub['result_r'].mean():>+.3f}")

# ── SWEEP DE CALIBRACION ──────────────────────────────────────────────────────
print(f"\n{'='*70}")
print(f"SWEEP CALIBRACION")
print(f"{'Config':<40}  {'n_IS':>5}  {'WR_IS':>6}  {'n_OOS':>5}  {'WR_OOS':>6}  {'AvgR_OOS':>9}  {'Capital':>10}")
print('-'*100)

configs=[
    (False,False,None, None, True, 'BASE longs (cvd=3, regime)'),
    (True, False,None, None, True, '+ block PDL+VAL'),
    (False,True, None, None, True, '+ block PDL+AL+VAL'),
    (True, True, None, None, True, '+ block PDL+VAL + PDL+AL+VAL'),
    (True, True, 15,   None, True, '+ block WL 15h UTC'),
    (True, True, 7,    None, True, '+ block WL 07h UTC'),
    (True, True, None, 0.15, True, '+ AL+VAL OBI>0.15 block'),
    (True, True, 15,   0.15, True, '+ WL 15h + AL+VAL OBI block'),
    (True, True, 15,   0.15, False,'+ sin London'),
]
for pdv,pdav,wlh,alobi,lon,label in configs:
    t,cap=simulate(df,block_pdl_val=pdv,block_pdl_al_val=pdav,
                   block_wl_hour=wlh,al_val_obi_thresh=alobi,london=lon,
                   cvd_req=3,regime_target=True)
    ist2=[x for x in t if not x['oos']]; oot2=[x for x in t if x['oos']]
    ni,wi,_=wr_avg(ist2); no,wo,ao=wr_avg(oot2)
    print(f"{label:<40}  {ni:>5}  {wi:>5.1f}%  {no:>5}  {wo:>5.1f}%  {ao:>+.3f}      ${cap:>9,.0f}")
