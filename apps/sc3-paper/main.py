"""
SC3 Demo Trading — absorción intradiaria en niveles VP (M5).

Señales sc3 + HTF filter → órdenes LÍMITE en Bybit Demo (o testnet/live).
Gestión: SL/TP adjuntos en la orden. Bybit gestiona el cierre.
Sizing dinámico: qty = RISK_USDT / abs(entry - stop) — paridad exacta con backtest.
Registra trades en Supabase tabla sc3_paper_trades.

Env:
  SYMBOL          — BTCUSDT | ETHUSDT | SOLUSDT
  EXEC_MODE       — demo | testnet | live
  BYBIT_API_KEY   — API key
  BYBIT_API_SECRET— API secret
  RISK_USDT       — riesgo fijo por trade en USDT (default 5 = 1% de $500)
  EXEC_LEVERAGE   — apalancamiento (default 1)
  SUPABASE_URL
  SUPABASE_KEY
"""
import asyncio, os, json, time, math, hmac, hashlib, logging
from collections import deque
from typing import Optional
import httpx, websockets

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SYMBOL      = os.environ["SYMBOL"]
EXEC_MODE   = os.environ.get("EXEC_MODE", "demo")
API_KEY     = os.environ.get("BYBIT_API_KEY", "")
API_SECRET  = os.environ.get("BYBIT_API_SECRET", "")
RISK_USDT   = float(os.environ.get("RISK_USDT", "5"))   # $5 = 1% de $500
LEVERAGE    = int(os.environ.get("EXEC_LEVERAGE", "1"))

# mínimos de qty por símbolo (Bybit linear)
QTY_STEP = {"BTCUSDT": 0.001, "ETHUSDT": 0.01, "SOLUSDT": 0.1}
QTY_MIN  = {"BTCUSDT": 0.001, "ETHUSDT": 0.01, "SOLUSDT": 0.1}

def calc_qty(entry: float, stop: float) -> str:
    """qty dinámica: arriesgar exactamente RISK_USDT."""
    risk_per_unit = abs(entry - stop)
    if risk_per_unit <= 0: return str(QTY_MIN[SYMBOL])
    raw = RISK_USDT / risk_per_unit
    step = QTY_STEP[SYMBOL]
    qty = max(QTY_MIN[SYMBOL], math.floor(raw / step) * step)
    return f"{qty:.{len(str(step).rstrip('0').split('.')[-1])}f}"
SUPA_URL    = os.environ.get("SUPABASE_URL", "")
SUPA_KEY    = os.environ.get("SUPABASE_KEY", "")

BYBIT_URLS = {
    "demo":    "https://api-demo.bybit.com",
    "testnet": "https://api-testnet.bybit.com",
    "live":    "https://api.bybit.com",
}
WS_HOSTS = [
    "wss://stream.bybit.com/v5/public/linear",
    "wss://stream.bytick.com/v5/public/linear",
]
REST_PUBLIC = ["https://api.bybit.com", "https://api.bytick.com"]
BYBIT_URL = BYBIT_URLS[EXEC_MODE]

SC3_PARAMS = {
    "BTCUSDT": dict(vr_thr=1.5, stop_atr=0.5, tol_atr=0.6, rr_cap=3.0, fp_bin=10.0),
    "ETHUSDT": dict(vr_thr=1.5, stop_atr=0.5, tol_atr=0.6, rr_cap=3.0, fp_bin=0.5),
    "SOLUSDT": dict(vr_thr=1.5, stop_atr=0.5, tol_atr=0.6, rr_cap=3.0, fp_bin=0.1),
}
PARAMS = SC3_PARAMS[SYMBOL]

ATR_N              = 14
VA_BARS            = 288   # 24h en M5
SWING_N            = 50
MAX_BARS           = 700
COOLDOWN           = 6
MAX_DAY            = 3
STOP_FLOOR         = 0.0015
MIN_RR             = 1.2
ORDER_TIMEOUT_BARS = 10    # cancela límite no llenada tras 10 barras (50min)
ENTRY_OFFSET_ATR   = 0.10  # coloca la límite 0.10 ATR hacia el precio → sube fill ~12%→30-68%
                           # manteniendo OOS positivo en los 3 (sweep validado 2026-07-01)

# ── Bybit Auth ────────────────────────────────────────────────────────────────
def _sign(params_str: str) -> dict:
    ts = str(int(time.time() * 1000))
    recv = "5000"
    sign_str = f"{ts}{API_KEY}{recv}{params_str}"
    sig = hmac.new(API_SECRET.encode(), sign_str.encode(), hashlib.sha256).hexdigest()
    return {
        "X-BAPI-API-KEY": API_KEY,
        "X-BAPI-TIMESTAMP": ts,
        "X-BAPI-SIGN": sig,
        "X-BAPI-RECV-WINDOW": recv,
    }

async def bybit_post(client: httpx.AsyncClient, path: str, body: dict) -> dict:
    payload = json.dumps(body)
    hdrs = {**_sign(payload), "Content-Type": "application/json"}
    r = await client.post(f"{BYBIT_URL}{path}", content=payload, headers=hdrs, timeout=10)
    return r.json()

