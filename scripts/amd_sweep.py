#!/usr/bin/env python3
"""
AMD Parameter Sweep — encuentra la mejor combinacion de parametros.

Referencia: 8 trades del doc AMD_ORDERFLOW_ESTRATEGIA (Jun 5-6, 2026).
El sweep evalua ~200 combinaciones y rankea por WR, avgR y cuantos
de los 8 trades de referencia captura cada combinacion.

Uso:
    python scripts/amd_sweep.py
    python scripts/amd_sweep.py --days 14
"""

import json, time as time_mod, argparse, urllib.request
from collections import deque
from datetime import datetime, timezone
from itertools import product

# ── Fetch ──────────────────────────────────────────────────────────────────────

def fetch_klines(symbol, start_ms, end_ms):
    all_raw=[]; cur=start_ms; limit=1500
    while cur<end_ms:
        url=(f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}"
             f"&interval=1m&startTime={cur}&endTime={end_ms}&limit={limit}")
        req=urllib.request.Request(url,headers={"User-Agent":"sweep/1"})
        with urllib.request.urlopen(req,timeout=30) as r:
            page=json.loads(r.read())
        if not page: break
        all_raw.extend(page); cur=int(page[-1][0])+60_000
        if len(page)<limit: break
        time_mod.sleep(0.05)
    bars=[]
    for k in all_raw:
        v=float(k[5]); tb=float(k[9])
        bars.append({"ts":int(k[0])//1000,"ts_ms":int(k[0]),
                     "o":float(k[1]),"h":float(k[2]),"l":float(k[3]),"c":float(k[4]),
                     "vol":v,"delta":2.0*tb-v})
    return bars

def ms_to_str(ms,fmt="%Y-%m-%d %H:%M"):
    return datetime.fromtimestamp(ms/1000,tz=timezone.utc).strftime(fmt)

