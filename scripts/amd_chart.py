#!/usr/bin/env python3
"""
Liquidity Sweep Chart — detecta barras que barren el swing high/low previo
y cierran de vuelta (fake breakout / stop hunt).

Logica: una sola barra con VR >= min_vr que:
  SHORT: high > swing_hi(LB) AND close < swing_hi  → entrada en close
  LONG:  low  < swing_lo(LB) AND close > swing_lo  → entrada en close
  Stop   = extreme +/- stop_buf%; Target = 2R

Uso:
    python scripts/amd_chart.py
    python scripts/amd_chart.py --days 14 --min-vr 5.0 --lb 30
    python scripts/amd_chart.py --micro --obi-gate --obi-min 0.10
"""

import json, os, sys, time as time_mod, argparse, urllib.request, webbrowser, re
from collections import deque
from datetime import datetime, timezone

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://ztdhvmcisjjyhbqlgkzm.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

def _load_env():
    for f in [".env", os.path.join(os.path.dirname(__file__), "..", ".env")]:
        try:
            for line in open(f):
                m = re.match(r'^(SUPABASE_KEY|SUPABASE_URL)\s*=\s*(.+)', line.strip())
                if m:
                    os.environ.setdefault(m.group(1), m.group(2))
        except FileNotFoundError:
            pass
_load_env()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# ── Fetch ──────────────────────────────────────────────────────────────────────

def fetch_klines(symbol, start_ms, end_ms):
    all_raw = []; cur = start_ms; limit = 1500
    print(f"[fetch] {symbol} M1 {ms_to_str(start_ms)} -> {ms_to_str(end_ms)}")
    while cur < end_ms:
        url = (f"https://fapi.binance.com/fapi/v1/klines?symbol={symbol}"
               f"&interval=1m&startTime={cur}&endTime={end_ms}&limit={limit}")
        req = urllib.request.Request(url, headers={"User-Agent": "amd/1"})
        with urllib.request.urlopen(req, timeout=30) as r:
            page = json.loads(r.read())
        if not page: break
        all_raw.extend(page)
        cur = int(page[-1][0]) + 60_000
        if len(page) < limit: break
        time_mod.sleep(0.08)
    bars = []
    for k in all_raw:
        v = float(k[5]); tb = float(k[9])
        bars.append({"ts": int(k[0]) // 1000, "ts_ms": int(k[0]),
                     "o": float(k[1]), "h": float(k[2]),
                     "l": float(k[3]), "c": float(k[4]),
                     "vol": v, "delta": 2.0 * tb - v})
    print(f"[fetch] {len(bars)} barras ok")
    return bars

