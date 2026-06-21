# MAKER — Estrategia de Liquidez (System A)

**Tipo**: Fade puro · Provisión de liquidez maker en niveles de volumen  
**Instrumento**: BTCUSDT Perpetuo (Bybit)  
**Timeframe**: M15 (decisión) · M1 (salida honesta)  
**Backtest**: 365 días tick-verificada · 2025-06-19 → 2026-06-19

---

## Qué hace

MAKER provee liquidez con órdenes límite maker en niveles de volumen estructural (POC). No predice dirección — **explota el rebote estadístico** cuando el precio toca un nivel donde hay liquidez real atrapada.

Cuando el precio vuelve al nivel, te llenan barato y rebota. Edge = mejor precio de entrada (maker) + rebate maker + selección adversa baja en zonas de alto volumen.

---

## Generadores de entrada (3 + 1)

| Componente | Descripción | Dirección |
|---|---|---|
| `poc_ob` | POC del Order Block (vela de mayor rango reciente) | Largo y corto |
| `poc_def` | POC defendido ≥ 2 veces por mínimos | Solo largo |
| `poc_def_short` | POC defendido ≥ 2 veces por máximos (mirror) | Solo corto |
| `naked_poc` | POC de sesión previa no revisitado (imán estructural) | Largo o corto |

Cartera balanceada 57/43 largo/corto gracias al mirror short.

---

## Gestión

```
Entrada: límite maker en el nivel
  ↓
TP1 (si TP1 >= 2.3 × riesgo): cerrar 50% → mover stop a breakeven
  ↓
Resto: target estructural (nivel más lejano de liquidez real)
  ↓
Stop: estructural (OB high/low ± 0.25 ATR · POC defendido - 0.6 ATR)
Timeout: 24h (salida taker)
```

**Sin gestión discrecional.** La salida es tan mecánica como la entrada.

---

## Configuración (live-honesta)

| Parámetro | Valor |
|---|---|
| Timeframe decisión | M15 |
| Entrada | Límite maker |
| Selección adversa | 2 bps |
| Filtro volatilidad | ATR > mediana móvil(500) |
| Rango mínimo TP1 | TP1 ≥ 2.3 × riesgo |
| Piso de stop | 0.15% (elimina stops minúsculos) |
| Fee entrada | 2 bps/lado maker |
| Fee stop/timeout | 5.5 bps/lado taker |
| Riesgo | Fijo $5/trade (1% de $500, sin compounding) |
| Cap diario | 2 trades/día por nivel |

---

## Métricas (365d · tick-verificada)

| # | Métrica | IS (274d) | OOS (90d) | Full (365d) |
|---|---|---|---|---|
| 1 | Trades | 438 | 114 | **508** |
| 2 | Win Rate | 45.9% | 47.4% | **47.0%** |
| 3 | Net R | 544R | 160R | **643R** |
| 4 | Avg R / trade | 1.24R | 1.41R | **1.27R** |
| 5 | Avg Winner | 4.20R | 4.38R | **4.13R** |
| 6 | Avg Loser | -1.27R | -1.27R | **-1.28R** |
| 7 | Profit Factor | 2.81 | 3.10 | **2.87** |
| 8 | Max Drawdown | -11.4R | -10.8R | **-11.4R** |
| 9 | Recovery Factor | 47.5 | 14.9 | **56.2** |
| 10 | Sharpe (anual) | 6.47 | 6.84 | **6.24** |
| 11 | Max racha negativa | 10 | 8 | **10** |
| 12 | Trades / día | 1.60 | 1.27 | **1.39** |
| 13 | Net R / día | 1.98R | 1.78R | **1.76R** |

**OOS supera IS** en avgR (+14%), Profit Factor (+10%) y Sharpe (+6%). Edge estable y sin degradación fuera de muestra.

---

## Micro-score (contexto por trade)

Cada trade lleva un score 0-4 de microestructura al momento de entrada:
- **OBI > 0.02**: libro con más bids que asks (compradores posicionados)
- **VR > 1.5**: volumen elevado en la barra (nivel significativo)
- **fp_absorb**: footprint confirma absorción activa en el nivel
- **DZ < -0.5** (largo): presión vendedora en el nivel (buyers absorbiendo)

Score ≥ 3: entrada de alta convicción. No filtra trades (todos tienen expectativa positiva) pero informa decisiones en live.

---

## Paper trading

```bash
# Local (dry-run, sin riesgo)
python -u live/paper_liquidity.py --system maker

# Railway (env vars)
SYSTEM=maker
TF=15
HIGH_VOL_ONLY=false   # true para solo operar en alta volatilidad
```

Persistencia automática en Supabase (`liquidity_paper_trades`) si SUPABASE_URL/KEY están seteadas.

---

## Notas

- **Fill ratio real**: el principal riesgo no capturado en backtest. En live, las órdenes límite maker pueden no llenarse si el precio toca el nivel y rebota antes del fill. Paper valida esto.
- **Naked POC en paper**: aproximado desde OHLCV (sin tick data). Directional correctness ~80%, no exactitud de nivel a $10.
- Max racha negativa esperada de 10 con WR 47% es estadísticamente **normal** en 500 trades. No señal de edge roto.
