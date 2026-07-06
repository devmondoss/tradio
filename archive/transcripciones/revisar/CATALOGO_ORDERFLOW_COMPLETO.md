# CATÁLOGO EXHAUSTIVO DE ORDERFLOW — 65 CONCEPTOS

> Extraído de las 20 transcripciones en `transcripciones/`. Fuente más densa: `Delta_Range_Reversal_v3_Unificado.md`.

---

## CATEGORÍA 1: MICROESTRUCTURA

---

### 1. FOOTPRINT CHART
**Qué es:** Vista "rayos X" de cada vela — muestra el volumen bid/ask ejecutado en cada nivel de precio.

**Cómo funciona:** Lado izquierdo = ventas de mercado (market sell), lado derecho = compras de mercado (market buy). El tick size agrupa niveles.

**Ejemplo BTC:** En M5, una vela en 68.000 muestra 1.200 contratos en el ask (compras) vs 300 en el bid (ventas) en el nivel 68.000. Solo mirando la vela verde normal no lo verías.

**No es señal por sí solo** — es el sustrato donde se detectan el resto de señales.

**Convergencias:** Delta, Open Interest, Imbalances, Absorción, Big Trades, POC de vela, Stacked Imbalances, Finish/Unfinish Action.

---

### 2. DELTA
**Qué es:** `Delta = compras_agresivas − ventas_agresivas`. Puede ser por vela, por nivel (row del footprint), o acumulado (CVD).

**Señal:**
- Delta positivo fuerte → compras agresivas
- Delta negativo fuerte → ventas agresivas
- **Regla crítica de cripto:** En BTC el delta suele ir en **contra** del precio. Delta positivo fuerte en máximos = compradores mal posicionados → bajista. En índices (ES/NQ) el delta sí va a favor del precio.

**Normalización:** DZ (Delta Z-score) = `(delta_vela − media(delta, N)) / stdev(delta, N)`, N=50 en M5.

**Ejemplo BTC:** BTC en 69.000 (máximo histórico), 5 velas consecutivas con delta +11M cada una, pero el precio no avanza. Los compradores agresivos quedaron atrapados → señal bajista.

**Convergencias:** Volume Profile (delta del perfil coloreado), CVD (delta acumulado), Absorción, Big Trades, OI, Footprint.

---

### 3. DELTA MÍNIMO / DELTA MÁXIMO (intracandle)
**Qué es:** El pico más negativo y más positivo que alcanzó el delta *dentro* de una vela (no el valor de cierre). La diferencia entre el extremo intracandle y el cierre = absorción cuantificada.

**Ejemplo BTC:** Vela en 65.000: delta mínimo = -26M, delta de cierre = -18M. Los 8M de diferencia fueron absorbidos por órdenes límite de compra. Absorción real medible.

**Convergencias:** Absorción, Finish Action, POC en mecha, Traders Atrapados, Big Trades.

---

### 4. OPEN INTEREST (OI)
**Qué es:** Total de posiciones abiertas en futuros/perpetuos sin cerrar. Solo aplica en derivados (no en spot).

**Señales:**
- OI baja en un sweep del low → son stops que saltan, no nuevas posiciones → fake breakdown → setup long
- OI sube con rompimiento del rango → nuevas posiciones → rompimiento real → no fade
- OI sube tras reclaim del extremo (`>= 1%`) → nuevas posiciones a favor → confirma sesgo
- Umbral de drop en barrida: `oi_drop_on_sweep_min = -1.5%`

**Ejemplo BTC:** Precio rompe 64.000, OI cae -1.8% → se activaron stops de longs → manipulación. El precio reclama 64.000 en 2 velas → OI sube +1.2% → nuevas posiciones largas iniciando la subida real.

**Convergencias:** Liquidaciones (crypto), Delta, Big Trades, CVD, AMD (fase manipulación), rompimiento real vs. absorción.

---

### 5. ABSORCIÓN
**Qué es:** Órdenes límite pasivas (institucionales/MM) que absorben el flujo agresivo sin dejar que el precio avance. Delta agresivo fuerte + precio que no se mueve = alguien grande del lado contrario.

**Fórmula del Absorption Score:**
```
desplazamiento = (cierre - apertura) / rango_vela   # de -1 a +1
AS_long  = max(-DZ, 0) × (1 - max(desplazamiento, 0))
AS_short = max( DZ, 0) × (1 - max(-desplazamiento, 0))
```

- AS_long >= 1.5 (mínimo) / >= 3.0 (fuerte) → ventas absorbidas → sesgo long potente
- AS_short >= 1.5 / >= 3.0 → compras absorbidas → sesgo short
- **Peso en el scoring: 22/100 (el más alto del sistema)**

**Ejemplo BTC:** BTC en Range Low 63.500. Vela con DZ = -2.1 (ventas masivas), precio solo baja 0.1% (casi no se mueve). AS_long = 2.1 × 0.9 ≈ 1.89 → absorción confirmada → vendedores mal posicionados → entrada long.

**Convergencias:** Traders Atrapados, POC en mecha, Finish Action, Stacked Imbalances, Big Trades, Liquidaciones, CVD divergencia, Delta flip.

---

### 6. TRADERS ATRAPADOS
**Qué es:** Participantes que entraron en la dirección equivocada. Cuando el precio se mueve en contra, se ven forzados a cerrar → aceleran el movimiento contrario.

**Cómo detectar:**
- Big Trades compradores en máximos del rango + vela que cierra por debajo del POC + CHoCH local → compradores atrapados → sesgo short
- Ventas grandes en mínimos + vela que cierra por encima → vendedores atrapados → sesgo long

**Patrón más potente:** Liquidaciones + Traders Atrapados tardíos en el mismo nivel → doble combustible en la dirección contraria.

**Ejemplo BTC:** Precio sube a 70.000, hay 3 Big Trades de +500 BTC en la mecha. El precio cierra en 69.500. En las siguientes 2 velas BTC rompe 69.000. Esos compradores de 70.000 están atrapados → cada vela bajista los liquida → cascada.

**Convergencias:** Absorción, Big Trades, POC en mecha, SFP, Naked POC, Liquidaciones, CHoCH.

---

### 7. IMBALANCES (Desequilibrios en Footprint)
**Qué es:** Diferencia extrema entre compras y ventas en niveles adyacentes (diagonal) del footprint. El precio se movió tan rápido que una de las partes no tuvo contraparte suficiente.

**Cómo se calcula:** Si bid del nivel N >= 300% del ask del nivel N+1 → imbalance comprador (verde). Viceversa → imbalance vendedor (rojo).

**Función:** Zonas de atracción, soporte/resistencia de microestructura. No son señal de entrada sola — marcan donde el precio tenderá a volver para "completar".

**Convergencias:** Stacked Imbalances (3 niveles consecutivos), FVG (versión ICT), Order Blocks, Vacíos de volumen.

---

### 8. STACKED IMBALANCES (Imbalances Apilados)
**Qué es:** 3 o más niveles consecutivos de imbalances en la misma dirección. Indica aceleración institucional fuerte.

