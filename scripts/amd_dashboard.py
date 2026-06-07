#!/usr/bin/env python3
"""
AMD Dashboard — app interactiva Dash/Plotly.

Corre un servidor local y abre el browser automaticamente.
Podes navegar cada senal con flechas, ver el chart con zoom/pan,
hover sobre las velas para ver OHLCV, y filtrar por sesion/direccion.

Uso:
    python scripts/amd_dashboard.py
    python scripts/amd_dashboard.py --days 14 --no-diverge
    python scripts/amd_dashboard.py --days 7 --session London
    python scripts/amd_dashboard.py --port 8051
"""

import json, os, sys, time as time_mod, argparse, urllib.request
from collections import deque
from datetime import datetime, timezone

import dash
from dash import dcc, html, Input, Output, State, callback_context
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ── Fetch Binance ──────────────────────────────────────────────────────────────
BINANCE = "https://fapi.binance.com"

def fetch_klines(symbol, start_ms, end_ms):
    all_raw = []
    cur = start_ms; limit = 1500
    print(f"[fetch] {symbol} M1 desde {ms_to_str(start_ms)} hasta {ms_to_str(end_ms)}...")
    while cur < end_ms:
        url = (f"{BINANCE}/fapi/v1/klines?symbol={symbol}&interval=1m"
               f"&startTime={cur}&endTime={end_ms}&limit={limit}")
        req = urllib.request.Request(url, headers={"User-Agent": "amd-dash/1"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            page = json.loads(resp.read().decode())
        if not page: break
        all_raw.extend(page)
        cur = int(page[-1][0]) + 60_000
        if len(page) < limit: break
        time_mod.sleep(0.08)
    bars = []
    for k in all_raw:
        v = float(k[5]); tb = float(k[9])
        bars.append({
            "ts_ms": int(k[0]),
            "open":  float(k[1]),
            "high":  float(k[2]),
            "low":   float(k[3]),
            "close": float(k[4]),
            "volume": v,
            "bar_delta": 2.0 * tb - v,
        })
    print(f"[fetch] {len(bars)} barras ok")
    return bars

def ms_to_str(ms, fmt="%Y-%m-%d %H:%M"):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(fmt)

def ms_to_dt(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)

def classify_session(ts_ms):
    h = (ts_ms // 3_600_000) % 24
    if   8  <= h < 13: return "London"
    elif 13 <= h < 17: return "LondonNyOverlap"
    elif 17 <= h < 22: return "NewYork"
    else:              return "Asia"

# ── Detector AMD ───────────────────────────────────────────────────────────────
CFG = {
    "accum_range_min_pct": 0.06,
    "accum_range_max_pct": 0.45,
    "accum_min_bars":      15,
    "accum_max_bars":      50,
    "manip_min_vr":        2.0,
    "dist_min_vr":         1.5,
    "dist_cvd_slope":      10.0,
    "stop_buffer_pct":     0.08,
    "min_rr":              2.0,
    "cooldown_bars":       45,
    "max_wait_bars_after_spike": 10,
    "max_hold_bars":       120,
    "warmup_bars":         60,
    "cvd_slope_win":       20,
    "vr_window":           50,
}

class AmdDetector:
    def __init__(self, use_slope=False, require_diverge=True):
        self.use_slope       = use_slope
        self.require_diverge = require_diverge
        self._rst()
        self.vol_hist = deque(maxlen=CFG["vr_window"] + 5)
        self.hist     = deque(maxlen=CFG["accum_max_bars"] + 10)
        self.cvd_run  = 0.0
        self.cvd_acc  = deque(maxlen=CFG["cvd_slope_win"] + 5)
        self.bars_seen = 0
        self.last_sig  = 0

    def _rst(self):
        self.phase = "IDLE"
        self.rh = self.rl = None; self.cvd_sum = 0.0; self.ab = 0
        self.spike_ext = self.spike_dir = None
        self.vr_sp = self.dlt_sp = None
        self.m_rh = self.m_rl = self.m_ab = self.m_cvd = None
        self.bss = 0; self.acc_start = None; self.spike_idx = None

    def _vr(self, v):
        if len(self.vol_hist) < 5: return 1.0
        m = sum(self.vol_hist) / len(self.vol_hist)
        return v / m if m > 0 else 1.0

    def _slope(self):
        n = len(self.cvd_acc)
        if n < 5: return None
        xs = list(range(n)); ys = list(self.cvd_acc)
        mx = sum(xs)/n; my = sum(ys)/n
        num = sum((x-mx)*(y-my) for x,y in zip(xs,ys))
        den = sum((x-mx)**2 for x in xs)
        return num/den if abs(den) > 1e-10 else None

    def on_bar(self, row, idx):
        h=row["high"]; l=row["low"]; c=row["close"]; v=row["volume"]; d=row["bar_delta"]
        self.bars_seen += 1
        self.vol_hist.append(v)
        self.hist.append({"high":h,"low":l,"close":c,"delta":d,"idx":idx})
        self.cvd_run += d; self.cvd_acc.append(self.cvd_run)
        if self.bars_seen < 60 or self.bars_seen - self.last_sig < 45: return None
        vr = self._vr(v); slope = self._slope()

        if self.phase == "IDLE":
            if len(self.hist) >= 15:
                w = list(self.hist)[-15:]
                _rh = max(b["high"] for b in w); _rl = min(b["low"] for b in w)
                pct = (_rh - _rl) / c * 100
                if CFG["accum_range_min_pct"] <= pct <= CFG["accum_range_max_pct"]:
                    self.phase = "ACC"; self.rh = _rh; self.rl = _rl; self.ab = 15
                    self.cvd_sum = sum(b["delta"] for b in w)
                    self.acc_start = w[0]["idx"]
            return None

        if self.phase == "ACC":
            if c > self.rl and c < self.rh:
                new_rh = max(self.rh, h); new_rl = min(self.rl, l)
                if (new_rh - new_rl) / c * 100 > 0.45: self._rst(); return None
                self.rh = new_rh; self.rl = new_rl; self.ab += 1; self.cvd_sum += d
                if self.ab > 50: self._rst()
                return None
            pct = (self.rh - self.rl) / c * 100
            if pct < 0.06 or pct > 0.45 or self.ab < 15: self._rst(); return None
            if vr < CFG["manip_min_vr"]: self._rst(); return None
            sd = "Up" if c > self.rh else "Down"
            se = h if sd == "Up" else l
            div = (sd == "Up" and d < 0) or (sd == "Down" and d > 0)
            if self.require_diverge and not div: self._rst(); return None
            self.phase = "MANIP"; self.spike_ext = se; self.spike_dir = sd
            self.vr_sp = vr; self.dlt_sp = d
            self.m_rh = self.rh; self.m_rl = self.rl; self.m_ab = self.ab; self.m_cvd = self.cvd_sum
            self.bss = 0; self.spike_idx = idx
            return None

        if self.phase == "MANIP":
            if self.bss >= 10: self._rst(); return None
            self.bss += 1
            dd = "Short" if self.spike_dir == "Up" else "Long"
            cr = (dd == "Short" and c < self.m_rh) or (dd == "Long" and c > self.m_rl)
            if not cr: return None
            if vr < CFG["dist_min_vr"]: return None
            if self.use_slope:
                if slope is None: return None
                ok = (dd == "Short" and slope < -CFG["dist_cvd_slope"]) or \
                     (dd == "Long"  and slope >  CFG["dist_cvd_slope"])
                if not ok: return None
            entry = c; buf = CFG["stop_buffer_pct"] / 100
            stop   = self.spike_ext * (1 + buf) if dd == "Short" else self.spike_ext * (1 - buf)
            risk   = abs(stop - entry)
            if risk < 1: return None
            target = entry - risk * 2 if dd == "Short" else entry + risk * 2
            rr     = abs(target - entry) / risk
            if rr < CFG["min_rr"]: return None
            sig = {
                "direction": dd, "entry_price": entry, "stop_price": stop,
                "target_price": target, "rr": round(rr, 3),
                "range_high": self.m_rh, "range_low": self.m_rl,
                "range_pct": round((self.m_rh - self.m_rl) / entry * 100, 4),
                "range_bars": self.m_ab, "cvd_in_range": round(self.m_cvd, 1),
                "spike_extreme": self.spike_ext, "spike_direction": self.spike_dir,
                "vr_at_spike": round(self.vr_sp, 3), "bar_delta_at_spike": round(self.dlt_sp, 1),
                "vr_at_entry": round(vr, 3), "cvd_slope_at_entry": round(slope, 2) if slope else None,
                "session": classify_session(row["ts_ms"]),
                "timestamp_ms": row["ts_ms"], "idx": idx,
                "accum_start_idx": self.acc_start, "spike_idx": self.spike_idx,
                "exit": None, "result_r": None, "exit_idx": None,
            }
            self.last_sig = self.bars_seen; self._rst()
            return sig
        return None

def simulate(signals, bars):
    for sig in signals:
        i0 = sig["idx"]
        for k in range(1, CFG["max_hold_bars"] + 1):
            if i0 + k >= len(bars): break
            b = bars[i0 + k]; h = b["high"]; l = b["low"]
            if sig["direction"] == "Short":
                if l <= sig["target_price"]:
                    sig["exit"]="TARGET"; sig["result_r"]=sig["rr"]; sig["exit_idx"]=i0+k; break
                if h >= sig["stop_price"]:
                    sig["exit"]="STOP"; sig["result_r"]=-1.0; sig["exit_idx"]=i0+k; break
            else:
                if h >= sig["target_price"]:
                    sig["exit"]="TARGET"; sig["result_r"]=sig["rr"]; sig["exit_idx"]=i0+k; break
                if l <= sig["stop_price"]:
                    sig["exit"]="STOP"; sig["result_r"]=-1.0; sig["exit_idx"]=i0+k; break
        else:
            sig["exit"] = "OPEN"; sig["exit_idx"] = min(i0 + CFG["max_hold_bars"], len(bars) - 1)
    return signals

# ── Construccion del grafico Plotly ────────────────────────────────────────────

COLORS = {
    "bg":      "#0d1117",
    "panel":   "#161b22",
    "grid":    "#21262d",
    "text":    "#e6edf3",
    "bull":    "#3fb950",
    "bear":    "#f85149",
    "accum":   "#388bfd",
    "spike":   "#f0883e",
    "entry":   "#bc8cff",
    "cvd":     "#58a6ff",
    "vr":      "#d2a8ff",
    "target":  "#3fb950",
    "stop":    "#f85149",
    "open":    "#8b949e",
}

def build_figure(bars, sig):
    i_entry  = sig["idx"]
    i_accum  = max(sig["accum_start_idx"] - 25, 0)
    i_exit   = sig["exit_idx"] if sig["exit_idx"] else min(i_entry + 60, len(bars) - 1)
    i_end    = min(i_exit + 15, len(bars) - 1)
    window   = bars[i_accum:i_end + 1]
    off      = i_accum

    # CVD acumulado en la ventana
    cvd_base = sum(b["bar_delta"] for b in bars[:i_accum])
    cvd_vals = []
    acc = cvd_base
    for b in window:
        acc += b["bar_delta"]
        cvd_vals.append(acc)

    # VR rolling 50 barras
    vr_vals = []
    for j in range(len(window)):
        gi = i_accum + j
        sl = [bars[k]["volume"] for k in range(max(0, gi - 50), gi)]
        mv = sum(sl) / len(sl) if sl else 1.0
        vr_vals.append(window[j]["volume"] / mv if mv > 0 else 1.0)

    dts = [ms_to_dt(b["ts_ms"]) for b in window]

    # Colores de velas por fase
    def candle_color(j):
        gi = i_accum + j
        if gi == sig["spike_idx"]:  return COLORS["spike"]
        if gi == i_entry:           return COLORS["entry"]
        return COLORS["bull"] if window[j]["close"] >= window[j]["open"] else COLORS["bear"]

    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.62, 0.19, 0.19],
        vertical_spacing=0.02,
        subplot_titles=("", "CVD acumulado", "Volumen relativo (VR)"),
    )

    # ── Velas ─────────────────────────────────────────────────────────────────
    # Agrupamos por fase para poder colorear
    def phase_of(j):
        gi = i_accum + j
        if gi < sig["accum_start_idx"]: return "pre"
        if gi == sig["spike_idx"]:      return "spike"
        if gi <= i_entry:               return "accum" if gi < sig["spike_idx"] else "wait"
        if sig["exit_idx"] and gi == sig["exit_idx"]: return "exit"
        return "after"

    # Velas normales (candelabro unico para todo para que plotly maneje el eje X)
    open_v  = [b["open"]  for b in window]
    high_v  = [b["high"]  for b in window]
    low_v   = [b["low"]   for b in window]
    close_v = [b["close"] for b in window]

    inc_clr = [candle_color(j) for j in range(len(window))]
    dec_clr = inc_clr  # mismo color por fase

    fig.add_trace(go.Candlestick(
        x=dts,
        open=open_v, high=high_v, low=low_v, close=close_v,
        increasing=dict(line=dict(color=COLORS["bull"], width=1),
                        fillcolor=COLORS["bull"]),
        decreasing=dict(line=dict(color=COLORS["bear"], width=1),
                        fillcolor=COLORS["bear"]),
        name="OHLC",
        showlegend=False,
        hovertext=[
            f"<b>{ms_to_str(window[j]['ts_ms'],'%H:%M')}</b><br>"
            f"O:{window[j]['open']:.2f}  H:{window[j]['high']:.2f}<br>"
            f"L:{window[j]['low']:.2f}  C:{window[j]['close']:.2f}<br>"
            f"Vol:{window[j]['volume']:.1f}  Δ:{window[j]['bar_delta']:+.1f}<br>"
            f"VR:{vr_vals[j]:.2f}x"
            for j in range(len(window))
        ],
        hoverinfo="text",
    ), row=1, col=1)

    # Resaltar barra del spike (overlay rectangle)
    if sig["spike_idx"] >= i_accum and sig["spike_idx"] <= i_end:
        j_sp = sig["spike_idx"] - off
        fig.add_vrect(
            x0=dts[j_sp - 1] if j_sp > 0 else dts[0],
            x1=dts[min(j_sp + 1, len(dts) - 1)],
            fillcolor=COLORS["spike"], opacity=0.25, layer="below",
            row=1, col=1,
        )
        fig.add_annotation(
            x=dts[j_sp], y=window[j_sp]["high"],
            text=f"SPIKE {sig['spike_direction']}<br>VR={sig['vr_at_spike']:.2f}x",
            showarrow=True, arrowhead=2, arrowcolor=COLORS["spike"],
            font=dict(color=COLORS["spike"], size=10),
            bgcolor=COLORS["panel"], bordercolor=COLORS["spike"],
            row=1, col=1,
        )

    # Resaltar barra de entry
    j_en = i_entry - off
    if 0 <= j_en < len(dts):
        fig.add_vrect(
            x0=dts[max(j_en - 1, 0)],
            x1=dts[min(j_en + 1, len(dts) - 1)],
            fillcolor=COLORS["entry"], opacity=0.2, layer="below",
            row=1, col=1,
        )
        fig.add_annotation(
            x=dts[j_en], y=window[j_en]["low"],
            text=f"ENTRY<br>{sig['entry_price']:.2f}",
            showarrow=True, arrowhead=2, arrowcolor=COLORS["entry"],
            font=dict(color=COLORS["entry"], size=10), ay=30,
            bgcolor=COLORS["panel"], bordercolor=COLORS["entry"],
            row=1, col=1,
        )

    # ── Zona de acumulacion ───────────────────────────────────────────────────
    j_acc = sig["accum_start_idx"] - off
    j_spk = sig["spike_idx"] - off
    if 0 <= j_acc < len(dts) and 0 <= j_spk < len(dts):
        fig.add_hrect(
            y0=sig["range_low"], y1=sig["range_high"],
            x0=dts[j_acc], x1=dts[j_spk],
            fillcolor=COLORS["accum"], opacity=0.12, layer="below",
            row=1, col=1,
        )

    # Lineas del rango
    fig.add_hline(y=sig["range_high"], line=dict(color=COLORS["accum"], width=1, dash="dot"),
                  row=1, col=1, annotation_text=f"RH {sig['range_high']:.2f}",
                  annotation_font_color=COLORS["accum"])
    fig.add_hline(y=sig["range_low"],  line=dict(color=COLORS["accum"], width=1, dash="dot"),
                  row=1, col=1, annotation_text=f"RL {sig['range_low']:.2f}",
                  annotation_font_color=COLORS["accum"])

    # Spike extreme
    fig.add_hline(y=sig["spike_extreme"],
                  line=dict(color=COLORS["spike"], width=1, dash="dashdot"),
                  row=1, col=1, annotation_text=f"Spike extreme {sig['spike_extreme']:.2f}",
                  annotation_font_color=COLORS["spike"])

    # Stop y Target (solo desde entry en adelante)
    if j_en >= 0 and j_en < len(dts):
        t_entry = dts[j_en]
        t_end   = dts[-1]
        fig.add_shape(type="line", x0=t_entry, x1=t_end,
                      y0=sig["stop_price"],   y1=sig["stop_price"],
                      line=dict(color=COLORS["stop"],   width=1.5, dash="dash"), row=1, col=1)
        fig.add_shape(type="line", x0=t_entry, x1=t_end,
                      y0=sig["target_price"], y1=sig["target_price"],
                      line=dict(color=COLORS["target"], width=1.5, dash="dash"), row=1, col=1)
        # Labels stop/target
        fig.add_annotation(x=t_end, y=sig["stop_price"],
                           text=f"STOP {sig['stop_price']:.2f}", xanchor="right",
                           font=dict(color=COLORS["stop"], size=9),
                           bgcolor=COLORS["panel"], showarrow=False, row=1, col=1)
        fig.add_annotation(x=t_end, y=sig["target_price"],
                           text=f"TARGET {sig['target_price']:.2f}", xanchor="right",
                           font=dict(color=COLORS["target"], size=9),
                           bgcolor=COLORS["panel"], showarrow=False, row=1, col=1)

    # Marker de exit
    if sig["exit_idx"] and sig["exit_idx"] <= i_end:
        j_ex = sig["exit_idx"] - off
        if 0 <= j_ex < len(dts):
            exit_price = sig["target_price"] if sig["exit"] == "TARGET" else sig["stop_price"]
            exit_clr   = COLORS["target"] if sig["exit"] == "TARGET" else \
                         (COLORS["stop"] if sig["exit"] == "STOP" else COLORS["open"])
            exit_sym   = "star" if sig["exit"] == "TARGET" else \
                         ("x" if sig["exit"] == "STOP" else "circle")
            fig.add_trace(go.Scatter(
                x=[dts[j_ex]], y=[exit_price],
                mode="markers+text",
                marker=dict(symbol=exit_sym, size=14, color=exit_clr,
                            line=dict(color="white", width=1)),
                text=[f"{sig['exit']} {sig['result_r']:+.2f}R" if sig["result_r"] else sig["exit"]],
                textposition="top center",
                textfont=dict(color=exit_clr, size=11),
                name=sig["exit"], showlegend=False,
                hoverinfo="skip",
            ), row=1, col=1)

    # ── Panel CVD ─────────────────────────────────────────────────────────────
    fig.add_trace(go.Scatter(
        x=dts, y=cvd_vals,
        mode="lines", line=dict(color=COLORS["cvd"], width=1.5),
        fill="tozeroy", fillcolor=f"rgba(88,166,255,0.12)",
        name="CVD", showlegend=False,
        hovertemplate="CVD: %{y:.0f}<extra></extra>",
    ), row=2, col=1)
    # Linea vertical en spike y entry en CVD
    if 0 <= j_spk < len(dts):
        fig.add_vline(x=dts[j_spk], line=dict(color=COLORS["spike"], width=1, dash="dot"), row=2, col=1)
    if 0 <= j_en < len(dts):
        fig.add_vline(x=dts[j_en], line=dict(color=COLORS["entry"], width=1, dash="dot"), row=2, col=1)
    fig.add_hline(y=0, line=dict(color=COLORS["grid"], width=0.8), row=2, col=1)

    # ── Panel VR ──────────────────────────────────────────────────────────────
    vr_clrs = []
    for j in range(len(window)):
        gi = i_accum + j
        if gi == sig["spike_idx"]: vr_clrs.append(COLORS["spike"])
        elif gi == i_entry:        vr_clrs.append(COLORS["entry"])
        else:                      vr_clrs.append(COLORS["vr"])

    fig.add_trace(go.Bar(
        x=dts, y=vr_vals,
        marker_color=vr_clrs, opacity=0.8,
        name="VR", showlegend=False,
        hovertemplate="VR: %{y:.2f}x<extra></extra>",
    ), row=3, col=1)
    fig.add_hline(y=2.0, line=dict(color=COLORS["spike"], width=0.8, dash="dot"), row=3, col=1)
    fig.add_hline(y=1.5, line=dict(color=COLORS["entry"], width=0.8, dash="dot"), row=3, col=1)
    if 0 <= j_spk < len(dts):
        fig.add_vline(x=dts[j_spk], line=dict(color=COLORS["spike"], width=1, dash="dot"), row=3, col=1)
    if 0 <= j_en < len(dts):
        fig.add_vline(x=dts[j_en], line=dict(color=COLORS["entry"], width=1, dash="dot"), row=3, col=1)

    # ── Tema oscuro ───────────────────────────────────────────────────────────
    fig.update_layout(
        paper_bgcolor=COLORS["bg"],
        plot_bgcolor=COLORS["panel"],
        font=dict(color=COLORS["text"], size=11),
        xaxis_rangeslider_visible=False,
        xaxis3_rangeslider_visible=False,
        margin=dict(l=60, r=40, t=10, b=40),
        height=680,
        hovermode="x unified",
    )
    for i in range(1, 4):
        fig.update_xaxes(
            gridcolor=COLORS["grid"], showgrid=True,
            showline=False, zeroline=False,
            row=i, col=1,
        )
        fig.update_yaxes(
            gridcolor=COLORS["grid"], showgrid=True,
            showline=False, zeroline=False,
            row=i, col=1,
        )

    return fig

# ── Dash App ───────────────────────────────────────────────────────────────────

def build_app(signals, bars):
    app = dash.Dash(__name__, title="AMD Dashboard")

    sessions = sorted(set(s["session"] for s in signals))
    dirs     = sorted(set(s["direction"] for s in signals))
    exits    = sorted(set(s["exit"] for s in signals if s["exit"]))

    def sig_label(s, i, total):
        r = f"{s['result_r']:+.2f}R" if s["result_r"] else s["exit"]
        return (f"[{i+1}/{total}]  {s['direction']:5}  {s['session']:15}  "
                f"{ms_to_str(s['timestamp_ms'],'%m-%d %H:%M')}  |  {r}")

    def stats_bar(sigs):
        closed = [s for s in sigs if s["exit"] in ("TARGET", "STOP")]
        if not closed: return "Sin senales cerradas"
        n = len(closed)
        wins = sum(1 for s in closed if s["result_r"] > 0)
        avg  = sum(s["result_r"] for s in closed) / n
        return (f"n={n}  |  WR {wins/n*100:.1f}%  |  avgR {avg:+.3f}  "
                f"|  TARGET {sum(1 for s in sigs if s['exit']=='TARGET')}  "
                f"STOP {sum(1 for s in sigs if s['exit']=='STOP')}  "
                f"OPEN {sum(1 for s in sigs if s['exit']=='OPEN')}")

    btn_style = {
        "background": "#21262d", "color": "#e6edf3", "border": "1px solid #30363d",
        "borderRadius": "6px", "padding": "8px 20px", "cursor": "pointer",
        "fontSize": "14px", "margin": "0 4px",
    }
    filter_style = {
        "background": "#21262d", "color": "#e6edf3", "border": "1px solid #30363d",
        "borderRadius": "6px", "padding": "6px", "fontSize": "13px",
        "minWidth": "150px",
    }

    app.layout = html.Div(style={"background": "#0d1117", "minHeight": "100vh",
                                  "padding": "16px", "fontFamily": "monospace"}, children=[

        # Header
        html.Div(style={"display": "flex", "alignItems": "center",
                         "justifyContent": "space-between", "marginBottom": "12px"}, children=[
            html.H2("AMD Dashboard — Accumulation · Manipulation · Distribution",
                    style={"color": "#e6edf3", "margin": 0, "fontSize": "16px"}),
            html.Div(id="stats-bar",
                     style={"color": "#8b949e", "fontSize": "12px"}),
        ]),

        # Filtros
        html.Div(style={"display": "flex", "gap": "12px", "marginBottom": "12px",
                         "alignItems": "center", "flexWrap": "wrap"}, children=[
            html.Span("Sesion:", style={"color": "#8b949e", "fontSize": "13px"}),
            dcc.Dropdown(
                id="filter-session",
                options=[{"label": "Todas", "value": "ALL"}] +
                        [{"label": s, "value": s} for s in sessions],
                value="ALL", clearable=False,
                style={**filter_style, "background": "#21262d"},
                className="dash-dropdown",
            ),
            html.Span("Direccion:", style={"color": "#8b949e", "fontSize": "13px"}),
            dcc.Dropdown(
                id="filter-dir",
                options=[{"label": "Ambas", "value": "ALL"}] +
                        [{"label": d, "value": d} for d in dirs],
                value="ALL", clearable=False,
                style={**filter_style, "background": "#21262d"},
            ),
            html.Span("Resultado:", style={"color": "#8b949e", "fontSize": "13px"}),
            dcc.Dropdown(
                id="filter-exit",
                options=[{"label": "Todos", "value": "ALL"}] +
                        [{"label": e, "value": e} for e in exits],
                value="ALL", clearable=False,
                style={**filter_style, "background": "#21262d"},
            ),
        ]),

        # Navegacion
        html.Div(style={"display": "flex", "alignItems": "center", "gap": "8px",
                         "marginBottom": "8px"}, children=[
            html.Button("◀◀ Primera", id="btn-first", n_clicks=0, style=btn_style),
            html.Button("◀ Anterior", id="btn-prev",  n_clicks=0, style=btn_style),
            html.Button("Siguiente ▶", id="btn-next", n_clicks=0, style=btn_style),
            html.Button("Ultima ▶▶",   id="btn-last", n_clicks=0, style=btn_style),
            html.Div(id="nav-label",
                     style={"color": "#58a6ff", "fontSize": "13px",
                             "background": "#161b22", "padding": "6px 12px",
                             "borderRadius": "6px", "border": "1px solid #21262d",
                             "flex": "1"}),
        ]),

        # Grafico principal
        dcc.Graph(id="main-chart", config={
            "displayModeBar": True,
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
            "scrollZoom": True,
        }),

        # Panel de metricas
        html.Div(style={"display": "flex", "gap": "12px", "marginTop": "8px",
                         "flexWrap": "wrap"}, children=[

            html.Div(id="metrics-box", style={
                "background": "#161b22", "border": "1px solid #21262d",
                "borderRadius": "8px", "padding": "14px", "flex": "1",
                "color": "#e6edf3", "fontSize": "12px", "minWidth": "280px",
            }),

            html.Div(id="interpret-box", style={
                "background": "#161b22", "border": "1px solid #21262d",
                "borderRadius": "8px", "padding": "14px", "flex": "1",
                "color": "#e6edf3", "fontSize": "12px", "minWidth": "280px",
            }),

            html.Div(id="suggestion-box", style={
                "background": "#161b22", "border": "1px solid #0d419d",
                "borderRadius": "8px", "padding": "14px", "flex": "1",
                "color": "#e6edf3", "fontSize": "12px", "minWidth": "280px",
            }),
        ]),

        # State oculto para indice actual
        dcc.Store(id="current-idx", data=0),
    ])

    @app.callback(
        Output("current-idx", "data"),
        Input("btn-first", "n_clicks"),
        Input("btn-prev",  "n_clicks"),
        Input("btn-next",  "n_clicks"),
        Input("btn-last",  "n_clicks"),
        Input("filter-session", "value"),
        Input("filter-dir",     "value"),
        Input("filter-exit",    "value"),
        State("current-idx", "data"),
    )
    def update_idx(n_first, n_prev, n_next, n_last, f_ses, f_dir, f_exit, cur):
        filtered = _filter(signals, f_ses, f_dir, f_exit)
        n = len(filtered)
        if n == 0: return 0
        ctx = callback_context
        if not ctx.triggered: return min(cur, n - 1)
        trig = ctx.triggered[0]["prop_id"].split(".")[0]
        if trig in ("filter-session", "filter-dir", "filter-exit"): return 0
        if trig == "btn-first": return 0
        if trig == "btn-last":  return n - 1
        if trig == "btn-prev":  return max(0, cur - 1)
        if trig == "btn-next":  return min(n - 1, cur + 1)
        return cur

    @app.callback(
        Output("main-chart",    "figure"),
        Output("nav-label",     "children"),
        Output("metrics-box",   "children"),
        Output("interpret-box", "children"),
        Output("suggestion-box","children"),
        Output("stats-bar",     "children"),
        Input("current-idx", "data"),
        Input("filter-session", "value"),
        Input("filter-dir",     "value"),
        Input("filter-exit",    "value"),
    )
    def update_chart(idx, f_ses, f_dir, f_exit):
        filtered = _filter(signals, f_ses, f_dir, f_exit)
        if not filtered:
            empty = go.Figure()
            empty.update_layout(paper_bgcolor="#0d1117", plot_bgcolor="#161b22",
                                 font_color="#e6edf3",
                                 annotations=[dict(text="Sin senales con estos filtros",
                                                   showarrow=False, font_size=16)])
            return empty, "Sin senales", "—", "—", "—", "0 senales"

        idx = min(idx, len(filtered) - 1)
        sig = filtered[idx]
        fig = build_figure(bars, sig)

        nav = sig_label(sig, idx, len(filtered))

        r_color = "#3fb950" if sig["exit"] == "TARGET" else \
                  ("#f85149" if sig["exit"] == "STOP" else "#8b949e")
        r_str   = (f"{sig['result_r']:+.2f}R" if sig["result_r"] is not None
                   else sig["exit"])

        metrics = html.Div([
            html.B("METRICAS DEL TRADE", style={"color": "#58a6ff"}), html.Br(),
            html.Br(),
            _row("Sesion",    sig["session"]),
            _row("Timestamp", ms_to_str(sig["timestamp_ms"], "%Y-%m-%d %H:%M UTC")),
            _row("Direccion", sig["direction"],
                 "#3fb950" if sig["direction"] == "Long" else "#f85149"),
            html.Br(),
            _row("Entry",    f"{sig['entry_price']:.2f}"),
            _row("Stop",     f"{sig['stop_price']:.2f}  "
                             f"({abs(sig['stop_price']-sig['entry_price']):.1f} pts)",
                 "#f85149"),
            _row("Target",   f"{sig['target_price']:.2f}  "
                             f"({abs(sig['target_price']-sig['entry_price']):.1f} pts)",
                 "#3fb950"),
            _row("RR",       f"{sig['rr']:.2f}:1"),
            html.Br(),
            _row("Rango",    f"{sig['range_pct']:.3f}%  |  {sig['range_bars']} barras"),
            _row("CVD rango",f"{sig['cvd_in_range']:+.0f}"),
            _row("Spike dir",f"{sig['spike_direction']}  VR={sig['vr_at_spike']:.2f}x"
                             f"  Δ={sig['bar_delta_at_spike']:+.0f}", "#f0883e"),
            _row("Entry VR", f"{sig['vr_at_entry']:.2f}x"),
            html.Br(),
            html.Span("Resultado: ", style={"color": "#8b949e"}),
            html.B(r_str, style={"color": r_color, "fontSize": "14px"}),
        ])

        interpret = _interpret_html(sig)
        suggest   = _suggest_html(sig, filtered)
        stats     = stats_bar(filtered)

        return fig, nav, metrics, interpret, suggest, stats

    return app


def _filter(signals, f_ses, f_dir, f_exit):
    out = signals
    if f_ses  != "ALL": out = [s for s in out if s["session"]   == f_ses]
    if f_dir  != "ALL": out = [s for s in out if s["direction"] == f_dir]
    if f_exit != "ALL": out = [s for s in out if s["exit"]      == f_exit]
    return out


def _row(label, value, color="#e6edf3"):
    return html.Div([
        html.Span(f"{label:<14}", style={"color": "#8b949e"}),
        html.Span(value, style={"color": color}),
    ], style={"marginBottom": "2px"})


def _interpret_html(sig):
    d = sig["direction"]; sd = sig["spike_direction"]
    delta = sig["bar_delta_at_spike"]
    vr_sp = sig["vr_at_spike"]
    r = sig["range_pct"]
    div = (sd == "Up" and delta < 0) or (sd == "Down" and delta > 0)

    items = []
    items.append(html.B("INTERPRETACION", style={"color": "#58a6ff"}))
    items.append(html.Br()); items.append(html.Br())

    # Patron
    if sd == "Up" and d == "Short":
        items.append(html.P([
            html.B("Patron: "), "Spike alcista FALSO → SHORT. ",
            "Institucionales barrieron stops SHORT sobre el rango y vendieron en la cima."
        ]))
    else:
        items.append(html.P([
            html.B("Patron: "), "Spike bajista FALSO → LONG. ",
            "Institucionales barrieron stops LONG bajo el rango y compraron en el piso."
        ]))

    # CVD divergencia
    if div:
        items.append(html.P([
            html.B("CVD diverge: ", style={"color": "#3fb950"}),
            f"delta={delta:+.0f}. El precio se movio en una direccion pero ",
            "los vendedores/compradores dominaron la barra. ",
            html.B("Firma institucional confirmada.", style={"color": "#3fb950"}),
        ]))
    else:
        items.append(html.P([
            html.B("CVD no diverge: ", style={"color": "#f85149"}),
            f"delta={delta:+.0f}. El flujo acompano el spike. ",
            "Mayor probabilidad de breakout real.",
        ]))

    # Calidad del rango
    if r < 0.15:
        rango_txt = f"Muy estrecho ({r:.3f}%) — mayor probabilidad de fakeout."
        rango_clr = "#3fb950"
    elif r < 0.30:
        rango_txt = f"Ideal ({r:.3f}%) — comprimido sin ser ruido."
        rango_clr = "#3fb950"
    else:
        rango_txt = f"Amplio ({r:.3f}%) — puede ser tendencia, no consolidacion."
        rango_clr = "#f0883e"
    items.append(html.P([html.B("Rango: "), html.Span(rango_txt, style={"color": rango_clr})]))

    # Spike VR
    if vr_sp >= 4.0:
        items.append(html.P([
            html.B("Spike VR: ", style={"color": "#3fb950"}),
            f"{vr_sp:.2f}x — Volumen institucional muy alto. Edge mayor.",
        ]))
    elif vr_sp >= 2.5:
        items.append(html.P([html.B("Spike VR: "), f"{vr_sp:.2f}x — Fuerte."]))
    else:
        items.append(html.P([
            html.B("Spike VR: ", style={"color": "#f0883e"}),
            f"{vr_sp:.2f}x — Minimo. Menor conviccion.",
        ]))

    return html.Div(items)


def _suggest_html(sig, filtered):
    d = sig["direction"]; ses = sig["session"]
    exit_v = sig["exit"]

    items = []
    items.append(html.B("SUGERENCIAS / ANALISIS", style={"color": "#d2a8ff"}))
    items.append(html.Br()); items.append(html.Br())

    # Sesion
    ses_stats = {}
    for s in filtered:
        cl = [x for x in filtered if x["session"] == s["session"] and x["exit"] in ("TARGET","STOP")]
        if s["session"] not in ses_stats and cl:
            n = len(cl); wins = sum(1 for x in cl if x["result_r"] > 0)
            ses_stats[s["session"]] = (wins/n*100, sum(x["result_r"] for x in cl)/n, n)

    if ses in ses_stats:
        wr, avgr, n = ses_stats[ses]
        color = "#3fb950" if wr >= 50 else "#f0883e" if wr >= 33 else "#f85149"
        items.append(html.P([
            html.B(f"Sesion {ses}: "),
            html.Span(f"WR={wr:.0f}% avgR={avgr:+.3f} n={n}", style={"color": color}),
            html.Br(),
            html.Span("Edge historico positivo en este filtro." if wr >= 50
                       else "Edge marginal — revisar contexto macro.", style={"fontSize": "11px"})
        ]))

    # Contexto de sesiones
    if ses == "London":
        items.append(html.P("London es la sesion con mejor edge AMD en el backtest 14d.",
                            style={"color": "#3fb950"}))
    elif ses == "Asia":
        items.append(html.P([
            "Asia tiene edge negativo en backtest 14d. ",
            html.B("Considerar filtrar esta sesion.", style={"color": "#f0883e"}),
        ]))
    elif ses == "NewYork":
        items.append(html.P([
            "NewYork tiene el peor edge AMD. ",
            html.B("Recomendado: filtrar.", style={"color": "#f85149"}),
        ]))

    # Direccion
    if d == "Long":
        items.append(html.P([
            "Longs AMD en contexto bajista (May-Jun 2026): ",
            html.B("0% WR. ", style={"color": "#f85149"}),
            "Filtrar Longs en Bear/TrendDown hasta tener mas datos.",
        ]))
    else:
        items.append(html.P([
            "Shorts AMD: mejor edge en el periodo. ",
            html.Span("(3/3 ganadores con CVD diverge + London)",
                      style={"color": "#3fb950", "fontSize": "11px"}),
        ]))

    # Resultado actual
    if exit_v == "TARGET":
        items.append(html.P(html.B("Este setup alcanzo TARGET — patron valido.",
                                    style={"color": "#3fb950"})))
    elif exit_v == "STOP":
        items.append(html.P(html.B("Stop alcanzado — revisar el CVD durante la acumulacion.",
                                    style={"color": "#f85149"})))
    else:
        items.append(html.P("Senal abierta — no resuelta dentro de 2h."))

    # Proximos pasos
    items.append(html.Hr(style={"borderColor": "#21262d"}))
    items.append(html.P([
        html.B("Proximos pasos: "),
        "Con 30+ senales cerradas homogeneas (btc_bars con microestructura), "
        "calibrar VPIN threshold y min_rr por sesion."
    ], style={"color": "#8b949e", "fontSize": "11px"}))

    return html.Div(items)


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol",      nargs="?", default="BTCUSDT")
    parser.add_argument("--days",      type=int,  default=14)
    parser.add_argument("--session",   default=None)
    parser.add_argument("--dir",       choices=["Short","Long"], default=None)
    parser.add_argument("--no-slope",  action="store_true")
    parser.add_argument("--no-diverge",action="store_true")
    parser.add_argument("--port",      type=int, default=8050)
    args = parser.parse_args()

    now_ms   = int(time_mod.time() * 1000)
    start_ms = now_ms - args.days * 86400 * 1000

    bars = fetch_klines(args.symbol, start_ms, now_ms)
    if not bars: sys.exit("Sin barras")

    det = AmdDetector(use_slope=not args.no_slope, require_diverge=not args.no_diverge)
    signals = []
    for i, row in enumerate(bars):
        sig = det.on_bar(row, i)
        if sig is None: continue
        if args.session and sig["session"] != args.session: continue
        if args.dir     and sig["direction"] != args.dir:   continue
        signals.append(sig)

    simulate(signals, bars)
    print(f"[detector] {len(signals)} senales | "
          f"{sum(1 for s in signals if s['exit']=='TARGET')} TARGET | "
          f"{sum(1 for s in signals if s['exit']=='STOP')} STOP | "
          f"{sum(1 for s in signals if s['exit']=='OPEN')} OPEN")

    if not signals:
        sys.exit("Sin senales con estos filtros. Prueba --no-slope o --no-diverge")

    app = build_app(signals, bars)
    print(f"\n[dash] http://localhost:{args.port}/")
    print("[dash] Ctrl+C para salir\n")

    import webbrowser; webbrowser.open(f"http://localhost:{args.port}/")
    app.run(debug=False, port=args.port, host="0.0.0.0")


if __name__ == "__main__":
    main()
