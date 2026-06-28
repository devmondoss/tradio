"""
_testnet_check.py — verifica que tus API keys de TESTNET funcionan (auth + permisos de órdenes)
ANTES de correr el ejecutor Rust. Mismo esquema de firma que exec.rs.

Uso (PowerShell):
  $env:BYBIT_API_KEY="xxx"; $env:BYBIT_API_SECRET="yyy"; python backtest/_testnet_check.py
Uso (bash):
  BYBIT_API_KEY=xxx BYBIT_API_SECRET=yyy python backtest/_testnet_check.py
"""
import os, time, hmac, hashlib, json, urllib.request

MODE = os.environ.get("EXEC_MODE", "demo").lower()
BASE = {"demo": "https://api-demo.bybit.com", "testnet": "https://api-testnet.bybit.com",
        "live": "https://api.bybit.com"}.get(MODE, "https://api-demo.bybit.com")
print(f"(modo={MODE} base={BASE})")
KEY = os.environ.get("BYBIT_API_KEY", "")
SEC = os.environ.get("BYBIT_API_SECRET", "")
RECV = "5000"
SYMBOL = os.environ.get("SYMBOL", "BTCUSDT")

if not KEY or not SEC:
    raise SystemExit("Faltan BYBIT_API_KEY / BYBIT_API_SECRET en el entorno.")

def sign(ts, payload):
    pre = f"{ts}{KEY}{RECV}{payload}"
    return hmac.new(SEC.encode(), pre.encode(), hashlib.sha256).hexdigest()

def req(method, path, params=None, body=None):
    ts = str(int(time.time() * 1000))
    if method == "GET":
        qs = "&".join(f"{k}={v}" for k, v in (params or {}).items())
        sig = sign(ts, qs)
        url = f"{BASE}{path}?{qs}" if qs else f"{BASE}{path}"
        data = None
    else:
        body_str = json.dumps(body or {}, separators=(",", ":"))
        sig = sign(ts, body_str)
        url = f"{BASE}{path}"
        data = body_str.encode()
    headers = {"X-BAPI-API-KEY": KEY, "X-BAPI-SIGN": sig, "X-BAPI-TIMESTAMP": ts,
               "X-BAPI-RECV-WINDOW": RECV, "Content-Type": "application/json"}
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=15) as resp:
        return json.loads(resp.read())

print(f"== Chequeo testnet ({SYMBOL}) ==")
# 1. AUTH: leer posición (no necesita fondos)
try:
    d = req("GET", "/v5/position/list", {"category": "linear", "symbol": SYMBOL})
    if d.get("retCode") == 0:
        print("  [1] AUTH OK (firma + key válidas)")
    else:
        print(f"  [1] AUTH FALLA: {d.get('retCode')} {d.get('retMsg')}"); raise SystemExit(1)
except Exception as e:
    print(f"  [1] AUTH ERROR: {e}"); raise SystemExit(1)

# 2. BALANCE: ¿hay USDT testnet? (faucet)
try:
    d = req("GET", "/v5/account/wallet-balance", {"accountType": "UNIFIED"})
    coins = d.get("result", {}).get("list", [{}])[0].get("coin", [])
    usdt = next((c for c in coins if c.get("coin") == "USDT"), None)
    bal = float(usdt["walletBalance"]) if usdt and usdt.get("walletBalance") else 0.0
    print(f"  [2] BALANCE USDT testnet: {bal:.2f}" + ("" if bal > 0 else "  ← fondéate con el faucet"))
except Exception as e:
    print(f"  [2] BALANCE warn: {e}")

# 3. ÓRDENES: colocar PostOnly MUY lejos del mercado y cancelar (prueba permisos sin arriesgar)
try:
    tk = req("GET", "/v5/market/tickers", {"category": "linear", "symbol": SYMBOL})
    last = float(tk["result"]["list"][0]["lastPrice"])
    far = round(last * 0.80, 1)   # 20% abajo → no se llena
    qty = os.environ.get("EXEC_QTY", "0.001")
    d = req("POST", "/v5/order/create", body={
        "category": "linear", "symbol": SYMBOL, "side": "Buy", "orderType": "Limit",
        "qty": qty, "price": str(far), "timeInForce": "PostOnly", "reduceOnly": False, "positionIdx": 0,
    })
    if d.get("retCode") == 0:
        oid = d["result"]["orderId"]
        print(f"  [3] PLACE OK (orderId {oid[:12]}…)")
        c = req("POST", "/v5/order/cancel", body={"category": "linear", "symbol": SYMBOL, "orderId": oid})
        print("  [3] CANCEL OK" if c.get("retCode") == 0 else f"  [3] CANCEL falla: {c.get('retMsg')}")
        print("\n  ✅ TODO OK — las keys sirven para el ejecutor.")
    else:
        print(f"  [3] PLACE FALLA: {d.get('retCode')} {d.get('retMsg')}")
        print("     (revisá permisos de la key: Orders + Positions; y que tengas saldo/leverage)")
except Exception as e:
    print(f"  [3] ORDER ERROR: {e}")