**Parámetros:** `stacked_imbalance_ratio = 3.0` (300%), `stacked_imbalance_levels = 3`.

**Señal:**
- Stacked imbalances alcistas → zona de alto interés comprador → soporte fuerte, objetivo de precio al corregir
- Stacked imbalances bajistas → resistencia fuerte

**Ejemplo BTC:** BTC rompe 66.000 con stacked imbalances alcistas en 3 niveles consecutivos + Big Trades compradores → rompimiento real, no fade.

**Convergencias:** Big Trades (en rompimiento), Order Blocks (confirma calidad del OB), FVG, Rompimiento real.

---

### 9. FINISH ACTION
**Qué es:** Nivel en el extremo de una mecha con **0** en bid o ask → el flujo se agotó completamente en ese nivel. No hay más interés en continuar en esa dirección.

**Señal:**
- Finish Action en el mínimo de la vela (0 en ventas) → agotamiento bajista → señal alcista adicional
- Finish Action en la parte alta (0 en compras) → agotamiento alcista → señal bajista

**Es señal de confluencia, nunca señal primaria de entrada.**

**Convergencias:** Absorción (confirma agotamiento del flujo), POC en mecha, Drenaje de delta, VR cayendo.

---

### 10. UNFINISH ACTION
**Qué es:** Lo opuesto al Finish Action. Nivel extremo con actividad sin resolver → el precio tiene trabajo pendiente → actúa como imán.

**Señal:**
- Unfinish Action arriba → imán alcista
- Unfinish Action abajo → imán bajista

**Red Flag crítica:** Si hay Unfinish Action en dirección contraria al trade planificado → invalida la reversión. Si hay Unfinish Action abajo y queremos un long, hay un imán que puede llevar el precio ahí.

**Convergencias:** Naked POC (concepto hermano), Finish Action (opuesto), POC de vela.

---

### 11. BIG TRADES
**Qué es:** Ejecuciones significativamente mayores al promedio. Se visualizan como burbujas en el gráfico.

`size_ratio = print_size / SMA(print_size, 100)`. Señal fuerte: `size_ratio >= 10`.

**Señal depende 100% de la UBICACIÓN:**
- Big Trade comprador en la **mecha de máximos** + cierre por debajo = comprador atrapado → bajista
- Big Trade vendedor en la **mecha de mínimos** + precio no baja = vendedor atrapado → alcista
- Big Trade en **rompimiento** en dirección del break → rompimiento real → operar a favor

**Pregunta lógica clave:** ¿Tiene sentido que una institución compre en un máximo histórico absoluto? No → son traders retail, no institucionales.

**Ejemplo BTC:** BTC en ATH 73.800, Big Trade de 1.200 BTC en la mecha superior. Los institucionales no compran en ATH — esos son longs retail atrapados. Setup short.

**Convergencias:** Traders Atrapados, Absorción, CHoCH, AMD (redistribución), VWAP (target post-Big-Trade), Rompimiento real.

---

### 12. CLUSTER DE VOLUMEN (dentro de vela)
**Qué es:** La zona dentro del footprint con mayor volumen ejecutado = POC de la vela individual. Es "donde está el bloque de órdenes real" dentro de un Order Block.

**Aplicación:** No marcar la vela entera de un OB — marcar el cluster exacto. Ahí va la orden límite.

**Para dos OBs consecutivos:** Hacer merge del cluster de ambas velas → encontrar el bloque de mayor volumen real → el precio reacciona exactamente en ese nivel combinado.

**Convergencias:** Volume Profile (POC, HVN), Order Blocks, Big Trades (suelen estar en el cluster).

---

### 13. VELAS DE VOLUMEN (Volume Candles / Trend Reversal)
**Qué es:** Velas que se forman cuando pasa un volumen predeterminado (no por tiempo fijo).

**Configuración cripto:** 4426 (más señales, entrada temprana) y 9664 (similar a M5, más confirmación). Índices: ratio 3 a 1.

**Ventaja:** CHoCH en velas de volumen confirma que hay interés real de volumen detrás del movimiento, no solo que pasó tiempo.

**Combinación ganadora:** CHoCH en velas de volumen + CHoCH en velas de tiempo = entrada de altísima probabilidad.

**Convergencias:** M1/M5 velas de tiempo (combinación), CHoCH/BMS en footprint, Confirmación de entrada.

---

### 14. DRENAJE DE DELTA (Fading Delta)
**Qué es:** El delta acumulado deja de incrementar pese a que el precio sigue moviéndose → agotamiento del flujo agresivo en esa dirección.

**Detección:**
- Precio hace nuevos máximos pero el delta positivo es cada vez menor → drenaje alcista → señal bajista
- Precio en mínimos pero el delta negativo se modera → drenaje bajista → señal alcista
- También visible en CVD: la línea deja de hacer nuevos extremos mientras el precio los sigue haciendo

**Ejemplo BTC:** BTC en 69.000, 70.000, 71.000 (nuevos máximos). Delta de las velas: +500M, +320M, +180M. Drenaje claro — cada rally tiene menos fuerza compradora. Posible techo.

**Convergencias:** CVD (divergencia = versión macro del drenaje), Finish Action, VR < 0.7 (confirmación de drenaje), Absorción.

---

### 15. POC DE VELA (intracandle Point of Control)
**Qué es:** Nivel con mayor volumen dentro de una sola vela. Indica el centro de gravedad de las órdenes de esa vela específica.

**Señal:**
- POC en mecha baja (`poc_wick_ratio >= 0.5`) → volumen concentrado abajo → absorción de ventas → alcista
- POC en mecha alta → absorción de compras → bajista
- POC en el centro → sin sesgo claro

**Patrón Reversal ATAS:** Vela cierra + delta positivo + cierre por encima del POC → orden límite un tick sobre el POC → señal alcista.

**Convergencias:** Absorción, Traders Atrapados, Cluster de volumen, Reversal Pattern, Absorción Pattern.

---

### 16. REVERSAL PATTERN (ATAS)
**Qué es:** Patrón específico de ATAS. La vela cierra con delta positivo y el cierre está por encima del POC intracandle.

**Señal:** Exclusivamente alcista. Entrada = orden límite un tick sobre el POC tras el cierre de la vela.

**Convergencias:** POC de vela, Delta, Absorción, Nivel de soporte/estructura.

---

### 17. ABSORCIÓN PATTERN (ATAS)
**Qué es:** Volumen 3-10x la media + números grandes en extremos del footprint + cierre contrario al movimiento de la mecha.

- Absorción bajista (mecha baja + cierre arriba del POC) → vendedores absorbidos → alcista
- Absorción alcista (mecha alta + cierre abajo del POC) → compradores absorbidos → bajista

**Convergencias:** POC de vela, Delta Mínimo/Máximo, Big Trades, Stacked Imbalances.

---

### 18. LIQUIDACIONES (Crypto)
**Qué es:** Cierres forzados de posiciones en futuros/perpetuos cuando el precio toca el precio de liquidación (margin call).

`liq_ratio = liq_usd_en_extremo / SMA(liq_usd, 50)`.
- Significativo: `liq_ratio >= 3.0`
- Fuerte: `liq_ratio >= 8.0`

