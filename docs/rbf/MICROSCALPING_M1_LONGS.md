# Microscalping M1 Longs — Configuración Completa

**Estado:** PAUSADO (2026-06-13) — guardado para prueba futura en paper trading  
**Razón de pausa:** targets por debajo del noise floor estructural en la mayoría de símbolos;
edge real solo en BTC y ETH con n insuficiente para validar.

---

## Concepto

Sistema de longs en M1 que detecta agotamiento vendedor / absorción usando microestructura
(CVD slope, OBI, VR, VWAP deviation). Dos estrategias desarrolladas en paralelo:

1. **Absorption** — detección de absorción silenciosa (wick bajo, OBI bid, CVD cayendo)
2. **Longs Minería** — patrones descubiertos por minería libre por símbolo (sin hipótesis previa)

---

## Estrategia 1: Absorption Long

### Archivos
- Backtest: `apps/rbf-review/api/absorption_backtest.py`
- UI tab: `absorption` en RBFModuleView

### Condiciones de entrada (todas deben cumplirse)
```
wick_atr    < 0.5      absorción silenciosa (wick inferior pequeño = no hay pánico)
bar_delta   < 0        presión vendedora visible
obi_l5      > 0.2      bid activo absorbiendo asks
vwap_dev    < -0.3%    precio por debajo del valor justo VWAP
cvd_slope   < -5       CVD cayendo (flujo vendedor agotándose)
```

### Parámetros de ejecución
```python
CAPITAL_INIT  = 500.0
RISK_PCT      = 0.02      # 2% del capital actual (compounding)
RR            = 2.0       # target 2R
TRAIL_R       = 1.90      # trailing stop activa en 1.90R
TRAIL_ATR_K   = 1.2       # trailing = best_high - 1.2×ATR
COOLDOWN      = 60        # 60 barras M1 entre señales por símbolo
FORWARD_BARS  = 120       # ventana máxima de simulación (2h)
```

### Gestión de la posición
- **Entrada:** open de barra i+1 (sin lookahead)
- **Stop:** wick_low − 0.15×ATR
- **Target:** entry + 2×risk (2R fijo)
- **Trailing:** activa cuando MFE ≥ 1.90R → floor en entry+1.90R, luego sube con ATR
- **Filtro USD mínimo por símbolo:**
  ```
  BTC $15 · ETH $5 · BNB $0.5 · SOL $0.08 · XRP $0.005
  ```

### Resultados backtest (Jun 5–13, 8d, n=60 aprox.)
- **BTC dominante** (~80% de señales)
- WR aproximada: ~40–45%
- Edge real solo cuando datos > 60 días (n insuficiente en datos actuales)

### Sesiones
Todas: London, LondonNyOverlap, NewYork, Asia, OffHours

---

## Estrategia 2: Longs Minería (patrones por símbolo)

### Archivos
- Backtest: `apps/rbf-review/api/longs_backtest.py`
- UI tab: `longs minería` en RBFModuleView
- Script de minería: `C:/tmp/mine_free2.py`
- Backtest formal del que salieron: `C:/tmp/backtest_patterns2.py`

### Método de descubrimiento
Minería libre sin hipótesis previas:
1. Calcular thresholds adaptativos por símbolo (percentiles reales de sus propios datos)
2. Enumerar ~50 features individuales + combinaciones de 2 + combinaciones de 3
3. Filtrar candidatos con WR > base (noise_floor como target, FORWARD=240 barras)
4. Seleccionar el combo más frecuente con WR alta en muestra de descubrimiento

### Patrones descubiertos (Jun 5–13)
```python
PATTERNS = {
    'BTCUSDT': 'decline>p75 + oi_momentum=False',
    'ETHUSDT': 'vwap_dev<p25 + sesion London',
    'BNBUSDT': 'cvd>p90 + sesion Overlap',     # marginal
    'SOLUSDT': 'sesion Overlap + vr<p25',        # marginal
    'XRPUSDT': 'sesion London + wick_hi>0.5ATR',
}
```

### Detectores (código exacto)
```python
def det_btc(bars, i, th):
    b = bars[i]
    if b.get('oi_momentum') is not False: return False   # OI NO creciendo
    dec = local_high_decline(bars, i, 30)
    return dec > th['dec_p75']   # caída desde high local > p75

def det_eth(bars, i, th):
    b = bars[i]
    ses = b.get('session') or ''
    if ses not in ('London', 'LondonNyOverlap'): return False
    vwap = b.get('vwap') or 0
    if vwap <= 0: return False
    return (b['close'] - vwap) / vwap * 100 < th['vd_p25']  # precio muy bajo VWAP

def det_bnb(bars, i, th):
    b = bars[i]
    ses = b.get('session') or ''
    if ses not in ('LondonNyOverlap',): return False
    return (b.get('cvd_slope') or 0) > th['cvd_p90']   # CVD alto en Overlap

def det_sol(bars, i, th):
    b = bars[i]
    ses = b.get('session') or ''
    if ses not in ('LondonNyOverlap',): return False
    return (b.get('vr') or 0) < th['vr_p25']   # VR bajo en Overlap

def det_xrp(bars, i, th):
    b = bars[i]
    ses = b.get('session') or ''
    if ses not in ('London', 'LondonNyOverlap'): return False
    atr = b.get('atr') or 0
    if atr <= 0: return False
    return (b['high'] - b['close']) / atr > 0.5   # wick superior > 0.5ATR
```

