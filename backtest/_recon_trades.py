"""
_recon_trades.py — reconstruye CADA paper trade tick-a-tick desde los raw_trades de Bybit
=========================================================================================
Para cada trade nativo de la era limpia: carga los ticks reales del símbolo, replayea
desde el fill (opened_at) hasta el cierre/timeout y mide la VERDAD:
  - MFE_R / MAE_R reales (vs lo que registró el binario)
  - ¿tocó tp1? ¿antes del stop?  -> valida la lógica del PARCIAL (filled1)
  - exit reason reconstruido + result_r -> valida la ejecución del binario
Compara cada métrica contra la BD. Marca anomalías (ej. tp1 tocado pero filled1=False).

Correr: python backtest/_recon_trades.py
"""
import json, urllib.request, sys
from pathlib import Path
import numpy as np, pandas as pd

URL = "https://jubpovmsfvaqfnidozfh.supabase.co"
KEY = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
       ".eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imp1YnBvdm1zZnZhcWZuaWRvemZoIiwicm9sZSI6"
       "InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4MTk4OTY1NywiZXhwIjoyMDk3NTY1NjU3fQ"
       ".X_UMT7FypCvmPX1WxmI_Sg29FVRG2IliVBOwkWMh3ZI")
DIRS = {"BTCUSDT": "E:/bybit-data/bybit-perp-btc/raw_trades",
        "ETHUSDT": "E:/bybit-data/bybit-perp-eth/raw_trades",
        "SOLUSDT": "E:/bybit-data/bybit-perp-sol/raw_trades"}
FEE_TAKER, FEE_MAKER = 0.0011, 0.0004
MK, TK = FEE_MAKER/2, FEE_TAKER/2
TIMEOUT_MS = 24*60*60*1000
FLIP = pd.Timestamp("2026-06-25 14:00", tz="UTC")

def get(q):
    req = urllib.request.Request(f"{URL}/rest/v1/{q}", headers={"apikey":KEY,"Authorization":f"Bearer {KEY}"})
    with urllib.request.urlopen(req, timeout=60) as r: return json.loads(r.read())

def ms(iso):
    return int(pd.Timestamp(iso).timestamp()*1000)

# --- cargar ticks de un símbolo para la ventana (ts, price) ---
_cache = {}
def load_ticks(sym):
    if sym in _cache: return _cache[sym]
    d = Path(DIRS[sym])
    days = sorted(d.glob("2026-06-2*.parquet"))
    days = [p for p in days if p.stem >= "2026-06-22"]
    parts = [pd.read_parquet(p, columns=["ts_ms","price"]) for p in days]
    df = pd.concat(parts, ignore_index=True).sort_values("ts_ms")
    ts = df.ts_ms.values.astype(np.int64); px = df.price.values.astype(np.float64)
    _cache[sym] = (ts, px)
    print(f"  [{sym}] ticks cargados: {len(ts):,}  {pd.to_datetime(ts[0],unit='ms')} -> {pd.to_datetime(ts[-1],unit='ms')}", file=sys.stderr)
    return _cache[sym]

