"""
_analyze_longs_quality.py
Entender qué hace buenos a WL+VAL y AL+VAL 14-18h UTC.
Buscar mas trades de esa calidad.
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

def simulate(df, extra_levels=None, al_val_hours=None):
    """
    extra_levels: lista adicional de (nombre, columna) a incluir
    al_val_hours: si no None, solo permitir AL+VAL en esas horas
    """
    if extra_levels is None: extra_levels = []
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
        base_levels = [('PDL','prev_day_low'),('AL','asian_low'),('WL','weekly_low'),('VAL','vp_val')]
        for k,col in base_levels + extra_levels:
            v=row.get(col)
            if v and v>0 and abs(low-v)/v<=LEVEL_TOL: levels.append(k)
        if not levels: continue
        lbl='+'.join(levels)
        if 'VAL' not in lbl: continue
        parts=lbl.split('+')
        if len(parts)>=3 and 'PDL' in parts and 'AL' in parts: continue

        hr=(ts//3_600_000)%24
        # Bloqueos v2
        if lbl=='VAL' and hr in {9,10,11}: continue
        if lbl=='AL+VAL' and hr in {11,17,19}: continue

        # Filtro de horas para AL+VAL
        if al_val_hours is not None and lbl=='AL+VAL' and hr not in al_val_hours: continue

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

trades_v2, _ = simulate(df)
oot = [t for t in trades_v2 if t['oos']]

def s(ts):
    if not ts: return 0,0.0,0.0
    n=len(ts); w=sum(1 for t in ts if t['result_r']>0)
    return n,w/n*100,sum(t['result_r'] for t in ts)/n

# ── ANALISIS WL+VAL: microestructura win vs loss ──────────────────────────────
print('\n=== WL+VAL — microestructura WIN vs LOSS (IS+OOS) ===')
wl_val = [t for t in trades_v2 if 'WL' in t['level'] and 'VAL' in t['level']]
m1_sub = df[['ts_ms','obi10_mean','delta','cvd_slope','big_trade_bullish',
             'thin_below','bid_wall','stacked_imb','regime','cvd_consec_pos']].copy()
wdf = pd.DataFrame(wl_val).merge(m1_sub, on='ts_ms', how='left')
wdf['win'] = wdf['result_r'] > 0
# convert potential string cols to numeric flags
for f in ['big_trade_bullish','thin_below','bid_wall','stacked_imb']:
    if f in wdf.columns:
        col = wdf[f]
        if col.dtype == object:
            wdf[f] = col.apply(lambda x: 1 if str(x).lower() in ('true','1','bullish','yes') else (0 if pd.isna(x) or str(x).lower() in ('false','0','','none') else float(x) if str(x).replace('.','').replace('-','').isnumeric() else 0))
        else:
            wdf[f] = pd.to_numeric(wdf[f], errors='coerce').fillna(0)
wins = wdf[wdf['win']==True]; loss = wdf[wdf['win']==False]
print(f'  Total: n={len(wdf)}  WR={wdf["win"].mean()*100:.1f}%  AvgR={wdf["result_r"].mean():+.3f}')
print(f'  OBI mean — WIN: {wins["obi10_mean"].mean():+.3f}  LOSS: {loss["obi10_mean"].mean():+.3f}')
print(f'  Delta mean  — WIN: {wins["delta"].mean():+.0f}  LOSS: {loss["delta"].mean():+.0f}')
for f in ['big_trade_bullish','thin_below','bid_wall','stacked_imb']:
    if f not in wdf.columns: continue
    w_pct = wins[f].mean()*100; l_pct = loss[f].mean()*100
    if abs(w_pct-l_pct)>10:
        print(f'  {f:<22} WIN={w_pct:.0f}%  LOSS={l_pct:.0f}%  diff={w_pct-l_pct:+.0f}pp')

# ── ANALISIS AL+VAL POR HORA — detalle 12-20h ─────────────────────────────────
print('\n=== AL+VAL hora 12-20h UTC (IS+OOS) — por que Overlap/NY es mejor ===')
al_trades=[t for t in trades_v2 if t['level']=='AL+VAL']
by_hr=defaultdict(list)
for t in al_trades: by_hr[(t['ts_ms']//3_600_000)%24].append(t['result_r'])
for hr in range(7,20):
    rs=by_hr.get(hr,[])
    if len(rs)<6: continue
    n=len(rs); w=sum(1 for r in rs if r>0)
    flag=' <<' if w/n<0.44 else (' **' if w/n>0.56 else '')
    print(f'  {hr:02d}h  n={n:>3}  WR={w/n*100:>5.1f}%  AvgR={sum(rs)/n:>+.3f}{flag}')

# ── AL+VAL SOLO EN HORAS BUENAS (12-16h + 18h) ───────────────────────────────
print('\n=== Test: AL+VAL SOLO en horas de calidad (12-16h, 18h) ===')
GOOD_AL_HRS = {12,13,14,15,16,18}
trades_alq, cap_alq = simulate(df, al_val_hours=GOOD_AL_HRS)
ist2=[t for t in trades_alq if not t['oos']]; oot2=[t for t in trades_alq if t['oos']]
ni,wi,_=s(ist2); no,wo,ao=s(oot2)
print(f'  n_IS={ni}  WR_IS={wi:.1f}%  n_OOS={no}  WR_OOS={wo:.1f}%  AvgR={ao:+.3f}  Capital=${cap_alq:,.0f}')

# ── NUEVOS NIVELES: swing_low_50 ──────────────────────────────────────────────
print('\n=== swing_low_50 como nivel adicional ===')
if 'swing_low_50' in df.columns:
    trades_sw, cap_sw = simulate(df, extra_levels=[('SL50','swing_low_50')])
    ist_sw=[t for t in trades_sw if not t['oos']]; oot_sw=[t for t in trades_sw if t['oos']]
    ni,wi,_=s(ist_sw); no,wo,ao=s(oot_sw)
    print(f'  n_IS={ni}  WR_IS={wi:.1f}%  n_OOS={no}  WR_OOS={wo:.1f}%  AvgR={ao:+.3f}  Capital=${cap_sw:,.0f}')
    # breakdown por nivel con SL50
    by_lbl2=defaultdict(list)
    for t in trades_sw: by_lbl2[t['level']].append(t['result_r'])
    new_lbls=[l for l in by_lbl2 if 'SL50' in l]
    for lbl in sorted(new_lbls, key=lambda x:-len(by_lbl2[x])):
        rs=by_lbl2[lbl]; n=len(rs); w=sum(1 for r in rs if r>0)
        if n<10: continue
        print(f'    {lbl:<25}  n={n:>4}  WR={w/n*100:>5.1f}%  AvgR={sum(rs)/n:>+.3f}')
else:
    print('  swing_low_50 no disponible')

# ── PDL+VAL POR HORA — ya tenia WR 55% ───────────────────────────────────────
print('\n=== PDL+VAL por hora UTC (IS+OOS) — nivel que ya es bueno ===')
pdl_val=[t for t in trades_v2 if t['level']=='PDL+VAL']
by_hr3=defaultdict(list)
for t in pdl_val: by_hr3[(t['ts_ms']//3_600_000)%24].append(t['result_r'])
for hr in range(7,20):
    rs=by_hr3.get(hr,[])
    if len(rs)<5: continue
    n=len(rs); w=sum(1 for r in rs if r>0)
    flag=' <<' if w/n<0.44 else (' **' if w/n>0.60 else '')
    print(f'  {hr:02d}h  n={n:>3}  WR={w/n*100:>5.1f}%  AvgR={sum(rs)/n:>+.3f}{flag}')

# ── SWEEP FINAL ───────────────────────────────────────────────────────────────
print(f'\n{"Config":<48}  {"n_IS":>4}  {"WR_IS":>6}  {"n_OOS":>4}  {"WR_OOS":>6}  {"AvgR_OOS":>9}  {"Capital":>10}')
print('-'*102)

def run(extra=None, al_hrs=None, label=''):
    t,cap=simulate(df, extra_levels=extra or [], al_val_hours=al_hrs)
    ist=[x for x in t if not x['oos']]; oot=[x for x in t if x['oos']]
    ni,wi,_=s(ist); no,wo,ao=s(oot)
    print(f'{label:<48}  {ni:>4}  {wi:>5.1f}%  {no:>4}  {wo:>5.1f}%  {ao:>+.3f}      ${cap:>9,.0f}')
    return no, wo, ao, cap

run(label='BASE v2 (VAL 9-11 + AL 11/17/19 blocked)')
run(al_hrs=GOOD_AL_HRS, label='+ AL+VAL solo horas buenas (12-16+18h)')
if 'swing_low_50' in df.columns:
    run(extra=[('SL50','swing_low_50')], label='+ swing_low_50 nivel')
    run(extra=[('SL50','swing_low_50')], al_hrs=GOOD_AL_HRS, label='+ SL50 + AL horas buenas')
