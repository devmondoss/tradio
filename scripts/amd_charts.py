#!/usr/bin/env python3
"""
AMD Charts — visualizacion de senales del detector Accumulation·Manipulation·Distribution.

Para cada senal genera un grafico M1 con:
  - Velas OHLC coloreadas por fase (acumulacion / spike / distribucion)
  - Rango de acumulacion (zona sombreada azul)
  - Spike extreme, entry, stop, target
  - Punto de salida (TARGET verde / STOP rojo / OPEN gris)
  - Panel CVD acumulado debajo
  - Panel VR debajo
  - Anotaciones con contexto e interpretacion

Uso:
    python scripts/amd_charts.py                     # 14 dias, no-slope, no-diverge
    python scripts/amd_charts.py --days 7 --no-slope # solo CVD diverge
    python scripts/amd_charts.py --session London    # solo London
    python scripts/amd_charts.py --dir Short         # solo Shorts
    python scripts/amd_charts.py --save              # guardar PNG en vez de mostrar
"""

import json
import os
import sys
import argparse
import time as time_mod
import urllib.request
from collections import deque
from datetime import datetime, timezone

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import numpy as np

# ── Colores ────────────────────────────────────────────────────────────────────
DARK_BG   = "#0d1117"
PANEL_BG  = "#161b22"
GRID_CLR  = "#21262d"
TEXT_CLR  = "#e6edf3"
BULL_CLR  = "#3fb950"   # vela alcista
BEAR_CLR  = "#f85149"   # vela bajista
ACCUM_CLR = "#388bfd"   # zona acumulacion
SPIKE_CLR = "#f0883e"   # barra del spike
ENTRY_CLR = "#bc8cff"   # barra de entry
CVD_CLR   = "#58a6ff"   # panel CVD
VR_CLR    = "#d2a8ff"   # panel VR

# ── Fetch Binance ──────────────────────────────────────────────────────────────
BINANCE = "https://fapi.binance.com"

def fetch_klines(symbol, start_ms, end_ms):
    all_raw = []
    cur = start_ms; limit = 1500
    while cur < end_ms:
        url = (f"{BINANCE}/fapi/v1/klines?symbol={symbol}&interval=1m"
               f"&startTime={cur}&endTime={end_ms}&limit={limit}")
        req = urllib.request.Request(url, headers={"User-Agent": "amd-charts/1"})
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
    return bars

def ms_to_str(ms, fmt="%m-%d %H:%M"):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(fmt)

