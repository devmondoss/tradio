"""
_shorts_score_v2.py
El score original para shorts no era monotono: score 4/4 = peor (45% WR).
Problema: delta para shorts el mejor grupo es el MENOS negativo (Q4, delta > -0.169).
Cuando delta es muy negativo (Q1), el trade es mediocre (50.4% WR).
Esto tiene sentido: para una vela con wick superior + cierre bajista,
se necesita que buyers HAYAN intentado subir (delta menos negativo = wick real).
Si delta es muy negativo desde el inicio, no hay wick real.

Score corregido:
  sell_vol >= Q50    (vendedores en resistencia)
  buy_vol >= Q50     (compradores atrapados)
  delta > Q50        (menos negativo = wick formado por buyers que fallaron)
  vr >= Q50          (volumen alto)

Tambien: cvd_slope y ask_wall como features adicionales.
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

def simulate(df, sizing_mode='flat', base_risk=0.02,
             sv_q50=4.0, bv_q50=2.7, dlt_q50=-1.087, vr_q50=0.93,
             cvd_q50=0.0, score_version='v1',
             ask_wall_boost=False):

    def score_v1(row):
        # ORIGINAL (erroneo para delta)
        sv=float(row.get('sell_vol') or 0); bv=float(row.get('buy_vol') or 0)
        dlt=float(row.get('delta') or 0); vr=float(row.get('vr') or 0)
        return sum([sv>=sv_q50, bv>=bv_q50, dlt<=dlt_q50, vr>=vr_q50])

    def score_v2(row):
        # CORREGIDO: delta > Q50 (menos negativo = mejor para shorts)
        sv=float(row.get('sell_vol') or 0); bv=float(row.get('buy_vol') or 0)
        dlt=float(row.get('delta') or 0); vr=float(row.get('vr') or 0)
        return sum([sv>=sv_q50, bv>=bv_q50, dlt>dlt_q50, vr>=vr_q50])

    def score_v3(row):
        # buy_vol, sell_vol, vr, cvd_slope>0
        sv=float(row.get('sell_vol') or 0); bv=float(row.get('buy_vol') or 0)
        vr=float(row.get('vr') or 0); cvd=float(row.get('cvd_slope') or 0)
        return sum([sv>=sv_q50, bv>=bv_q50, cvd>0, vr>=vr_q50])

    def score_v4(row):
        # buy_vol + sell_vol + delta>Q50 + cvd_slope>0
        sv=float(row.get('sell_vol') or 0); bv=float(row.get('buy_vol') or 0)
        dlt=float(row.get('delta') or 0); cvd=float(row.get('cvd_slope') or 0)
        return sum([sv>=sv_q50, bv>=bv_q50, dlt>dlt_q50, cvd>0])

    scorer = {'v1':score_v1,'v2':score_v2,'v3':score_v3,'v4':score_v4}[score_version]
    BOOST = [0.20,0.50,1.00,1.50,2.00]

    h1=resamp(df,H1_MS); h1['atr']=atr14(h1['high'].values,h1['low'].values,h1['close'].values)
    h1_ctx={int(r.ts_ms):(float(r.high),float(r.atr)) for r in h1.itertuples()}
    rows=df.to_dict('records'); trades=[]; in_t=False
    ep=sl=tp=dist=0.0; t_start=t_entry=0; t_lbl=t_sess=''; cvd_streak=0
    cap=CAPITAL; monthly_risk=CAPITAL*base_risk; current_month=-1
    entry_risk=0.0; entry_score=0
    BLOCK_VAH = {'PDH+VAH','PDH+AH+VAH'}

    for i,row in enumerate(rows):
        ts=int(row['ts_ms'])
        if in_t:
            obi=float(row.get('obi10_mean') or 0); cs=float(row.get('cvd_slope') or 0)
            cvd_streak=(cvd_streak+1) if cs>0 else 0
            cur_r=(ep-row['close'])/dist; reason=None; exit_px=0.0
            if row['high']>=sl:      reason,exit_px='stop',sl
            elif row['low']<=tp:     reason,exit_px='target',tp
            elif i-t_start>=FORWARD: reason,exit_px='timeout',row['close']
            elif cvd_streak>=3 and obi>0.15 and cur_r>=1.0: reason,exit_px='cvd_exit',row['close']
            if reason:
                pnl_r=(ep-exit_px)/dist
                pnl_usd=entry_risk*pnl_r-entry_risk*FEE_RT
                trades.append({'ts_ms':t_entry,'result_r':round(pnl_r,3),'oos':ts>=OOS_MS,
                                'level':t_lbl,'sess':t_sess,'score':entry_score})
                cap+=pnl_usd; in_t=False; cvd_streak=0
            continue

        month=pd.Timestamp(ts,unit='ms',tz='UTC').month+pd.Timestamp(ts,unit='ms',tz='UTC').year*12
        if month!=current_month: monthly_risk=cap*base_risk; current_month=month
        sess=session(ts)
        if not sess: continue

        high=row['high']; levels=[]
        for k,col in [('PDH','prev_day_high'),('AH','asian_high'),('WH','weekly_high'),('VAH','vp_vah')]:
            v=row.get(col)
            if v and v>0 and abs(high-v)/v<=LEVEL_TOL: levels.append(k)
        if not levels: continue
        lbl='+'.join(levels)
        if 'VAH' not in lbl: continue
        if lbl in BLOCK_VAH: continue
        hr=(ts//3_600_000)%24
        if 'WH' in lbl.split('+') and hr==15: continue
        if lbl=='AH+VAH' and float(row.get('obi10_mean') or 0) < -0.15: continue

        c,o,h,l=float(row['close']),float(row['open']),float(row['high']),float(row['low'])
        rng=h-l
        if rng<=0: continue
        if not (0.30<(h-max(c,o))/rng<0.85) or c>o: continue
        obi=float(row.get('obi10_mean') or 0); delta=float(row.get('delta') or 0)
        if obi>=-0.05 and delta>=0: continue

        h1d=h1_ctx.get((ts//H1_MS)*H1_MS)
        if h1d is None: continue
        h1h,h1a=h1d; sl_=h1h+0.40*h1a; d=sl_-row['close']
        if d<=0: continue
        if not (MIN_STOP<=d/row['close']<=MAX_STOP): continue
        reg=str(row.get('regime') or '')
        tgt=1.5 if reg=='Chop' else (3.0 if reg=='Expansion' else 2.0)

        sc=scorer(row)
        if ask_wall_boost and bool(row.get('ask_wall')): sc=min(sc+1,4)

        if   sizing_mode=='flat':    risk=monthly_risk
        elif sizing_mode=='boost_a': risk=monthly_risk*BOOST[sc]
        elif sizing_mode=='boost_b': risk=monthly_risk*[0.30,0.60,1.00,1.40,1.80][sc]
        else: risk=monthly_risk

        in_t=True; ep=row['close']; sl=sl_; dist=d; tp=ep-tgt*dist
        entry_risk=risk; entry_score=sc; t_start=i; t_entry=ts; t_lbl=lbl; t_sess=sess; cvd_streak=0

    return trades, cap

print('Cargando M1...')
df=pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
for c in df.columns:
    if df[c].dtype==object: df[c]=df[c].fillna('')
    elif df[c].dtype==float: df[c]=df[c].fillna(0.0)

# Thresholds IS
t_is_raw,_=simulate(df,sizing_mode='flat')
t_is_only=[x for x in t_is_raw if not x['oos']]
tdf_is=pd.DataFrame(t_is_only).merge(df[['ts_ms','sell_vol','buy_vol','delta','vr','cvd_slope']],on='ts_ms',how='left')
SV=tdf_is['sell_vol'].quantile(0.5); BV=tdf_is['buy_vol'].quantile(0.5)
DL=tdf_is['delta'].quantile(0.5); VR=tdf_is['vr'].quantile(0.5)
CV=0.0  # cvd_slope > 0
print(f'  IS thresholds: sv={SV:.3f}  bv={BV:.3f}  delta={DL:.3f}  vr={VR:.3f}')

kw=dict(sv_q50=SV, bv_q50=BV, dlt_q50=DL, vr_q50=VR)

# Score distribution por version
def score_dist(trades, label):
    oot=[x for x in trades if x['oos']]
    by_sc=defaultdict(list)
    for x in oot: by_sc[x['score']].append(x['result_r'])
    print(f'\n  {label}')
    for sc in range(5):
        rs=by_sc.get(sc,[])
        if not rs: continue
        n=len(rs); w=sum(1 for r in rs if r>0); avg=sum(rs)/n
        pct=n/len(oot)*100
        flag=' ***' if w/n>=0.58 else (' <<' if w/n<0.44 else '')
        print(f'    Score {sc}/4  n={n:>4}({pct:.0f}%)  WR={w/n*100:>5.1f}%  AvgR={avg:>+.3f}{flag}')

def s(ts):
    if not ts: return 0,0.0,0.0
    n=len(ts); w=sum(1 for t in ts if t['result_r']>0)
    return n,w/n*100,sum(t['result_r'] for t in ts)/n

print('\n=== Score distributions por version (OOS) ===')
for sv in ['v1','v2','v3','v4']:
    t,_=simulate(df, sizing_mode='flat', score_version=sv, **kw)
    score_dist(t, f'Score {sv}')

print(f'\n{"Config":<62}  {"n_IS":>4}  {"WR_IS":>6}  {"n_OOS":>4}  {"WR_OOS":>6}  {"AvgR_OOS":>9}  {"Capital":>10}')
print('-'*116)

def run(label, **kwargs):
    t,cap=simulate(df,**{**kw,**kwargs})
    ist=[x for x in t if not x['oos']]; oot=[x for x in t if x['oos']]
    ni,wi,_=s(ist); no,wo,ao=s(oot)
    flag=' ***' if cap>100000 else (' **' if cap>70000 else (' !!' if cap>50000 else ''))
    print(f'{label:<62}  {ni:>4}  {wi:>5.1f}%  {no:>4}  {wo:>5.1f}%  {ao:>+.3f}      ${cap:>9,.0f}{flag}')
    return cap

run('FLAT 2% [referencia]',               sizing_mode='flat',    score_version='v1')
print()
for sv in ['v1','v2','v3','v4']:
    run(f'BOOST a — score {sv}',          sizing_mode='boost_a', score_version=sv)
print()
for sv in ['v2','v3','v4']:
    run(f'BOOST b — score {sv}',          sizing_mode='boost_b', score_version=sv)
print()
# ask_wall como bonus
run('BEST score + ask_wall bonus + BOOST a', sizing_mode='boost_a', score_version='v2', ask_wall_boost=True)
run('BEST score + ask_wall bonus + BOOST b', sizing_mode='boost_b', score_version='v2', ask_wall_boost=True)

# Curva mensual del mejor
print('\n=== Curva mensual (mejor config) ===')
t_best,cap_best=simulate(df, sizing_mode='boost_a', score_version='v2', **kw)
# agregar capitalizacion mensual
monthly_data={}
for trade in t_best:
    ts=trade['ts_ms']
    mo=pd.Timestamp(ts,unit='ms',tz='UTC').strftime('%Y-%m')
    monthly_data[mo]=monthly_data.get(mo,{'n':0,'wins':0,'r':0.0})
    monthly_data[mo]['n']+=1; monthly_data[mo]['r']+=trade['result_r']
    if trade['result_r']>0: monthly_data[mo]['wins']+=1
for mo,d in sorted(monthly_data.items()):
    wr=d['wins']/d['n']*100 if d['n'] else 0
    is_l='IS ' if mo<'2026-03' else 'OOS'
    print(f'  {mo} {is_l}  n={d["n"]:>3}  WR={wr:>5.1f}%  TotalR={d["r"]:>+6.2f}')
print(f'  Capital final: ${cap_best:,.0f}')