async def bybit_get(client: httpx.AsyncClient, path: str, params: dict = {}) -> dict:
    # CRÍTICO: firmar y ENVIAR el mismo query string ordenado. Si se pasa params= a httpx,
    # los manda en orden de inserción ≠ orden firmado (sorted) → firma inválida (retCode
    # 10004) en toda consulta con orderId/limit (order/realtime, closed-pnl). Romper esto
    # dejaba la detección de fill y el registro de cierres completamente ciegos.
    qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    hdrs = _sign(qs)
    url = f"{BYBIT_URL}{path}" + (f"?{qs}" if qs else "")
    r = await client.get(url, headers=hdrs, timeout=10)
    return r.json()

# ── Supabase ──────────────────────────────────────────────────────────────────
SUPA_HDR = lambda: {
    "apikey": SUPA_KEY, "Authorization": f"Bearer {SUPA_KEY}",
    "Content-Type": "application/json", "Prefer": "return=minimal",
}

async def supa_insert(client: httpx.AsyncClient, row: dict):
    if not SUPA_URL or not SUPA_KEY: return
    try:
        r = await client.post(f"{SUPA_URL}/rest/v1/sc3_paper_trades",
                              headers=SUPA_HDR(), json=row, timeout=10)
        if r.status_code not in (200, 201):
            log.warning(f"[supa] {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.warning(f"[supa] {e}")

async def supa_save_pos(client: httpx.AsyncClient, sig: dict, fill_ts: int):
    """Persiste posición abierta para sobrevivir redeploys."""
    if not SUPA_URL or not SUPA_KEY: return
    try:
        # borrar la anterior del mismo símbolo
        await client.delete(f"{SUPA_URL}/rest/v1/sc3_open_pos",
                            headers=SUPA_HDR(),
                            params={"symbol": f"eq.{SYMBOL}"}, timeout=5)
        row = {"symbol": SYMBOL, "side": sig.get("side"), "entry": sig.get("entry"),
               "stop": sig.get("stop"), "tp": sig.get("tp"), "vr": sig.get("vr"),
               "atr": sig.get("atr"), "tag": sig.get("tag"),
               "htf_filter": sig.get("htf_filter", ""), "ts_open": sig.get("ts_open", 0),
               "fill_ts_ms": fill_ts, "placed_ts": sig.get("placed_ts", 0)}
        await client.post(f"{SUPA_URL}/rest/v1/sc3_open_pos",
                          headers=SUPA_HDR(), json=row, timeout=5)
    except Exception as e:
        log.warning(f"[supa_save_pos] {e}")

async def supa_clear_pos(client: httpx.AsyncClient):
    if not SUPA_URL or not SUPA_KEY: return
    try:
        await client.delete(f"{SUPA_URL}/rest/v1/sc3_open_pos",
                            headers=SUPA_HDR(),
                            params={"symbol": f"eq.{SYMBOL}"}, timeout=5)
    except Exception as e:
        log.warning(f"[supa_clear_pos] {e}")

async def supa_load_pos(client: httpx.AsyncClient) -> Optional[dict]:
    """Restaura contexto de posición tras redeploy."""
    if not SUPA_URL or not SUPA_KEY: return None
    try:
        r = await client.get(f"{SUPA_URL}/rest/v1/sc3_open_pos",
                             headers=SUPA_HDR(),
                             params={"symbol": f"eq.{SYMBOL}", "select": "*"}, timeout=5)
        rows = r.json()
        return rows[0] if rows else None
    except Exception as e:
        log.warning(f"[supa_load_pos] {e}")
        return None

# ── State (señales) ───────────────────────────────────────────────────────────
class Bar:
    __slots__ = ("ts_ms","o","h","l","c","vol","delta","atr","fp")
    def __init__(self, ts_ms, o, h, l, c, vol, delta=0.0, atr=0.0, fp=None):
        self.ts_ms=ts_ms; self.o=o; self.h=h; self.l=l; self.c=c
        self.vol=vol; self.delta=delta; self.atr=atr; self.fp=fp or {}

class State:
    def __init__(self):
        self.bars:   deque[Bar] = deque(maxlen=MAX_BARS)
        self.h1bars: deque[Bar] = deque(maxlen=300)
        self.h4bars: deque[Bar] = deque(maxlen=100)
        self.cur_atr = 0.0
        self.atr_history: deque[float] = deque(maxlen=600)
        self.vol_history: deque[float] = deque(maxlen=600)
        self.cool_bar = 0
        self.bar_idx  = 0
        self.day_count: dict[int, int] = {}
        self._atr_was_ok = False  # para log de transición

    def atr_median(self) -> float:
        if len(self.atr_history) < 50: return 0.0
        s = sorted(self.atr_history)
        return s[len(s)//2]

    def vol_mean(self) -> float:
        if len(self.vol_history) < 20: return 1.0
        return sum(self.vol_history) / len(self.vol_history)

    def _vp_levels(self):
        window = list(self.bars)[-VA_BARS:]
        if len(window) < 20: return None, None, None
        fp: dict[float, float] = {}
        for b in window:
            for pb, (buy, sell) in b.fp.items():
                fp[pb] = fp.get(pb, 0.0) + buy + sell
        if not fp: return None, None, None
        total = sum(fp.values())
        poc_bin = max(fp, key=fp.get)
        poc = poc_bin * PARAMS["fp_bin"]
        bins_sorted = sorted(fp.keys())
        poc_idx = bins_sorted.index(poc_bin)
        va_vol = fp[poc_bin]; up = poc_idx; dn = poc_idx
        while va_vol < 0.70 * total:
            up_v = fp.get(bins_sorted[up+1], 0.0) if up+1 < len(bins_sorted) else 0.0
            dn_v = fp.get(bins_sorted[dn-1], 0.0) if dn-1 >= 0 else 0.0
            if up_v >= dn_v and up+1 < len(bins_sorted): up += 1; va_vol += up_v
            elif dn-1 >= 0: dn -= 1; va_vol += dn_v
            else: break
        return poc, bins_sorted[up]*PARAMS["fp_bin"], bins_sorted[dn]*PARAMS["fp_bin"]

    def _pdh_pdl(self):
        if len(self.bars) < 2: return None, None
        bl = list(self.bars)
        today = int(bl[-1].ts_ms // 86_400_000)
        prev = [b for b in bl if int(b.ts_ms // 86_400_000) < today]
        if not prev: return None, None
        ld = max(int(b.ts_ms // 86_400_000) for b in prev)
        yd = [b for b in prev if int(b.ts_ms // 86_400_000) == ld]
        return max(b.h for b in yd), min(b.l for b in yd)

    def _weekly_hl(self):
        if not self.bars: return None, None
        now = list(self.bars)[-1].ts_ms
        w = [b for b in self.bars if b.ts_ms >= now - 7*86_400_000]
        return (max(b.h for b in w), min(b.l for b in w)) if w else (None, None)

    def _swing_hl(self):
        w = list(self.bars)[-SWING_N:]
        return (max(b.h for b in w), min(b.l for b in w)) if w else (None, None)

    def _htf_ema(self, tf: str):
        bars = self.h1bars if tf == "h1" else self.h4bars
        if len(bars) < 20: return None
        closes = [b.c for b in bars]
        ema = closes[0]; k = 2/(20+1)
        for c in closes[1:]: ema = c*k + ema*(1-k)
        return closes[-1], ema

    def compute_signals(self) -> list[dict]:
        if len(self.bars) < 60: return []
        bar = list(self.bars)[-1]
        atr = bar.atr
        if atr <= 0: return []
        atr_med = self.atr_median()
        diag = self.bar_idx % 6 == 0  # log diagnóstico cada 30 min
        atr_ok = atr_med > 0 and atr > atr_med
        if atr_ok != self._atr_was_ok:
            ratio = 100*atr/atr_med if atr_med > 0 else 0
            status = "DESBLOQUEADO" if atr_ok else "BLOQUEADO"
            log.info(f"[atr] {status}  atr={atr:.2f} med={atr_med:.2f} ({ratio:.0f}%)")
            self._atr_was_ok = atr_ok
        if not atr_ok:
            if diag:
                ratio = 100*atr/atr_med if atr_med > 0 else 0
                log.info(f"[diag] ATR_BLOCK  atr={atr:.2f} med={atr_med:.2f} ({ratio:.0f}%)")
            return []
        if self.bar_idx < self.cool_bar:
            if diag: log.info(f"[diag] COOLDOWN   bar={self.bar_idx} cool_until={self.cool_bar}")
            return []
        day_key = int(bar.ts_ms // 86_400_000)
        if self.day_count.get(day_key, 0) >= MAX_DAY:
            if diag: log.info(f"[diag] MAX_DAY    trades_hoy={self.day_count.get(day_key,0)}/{MAX_DAY}")
            return []

        vol_mean = self.vol_mean()
        vr = bar.vol / vol_mean if vol_mean > 0 else 0.0
        if vr < PARAMS["vr_thr"] and vr <= 3.0:
            if diag: log.info(f"[diag] VR_BLOCK   vr={vr:.2f} < thr={PARAMS['vr_thr']}")
            return []

        poc, vah, val = self._vp_levels()
        pdh, pdl = self._pdh_pdl()
        wh, wl = self._weekly_hl()
        swh, swl = self._swing_hl()
        tol = PARAMS["tol_atr"] * atr

        h1 = self._htf_ema("h1"); h4 = self._htf_ema("h4")
        h1_bull = h1[0] > h1[1] if h1 else False
        h4_bull = h4[0] > h4[1] if h4 else False
        h1_bear = h1[0] < h1[1] if h1 else False
        h4_bear = h4[0] < h4[1] if h4 else False

        close = bar.c; delta = bar.delta
        sigs = []

        htf_long = ("h1" if h1_bull else "") + ("_h4" if h4_bull else "") + ("_vr3" if vr > 3 else "")
        htf_long = htf_long.strip("_") or ""
        for lvl, tag in [(val,"val"),(poc,"poc"),(pdl,"pdl"),(wl,"wl"),(swl,"swl")]:
            if lvl is None or not math.isfinite(lvl): continue
            if not (lvl < close and abs(close - lvl) <= tol): continue
            if delta >= 0: continue
            if not (h1_bull or h4_bull or vr > 3): continue
            # stop/tp anclados al NIVEL (paridad con backtest); entry desplazado hacia el
            # precio ENTRY_OFFSET_ATR para mejorar fill. Si el offset cruzaría el precio
            # (nivel muy cerca), descansar en el nivel exacto para seguir siendo maker.
            stop = lvl - PARAMS["stop_atr"] * atr
            lvl_risk = lvl - stop
            cands = [c for c in [vah, pdh, wh, swh] if c and math.isfinite(c) and c > lvl*1.001]
            if not cands: continue
            tp = min(lvl + PARAMS["rr_cap"]*lvl_risk, max(cands))
            entry = lvl + ENTRY_OFFSET_ATR * atr
            if entry >= close: entry = lvl
            mr = STOP_FLOOR * entry
            if abs(entry - stop) < mr: stop = entry - mr
            risk = entry - stop
            if risk <= 0 or not (stop < entry < tp): continue
            if (tp - entry)/risk < MIN_RR: continue
            sigs.append(dict(side="long", entry=round(entry,2), stop=round(stop,2),
                             tp=round(tp,2), atr=atr, vr=vr, tag=tag, htf_filter=htf_long))
            break

        if not sigs:
            htf_short = ("h1" if h1_bear else "") + ("_h4" if h4_bear else "") + ("_vr3" if vr > 3 else "")
            htf_short = htf_short.strip("_") or ""
            for lvl, tag in [(vah,"vah"),(poc,"poc"),(pdh,"pdh"),(wh,"wh"),(swh,"swh")]:
                if lvl is None or not math.isfinite(lvl): continue
                if not (lvl > close and abs(lvl - close) <= tol): continue
                if delta <= 0: continue
                if not (h1_bear or h4_bear or vr > 3): continue
                stop = lvl + PARAMS["stop_atr"] * atr
                lvl_risk = stop - lvl
                cands = [c for c in [val, pdl, wl, swl] if c and math.isfinite(c) and c < lvl*0.999]
                if not cands: continue
                tp = max(lvl - PARAMS["rr_cap"]*lvl_risk, min(cands))
                entry = lvl - ENTRY_OFFSET_ATR * atr
                if entry <= close: entry = lvl
                mr = STOP_FLOOR * entry
                if abs(stop - entry) < mr: stop = entry + mr
                risk = stop - entry
                if risk <= 0 or not (tp < entry < stop): continue
                if (entry - tp)/risk < MIN_RR: continue
                sigs.append(dict(side="short", entry=round(entry,2), stop=round(stop,2),
                                 tp=round(tp,2), atr=atr, vr=vr, tag=tag, htf_filter=htf_short))
                break

        if sigs:
            self.cool_bar = self.bar_idx + COOLDOWN
            # NOTA: el cupo diario (day_count) se incrementa al CONFIRMAR fill en poll(),
            # no aquí: una límite que expira sin llenar no debe quemar un trade del día.
        elif diag:
            h1_dir = "bull" if h1_bull else ("bear" if h1_bear else "flat")
            h4_dir = "bull" if h4_bull else ("bear" if h4_bear else "flat")
            fmt = lambda x: f"{x:.2f}" if x else "N/A"
            log.info(f"[diag] NO_LEVEL   close={bar.c:.2f} vr={vr:.2f} delta={bar.delta:.0f} "
                     f"h1={h1_dir} h4={h4_dir} tol={tol:.2f} "
                     f"val={fmt(val)} vah={fmt(vah)} poc={fmt(poc)}")
        return sigs

# ── Executor ──────────────────────────────────────────────────────────────────
class Executor:
    def __init__(self, client: httpx.AsyncClient, supa: httpx.AsyncClient):
        self.client = client
        self.supa   = supa
        self.open_order_id: Optional[str] = None
        self.open_sig: Optional[dict]     = None
        self.order_placed_bar: int        = 0
        self.position_open: bool          = False
        self.bar_idx: int                 = 0
        self.fill_ts: int                 = 0    # ms del fill real

    async def setup_leverage(self):
        r = await bybit_post(self.client, "/v5/position/set-leverage", {
            "category": "linear", "symbol": SYMBOL,
            "buyLeverage": str(LEVERAGE), "sellLeverage": str(LEVERAGE),
        })
        log.info(f"[leverage] {r.get('retMsg','')}")

    async def place(self, sig: dict):
        side = "Buy" if sig["side"] == "long" else "Sell"
        qty  = calc_qty(sig["entry"], sig["stop"])
        body = {
            "category":    "linear",
            "symbol":      SYMBOL,
            "side":        side,
            "orderType":   "Limit",
            "qty":         qty,
            "price":       str(sig["entry"]),
            "stopLoss":    str(sig["stop"]),
            "takeProfit":  str(sig["tp"]),
            "slTriggerBy": "LastPrice",
            "tpTriggerBy": "LastPrice",
            "timeInForce": "GTC",
            "positionIdx": 0,
        }
        r = await bybit_post(self.client, "/v5/order/create", body)
        if r.get("retCode") == 0:
            oid = r["result"]["orderId"]
            self.open_order_id    = oid
            self.open_sig         = {**sig, "placed_ts": int(time.time() * 1000)}
            self.order_placed_bar = self.bar_idx
            log.info(f"[ORDER] {sig['side'].upper()} entry={sig['entry']} "
                     f"sl={sig['stop']} tp={sig['tp']} qty={qty} risk=${RISK_USDT} id={oid}")
        else:
            log.warning(f"[order_err] {r.get('retMsg')} | {r}")

    async def ensure_tpsl(self):
        """Fija SL/TP SOBRE LA POSICIÓN y verifica que hayan quedado.

        El stopLoss/takeProfit que se manda en /v5/order/create no queda pegado a la
        posición: auditoría 2026-08-03 encontró las 3 posiciones vivas de la cuenta demo
        (BTC/ETH/SOL) con stopLoss='' y takeProfit='' — una de ellas abierta hacía 20 días.
        Consecuencias: la posición no cierra nunca en su nivel, el exit cae fuera de
        [stop, tp] (47/69 filas en julio), y el servicio queda trabado creyendo que tiene
        posición abierta (sc3-btc y sc3-sol dejaron de operar el 13/14-jul por esto).
        """
        sig = self.open_sig or {}
        sl, tp = sig.get("stop", 0), sig.get("tp", 0)
        if not (sl and tp):
            log.warning("[tpsl] sig sin stop/tp — no se puede proteger la posición"); return
        r = await bybit_post(self.client, "/v5/position/trading-stop", {
            "category": "linear", "symbol": SYMBOL,
            "stopLoss": str(sl), "takeProfit": str(tp),
            "slTriggerBy": "LastPrice", "tpTriggerBy": "LastPrice",
            "tpslMode": "Full", "positionIdx": 0,
        })
        # retCode 34040 = "not modified" (ya estaban puestos) → no es error
        if r.get("retCode") not in (0, 34040):
            log.error(f"[tpsl] FALLÓ trading-stop: {r.get('retCode')} {r.get('retMsg')}")
        # verificación: leer la posición y confirmar que el SL quedó
        chk = await bybit_get(self.client, "/v5/position/list",
                              {"category": "linear", "symbol": SYMBOL})
        for p in chk.get("result", {}).get("list", []):
            if float(p.get("size", "0") or 0) <= 0: continue
            got_sl, got_tp = p.get("stopLoss", ""), p.get("takeProfit", "")
            if got_sl and got_tp:
                log.info(f"[tpsl] posición protegida: SL={got_sl} TP={got_tp}")
            else:
                log.error(f"[tpsl] POSICIÓN DESPROTEGIDA tras trading-stop "
                          f"(SL='{got_sl}' TP='{got_tp}') — riesgo sin cortar")

    async def cancel(self):
        if not self.open_order_id: return
        r = await bybit_post(self.client, "/v5/order/cancel", {
            "category": "linear", "symbol": SYMBOL, "orderId": self.open_order_id,
        })
        log.info(f"[cancel] {self.open_order_id} → {r.get('retMsg','')}")
        self.open_order_id = None; self.open_sig = None

    async def poll(self):
        self.bar_idx += 1

        if self.open_order_id and not self.position_open:
            # Detección de fill vía order/realtime (devuelve el fill al instante; history
            # tiene lag de indexado). Chequear el fill ANTES del timeout para no cancelar
            # una orden ya llena. Requiere el fix de firma en bybit_get (si no, 10004 ciego).
            r = await bybit_get(self.client, "/v5/order/realtime",
                                {"category":"linear","symbol":SYMBOL,"orderId":self.open_order_id})
            items = r.get("result",{}).get("list",[])
            status = items[0].get("orderStatus","") if items else ""
            exec_qty = float(items[0].get("cumExecQty","0") or 0) if items else 0.0
            if status in ("Filled","PartiallyFilled") or exec_qty > 0:
                self.position_open = True
                self.fill_ts       = int(time.time() * 1000)
                self.open_order_id = None
                # precio de fill real: lo usa _record_closed para identificar CUÁL registro
                # de closed-PnL es el de esta posición (la ventana temporal sola no alcanza:
                # el cierre de la posición anterior puede caer después de nuestro placed_ts).
                fill_px = float(items[0].get("avgPrice", 0) or 0) if items else 0.0
                self.open_sig["fill_px"] = fill_px or self.open_sig.get("entry", 0)
                await self.ensure_tpsl()
                day_key = self.fill_ts // 86_400_000
                self.day_count[day_key] = self.day_count.get(day_key, 0) + 1
                log.info(f"[filled] posición {self.open_sig['side']} @ {self.open_sig['entry']} "
                         f"trades_hoy={self.day_count[day_key]}/{MAX_DAY}")
                # persistir posición para sobrevivir redeploys
                await supa_save_pos(self.supa, self.open_sig, self.fill_ts)
            elif self.bar_idx - self.order_placed_bar >= ORDER_TIMEOUT_BARS:
                log.info(f"[timeout] orden {self.open_order_id} sin llenar — cancelo")
                await self.cancel(); return

        if self.position_open:
            r = await bybit_get(self.client, "/v5/position/list",
                                {"category":"linear","symbol":SYMBOL})
            has_pos = any(float(p.get("size","0")) > 0
                         for p in r.get("result",{}).get("list",[]))
            if not has_pos:
                await self._record_closed()
                await supa_clear_pos(self.supa)
                self.position_open = False; self.open_sig = None; self.fill_ts = 0

    async def _record_closed(self):
        await asyncio.sleep(2)
        sig = self.open_sig or {}
        # Matcheo por VENTANA TEMPORAL, no items[0]. Antes se tomaba el último closed-PnL
        # de la cuenta y se le pegaba el stop/tp del sig en memoria: si el servicio
        # redeployaba o se perdía un poll, cruzaba el PnL de un trade con la señal de otro.
        # Auditoría 2026-08-02: 47/69 filas tenían el exit fuera de [stop, tp] y 4 filas
        # compartían literalmente el mismo registro de closed-PnL, con R de hasta ±33
        # bajo un rr_cap de 3.0. Ahora: solo registros posteriores al placed_ts de ESTA
        # orden, agregando los cierres parciales de la misma posición; si no matchea nada,
        # no se escribe fila (mejor un hueco que un dato falso).
        r = await bybit_get(self.client, "/v5/position/closed-pnl",
                            {"category":"linear","symbol":SYMBOL,"limit":"50"})
        items = r.get("result",{}).get("list",[])
        if not items: log.warning("[pnl] no closed PnL encontrado"); return

        floor_ts = int(sig.get("placed_ts", 0) or 0) - 60_000
        cands = sorted((it for it in items
                        if int(it.get("updatedTime", 0) or 0) >= floor_ts),
                       key=lambda it: int(it.get("updatedTime", 0) or 0))
        if not cands:
            log.warning(f"[pnl] sin closed-PnL posterior a placed_ts={sig.get('placed_ts')} "
                        f"— NO se registra la fila (revisar manualmente)")
            return

        # La ventana temporal sola no alcanza: el cierre de la posición ANTERIOR puede caer
        # después de nuestro placed_ts (verificado en la cuenta demo: 1 de 8 casos elegía el
        # trade previo). Segundo filtro: el avgEntryPrice del registro tiene que parecerse al
        # precio de fill real de esta orden. Tolerancia = 2× el risk teórico, con piso de
        # 0.1% del precio para absorber el slippage del maker.
        target = float(sig.get("fill_px", 0) or 0) or float(sig.get("entry", 0) or 0)
        risk_th = abs(float(sig.get("entry", 0) or 0) - float(sig.get("stop", 0) or 0))
        tol = max(2.0 * risk_th, 0.001 * target) if target > 0 else 0.0
        near = [it for it in cands
                if abs(float(it.get("avgEntryPrice", 0) or 0) - target) <= tol]
        if not near:
            log.warning(f"[pnl] ningún closed-PnL con avgEntryPrice ≈ {target} (tol={tol:.4f}) "
                        f"— NO se registra la fila (revisar manualmente)")
            return
        cands = near

        # La posición puede haber cerrado en varios pedazos (SL/TP parciales): agrego los
        # registros consecutivos que comparten el mismo avgEntryPrice.
        ref_entry = float(cands[0].get("avgEntryPrice", 0) or 0)
        group = [it for it in cands
                 if ref_entry > 0
                 and abs(float(it.get("avgEntryPrice", 0) or 0) - ref_entry) / ref_entry < 1e-6]
        closed_pnl = sum(float(it.get("closedPnl", 0) or 0) for it in group)
        qty        = sum(float(it.get("qty", 0) or 0) for it in group)
        entry_px   = ref_entry
        exit_px    = (sum(float(it.get("avgExitPrice", 0) or 0) * float(it.get("qty", 0) or 0)
                          for it in group) / qty) if qty > 0 else 0.0

        stop_px = float(sig.get("stop", 0) or 0)
        if entry_px <= 0 or stop_px <= 0:
            log.warning(f"[pnl] entry/stop inválidos (entry={entry_px} stop={stop_px}) "
                        f"— NO se registra la fila")
            return
        # risk sobre el FILL real (entry_px), no el nivel teórico de la señal — con stops
        # tan ajustados (~0.5 ATR) el slippage entry teórico vs real distorsiona el R.
        risk = abs(entry_px - stop_px)
        risk_usdt = risk * qty
        if risk_usdt <= 0:
            log.warning(f"[pnl] risk_usdt={risk_usdt} (risk={risk} qty={qty}) — NO se registra")
            return
        r_val = closed_pnl / risk_usdt

        # reason real: dónde cayó el exit, no el signo del R (antes: 'target' si R>0).
        tp_px = float(sig.get("tp", 0) or 0)
        lo, hi = min(stop_px, tp_px), max(stop_px, tp_px)
        if tp_px <= 0:                 reason = "unknown"
        elif exit_px < lo:             reason = "below_range"
        elif exit_px > hi:             reason = "above_range"
        elif abs(exit_px - tp_px) < abs(exit_px - stop_px): reason = "target"
        else:                          reason = "stop"
        if reason in ("below_range", "above_range", "unknown"):
            log.warning(f"[pnl] exit={exit_px} FUERA de [stop={stop_px}, tp={tp_px}] "
                        f"→ reason={reason} R={r_val:+.2f} (SL/TP no gobernó la salida)")
        log.info(f"[CLOSED] pnl={closed_pnl:.4f} R={r_val:+.3f} entry={entry_px} "
                 f"exit={exit_px} reason={reason} parciales={len(group)}")
        ts_close = int(time.time() * 1000)
        fill_ts  = self.fill_ts or sig.get("placed_ts", 0)
        ttf_s    = round((fill_ts - sig.get("placed_ts", fill_ts)) / 1000) if fill_ts else 0
        row = {
            "symbol":        SYMBOL,
            "side":          sig.get("side","?"),
            "tag":           sig.get("tag",""),
            "htf_filter":    sig.get("htf_filter",""),
            "entry":         entry_px,
            "stop":          sig.get("stop", 0),
            "tp":            sig.get("tp", 0),
            "exit_px":       exit_px,
            "r":             round(r_val, 4),
            "result_r":      round(r_val, 4),
            "win":           r_val > 0,
            "reason":        reason,
            "vr":            round(sig.get("vr", 0), 2),
            "atr":           round(sig.get("atr", 0), 4),
            "ts_open":       sig.get("ts_open", 0),
            "fill_ts_ms":    fill_ts,
            "ts_close":      ts_close,
            "time_to_fill_s": ttf_s,
        }
        await supa_insert(self.supa, row)

# ── Bootstrap ─────────────────────────────────────────────────────────────────
async def bootstrap_klines(interval: str, limit: int = 1000, retries: int = 3) -> list[Bar]:
    # Reintenta sobre ambos hosts: Bybit puede devolver 200 con retCode!=0 (rate-limit
    # en ráfaga) y result.list ausente -> antes eso dejaba H4=0 sin recuperación.
    async with httpx.AsyncClient(timeout=30) as c:
        for attempt in range(retries):
            for host in REST_PUBLIC:
                try:
                    r = await c.get(f"{host}/v5/market/kline",
                        params={"category":"linear","symbol":SYMBOL,
                                "interval":interval,"limit":limit})
                    j = r.json()
                    if j.get("retCode") != 0:
                        log.warning(f"[boot] {host} {interval} retCode={j.get('retCode')} {j.get('retMsg')}")
                        continue
                    data = (j.get("result") or {}).get("list") or []
                    if not data:
                        log.warning(f"[boot] {host} {interval} lista vacía")
                        continue
                    bars = []
                    for k in reversed(data):
                        ts = int(k[0]); o,h,l,cl,vol = [float(k[i]) for i in range(1,6)]
                        bars.append(Bar(ts,o,h,l,cl,vol))
                    if bars and bars[-1].ts_ms > int(time.time()*1000) - 30_000:
                        bars.pop()
                    log.info(f"[boot] {interval}: {len(bars)} barras")
                    return bars
                except Exception as e:
                    log.warning(f"[boot] {host} {interval} {e!r}")
            if attempt < retries - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
    log.error(f"[boot] {interval}: SIN DATOS tras {retries} intentos")
    return []

def _build_state(state: State, m5: list[Bar], h1: list[Bar], h4: list[Bar]):
    cur = 0.0
    for i, b in enumerate(m5):
        prev = m5[i-1].c if i > 0 else b.c
        tr = max(b.h-b.l, abs(b.h-prev), abs(b.l-prev))
        cur = tr if cur == 0 else cur*(ATR_N-1)/ATR_N + tr/ATR_N
        b.atr = cur; b.delta = b.vol*(1 if b.c>b.o else -1)
        pb = round(b.c / PARAMS["fp_bin"])
        b.fp = {pb: (b.vol/2 if b.c>b.o else 0, b.vol/2 if b.c<=b.o else 0)}
        state.bars.append(b)
        state.atr_history.append(cur); state.vol_history.append(b.vol)
    state.cur_atr = cur
    for b in h1: state.h1bars.append(b)
    for b in h4: state.h4bars.append(b)
    state.bar_idx = len(m5)

# ── WS handler ────────────────────────────────────────────────────────────────
def on_kline(state: State, msg: dict) -> Optional[Bar]:
    topic = msg.get("topic",""); data = msg.get("data",[])
    if not data: return None
    k = data[0]; ok = k.get("confirm", False)
    ts = int(k["start"]); o,h,l,c,vol = [float(k[x]) for x in ["open","high","low","close","volume"]]

    if "kline.5." in topic and ok:
        prev_c = list(state.bars)[-1].c if state.bars else c
        tr = max(h-l, abs(h-prev_c), abs(l-prev_c))
        state.cur_atr = tr if state.cur_atr==0 else state.cur_atr*(ATR_N-1)/ATR_N+tr/ATR_N
        b = Bar(ts,o,h,l,c,vol, delta=vol*(1 if c>o else -1), atr=state.cur_atr)
        pb = round(c/PARAMS["fp_bin"])
        b.fp = {pb: (vol/2 if c>o else 0, vol/2 if c<=o else 0)}
        state.bars.append(b); state.atr_history.append(state.cur_atr)
        state.vol_history.append(vol); state.bar_idx += 1
        return b
    elif "kline.60." in topic and ok:
        state.h1bars.append(Bar(ts,o,h,l,c,vol))
    elif "kline.240." in topic and ok:
        state.h4bars.append(Bar(ts,o,h,l,c,vol))
    return None

# ── Main ──────────────────────────────────────────────────────────────────────
async def run():
    log.info(f"=== SC3 {EXEC_MODE.upper()} | {SYMBOL} | risk=${RISK_USDT} lev={LEVERAGE}x ===")
    state = State()
    async with httpx.AsyncClient() as exec_client, httpx.AsyncClient() as supa_client:
        executor = Executor(exec_client, supa_client)
        await executor.setup_leverage()

        m5 = await bootstrap_klines("5",   1000); await asyncio.sleep(0.4)
        h1 = await bootstrap_klines("60",  300);  await asyncio.sleep(0.4)
        h4 = await bootstrap_klines("240", 100)
        _build_state(state, m5, h1, h4)
        atr_now = state.cur_atr
        atr_med = state.atr_median()
        ratio   = 100*atr_now/atr_med if atr_med > 0 else 0
        atr_st  = "OK" if atr_now > atr_med else f"BLOCK ({ratio:.0f}%)"
        h1r = state._htf_ema("h1"); h4r = state._htf_ema("h4")
        h1_dir = ("bull" if h1r[0]>h1r[1] else "bear") if h1r else "?"
        h4_dir = ("bull" if h4r[0]>h4r[1] else "bear") if h4r else "?"
        poc, vah, val = state._vp_levels()
        log.info(f"[boot] M5={len(state.bars)} H1={len(state.h1bars)} H4={len(state.h4bars)}")
        log.info(f"[boot] ATR={atr_now:.2f}  med={atr_med:.2f}  ratio={ratio:.0f}%  → {atr_st}")
        log.info(f"[boot] HTF  h1={h1_dir}  h4={h4_dir}")
        log.info(f"[boot] VP   poc={poc}  vah={vah}  val={val}")

        # ── Reconciliación con el exchange (fuente de verdad) ────────────────
        # Bybit manda. Compara la posición REAL con lo que el código cree y cubre los
        # 4 casos, incluido adoptar posiciones HUÉRFANAS (fill que nunca se trackeó por
        # crash/redeploy o fallo de detección) para no perder trades ni apilar órdenes.
        ctx = await supa_load_pos(supa_client)
        pos_r = await bybit_get(exec_client, "/v5/position/list",
                                {"category": "linear", "symbol": SYMBOL})
        live = next((p for p in pos_r.get("result",{}).get("list",[])
                     if float(p.get("size","0")) > 0), None)
        if live and ctx:
            # (a) posición + contexto → restaurar tracking
            executor.open_sig      = ctx
            executor.fill_ts       = ctx.get("fill_ts_ms", 0)
            executor.position_open = True
            log.info(f"[reconcile] RESTAURADA {ctx.get('side')} entry={ctx.get('entry')} "
                     f"fill_ts={ctx.get('fill_ts_ms')}")
            # una posición restaurada puede venir sin SL/TP en el exchange (caso real:
            # sc3-btc arrastró un short desprotegido 20 días) → re-armar y verificar
            await executor.ensure_tpsl()
        elif live and not ctx:
            # (c) HUÉRFANA: posición en el exchange sin registro → adoptar desde sus datos
            side = "long" if live.get("side") == "Buy" else "short"
            adopted = {"side": side, "entry": float(live.get("avgPrice", 0) or 0),
                       "stop": float(live.get("stopLoss") or 0),
                       "tp": float(live.get("takeProfit") or 0),
                       "tag": "reconciled", "htf_filter": "", "vr": 0, "atr": 0,
                       "placed_ts": 0, "ts_open": 0}
            executor.open_sig      = adopted
            executor.fill_ts       = int(time.time() * 1000)
            executor.position_open = True
            await supa_save_pos(supa_client, adopted, executor.fill_ts)
            log.warning(f"[reconcile] HUÉRFANA adoptada: {side} entry={adopted['entry']} "
                        f"sl={adopted['stop']} tp={adopted['tp']}")
        elif ctx and not live:
            # (b) contexto sin posición → cerró durante downtime → registrar y limpiar
            log.info("[reconcile] posición cerró durante downtime — registrando...")
            executor.open_sig = ctx
            executor.fill_ts  = ctx.get("fill_ts_ms", 0)
            await executor._record_closed()
            await supa_clear_pos(supa_client)
        else:
            log.info("[reconcile] sin posición abierta — arranque limpio")

        topics = [f"kline.5.{SYMBOL}", f"kline.60.{SYMBOL}", f"kline.240.{SYMBOL}"]
        ws_idx = 0
        while True:
            try:
                async with websockets.connect(WS_HOSTS[ws_idx%len(WS_HOSTS)], ping_interval=20) as ws:
                    await ws.send(json.dumps({"op":"subscribe","args":topics}))
                    log.info("[ws] conectado")
                    async for raw in ws:
                        bar = on_kline(state, json.loads(raw))
                        if bar is None: continue
                        await executor.poll()
                        if not (executor.open_order_id or executor.position_open):
                            sigs = state.compute_signals()
                            if sigs:
                                sig = sigs[0]; sig["ts_open"] = bar.ts_ms
                                await executor.place(sig)
            except Exception as e:
                ws_idx += 1
                log.warning(f"[ws] {e} — reconectando en 5s...")
                await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(run())