def classify_session(ts_ms):
    h = (ts_ms // 3_600_000) % 24
    if   8  <= h < 13: return "London"
    elif 13 <= h < 17: return "LondonNyOverlap"
    elif 17 <= h < 22: return "NewYork"
    else:              return "Asia"

# ── Detector AMD (replica de amd_backtest.py) ─────────────────────────────────
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
        self.use_slope      = use_slope
        self.require_diverge= require_diverge
        self._reset()
        self.vol_hist  = deque(maxlen=CFG["vr_window"] + 5)
        self.hist      = deque(maxlen=CFG["accum_max_bars"] + 10)
        self.cvd_run   = 0.0
        self.cvd_acc   = deque(maxlen=CFG["cvd_slope_win"] + 5)
        self.bars_seen = 0
        self.last_sig  = 0
        # Para reconstruir el contexto de cada senal
        self.accum_start_idx = None

    def _reset(self):
        self.phase     = "IDLE"
        self.rh = self.rl = None
        self.cvd_sum   = 0.0
        self.ab        = 0
        self.spike_ext = self.spike_dir = None
        self.vr_sp = self.dlt_sp = None
        self.m_rh = self.m_rl = self.m_ab = self.m_cvd = None
        self.bss       = 0
        self.accum_start_idx = None

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
        self.vol_hist.append(v); self.hist.append({"high":h,"low":l,"close":c,"delta":d,"idx":idx})
        self.cvd_run += d; self.cvd_acc.append(self.cvd_run)

        if self.bars_seen < 60 or self.bars_seen - self.last_sig < 45: return None

        vr = self._vr(v); slope = self._slope()

        if self.phase == "IDLE":
            n = 15
            if len(self.hist) >= n:
                w = list(self.hist)[-n:]
                _rh=max(b["high"] for b in w); _rl=min(b["low"] for b in w)
                pct=(_rh-_rl)/c*100
                if CFG["accum_range_min_pct"] <= pct <= CFG["accum_range_max_pct"]:
                    self.phase="ACC"; self.rh=_rh; self.rl=_rl; self.ab=n
                    self.cvd_sum=sum(b["delta"] for b in w)
                    self.accum_start_idx = w[0]["idx"]
            return None

        if self.phase == "ACC":
            if c > self.rl and c < self.rh:
                new_rh=max(self.rh,h); new_rl=min(self.rl,l)
                if (new_rh-new_rl)/c*100 > 0.45: self._reset(); return None
                self.rh=new_rh; self.rl=new_rl; self.ab+=1; self.cvd_sum+=d
                if self.ab > 50: self._reset()
                return None
            pct=(self.rh-self.rl)/c*100
            if pct<0.06 or pct>0.45 or self.ab<15: self._reset(); return None
            if vr < CFG["manip_min_vr"]: self._reset(); return None
            sd = "Up" if c > self.rh else "Down"
            se = h if sd=="Up" else l
            div = (sd=="Up" and d<0) or (sd=="Down" and d>0)
            if self.require_diverge and not div: self._reset(); return None
            self.phase="MANIP"; self.spike_ext=se; self.spike_dir=sd
            self.vr_sp=vr; self.dlt_sp=d
            self.m_rh=self.rh; self.m_rl=self.rl; self.m_ab=self.ab; self.m_cvd=self.cvd_sum
            self.bss=0; self.spike_idx=idx
            return None

        if self.phase == "MANIP":
            if self.bss >= 10: self._reset(); return None
            self.bss += 1
            dd = "Short" if self.spike_dir=="Up" else "Long"
            cr = (dd=="Short" and c<self.m_rh) or (dd=="Long" and c>self.m_rl)
            if not cr: return None
            if vr < CFG["dist_min_vr"]: return None
            if self.use_slope:
                if slope is None: return None
                ok=(dd=="Short" and slope<-CFG["dist_cvd_slope"]) or (dd=="Long" and slope>CFG["dist_cvd_slope"])
                if not ok: return None
            entry=c; buf=CFG["stop_buffer_pct"]/100
            stop = self.spike_ext*(1+buf) if dd=="Short" else self.spike_ext*(1-buf)
            risk = abs(stop-entry)
            if risk < 1: return None
            target = entry-risk*2 if dd=="Short" else entry+risk*2
            rr = abs(target-entry)/risk
            if rr < CFG["min_rr"]: return None
            pct=(self.m_rh-self.m_rl)/entry*100
            sig = {
                "direction": dd, "entry_price": entry, "stop_price": stop,
                "target_price": target, "rr": rr,
                "range_high": self.m_rh, "range_low": self.m_rl,
                "range_pct": pct, "range_bars": self.m_ab, "cvd_in_range": self.m_cvd,
                "spike_extreme": self.spike_ext, "spike_direction": self.spike_dir,
                "vr_at_spike": self.vr_sp, "bar_delta_at_spike": self.dlt_sp,
                "vr_at_entry": vr, "cvd_slope_at_entry": slope,
                "session": classify_session(row["ts_ms"]),
                "timestamp_ms": row["ts_ms"], "idx": idx,
                "accum_start_idx": self.accum_start_idx,
                "spike_idx": self.spike_idx,
                "exit": None, "result_r": None, "exit_idx": None,
            }
            self.last_sig = self.bars_seen; self._reset()
            return sig
        return None

def simulate(signals, bars):
    for sig in signals:
        i0 = sig["idx"]
        for k in range(1, CFG["max_hold_bars"]+1):
            if i0+k >= len(bars): break
            b = bars[i0+k]; h=b["high"]; l=b["low"]
            if sig["direction"]=="Short":
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
            sig["exit"]="OPEN"; sig["exit_idx"]=min(i0+CFG["max_hold_bars"], len(bars)-1)
    return signals

# ── Graficos ───────────────────────────────────────────────────────────────────

def draw_candle(ax, x, o, h, l, c, width=0.6, is_spike=False, is_entry=False):
    bull = c >= o
    if is_spike:   body_clr = SPIKE_CLR; wick_clr = SPIKE_CLR
    elif is_entry: body_clr = ENTRY_CLR; wick_clr = ENTRY_CLR
    else:          body_clr = BULL_CLR if bull else BEAR_CLR; wick_clr = body_clr

    # Wick
    ax.plot([x, x], [l, h], color=wick_clr, linewidth=0.8, zorder=2)
    # Body
    y0 = min(o, c); h_body = max(abs(c - o), 0.01 * (h - l))
    rect = plt.Rectangle((x - width/2, y0), width, h_body,
                          color=body_clr, alpha=0.9, zorder=3)
    ax.add_patch(rect)

def plot_signal(bars, sig, sig_num, total, save_dir=None):
    # Ventana de visualizacion
    i_entry = sig["idx"]
    i_accum = max(sig["accum_start_idx"] - 20, 0)
    i_exit  = sig["exit_idx"] if sig["exit_idx"] is not None else min(i_entry+60, len(bars)-1)
    i_end   = min(i_exit + 10, len(bars)-1)
    window  = bars[i_accum:i_end+1]
    xs      = list(range(len(window)))
    off     = i_accum

    # CVD running acumulado en la ventana
    cvd_start = 0.0
    for b in bars[:i_accum]:
        cvd_start += b["bar_delta"]
    cvd_vals = [cvd_start]
    for b in window[1:]:
        cvd_vals.append(cvd_vals[-1] + b["bar_delta"])

    # VR (50-bar rolling)
    vr_vals = []
    for j, b in enumerate(window):
        gi = i_accum + j
        vol_slice = [bars[k]["volume"] for k in range(max(0, gi-50), gi)]
        mv = sum(vol_slice)/len(vol_slice) if vol_slice else 1.0
        vr_vals.append(b["volume"]/mv if mv > 0 else 1.0)

    # Indices locales
    def local(gi): return gi - off

    l_accum = local(sig["accum_start_idx"])
    l_spike = local(sig["spike_idx"])
    l_entry = local(i_entry)
    l_exit  = local(i_exit)

    # ── Layout ────────────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(18, 12), facecolor=DARK_BG)
    gs  = gridspec.GridSpec(3, 1, height_ratios=[5, 1.5, 1.5], hspace=0.06)
    ax_price = fig.add_subplot(gs[0])
    ax_cvd   = fig.add_subplot(gs[1], sharex=ax_price)
    ax_vr    = fig.add_subplot(gs[2], sharex=ax_price)

    for ax in (ax_price, ax_cvd, ax_vr):
        ax.set_facecolor(PANEL_BG)
        ax.tick_params(colors=TEXT_CLR, labelsize=8)
        ax.spines[:].set_color(GRID_CLR)
        ax.grid(True, color=GRID_CLR, linewidth=0.4, alpha=0.6)
        ax.yaxis.label.set_color(TEXT_CLR)
        ax.xaxis.label.set_color(TEXT_CLR)

    plt.setp(ax_price.get_xticklabels(), visible=False)
    plt.setp(ax_cvd.get_xticklabels(), visible=False)

    # ── Velas ──────────────────────────────────────────────────────────────────
    for j, b in enumerate(window):
        is_spike = (j == l_spike)
        is_entry = (j == l_entry)
        draw_candle(ax_price, j, b["open"], b["high"], b["low"], b["close"],
                    is_spike=is_spike, is_entry=is_entry)

    # ── Zona de acumulacion ────────────────────────────────────────────────────
    rh = sig["range_high"]; rl = sig["range_low"]
    ax_price.axhspan(rl, rh, xmin=l_accum/len(xs), xmax=l_spike/len(xs),
                     color=ACCUM_CLR, alpha=0.12, zorder=1)
    ax_price.axhline(rh, color=ACCUM_CLR, linewidth=0.8, linestyle="--", alpha=0.6, zorder=1)
    ax_price.axhline(rl, color=ACCUM_CLR, linewidth=0.8, linestyle="--", alpha=0.6, zorder=1)
    ax_price.text(l_accum + 0.5, rh, f"RH {rh:.1f}", color=ACCUM_CLR,
                  fontsize=7, va="bottom", alpha=0.8)
    ax_price.text(l_accum + 0.5, rl, f"RL {rl:.1f}", color=ACCUM_CLR,
                  fontsize=7, va="top", alpha=0.8)

    # ── Spike extreme ──────────────────────────────────────────────────────────
    ax_price.axhline(sig["spike_extreme"], color=SPIKE_CLR,
                     linewidth=1.0, linestyle=":", alpha=0.8)
    ax_price.text(l_spike + 0.5, sig["spike_extreme"],
                  f"Spike {sig['spike_extreme']:.1f}", color=SPIKE_CLR, fontsize=7, va="center")

    # ── Stop / Target ──────────────────────────────────────────────────────────
    ax_price.axhline(sig["stop_price"],   color=BEAR_CLR, linewidth=1.2,
                     linestyle="--", alpha=0.9, xmin=l_entry/max(len(xs),1))
    ax_price.axhline(sig["target_price"], color=BULL_CLR, linewidth=1.2,
                     linestyle="--", alpha=0.9, xmin=l_entry/max(len(xs),1))

    # ── Entry marker ──────────────────────────────────────────────────────────
    ax_price.annotate("ENTRY", xy=(l_entry, sig["entry_price"]),
                      xytext=(l_entry + 1.5, sig["entry_price"]),
                      color=ENTRY_CLR, fontsize=8, fontweight="bold",
                      arrowprops=dict(arrowstyle="->", color=ENTRY_CLR, lw=1))

    # ── Exit marker ───────────────────────────────────────────────────────────
    if l_exit < len(window):
        b_exit = window[l_exit]
        exit_price = sig["target_price"] if sig["exit"]=="TARGET" else sig["stop_price"]
        exit_clr = BULL_CLR if sig["exit"]=="TARGET" else (BEAR_CLR if sig["exit"]=="STOP" else "#8b949e")
        ax_price.scatter(l_exit, exit_price, color=exit_clr, s=120, zorder=10,
                         marker="*" if sig["exit"]=="TARGET" else ("x" if sig["exit"]=="STOP" else "o"))
        ax_price.annotate(f"{sig['exit']}\n{sig['result_r']:+.2f}R" if sig["result_r"] else sig["exit"],
                          xy=(l_exit, exit_price),
                          xytext=(l_exit + 1, exit_price + (rh-rl)*0.5),
                          color=exit_clr, fontsize=8, fontweight="bold",
                          arrowprops=dict(arrowstyle="->", color=exit_clr, lw=0.8))

    # ── Labels eje X ──────────────────────────────────────────────────────────
    step = max(1, len(window) // 10)
    tick_pos = list(range(0, len(window), step))
    ax_vr.set_xticks(tick_pos)
    ax_vr.set_xticklabels([ms_to_str(window[j]["ts_ms"]) for j in tick_pos],
                           rotation=30, ha="right", fontsize=7, color=TEXT_CLR)

    # ── Panel CVD ─────────────────────────────────────────────────────────────
    ax_cvd.plot(xs, cvd_vals, color=CVD_CLR, linewidth=1.2, label="CVD")
    ax_cvd.fill_between(xs, 0, cvd_vals,
                         where=[v > 0 for v in cvd_vals], color=CVD_CLR, alpha=0.15)
    ax_cvd.fill_between(xs, 0, cvd_vals,
                         where=[v < 0 for v in cvd_vals], color=BEAR_CLR, alpha=0.15)
    ax_cvd.axvline(l_spike, color=SPIKE_CLR, linewidth=0.8, linestyle="--", alpha=0.7)
    ax_cvd.axvline(l_entry, color=ENTRY_CLR, linewidth=0.8, linestyle="--", alpha=0.7)
    ax_cvd.set_ylabel("CVD", color=TEXT_CLR, fontsize=8)
    ax_cvd.axhline(0, color=GRID_CLR, linewidth=0.6)

    # ── Panel VR ──────────────────────────────────────────────────────────────
    bar_clrs = [SPIKE_CLR if j==l_spike else (ENTRY_CLR if j==l_entry else VR_CLR)
                for j in range(len(window))]
    ax_vr.bar(xs, vr_vals, color=bar_clrs, width=0.7, alpha=0.8)
    ax_vr.axhline(2.0, color=SPIKE_CLR, linewidth=0.7, linestyle="--", alpha=0.6)
    ax_vr.axhline(1.5, color=ENTRY_CLR, linewidth=0.7, linestyle="--", alpha=0.5)
    ax_vr.axvline(l_spike, color=SPIKE_CLR, linewidth=0.8, linestyle="--", alpha=0.7)
    ax_vr.axvline(l_entry, color=ENTRY_CLR, linewidth=0.8, linestyle="--", alpha=0.7)
    ax_vr.set_ylabel("VR", color=TEXT_CLR, fontsize=8)
    ax_vr.set_ylim(0, max(5.0, max(vr_vals)*1.1))

    # ── Titulo y cabecera de info ──────────────────────────────────────────────
    dir_emoji = "▼ SHORT" if sig["direction"]=="Short" else "▲ LONG"
    result_str = (f"{sig['result_r']:+.2f}R {sig['exit']}"
                  if sig["result_r"] is not None else f"OPEN ({sig['exit']})")
    exit_clr = (BULL_CLR if sig["exit"]=="TARGET" else
                (BEAR_CLR if sig["exit"]=="STOP" else "#8b949e"))

    title = (f"AMD  {dir_emoji}  —  {sig['session']}  —  {ms_to_str(sig['timestamp_ms'], '%Y-%m-%d %H:%M UTC')}"
             f"  —  [{sig_num}/{total}]")
    fig.suptitle(title, color=TEXT_CLR, fontsize=13, fontweight="bold", y=0.98)

    # ── Caja de metricas ───────────────────────────────────────────────────────
    info_lines = [
        f"Entry    {sig['entry_price']:.2f}",
        f"Stop     {sig['stop_price']:.2f}  ({abs(sig['stop_price']-sig['entry_price']):.1f} pts)",
        f"Target   {sig['target_price']:.2f}  ({abs(sig['target_price']-sig['entry_price']):.1f} pts)",
        f"RR       {sig['rr']:.2f}:1",
        "",
        f"Range    {sig['range_pct']:.3f}%  |  {sig['range_bars']} barras",
        f"CVD rng  {sig['cvd_in_range']:+.0f}",
        f"Spike    {sig['spike_direction']}  VR={sig['vr_at_spike']:.2f}x  Δ={sig['bar_delta_at_spike']:+.0f}",
        f"Entry    VR={sig['vr_at_entry']:.2f}x",
        "",
        f"Resultado: {result_str}",
    ]
    info_text = "\n".join(info_lines)
    ax_price.text(0.01, 0.98, info_text, transform=ax_price.transAxes,
                  color=TEXT_CLR, fontsize=8, va="top", ha="left",
                  fontfamily="monospace",
                  bbox=dict(boxstyle="round,pad=0.4", facecolor=PANEL_BG,
                            edgecolor=GRID_CLR, alpha=0.85))

    # ── Interpretacion ─────────────────────────────────────────────────────────
    interp = _interpret(sig)
    ax_price.text(0.99, 0.98, interp, transform=ax_price.transAxes,
                  color=TEXT_CLR, fontsize=7.5, va="top", ha="right",
                  fontfamily="monospace",
                  bbox=dict(boxstyle="round,pad=0.4", facecolor=PANEL_BG,
                            edgecolor=GRID_CLR, alpha=0.85))

    # ── Leyenda de fases ───────────────────────────────────────────────────────
    legend_elems = [
        mpatches.Patch(color=ACCUM_CLR, alpha=0.5, label=f"Acumulacion ({sig['range_bars']} barras)"),
        mpatches.Patch(color=SPIKE_CLR, label=f"Spike {sig['spike_direction']} VR={sig['vr_at_spike']:.2f}x"),
        mpatches.Patch(color=ENTRY_CLR, label=f"Entry distribucion"),
        mpatches.Patch(color=BULL_CLR,  alpha=0.7, label=f"Target {sig['target_price']:.1f}"),
        mpatches.Patch(color=BEAR_CLR,  alpha=0.7, label=f"Stop {sig['stop_price']:.1f}"),
    ]
    ax_price.legend(handles=legend_elems, loc="lower left",
                    facecolor=PANEL_BG, edgecolor=GRID_CLR,
                    labelcolor=TEXT_CLR, fontsize=7.5)

    ax_price.set_xlim(-1, len(window))
    plt.tight_layout(rect=[0, 0, 1, 0.97])

    if save_dir:
        fname = os.path.join(save_dir,
                             f"amd_{sig_num:02d}_{sig['direction']}_{sig['session']}"
                             f"_{ms_to_str(sig['timestamp_ms'],'%m%d_%H%M')}.png")
        plt.savefig(fname, dpi=130, facecolor=DARK_BG, bbox_inches="tight")
        print(f"  [save] {fname}")
        plt.close()
    else:
        plt.show()
        plt.close()


def _interpret(sig):
    lines = []
    d = sig["direction"]; sd = sig["spike_direction"]
    r = sig["range_pct"]
    vr_sp = sig["vr_at_spike"]
    delta  = sig["bar_delta_at_spike"]

    # Tipo de patron
    if sd == "Up" and d == "Short":
        lines.append("PATRON: Spike alcista falso -> SHORT")
        lines.append("  Institucionales barrieron stops SHORT")
        lines.append("  sobre el rango, vendieron en la cima.")
    else:
        lines.append("PATRON: Spike bajista falso -> LONG")
        lines.append("  Institucionales barrieron stops LONG")
        lines.append("  bajo el rango, compraron en el piso.")

    # Calidad del rango
    if r < 0.15:
        lines.append(f"\nRANGO: Muy estrecho ({r:.3f}%)")
        lines.append("  Mayor probabilidad de fakeout.")
    elif r < 0.30:
        lines.append(f"\nRANGO: Comprimido ({r:.3f}%) - ideal")
    else:
        lines.append(f"\nRANGO: Amplio ({r:.3f}%)")
        lines.append("  Puede ser tendencia, no consolidacion.")

    # Calidad del spike
    if vr_sp >= 4.0:
        lines.append(f"\nSPIKE: Muy fuerte VR={vr_sp:.2f}x")
        lines.append("  Volumen institucional alto.")
    elif vr_sp >= 2.5:
        lines.append(f"\nSPIKE: Fuerte VR={vr_sp:.2f}x - bueno")
    else:
        lines.append(f"\nSPIKE: Minimo VR={vr_sp:.2f}x")
        lines.append("  Edge menor en spikes de VR bajo.")

    # CVD divergencia
    div = (sd=="Up" and delta<0) or (sd=="Down" and delta>0)
    if div:
        lines.append(f"\nCVD: DIVERGE {delta:+.0f}")
        lines.append("  Confirma manipulacion institucional.")
    else:
        lines.append(f"\nCVD: No diverge {delta:+.0f}")
        lines.append("  Puede ser breakout real, mas riesgo.")

    # Resultado
    exit_txt = sig["exit"]
    if exit_txt == "TARGET":
        lines.append(f"\nRESULTADO: GANADOR +{sig['rr']:.2f}R")
    elif exit_txt == "STOP":
        lines.append(f"\nRESULTADO: PERDEDOR -1.00R")
    else:
        lines.append(f"\nRESULTADO: ABIERTO >2h")

    # Sugerencia
    session = sig["session"]
    if session == "London":
        lines.append("\nSESION: London - mejor edge historico")
    elif session == "Asia":
        lines.append("\nSESION: Asia - edge negativo en backtest")
        lines.append("  Considerar filtrar esta sesion.")
    elif session == "NewYork":
        lines.append("\nSESION: NewYork - edge negativo")
        lines.append("  Considerar filtrar esta sesion.")
    else:
        lines.append(f"\nSESION: {session}")

    return "\n".join(lines)

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol",     nargs="?", default="BTCUSDT")
    parser.add_argument("--days",     type=int,  default=14)
    parser.add_argument("--dir",      choices=["Short", "Long"], default=None)
    parser.add_argument("--session",  default=None)
    parser.add_argument("--no-slope", action="store_true")
    parser.add_argument("--no-diverge", action="store_true",
                        help="No exigir CVD diverge en el spike")
    parser.add_argument("--save",     action="store_true",
                        help="Guardar PNG en vez de mostrar interactivo")
    parser.add_argument("--max",      type=int, default=30,
                        help="Maximo de graficos a generar (default 30)")
    args = parser.parse_args()

    now_ms   = int(time_mod.time() * 1000)
    start_ms = now_ms - args.days * 86400 * 1000

    print(f"[fetch] {args.symbol} {args.days} dias...")
    raw  = fetch_klines(args.symbol, start_ms, now_ms)
    bars = raw
    if not bars:
        sys.exit("[error] Sin barras")
    print(f"[parse] {len(bars)} barras M1: {ms_to_str(bars[0]['ts_ms'])} -> {ms_to_str(bars[-1]['ts_ms'])}")

    CFG["manip_min_vr"] = 2.0
    CFG["dist_min_vr"]  = 1.5

    det = AmdDetector(use_slope=not args.no_slope,
                      require_diverge=not args.no_diverge)
    signals = []
    for i, row in enumerate(bars):
        sig = det.on_bar(row, i)
        if sig is None: continue
        if args.dir and sig["direction"] != args.dir: continue
        if args.session and sig["session"] != args.session: continue
        signals.append(sig)

    simulate(signals, bars)
    print(f"[detector] {len(signals)} senales | {sum(1 for s in signals if s['exit']=='TARGET')} TARGET | "
          f"{sum(1 for s in signals if s['exit']=='STOP')} STOP | "
          f"{sum(1 for s in signals if s['exit']=='OPEN')} OPEN")

    if not signals:
        print("Sin senales. Proba con --no-slope o --no-diverge.")
        return

    save_dir = None
    if args.save:
        save_dir = f"amd_charts_{args.symbol}"
        os.makedirs(save_dir, exist_ok=True)
        print(f"[save] Guardando en {save_dir}/")

    total = min(len(signals), args.max)
    print(f"[charts] Generando {total} graficos...")
    for i, sig in enumerate(signals[:total], 1):
        print(f"  [{i}/{total}] {sig['direction']:5} {sig['session']:15} "
              f"{ms_to_str(sig['timestamp_ms'])} | {sig['exit']} "
              f"{sig['result_r']:+.2f}R" if sig["result_r"] else
              f"  [{i}/{total}] {sig['direction']:5} {sig['session']:15} "
              f"{ms_to_str(sig['timestamp_ms'])} | {sig['exit']}")
        plot_signal(bars, sig, i, total, save_dir=save_dir)

    if save_dir:
        print(f"\n[done] {total} graficos guardados en {save_dir}/")

if __name__ == "__main__":
    main()