def fetch_micro(symbol, start_ms, end_ms):
    """Descarga btc_bars de Supabase y retorna dict ts_ms → {obi_l5, dz, vpin, absorption, bar_delta}."""
    if not SUPABASE_KEY:
        return {}
    start_s = start_ms // 1000
    end_s   = end_ms   // 1000
    params  = (f"symbol=eq.{symbol}&ts_ms=gte.{start_ms}&ts_ms=lte.{end_ms}"
               f"&order=ts_ms.asc"
               f"&select=ts_ms,obi_l5,dz,vpin,absorption,bar_delta,cvd_slope,stacked_imb")
    rows = []; offset = 0; limit = 1000
    print(f"[micro] descargando btc_bars {symbol}...")
    while True:
        url = f"{SUPABASE_URL}/rest/v1/btc_bars?{params}&limit={limit}&offset={offset}"
        req = urllib.request.Request(url, headers={
            "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                page = json.loads(r.read())
        except Exception as e:
            print(f"[micro] error btc_bars: {e}")
            break
        rows.extend(page)
        if len(page) < limit: break
        offset += limit
    if not rows:
        # fallback: try scalping_bars
        print("[micro] btc_bars vacio, intentando scalping_bars...")
        params2 = (f"symbol=eq.{symbol}&ts_ms=gte.{start_ms}&ts_ms=lte.{end_ms}"
                   f"&order=ts_ms.asc&select=ts_ms,obi_l5,dz,bar_delta,cvd_slope")
        offset2 = 0
        while True:
            url2 = f"{SUPABASE_URL}/rest/v1/scalping_bars?{params2}&limit={limit}&offset={offset2}"
            req2 = urllib.request.Request(url2, headers={
                "apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}"})
            try:
                with urllib.request.urlopen(req2, timeout=15) as r:
                    page2 = json.loads(r.read())
            except Exception as e:
                print(f"[micro] error scalping_bars: {e}")
                break
            rows.extend(page2)
            if len(page2) < limit: break
            offset2 += limit
    def _f(v):
        if v is None or v == "None": return 0.0
        try: return float(v)
        except: return 0.0

    result = {}
    for r in rows:
        result[int(r["ts_ms"])] = {
            "obi_l5":    _f(r.get("obi_l5")),
            "dz":        _f(r.get("dz")),
            "vpin":      _f(r.get("vpin")),
            "absorption":_f(r.get("absorption")),
            "bar_delta": _f(r.get("bar_delta")),
            "cvd_slope": _f(r.get("cvd_slope")),
        }
    print(f"[micro] {len(result)} barras con microestructura "
          f"({ms_to_str(start_ms)} -> {ms_to_str(end_ms)})")
    return result

def ms_to_str(ms, fmt="%Y-%m-%d %H:%M"):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(fmt)

def session_of(ts_ms):
    h = (ts_ms // 3_600_000) % 24
    if   8  <= h < 13: return "London"
    elif 13 <= h < 17: return "Overlap"
    elif 17 <= h < 22: return "NewYork"
    else:              return "Asia"

# ── Detector ───────────────────────────────────────────────────────────────────

CFG = dict(lookback=30, min_vr=6.0, stop_buf=0.08, min_rr=2.0,
           cooldown=10, max_hold=120, warmup=50, vr_win=50,
           require_div=True, min_excess=0.0)

class Detector:
    """Liquidity Sweep detector.

    Condiciones (todas en la MISMA barra):
      SHORT: high > swing_hi(LB)  AND  close < swing_hi  AND  vr >= min_vr
             + si require_div: delta < 0  (sellers reales atras del spike)
      LONG:  low  < swing_lo(LB)  AND  close > swing_lo  AND  vr >= min_vr
             + si require_div: delta > 0  (buyers reales atras del spike)
    """
    def __init__(self):
        self.vhist = deque(maxlen=CFG["vr_win"] + 5)
        self.bhist = deque(maxlen=CFG["lookback"] + 2)
        self.seen  = 0
        self.last  = 0

    def _vr(self, v):
        if len(self.vhist) < 10: return 1.0
        m = sum(self.vhist) / len(self.vhist)
        return v / m if m > 0 else 1.0

    def on_bar(self, b, idx):
        h = b["h"]; l = b["l"]; c = b["c"]; v = b["vol"]; d = b["delta"]
        self.seen += 1
        self.vhist.append(v)

        # Swing calculado sobre barras PREVIAS (shift=1)
        if len(self.bhist) >= CFG["lookback"]:
            swing_hi = max(x["h"] for x in self.bhist)
            swing_lo = min(x["l"] for x in self.bhist)
        else:
            self.bhist.append({"h": h, "l": l})
            return None
        self.bhist.append({"h": h, "l": l})

        if self.seen < CFG["warmup"]: return None
        if self.seen - self.last < CFG["cooldown"]: return None

        vr = self._vr(v)
        if vr < CFG["min_vr"]: return None

        is_short = (h > swing_hi) and (c < swing_hi)
        is_long  = (l < swing_lo) and (c > swing_lo)
        if not is_short and not is_long: return None

        # CVD diverge: spike up con sellers reales (delta<0) o spike down con buyers (delta>0)
        if CFG["require_div"]:
            div = (is_short and d < 0) or (is_long and d > 0)
            if not div: return None

        direction = "Short" if is_short else "Long"
        spike_dir = "Up"   if is_short else "Down"
        spike_ext = h      if is_short else l
        swept_lvl = swing_hi if is_short else swing_lo
        excess_pct = ((h - swing_hi) / swing_hi * 100) if is_short \
                else ((swing_lo - l) / swing_lo * 100)

        if excess_pct < CFG["min_excess"]: return None

        entry = c
        buf   = CFG["stop_buf"] / 100
        stop  = spike_ext * (1 + buf) if is_short else spike_ext * (1 - buf)
        risk  = abs(stop - entry)
        if risk < 1: return None
        target = entry - risk * 2 if is_short else entry + risk * 2
        rr = abs(target - entry) / risk
        if rr < CFG["min_rr"]: return None

        sig = dict(
            direction=direction, entry=entry, stop=stop, target=target, rr=round(rr, 3),
            rh=swept_lvl if is_short else swing_hi,
            rl=swing_lo  if is_long  else swept_lvl,
            rng_pct=round(excess_pct, 4),
            rng_bars=CFG["lookback"],
            cvd_range=round(d, 1),
            spike_ext=spike_ext, spike_dir=spike_dir,
            vr_spike=round(vr, 3), delta_spike=round(d, 1),
            vr_entry=round(vr, 3), cvd_slope=None,
            session=session_of(b["ts_ms"]),
            ts_ms=b["ts_ms"], ts=b["ts"],
            idx=idx,
            ai=max(0, idx - CFG["lookback"]),
            si=idx,
            ai_ts=0, si_ts=0,
            exit=None, result_r=None, exit_idx=None, exit_ts=None,
        )
        self.last = self.seen
        return sig

def simulate(signals, bars, capital=50.0, leverage=10.0):
    position_usd = capital * leverage  # $500
    for sig in signals:
        sig["ai_ts"] = bars[min(sig["ai"], len(bars)-1)]["ts"]
        sig["si_ts"] = bars[min(sig["si"], len(bars)-1)]["ts"]
        i0 = sig["idx"]
        for k in range(1, CFG["max_hold"]+1):
            if i0+k >= len(bars): break
            b = bars[i0+k]; h = b["h"]; l = b["l"]
            if sig["direction"] == "Short":
                if l <= sig["target"]:
                    sig["exit"]="TARGET"; sig["result_r"]=sig["rr"]; sig["exit_idx"]=i0+k; sig["exit_ts"]=b["ts"]; break
                if h >= sig["stop"]:
                    sig["exit"]="STOP";   sig["result_r"]=-1.0;      sig["exit_idx"]=i0+k; sig["exit_ts"]=b["ts"]; break
            else:
                if h >= sig["target"]:
                    sig["exit"]="TARGET"; sig["result_r"]=sig["rr"]; sig["exit_idx"]=i0+k; sig["exit_ts"]=b["ts"]; break
                if l <= sig["stop"]:
                    sig["exit"]="STOP";   sig["result_r"]=-1.0;      sig["exit_idx"]=i0+k; sig["exit_ts"]=b["ts"]; break
        else:
            sig["exit"] = "OPEN"; sig["exit_ts"] = bars[min(i0+CFG["max_hold"], len(bars)-1)]["ts"]
        # PnL en dolares
        risk_usd = position_usd * abs(sig["stop"] - sig["entry"]) / sig["entry"]
        if sig["result_r"] is not None:
            sig["pnl_usd"] = round(sig["result_r"] * risk_usd, 2)
        else:
            sig["pnl_usd"] = None
        sig["risk_usd"]   = round(risk_usd, 2)
        sig["pos_usd"]    = round(position_usd, 2)
    return signals

def build_series(bars, signals, micro=None):
    candles=[{"time":b["ts"],"open":b["o"],"high":b["h"],"low":b["l"],"close":b["c"]} for b in bars]
    cvd_vals=[]; acc=0.0
    for b in bars: acc+=b["delta"]; cvd_vals.append(round(acc,1))
    cvd=[{"time":bars[i]["ts"],"value":cvd_vals[i]} for i in range(len(bars))]
    vr_data=[]
    for j,b in enumerate(bars):
        sl=[bars[k]["vol"] for k in range(max(0,j-50),j)]
        mv=sum(sl)/len(sl) if sl else 1.0
        vr_data.append({"time":b["ts"],"value":round(b["vol"]/mv if mv>0 else 1.0,3),"color":"#21262d"})
    # Equity curve
    eq_data = []
    closed_sigs = sorted(
        [s for s in signals if s["pnl_usd"] is not None and s["exit_ts"] is not None],
        key=lambda s: s["exit_ts"]
    )
    cum = 0.0
    for s in closed_sigs:
        cum += s["pnl_usd"]
        eq_data.append({"time": s["exit_ts"], "value": round(cum, 2)})
    # OBI panel — solo cuando hay datos de microestructura
    obi_data = []
    if micro:
        for b in bars:
            m = micro.get(b["ts_ms"])
            if m is not None:
                v = round(m["obi_l5"], 4)
                color = "#3fb950" if v >= 0 else "#f85149"
                obi_data.append({"time": b["ts"], "value": v, "color": color})
    return candles, cvd, vr_data, eq_data, obi_data

# ── HTML ───────────────────────────────────────────────────────────────────────

def build_html(symbol, signals_json, candles_json, cvd_json, vr_json, equity_json, obi_json, capital, leverage, has_micro=False):
    return (
"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>LiqSweep \xb7 """ + symbol + """</title>
<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{background:#0d1117;color:#e6edf3;font-family:'SF Mono','Fira Code',monospace;font-size:12px;height:100vh;display:flex;flex-direction:column;overflow:hidden}
/* header */
#hdr{background:#161b22;border-bottom:1px solid #21262d;padding:8px 14px;display:flex;align-items:center;gap:16px;flex-shrink:0}
#hdr h1{font-size:13px;font-weight:600;letter-spacing:.4px;color:#e6edf3}
.st{color:#8b949e;font-size:11px}.st b{color:#e6edf3}.pos b{color:#3fb950}.neg b{color:#f85149}
#flt{display:flex;gap:6px;margin-left:auto}
select{background:#0d1117;color:#8b949e;border:1px solid #30363d;border-radius:5px;padding:3px 7px;font-size:11px;font-family:inherit;cursor:pointer;outline:none}
select:hover{border-color:#388bfd;color:#e6edf3}
/* toolbar */
#tb{background:#161b22;border-bottom:1px solid #21262d;padding:5px 14px;display:flex;align-items:center;gap:6px;flex-shrink:0}
.tbtn{background:#21262d;color:#8b949e;border:1px solid #30363d;border-radius:5px;padding:4px 10px;font-size:12px;cursor:pointer;font-family:inherit;transition:all .15s;user-select:none}
.tbtn:hover{background:#30363d;color:#e6edf3}
.tbtn.on{background:#1f3460;border-color:#388bfd;color:#58a6ff}
.tbtn.del:hover{background:#3d1a1a;border-color:#da3633;color:#f85149}
#tsep{width:1px;height:20px;background:#30363d;margin:0 2px}
.clr{width:16px;height:16px;border-radius:50%;cursor:pointer;border:2px solid transparent;display:inline-block;transition:border-color .15s}
.clr.on{border-color:#e6edf3}
#tbhint{color:#484f58;font-size:10px;margin-left:auto}
/* wrap */
#wrap{display:flex;flex:1;overflow:hidden}
/* sidebar */
#sidebar{width:252px;flex-shrink:0;background:#0d1117;border-right:1px solid #21262d;display:flex;flex-direction:column;overflow:hidden}
#sh{padding:6px 10px;background:#161b22;border-bottom:1px solid #21262d;color:#484f58;font-size:10px;text-transform:uppercase;letter-spacing:.8px;display:grid;grid-template-columns:20px 1fr 50px 32px 42px;gap:4px}
#sl{flex:1;overflow-y:auto}
#sl::-webkit-scrollbar{width:3px}
#sl::-webkit-scrollbar-thumb{background:#30363d;border-radius:2px}
.row{display:grid;grid-template-columns:20px 1fr 50px 32px 42px;gap:4px;align-items:center;padding:6px 10px;border-bottom:1px solid #0d1117;cursor:pointer;transition:background .1s;border-left:2px solid transparent;border-right:2px solid transparent}
.row:hover{background:#161b22}
.row.on{background:#161b22;border-left-color:#388bfd}
.row.T{border-right-color:#3fb950}.row.S{border-right-color:#f85149}.row.O{border-right-color:#30363d}
.rn{color:#484f58;font-size:10px}.ri{display:flex;flex-direction:column;gap:1px;overflow:hidden}
.rt{color:#8b949e;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rs{font-size:10px;color:#58a6ff}
.rd{font-size:11px;font-weight:600;text-align:center}
.rd.sh{color:#f85149}.rd.lo{color:#3fb950}
.rr2{font-size:11px;font-weight:600;text-align:right}
.rr2.p{color:#3fb950}.rr2.n{color:#f85149}.rr2.u{color:#484f58}
/* right */
#right{flex:1;display:flex;flex-direction:column;overflow:hidden}
#ibar{background:#161b22;border-bottom:1px solid #21262d;padding:6px 14px;display:flex;align-items:center;gap:10px;flex-shrink:0;min-height:34px;flex-wrap:wrap}
.pill{background:#21262d;border-radius:4px;padding:2px 7px;font-size:10px;color:#8b949e;border:1px solid transparent}
.pill b{color:#e6edf3}.pill.g{border-color:#238636}.pill.g b{color:#3fb950}
.pill.r{border-color:#da3633}.pill.r b{color:#f85149}
.pill.o b{color:#f0883e}.pill.v b{color:#bc8cff}
/* charts */
#charts{flex:1;display:flex;flex-direction:column;overflow:hidden}
#pc{flex:4;min-height:0;position:relative}
#overlay{position:absolute;top:0;left:0;z-index:5;pointer-events:none}
.div{height:1px;background:#21262d;flex-shrink:0}
#cc{flex:1;min-height:0;position:relative}
#vc{flex:1;min-height:0;position:relative}
#ec{flex:1;min-height:0;position:relative}
.lbl{position:absolute;top:4px;left:8px;font-size:9px;color:#30363d;letter-spacing:.6px;text-transform:uppercase;pointer-events:none;z-index:10}
#pnl-block{display:flex;align-items:center;gap:10px;padding:0 12px;border-left:1px solid #30363d;margin-left:4px}
.pnl-pos{color:#3fb950;font-weight:700;font-size:13px}
.pnl-neg{color:#f85149;font-weight:700;font-size:13px}
.pnl-lbl{color:#484f58;font-size:10px}
</style>
</head>
<body>
<div id="hdr">
  <h1>LiqSweep \xb7 """ + symbol + """</h1>
  <div class="st">n <b id="sn">0</b></div>
  <div class="st pos">WR <b id="sw">-</b></div>
  <div class="st" id="saw">avgR <b id="sa">-</b></div>
  <div class="st">TARGET <b id="st2">0</b></div>
  <div class="st neg">STOP <b id="ss">0</b></div>
  <div class="st">OPEN <b id="so">0</b></div>
  <div id="pnl-block">
    <span class="pnl-lbl">$""" + str(int(capital)) + """ &times; """ + str(int(leverage)) + """x</span>
    <span id="pnl-total" class="pnl-pos">$0.00</span>
    <span id="pnl-pct"   class="pnl-lbl">(0.00%)</span>
    <span id="pnl-dd"    class="pnl-lbl">DD $0</span>
  </div>
  <div id="flt">
    <select id="fs" onchange="filt()"><option value="">Sesion</option></select>
    <select id="fd" onchange="filt()"><option value="">Dir</option></select>
    <select id="fe" onchange="filt()"><option value="">Exit</option></select>
  </div>
</div>
<div id="tb">
  <button class="tbtn on" id="t-cursor" title="Cursor (Esc)">&#9654; Cursor</button>
  <button class="tbtn"    id="t-pencil" title="Lapiz freehand (D)">&#9998; Lapiz</button>
  <div id="tsep"></div>
  <span class="clr on"  style="background:#e6edf3" data-c="#e6edf3" title="Blanco"></span>
  <span class="clr"     style="background:#f85149" data-c="#f85149" title="Rojo"></span>
  <span class="clr"     style="background:#3fb950" data-c="#3fb950" title="Verde"></span>
  <span class="clr"     style="background:#f0883e" data-c="#f0883e" title="Naranja"></span>
  <span class="clr"     style="background:#bc8cff" data-c="#bc8cff" title="Violeta"></span>
  <span class="clr"     style="background:#58a6ff" data-c="#58a6ff" title="Azul"></span>
  <div id="tsep"></div>
  <button class="tbtn del" id="t-clear" title="Borrar todos los trazos">&#10005; Borrar</button>
  <div id="tsep"></div>
  <button class="tbtn on" id="t-cvd" onclick="togglePanel('cc','t-cvd')">CVD</button>
  <button class="tbtn on" id="t-vr"  onclick="togglePanel('vc','t-vr')">VR</button>
  <button class="tbtn on" id="t-eq"  onclick="togglePanel('ec','t-eq')">EQUITY</button>
  """ + ("""<button class="tbtn on" id="t-obi" onclick="togglePanel('oc','t-obi')">OBI</button>""" if has_micro else "") + """
  <span id="tbhint">D = lapiz &nbsp; Esc = cursor &nbsp; &#8593;&#8595; = navegar</span>
</div>
<div id="wrap">
  <div id="sidebar">
    <div id="sh"><span>#</span><span>Fecha / Sesion</span><span style="text-align:center">Dir</span><span style="text-align:right">R</span><span style="text-align:right">$</span></div>
    <div id="sl"></div>
  </div>
  <div id="right">
    <div id="ibar"><span style="color:#484f58;font-size:11px">Selecciona una senal de la tabla o haz click en un marker del chart</span></div>
    <div id="charts">
      <div id="pc">
        <canvas id="overlay"></canvas>
        <span class="lbl">PRECIO M1</span>
      </div>
      <div class="div"></div>
      <div id="cc" style="position:relative"><span class="lbl">CVD</span></div>
      <div class="div"></div>
      <div id="vc" style="position:relative"><span class="lbl">VR</span></div>
      <div class="div"></div>
      <div id="ec" style="position:relative"><span class="lbl">EQUITY $</span></div>
      """ + ("""<div class="div"></div><div id="oc" style="position:relative"><span class="lbl">OBI L5</span></div>""" if has_micro else "") + """
    </div>
  </div>
</div>
<script>
const SIGS    = """ + signals_json + """;
const CANDLES = """ + candles_json + """;
const CVD     = """ + cvd_json + """;
const VR_D    = """ + vr_json + """;
const EQ_D    = """ + equity_json + """;
const OBI_D   = """ + obi_json + """;
const CAPITAL = """ + str(capital) + """;
const LEVERAGE= """ + str(leverage) + """;
const HAS_MICRO = """ + ("true" if has_micro else "false") + """;

// ── Charts ────────────────────────────────────────────────────────────────────
const BASE = {
  layout: { background:{ type:'solid', color:'#0d1117' }, textColor:'#8b949e', fontSize:10 },
  grid: { vertLines:{ color:'#161b22' }, horzLines:{ color:'#161b22' } },
  crosshair: { mode:1 },
  timeScale: { timeVisible:true, secondsVisible:false, borderColor:'#21262d', rightOffset:14 },
  rightPriceScale: { borderColor:'#21262d' },
  handleScroll: { mouseWheel:true, pressedMouseMove:true },
  handleScale: { axisPressedMouseMove:true, mouseWheel:true, pinch:true },
};
const pEl=document.getElementById('pc');
const cEl=document.getElementById('cc');
const vEl=document.getElementById('vc');
const eEl=document.getElementById('ec');
const oEl=HAS_MICRO?document.getElementById('oc'):null;

const PC=LightweightCharts.createChart(pEl,{...BASE});
const CC=LightweightCharts.createChart(cEl,{...BASE});
const VC=LightweightCharts.createChart(vEl,{...BASE});
const EC=LightweightCharts.createChart(eEl,{...BASE});
const OC=oEl?LightweightCharts.createChart(oEl,{...BASE}):null;

const ALL_CHARTS=[PC,CC,VC,EC,...(OC?[OC]:[])];

// Sync zoom
let syncing=false;
function syncR(src,...ts){
  src.timeScale().subscribeVisibleLogicalRangeChange(r=>{
    if(syncing||!r)return; syncing=true;
    ts.forEach(c=>c.timeScale().setVisibleLogicalRange(r)); syncing=false;
  });
}
syncR(PC,CC,VC,EC,...(OC?[OC]:[]));
syncR(CC,PC,VC,EC,...(OC?[OC]:[]));
syncR(VC,PC,CC,EC,...(OC?[OC]:[]));
syncR(EC,PC,CC,VC,...(OC?[OC]:[]));
if(OC) syncR(OC,PC,CC,VC,EC);

// Sync crosshair
function syncX(src,...ts){
  src.subscribeCrosshairMove(p=>{
    if(!p.time){ts.forEach(c=>c.clearCrosshairPosition());return;}
    ts.forEach(c=>{ try{c.setCrosshairPosition(0,p.time,c._s);}catch(_){} });
  });
}

const cSeries=PC.addCandlestickSeries({
  upColor:'#3fb950',downColor:'#f85149',
  wickUpColor:'#3fb950',wickDownColor:'#f85149',
  borderUpColor:'#3fb950',borderDownColor:'#f85149',
});
cSeries.setData(CANDLES); PC._s=cSeries;

const cvdS=CC.addBaselineSeries({
  baseValue:{type:'price',price:0},
  topLineColor:'#388bfd',topFillColor1:'rgba(56,139,253,0.15)',topFillColor2:'rgba(56,139,253,0.03)',
  bottomLineColor:'#f85149',bottomFillColor1:'rgba(248,81,73,0.03)',bottomFillColor2:'rgba(248,81,73,0.15)',
  lineWidth:1,
});
cvdS.setData(CVD); CC._s=cvdS;

const vrS=VC.addHistogramSeries({priceFormat:{type:'volume'}});
vrS.setData(VR_D); VC._s=vrS;

// Equity curve — linea azul/verde/roja segun PnL acumulado
const eqS=EC.addBaselineSeries({
  baseValue:{type:'price',price:0},
  topLineColor:'#3fb950',topFillColor1:'rgba(63,185,80,0.15)',topFillColor2:'rgba(63,185,80,0.03)',
  bottomLineColor:'#f85149',bottomFillColor1:'rgba(248,81,73,0.03)',bottomFillColor2:'rgba(248,81,73,0.15)',
  lineWidth:2,priceFormat:{type:'price',precision:2,minMove:0.01},
});
if(EQ_D.length) eqS.setData(EQ_D); EC._s=eqS;

// OBI panel
let obiS=null;
if(OC && OBI_D.length){
  obiS=OC.addHistogramSeries({
    priceFormat:{type:'price',precision:3,minMove:0.001},
  });
  obiS.setData(OBI_D); OC._s=obiS;
  // zero line
  OC.addLineSeries({color:'rgba(255,255,255,0.15)',lineWidth:1,lineStyle:2})
     .setData(OBI_D.map(d=>({time:d.time,value:0})));
}

syncX(PC,CC,VC,EC,...(OC?[OC]:[]));
syncX(CC,PC,VC,EC,...(OC?[OC]:[]));
syncX(VC,PC,CC,EC,...(OC?[OC]:[]));
syncX(EC,PC,CC,VC,...(OC?[OC]:[]));
if(OC) syncX(OC,PC,CC,VC,EC);

// Resize
new ResizeObserver(()=>{
  PC.applyOptions({width:pEl.clientWidth,height:pEl.clientHeight});
  CC.applyOptions({width:cEl.clientWidth,height:cEl.clientHeight});
  VC.applyOptions({width:vEl.clientWidth,height:vEl.clientHeight});
  EC.applyOptions({width:eEl.clientWidth,height:eEl.clientHeight});
  if(OC && oEl) OC.applyOptions({width:oEl.clientWidth,height:oEl.clientHeight});
  resizeOv();
}).observe(document.getElementById('charts'));

// ── Canvas overlay ────────────────────────────────────────────────────────────
const ov=document.getElementById('overlay');
const ctx=ov.getContext('2d');

function resizeOv(){
  ov.width=pEl.clientWidth; ov.height=pEl.clientHeight;
  redraw();
}

// Redibujar en scroll/zoom
PC.timeScale().subscribeVisibleLogicalRangeChange(()=>redraw());

// ── Estado ────────────────────────────────────────────────────────────────────
let selSig=null;
let pLines=[];
let drawings=[]; // [{color, pts:[{time,price}]}]
let curPath=null;
let drawing=false;
let tool='cursor';
let penColor='#e6edf3';
let curIdx=-1;
let filtered=[];

// ── Position box ──────────────────────────────────────────────────────────────
function drawBox(sig, isSel){
  const eX=PC.timeScale().timeToCoordinate(sig.ts);
  const xX=PC.timeScale().timeToCoordinate(sig.exit_ts || sig.ts+3600);
  const eY=cSeries.priceToCoordinate(sig.entry);
  const sY=cSeries.priceToCoordinate(sig.stop);
  const tY=cSeries.priceToCoordinate(sig.target);
  if(eX==null||xX==null||eY==null||sY==null||tY==null)return;

  // Opacidades: seleccionada brilla mas, el resto sutil
  const fa = isSel ? 0.22 : 0.09;  // fill alpha
  const sa = isSel ? 0.55 : 0.25;  // stroke alpha

  const x0=Math.min(eX,xX), x1=Math.max(eX,xX), w=Math.max(x1-x0,2);

  // Zona de perdida (rojo)
  const ly0=Math.min(eY,sY), lh=Math.abs(sY-eY);
  ctx.fillStyle='rgba(248,81,73,'+fa+')';
  ctx.fillRect(x0,ly0,w,lh);
  ctx.strokeStyle='rgba(248,81,73,'+sa+')';
  ctx.lineWidth=isSel?1:0.5; ctx.setLineDash([]);
  ctx.strokeRect(x0,ly0,w,lh);

  // Zona de ganancia (verde)
  const gy0=Math.min(eY,tY), gh=Math.abs(tY-eY);
  ctx.fillStyle='rgba(63,185,80,'+fa+')';
  ctx.fillRect(x0,gy0,w,gh);
  ctx.strokeStyle='rgba(63,185,80,'+sa+')';
  ctx.strokeRect(x0,gy0,w,gh);

  // Linea de entry
  ctx.strokeStyle='rgba(230,237,243,'+(isSel?0.6:0.2)+')';
  ctx.lineWidth=1; ctx.setLineDash(isSel?[5,4]:[]);
  ctx.beginPath(); ctx.moveTo(x0,eY); ctx.lineTo(x1,eY); ctx.stroke();
  ctx.setLineDash([]);

  // Labels y badge solo en la seleccionada
  if(isSel){
    ctx.font='bold 10px monospace'; ctx.textAlign='left';
    ctx.fillStyle='rgba(63,185,80,0.9)';
    ctx.fillText('T '+sig.target.toFixed(1), x1+4, tY+4);
    ctx.fillStyle='rgba(248,81,73,0.9)';
    ctx.fillText('S '+sig.stop.toFixed(1), x1+4, sY+4);
    ctx.fillStyle='rgba(230,237,243,0.85)';
    ctx.fillText('E '+sig.entry.toFixed(1), x0+4, eY-5);
    ctx.textAlign='start';
    if(sig.result_r!=null){
      const isWin=sig.result_r>0;
      ctx.fillStyle=isWin?'rgba(63,185,80,0.9)':'rgba(248,81,73,0.9)';
      ctx.font='bold 11px monospace';
      ctx.fillText((sig.result_r>0?'+':'')+sig.result_r.toFixed(2)+'R',
                   x0+4, isWin?gy0+13:ly0+13);
    }
  } else {
    // Para las no seleccionadas: solo un mini badge del resultado
    if(sig.result_r!=null){
      ctx.font='9px monospace'; ctx.textAlign='left';
      ctx.fillStyle=sig.result_r>0?'rgba(63,185,80,0.5)':'rgba(248,81,73,0.5)';
      ctx.fillText((sig.result_r>0?'+':'')+sig.result_r.toFixed(1)+'R', x0+2, eY-3);
      ctx.textAlign='start';
    }
  }
}

// ── Trazos ────────────────────────────────────────────────────────────────────
function drawPath(pts, color, width){
  if(pts.length<2)return;
  ctx.strokeStyle=color; ctx.lineWidth=width||2;
  ctx.lineCap='round'; ctx.lineJoin='round'; ctx.setLineDash([]);
  ctx.beginPath();
  pts.forEach((p,i)=>{
    const x=PC.timeScale().timeToCoordinate(p.time);
    const y=cSeries.priceToCoordinate(p.price);
    if(x==null||y==null)return;
    i===0?ctx.moveTo(x,y):ctx.lineTo(x,y);
  });
  ctx.stroke();
}

function redraw(){
  ctx.clearRect(0,0,ov.width,ov.height);
  // Primero todas las senales (baja opacidad)
  filtered.forEach(sig=>{
    if(sig!==selSig) drawBox(sig, false);
  });
  // La seleccionada encima (alta opacidad)
  if(selSig) drawBox(selSig, true);
  // Trazos del lapiz
  drawings.forEach(d=>drawPath(d.pts,d.color,2));
  if(curPath) drawPath(curPath.pts,curPath.color,2);
}

// ── Drawing tool events ───────────────────────────────────────────────────────
ov.addEventListener('mousedown',e=>{
  if(tool!=='pencil')return;
  e.stopPropagation(); drawing=true;
  const t=PC.timeScale().coordinateToTime(e.offsetX);
  const p=cSeries.coordinateToPrice(e.offsetY);
  if(t==null||p==null)return;
  curPath={color:penColor,pts:[{time:t,price:p}]};
});
ov.addEventListener('mousemove',e=>{
  if(!drawing||tool!=='pencil')return;
  const t=PC.timeScale().coordinateToTime(e.offsetX);
  const p=cSeries.coordinateToPrice(e.offsetY);
  if(t==null||p==null)return;
  curPath.pts.push({time:t,price:p}); redraw();
});
ov.addEventListener('mouseup',e=>{
  if(!drawing)return; drawing=false;
  if(curPath&&curPath.pts.length>1) drawings.push(curPath);
  curPath=null; redraw();
});
ov.addEventListener('mouseleave',e=>{
  if(drawing&&curPath&&curPath.pts.length>1) drawings.push(curPath);
  drawing=false; curPath=null; redraw();
});

// ── Toolbar ───────────────────────────────────────────────────────────────────
function setTool(t){
  tool=t;
  const isPencil=(t==='pencil');
  ov.style.pointerEvents=isPencil?'auto':'none';
  ov.style.cursor=isPencil?'crosshair':'default';
  document.getElementById('t-cursor').classList.toggle('on',t==='cursor');
  document.getElementById('t-pencil').classList.toggle('on',t==='pencil');
}
document.getElementById('t-cursor').addEventListener('click',()=>setTool('cursor'));
document.getElementById('t-pencil').addEventListener('click',()=>setTool('pencil'));
document.getElementById('t-clear').addEventListener('click',()=>{ drawings=[]; redraw(); });

function togglePanel(id, btnId){
  const panel=document.getElementById(id);
  const divs=document.querySelectorAll('.div');
  const btn=document.getElementById(btnId);
  const visible=panel.style.display!=='none';
  panel.style.display=visible?'none':'';
  // ocultar/mostrar el separador correspondiente
  panel.previousElementSibling.style.display=visible?'none':'';
  btn.classList.toggle('on',!visible);
  // reajustar chart size
  setTimeout(()=>{
    PC.applyOptions({width:pEl.clientWidth,height:pEl.clientHeight});
    CC.applyOptions({width:cEl.clientWidth,height:cEl.clientHeight});
    VC.applyOptions({width:vEl.clientWidth,height:vEl.clientHeight});
    EC.applyOptions({width:eEl.clientWidth,height:eEl.clientHeight});
    if(OC && oEl) OC.applyOptions({width:oEl.clientWidth,height:oEl.clientHeight});
    resizeOv(); redraw();
  },50);
}
document.querySelectorAll('.clr').forEach(el=>{
  el.addEventListener('click',()=>{
    penColor=el.dataset.c;
    document.querySelectorAll('.clr').forEach(c=>c.classList.remove('on'));
    el.classList.add('on');
  });
});

// ── Seleccion de senal ────────────────────────────────────────────────────────
function clearLines(){ pLines.forEach(l=>{ try{cSeries.removePriceLine(l);}catch(_){} }); pLines=[]; }

function selectSig(sig, scroll=true){
  selSig=sig; curIdx=SIGS.indexOf(sig);
  clearLines();
  // Lineas de precio (markers en el eje)
  pLines.push(cSeries.createPriceLine({price:sig.stop,color:'#f85149',lineWidth:1,
    lineStyle:LightweightCharts.LineStyle.Dashed,title:'STOP',axisLabelVisible:true}));
  pLines.push(cSeries.createPriceLine({price:sig.target,color:'#3fb950',lineWidth:1,
    lineStyle:LightweightCharts.LineStyle.Dashed,title:'TARGET',axisLabelVisible:true}));
  pLines.push(cSeries.createPriceLine({price:sig.rh,color:'rgba(56,139,253,0.5)',lineWidth:1,
    lineStyle:LightweightCharts.LineStyle.Dotted,title:'RH',axisLabelVisible:false}));
  pLines.push(cSeries.createPriceLine({price:sig.rl,color:'rgba(56,139,253,0.5)',lineWidth:1,
    lineStyle:LightweightCharts.LineStyle.Dotted,title:'RL',axisLabelVisible:false}));

  if(scroll){
    PC.timeScale().setVisibleRange({
      from: sig.ai_ts - 40*60,
      to:   (sig.exit_ts||sig.ts) + 80*60,
    });
  }
  redraw();

  // Info bar
  const r=sig.result_r;
  const rStr=r!=null?(r>0?'+'+r.toFixed(2)+'R':r.toFixed(2)+'R'):sig.exit;
  const rc=r>0?'g':(r<0?'r':'');
  const d=new Date(sig.ts_ms);
  const dt=d.toISOString().slice(0,16).replace('T',' ')+' UTC';
  const divOk=(sig.delta_spike<0&&sig.spike_dir==='Up')||(sig.delta_spike>0&&sig.spike_dir==='Down');
  document.getElementById('ibar').innerHTML=
    '<span style="font-size:13px;font-weight:600;color:'+(sig.direction==='Short'?'#f85149':'#3fb950')+'">'+
    (sig.direction==='Short'?'&#9660; SHORT':'&#9650; LONG')+'</span>'+
    '<div class="pill"><b>'+dt+'</b></div>'+
    '<div class="pill"><b>'+sig.session+'</b></div>'+
    '<div class="pill o">Sweep <b>'+sig.spike_dir+' VR='+sig.vr_spike.toFixed(2)+'x '+(divOk?'CVD&#10003;':'CVD&#10007;')+'</b></div>'+
    '<div class="pill v">Excess <b>'+sig.rng_pct.toFixed(3)+'% | LB='+sig.rng_bars+'b</b></div>'+
    '<div class="pill">E/S/T <b>'+sig.entry.toFixed(1)+' / '+sig.stop.toFixed(1)+' / '+sig.target.toFixed(1)+'</b></div>'+
    '<div class="pill">Riesgo <b>$'+sig.risk_usd.toFixed(2)+'</b> | Pos <b>$'+sig.pos_usd.toFixed(0)+'</b></div>'+
    '<div class="pill '+rc+'">R <b>'+rStr+'</b></div>'+
    (sig.pnl_usd!=null?'<div class="pill '+(sig.pnl_usd>=0?'g':'r')+'">PnL <b>'+(sig.pnl_usd>=0?'+$':'-$')+Math.abs(sig.pnl_usd).toFixed(2)+'</b></div>':'')+
    (sig.obi_l5!=null?'<div class="pill" style="border-color:'+(sig.obi_l5>=0?'#238636':'#da3633')+'">OBI <b style="color:'+(sig.obi_l5>=0?'#3fb950':'#f85149')+'">'+sig.obi_l5.toFixed(3)+'</b> DZ <b>'+sig.dz.toFixed(2)+'</b></div>':'');

  // Highlight tabla
  document.querySelectorAll('.row').forEach(el=>{
    const isMe=Number(el.dataset.i)===curIdx;
    el.classList.toggle('on',isMe);
    if(isMe) el.scrollIntoView({block:'nearest'});
  });
}

// Click en chart para seleccionar senal cercana
PC.subscribeClick(p=>{
  if(!p.time||tool!=='cursor')return;
  let best=null,d=Infinity;
  filtered.forEach(s=>{
    const dd=Math.abs(s.ts-p.time);
    if(dd<d&&dd<300){d=dd;best=s;}
  });
  if(best) selectSig(best,false);
});

// ── Markers ───────────────────────────────────────────────────────────────────
function buildMarkers(sigs){
  const m=[];
  sigs.forEach(sig=>{
    const clr=sig.exit==='TARGET'?'#3fb950':(sig.exit==='STOP'?'#f85149':'#8b949e');
    const r=sig.result_r;
    const txt=r!=null?(r>0?'+'+r.toFixed(1):r.toFixed(1))+'R':'?';
    m.push({time:sig.ts,
      position:sig.direction==='Short'?'aboveBar':'belowBar',
      color:clr, shape:sig.direction==='Short'?'arrowDown':'arrowUp',
      text:txt, size:1});
    m.push({time:sig.si_ts,
      position:sig.spike_dir==='Up'?'aboveBar':'belowBar',
      color:'#f0883e', shape:'circle', text:'', size:0});
  });
  m.sort((a,b)=>a.time-b.time);
  cSeries.setMarkers(m);
}

// ── Tabla ─────────────────────────────────────────────────────────────────────
function buildTable(sigs){
  const sl=document.getElementById('sl');
  sl.innerHTML='';
  sigs.forEach((sig,i)=>{
    const oi=SIGS.indexOf(sig);
    const r=sig.result_r;
    const rStr=r!=null?(r>0?'+'+r.toFixed(2):r.toFixed(2)):'—';
    const rc=r>0?'p':(r<0?'n':'u');
    const ec=sig.exit==='TARGET'?'T':(sig.exit==='STOP'?'S':'O');
    const d=new Date(sig.ts_ms);
    const ts=d.toISOString().slice(5,16).replace('T',' ');
    const el=document.createElement('div');
    el.className='row '+ec+(oi===curIdx?' on':'');
    el.dataset.i=oi;
    const pnl=sig.pnl_usd!=null?sig.pnl_usd:null;
    const pStr=pnl!=null?(pnl>=0?'+$'+pnl.toFixed(2):'-$'+Math.abs(pnl).toFixed(2)):'—';
    const pc2=pnl!=null?(pnl>0?'p':(pnl<0?'n':'u')):'u';
    el.innerHTML='<span class="rn">'+(i+1)+'</span>'+
      '<div class="ri"><span class="rt">'+ts+'</span><span class="rs">'+sig.session+'</span></div>'+
      '<span class="rd '+(sig.direction==='Short'?'sh':'lo')+'">'+(sig.direction==='Short'?'&#9660;':'&#9650;')+' '+sig.direction+'</span>'+
      '<span class="rr2 '+rc+'">'+rStr+'</span>'+
      '<span class="rr2 '+pc2+'" style="font-size:9px">'+pStr+'</span>';
    el.addEventListener('click',()=>selectSig(sig));
    sl.appendChild(el);
  });
  buildMarkers(sigs);
}

function updateStats(sigs){
  const cl=sigs.filter(s=>s.exit==='TARGET'||s.exit==='STOP');
  const n=cl.length, wins=cl.filter(s=>s.result_r>0).length;
  const avg=n>0?cl.reduce((a,s)=>a+s.result_r,0)/n:0;
  document.getElementById('sn').textContent=sigs.length;
  document.getElementById('sw').textContent=n>0?(wins/n*100).toFixed(1)+'%':'-';
  document.getElementById('sa').textContent=n>0?(avg>=0?'+':'')+avg.toFixed(3):'-';
  document.getElementById('saw').className='st '+(avg>=0?'pos':'neg');
  document.getElementById('st2').textContent=sigs.filter(s=>s.exit==='TARGET').length;
  document.getElementById('ss').textContent=sigs.filter(s=>s.exit==='STOP').length;
  document.getElementById('so').textContent=sigs.filter(s=>s.exit==='OPEN').length;
  // PnL block
  const totalPnl=cl.reduce((a,s)=>a+(s.pnl_usd||0),0);
  const pct=CAPITAL>0?(totalPnl/CAPITAL*100):0;
  // Max drawdown
  let peak=0,dd=0,run=0;
  cl.forEach(s=>{run+=s.pnl_usd||0; if(run>peak)peak=run; dd=Math.min(dd,run-peak);});
  const pnlEl=document.getElementById('pnl-total');
  pnlEl.textContent=(totalPnl>=0?'+':'')+totalPnl.toFixed(2)+'$';
  pnlEl.className=totalPnl>=0?'pnl-pos':'pnl-neg';
  document.getElementById('pnl-pct').textContent='('+(pct>=0?'+':'')+pct.toFixed(1)+'%)';
  document.getElementById('pnl-dd').textContent='DD '+(dd===0?'$0':dd.toFixed(2)+'$');
}

function filt(){
  const fs=document.getElementById('fs').value;
  const fd=document.getElementById('fd').value;
  const fe=document.getElementById('fe').value;
  filtered=SIGS.filter(s=>(!fs||s.session===fs)&&(!fd||s.direction===fd)&&(!fe||s.exit===fe));
  buildTable(filtered); updateStats(filtered);
}

// Inicializar filtros
['session','direction','exit'].forEach((k,ki)=>{
  const sel=[document.getElementById('fs'),document.getElementById('fd'),document.getElementById('fe')][ki];
  [...new Set(SIGS.map(s=>s[k]))].sort().forEach(v=>{
    sel.innerHTML+='<option value="'+v+'">'+v+'</option>';
  });
});

// Teclado
document.addEventListener('keydown',e=>{
  if(e.key==='d'||e.key==='D') setTool(tool==='pencil'?'cursor':'pencil');
  if(e.key==='Escape') setTool('cursor');
  if(!filtered.length)return;
  const ci=filtered.findIndex(s=>SIGS.indexOf(s)===curIdx);
  if(e.key==='ArrowDown'&&ci<filtered.length-1) selectSig(filtered[ci+1]);
  if(e.key==='ArrowUp'&&ci>0) selectSig(filtered[ci-1]);
});

// Arrancar
filt();
if(filtered.length) selectSig(filtered[0]);
resizeOv();
</script>
</body>
</html>"""
    )

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Liquidity Sweep Chart")
    ap.add_argument("symbol",       nargs="?", default="BTCUSDT")
    ap.add_argument("--days",       type=int,   default=14)
    ap.add_argument("--session",    default=None,
                        help="Sesiones: London,Asia,Overlap,NewYork")
    ap.add_argument("--dir",        choices=["Short","Long"], default=None)
    ap.add_argument("--lb",         type=int,   default=None,
                        help="Lookback bars swing hi/lo (default 30)")
    ap.add_argument("--min-vr",     type=float, default=None,
                        help="VR minimo en barra de sweep (default 6.0)")
    ap.add_argument("--cooldown",   type=int,   default=None,
                        help="Barras cooldown entre senales (default 10)")
    ap.add_argument("--no-div",     action="store_true",
                        help="No exigir CVD diverge en el sweep")
    ap.add_argument("--min-excess", type=float, default=None,
                        help="Excess minimo mas alla del swing %% (default 0.0)")
    ap.add_argument("--capital",    type=float, default=50.0,
                        help="Capital en USD (default 50)")
    ap.add_argument("--leverage",   type=float, default=10.0,
                        help="Apalancamiento (default 10)")
    ap.add_argument("--micro",      action="store_true",
                        help="Cargar OBI/microestructura de Supabase btc_bars")
    ap.add_argument("--obi-gate",   action="store_true",
                        help="Filtrar senales usando OBI: Short requiere obi<-threshold, Long obi>threshold")
    ap.add_argument("--obi-min",    type=float, default=0.10,
                        help="Umbral OBI para el gate (default 0.10, escala -1..1)")
    ap.add_argument("-o",           default="amd_chart.html")
    args = ap.parse_args()

    now_ms   = int(time_mod.time() * 1000)
    start_ms = now_ms - args.days * 86400 * 1000
    bars = fetch_klines(args.symbol, start_ms, now_ms)
    if not bars: sys.exit("Sin barras")

    if args.lb          is not None: CFG["lookback"]     = args.lb
    if args.min_vr      is not None: CFG["min_vr"]       = args.min_vr
    if args.cooldown    is not None: CFG["cooldown"]     = args.cooldown
    if args.min_excess  is not None: CFG["min_excess"]   = args.min_excess
    if args.no_div:                  CFG["require_div"]  = False

    capital  = args.capital
    leverage = args.leverage

    # Cargar microestructura de Supabase cuando se pide
    micro = {}
    if args.micro:
        micro = fetch_micro(args.symbol, start_ms, now_ms)

    det = Detector()

    allowed_ses = set(args.session.split(",")) if args.session else None
    signals = []
    for i, b in enumerate(bars):
        sig = det.on_bar(b, i)
        if sig is None: continue
        if allowed_ses and sig["session"] not in allowed_ses: continue
        if args.dir     and sig["direction"] != args.dir:   continue

        # OBI gate: filtrar por orderbook si hay datos en esa barra
        if args.obi_gate and micro:
            m = micro.get(b["ts_ms"])
            if m is not None:
                obi = m["obi_l5"]
                if sig["direction"] == "Short" and obi > -args.obi_min:
                    continue  # OBI no confirma presion vendedora
                if sig["direction"] == "Long"  and obi < args.obi_min:
                    continue  # OBI no confirma presion compradora

        # Adjuntar valores de microestructura a la señal (para info bar)
        if micro:
            m = micro.get(b["ts_ms"])
            if m:
                sig["obi_l5"]    = round(m["obi_l5"], 4)
                sig["dz"]        = round(m["dz"], 3)
                sig["vpin"]      = round(m["vpin"], 3)
                sig["cvd_slope_m"] = round(m["cvd_slope"], 2)

        signals.append(sig)

    simulate(signals, bars, capital=capital, leverage=leverage)
    n_t=sum(1 for s in signals if s["exit"]=="TARGET")
    n_s=sum(1 for s in signals if s["exit"]=="STOP")
    n_o=sum(1 for s in signals if s["exit"]=="OPEN")
    closed_pnl = sum(s["pnl_usd"] for s in signals if s["pnl_usd"] is not None)
    obi_note = f" | OBI gate={args.obi_min}" if args.obi_gate and micro else ""
    print(f"[detector] {len(signals)} senales | TARGET={n_t} STOP={n_s} OPEN={n_o} | PnL={closed_pnl:+.2f}${obi_note}")
    if not signals: sys.exit("Sin senales.")

    print("[build] Preparando series...")
    candles, cvd, vr, equity, obi_series = build_series(bars, signals, micro=micro)
    has_micro = bool(obi_series)

    html = build_html(
        symbol       = args.symbol,
        signals_json = json.dumps(signals,     separators=(',',':')),
        candles_json = json.dumps(candles,     separators=(',',':')),
        cvd_json     = json.dumps(cvd,         separators=(',',':')),
        vr_json      = json.dumps(vr,          separators=(',',':')),
        equity_json  = json.dumps(equity,      separators=(',',':')),
        obi_json     = json.dumps(obi_series,  separators=(',',':')),
        capital      = capital,
        leverage     = leverage,
        has_micro    = has_micro,
    )

    out = args.o
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    kb = os.path.getsize(out) // 1024
    print(f"[output] {out}  ({kb} KB)")
    webbrowser.open(f"file:///{os.path.abspath(out)}")
    print("[done] Abierto en el browser")

if __name__ == "__main__":
    main()