**Regla crítica:** **Liquidaciones sin absorción = NO trade. Liquidaciones + absorción + reclaim = setup válido.**

**Ejemplo BTC:** Precio barre 63.000 (SSL), liq_ratio = 5.2, OI cae -2.1%, delta negativo masivo absorbido (AS_long = 2.3), precio reclama 63.000 en 2 velas. Setup long completo.

**Convergencias:** OI (confirma si son stops o nuevas posiciones), Absorción, Traders Atrapados, Big Trades en extremo, Reclaim del extremo.

---

### 19. DOM / DEPTH OF MARKET
**Qué es:** El libro de órdenes en tiempo real mostrando las órdenes límite pendientes a cada nivel (resting orders).

**Advertencia:** Las órdenes del DOM pueden ser iceberg (parciales), spoofing (se retiran antes de ejecutarse) o genuinas. No suficiente por sí solas — confirmar con footprint y Big Trades.

**Convergencias:** Heatmap (representación histórica del DOM), Absorción (el DOM absorbe las market orders), Big Trades, Liquidity Imbalance.

---

### 20. HEATMAP (DOM histórico / Mapa de calor de liquidez)
**Qué es:** Historial del libro de órdenes → dónde se concentraron las resting orders a lo largo del tiempo. Los colores más intensos = mayor concentración de liquidez acumulada.

`LI = (liq_resting_bid - liq_resting_ask) / (bid + ask)`. Relevante: `abs(LI) >= 0.35`.

**Uso en spec v3:** Evaluar el heatmap ANTES de que el precio llegue al extremo (Fase 2 — pre-llegada).

**Señal:**
- Clusters de liquidez debajo del precio → muro de compra → soporte con liquidez real
- Clusters encima → muro de venta → resistencia

**Convergencias:** DOM, Big Trades (los muros del heatmap son los que absorben), Equal Highs/Lows (ICT), Stop Loss clusters.

---

## CATEGORÍA 2: VOLUMEN Y PERFIL

---

### 21. VOLUME PROFILE
**Qué es:** Distribución horizontal del volumen a cada nivel de precio durante un período. No es temporal — es espacial. La herramienta central de Action Market Theory.

**Tipos:**
- **SVP** (Session): perfil de la sesión del día
- **PVP** (Periodic): diarios, semanales, mensuales automáticos
- **FRVP** (Fixed Range): manual sobre rango elegido libremente
- **Composite/Merge**: unir perfiles solapados

**Señal:**
- Value Area progresando hacia arriba semanalmente → tendencia alcista
- Perfiles solapados sin desplazamiento → rango (Balance)

**Convergencias:** POC, VAH/VAL, HVN/LVN, Naked POC, Delta Profile, VWAP, Market Profile/TPO, AMD.

---

### 22. POC (Point of Control)
**Qué es:** Precio con mayor volumen del perfil. Centro de gravedad → el precio tiende a reaccionar al llegar al POC previo.

**Regla AMT 4:** Si el precio reacciona en el POC de un rango previo → posiblemente no completa ese balance y va a retestear el balance anterior.

**Target:** POC/mid del rango = TP1 en spec v3.

**Ejemplo BTC:** POC semanal en 66.500. BTC sube a 70.000, cae. Cuando toca 66.500 reacciona con fuerza. Target habitual TP1.

**Convergencias:** Naked POC, VAH/VAL, HVN/LVN, TPOC (TPO POC), AMD.

---

### 23. NAKED POC
**Qué es:** POC de un período anterior que el precio **aún no ha retestado**. Imán de precio muy fuerte porque representa un nivel de valor pendiente de visitarse.

ATAS los marca automáticamente. "Los naked funcionan como liquidez porque hubo un movimiento grande desde ahí."

**Señal:**
- Naked POC debajo del precio → soporte magnético → target bajista
- Naked POC encima del precio → resistencia magnética → target alcista
- Navegación natural: de POC a POC (Naked a Naked)

**Ejemplo BTC:** Naked POC diario en 65.700. BTC estaba en 68.000, bajó directo a 65.700. TP perfecto de long desde 63.000.

**Convergencias:** POC, TPOC, Liquidez pendiente, Value Area (los naked suelen ser niveles importantes del VA previo), Targets de trade.

---

### 24. VALUE AREA HIGH / LOW (VAH / VAL)
**Qué es:** Límites que contienen el 68-70% del volumen del período. VAH = límite superior, VAL = límite inferior.

**Regla AMT 1:** Si el precio tiene aceptación dentro del Value Area → muy probable que visite el lado contrario (rotación).

**Aperturas:**
- Precio abre por encima del VAH → tendencial alcista → buscar continuación o rechazo
- Precio abre por debajo del VAL → tendencial bajista

**Convergencias:** POC, HVN/LVN, Volume Profile Open (4 variantes), AMD, VWAP.

---

### 25. HIGH VOLUME NODE (HVN)
**Qué es:** Zonas de alto volumen en el perfil → picos en el histograma. Freno del precio, toma de profit institucional, soporte/resistencia fuerte.

**Señal:** Target habitual de trades — tomar profit en el siguiente HVN del perfil previo.

**Convergencias:** POC (máximo HVN), VAH/VAL, Composite Profile, Targets de trade.

---

### 26. LOW VOLUME NODE (LVN)
**Qué es:** Zonas de bajo volumen en el perfil → el precio las atraviesa rápidamente (poca contraparte que lo frene).

**Señal en scalping (ES/NQ):** Entrar en el LVN en dirección de la tendencia → el precio lo atraviesa rápido → capturar el movimiento.

**Equivalencia:** Single Prints del TPO (ineficiencia temporal), FVG (ineficiencia de precio).

**Convergencias:** HVN (opuesto), Single Prints, Volume Gap (mismo concepto en contexto tendencial).

---

### 27. MARKET PROFILE / TPO (Time Price Opportunity)
**Qué es:** Distribución del **tiempo** (no volumen) dedicado a cada precio. Cada letra = 30 minutos. Diferente del Volume Profile.

**Componentes:**
- **TPOC** (Time POC): nivel donde más letras se apilan
- **Single Prints**: solo una letra en un precio → ineficiencia temporal → imán
- **Buying Tail**: pocas letras en el mínimo → rechazo bajista → alcista
- **Selling Tail**: pocas letras en el máximo → rechazo alcista → bajista
- **Campana de Gauss**: distribución equilibrada = sesión balanceada

**Señal:**
- Single Prints alcistas (hueco arriba) → imán alcista
- Single Prints bajistas → imán bajista
- Si el primer período del día (A) está en la parte alta → sesión tendencial alcista potencial

**Convergencias:** Volume Profile (complementario), Single Prints = FVG (ICT), Naked POC / TPOC, Value Area.

---

### 28. SINGLE PRINTS (TPO)
**Qué es:** Niveles donde solo aparece una letra del TPO. El mercado pasó tan rápido que no construyó valor ahí.

**Equivalencia directa:** "Los Single Prints del TPO son los verdaderos Fair Value Gaps."

