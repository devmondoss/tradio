# CRT — Candle Range Theory (Research Log)

> Estado: **investigado, no desplegado**. Edge real pero frecuencia insuficiente para servicio propio. Candidato a agregar como señal adicional en SC3 paper.

---

## Qué es

Modelo AMD (Accumulation → Manipulation → Distribution) sobre la vela H1 anterior como rango de referencia.

**Setup de 3 pasos:**
1. Vela H1 cierra → define `[h1_high, h1_low]`
2. En M5: precio supera `h1_high` (o `h1_low`) con el wick, pero **cierra de vuelta dentro** → sweep
3. Precio revierte al extremo opuesto del rango

**Entry maker** en el nivel barrido (`h1_high` para bearish, `h1_low` para bullish). Stop más allá del wick del sweep.

---

## Diferencia vs SC3 y Liquidity A

| | SC3 | Liquidity A | CRT |
|--|-----|------------|-----|
| Nivel de entrada | VP (POC/VAH/VAL/PDH/PDL) | VP igual | H1 high/low anterior |
| Condición | Absorción: delta adverso + sostiene | Precio llega al nivel | Sweep: wick rompe + cierra dentro |
| TF señal | M5 | M15 | M5 |
| Target | Estructural 3R | Parcial TP1 + breakeven | Estructural o extremo opuesto H1 |

CRT detecta **falsos breakouts de rango H1**. SC3 detecta **absorción en niveles VP**. Setups independientes, baja correlación esperada.

---

## Filtros validados (regla dura IS+OOS en los 3 activos)

```python
# Config canónica CRT (mejor balance calidad/frecuencia)
CRT_CANONICAL = dict(
    htf_filter   = True,   # H1 EMA20 OR H4 EMA20 alineado OR vr>3
    fvg_entry    = True,   # confirmar en barra siguiente al sweep (no en el sweep mismo)
    vr_min       = 1.5,    # spike de volumen en el sweep
    stop_buf     = 0.3,    # stop = wick_extreme + 0.3*ATR
    min_sweep_atr= 0.0,    # 0.2 mejora ETH pero reduce n BTC a 28
    delta_confirm= False,  # rompe ETH en todos los configs
    mgmt         = "fade",
)
```

**ATR filter**: `atr > mediana(500)` — siempre activo en `run_setup`.

---

## Métricas OOS validadas

**Motor**: `backtest/_crt.py`. OOS desde `2026-03-01`. M5, fee honesto, salida M1.

### Config canónica: `+htf +fvg_entry`

| Símbolo | n total | n OOS | IS avgR | OOS avgR | WR | DD |
|---------|---------|-------|---------|----------|----|----|
| BTCUSDT | ~189 | 35 | +0.576 | **+0.931** | 57% | 12% |
| ETHUSDT | ~150 | 53 | +0.800 | **+0.780** | 52% | 9% |
| SOLUSDT | ~140 | 49 | +0.700 | **+0.625** | 55% | 4% |
| **Portfolio** | | **137** | | **+0.778** | | |

Frecuencia: ~0.3/día por activo, ~1.1/día portfolio combinado.

### Config alta frecuencia: `+htf struct` (sin fvg_entry)

| Símbolo | n OOS | OOS avgR | WR | DD | n/día |
|---------|-------|----------|----|----|-------|
| BTCUSDT | 163 | +0.322 | 46% | 11% | 1.3 |
| ETHUSDT | 179 | +0.686 | 50% | 5% | 1.4 |
| SOLUSDT | 162 | +0.440 | 49% | 11% | 1.3 |
| **Portfolio** | **504** | **+0.483** | | | **4.0** |

---

## Sweep de variantes testeadas

| Variante | BTC OOS | ETH OOS | SOL OOS | Avg | Pasa | Nota |
|---------|---------|---------|---------|-----|------|------|
| base vr>=1.5 | +0.091 | +0.400 | +0.090 | +0.194 | ✅ | débil |
| base vr>=2.0 | +0.076 | +0.340 | +0.360 | +0.259 | ✅ | |
| +htf struct | +0.322 | +0.686 | +0.440 | +0.483 | ✅ | frecuente |
| +htf h1_tgt | +0.257 | +0.642 | +0.375 | +0.425 | ✅ | |
| h1_tgt +delta | +0.467 | +0.107 | +0.620 | +0.398 | ✅ | ETH borderline |
| +delta (cualquier) | — | **negativo** | — | — | ❌ | rompe ETH siempre |
| +htf+delta | +0.539 | -0.115 | +0.472 | — | ❌ | |
| +sweep>=0.2 +htf | +0.372 | +0.671 | +0.496 | +0.513 | ✅ | |
| +delta BTC+SOL +htf | +0.539 | +0.686 | +0.472 | +0.566 | ✅ | BTC n=34 thin |
| **+htf +fvg_entry** | **+0.931** | **+0.780** | **+0.625** | **+0.778** | **✅** | **canónica** |
| +htf +fvg +sweep0.2 | +0.858 | +1.138 | +0.620 | +0.872 | ✅ | BTC n=28 thin |
| +filter_50_vp | +0.286 | +0.031 | +0.405 | +0.240 | ✅ | mata ETH |

