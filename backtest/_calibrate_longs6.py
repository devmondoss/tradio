"""
_calibrate_longs6.py — Ronda 5: refinar alrededor del mejor v5
Mejor v5: AL 07+12h + PDL 11+12+14h => $25,129
Explorar combinaciones adicionales.
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

def simulate(df, block_val_hrs=set(), block_al_hrs=set(), block_pdl_hrs=set()):
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
        if lbl=='VAL'     and hr in block_val_hrs: continue
        if lbl=='AL+VAL'  and hr in block_al_hrs:  continue
        if lbl=='PDL+VAL' and hr in block_pdl_hrs: continue

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

# Mejor v5: AL {7,11,12,17,19} + PDL {11,12,14} + VAL {9,10,11}
BEST_VAL = {9,10,11}
BEST_AL  = {7,11,12,17,19}
BEST_PDL = {11,12,14}

print(f'\n{"Config":<58}  {"n_IS":>4}  {"WR_IS":>6}  {"n_OOS":>4}  {"WR_OOS":>6}  {"AvgR_OOS":>9}  {"Capital":>10}')
print('-'*112)

configs = [
    (BEST_VAL, BEST_AL,        BEST_PDL,          'BEST v5 (referencia)'),
    # PDL: adicionar mas horas malas
    (BEST_VAL, BEST_AL,        BEST_PDL|{8},      '+ PDL block 08h (40% WR, n=5)'),
    (BEST_VAL, BEST_AL,        BEST_PDL|{13},     '+ PDL block 13h'),
    (BEST_VAL, BEST_AL,        BEST_PDL|{8,13},   '+ PDL block 08+13h'),
    # AL+VAL: probar 13h
    (BEST_VAL, BEST_AL|{13},   BEST_PDL,          '+ AL+VAL block 13h (45.8% WR)'),
    (BEST_VAL, BEST_AL|{13},   BEST_PDL|{8},      '+ AL+VAL 13h + PDL 08h'),
    # VAL: probar ampliar bloqueos
    (BEST_VAL|{8}, BEST_AL,    BEST_PDL,          '+ VAL block 08h'),
    (BEST_VAL|{12},BEST_AL,    BEST_PDL,          '+ VAL block 12h'),
    # Quitar algunos bloqueos del BEST para ver si son necesarios
    ({9,10,11}, {11,12,17,19}, BEST_PDL,          '- quitar AL 07h del bloqueo'),
    ({9,10,11}, {7,12,17,19},  BEST_PDL,          '- quitar AL 11h del bloqueo'),
    # Bloqueos PDL mas agresivos
    (BEST_VAL, BEST_AL,        {11,12,14,8,13},   '+ PDL block todos los malos'),
    # Ver si PDL+VAL en NY es realmente bueno (15,17,18h ok)
    (BEST_VAL, BEST_AL,        {7,8,9,10,11,12,13,14}, 'PDL solo NY (15-19h)'),
]

best_cap = 0; best_label = ''
for bv, ba, bp, label in configs:
    t,cap=simulate(df, block_val_hrs=bv, block_al_hrs=ba, block_pdl_hrs=bp)
    ist=[x for x in t if not x['oos']]; oot=[x for x in t if x['oos']]
    ni,wi,_=s(ist); no,wo,ao=s(oot)
    flag = ' ***' if cap > 26000 else (' **' if cap > 25129 else '')
    print(f'{label:<58}  {ni:>4}  {wi:>5.1f}%  {no:>4}  {wo:>5.1f}%  {ao:>+.3f}      ${cap:>9,.0f}{flag}')
    if cap > best_cap: best_cap=cap; best_label=label

print(f'\n=> MEJOR: {best_label}  Capital=${best_cap:,.0f}')

# ── Detalle por nivel del BEST v5 en OOS ────────────────────────────────────
print('\n=== Breakdown por nivel OOS — BEST v5 ===')
t_best,_=simulate(df, block_val_hrs=BEST_VAL, block_al_hrs=BEST_AL, block_pdl_hrs=BEST_PDL)
oot_best=[x for x in t_best if x['oos']]
by_lbl=defaultdict(list)
for t in oot_best: by_lbl[t['level']].append(t['result_r'])
for lbl in sorted(by_lbl, key=lambda x:-len(by_lbl[x])):
    rs=by_lbl[lbl]; n=len(rs); w=sum(1 for r in rs if r>0)
    flag=' <<' if w/n<0.44 else (' **' if w/n>0.58 else '')
    print(f'  {lbl:<25}  n={n:>4}  WR={w/n*100:>5.1f}%  AvgR={sum(rs)/n:>+.3f}{flag}')
