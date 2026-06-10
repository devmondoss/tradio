# MomentumFlow v2 — Plan Paper (sin capital real)

## Decisión

MomentumFlow v2 corre en modo **paper únicamente** hasta acumular al menos 60 días
de señales live. No se arriesga capital real con este detector hasta validar el edge
con datos out-of-sample.

## Qué es

Detector de momentum intra-barra. A diferencia del RBF (que necesita una consolidación
previa de 15-60 barras), MF v2 dispara sobre una sola barra M1 con:

- `|DZ| >= 2.0` — z-score del delta vs distribución histórica del símbolo
- `VR >= 3.0` — volumen de la barra >= 3× la media de las últimas 50
- `STK_IMB` alineado con la dirección (Bearish para Short, Bullish para Long)

Stop: `1.0 × ATR` desde el cierre (ajustado desde 0.5 para viabilidad con comisiones).

## Estado del backtest (4.4 días, Python)

| Métrica        | Global     | Short+London+DZ≥4 |
|----------------|------------|-------------------|
| n              | 266        | ~13-29            |
| WR             | 37.6%      | ~59%              |
| AvgR           | +0.117R    | ~+1.29R           |
| Señales/día    | 60.5       | ~3-7              |

**Problema crítico**: parámetros ajustados sobre los mismos 4.4 días usados para
evaluar → data snooping. El WR global (37.6%) está por debajo del mínimo requerido (65%).

## Por qué paper y no live

1. **Datos insuficientes**: n=13-29 en el mejor subset, necesitan n≥60 out-of-sample.
2. **WR global bajo**: 37.6% está muy por debajo del objetivo de 65%.
3. **Comisiones con stop 0.5×ATR**: fees RT = 2× el riesgo en BTC/ETH. Con 1.0×ATR
   la viabilidad mejora pero falta confirmar con datos reales.
4. **Correlación entre símbolos**: 38% de señales disparan al mismo timestamp en
   múltiples pares = una sola apuesta de mercado, no trades independientes.

## Cuándo re-evaluar

- **60+ días live**: analizar Short+London/Asia con DZ≥4 — si WR≥55% con n≥60,
  considerar activar con tamaño mínimo.
- **Mientras tanto**: el RBF vivo acumula datos con las nuevas reglas
  (ATR_STOP_K=1.0, TRAIL_ATR_K=1.2). Eso es la prioridad.

## Señales a guardar en Supabase (si se implementa paper)

| Campo         | Descripción                        |
|---------------|------------------------------------|
| `symbol`      | par (BTCUSDT, etc.)                |
| `direction`   | Short / Long                       |
| `entry_price` | close de la barra de señal         |
| `stop_price`  | entry ± 1.0 × ATR                  |
| `target_price` | entry ± 2.0 × ATR (RR 2:1)       |
| `dz`          | z-score del delta                  |
| `vr`          | volume ratio                       |
| `session`     | London / Asia / NY / Overlap       |
| `stk_imb`     | Bearish / Bullish / None           |
| `outcome`     | rellenar cuando cierra (TARGET/STOP/TRAIL) |

## Relación con RBF

Los dos detectores son **complementarios**, no excluyentes:

- **RBF**: necesita consolidación previa → 3-5 señales/día, más selectivo
- **MF v2**: entra en momentum puro sin rango → potencialmente más frecuente

Si con 60+ días el edge de Short+London+DZ≥4 se confirma, MF v2 se implementa en
Rust como segundo detector activo bajo el mismo paper trader framework del RBF.