### Thresholds adaptativos (calculados en runtime)
```python
def calc_th(bars):
    # percentiles reales de la distribución del símbolo
    cvd_p90  = percentile(cvd_slopes, 0.90)
    vd_p25   = percentile(vwap_devs, 0.25)
    vr_p25   = percentile(vrs, 0.25)
    dec_p75  = percentile(local_declines_30b, 0.75)
```
Esto es clave: NO hay thresholds hardcodeados. Se calculan sobre los datos reales del período,
así cada símbolo usa su propia escala (BTC CVD ≠ ETH CVD ≠ SOL CVD en órdenes de magnitud).

### Noise floor por símbolo (MFE mínimo para cubrir fees)
```
BTCUSDT: 0.41%
ETHUSDT: 0.45%
BNBUSDT: 0.41%
SOLUSDT: 0.65%
XRPUSDT: 0.65%
```

### Parámetros de ejecución
```python
CAPITAL_INIT = 500.0
RISK_PCT     = 0.02      # compounding 2%
TRAIL_R      = 1.90      # trailing lock floor
TRAIL_K      = 1.2       # best_high - 1.2×ATR
FORWARD      = 240       # 4h ventana máxima
COOLDOWN     = 15        # 15 barras M1 (reducido para más señales con pocos datos)
```

### Gestión de la posición
- **Entrada:** open de barra i+1
- **Stop:** swing_low(10 barras) − 0.15×ATR
- **Filtro:** risk/entry ≤ 3% (descarta stops demasiado amplios)
- **Target:** entry + 2×risk
- **Trailing:** igual que absorption

### Resultados backtest formal (Jun 5–13, ~8d)
| Símbolo | n   | WR    | AvgR    | TotR    | Equity  | Estado    |
|---------|-----|-------|---------|---------|---------|-----------|
| BTC     | 157 | 43.9% | +0.474  | +73.40  | $1,897  | ✅ Edge real |
| ETH     | 50  | 42.0% | +0.715  | +35.74  | $931    | ✅ Edge real |
| BNB     | 35  | 28.6% | −0.171  | −12.4   | negativo | ❌ Marginal |
| SOL     | 47  | 29.8% | −0.136  | −13.4   | negativo | ❌ Marginal |
| XRP     | 49  | 36.7% | +0.115  | +5.52   | $543    | ⚠️ Marginal |

**Cautela:** BTC tiene 2 trades outlier (r=18.78 Jun-8, r=6.0 Jun-9) que inflan los resultados.
Sin esos 2 trades el equity cae significativamente. Verificar manualmente antes de activar.

---

## Problema estructural (razón de la pausa)

El edge real de M1 absorption/longs existe pero tiene dos limitaciones graves:

### 1. Targets por debajo del noise floor
Con stop de 3–8 pips en BTC, el target 2R = 6–16 pips. El noise floor de BTC es 0.41%
(~$420 en BTC=102k). Los targets son $40–$160, muy por debajo del movimiento estructural.
Las fees + spread consumen el edge.

### 2. El cooldown bloqueaba la señal correcta
BTC Jun-5: primera señal CVD=-110 (falsa, 18:38). Real minimum CVD=-653 llegó a 18:52.
Cooldown de 15 barras bloqueó la entrada en el verdadero mínimo.

### Lo que realmente capturaría el movimiento grande
El trade manual del usuario (+4.92%, ~6R, Jun-5 19:12 UTC) fue un movimiento H1/H4.
Para capturarlo se necesita:
- Contexto HTF (H1/H4/D1) que identifique zona de interés estructural
- Entrada M1 como trigger (el microscalping actual solo tiene la entrada, no el contexto)
- Target estructural (PDL, FVG H1, zona de liquidez) en vez de 2R fijo

---

## Para reactivar

1. Asegurarse de tener ≥60 días de datos de microestructura en Supabase
2. Ejecutar `mine_free2.py` con los nuevos datos para recalibrar patrones
3. Verificar outliers BTC manualmente (trades con r > 5)
4. Considerar añadir filtro de contexto HTF antes de la entrada M1
5. UI: tab `longs minería` y `absorption` ya están disponibles en la review app

---

## Archivos relevantes
```
apps/rbf-review/api/
├── absorption_backtest.py      # Estrategia 1
└── longs_backtest.py           # Estrategia 2

C:/tmp/
├── mine_free2.py               # Script de minería libre v2
└── backtest_patterns2.py       # Backtest formal patrones descubiertos

docs/rbf/
└── MICROSCALPING_M1_LONGS.md   # este archivo
```