**Confluencia máxima:** Single Print + Vacío de Volumen en ese mismo nivel = ineficiencia doble → target o entrada de alta probabilidad.

**Convergencias:** FVG (ICT), LVN (Volume Profile), Unfinish Action (footprint), Naked POC.

---

### 29. VOLUME PROFILE OPEN — 4 VARIANTES
Dónde abre el precio respecto al VA y POC del día anterior determina el tipo de día:

| Variante | Apertura | Día esperado | Estrategia |
|---|---|---|---|
| 1 | Dentro del VA (entre VAH y VAL) | Rango | Ping-pong VAL-VAH |
| 2 | Fuera del VA pero dentro del rango total | Moderadamente tendencial | Buscar POC primero, luego continuar |
| 3 | Gap completo fuera del perfil | Tendencial fuerte | Continuar en dirección del gap |
| 4 | Fuera del perfil pero acepta de vuelta dentro | Falso breakout | Tradear el rango completo |

**Convergencias:** Value Area (VAH/VAL), POC, AMD (la apertura define la fase del día), Sesiones (London, NY).

---

### 30. COMPOSITE PROFILE / MERGE
**Qué es:** Unión de múltiples perfiles solapados → POC combinado más sólido. Cuando dos días consecutivos forman el mismo balance → unirlos da el nivel de mayor volumen del rango completo.

**Aplicación en OBs:** Dos OBs consecutivos → merge del cluster de ambas velas → nivel donde el precio reacciona exactamente.

**Convergencias:** Volume Profile, POC, VAH/VAL, Order Blocks, FRVP.

---

### 31. DELTA PROFILE (Perfil Delta-coloreado)
**Qué es:** Volume Profile donde cada barra está coloreada según el delta en ese nivel (no por volumen total). Verde = delta positivo, Rojo = delta negativo.

**Señal:**
- Alto volumen + delta positivo (verde) → compradores agresivos en esa zona → soporte real con presión compradora
- Alto volumen + delta negativo (rojo) → vendedores agresivos → resistencia con presión vendedora
- Divergencia delta-precio en perfil → posible absorción

**Convergencias:** Volume Profile, Delta, CVD, Order Blocks.

---

### 32. RASTRO DEL VWAP (Previous Upper/Lower Value)
**Qué es:** El nivel donde cerró el VWAP de la sesión anterior. ATAS lo muestra automáticamente como línea persistente. "Solo ATAS tiene esta función."

**Señal:** El precio siempre tiende a volver al VWAP de la sesión anterior en algún momento. Actúa como imán / Target 2.

**Uso:** Cuando el precio toca el rastro + rechazo + Big Trades fallidos → entrada hacia el VWAP actual.

**Convergencias:** VWAP, Big Trades (gatillo de entrada), Zonas de acumulación.

---

## CATEGORÍA 3: INDICADORES DERIVADOS

---

### 33. CVD / CBD (Cumulative Volume Delta)
**Qué es:** `CVD = Σ delta de cada vela`. Muestra la presión neta compradora/vendedora acumulada en el tiempo. En ATAS se llama CBD (Cumulative Buy-side Delta).

**Divergencias (señal estrella):**

| Situación | Precio | CVD | Interpretación |
|---|---|---|---|
| **Divergencia alcista** | Lower Low (nuevo mínimo) | Higher Low (no confirma) | Ventas agresivas disminuyeron → absorción → alcista |
| **Divergencia bajista** | Higher High (nuevo máximo) | Lower High (no confirma) | Compras agresivas disminuyeron → absorción → bajista |
| **No divergencia (no operar contra)** | Lower Low | Lower Low también | Confirmación bajista, no oponerse |

**Ruptura con CVD:** CVD + precio rompen en misma dirección → rompimiento real.

**Pendiente del CVD:**
- Fuerte positiva → tendencia alcista
- Fuerte negativa → tendencia bajista
- Plana/oscilante → rango

**Lookback para divergencias:** 20 velas (`cvd_divergence_lookback = 20`).

**Ejemplo BTC:** BTC hace Low en 63.000, luego Low en 62.500 (Lower Low). CVD: -800M, -650M (Higher Low). Divergencia alcista. Los vendedores se están agotando → reversión probable desde 62.500.

**Convergencias:** Delta, Volume Profile, Big Trades, OI (CVD dice dirección, OI dice si son nuevas posiciones), Drenaje de delta.

---

### 34. VWAP (Volume Weighted Average Price)
**Qué es:** `VWAP = Σ(precio × volumen) / Σ(volumen)`. Reinicio diario a las 00:00 UTC. Termómetro institucional — las instituciones miden su ejecución contra el VWAP.

**Señal:**
- Precio sostenido por encima del VWAP → sesgo alcista → buscar longs en retrocesos al VWAP
- Precio sostenido por debajo → sesgo bajista → buscar shorts en retrocesos
- VWAP con pendiente alcista + retroceso = entrada long de alta probabilidad
- Sin pendiente (lateral): no usar VWAP como señal de dirección

**En spec v3:** VWAP es Fase 5 (gestión / target), **no gatillo de entrada**.

**Zona muerta:** `vwap_dead_zone_atr = 0.15` — no operar cuando el precio está muy cerca del VWAP.

**Convergencias:** Big Trades, CVD, Volume Profile (el VWAP suele estar cerca del POC en sesiones balanceadas), Rastro del VWAP, Scalping (retroceso al VWAP como entrada).

---

### 35. VWAP BANDAS ±1σ / ±2σ
**Qué es:** Desviaciones estándar alrededor del VWAP. ±1σ contiene ~68% de los precios de la sesión; ±2σ contiene ~95%.

Las instituciones compran cerca de -1σ/-2σ y venden cerca de +1σ/+2σ.

**Señal:**
- Precio en -2σ → sobreextensión bajista → alta probabilidad de retorno al VWAP
- Precio en +2σ → sobreextensión alcista → retorno al VWAP
- Estrategia simple: zona alta = buscar ventas, zona baja = buscar compras

**Convergencias:** VWAP, Zonas alta/baja, Big Trades (confirmar rechazo en la banda).

---

### 36. VR (Volume Ratio)
**Qué es:** `VR = volumen_vela / SMA(volumen, N)`. N=50 en M5, N=150 en M1. Normaliza el volumen para adaptarse a condiciones cambiantes.

| VR | Interpretación |
|---|---|
| >= 4.0 | Umbral de rompimiento real (`volume_breakout_vr = 4.0`) |
| >= 2.0 | Volumen elevado → señal de absorción o iniciativa real |
| < 0.7 | Drenaje de volumen → Finish Action → agotamiento |

**Convergencias:** Absorción (VR >= 2.0 requerido), Rompimiento real (VR >= 4.0), Finish Action (VR < 0.7), DZ (ambos normalizan).

---

### 37. DZ (Delta Z-score)
**Qué es:** `DZ = (delta_vela − media(delta, N)) / stdev(delta, N)`. Normaliza la intensidad del delta entre diferentes condiciones de mercado.

