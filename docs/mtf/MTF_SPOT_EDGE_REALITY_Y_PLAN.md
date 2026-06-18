# MTF Spot — Realidad del Edge y Plan (2026-06-18)

> Sesión de auditoría profunda. Resume QUÉ encontramos (el edge real tras corregir
> fees), POR QUÉ el sistema no era lo que decían los números, y QUÉ haremos después
> de subir el AvgR. Incluye inventario de features para saber si nos alcanzan.
>
> Docs relacionados: `MTF_SPOT_SIZING_AUDIT.md`, `../MTF_SPOT_SHORTS_SPEC.md`, memoria `project-bybit-fees`.

---

## 1. Hallazgos de la sesión (en orden de impacto)

### 1.1 Bug de fees — inflaba el edge ~200×
`mtf_basics.py` / `mtf_longs.py` restaban `entry_risk * 0.0007` (fee sobre el RIESGO).
La fee real es sobre el **NOTIONAL**: `fee_r = FEE_RT * entry/dist = FEE_RT / stop_pct`.
- **Corregido:** `gross_r - FEE_RT*ep/dist`, `FEE_RT = 0.0020` (round-trip spot básico real).
- Antes la fee costaba 0.0007R; la real cuesta **~0.42R por trade** a stop 0.5%.

### 1.2 Fee real Bybit (verificado en doc oficial)
- **No-VIP spot: maker 0.1% / taker 0.1%** → round-trip a market = **0.20%**.
- Fee sobre el activo comprado. Órdenes no llenadas/canceladas = gratis.
- Maker = taker en básico (límite no baja fee hasta VIP). Token MNT: −25% → 0.15%.
- Futuros (perp) taker ≈ 0.055% → round-trip **0.11%** (la mitad que spot).

### 1.3 El edge real es FINO pero positivo (shorts)
Desglose shorts @ fee futuros 0.11% (result_r neto):
```
avg GANANCIA = +1.24R   avg PÉRDIDA = −1.21R   WR = 52.5%
AvgR neto    = +0.075R  (= 0.525×1.24 − 0.475×1.21, cuadra exacto, NO es bug)
ganancia neta media/trade = 0.0376% del notional
```
Es una **moneda al aire cargada 2.5pp**. Ganancia y pérdida casi simétricas → el edge
vive solo en el WR ligeramente >50%. Real, pero frágil.

### 1.4 PnL real por venue (config actual, $500)
| | Spot 0.20% | Spot+MNT 0.15% | Futuros 0.11% |
|--|--|--|--|
| **Shorts** (compounding 2%) | $500→**$25** (−95%) | ~breakeven | $500→**$2,423** (+385%) |
| **Longs** (TotalR año) | −237R | −120R | **−25R** (sigue negativo) |

- **Spot, cualquier dirección: pierde.** El "$104K" era 100% el bug de fees.
- **Futuros rescata solo SHORTS.** Longs son negativos incluso a 0.11%.
- El número se siente chico solo porque la base es $500. En % (+385%) es enorme; escala lineal por capital.

### 1.5 Restricciones que descubrimos
- **Spot no apalanca ni shortea.** Shorts requieren spot-margin (con interés) o futuros.
- **Spot + $500 + stop 0.5% topa el riesgo a ~0.5%/trade**, no 2% (posición no puede exceder el capital sin leverage).
- **Capital ocioso:** una posición a la vez, ~2.7 trades/día → capital parado gran parte del tiempo.
- **El edge descansa en pocos meses** (marzo 2026 = la mitad del año). Fragilidad alta.
- **El conteo de trades ya está aprovechado.** Más trades no ayuda; cada uno aporta ~0.04% notional. La palanca es **R por trade**, no frecuencia.

### 1.6 Sizing (de la sesión previa, sigue vigente)
Score sizing NO mejora el edge (es apalancamiento). Escala conservadora `[1,1,1,1,1.5]`
(boost solo sc4, único bucket robusto). Target override eliminado (overfit a OOS). Ver `MTF_SPOT_SIZING_AUDIT.md`.

---

## 2. Plan — DESPUÉS de subir el AvgR

El objetivo central: **engrosar el AvgR sin sacrificar volumen**, atacando las SALIDAS
(las entradas parecen maduras). Pasos en orden:

### Paso 1 — Subir AvgR vía salidas (✅ HECHO para SHORTS — `_shorts_exits.py`)
**Resultado:** el CVD exit + regime target cortaban ganadores a ~1.3R. La solución NO
fue trailing/BE/parcial (todos fallaron: cortan ganadores o generan scratch), sino
**target FIJO 2.5R sin CVD exit**:
- AvgR neto SHORTS @ futuros: **+0.072 → +0.121** (≈duplicado), IS +0.126 ≈ OOS +0.121 (robusto, no overfit).
- avg ganancia 1.25R → ~2.0R. WR baja a 40% pero compensa. Compounding flat $1,619 → $2,100.
- **Aplicado a `mtf_basics.py`:** `TARGET_R=2.5`, regime target eliminado, CVD exit eliminado.
- Pendiente: mismo trabajo en LONGS (`mtf_longs.py` sigue con regime+CVD).