def replay(t):
    sym, side = t["symbol"], t["side"]
    entry, stop = float(t["entry"]), float(t["stop"])
    tp1 = float(t["tp1"]) if t.get("tp1") is not None else None
    tp2 = float(t["target"]); atr = float(t["atr"] or 0)
    risk = abs(entry-stop)
    if risk <= 0: return None
    o_ms = ms(t["opened_at"]); end_ms = o_ms + TIMEOUT_MS
    c_ms = ms(t["closed_at"]) if t.get("closed_at") else end_ms   # cierre real registrado
    ts, px = load_ticks(sym)
    i0 = np.searchsorted(ts, o_ms); i1 = np.searchsorted(ts, min(end_ms, ts[-1]))
    if i1 - i0 < 2: return None
    seg = px[i0:i1]                                 # ventana de SIM (hasta timeout)
    iC = np.searchsorted(ts, min(c_ms, ts[-1]))
    segH = px[i0:max(iC, i0+2)]                     # ventana de HOLDING real (para MFE/MAE)
    # MFE/MAE reales (hasta el cierre real, comparable con la BD)
    if side == "long":
        mfe = (segH.max()-entry)/risk; mae = (entry-segH.min())/risk
    else:
        mfe = (entry-segH.min())/risk; mae = (segH.max()-entry)/risk
    # secuencia de eventos durante el holding real
    def first_cross(arr, level, up):
        if level is None or not np.isfinite(level): return None
        hits = (arr>=level) if up else (arr<=level)
        return int(np.argmax(hits)) if hits.any() else None
    if side == "long":
        i_tp1 = first_cross(segH, tp1, True); i_stp = first_cross(segH, stop, False); i_tp2 = first_cross(segH, tp2, True)
    else:
        i_tp1 = first_cross(segH, tp1, False); i_stp = first_cross(segH, stop, True); i_tp2 = first_cross(segH, tp2, False)
    tp1_touched = i_tp1 is not None
    tp1_before_stop = tp1_touched and (i_stp is None or i_tp1 < i_stp)
    # --- replay FADE: 50% en tp1 -> BE -> tp2 ; stop ---
    def sim_fade():
        cur = stop; realized=0.0; rem=1.0; f1=False; p1=0.5 if tp1 else 0.0; reason="timeout"
        for k in range(len(seg)):
            p = seg[k]
            if side=="long":
                if p <= cur: realized += rem*((cur-entry)/risk); reason="be" if f1 else "stop"; break
                if not f1 and tp1 and p >= tp1: realized += p1*((tp1-entry)/risk); rem-=p1; f1=True; cur=entry
                if p >= tp2: realized += rem*((tp2-entry)/risk); reason="target"; break
            else:
                if p >= cur: realized += rem*((entry-cur)/risk); reason="be" if f1 else "stop"; break
                if not f1 and tp1 and p <= tp1: realized += p1*((entry-tp1)/risk); rem-=p1; f1=True; cur=entry
                if p <= tp2: realized += rem*((entry-tp2)/risk); reason="target"; break
        else:
            px_end = seg[-1]; realized += rem*(((px_end-entry) if side=="long" else (entry-px_end))/risk)
        exit_side = MK if reason=="target" else TK
        fee_r = (MK*1.0 + (MK*p1 if f1 else 0.0) + exit_side*rem)*entry/risk
        return realized-fee_r, reason, f1
    # --- replay FADE SIN parcial: posición entera a tp2 o stop ---
    def sim_fade_nopartial():
        reason="timeout"; res=None
        for k in range(len(seg)):
            p=seg[k]
            if side=="long":
                if p<=stop: res=(stop-entry)/risk; reason="stop"; break
                if p>=tp2:  res=(tp2-entry)/risk; reason="target"; break
            else:
                if p>=stop: res=(entry-stop)/risk; reason="stop"; break
                if p<=tp2:  res=(entry-tp2)/risk; reason="target"; break
        if res is None:
            pe=seg[-1]; res=((pe-entry) if side=="long" else (entry-pe))/risk
        exit_side = MK if reason=="target" else TK
        fee_r=(MK + exit_side)*entry/risk
        return res-fee_r, reason
    # --- replay TRAIL: ATR*4 ---
    def sim_trail():
        fee_r=(MK+TK)*entry/risk; best=entry; trail=stop
        for k in range(len(seg)):
            p=seg[k]
            if side=="long":
                best=max(best,p); trail=max(trail,best-4*atr)
                if p<=trail: return (trail-entry)/risk-fee_r,"trail"
            else:
                best=min(best,p); trail=min(trail,best+4*atr)
                if p>=trail: return (entry-trail)/risk-fee_r,"trail"
        px_end=seg[-1]; return (((px_end-entry) if side=="long" else (entry-px_end))/risk-fee_r,"trail")
    r_np, reason_np = (sim_fade_nopartial() if t["gestion"]=="fade" else (np.nan,""))
    if t["gestion"]=="fade":
        my_r, my_reason, my_f1 = sim_fade()
    else:
        my_r, my_reason = sim_trail(); my_f1 = False
    return dict(mfe=mfe, mae=mae, tp1_touched=tp1_touched, tp1_before_stop=tp1_before_stop,
                tp2_touched=i_tp2 is not None, my_r=my_r, my_reason=my_reason, my_f1=my_f1,
                r_nopartial=r_np, reason_np=reason_np,
                tp1_dist_R=(abs(tp1-entry)/risk if tp1 else np.nan))