| DZ | Interpretación |
|---|---|
| <= -2.0 | Umbral de rompimiento bajista real |
| <= -1.5 | Ventas agresivas significativas |
| >= +1.5 | Compras agresivas significativas |
| >= +2.0 | Umbral de rompimiento alcista real |

**Convergencias:** VR (ambos normalizan), Absorption Score (AS usa DZ como componente), Rompimiento real vs. absorción.

---

### 38. ABSORPTION SCORE (AS)
**Qué es:** Métrica continua que cuantifica la intensidad de absorción. Combina agresión (DZ) con falta de progreso de precio (desplazamiento). La señal más pesada del sistema.

```
desplazamiento = (cierre − apertura) / rango_vela     # de -1 a +1
AS_long  = max(−DZ, 0) × (1 − max(desplazamiento, 0))
AS_short = max( DZ, 0) × (1 − max(−desplazamiento, 0))
```

| AS | Señal |
|---|---|
| >= 3.0 | Absorción fuerte — alta convicción |
| >= 1.5 | Absorción mínima — señal válida |

**Peso en el scoring: 22/100.**

**Convergencias:** DZ, VR, Absorción (footprint), POC en mecha.

---

### 39. LIQUIDITY IMBALANCE (LI)
**Qué es:** `LI = (liq_resting_bid − liq_resting_ask) / (bid + ask)`. Del heatmap/DOM histórico.

- LI > 0 → más liquidez compradora → soporte potencial
- LI < 0 → más liquidez vendedora → resistencia potencial
- `abs(LI) >= 0.35` → desequilibrio relevante

**Peso en el scoring: 2/100** — útil pero no determinante por sí sola.

**Convergencias:** Heatmap, DOM, Big Trades.

---

## CATEGORÍA 4: PATRONES DE VELAS Y CONTEXTO

---

### 40. SWING FAILURE PATTERN (SFP)
**Qué es:** Falso rompimiento de un máximo o mínimo significativo. El precio supera el swing previo (toma la liquidez) pero no logra cerrar por fuera, revirtiendo con fuerza.

**Validación:** Vela que rompe el swing previo pero cierra dentro del rango anterior. Volumen > media en la vela del SFP.

**Señal:**
- SFP en máximo + volumen > media → toma de BSL real → bajista post-rechazo
- SFP en mínimo → toma de SSL real → alcista post-rechazo

**Ejemplo BTC:** BTC pincha 73.900 (sobre ATH de 73.800), pero cierra en 73.600 con volumen 2.8x la media. Compradores de 73.900 atrapados → setup short.

**Convergencias:** Liquidez (BSL/SSL), Volumen, Big Trades (en la mecha del SFP), CHoCH local.

---

### 41. ORDER BLOCK (OB)
**Qué es (ICT):** Último candle bajista antes de un impulso alcista (Bullish OB) o último candle alcista antes de un impulso bajista (Bearish OB).

**Validación con Order Flow — condiciones para OB válido:**
1. Forma un swing high/low (fractal de Williams)
2. Hay zona de soporte/resistencia de alto timeframe en esa zona
3. El precio cierra por debajo/encima del 50% del OB
4. Cambio de estructura en LTF a partir de su movimiento
5. Genera impulso con imbalances / stacked imbalances
6. Tiene Fibonacci OTE (618) en la misma zona
7. **Volumen > media** en el OB
8. **Delta significativo** en dirección del movimiento

**OB inválido:** Volumen < 50% de la media + delta insignificante → no hay bloque real de órdenes, solo precio.

**Ejemplo BTC:** OB en 66.200 (última vela bajista antes del impulso a 70.000). Volumen 1.8x la media, delta -800M, stacked imbalances alcistas justo encima. Cuando BTC retestea 66.200 → entrada long.

**Convergencias:** Volume Profile (cluster dentro del OB), Delta, Imbalances, FVG (los imbalances del OB son FVGs), Fibonacci/OTE, Stacked Imbalances, SMC.

---

### 42. FVG (Fair Value Gap)
**Qué es (ICT):** Zona entre la mecha del candle 1 y el cuerpo del candle 3 en un movimiento de 3 velas donde el precio no cubrió todas las órdenes. Equivale a imbalances/stacked imbalances en footprint.

**Equivalencias:**
- FVG (ICT) = Stacked Imbalances (footprint) = Single Prints (TPO)

**Señal:**
- Bullish FVG (gap abajo) → soporte → long al retestear
- Bearish FVG (gap arriba) → resistencia → short al retestear

**Advertencia:** En oro los FVGs no sirven (los consume todos). Usar OTE en su lugar.

**Convergencias:** Imbalances (footprint), Stacked Imbalances, Single Prints (TPO), IFVG, Order Block.

---

### 43. IFVG (Inverse Fair Value Gap)
**Qué es:** FVG "consumido" (llenado) que ahora actúa como soporte/resistencia desde el lado opuesto.

- IFVG alcista: FVG bajista previamente llenado → actúa como soporte
- IFVG bajista: FVG alcista llenado → actúa como resistencia

**Convergencias:** FVG, Order Blocks, Mitigation Blocks.

---

### 44. AMD (Acumulación - Manipulación - Distribución / Power of 3)
**Qué es:** Ciclo de 3 fases ejecutado en múltiples timeframes. En ICT también llamado "Power of 3".

**Las 3 fases:**
1. **Acumulación:** Rango lateral, institucionales comprando gradualmente. Volumen bajo.
2. **Manipulación:** Fakeout en dirección contraria a la distribución final → activa stops, recoge liquidez. Señales: VPIN alto, CVD divergido, OI cayendo.
3. **Distribución:** El movimiento real y fuerte en la dirección planificada.

**Intraday:** Asia = acumulación → London = manipulación → NY = distribución.

**Ejemplo BTC:** Asia rango 66.000-66.500. London barre el low a 65.700 (liq_ratio=4.5, OI cae -1.8%). NY arranca en 66.000 y sube a 68.500 (distribución). Setup long estaba en 65.700 con absorción.

**Convergencias:** Power of 3 (ICT), Wyckoff, Big Trades (masivamente en distribución), CVD (divergencia en manipulación), OI (baja en manipulación, sube en distribución real), Liquidaciones, VPIN.

---

### 45. BOS / CHoCH (Break of Structure / Change of Character)
**Qué es:**
- **BOS:** El precio rompe el último High (tendencia alcista) o último Low (tendencia bajista) → confirma continuación
- **CHoCH:** Rompe en dirección contraria → primera señal de reversión potencial

**Señal:**
- BOS alcista → continuar longs, buscar pullbacks para agregar
- CHoCH alcista (HL tras tendencia bajista) → primera señal de reversión alcista → buscar confirmación
- CHoCH en velas de volumen = mayor significado que en velas de tiempo

**Convergencias:** Order Blocks (el CHoCH suele originarse en un OB), Delta (el CHoCH debería coincidir con delta flip), Velas de Volumen.

---

### 46. BSL / SSL (Buy Side / Sell Side Liquidity)
**Qué es:**
- **BSL (Buy Side Liquidity):** Stops de vendedores (stop loss de shorts) sobre máximos recientes → liquidez que los institucionales buscan para vender
- **SSL (Sell Side Liquidity):** Stops de compradores bajo mínimos recientes

