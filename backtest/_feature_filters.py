"""
_feature_filters.py
Probar los filtros basados en features reales (no horas).
Referencia: base v2 con bloqueos de hora => $23,742
Objetivo: superar con filtros de condicion de mercado.
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

def simulate(df,
             block_big_bear=False,       # big_trade_bearish=True -> skip
             block_bid_wall=False,        # bid_wall=True -> skip
             block_bearish_imb=False,     # stacked_imb='Bearish' -> skip
             obi_min_thresh=None,         # obi10_min must be < thresh (e.g. -0.81)
             sell_vol_thresh=None,        # sell_vol must be > thresh (absorption)
             buy_vol_thresh=None,         # buy_vol must be > thresh
             delta_thresh=None,           # delta must be > thresh
             block_expansion=False,       # bloquear Expansion regime
             use_hour_blocks=False,       # comparacion con bloques de hora
             ):
    h1=resamp(df,H1_MS); h1['atr']=atr14(h1['high'].values,h1['low'].values,h1['close'].values)
    h1_ctx={int(r.ts_ms):(float(r.low),float(r.atr)) for r in h1.itertuples()}
    rows=df.to_dict('records'); trades=[]; in_t=False
    ep=sl=tp=dist=0.0; t_start=t_entry=0; t_lbl=t_sess=''; cvd_streak=0
    cap=CAPITAL; monthly_risk=CAPITAL*RISK_PCT; current_month=-1

    BLOCK_VAL_HRS = {9,10,11}
    BLOCK_AL_HRS  = {7,11,12,17,19}
    BLOCK_PDL_HRS = {8,11,12,14}

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
                trades.append({'ts_ms':t_entry,'result_r':round(pnl_r,3),'oos':ts>=OOS_MS,'level':t_lbl,'sess':t_sess})
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

        # ── FEATURE FILTERS ──────────────────────────────────────────
        reg = str(row.get('regime') or '')
        if block_expansion and reg=='Expansion': continue

        if block_big_bear and bool(row.get('big_trade_bearish')): continue

        if block_bid_wall and bool(row.get('bid_wall')): continue

        if block_bearish_imb and str(row.get('stacked_imb'))=='Bearish': continue

        if obi_min_thresh is not None:
            obi_min = float(row.get('obi10_min') or 0)
            if obi_min > obi_min_thresh: continue  # necesitamos min MUY negativo

        if sell_vol_thresh is not None:
            sv = float(row.get('sell_vol') or 0)
            if sv < sell_vol_thresh: continue

        if buy_vol_thresh is not None:
            bv = float(row.get('buy_vol') or 0)
            if bv < buy_vol_thresh: continue

        if delta_thresh is not None:
            if delta < delta_thresh: continue

        # ─────────────────────────────────────────────────────────────
        h1d=h1_ctx.get((ts//H1_MS)*H1_MS)
        if h1d is None: continue
        h1l,h1a=h1d; sl_=h1l-0.40*h1a; d=row['close']-sl_
        if d<=0: continue
        if not (MIN_STOP<=d/row['close']<=MAX_STOP): continue
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

print(f'\n{"Config":<60}  {"n_IS":>4}  {"WR_IS":>6}  {"n_OOS":>4}  {"WR_OOS":>6}  {"AvgR_OOS":>9}  {"Capital":>10}')
print('-'*114)

def run(label, **kwargs):
    t,cap=simulate(df,**kwargs)
    ist=[x for x in t if not x['oos']]; oot=[x for x in t if x['oos']]
    ni,wi,_=s(ist); no,wo,ao=s(oot)
    flag = ' ***' if cap>27000 else (' **' if cap>25745 else (' !!' if cap>23742 else ''))
    print(f'{label:<60}  {ni:>4}  {wi:>5.1f}%  {no:>4}  {wo:>5.1f}%  {ao:>+.3f}      ${cap:>9,.0f}{flag}')
    return no,wo,ao,cap

# Referencias
run('BASE sin hora ni feature filters',)
run('BASE v2 con bloques de hora',                use_hour_blocks=True)
run('BEST v5+PDL08 (mejor actual)',               use_hour_blocks=True)  # este no tiene PDL08 pero sirve de ref

print()
# ── Filtros de feature solos (sin bloques de hora) ─────────────────────────
run('block big_trade_bearish',                    block_big_bear=True)
run('block bid_wall',                             block_bid_wall=True)
run('block stacked_imb=Bearish',                  block_bearish_imb=True)
run('block Expansion',                            block_expansion=True)
run('obi10_min < -0.81 (absorcion real)',         obi_min_thresh=-0.81)
run('obi10_min < -0.85',                          obi_min_thresh=-0.85)
run('sell_vol > 1.54 (Q25 absorcion)',            sell_vol_thresh=1.54)
run('sell_vol > 2.98 (Q50 absorcion)',            sell_vol_thresh=2.98)
run('buy_vol > 2.13 (Q25 volumen comprador)',     buy_vol_thresh=2.13)
run('delta > 0.86 (Q50 delta positivo)',          delta_thresh=0.86)

print()
# ── Combinaciones (sin bloques de hora) ────────────────────────────────────
run('big_bear + bid_wall + bearish_imb',
    block_big_bear=True, block_bid_wall=True, block_bearish_imb=True)
run('big_bear + obi_min<-0.81',
    block_big_bear=True, obi_min_thresh=-0.81)
run('big_bear + sell_vol>1.54',
    block_big_bear=True, sell_vol_thresh=1.54)
run('big_bear + bid_wall + obi_min<-0.81',
    block_big_bear=True, block_bid_wall=True, obi_min_thresh=-0.81)
run('big_bear + bid_wall + bearish_imb + obi_min<-0.81',
    block_big_bear=True, block_bid_wall=True, block_bearish_imb=True, obi_min_thresh=-0.81)
run('big_bear + bearish_imb + sell_vol>1.54',
    block_big_bear=True, block_bearish_imb=True, sell_vol_thresh=1.54)

print()
# ── Feature filters VS hora blocks ─────────────────────────────────────────
run('HORA: v2 + big_bear',
    use_hour_blocks=True, block_big_bear=True)
run('HORA: v2 + big_bear + bearish_imb',
    use_hour_blocks=True, block_big_bear=True, block_bearish_imb=True)
run('HORA: v2 + big_bear + bid_wall',
    use_hour_blocks=True, block_big_bear=True, block_bid_wall=True)
run('HORA: v2 + big_bear + bid_wall + bearish_imb',
    use_hour_blocks=True, block_big_bear=True, block_bid_wall=True, block_bearish_imb=True)
run('HORA: v2 + big_bear + obi_min<-0.81',
    use_hour_blocks=True, block_big_bear=True, obi_min_thresh=-0.81)
run('HORA: v2 + big_bear + bid_wall + obi_min<-0.81',
    use_hour_blocks=True, block_big_bear=True, block_bid_wall=True, obi_min_thresh=-0.81)
run('HORA: v2 + todos los features',
    use_hour_blocks=True, block_big_bear=True, block_bid_wall=True,
    block_bearish_imb=True, obi_min_thresh=-0.81)