### Paso 2 — Re-validar SHORTS sobre datos de FUTUROS
- Toda la calibración es sobre order book de **spot**. Futuros tiene precio ≈ igual pero
  microestructura distinta (OBI/depth/CVD del libro de perp).
- Conseguir dataset de **futuros BTC Bybit** (mismo formato: tick + ob200) y confirmar que el +77R se sostiene.
- Sin esto, "irse a futuros" es una hipótesis, no un hecho.

### Paso 3 — LONGS: DESCARTADOS (analizado — `_longs_exits.py`)
**Veredicto: longs NO tienen edge robusto net de fees.** El análisis de salidas lo confirmó:
- **Todas las variantes son negativas en IS** (BASELINE IS −0.036, fijo2R+CVD IS −0.052). Los OOS positivos (+0.03/+0.047) son chicos y sin respaldo IS → ruido, no edge.
- **Shorts y longs NO son simétricos:** shorts corren (target fijo sube AvgR), longs **revierten** (fijo 2.5R sin CVD = IS −0.124). El CVD exit *ayudaba* a longs porque salía antes de la reversión.
- No es problema de salidas (ninguna los rescata) — las **entradas** long carecen de ventaja > 0.11% fee.
- Consecuencia: **muere la idea "shorts+longs en paralelo"**. Solo shorts. `mtf_longs.py` sin cambios, parado.
- Reabrir solo si aparece una fuente de edge de ENTRADA nueva para longs.

### Paso 4 — Bajar la fee (estructural)
- Pagar fees con **MNT** (−25%).
- Apuntar a **VIP** (maker → cerca de 0%) usando límites donde el spec lo permita.
- En futuros, evaluar entradas **maker/límite** (recordar: límite no llenado = gratis).

### Paso 5 — Recién entonces: paper live → Rust → real
- Portar el core robusto (no el overfit) a `mtf_spot_detector.rs`, re-correr parity.
- Paper live, comparar fills reales vs backtest. Si cuadra, $500 real para validar barato.

---

## 3. ¿Tenemos los features necesarios? (inventario de 81 columnas)

### 3.1 Para subir AvgR (Paso 1) — SÍ, tenemos todo
Trailing/runner/BE solo necesitan camino de precio + distancias:
- `open/high/low/close`, `atr14` (trail dist), `cvd_slope`, `cvd_consec_pos/neg`,
  `obi10_mean`, `vpin`, `regime`, `dz`. **Suficiente. No falta nada para optimizar salidas.**

### 3.2 Para refinar calidad de entrada (si el AvgR no sube bastante)
Microestructura rica ya disponible — explorar como filtros de calidad (no de hora):
- Absorción: `abs_ask`, `abs_bid`, `cvd_div`, `stacked_imb`, `big_trade_bearish/bullish`
- Libro: `ask_wall`, `bid_wall`, `thin_above`, `thin_below`, `obi_range`, `spread_mean`, `near5_ask/bid`, `max_ask5/bid5`
- Estructura: `sweep_confirmed`, `pdh_sweep`, `equal_high_sweep`, `london_sweep_h`, `fib_ote`, `ote_62/79`, `ote_rejection`, `vp_lvn_below`, `vp_poc`
- Régimen: `vr`, `vpin`, `tight_range`, `bars_since_low_vr`

### 3.3 Lo que NO tenemos (habría que INVESTIGAR/complementar)
- **Datos de futuros** (OI, funding, liquidaciones) — clave si migramos a futuros. El dataset
  actual es spot puro; OI/funding **no existen** aquí (por eso el spec viejo de futuros los usaba y SPOT no).
- **Profundidad de libro multinivel en el tiempo** — solo tenemos near5/max5, no las 200 capas completas por barra.
- **Distribución de tamaño de trades** (más allá de big_trade flags) — útil para detectar absorción institucional fina.
- **Slippage / spread real de ejecución** — el backtest entra a `close`; falta modelar el spread de cruce.

### 3.4 Veredicto
- **Para el Paso 1 (subir AvgR vía salidas): tenemos todo.** Arrancar ya.
- **Para refinar entradas: tenemos features de sobra**, sin investigar nada nuevo.
- **Para migrar a futuros en serio: FALTA dataset de futuros** (precio + libro perp, idealmente con OI/funding). Eso es lo único que requiere "complementar lo construido" con datos nuevos.

---

## 4. Estado y archivos

- `backtest/mtf_basics.py` — shorts, **fee corregida** + sizing conservador.
- `backtest/mtf_longs.py` — longs, **fee corregida** + sizing conservador.
- `backtest/_audit_v7.py` — auditoría sizing/override.
- `backtest/_longs_edge.py` — análisis MFE de salidas.
- `backtest/_recalibrate_fees.py` — barrido stop×target×fee (demostró que spot no sobrevive).

**No trading real ni Rust hasta:** subir AvgR (Paso 1) + re-validar shorts en datos de futuros (Paso 2).