**Señal:**
- Toma de BSL (sweep arriba) → institucionales venden a esos precios → bajista post-toma
- Toma de SSL (sweep abajo) → institucionales compran → alcista post-toma

**Validación obligatoria:** Toma de liquidez sin incremento de volumen = NO es institucional → **no operar**.

**Convergencias:** Heatmap, SFP, Equal Highs/Lows, AMD (la manipulación ES una toma de BSL/SSL), Liquidaciones.

---

### 47. EQUAL HIGHS / EQUAL LOWS (EQH / EQL)
**Qué es:** Dos o más máximos/mínimos al mismo nivel → acumulación obvia de stops → el mercado los busca antes de moverse en la dirección real.

**Señal:**
- EQH → liquidez alcista obvia → precio los barre → post-toma sesgo bajista
- EQL → post-toma sesgo alcista

**Convergencias:** BSL/SSL, Heatmap, SFP, Toma de liquidez.

---

### 48. PATRÓN ENGULF (con volumen)
**Qué es:** Vela que envuelve completamente la vela anterior. Relevante con: volumen > media + estar en zona de interés + delta favorable.

**Señal:**
- Engulf alcista en zona de soporte (barrió abajo, cerró arriba) → compradores absorbieron toda la venta → señal long
- Engulf bajista en zona de resistencia (barrió arriba, cerró abajo) → señal short

**Convergencias:** Absorción, SUP-B, Order Blocks, Toma de liquidez, Delta.

---

### 49. SUP-B (Support Body)
**Qué es:** Cuerpo de vela que actúa como zona de soporte en retest. Combinado con Order Flow (delta concentrado + Big Trades compradores) = entrada sólida equivalente a un OB validado.

**Convergencias:** Order Block, Absorción, Traders Atrapados.

---

### 50. TRIPLE PICO / TRIPLE TECHO / TRIPLE SUELO
**Qué es:** Tres intentos de romper un nivel con volumen decreciente → agotamiento. Tercer intento con Big Trades atrapados → señal fuerte de reversión.

**Señal:**
- Triple techo + Big Trades compradores atrapados en el tercer máximo + CHoCH → entrada short
- Triple suelo + Big Trades vendedores atrapados → entrada long

**Convergencias:** Equal Highs, SFP (el tercero suele ser un SFP), BSL, Big Trades.

---

### 51. TOMA DE LIQUIDEZ (validada con volumen)
**Qué es:** Sweep de máximo/mínimo + confirmación de que fue institucional (volumen real).

**Validación:**
- Volumen > media en la vela que toma la liquidez
- `liq_ratio >= 3.0` (liquidaciones crypto)
- Big Trades en la mecha
- Delta opuesto al movimiento (absorción)

**Señal:**
- Toma de SSL + volumen alto + liquidaciones + Big Trades compradores en la mecha → alcista
- Toma de BSL + volumen alto + Big Trades vendedores → bajista

**Red Flag:** Toma sin volumen = fake → no operar.

**Convergencias:** SFP, BSL/SSL, Liquidaciones, Absorción, CHoCH, AMD.

---

### 52. ARMÓNICO (Harmonic Pattern)
**Qué es:** Patrón de PA basado en ratios de Fibonacci. Define zonas de reversión de alta probabilidad.

**Con Order Flow:** El armónico define la zona; la absorción, Big Trades y delta flip confirman la reacción real.

**Convergencias:** Fibonacci/OTE, Naked POC, Volume Profile, Nivel HTF, Señales OF.

---

## CATEGORÍA 5: CONFLUENCIAS Y ESTRATEGIAS

---

### 53. ACTION MARKET THEORY (AMT) — 5 REGLAS

**Principio:** 80% del tiempo en Balance (rango), 20% en Imbalance (tendencia).

**5 Reglas:**
1. Precio con aceptación dentro del VA → probablemente visita el lado contrario (rotación)
2. Precio dentro del balance oscila entre VAL y VAH
3. Si rompe balance → busca el balance previo
4. Si reacciona en el POC de un rango → posiblemente va a retestear el balance previo
5. Si tiempo + delta + OI se construyen debajo de VAH → probable rompimiento

**Comportamiento responsivo vs. iniciativo:**
- **Responsivo:** Precio en extremo del rango → vuelve al centro → fácil de operar
- **Iniciativo:** Precio rompe el VA → busca nuevo balance → requiere confirmación OF

**Convergencias:** Volume Profile, POC/VAH/VAL, AMD, CVD, Big Trades.

---

### 54. MAMUSHKA (Top-Down Analysis)
**Qué es:** Análisis de arriba hacia abajo usando múltiples timeframes. Mensual → Semanal → Diario → ejecución en LTF. El nombre mamushka (muñecas rusas) refleja que cada timeframe está contenido en el superior.

**Cómo se aplica:**
1. **Mensual:** Grandes balances históricos, POC mensual, VAH/VAL mensual. ¿Tendencia o rango?
2. **Semanal:** Balance semanal, Naked POCs semanales. ¿El VA semanal progresa hacia arriba o abajo?
3. **Diario:** VP Open (4 variantes), nivel del día, sesiones.
4. **Ejecución:** H1/M30 contexto, M5/M1 entrada con Order Flow.

**Ejemplo BTC:**
- Mensual: Rango histórico 60.000-75.000. POC mensual = 67.500.
- Semanal: VA 64.000-70.000, desplazándose al alza.
- Diario: Variante 1 (apertura dentro del VA) → día de rango.
- M5: Absorción en VAL 64.000 + CVD divergencia → entrada long hacia VAH 70.000.

**Convergencias:** Volume Profile (cada temporalidad tiene su VP), Naked POC (los naked de HTF son los más importantes), AMD, Level to Level Trading.

---

### 55. LEVEL TO LEVEL TRADING
**Qué es:** El precio va de nivel A a nivel B, luego de B a C. Siempre operar de nivel a nivel, no de sentimiento a sentimiento.

**Principios:**
- "Tradeamos niveles, no sentimientos."
- El bías puede ayudar en la confianza pero se opera lo que el chart da, no lo que uno quiere que pase.
- Preparar siempre ambos escenarios (bullish Y bearish).
- El stop va en la invalidación del nivel, no en un número emocional.

**Convergencias:** Mamushka (los niveles A-B-C son los identificados en top-down), Naked POC, AMD.

---

### 56. ESTRATEGIA DELTA RANGES
**Qué es:** Identificar rangos donde el delta contradice el precio en los extremos:
- Delta positivo en la parte baja del rango (compras que no subieron el precio) → absorción / atrapados → precio irá al extremo opuesto
- Delta negativo en la parte alta (ventas que no bajaron) → idéntico en reversa

**Diferencia cripto vs futuros:**
- Cripto (BTC): delta suele ir **contra** el precio → absorción más común
- Futuros/índices (ES/NQ): delta suele ir **a favor** del precio

**Convergencias:** CVD, Volume Profile, Absorción, Big Trades, AMD.

