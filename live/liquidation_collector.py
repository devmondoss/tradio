"""
liquidation_collector.py — captura forward de liquidaciones Bybit → Supabase.
=============================================================================
Bybit NO publica histórico de liquidaciones. La ÚNICA forma de tener datos para
testear la hipótesis "cascada de liquidaciones cerca del nivel = combustible" es
empezar a guardarlas YA. Este proceso es INDEPENDIENTE del paper trading (no toca
liquidity_monitor → cero redeploy/warmup de los servicios de trading).

Se suscribe a allLiquidation.{symbol} para los 3 activos, parsea cada evento y lo
vuelca en lotes a la tabla liquidity_liquidations (migrations/liquidity_liquidations.sql).

Correr local:   SUPABASE_URL=... SUPABASE_KEY=... python live/liquidation_collector.py
Railway:        4º servicio · startCommand = python live/liquidation_collector.py
"""
import asyncio, json, os, time
import urllib.request
import websockets

WS_URL   = "wss://stream.bybit.com/v5/public/linear"
SYMBOLS  = os.environ.get("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",")
SUPA_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPA_KEY = os.environ.get("SUPABASE_KEY", "")
FLUSH_N  = int(os.environ.get("FLUSH_N", "10"))      # vacía el buffer cada N eventos
FLUSH_S  = int(os.environ.get("FLUSH_S", "30"))      # ...o cada S segundos

_buf = []
_last_flush = time.time()


MAX_BUF = int(os.environ.get("MAX_BUF", "20000"))   # tope del buffer si Supabase cae mucho rato


def _insert(rows):
    """POST batch a Supabase REST (bloqueante; en executor). Devuelve True solo si confirmó."""
    if not (SUPA_URL and SUPA_KEY):
        print(f"[liq] (sin Supabase) {len(rows)} eventos: {rows[-1]}"); return True
    data = json.dumps(rows).encode()
    req = urllib.request.Request(
        f"{SUPA_URL}/rest/v1/liquidity_liquidations",
        data=data, method="POST",
        headers={"apikey": SUPA_KEY, "Authorization": f"Bearer {SUPA_KEY}",
                 "Content-Type": "application/json", "Prefer": "return=minimal"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status < 300: return True
            print(f"[liq] insert HTTP {r.status}"); return False
    except Exception as e:
        print(f"[liq] insert error: {e}"); return False


async def flush(force=False):
    """Vuelca el buffer SOLO si Supabase confirma. Si falla, NO descarta: reintenta
    el próximo ciclo (los eventos quedan en el buffer). Último recurso: cap MAX_BUF."""
    global _last_flush
    if not _buf: return
    if not force and len(_buf) < FLUSH_N and (time.time()-_last_flush) < FLUSH_S: return
    n = len(_buf)                                  # snapshot: solo intentamos los n actuales
    rows = _buf[:n]                                # (eventos que lleguen durante el POST quedan)
    ok = await asyncio.get_event_loop().run_in_executor(None, _insert, rows)
    _last_flush = time.time()
    if ok:
        del _buf[:n]
        print(f"[liq] +{n} -> Supabase  (total visto: {_seen[0]}, buffer {len(_buf)})")
    else:
        print(f"[liq] insert FALLÓ — reintenta próximo ciclo (buffer {len(_buf)}, sin perder datos)")
        if len(_buf) > MAX_BUF:                    # outage muy largo: descarta lo más viejo (y avisa)
            drop = len(_buf) - MAX_BUF
            del _buf[:drop]
            print(f"[liq] ⚠️ buffer > {MAX_BUF}: descartados {drop} eventos más viejos")


_seen = [0]


def parse(symbol, data):
    """Evento allLiquidation: {T, s, S, v, p}. S=lado del orden de liquidación."""
    for d in data:
        try:
            p = float(d.get("p", 0)); v = float(d.get("v", 0))
            if p <= 0 or v <= 0: continue
            _buf.append({"ts_ms": int(d.get("T", 0)), "symbol": d.get("s", symbol),
                         "side": d.get("S", ""), "price": p, "qty": v,
                         "usd": round(p*v, 2)})
            _seen[0] += 1
        except (ValueError, TypeError):
            continue


async def run():
    sub = {"op": "subscribe", "args": [f"allLiquidation.{s}" for s in SYMBOLS]}
    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20) as ws:
                await ws.send(json.dumps(sub))
                print(f"[liq] suscrito allLiquidation: {SYMBOLS}")
                last_hb = time.time()
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=FLUSH_S)
                        d = json.loads(raw)
                        topic = d.get("topic", "")
                        if topic.startswith("allLiquidation"):
                            parse(topic.split(".")[-1], d.get("data", []))
                    except asyncio.TimeoutError:
                        pass            # sin eventos: cae al flush periódico
                    if time.time() - last_hb >= 300:    # heartbeat cada 5min (vivo aunque calmo)
                        print(f"[liq] vivo · total visto {_seen[0]} · buffer {len(_buf)}")
                        last_hb = time.time()
                    await flush()
        except Exception as e:
            print(f"[liq] WS caído: {e} — reconectando en 5s")
            await flush(force=True)
            await asyncio.sleep(5)


if __name__ == "__main__":
    print(f">>> liquidation_collector  symbols={SYMBOLS}  supa={'sí' if SUPA_URL else 'NO (dry)'}")
    asyncio.run(run())