---

## Lo que NO funciona (cerrado)

| Idea | Resultado | Motivo |
|------|-----------|--------|
| Delta confirm universal | ETH OOS negativo en todas las configs | Microestructura ETH distinta: sweeps con delta adverso son breakouts reales, no fakes |
| filter_50_vp | ETH colapsa a +0.031 | VP no está realmente en el 50% del rango H1 con frecuencia, filtro over-agresivo cuando se activa |
| VP confluence (H1 nivel cerca de POC/VAH) | BTC baja de +0.322 → +0.158 | CRT y VP son setups distintos por diseño — no hay razón para que coincidan |
| H1 range size filter (>1 ATR) | Sin cambio | Los rangos H1 casi siempre son >1 ATR en perps, filtro nunca se activa |
| Trail management | WR 25-30%, DD 18-41% | CRT es mean-reversion, trail destruye el edge |
| Taker entry | No testeado, descartado por analogía con SC3 | Selección adversa |

---

## Por qué fvg_entry mejora tanto

El setup clásico (señal en la barra del sweep mismo) incluye sweeps que inmediatamente continúan como breakouts reales. Con `fvg_entry=True`, la señal se genera en la **barra siguiente al sweep**:

- Si el precio continuó en la dirección del sweep → no hay fill maker en el nivel (breakout real, se auto-filtra)
- Si el precio se quedó dentro del rango H1 → hay fill (sweep genuino, se toma)

La barra de confirmación actúa como un filtro de calidad gratis: solo entra en sweeps que ya demostraron reversión.

---

## Comparación con otras estrategias

| Estrategia | OOS avg | n/día | Estado |
|-----------|---------|-------|--------|
| SC3 canónico | +0.531 | 3.4 | Paper live 3 activos |
| Liquidity A (fade) | +1.24/+1.46/+0.76 | ~2 | Paper live 3 activos |
| IFVG+HTF+dist | +1.97/+1.91/+1.87 | ~0.5 | Pendiente deploy Rust |
| **CRT +htf +fvg** | **+0.778** | **~1.1** | No desplegado |
| CRT +htf struct | +0.483 | ~4.0 | No desplegado |

CRT con fvg_entry supera SC3 en calidad por trade (+0.778 vs +0.531) pero tiene frecuencia muy baja para servicio dedicado. La versión de alta frecuencia (base +htf) es comparable a SC3 pero sin ventaja clara.

---

## Infraestructura

**Backtest**: `backtest/_crt.py`

Funciones principales:
- `gen_crt(symbol, ...)` — generador de señales M5
- `run_crt(symbol, ...)` — runner completo (carga datos, corre backtest, devuelve stats)
- `_load_h1_ohlc(symbol)` — carga H1 OHLC para definir rango CRT

Dependencias:
- `_scalp.py`: `load`, `load_m1_exit`, `run_setup`, `stats`, `_load_htf`
- `_listas2.py`: `struct_target`, `L2`

**No tiene servicio paper** — la infraestructura sería idéntica a `apps/sc3-paper/` si se desplegara.

---

## Próximos pasos sugeridos (cuando se retome)

1. **Portfolio CRT+SC3**: testear `gen_sc3x` + `gen_crt` corriendo juntos en el mismo run_setup. Medir si el portfolio combinado mejora frecuencia sin canibalizar edge.

2. **Ajustar ETH delta**: explorar si delta_confirm con un umbral diferente (ej. `delta < -X` en vez de `< 0`) funciona en ETH.

3. **Deploy como señal adicional en sc3-paper**: agregar `gen_crt` al `main.py` de SC3 paper como señal complementaria. Mismo servicio, misma tabla Supabase, mismo riesgo.

4. **Validar con IFVG**: CRT fvg_entry y la IFVG son conceptualmente similares (sweep → fill en zona de desequilibrio). ¿Son las mismas señales con distinto detector?

---

## Fuente de investigación

Webinar "CRT + Orderflow" — Subdimi / canal ATAS (2026-06-30). Conceptos clave del video aplicados:
- Entry en barra de confirmación post-sweep → `fvg_entry=True` (mejora +61% OOS)
- Filtro 50% con VP → `filter_50_vp=True` (descartado, mata ETH)
- Delta adverso en sweep → `delta_confirm=True` (descartado, rompe ETH)
- Volume spike (VR) → ya estaba implementado como `vr_min`