---

### 57. ESTRATEGIA DELTA RANGE REVERSAL — SPEC V3 (Sistema Completo)

**Tesis:** En un rango intradía, los extremos son zonas de decisión. Si el precio llega con flujo agresivo pero no acepta fuera del rango, ese flujo queda atrapado → rotación hacia POC/mid/VWAP o extremo opuesto.

**Solo opera en RANGO. Nunca en tendencia. Nunca en el medio del rango.**

**5 Fases del flujo:**

| Fase | Descripción | Condiciones |
|---|---|---|
| 1. Detección de rango | Confirmar régimen rango | >= 20 velas en rango, >= 3 toques, pendiente mid < 0.15, rango 0.8-3.5 ATR, voto 2/3 régimen |
| 2. Mapeo de liquidez | Evaluar heatmap pre-llegada | LI en extremos antes de que el precio llegue |
| 3. Liquidaciones + OI | Al barrer el extremo | `liq_ratio >= 3.0` + `OI cae >= 1.5%` |
| 4. Footprint + Absorción | Confirmar absorción | DZ <= -1.5 + VR >= 2.0 + precio no acepta fuera + POC en mecha + delta flip siguiente vela |
| 5. Gestión | Ejecutar | TP1 = POC/mid (50%), TP2 = extremo opuesto (30%), 20% trailing |

**Score de entrada (0-100):**

| Score | Modo | Riesgo |
|---|---|---|
| < 55 | No trade | — |
| 55-69 | Scout | 0.5× riesgo |
| 70-84 | Estándar | 1.0× riesgo |
| >= 85 | Alta convicción | 1.5× riesgo |

**Pesos del score:**

| Señal | Peso |
|---|---|
| Absorción (AS) | 22 |
| Proximidad al extremo | 18 |
| POC en mecha | 12 |
| Delta flip | 12 |
| Volumen (VR) | 10 |
| CVD divergencia | 8 |
| Big Trades atrapados | 6 |
| Liquidaciones | 6 |
| OI confirma | 4 |
| Liquidez heatmap (LI) | 2 |

**Gate duro (no overrideable):** Si la vela cierra fuera del rango + siguiente vela acepta + VR >= 4.0 + DZ >= 2.0 + CVD confirma → **ROMPIMIENTO REAL → PROHIBIDO el fade aunque el score sea 90.**

**Red Flags críticas:** Cierre fuerte fuera del rango, aceptación fuera del rango, delta continuando en dirección del break, CVD confirmando ruptura, no reclaim en 3 velas, zona media, RR < 1.5, noticia macro.

---

### 58. ESTRATEGIA SCALPING ES/NQ
**Componentes:**
- VWAP con pendiente alcista + retroceso al VWAP = entrada long
- LVN en dirección de la tendencia = entrada para capturar movimiento rápido
- Clusters de delta en zona de S/R: cuando el cluster "resuelve" rompiendo → entrada con ellos
- Contexto M30 para determinar el sesgo del día

**Sesiones:** London (07:00-10:00 UTC) y NY (13:00-17:00 UTC): máxima liquidez → mejores trades.

**Convergencias:** VWAP, LVN, Delta, Volume Profile (contexto M30), AMD.

---

### 59. METODOLOGÍA VP OPEN (Aplicación práctica)
Definir sesgo semanal/mensual (mamushka) → aplicar la variante del día → ejecutar con Order Flow en la sesión.

**Convergencias:** Volume Profile, AMD, VWAP (parte del open del día).

---

### 60. ESTRATEGIA VWAP + BIG TRADES (Carlos Carrillo)
**Regla de zona:**
- Zona alta + Big Trades compradores que **NO suben** el precio = compradores atrapados → **vender**
- Zona baja + Big Trades vendedores que **NO bajan** el precio = vendedores atrapados → **comprar**
- **Zona media (cercana al VWAP) = PROHIBIDO operar**

**Targets:** Zona opuesta (TP1) → Rastro del VWAP / Previous Value (TP2).

**Convergencias:** VWAP, Big Trades, Absorción, Rastro del VWAP, Traders Atrapados.

---

### 61. CONFLUENCIA COMO PRINCIPIO ESTRATÉGICO

"Ninguna señal individual es suficiente. Se requieren al menos 2 señales coincidentes para tomar un trade. Cuantas más confluencias, mayor probabilidad de éxito."

**Setups de mayor probabilidad histórica:**

| Setup | Señales convergentes |
|---|---|
| Reversión extremo de rango (BTC) | AS >= 1.5 + liq_ratio >= 3 + OI Drop + Delta Flip + POC en mecha + CVD Divergencia + Big Trades atrapados |
| Short en máximo histórico (BTC) | Big Trades compradores en mecha + Triple pico + CHoCH local + Toma de BSL + CVD divergencia bajista |
| Order Block de alta probabilidad | Volumen > media + Delta significativo + Stacked Imbalances + OTE + CHoCH en LTF + Absorción en cluster |
| Scalping índices (ES/NQ) | VWAP con pendiente + retroceso + LVN en dirección + Delta clusters + contexto M30 |
| Rompimiento real de rango | VR >= 4.0 + DZ >= 2.0 + CVD confirma + Big Trades iniciativas + aceptación fuera + OI sube |
| Swing en oro | Naked POC + OTE (618-786) + Cluster de volumen + CHoCH + Sesión NY |

---

### 62. JOURNAL Y ESTADÍSTICA

**Campos del Journal Automático (spec v3):**
- Timestamp, mercado, sesión, setup_id, lado, régimen
- Range_high, range_low, range_poc
- Entry, stop, TP1, TP2
- Score total + desglose por señal
- VR, DZ, AS at entry
- Estado CVD, Big Trade presente, liq_ratio, estado OI
- Modo de entrada, razón de salida, resultado en R

**Para qué sirve el desglose del score:** "Correlacionar qué señales realmente predicen ganancias → recalibrar los pesos con datos reales, no con intuición."

---

### 63. GESTIÓN DE TRADE

**Parámetros del spec v3:**
- TP1 en POC/mid del rango → cerrar 50%, mover stop a breakeven
- TP2 en extremo opuesto → cerrar 30%
- 20% restante con trailing: SL bajo mínimo de las últimas 3 velas
- Salir si: delta revierte fuerte, sin nuevo extremo en 3 velas, Big Trade opuesto en zona de target

**Disciplina de sesión:**
- Máximo 3 trades por sesión
- Parar tras 2 pérdidas consecutivas
- Target diario: 1.0% de la cuenta
- Stop loss diario: 0.6% de la cuenta
- Riesgo por trade: 1.0%

---

### 64. REGÍMENES DE MERCADO: RANGO vs. TENDENCIA

**Detección — voto 2/3:**
1. El value area se desplaza progresivamente (tendencia) vs. permanece en lugar (rango)
2. El precio se sostiene un lado del VWAP (tendencia) vs. cruza frecuentemente (rango)
3. Pendiente del CVD fuerte (tendencia) vs. CVD plano/oscilante (rango)

**AMT:** "80% del tiempo en balance (rango), 20% en imbalance (tendencia)."

