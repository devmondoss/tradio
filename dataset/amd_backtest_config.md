# AMD Backtest — Configuración y Resultados

**Fecha:** 2026-06-07  
**Engine:** NautilusTrader 1.224.0  
**Script:** `scripts/amd_nautilus_backtest.py`  
**Datos:** Binance FAPI `/fapi/v1/klines` M1, 90 días (2026-03-09 → 2026-06-07)  
**Símbolo:** BTCUSDT perpetuo  
**Barras:** 129,600 M1

---

## Parámetros del detector

| Parámetro | Valor | Descripción |
|---|---|---|
| `accum_min_bars` | 15 | Mínimo de barras para validar rango de acumulación |
| `accum_max_bars` | 50 | Máximo antes de invalidar el rango |
| `accum_range_min_pct` | 0.06% | Rango mínimo como % del precio |
| `accum_range_max_pct` | 0.45% | Rango máximo como % del precio |
| `manip_min_vr` | 2.0x | VR mínimo en la barra del spike (manipulación) |
| `dist_min_vr` | 1.5x | VR mínimo en la barra de entry (distribución) |
| `dist_cvd_slope` | 10.0 | \|cvd_slope\| mínimo para confirmar entry (desactivado en este run) |
| `stop_buffer_pct` | 0.08% | Buffer sobre el spike extreme para el stop |
| `min_rr` | 2.0 | RR mínimo para emitir señal |
| `cooldown_bars` | 45 | Barras de cooldown tras una señal |
| `max_wait_bars_after_spike` | 10 | Máximo de barras esperando entry tras el spike |
| `max_hold_bars` | 120 | Máximo de barras antes de TIMEOUT (2h) |
| `warmup_bars` | 60 | Barras iniciales ignoradas para estabilizar VR y CVD |
| `vr_window` | 50 | Ventana de barras para calcular la media de volumen (VR) |
| `cvd_slope_win` | 20 | Ventana OLS para calcular la pendiente del CVD |

**Gates activos en este run:**
- `use_slope_gate = False` — pendiente CVD desactivada
- `require_diverge = True` — CVD debe divergir del precio en el spike

---

## Features disponibles por señal

| Columna | Tipo | Descripción |
|---|---|---|
| `ts_ms` | int | Timestamp de entry en epoch ms |
| `direction` | str | `Long` / `Short` |
| `entry` | float | Precio de cierre de la barra de entry |
| `stop` | float | Stop loss = spike_extreme ± buffer 0.08% |
| `target` | float | Take profit = entry ± 2× risk |
| `rr` | float | Risk/reward ratio (siempre ≥ 2.0) |
| `range_high` | float | Máximo del rango de acumulación |
| `range_low` | float | Mínimo del rango de acumulación |
| `range_bars` | int | Número de barras en el rango |
| `spike_dir` | str | Dirección del spike manipulador (`Up` / `Down`) |
| `vr_spike` | float | Volume ratio en la barra del spike |
| `vr_entry` | float | Volume ratio en la barra de entry |
| `delta_spike` | float | bar_delta en la barra del spike (positivo = compra neta) |
| `cvd_slope` | float | Pendiente OLS del CVD acumulado en ventana de 20 barras |
| `session` | str | Sesión de mercado: `Asia` / `London` / `LondonNY` / `NewYork` |
| `open_bar` | int | Índice de barra en que se abrió la señal |
| `exit` | str | `TARGET` / `STOP` / `TIMEOUT` / `OPEN` |
| `result_r` | float | Resultado en R (+2.0 target, -1.0 stop, 0.0 timeout) |
| `exit_bar` | int | Índice de barra en que cerró el trade |

**Features NO disponibles** (requieren datos de microestructura en vivo):
- `obi_l5` — order book imbalance top 5 niveles
- `absorption_long/short` — footprint tick a tick
- `liq_ratio` — ratio de liquidaciones
- `vpin` — volume-synchronized probability of informed trading

---

## Resultados (90 días, slope gate OFF)

| Métrica | Valor |
|---|---|
| Total señales | 54 |
| Cerradas | 49 |
| Timeout | 5 |
| Win rate | 34.7% (17W / 32L) |
| Avg R | +0.041 |

### Por sesión

| Sesión | n | WR% | Avg R |
|---|---|---|---|
| London | 22 | 54.5% | **+0.636** |
| NewYork | 4 | 50.0% | **+0.500** |
| Asia | 21 | 14.3% | **-0.571** |
| LondonNY | 2 | 0.0% | -1.000 |

### Por sesión + dirección

| Sesión + Dir | n | WR% | Avg R |
|---|---|---|---|
| London Long | 16 | 62.5% | **+0.875** |
| NewYork Short | 2 | 100.0% | **+2.000** |
| Asia Short | 5 | 40.0% | +0.200 |
| London Short | 6 | 33.3% | 0.000 |
| NewYork Long | 2 | 0.0% | -1.000 |
| LondonNY Short | 2 | 0.0% | -1.000 |
| **Asia Long** | **16** | **6.2%** | **-0.812** |

### Observaciones

- **Asia Long** es el peor segmento: 1 win en 16 trades. Filtrar este caso eliminaría la mayoría del drawdown.
- **London Long + London/NY Short** concentran el edge real.
- Con slope gate activado (`use_slope_gate=True`) → 0 señales en 14d, el gate es demasiado restrictivo con los parámetros actuales.
- `delta_spike` positivo alto (compra neta) en spikes Down → señal de Long, pero en Asia no es predictivo.
- `vr_spike` no discrimina bien entre sesiones — valores altos aparecen tanto en trades ganadores como perdedores.

---

## Próximos pasos sugeridos

1. Filtrar `Asia Long` — elimina el 80% del drawdown
2. Calibrar `dist_cvd_slope` a un umbral menor (ej. 3.0–5.0) en vez de 10.0 para re-activar el slope gate
3. Agregar datos de más símbolos (ETHUSDT, SOLUSDT) para aumentar n
4. Cuando haya datos de microestructura: incorporar `obi_l5` como gate adicional en London Long