def session_of(ts_ms):
    h=(ts_ms//3_600_000)%24
    if   8<=h<13: return "London"
    elif 13<=h<17: return "Overlap"
    elif 17<=h<22: return "NewYork"
    else:          return "Asia"

# ── Trades de referencia (8 ejemplos del doc AMD_ORDERFLOW_ESTRATEGIA) ─────────
# Timestamps en segundos UTC de los entries de los 8 trades
# Jun 5 2026: 13:15, 18:31, 19:36, 20:21, 23:11
# Jun 6 2026: 04:22, 05:18, 10:01
REF_TIMES_UTC = [
    "2026-06-05 13:15",
    "2026-06-05 18:31",
    "2026-06-05 19:36",
    "2026-06-05 20:21",
    "2026-06-05 23:11",
    "2026-06-06 04:22",
    "2026-06-06 05:18",
    "2026-06-06 10:01",
]
REF_TS = [
    int(datetime.strptime(t, "%Y-%m-%d %H:%M")
        .replace(tzinfo=timezone.utc).timestamp())
    for t in REF_TIMES_UTC
]
REF_WINDOW = 30 * 60  # ±30 minutos — el entry puede ser unos bars despues del spike

# ── Detector (version minimal para sweep) ──────────────────────────────────────

def run_detector(bars, cfg):
    """Corre el detector AMD con los parametros dados. Retorna lista de senales."""
    vhist=deque(maxlen=cfg["vr_win"]+5)
    hist=deque(maxlen=cfg["acc_max"]+10)
    cvd=0.0; cacc=deque(maxlen=20+5)
    seen=0; last=0
    phase="IDLE"; rh=rl=None; csum=0.0; ab=0
    se=sd=None; vsp=dsp=None
    mrh=mrl=mab=mcvd=None; bss=0; ai=si=None
    signals=[]

    def _vr(v):
        if len(vhist)<5: return 1.0
        m=sum(vhist)/len(vhist); return v/m if m>0 else 1.0

    def _slope():
        n=len(cacc)
        if n<5: return None
        xs=list(range(n)); ys=list(cacc)
        mx=sum(xs)/n; my=sum(ys)/n
        num=sum((x-mx)*(y-my) for x,y in zip(xs,ys))
        den=sum((x-mx)**2 for x in xs)
        return num/den if abs(den)>1e-10 else None

    def rst():
        nonlocal phase,rh,rl,csum,ab,se,sd,vsp,dsp,mrh,mrl,mab,mcvd,bss,ai,si
        phase="IDLE"; rh=rl=None; csum=0.0; ab=0
        se=sd=None; vsp=dsp=None
        mrh=mrl=mab=mcvd=None; bss=0; ai=si=None

    for idx,b in enumerate(bars):
        h=b["h"]; l=b["l"]; c=b["c"]; v=b["vol"]; d=b["delta"]
        seen+=1; vhist.append(v)
        hist.append({"h":h,"l":l,"c":c,"d":d,"i":idx})
        cvd+=d; cacc.append(cvd)
        if seen<cfg["warmup"] or seen-last<cfg["cooldown"]: continue
        vr=_vr(v)

        if phase=="IDLE":
            if len(hist)>=cfg["acc_min"]:
                w=list(hist)[-cfg["acc_min"]:]
                _rh=max(x["h"] for x in w); _rl=min(x["l"] for x in w)
                pct=(_rh-_rl)/c*100
                if cfg["rng_min"]<=pct<=cfg["rng_max"]:
                    phase="ACC"; rh=_rh; rl=_rl; ab=cfg["acc_min"]
                    csum=sum(x["d"] for x in w); ai=w[0]["i"]

        elif phase=="ACC":
            if c>rl and c<rh:
                nr=max(rh,h); nl=min(rl,l)
                if (nr-nl)/c*100>cfg["rng_max"]: rst(); continue
                rh=nr; rl=nl; ab+=1; csum+=d
                if ab>cfg["acc_max"]: rst()
            else:
                pct=(rh-rl)/c*100
                if pct<cfg["rng_min"] or pct>cfg["rng_max"] or ab<cfg["acc_min"]:
                    rst(); continue
                if vr<cfg["vr_spike"]: rst(); continue
                sp_d="Up" if c>rh else "Down"
                sp_e=h if sp_d=="Up" else l
                div=(sp_d=="Up" and d<0) or (sp_d=="Down" and d>0)
                if cfg["require_div"] and not div: rst(); continue
                phase="MANIP"; se=sp_e; sd=sp_d; vsp=vr; dsp=d
                mrh=rh; mrl=rl; mab=ab; mcvd=csum; bss=0; si=idx

        elif phase=="MANIP":
            if bss>=cfg["max_wait"]: rst(); continue
            bss+=1
            dd="Short" if sd=="Up" else "Long"
            cr=(dd=="Short" and c<mrh) or (dd=="Long" and c>mrl)
            if not cr: continue
            if vr<cfg["vr_entry"]: continue
            entry=c; buf=cfg["stop_buf"]/100
            stop=se*(1+buf) if dd=="Short" else se*(1-buf)
            risk=abs(stop-entry)
            if risk<1: continue
            target=entry-risk*2 if dd=="Short" else entry+risk*2
            rr=abs(target-entry)/risk
            if rr<cfg["min_rr"]: continue
            # Filtro de sesion
            ses=session_of(b["ts_ms"])
            if cfg["sessions"] and ses not in cfg["sessions"]: rst(); continue
            signals.append({
                "direction":dd,"entry":entry,"stop":stop,"target":target,"rr":round(rr,3),
                "session":ses,"ts":b["ts"],"ts_ms":b["ts_ms"],
                "idx":idx,"ai":ai,"si":si,
                "vr_spike":round(vsp,3),"delta_spike":round(dsp,1),
                "vr_entry":round(vr,3),"cvd_div":div,
                "exit":None,"result_r":None,
            })
            last=seen; rst()

    return signals

def simulate(signals, bars, max_hold=120):
    for sig in signals:
        i0=sig["idx"]
        for k in range(1,max_hold+1):
            if i0+k>=len(bars): break
            b=bars[i0+k]; h=b["h"]; l=b["l"]
            if sig["direction"]=="Short":
                if l<=sig["target"]: sig["exit"]="TARGET"; sig["result_r"]=sig["rr"]; break
                if h>=sig["stop"]:   sig["exit"]="STOP";   sig["result_r"]=-1.0; break
            else:
                if h>=sig["target"]: sig["exit"]="TARGET"; sig["result_r"]=sig["rr"]; break
                if l<=sig["stop"]:   sig["exit"]="STOP";   sig["result_r"]=-1.0; break
        else:
            sig["exit"]="OPEN"
    return signals

def count_ref(signals):
    """Cuantos de los 8 trades de referencia fueron capturados."""
    captured=set()
    for sig in signals:
        for i,rt in enumerate(REF_TS):
            if abs(sig["ts"]-rt)<=REF_WINDOW:
                captured.add(i)
    return len(captured), captured

def eval_combo(bars, cfg, max_hold=120):
    sigs=run_detector(bars,cfg)
    simulate(sigs,bars,max_hold)
    closed=[s for s in sigs if s["exit"] in ("TARGET","STOP")]
    n=len(closed)
    if n==0:
        return dict(n_sigs=len(sigs),n_closed=0,wr=0,avgr=0,
                    expectancy=0,n_ref=0,n_target=0,n_stop=0,n_open=0)
    wins=[s for s in closed if s["result_r"]>0]
    wr=len(wins)/n
    avgr=sum(s["result_r"] for s in closed)/n
    n_ref,_=count_ref(sigs)
    n_target=sum(1 for s in sigs if s["exit"]=="TARGET")
    n_stop=sum(1 for s in sigs if s["exit"]=="STOP")
    n_open=sum(1 for s in sigs if s["exit"]=="OPEN")
    # Expectancy: considera el RR medio
    exp=sum(s["result_r"] for s in closed)/len(closed) if closed else 0
    return dict(n_sigs=len(sigs),n_closed=n,wr=round(wr*100,1),avgr=round(avgr,3),
                expectancy=round(exp,3),n_ref=n_ref,n_target=n_target,
                n_stop=n_stop,n_open=n_open)

# ── Sweep ──────────────────────────────────────────────────────────────────────

SESSION_PRESETS = {
    "ALL":          None,
    "London":       {"London"},
    "Lon+Ovlp":     {"London","Overlap"},
    "Asia+Lon":     {"Asia","London"},
    "No-NY":        {"Asia","London","Overlap"},
}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("symbol",nargs="?",default="BTCUSDT")
    ap.add_argument("--days",type=int,default=14)
    args=ap.parse_args()

    now_ms=int(time_mod.time()*1000)
    start_ms=now_ms-args.days*86400*1000
    print(f"[fetch] {args.symbol} {args.days} dias...")
    bars=fetch_klines(args.symbol,start_ms,now_ms)
    print(f"[fetch] {len(bars)} barras ok")

    # Grilla de parametros
    grid={
        "vr_spike":       [1.5, 2.0, 2.5, 3.0],
        "vr_entry":       [1.0, 1.5],
        "require_div":    [True, False],
        "acc_min":        [10, 15, 20],
        "acc_max":        [60, 120],
        "rng_max":        [0.30, 0.45, 0.60],
        "max_wait":       [5, 10, 15],
        "sessions_key":   list(SESSION_PRESETS.keys()),
    }

    # Base cfg constante
    base=dict(
        rng_min=0.04,
        stop_buf=0.08, min_rr=2.0,
        cooldown=30, warmup=60, vr_win=50,
    )

    keys=list(grid.keys())
    combos=list(product(*[grid[k] for k in keys]))
    total=len(combos)
    print(f"[sweep] {total} combinaciones...")

    results=[]
    for i,vals in enumerate(combos):
        if i%100==0: print(f"  {i}/{total}...",end="\r")
        cfg={**base}
        for k,v in zip(keys,vals):
            if k=="sessions_key":
                cfg["sessions"]=SESSION_PRESETS[v]
                cfg["_ses_key"]=v
            elif k=="acc_max":
                cfg["acc_max"]=v
            else:
                cfg[k]=v
        r=eval_combo(bars,cfg)
        r["params"]={k:v for k,v in zip(keys,vals)}
        results.append(r)

    print(f"\n[sweep] done — {total} combinaciones evaluadas")

    # Filtrar: al menos 5 senales cerradas y WR > 0
    valid=[r for r in results if r["n_closed"]>=5]
    # Rankear: score = WR * avgR * (n_ref/8 + 0.1)
    for r in valid:
        r["score"]=r["wr"]/100 * max(r["avgr"],0) * (r["n_ref"]/8 + 0.2)

    valid.sort(key=lambda r: (-r["score"], -r["n_ref"], -r["wr"]))

    # Top 25
    print(f"\n{'='*110}")
    print(f"TOP 25 — score = WR * avgR * (n_ref/8 + 0.2)")
    print(f"{'='*110}")
    hdr=f"{'#':>3} {'Sesion':12} {'VRspike':8} {'VRentry':8} {'CVDiv':6} {'acc':4} {'rngMax':7} {'wait':5} | {'n':>4} {'WR%':>6} {'avgR':>7} {'ref/8':>6} {'score':>7}"
    print(hdr)
    print("-"*110)

    for rank,r in enumerate(valid[:25],1):
        p=r["params"]
        sk=p["sessions_key"]
        div="YES" if p["require_div"] else "no"
        ref_star="★"*r["n_ref"] + "·"*(8-r["n_ref"])
        print(
            f"{rank:>3} {sk:12} {p['vr_spike']:>8.1f} {p['vr_entry']:>8.1f} "
            f"{div:>6} {p['acc_min']:>4} {p['rng_max']:>7.2f} {p['max_wait']:>5} | "
            f"{r['n_closed']:>4} {r['wr']:>6.1f}% {r['avgr']:>+7.3f} "
            f"  {ref_star} ({r['n_ref']}/8) {r['score']:>7.4f}"
        )

    # Tabla detalle: top 5 con todas las metricas
    print(f"\n{'='*90}")
    print("DETALLE TOP 5")
    print(f"{'='*90}")
    for rank,r in enumerate(valid[:5],1):
        p=r["params"]
        print(f"\n[#{rank}] Score={r['score']:.4f}")
        print(f"  Sesiones     : {p['sessions_key']}")
        print(f"  VR spike     : {p['vr_spike']:.1f}x")
        print(f"  VR entry     : {p['vr_entry']:.1f}x")
        print(f"  CVD diverge  : {'Si' if p['require_div'] else 'No'}")
        print(f"  Acc min bars : {p['acc_min']}")
        print(f"  Rango max    : {p['rng_max']:.2f}%")
        print(f"  Max wait     : {p['max_wait']} barras")
        print(f"  Senales      : {r['n_sigs']} total | {r['n_closed']} cerradas | "
              f"TARGET={r['n_target']} STOP={r['n_stop']} OPEN={r['n_open']}")
        print(f"  WR           : {r['wr']:.1f}%")
        print(f"  avg R        : {r['avgr']:+.3f}")
        print(f"  Ref trades   : {r['n_ref']}/8  {'★'*r['n_ref']+'·'*(8-r['n_ref'])}")

    # Analisis por sesion del mejor
    best=valid[0]
    bp=best["params"]
    best_cfg={**base,
              "vr_spike":bp["vr_spike"],"vr_entry":bp["vr_entry"],
              "require_div":bp["require_div"],"acc_min":bp["acc_min"],
              "acc_max":bp.get("acc_max",60),
              "rng_max":bp["rng_max"],"max_wait":bp["max_wait"],
              "sessions":SESSION_PRESETS[bp["sessions_key"]]}
    sigs=run_detector(bars,best_cfg)
    simulate(sigs,bars)
    closed=[s for s in sigs if s["exit"] in ("TARGET","STOP")]

    print(f"\n{'='*70}")
    print(f"MEJOR COMBO — breakdown por sesion y direccion")
    print(f"{'='*70}")
    from collections import defaultdict
    by_ses=defaultdict(list)
    for s in closed: by_ses[s["session"]].append(s)
    for ses,sl in sorted(by_ses.items()):
        n=len(sl); wins=sum(1 for s in sl if s["result_r"]>0)
        avg=sum(s["result_r"] for s in sl)/n
        print(f"  {ses:12} n={n:>3}  WR={wins/n*100:>5.1f}%  avgR={avg:>+.3f}")

    by_dir=defaultdict(list)
    for s in closed: by_dir[s["direction"]].append(s)
    print()
    for dd,sl in sorted(by_dir.items()):
        n=len(sl); wins=sum(1 for s in sl if s["result_r"]>0)
        avg=sum(s["result_r"] for s in sl)/n
        print(f"  {dd:8} n={n:>3}  WR={wins/n*100:>5.1f}%  avgR={avg:>+.3f}")

    # Ref trades capturados
    _,cap=count_ref(sigs)
    print(f"\n  Trades de referencia capturados: {len(cap)}/8")
    for i,rt in enumerate(REF_TS):
        ts_str=datetime.fromtimestamp(rt,tz=timezone.utc).strftime("%m-%d %H:%M")
        mark="OK" if i in cap else "--"
        print(f"    [{mark}] {ts_str}  {REF_TIMES_UTC[i]}")

    # Sugerir config para strategy.toml
    print(f"\n{'='*70}")
    print("SUGERENCIA strategy.toml [amd_detector]")
    print(f"{'='*70}")
    ses_list=sorted(SESSION_PRESETS[bp["sessions_key"]]) if SESSION_PRESETS[bp["sessions_key"]] else ["ALL"]
    print(f"""
[amd_detector]
enabled                   = true
accum_range_min_pct       = 0.04
accum_range_max_pct       = {bp['rng_max']:.2f}
accum_min_bars            = {bp['acc_min']}
accum_max_bars            = 60
manip_min_vr              = {bp['vr_spike']:.1f}
manip_vpin_threshold      = 0.55    # ajustar con datos live
dist_min_vr               = {bp['vr_entry']:.1f}
dist_cvd_slope            = 5.0     # relajado — slope interno no discrimina bien
dist_obi_confirm          = 0.08
stop_buffer_pct           = 0.08
min_rr                    = 2.0
cooldown_bars             = 30
max_wait_bars_after_spike = {bp['max_wait']}
# Sesiones: {', '.join(ses_list)}
""")

if __name__=="__main__":
    main()
