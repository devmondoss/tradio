# Validación en vivo — cartera liquidity-provision (H1+H5+H21)

Valida el **único riesgo que el backtest no puede zanjar**: el ratio de fills maker reales.
El backtest asume fill al tocar el nivel; en vivo hay cola de órdenes. Esto lo mide.

## Archivos
- `levels.py` — calcula los niveles donde reposan las órdenes límite (paridad exacta con
  `backtest/_consolidated.py`): área-valor del día previo (H1), POC del order block (H5),
  POC defendido (H21). Sólo devuelve niveles del lado correcto del mercado.
- `paper_liquidity.py` — harness de validación.

## Filtro de volatilidad (cableado)
El edge mejora mucho operando solo en **alta volatilidad** (ATR > su mediana móvil 500; ver
`docs/orderflow/LIQUIDITY_STRATEGY.md`). El harness está cableado para ello:
- Cada nivel se taguea con `vol_regime` ('high'/'low').
- El **fill ratio y el PnL se registran SEPARADOS por régimen** (VOL-HIGH / VOL-LOW), justo para
  validar el caveat: ¿en alta vol los fills empeoran (selección adversa) y se come el +0.51R?
- Por defecto observa **ambos** regímenes (validación comparada). Con `--high-vol-only` solo
  repone niveles en alta vol (modo despliegue real).

## Modos
### 1. DRY-RUN (por defecto — sin claves, sin riesgo)
```
python live/paper_liquidity.py                  # observa ambos regímenes (validación)
python live/paper_liquidity.py --high-vol-only  # solo alta vol (cómo se desplegaría)
```
Se conecta al feed PÚBLICO de Bybit (mainnet, datos reales), repone órdenes límite VIRTUALES
cada vela M5 cerrada, y mide en tiempo real, **separado por régimen de volatilidad**: ratio de
fills, outcome real (stop/TP) y PnL paper. Todo forward, sin look-ahead. Déjalo corriendo días.
La línea de stats muestra: `VOL-HIGH: fill=x/y (z%) ... | VOL-LOW: fill=...`.

**Limitación honesta:** el dry-run asume fill cuando el precio TOCA el nivel (igual que el
backtest). No modela posición en cola. Mide la parte forward real (¿el precio llega?, ¿revierte?)
pero NO el fill ratio con cola. Para eso → modo testnet.

### 2. LIVE-TESTNET (órdenes POST-ONLY reales — mide fill ratio con cola)
```
export BYBIT_TESTNET=true BYBIT_API_KEY=... BYBIT_API_SECRET=...
python live/paper_liquidity.py --live-testnet
```
Coloca órdenes límite **post-only** (maker, nunca taker) reales en testnet. Es la única forma de
medir el fill ratio real. Gated: aborta si no hay claves testnet. Claves en testnet.bybit.com.

## Qué buscar
- **Fill ratio:** si baja mucho (<50%) y los fills que faltan son los buenos rebotes, el edge del
  backtest se degrada. Es la métrica decisiva.
- **avgR/WR de los fills reales** vs backtest (H1≈+0.33, H5≈+0.22, H21≈+0.05 OOS). Si se mantienen,
  la cartera es desplegable.

## Deploy en Railway (24/7, persistencia Supabase)

Servicio SEPARADO del monitor Rust. Pasos:

1. **Migración Supabase** (una vez): correr `migrations/liquidity_paper.sql` en el SQL editor de
   Supabase. Crea `liquidity_paper_trades` (trades cerrados) + `liquidity_paper_snapshots`
   (fill ratio en el tiempo) + vista `liquidity_paper_latest`.
2. **Railway** → New Service → Deploy from GitHub repo → **Settings → Root Directory = `live`**
   (usa `live/Dockerfile` automáticamente).
3. **Variables**: copiar de `railway.liquidity-paper.env.example`:
   - `SYMBOL=BTCUSDT`, `TF=15`, `HIGH_VOL_ONLY=false`
   - `SUPABASE_URL`, `SUPABASE_KEY` (service key)
4. Deploy. Logs en vivo (stdout sin buffer). Cada cierre de M15 escribe un snapshot; cada trade
   cerrado escribe una fila. Sin claves de Bybit (es dry-run, datos públicos, cero riesgo).

**Qué consultar en Supabase:**
```sql
select * from liquidity_paper_latest;                          -- estado actual (fill ratio por régimen)
select vol_regime, count(*), avg(result_r), avg(win::int)      -- calidad de fills real
  from liquidity_paper_trades group by vol_regime;
```

## Backtest de referencia
```
python backtest/_consolidated.py --tf 5 --margin 2 --capN 2
```
OOS: WR 56%, avgR +0.15, $500→$854 (+71% en 3.5m), 6/6 trimestres positivos, MaxDD 24%.
