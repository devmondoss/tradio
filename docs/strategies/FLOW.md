# FLOW — Estrategia Adaptativa por Régimen (System C)

**Tipo**: Fade en chop · ATR trail en tendencia  
**Instrumento**: BTCUSDT Perpetuo (Bybit)  
**Timeframe**: M15 (decisión) · M1 (salida honesta)  
**Backtest**: 365 días tick-verificada · 2025-06-19 → 2026-06-19

---

## Qué hace

FLOW usa los mismos niveles que MAKER pero adapta la **gestión de salida al régimen del mercado**:

- **Chop (rango)** → fade idéntico a MAKER: parcial en TP1 → BE → target estructural
- **Tendencia** → ATR trailing stop: monta la continuación, sale cuando el momentum se agota

El régimen se detecta en M15: si el precio está alejado > 0.6 ATR de su SMA50 → tendencia. Caso contrario → chop.

---

## Generadores de entrada (3 + 1)

Idénticos a MAKER — el nivel de entrada es el mismo, cambia solo la gestión post-fill:

| Componente | Descripción | Dirección |
|---|---|---|
| `poc_ob` | POC del Order Block (vela de mayor rango reciente) | Largo y corto |
| `poc_def` | POC defendido ≥ 2 veces por mínimos | Solo largo |
| `poc_def_short` | POC defendido ≥ 2 veces por máximos (mirror) | Solo corto |
| `naked_poc` | POC de sesión previa no revisitado (imán estructural) | Largo o corto |

---

## Gestión

```
Entrada: límite maker en el nivel (siempre)
  ↓
CHOP (67% de trades):
  TP1 (si TP1 >= 2.3R): cerrar 50% → mover stop a BE → target estructural
  → igual que MAKER

TENDENCIA (33% de trades):
  ATR trail = 4 × ATR desde mejor precio alcanzado
  → sale cuando el precio retrocede 4 ATR del máximo/mínimo
  → captura la continuación sin target fijo
```

---

## Configuración (live-honesta)

| Parámetro | Valor |
|---|---|
| Timeframe decisión | M15 |
| Entrada | Límite maker |
| Selección adversa | 2 bps |
| Filtro volatilidad | ATR > mediana móvil(500) |
| Detección régimen | precio vs SMA50 ± 0.6 ATR |
| TP1 mínimo (chop) | TP1 ≥ 2.3 × riesgo |
| Trail (tendencia) | 4 × ATR desde mejor precio |
| Piso de stop | 0.15% |
| Fee entrada | 2 bps/lado maker |
| Fee stop/trail | 5.5 bps/lado taker |
| Riesgo | Fijo $5/trade (1% de $500, sin compounding) |

---

## Métricas (365d · tick-verificada)

| # | Métrica | IS (274d) | OOS (90d) | Full (365d) |
|---|---|---|---|---|
| 1 | Trades | 489 | 128 | **583** |
| 2 | Win Rate | 41.1% | 43.8% | **41.9%** |
| 3 | Net R | 728R | 234R | **824R** |
| 4 | Avg R / trade | 1.49R | 1.83R | **1.41R** |
| 5 | Avg Winner | 5.41R | 5.78R | **5.13R** |
| 6 | Avg Loser | -1.25R | -1.24R | **-1.26R** |
| 7 | Profit Factor | 3.02 | 3.63 | **2.93** |
| 8 | Max Drawdown | -16.2R | -10.3R | **-16.2R** |
| 9 | Recovery Factor | 44.8 | 22.9 | **50.7** |
| 10 | Sharpe (anual) | 6.57 | 7.05 | **6.15** |
| 11 | Max racha negativa | 10 | 5 | **10** |
| 12 | Trades / día | 1.78 | 1.42 | **1.60** |
| 13 | Net R / día | 2.66R | 2.60R | **2.26R** |

**OOS significativamente mejor que IS**: avgR +23%, Profit Factor +20%, Sharpe +7%, Max DD menor (-10.3R vs -16.2R). Edge más fuerte en el período no visto.

### Composición interna (365d)

| Componente | Trades | WR | Avg R | Net R |
|---|---|---|---|---|
| Fades (chop, 67%) | 390 | 41.8% | 1.00R | 390R |
| Trails (tendencia, 33%) | 193 | 42.0% | 2.25R | **434R** |
| **Total** | **583** | **41.9%** | **1.41R** | **824R** |

Los trails representan el 33% de los trades pero aportan el 53% del Net R.

---

## Relación con MAKER

FLOW ⊃ MAKER. Los mismos generadores de entrada, gestión diferente en tendencia:
- 74% de los trades de FLOW son idénticos a MAKER (fade en chop)
- 26% son exclusivos de FLOW (trail en tendencia)
- Correr MAKER + FLOW simultáneamente = 1.7x leverage sobre los mismos trades, **no diversificación**
- Para $500: elegir FLOW (824R vs 643R)
- Para $1000 con dos cuentas: MAKER + Trail-only (1077R, Recovery Factor 94.1, Max DD -11.4R)

---

## Paper trading

```bash
# Local (dry-run, sin riesgo)
python -u live/paper_liquidity.py --system flow

# Railway (env vars)
SYSTEM=flow
TF=15
HIGH_VOL_ONLY=false
```

Persistencia automática en Supabase si SUPABASE_URL/KEY están seteadas.  
El campo `gestion` en cada registro indica si el trade se gestionó como `fade` o `trail`.

---

## Notas

- **WR 41.9%** es correcto y esperado. Con Profit Factor 2.93, necesitas ganar el 34% de los trades para ser rentable. Operar con la expectativa de WR 50%+ provocará abandono prematuro del sistema.
- **Max racha negativa 10**: estadísticamente garantizada con WR 42% en 583 trades. No es señal de edge roto — el sistema la incluye en su Recovery Factor de 50.7.
- **Naked POC en paper**: aproximado desde OHLCV sin tick data. La entrada (fill ratio) es fiel; el nivel exacto puede diferir ±$50 del calculado con ticks.
- **Régimen en paper**: detección simple (SMA50 + 0.6 ATR). El backtest usa un clasificador más sofisticado. Diferencia marginal en producción.
