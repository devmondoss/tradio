"""
_shorts_score_sizing.py
Aplicar score sizing al sistema de shorts.
Para shorts los features son "espejo":
  - sell_vol alto: vendedores agresivos en resistencia -> bueno
  - buy_vol alto:  compradores ATRAPADOS en resistencia -> bueno (trapped longs)
  - delta < Q50:   presion neta vendedora -> bueno
  - vr > Q50:      volumen institucional -> bueno

Proceso:
  1. Discriminant analysis en trades shorts
  2. Calcular thresholds IS
  3. Sweep de sizing modes
  4. Comparar con flat 2%
"""
import pandas as pd, numpy as np
from collections import defaultdict
from scipy import stats as scipy_stats

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

def simulate_shorts(df, sizing_mode='flat', base_risk=0.02,
                    sv_q50=None, bv_q50=None, dlt_q50=None, vr_q50=None,
                    ask_wall_block=False):
    """Shorts canónico v5 + optional score sizing."""
    h1=resamp(df,H1_MS); h1['atr']=atr14(h1['high'].values,h1['low'].values,h1['close'].values)
    h1_ctx={int(r.ts_ms):(float(r.high),float(r.atr)) for r in h1.itertuples()}
    rows=df.to_dict('records'); trades=[]; in_t=False
    ep=sl=tp=dist=0.0; t_start=t_entry=0; t_lbl=t_sess=''; cvd_streak=0
    cap=CAPITAL; monthly_risk=CAPITAL*base_risk; current_month=-1
    entry_risk=0.0; entry_score=0

    BLOCK_VAH_LBLS = {'PDH+VAH','PDH+AH+VAH'}

    def score(row):
        if sv_q50 is None: return 2
        sv  = float(row.get('sell_vol') or 0)
        bv  = float(row.get('buy_vol')  or 0)
        dlt = float(row.get('delta')    or 0)
        vr  = float(row.get('vr')       or 0)
        # Shorts: sell_vol alto (sellers en resistencia), buy_vol alto (trapped longs),
        # delta NEGATIVO (presion vendedora), vr alto (volumen)
        return sum([sv>=sv_q50, bv>=bv_q50, dlt<=dlt_q50, vr>=vr_q50])

    BOOST = [0.20, 0.50, 1.00, 1.50, 2.00]

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
        if lbl in BLOCK_VAH_LBLS: continue

        hr=(ts//3_600_000)%24
        if 'WH' in lbl.split('+') and hr==15: continue
        if lbl=='AH+VAH' and float(row.get('obi10_mean') or 0) < -0.15: continue

        if ask_wall_block and bool(row.get('ask_wall')): continue

        c,o,h,l=float(row['close']),float(row['open']),float(row['high']),float(row['low'])
        rng=h-l
        if rng<=0: continue
        wick_up=(h-max(c,o))/rng
        if not (0.30<wick_up<0.85) or c>o: continue
        obi=float(row.get('obi10_mean') or 0); delta=float(row.get('delta') or 0)
        if obi>=-0.05 and delta>=0: continue

        h1d=h1_ctx.get((ts//H1_MS)*H1_MS)
        if h1d is None: continue
        h1h,h1a=h1d; sl_=h1h+0.40*h1a; d=sl_-row['close']
        if d<=0: continue
        if not (MIN_STOP<=d/row['close']<=MAX_STOP): continue
        reg=str(row.get('regime') or '')
        tgt=1.5 if reg=='Chop' else (3.0 if reg=='Expansion' else 2.0)

        sc=score(row)
        if   sizing_mode=='flat':    risk=monthly_risk
        elif sizing_mode=='boost_a': risk=monthly_risk*BOOST[sc]
        elif sizing_mode=='boost_b': risk=monthly_risk*[0.30,0.60,1.00,1.40,1.80][sc]
        elif sizing_mode=='boost_c': risk=monthly_risk*[0.40,0.70,1.00,1.30,1.60][sc]
        else: risk=monthly_risk

        in_t=True; ep=row['close']; sl=sl_; dist=d; tp=ep-tgt*dist
        entry_risk=risk; entry_score=sc; t_start=i; t_entry=ts; t_lbl=lbl; t_sess=sess; cvd_streak=0

    return trades, cap

print('Cargando M1...')
df=pd.read_parquet('data/bybit-spot/processed/btcusdt_m1.parquet').sort_values('ts_ms').reset_index(drop=True)
for c in df.columns:
    if df[c].dtype==object: df[c]=df[c].fillna('')
    elif df[c].dtype==float: df[c]=df[c].fillna(0.0)

# ── Analisis discriminante en trades shorts ──────────────────────────────────
print('\n=== 1. Analisis discriminante shorts (sin scoring) ===')
t_base, _ = simulate_shorts(df, sizing_mode='flat')
tdf=pd.DataFrame(t_base)
feat_cols=['sell_vol','buy_vol','delta','vr','obi10_mean','obi10_min','obi_range',
           'spread_mean','vpin','cvd_slope','prev_bar_delta','ask_wall',
           'thin_above','big_trade_bullish','big_trade_bearish','stacked_imb',
           'cvd_consec_pos','bars_since_low_vr','regime']
feat_cols=[c for c in feat_cols if c in df.columns]
tdf=tdf.merge(df[['ts_ms']+feat_cols], on='ts_ms', how='left')
tdf['win']=(tdf['result_r']>0).astype(int)
wins=tdf[tdf['win']==1]; loss=tdf[tdf['win']==0]
print(f'  Total trades: {len(tdf)}  WR={tdf["win"].mean()*100:.1f}%')

results=[]
for col in feat_cols:
    if tdf[col].dtype in [float,'float64','int16','int64','int32']:
        wv=wins[col].dropna().values; lv=loss[col].dropna().values
        if len(wv)<10 or len(lv)<10: continue
        ks,_=scipy_stats.ks_2samp(wv,lv)
        results.append({'col':col,'type':'num','ks':ks,'w_mean':wv.mean(),'l_mean':lv.mean()})
    elif tdf[col].dtype==bool:
        t_wr=tdf[tdf[col]==True]['win'].mean()*100; f_wr=tdf[tdf[col]==False]['win'].mean()*100
        results.append({'col':col,'type':'bool','ks':abs(t_wr-f_wr)/100,'w_mean':t_wr,'l_mean':f_wr})
    elif tdf[col].dtype==object:
        cats=[(c2,len(tdf[tdf[col]==c2]),tdf[tdf[col]==c2]['win'].mean()*100) for c2 in tdf[col].unique() if len(tdf[tdf[col]==c2])>=10]
        if len(cats)<2: continue
        rng=max(x[2] for x in cats)-min(x[2] for x in cats)
        if rng>5: results.append({'col':col,'type':'cat','ks':rng/100,'w_mean':max(x[2] for x in cats),'l_mean':min(x[2] for x in cats),'cats':cats})

results.sort(key=lambda x:-x['ks'])
print(f'\n  {"Feature":<28} {"Type":<5} {"KS":>6}  Detalle')
for r in results[:18]:
    if r['type']=='num':
        print(f'  {r["col"]:<28} num   {r["ks"]:>6.3f}  win_mean={r["w_mean"]:+.3f}  loss_mean={r["l_mean"]:+.3f}  diff={r["w_mean"]-r["l_mean"]:+.3f}')
    elif r['type']=='bool':
        flag=' ***' if abs(r['w_mean']-r['l_mean'])>8 else ''
        print(f'  {r["col"]:<28} bool  {r["ks"]:>6.3f}  TRUE={r["w_mean"]:.1f}%WR  FALSE={r["l_mean"]:.1f}%WR  diff={r["w_mean"]-r["l_mean"]:+.1f}pp{flag}')
    elif r['type']=='cat':
        cats_str='  '.join(f'{c}={wr:.1f}%(n={n})' for c,n,wr in sorted(r['cats'],key=lambda x:-x[2]))
        print(f'  {r["col"]:<28} cat   {r["ks"]:>6.3f}  {cats_str}')

# ── Cuantiles de las mejores numericas ────────────────────────────────────────
print(f'\n=== Cuartiles numericas (para thresholds) ===')
for col in ['sell_vol','buy_vol','delta','vr','obi10_min']:
    if col not in tdf.columns: continue
    vals=tdf[col].dropna()
    q25,q50,q75=vals.quantile([0.25,0.5,0.75])
    print(f'\n  {col} (Q25={q25:.3f} Q50={q50:.3f} Q75={q75:.3f}):')
    bnd=[(-np.inf,q25),(q25,q50),(q50,q75),(q75,np.inf)]; lbls=['Q1','Q2','Q3','Q4']
    for (lo,hi),lbl in zip(bnd,lbls):
        sub=tdf[(tdf[col]>lo)&(tdf[col]<=hi)]
        if len(sub)<10: continue
        w=sub['win'].mean()*100; avg=sub['result_r'].mean()
        flag=' ***' if w>58 else (' <<' if w<44 else '')
        print(f'    {lbl:<8}  n={len(sub):>4}  WR={w:>5.1f}%  AvgR={avg:>+.3f}{flag}')

# ── Calcular thresholds IS ────────────────────────────────────────────────────
print(f'\n=== 2. Thresholds IS (Jun25-Feb26) ===')
t_is=[x for x in t_base if not x['oos']]
tdf_is=pd.DataFrame(t_is).merge(df[['ts_ms','sell_vol','buy_vol','delta','vr']],on='ts_ms',how='left')
SV_Q50=tdf_is['sell_vol'].quantile(0.50); SV_Q75=tdf_is['sell_vol'].quantile(0.75)
BV_Q50=tdf_is['buy_vol'].quantile(0.50);  BV_Q75=tdf_is['buy_vol'].quantile(0.75)
DLT_Q50=tdf_is['delta'].quantile(0.50);   VR_Q50=tdf_is['vr'].quantile(0.50)
print(f'  sell_vol Q50={SV_Q50:.3f}  buy_vol Q50={BV_Q50:.3f}  delta Q50={DLT_Q50:.3f}  vr Q50={VR_Q50:.3f}')

# ── Score distribution actual (IS thresholds) ────────────────────────────────
print(f'\n=== 3. Score distribution OOS (IS thresholds) ===')
t_sc,_=simulate_shorts(df,sizing_mode='flat',sv_q50=SV_Q50,bv_q50=BV_Q50,dlt_q50=DLT_Q50,vr_q50=VR_Q50)
oot_sc=[x for x in t_sc if x['oos']]
by_sc=defaultdict(list)
for x in oot_sc: by_sc[x['score']].append(x['result_r'])
for sc in range(5):
    rs=by_sc.get(sc,[])
    if not rs: continue
    n=len(rs); w=sum(1 for r in rs if r>0); avg=sum(rs)/n
    pct=n/len(oot_sc)*100
    flag=' ***' if w/n>=0.58 else (' <<' if w/n<0.44 else '')
    print(f'  Score {sc}/4  n={n:>4}({pct:.0f}%)  WR={w/n*100:>5.1f}%  AvgR={avg:>+.3f}{flag}')

# ── Sweep de sizing ───────────────────────────────────────────────────────────
def s(ts):
    if not ts: return 0,0.0,0.0
    n=len(ts); w=sum(1 for t in ts if t['result_r']>0)
    return n,w/n*100,sum(t['result_r'] for t in ts)/n

kw=dict(sv_q50=SV_Q50,bv_q50=BV_Q50,dlt_q50=DLT_Q50,vr_q50=VR_Q50)

print(f'\n=== 4. Sweep sizing ===')
print(f'\n{"Config":<55}  {"n_IS":>4}  {"WR_IS":>6}  {"n_OOS":>4}  {"WR_OOS":>6}  {"AvgR_OOS":>9}  {"Capital":>10}')
print('-'*109)

def run(label, **kwargs):
    t,cap=simulate_shorts(df,**{**kw,**kwargs})
    ist=[x for x in t if not x['oos']]; oot=[x for x in t if x['oos']]
    ni,wi,_=s(ist); no,wo,ao=s(oot)
    ref=44794
    flag=' ***' if cap>ref*2 else (' **' if cap>ref*1.5 else (' !!' if cap>ref else ''))
    print(f'{label:<55}  {ni:>4}  {wi:>5.1f}%  {no:>4}  {wo:>5.1f}%  {ao:>+.3f}      ${cap:>9,.0f}{flag}')
    return cap

run('FLAT 2% [referencia actual $44,794]',     sizing_mode='flat')
print()
run('BOOST c (0.4 0.7 1.0 1.3 1.6)',           sizing_mode='boost_c')
run('BOOST b (0.3 0.6 1.0 1.4 1.8)',           sizing_mode='boost_b')
run('BOOST a (0.2 0.5 1.0 1.5 2.0)',           sizing_mode='boost_a')
print()
# Validacion: ask_wall para shorts (espejo de bid_wall en longs)
run('ask_wall block + flat',                    sizing_mode='flat', ask_wall_block=True)
run('ask_wall block + BOOST a',                 sizing_mode='boost_a', ask_wall_block=True)
run('ask_wall block + BOOST b',                 sizing_mode='boost_b', ask_wall_block=True)
