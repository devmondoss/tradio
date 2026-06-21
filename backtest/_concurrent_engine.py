"""
_concurrent_engine.py — Motor multi-posición para medir el techo de tpd.
=========================================================================
El lock de 1 posición tope el sistema a ~1 tpd. Aquí permitimos N posiciones
abiertas y comparamos 3 modelos de riesgo:
  'directions' : máx 1 short + 1 long concurrentes (2 slots, riesgo por-trade igual)
  'cap'        : N posiciones pero riesgo abierto total <= MAX_OPEN_RISK (6%)
  'nocap'      : toda señal confirmada se toma (máx volumen/compounding, riesgo alto)

Disparador shorts = unión ICT validada (rejection_VAH + FVG + OB + displacement + sweep;
  OTE/EqualHigh excluidos por flip OOS). Longs = espejo simulate_long.
Confirmación orderflow constante. Target 2.8R, timeout 240m (4h). Cooldown 15m/dir
  para no apilar la misma vela repetida en un cluster.

Uso: python backtest/_concurrent_engine.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'backtest'))
import mtf_v2 as m
import _ict_triggers as it

TARGET_R = 2.8
FWD      = 240          # timeout 4h
COOLDOWN = 15           # barras (min) entre entradas de la misma dirección

SHORT_TRIGS = [it.trig_rejection_vah, it.trig_fvg, it.trig_ob, it.trig_displacement, it.trig_sweep]
def short_trigger(row): return any(t(row) for t in SHORT_TRIGS)


def load():
    df = pd.read_parquet(m.DATA_M1).sort_values('ts_ms').reset_index(drop=True)
    for c in df.select_dtypes('object').columns:  df[c] = df[c].fillna('')
    for c in df.select_dtypes('float').columns:   df[c] = df[c].fillna(0.0)
    for c in df.select_dtypes('bool').columns:     df[c] = df[c].fillna(False)
    return df


def sizing_short(row, nt_q50, vpin_q50):
    nt=float(row.get('n_trades') or 0); cvd=float(row.get('cvd_slope') or 0)
    vpin=float(row.get('vpin') or 0); obi=float(row.get('obi10_mean') or 0)
    tape=nt>=nt_q50; vhi=vpin>=vpin_q50
    if tape and vhi and cvd>0: return 2.5
    if tape and vhi:           return 2.0
    if cvd>0 or tape or obi>=0: return 1.5
    return 1.0

def sizing_long(row, nt_q50, vpin_q50):
    nt=float(row.get('n_trades') or 0); cvd=float(row.get('cvd_slope') or 0)
    vpin=float(row.get('vpin') or 0); obi=float(row.get('obi10_mean') or 0)
    tape=nt>=nt_q50; vhi=vpin>=vpin_q50
    if tape and vhi and cvd>0: return 2.5
    if tape and vhi:           return 2.0
    if cvd>0 or obi>0:         return 1.5
    return 1.0


def elig_short(row, d1_ema):
    if d1_ema is None or row['close'] > d1_ema*0.980: return False
    if not (row.get('h1_bos_bear', False) or row.get('h1_choch_bear', False)): return False
    if not short_trigger(row): return False
    if not row.get('body_below_poc', False): return False
    if float(row.get('minus_ticks') or 0) <= float(row.get('plus_ticks') or 0): return False
    if row.get('fp_absorb_buy', False): return False
    if not row.get('vp_lvn_below', False): return False
    return True

def elig_long(row, d1_ema):
    if d1_ema is None: return False
    ratio = row['close']/d1_ema if d1_ema else 0
    if not (1.000 <= ratio <= 1.030): return False
    if row.get('h4_bos_bear', False): return False
    if not row.get('h1_bos_bull', False): return False
    ok,lbl = m.active_level_long(row)
    if not ok or not any(p in lbl.split('+') for p in ('VAL','AL','PDL','WL')): return False
    parts=lbl.split('+')
    if len(parts)>=3 and 'PDL' in parts and 'AL' in parts: return False
    if not m.rejection_long(row): return False
    poc=float(row.get('vp_poc') or 0)
    if poc>0 and min(float(row['open']),float(row['close']))>poc: return False
    if float(row.get('plus_ticks') or 0) <= float(row.get('minus_ticks') or 0): return False
    if row.get('fp_absorb_sell', False): return False
    return True


def run(df, model='cap', max_open_risk=0.06, sessions_long=('overlap','ny')):
    is_df = df[df['ts_ms'] < m.OOS_MS]
    nt_q50 = float(is_df['n_trades'].quantile(0.50)); vpin_q50 = float(is_df['vpin'].quantile(0.50))

    h1 = m.resamp(df, m.H1_MS); h1['atr']=m.atr14(h1['high'].values,h1['low'].values,h1['close'].values)
    h1hi = {int(r.ts_ms):(float(r.high),float(r.atr)) for r in h1.itertuples()}
    h1lo = {int(r.ts_ms):(float(r.low), float(r.atr)) for r in h1.itertuples()}
    d1 = m.resamp(df, m.D1_MS); d1['ema20']=m.ema(d1['close'].values,20)
    d1c = {int(r.ts_ms):float(r.ema20) for r in d1.itertuples()}

    rows = df.to_dict('records')
    openp=[]; closed=[]
    cap=m.CAPITAL; monthly_risk=cap*m.RISK_PCT; cur_month=-1
    last_entry={'short':-10**9,'long':-10**9}
    eq_curve=[]; peak=cap; maxdd=0.0; concur_samples=[]

    for i,row in enumerate(rows):
        ts=int(row['ts_ms'])
        # exits
        keep=[]
        for p in openp:
            bars_el=i-p['t_start']; reason=ex=None
            if p['side']=='short':
                if row['high']>=p['sl']: reason,ex='stop',p['sl']
                elif row['low']<=p['tp']: reason,ex='target',p['tp']
                elif bars_el>=FWD: reason,ex='timeout',row['close']
                if reason: r=(p['ep']-ex)/p['dist']-m.FEE_RT*p['ep']/p['dist']
            else:
                if row['low']<=p['sl']: reason,ex='stop',p['sl']
                elif row['high']>=p['tp']: reason,ex='target',p['tp']
                elif bars_el>=FWD: reason,ex='timeout',row['close']
                if reason: r=(ex-p['ep'])/p['dist']-m.FEE_RT*p['ep']/p['dist']
            if reason:
                pnl=p['risk']*r; cap+=pnl
                closed.append({'ts_ms':p['t_entry'],'side':p['side'],'result_r':r,
                               'pnl_usd':pnl,'oos':p['t_entry']>=m.OOS_MS,'exit_ts':ts})
                peak=max(peak,cap); maxdd=max(maxdd,(peak-cap)/peak)
            else: keep.append(p)
        openp=keep
        concur_samples.append(len(openp))

        month=pd.Timestamp(ts,unit='ms',tz='UTC').month+pd.Timestamp(ts,unit='ms',tz='UTC').year*12
        if month!=cur_month: monthly_risk=cap*m.RISK_PCT; cur_month=month

        sess=m.session(ts)
        if sess=='' : continue
        d1_ema=d1c.get((ts//m.D1_MS)*m.D1_MS - m.D1_MS)

        for side in ('short','long'):
            if side=='long' and sess not in sessions_long: continue
            if i-last_entry[side] < COOLDOWN: continue
            if side=='short':
                if not elig_short(row,d1_ema): continue
                h1d=h1hi.get((ts//m.H1_MS)*m.H1_MS)
                if h1d is None: continue
                h1h,h1a=h1d; sl=h1h+0.40*h1a; dist=sl-row['close']
                if dist<=0: continue
                sp=dist/row['close']
                if not (m.MIN_STOP<=sp<=m.MAX_STOP): continue
                mult=sizing_short(row,nt_q50,vpin_q50)
            else:
                if not elig_long(row,d1_ema): continue
                h1d=h1lo.get((ts//m.H1_MS)*m.H1_MS)
                if h1d is None: continue
                h1l,h1a=h1d; sl=h1l-0.40*h1a; dist=row['close']-sl
                if dist<=0: continue
                sp=dist/row['close']
                if not (m.MIN_STOP<=sp<=m.MAX_STOP): continue
                mult=sizing_long(row,nt_q50,vpin_q50)

            risk=monthly_risk*mult
            open_risk=sum(p['risk'] for p in openp)
            if model=='directions' and any(p['side']==side for p in openp): continue
            if model=='cap' and open_risk+risk > cap*max_open_risk: continue
            # nocap: sin límite
            ep=row['close']
            tp = ep-TARGET_R*dist if side=='short' else ep+TARGET_R*dist
            openp.append({'side':side,'ep':ep,'sl':sl,'tp':tp,'dist':dist,
                          'risk':risk,'t_start':i,'t_entry':ts})
            last_entry[side]=i

    return closed, cap, maxdd, float(np.mean(concur_samples)), max(concur_samples)


def report(closed, cap, maxdd, avg_concur, max_concur, label, days):
    def s(ts):
        if not ts: return (0,0,0,0)
        n=len(ts); wr=sum(1 for t in ts if t['result_r']>0)/n*100
        return (n,wr,sum(t['result_r'] for t in ts)/n,sum(t['result_r'] for t in ts))
    is_t=[t for t in closed if not t['oos']]; oos_t=[t for t in closed if t['oos']]
    ni,wi,ai,ti=s(is_t); no,wo,ao,to=s(oos_t)
    tpd=len(closed)/days
    sh=sum(1 for t in closed if t['side']=='short'); lo=len(closed)-sh
    print(f'{label:<20}| IS n={ni:>4} WR={wi:>4.1f}% AvgR={ai:>+.3f} TotR={ti:>+6.0f} '
          f'| OOS n={no:>3} WR={wo:>4.1f}% AvgR={ao:>+.3f} TotR={to:>+5.0f} '
          f'| {tpd:>4.2f}tpd | Eq=${cap:>10,.0f} | DD={maxdd*100:>4.1f}% | concur~{avg_concur:.2f}/max{max_concur} | S{sh}/L{lo}')


def main():
    df=load(); days=(df.ts_ms.max()-df.ts_ms.min())/86_400_000
    print(f'Días: {days:.0f}  | Target {TARGET_R}R  timeout {FWD}m  cooldown {COOLDOWN}m\n')
    print('='*180)
    for model,kw in [('directions',{}),('cap',{'max_open_risk':0.06}),
                     ('cap',{'max_open_risk':0.10}),('nocap',{})]:
        closed,cap,dd,ac,mc = run(df, model=model, **kw)
        tag = model + (f" {int(kw['max_open_risk']*100)}%" if 'max_open_risk' in kw else '')
        report(closed,cap,dd,ac,mc,tag,days)
    print('='*180)
    print('Referencia: v2 actual (1 posición, target 2.8R, timeout 20h) = 0.31 tpd, Eq ~$15.8k')


if __name__ == '__main__':
    main()