def main():
    tr = pd.DataFrame(get("liquidity_paper_trades?select=*&reconstructed=eq.false&order=created_at.asc&limit=10000"))
    tr["created_at"] = pd.to_datetime(tr.created_at, utc=True)
    c = tr[tr.created_at > FLIP].copy().reset_index(drop=True)
    print(f"Reconstruyendo {len(c)} trades nativos era limpia (tick-a-tick)\n")
    rows=[]
    for _, t in c.iterrows():
        try: a = replay(t)
        except Exception as e: a=None; print("ERR",t["symbol"],e,file=sys.stderr)
        if a is None: continue
        rows.append({**{k:t[k] for k in ("symbol","side","kind","gestion","reason","result_r","mfe_r","mae_r","filled1","tp1","entry","stop")}, **a})
    R = pd.DataFrame(rows)
    for col in ("result_r","mfe_r","mae_r"): R[col]=pd.to_numeric(R[col],errors="coerce")
    R["db_filled1"]=R.filled1==True

    print("="*100)
    print("VALIDACIÓN binario vs reconstrucción tick-a-tick (era limpia, MFE/MAE hasta el cierre real)")
    print("="*100)
    print(f"n reconstruidos: {len(R)}")
    # 1) MFE/MAE: ¿coincide lo que registró el binario?
    R["mfe_err"]=(R.mfe-R.mfe_r).abs(); R["mae_err"]=(R.mae-R.mae_r).abs()
    print(f"\n[MFE] |recon-bd| mediana={R.mfe_err.median():.3f}R  p90={R.mfe_err.quantile(.9):.3f}R  (recon mediana={R.mfe.median():.2f} vs bd {R.mfe_r.median():.2f})")
    print(f"[MAE] |recon-bd| mediana={R.mae_err.median():.3f}R  p90={R.mae_err.quantile(.9):.3f}R  (recon {R.mae.median():.2f} vs bd {R.mae_r.median():.2f})")

    # 2) ¿QUÉ HIZO EL BINARIO: parcial o posición entera? -> cuál matchea result_r
    fade=R[R.gestion=="fade"].copy()
    fade["err_partial"]=(fade.my_r-fade.result_r).abs()
    fade["err_nopart"]=(fade.r_nopartial-fade.result_r).abs()
    n_match_part=int((fade.err_partial<fade.err_nopart).sum()); n_match_np=int((fade.err_nopart<=fade.err_partial).sum())
    print("\n" + "="*100)
    print("¿EL BINARIO TOMA EL PARCIAL O CORRE LA POSICIÓN ENTERA? (fades, n=%d)"%len(fade))
    print("="*100)
    print(f"  result_r BD matchea mejor con PARCIAL:        {n_match_part}/{len(fade)}  (|err| med {fade.err_partial.median():.3f}R)")
    print(f"  result_r BD matchea mejor con POSICIÓN ENTERA:{n_match_np}/{len(fade)}  (|err| med {fade.err_nopart.median():.3f}R)")
    print(f"  avgR fade BD={fade.result_r.mean():+.3f}  |  con-parcial={fade.my_r.mean():+.3f}  |  sin-parcial(entera)={fade.r_nopartial.mean():+.3f}")

    # 3) El parcial: tp1 tocado antes del stop vs filled1 registrado
    anom = fade[(fade.tp1_before_stop) & (~fade.db_filled1)]
    print(f"\n  tp1 tocado ANTES del stop (recon ticks): {int(fade.tp1_before_stop.sum())}/{len(fade)}")
    print(f"  binario marcó filled1=True:              {int(fade.db_filled1.sum())}/{len(fade)}")
    print(f"  tp1 tocado pero filled1=False:           {len(anom)}/{len(fade)}")
    print("\n  desglose de esos casos (qué pasó en realidad):")
    for _,r in anom.iterrows():
        print(f"    {r.symbol} {r.side} {r.kind:<13} reason_bd={r.reason:<8} result_bd={r.result_r:+6.2f} "
              f"| con-parcial={r.my_r:+6.2f} sin-parcial={r.r_nopartial:+6.2f} | mfe={r.mfe:.2f}R tp1d={r.tp1_dist_R:.2f}R")

    # 4) impacto NETO del parcial sobre TODOS los fades (no solo anómalos)
    print("\n  IMPACTO del parcial sobre el avgR de fades:")
    print(f"    sin parcial (correr entera): {fade.r_nopartial.mean():+.3f}R  WR={100*(fade.r_nopartial>0).mean():.0f}%")
    print(f"    con parcial (spec backtest): {fade.my_r.mean():+.3f}R  WR={100*(fade.my_r>0).mean():.0f}%")
    print(f"    -> el parcial {'SUBE' if fade.my_r.mean()>fade.r_nopartial.mean() else 'BAJA'} avgR "
          f"{fade.my_r.mean()-fade.r_nopartial.mean():+.3f}R y {'sube' if (fade.my_r>0).mean()>(fade.r_nopartial>0).mean() else 'baja'} WR "
          f"{100*((fade.my_r>0).mean()-(fade.r_nopartial>0).mean()):+.0f}pp")

    out = Path("backtest/_recon_out.csv"); R.to_csv(out, index=False)
    print(f"\n  detalle -> {out}")

if __name__=="__main__": main()