**Aplicación estratégica:**
- En rango → operar reversiones en extremos (Delta Range Reversal, Delta Ranges, VP ping-pong)
- En tendencia → operar retrocesos al VWAP, LVNs, OBs de continuación
- **La estrategia Delta Range Reversal solo opera en RANGO**

---

### 65. PSICOLOGÍA Y DISCIPLINA

Principios mencionados en múltiples transcripciones:
- Conocerse como trader: identificar fortalezas y debilidades mediante el journal
- Estabilidad emocional: no operar sin equilibrio emocional
- Riesgo fijo por trade: siempre el mismo tamaño → consistencia estadística
- "8 horas frente a la pantalla sin tomar un trade si no hay setup" → la no-acción es una posición
- Preparar siempre ambos escenarios (bullish Y bearish)
- "El bías puede ayudar en la confianza de un trade pero uno tiene que siempre tradear lo que el chart le da, no lo que uno quiere que pase."

---

## EJEMPLO BTC COMPLETO — CONVERGENCIA EN UN TRADE

**Escenario:** BTC en rango intradía 64.000-67.500. Precio llega al Range Low 64.000.

```
Fase 2 — pre-llegada:
  → Heatmap: LI negativo en 63.800 (liquidez vendedora resting)
  → Naked POC diario en 63.900

Fase 3 — al barrer 64.000:
  → Precio barre SSL a 63.850
  → liq_ratio = 4.8 (liquidaciones de longs, 4.8x la media)
  → OI cae -2.3%

Fase 4 — footprint en la vela de la barrida:
  → DZ = -1.8 (ventas masivas)
  → VR = 2.6 (volumen 2.6x la media)
  → POC de la vela en la mecha baja (poc_wick_ratio = 0.72)
  → AS_long = 1.8 × (1 - (-0.05)) ≈ 1.89
  → Vela siguiente: delta flip a +500M
  → CVD hace Higher Low mientras precio hace Lower Low (divergencia)

Score estimado:
  AS (22) + Proximidad (18) + POC en mecha (12) + Delta flip (12) +
  VR (10) + CVD divergencia (8) + Liquidaciones (6) + OI (4) = 92/100

→ Entrada long en 64.050
→ Stop en 63.600 (bajo la mecha + invalidación del nivel)
→ TP1 = 65.750 (POC/mid del rango) — cerrar 50%
→ TP2 = 67.500 (Range High) — cerrar 30%
→ RR = 3.9R
```

---

## ÍNDICE DE CONCEPTOS

| # | Concepto | Categoría |
|---|---|---|
| 1 | Footprint Chart | Microestructura |
| 2 | Delta | Microestructura |
| 3 | Delta Mínimo / Máximo (intracandle) | Microestructura |
| 4 | Open Interest (OI) | Microestructura |
| 5 | Absorción | Microestructura |
| 6 | Traders Atrapados | Microestructura |
| 7 | Imbalances | Microestructura |
| 8 | Stacked Imbalances | Microestructura |
| 9 | Finish Action | Microestructura |
| 10 | Unfinish Action | Microestructura |
| 11 | Big Trades | Microestructura |
| 12 | Cluster de Volumen | Microestructura |
| 13 | Velas de Volumen (Trend Reversal) | Microestructura |
| 14 | Drenaje de Delta | Microestructura |
| 15 | POC de Vela (intracandle) | Microestructura |
| 16 | Reversal Pattern (ATAS) | Microestructura |
| 17 | Absorción Pattern (ATAS) | Microestructura |
| 18 | Liquidaciones (Crypto) | Microestructura |
| 19 | DOM / Depth of Market | Microestructura |
| 20 | Heatmap (DOM histórico) | Microestructura |
| 21 | Volume Profile | Volumen y Perfil |
| 22 | POC (Point of Control) | Volumen y Perfil |
| 23 | Naked POC | Volumen y Perfil |
| 24 | VAH / VAL (Value Area High/Low) | Volumen y Perfil |
| 25 | HVN (High Volume Node) | Volumen y Perfil |
| 26 | LVN (Low Volume Node) | Volumen y Perfil |
| 27 | Market Profile / TPO | Volumen y Perfil |
| 28 | Single Prints (TPO) | Volumen y Perfil |
| 29 | Volume Profile Open — 4 Variantes | Volumen y Perfil |
| 30 | Composite Profile / Merge | Volumen y Perfil |
| 31 | Delta Profile | Volumen y Perfil |
| 32 | Rastro del VWAP (Previous Value) | Volumen y Perfil |
| 33 | CVD / CBD (Cumulative Volume Delta) | Indicadores Derivados |
| 34 | VWAP | Indicadores Derivados |
| 35 | VWAP Bandas ±1σ / ±2σ | Indicadores Derivados |
| 36 | VR (Volume Ratio) | Indicadores Derivados |
| 37 | DZ (Delta Z-score) | Indicadores Derivados |
| 38 | Absorption Score (AS) | Indicadores Derivados |
| 39 | Liquidity Imbalance (LI) | Indicadores Derivados |
| 40 | Swing Failure Pattern (SFP) | Patrones y Contexto |
| 41 | Order Block (OB) | Patrones y Contexto |
| 42 | FVG (Fair Value Gap) | Patrones y Contexto |
| 43 | IFVG (Inverse Fair Value Gap) | Patrones y Contexto |
| 44 | AMD (Acumulación-Manipulación-Distribución) | Patrones y Contexto |
| 45 | BOS / CHoCH (Break of Structure) | Patrones y Contexto |
| 46 | BSL / SSL (Buy/Sell Side Liquidity) | Patrones y Contexto |
| 47 | Equal Highs / Equal Lows | Patrones y Contexto |
| 48 | Patrón Engulf (con volumen) | Patrones y Contexto |
| 49 | SUP-B (Support Body) | Patrones y Contexto |
| 50 | Triple Pico / Techo / Suelo | Patrones y Contexto |
| 51 | Toma de Liquidez (validada) | Patrones y Contexto |
| 52 | Armónico (Harmonic Pattern) | Patrones y Contexto |
| 53 | Action Market Theory (AMT) | Confluencias y Estrategias |
| 54 | Mamushka (Top-Down Analysis) | Confluencias y Estrategias |
| 55 | Level to Level Trading | Confluencias y Estrategias |
| 56 | Estrategia Delta Ranges | Confluencias y Estrategias |
| 57 | Delta Range Reversal — Spec V3 | Confluencias y Estrategias |
| 58 | Estrategia Scalping ES/NQ | Confluencias y Estrategias |
| 59 | Metodología VP Open | Confluencias y Estrategias |
| 60 | Estrategia VWAP + Big Trades | Confluencias y Estrategias |
| 61 | Confluencia como Principio | Confluencias y Estrategias |
| 62 | Journal y Estadística | Confluencias y Estrategias |
| 63 | Gestión de Trade | Confluencias y Estrategias |
| 64 | Regímenes: Rango vs. Tendencia | Confluencias y Estrategias |
| 65 | Psicología y Disciplina | Confluencias y Estrategias |
