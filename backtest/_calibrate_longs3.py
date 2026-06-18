"""
_calibrate_longs3.py — Segunda ronda de calibracion
Longs v1: block PDL+AL+VAL ya aplicado.
Exploramos:
  A) VAL solo y AL+VAL por hora UTC — identificar horas toxicas
  B) Flujo: OBI>0.05 OR delta>0 vs variantes mas estrictas
  C) Sesion overlap — es la mas debil, vale la pena?
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

def simulate(df, block_hrs_val=set(), block_hrs_al=set(),
             obi_thresh=0.05, require_delta=False, no_overlap=False):
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
                trades.append({'ts_ms':t_entry,'result_r':round(pnl_r,3),
                                'oos':ts>=OOS_MS,'level':t_lbl,'sess':t_sess})
                cap+=pnl_usd; in_t=False; cvd_streak=0
            continue

        month=pd.Timestamp(ts,unit='ms',tz='UTC').month+pd.Timestamp(ts,unit='ms',tz='UTC').year*12
        if month!=current_month: monthly_risk=cap*RISK_PCT; current_month=month

        sess=session(ts)
        if not sess: continue
        if no_overlap and sess=='overlap': continue

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
        if lbl=='VAL'    and hr in block_hrs_val: continue
        if lbl=='AL+VAL' and hr in block_hrs_al:  continue

        c,o,h,l=float(row['close']),float(row['open']),float(row['high']),float(row['low'])
        rng=h-l
        if rng<=0: continue
        if not (0.30<(min(c,o)-l)/rng<0.85) or c<o: continue

        obi=float(row.get('obi10_mean') or 0); delta=float(row.get('delta') or 0)
        if require_delta:
            if delta<=0: continue   # exigir delta positivo (compradores netos)
        else:
            if obi<=obi_thresh and delta<=0: continue

        h1d=h1_ctx.get((ts//H1_MS)*H1_MS)
        if h1d is None: continue
        h1l,h1a=h1d; sl_=h1l-0.40*h1a; d=row['close']-sl_
        if d<=0: continue
        if not (MIN_STOP<=d/row['close']<=MAX_STOP): continue
        reg=str(row.get('regime') or '')
        tgt=1.5 if reg=='Chop' else (3.0 if reg=='Expansion' else 2.0)
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

# ── ANALISIS VAL SOLO POR HORA ────────────────────────────────────────────────
trades_v1,_=simulate(df)
all_t=trades_v1

print('\n-- VAL solo: WR por hora UTC (IS+OOS) ---------------------------')
val_only=[t for t in all_t if t['level']=='VAL']
by_hr=defaultdict(list)
for t in val_only: by_hr[(t['ts_ms']//3_600_000)%24].append(t['result_r'])
for hr in range(7,20):
    rs=by_hr.get(hr,[])
    if len(rs)<8: continue
    n=len(rs); w=sum(1 for r in rs if r>0)
    flag=' <<' if w/n<0.42 else (' **' if w/n>0.56 else '')
    print(f'  {hr:02d}h  n={n:>3}  WR={w/n*100:>5.1f}%  AvgR={sum(rs)/n:>+.3f}{flag}')

print('\n-- AL+VAL: WR por hora UTC (IS+OOS) ------------------------------')
al_val=[t for t in all_t if t['level']=='AL+VAL']
by_hr2=defaultdict(list)
for t in al_val: by_hr2[(t['ts_ms']//3_600_000)%24].append(t['result_r'])
for hr in range(7,20):
    rs=by_hr2.get(hr,[])
    if len(rs)<8: continue
    n=len(rs); w=sum(1 for r in rs if r>0)
    flag=' <<' if w/n<0.42 else (' **' if w/n>0.56 else '')
    print(f'  {hr:02d}h  n={n:>3}  WR={w/n*100:>5.1f}%  AvgR={sum(rs)/n:>+.3f}{flag}')

# ── SWEEP ─────────────────────────────────────────────────────────────────────
print(f'\n{"Config":<42}  {"n_IS":>4}  {"WR_IS":>6}  {"n_OOS":>4}  {"WR_OOS":>6}  {"AvgR_OOS":>9}  {"Capital":>10}')
print('-'*95)

configs=[
    ({},   {},   0.05, False, False, 'BASE v1 (block PDL+AL+VAL)'),
    ({8,9},{},   0.05, False, False, 'VAL block 08h+09h'),
    ({16}, {},   0.05, False, False, 'VAL block 16h'),
    ({},   {8,9},0.05, False, False, 'AL+VAL block 08h+09h'),
    ({},   {16}, 0.05, False, False, 'AL+VAL block 16h'),
    ({},   {},   0.10, False, False, 'Flujo: OBI>0.10 OR delta>0'),
    ({},   {},   0.05, True,  False, 'Flujo: solo delta>0 (neto compradores)'),
    ({},   {},   0.05, False, True,  'Sin Overlap'),
]

for bv, ba, obi_t, req_d, no_ov, label in configs:
    t,cap=simulate(df, block_hrs_val=bv, block_hrs_al=ba,
                   obi_thresh=obi_t, require_delta=req_d, no_overlap=no_ov)
    ist=[x for x in t if not x['oos']]; oot=[x for x in t if x['oos']]
    ni,wi,_=s(ist); no,wo,ao=s(oot)
    print(f'{label:<42}  {ni:>4}  {wi:>5.1f}%  {no:>4}  {wo:>5.1f}%  {ao:>+.3f}      ${cap:>9,.0f}')
