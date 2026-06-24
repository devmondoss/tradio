"""
_reconstruct_orphans.py — Recupera el resultado real de los fills huérfanos
(posiciones que el bug de persistencia perdió antes de cerrar).

Método:
  1. entry = precio exacto del fill (de events).
  2. stop/target = reconstruidos con la lógica portada de levels.rs, sobre las
     barras M15 de la barra del PLACE correspondiente (donde se fijó el nivel).
  3. gestión = simulada forward en M1 con el mismo algoritmo de book.rs::on_trade
     (scale2, fade/trail, timeout 24h, mismos fees).

Valida contra el SOL 18:47 que sí cerró (-1.15R registrado).
"""
import json, urllib.request, datetime as dt, time, math

URL="https://jubpovmsfvaqfnidozfh.supabase.co"
KEY="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imp1YnBvdm1zZnZhcWZuaWRvemZoIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MTk4OTY1NywiZXhwIjoyMDk3NTY1NjU3fQ.X_UMT7FypCvmPX1WxmI_Sg29FVRG2IliVBOwkWMh3ZI"

# ── constantes (idénticas a levels.rs / book.rs) ──
VA_BARS=96; SWING=50; OB_WIN=15; MIN_RR=1.2; MIN_TP1_RR=2.3
TRAIL_ATR=4.0; ATR_N=14; TOUCH_TOL=0.002; BIN=5.0
FEE_MAKER=0.0002; FEE_TAKER=0.00055; SCALE2=0.3
TP2_CAP={"SOLUSDT":2.25}   # del env railway.liquidity-sol

def sb(t,p):
    req=urllib.request.Request(f"{URL}/rest/v1/{t}?{p}",headers={"apikey":KEY,"Authorization":f"Bearer {KEY}"})
    return json.loads(urllib.request.urlopen(req).read().decode())
def sb_insert(t,rows):
    req=urllib.request.Request(f"{URL}/rest/v1/{t}",data=json.dumps(rows).encode(),method="POST",
        headers={"apikey":KEY,"Authorization":f"Bearer {KEY}","Content-Type":"application/json","Prefer":"return=minimal"})
    try:
        urllib.request.urlopen(req); return None
    except urllib.error.HTTPError as e:
        return f"{e.code}: {e.read().decode()[:200]}"
def sb_delete(t,q):
    req=urllib.request.Request(f"{URL}/rest/v1/{t}?{q}",method="DELETE",
        headers={"apikey":KEY,"Authorization":f"Bearer {KEY}"})
    try: urllib.request.urlopen(req)
    except urllib.error.HTTPError: pass
def iso(ms): return dt.datetime.fromtimestamp(ms/1000,dt.timezone.utc).isoformat()
def iso2ms(s): return int(dt.datetime.fromisoformat(s.replace("Z","+00:00")).timestamp()*1000)

def bybit(sym, interval, start, end):
    out=[]; cur=end
    while cur>start:
        url=(f"https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}"
             f"&interval={interval}&end={cur}&limit=1000")
        d=json.loads(urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"x"})).read())
        lst=d.get("result",{}).get("list",[])
        if not lst: break
        for k in lst: out.append([int(k[0]),float(k[1]),float(k[2]),float(k[3]),float(k[4]),float(k[5])])
        oldest=min(int(k[0]) for k in lst)
        if oldest<=start or len(lst)<1000: break
        cur=oldest-1; time.sleep(0.15)
    out.sort(); return out  # [ts,o,h,l,c,v]

# ── lógica portada de levels.rs ──
def atr_series(bars):
    a=[0.0]*len(bars); cur=0.0
    for i in range(1,len(bars)):
        h,l=bars[i][2],bars[i][3]; pc=bars[i-1][4]
        tr=max(h-l,abs(h-pc),abs(l-pc))
        cur=tr if cur==0.0 else cur*(1-1/ATR_N)+tr*(1/ATR_N)
        a[i]=cur
    return a

def value_area(bars):  # sin footprint: close×volume
    n=min(len(bars),VA_BARS)
    if n<4: return None
    recent=bars[len(bars)-n:]
    vol={}
    for b in recent:
        binp=round(b[4]/BIN); vol[binp]=vol.get(binp,0.0)+b[5]
    if not vol: return None
    poc_bin=max(vol,key=vol.get); poc=poc_bin*BIN
    total=sum(vol.values()); sel=[]; cum=0.0
    for binp,v in sorted(vol.items(),key=lambda x:-x[1]):
        sel.append(binp); cum+=v
        if cum>=0.70*total: break
    return {"poc":poc,"vah":max(sel)*BIN,"val":min(sel)*BIN}

def swing_hl(bars):
    n=min(len(bars)-1,SWING)
    w=bars[len(bars)-(n+1):len(bars)-1]
    return (max(b[2] for b in w), min(b[3] for b in w)) if w else (math.nan,math.nan)

def day_hl(bars,offset_lo,offset_hi):  # día(s) relativo
    today=bars[-1][0]//86_400_000
    sel=[b for b in bars if offset_lo<=(today-b[0]//86_400_000)<=offset_hi]
    return (max(b[2] for b in sel),min(b[3] for b in sel)) if sel else (math.nan,math.nan)

def struct_target(side,entry,va,sh,sl,pdh,pdl,wh,wl):
    fin=lambda v: v==v and abs(v)!=math.inf
    if side=="long":
        c=[v for v in (va["vah"],sh,pdh,wh) if fin(v) and v>entry*1.001]
        if not c: return (None,None)
        tp2=max(c); tp1=min(c)
    else:
        c=[v for v in (va["val"],sl,pdl,wl) if fin(v) and v<entry*0.999]
        if not c: return (None,None)
        tp2=min(c); tp1=max(c)
    tp1_out=None if abs(tp1-tp2)<1.0 else tp1
    return (tp1_out,tp2)

def reconstruct_level(bars, atrs, idx, side, kind, entry, sym):
    """Devuelve (stop, tp1, tp) del nivel en la barra idx (la del place)."""
    sub=bars[:idx+1]
    if len(sub)<300: return None
    atr=atrs[idx]
    if atr<=0: return None
    va=value_area(sub)
    if not va: return None
    sh,sl=swing_hl(sub); pdh,pdl=day_hl(sub,1,1); wh,wl=day_hl(sub,1,8)
    if kind=="poc_ob":
        # el entry = mid/POC de la vela OB que usó el sistema. La identifico
        # buscando, en una ventana amplia, la vela cuyo punto medio ≈ entry
        # (robusto: no depende de replicar la ventana exacta del sistema).
        win=sub[max(0,len(sub)-OB_WIN-5):]
        ob=min(win, key=lambda b: abs((b[2]+b[3])/2.0 - entry))
        stop=ob[3]-0.25*atr if side=="long" else ob[2]+0.25*atr
    elif kind=="poc_def":
        stop=entry-0.6*atr
    elif kind=="poc_def_short":
        stop=entry+0.6*atr
    else: return None
    tp1,tp=struct_target(side,entry,va,sh,sl,pdh,pdl,wh,wl)
    if tp is None: return None
    risk=abs(entry-stop)
    if risk<=0: return None
    cap=TP2_CAP.get(sym,0.0)
    if cap>0:
        capx=entry+cap*risk if side=="long" else entry-cap*risk
        beyond=tp>capx if side=="long" else tp<capx
        if beyond:
            tp=capx
            if tp1 is not None:
                inr=(tp1>entry and tp1<tp) if side=="long" else (tp1<entry and tp1>tp)
                if not inr: tp1=None
    if abs(tp-entry)/risk < MIN_RR: return None
    take_partial = (tp1 is not None) and (abs(tp1-entry)/risk >= MIN_TP1_RR)
    return dict(stop=stop,tp1=tp1 if take_partial else None,tp=tp,atr=atr,risk=risk)

def simulate(m1, fill_ts, side, entry, lv, gestion):
    """Port de book.rs::on_trade. Devuelve (result_r, exit_px, reason, closed_ts)."""
    stop=lv["stop"]; tp=lv["tp"]; tp1=lv["tp1"]; atr=lv["atr"]
    scale2_price = entry-SCALE2*atr if side=="long" else entry+SCALE2*atr
    scale2_filled=False; eff=entry
    cur_stop=stop; realized=0.0; rem=1.0; filled1=False
    best=entry; trail=stop
    bars=[b for b in m1 if b[0]>=fill_ts]
    timeout=24*3600*1000
    for ts,o,hi,lo,cl,v in bars:
        # scale2
        if not scale2_filled:
            hit2 = lo<=scale2_price if side=="long" else hi>=scale2_price
            if hit2:
                scale2_filled=True; eff=(entry+scale2_price)/2.0
        risk=abs(eff-stop)
        if risk<=0: continue
        if ts-fill_ts>timeout:
            g=(cl-eff)/risk if side=="long" else (eff-cl)/risk
            fee=(FEE_MAKER+FEE_TAKER)*eff/risk
            return (g-fee, cl, "timeout", ts)
        if gestion=="trail":
            if side=="long":
                best=max(best,hi); trail=max(trail,best-TRAIL_ATR*atr)
                if lo<=trail:
                    g=(trail-eff)/risk; fee=(FEE_MAKER+FEE_TAKER)*eff/risk
                    return (g-fee,trail,"trail",ts)
            else:
                best=min(best,lo); trail=min(trail,best+TRAIL_ATR*atr)
                if hi>=trail:
                    g=(eff-trail)/risk; fee=(FEE_MAKER+FEE_TAKER)*eff/risk
                    return (g-fee,trail,"trail",ts)
        else:  # fade
            if side=="long":
                if lo<=cur_stop:
                    realized+=rem*((cur_stop-eff)/risk); reason="breakeven" if filled1 else "stop"
                    fee=(FEE_MAKER+(FEE_MAKER*0.5 if filled1 else 0)+ (FEE_MAKER if reason=='target' else FEE_TAKER)*rem)*eff/risk
                    return (realized-fee,cur_stop,reason,ts)
                if (not filled1) and tp1 and hi>=tp1:
                    realized+=0.5*((tp1-eff)/risk); rem-=0.5; filled1=True; cur_stop=eff
                if hi>=tp:
                    realized+=rem*((tp-eff)/risk)
                    fee=(FEE_MAKER+(FEE_MAKER*0.5 if filled1 else 0)+FEE_MAKER*rem)*eff/risk
                    return (realized-fee,tp,"target",ts)
            else:
                if hi>=cur_stop:
                    realized+=rem*((eff-cur_stop)/risk); reason="breakeven" if filled1 else "stop"
                    fee=(FEE_MAKER+(FEE_MAKER*0.5 if filled1 else 0)+ (FEE_MAKER if reason=='target' else FEE_TAKER)*rem)*eff/risk
                    return (realized-fee,cur_stop,reason,ts)
                if (not filled1) and tp1 and lo<=tp1:
                    realized+=0.5*((eff-tp1)/risk); rem-=0.5; filled1=True; cur_stop=eff
                if lo<=tp:
                    realized+=rem*((eff-tp)/risk)
                    fee=(FEE_MAKER+(FEE_MAKER*0.5 if filled1 else 0)+FEE_MAKER*rem)*eff/risk
                    return (realized-fee,tp,"target",ts)
    # sigue abierta (no cerró en la ventana de datos)
    last=bars[-1] if bars else None
    if last:
        risk=abs(eff-stop); g=((last[4]-eff) if side=="long" else (eff-last[4]))/risk
        return (g-(FEE_MAKER+FEE_TAKER)*eff/risk, last[4], "OPEN", last[0])
    return None

# ── main ──
def main():
    fills=sb("liquidity_paper_events","select=symbol,side,kind,price,at,gestion,regime,vol_regime,system&event_type=eq.fill&order=at.asc")
    places=sb("liquidity_paper_events","select=symbol,side,kind,price,at&event_type=eq.place&order=at.asc")
    for x in fills+places: x['ts']=iso2ms(x['at'])
    # dedup por (symbol,at,side,price,system,gestion): cada combo = un trade real de un book
    byfill={}
    for f in fills:
        k=(f['symbol'],f['at'][:19],f['side'],round(f['price'],4),f.get('system','?'),f['gestion'])
        byfill[k]=f
    real=[{'f':f,'gestiones':{f['gestion']}} for f in byfill.values()]

    syms=set(f['f']['symbol'] for f in real)
    start=min(f['f']['ts'] for f in real)-12*86_400_000
    end=max(f['f']['ts'] for f in real)+ 30*3600*1000
    m15={s:bybit(s,"15",start,end) for s in syms}
    m1 ={s:bybit(s,"1", min(f['f']['ts'] for f in real)-3600000, end) for s in syms}
    atr15={s:atr_series(m15[s]) for s in syms}
    for s in syms: print(f"{s}: M15={len(m15[s])} M1={len(m1[s])}")

    def find_place(f):
        # primer place de la racha contigua que llevó al fill (el nivel se fija al aparecer)
        cands=sorted([p for p in places if p['symbol']==f['symbol'] and p['side']==f['side']
               and p['kind']==f['kind'] and abs(p['price']-f['price'])<2.0 and p['ts']<=f['ts']],
               key=lambda p:p['ts'])
        if not cands: return None
        streak_start=cands[-1]
        for p in reversed(cands[:-1]):
            if streak_start['ts']-p['ts'] <= 20*60_000: streak_start=p
            else: break
        return streak_start
    def bar_idx(bars,ts):
        lo,hi=0,len(bars)-1; idx=0
        for i,b in enumerate(bars):
            if b[0]<=ts: idx=i
            else: break
        return idx

    write = "--write" in __import__("sys").argv
    print(f"\n{'fill':>11} {'sym':7} {'side':5} {'kind':13} {'sys':5} {'gest':5} {'entry':>9} {'stop':>9} {'target':>9} {'→R':>7} {'reason':>9}")
    print("-"*112)
    results=[]; rows=[]
    for item in sorted(real,key=lambda x:x['f']['ts']):
        f=item['f']; sym=f['symbol']; g=f['gestion']; system=f.get('system','?')
        pl=find_place(f)
        idx=bar_idx(m15[sym], pl['ts'] if pl else f['ts'])
        lv=reconstruct_level(m15[sym],atr15[sym],idx,f['side'],f['kind'],f['price'],sym)
        if not lv:
            print(f"{f['at'][5:16]:>11} {sym:7} {f['side']:5} {f['kind']:13} {system:5} {g:5} {f['price']:>9.2f}  (no reconstruido)")
            continue
        sim=simulate(m1[sym],f['ts'],f['side'],f['price'],lv,g)
        if not sim: continue
        r,expx,reason,cts=sim
        results.append(dict(sym=sym,gestion=g,r=r))
        print(f"{f['at'][5:16]:>11} {sym:7} {f['side']:5} {f['kind']:13} {system:5} {g:5} {f['price']:>9.2f} {lv['stop']:>9.2f} {lv['tp']:>9.2f} {r:>+7.2f} {reason:>9}")
        rows.append({"symbol":sym,"tf":"15","kind":f['kind'],"side":f['side'],
            "vol_regime":f.get('vol_regime','?'),"regime":f.get('regime','?'),"gestion":g,
            "entry":f['price'],"stop":round(lv['stop'],4),"target":round(lv['tp'],4),
            "exit_price":round(expx,4),"result_r":round(r,4),"win":r>0,"reason":reason,
            "system":system,"opened_at":iso(f['ts']),"closed_at":iso(cts),
            "bar_delta_at_fill":0.0,"scale2_filled":False,"effective_entry":f['price'],
            "reconstructed":True})

    print("\n── Resumen reconstruido ──")
    if results:
        rs=[x['r'] for x in results]; wins=sum(1 for r in rs if r>0)
        print(f"  trades: {len(rs)}  avgR {sum(rs)/len(rs):+.3f}  netR {sum(rs):+.1f}  WR {wins/len(rs)*100:.0f}%")
        for g in ("fade","trail"):
            sub=[x['r'] for x in results if x['gestion']==g]
            if sub: print(f"    {g:5}: avgR {sum(sub)/len(sub):+.3f} netR {sum(sub):+.1f} n {len(sub)}")
        for s in sorted(syms):
            sub=[x['r'] for x in results if x['sym']==s]
            if sub: print(f"    {s:8}: avgR {sum(sub)/len(sub):+.3f} netR {sum(sub):+.1f} n {len(sub)}")

    closed=[r for r in rows if r['reason']!="OPEN"]
    nopen=len(rows)-len(closed)
    if write and closed:
        print(f"\n[write] {len(closed)} cerrados a escribir ({nopen} OPEN omitidos — los gestiona el sistema).")
        sb_delete("liquidity_paper_trades","reconstructed=eq.true")
        err=sb_insert("liquidity_paper_trades",closed)
        print("[write] ERROR:",err if err else "OK — insertados con reconstructed=true")
    elif closed:
        print(f"\n  {len(closed)} cerrados listos para escribir ({nopen} OPEN omitidos). Corré con --write tras el ALTER.")

if __name__=="__main__":
    main()
