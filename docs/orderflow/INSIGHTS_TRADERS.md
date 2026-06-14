# Insights de Traders — Extracto para RBF

*Generado: 2026-06-12. Fuente: 15 transcripciones en `docs/orderflow/revisar/`*

> Este documento extrae los conceptos más relevantes de cada recurso y los mapea directamente
> al sistema RBF (Range Breakout Flow) que opera en M1 crypto (BTC/ETH/BNB/SOL/XRP) con
> VR, CVD, OBI, score 0-6, trailing lock floor, sesiones London/NY/Overlap.

---

## PATRONES RECURRENTES — Lo que repiten todos

Estos conceptos aparecen en 5+ fuentes independientes. Son los más confiables.

| Concepto | Aparece en | Relevancia RBF |
|---|---|---|
| LVN (Low Volume Node) = magnet | 7 fuentes | ★★★ VR bajo en rango = LVN confirmado |
| CVD/delta como confirmación direccional | 9 fuentes | ★★★ Core del sistema |
| Absorción (volumen sin precio) = reversión | 6 fuentes | ★★★ Flag `absorption` en score |
| Context + Location + Confirmation | 5 fuentes | ★★★ Exactamente el score 0-6 |
| VR 3–5x como sweet spot | Análisis propio | ★★★ Confirmado en backtesting |
| Sesiones London/NY open = prime time | 8 fuentes | ★★ Ya implementado |
| Trailing stop en estructura, no distancia fija | 6 fuentes | ★★ Trailing lock floor existente |
| Entrada post-retest, no en el break | 8 fuentes | ★★ Pre/post breakout ya diferenciado |
| Multi-timeframe: daily bias + M1 entry | 7 fuentes | ★ HTF H1 ya trackea esto |
| VR>5x = breakout exhausto (no CVD, sino extensión) | Análisis propio | ★ Nuevo hallazgo |

---

## POR FUENTE — Análisis individual

---

### 1. I Traded AGAIN with a PRO Scalper
**Fuente:** Chart Fanatics — scalper verificado en crypto/índices
**Enfoque:** Orderflow puro, lectura de delta y volumen agresivo en M1

1. **Delta agresivo vs pasivo** — Los agresores (market orders) revelan intención. Los pasivos (límite) pueden ser spoof. *RBF: CVD mide órdenes agresivas acumuladas. OBI mide pasivas. Cuando ambos apuntan igual dirección = convicción máxima.*

2. **LVN como repetición** — El mercado vuelve a llenar zonas de bajo volumen post-impulso. Son trampas de liquidez que generan reversiones. *RBF: El flag `lvn_thin` en el score capta esto. Expandir: cuando precio visita LVN en menos de 15 barras post-impulso = señal más fresca y válida.*

3. **Confirmación post-entrada obligatoria** — Si CVD/OBI revierten en las primeras 2 velas post-entry = salida inmediata. No esperes. *RBF: El trailing lock floor cubre esto — si activa en 1.90R es porque el precio ya se movió. Pero la idea de monitorear las primeras 2-3 velas post-entry para invalidación temprana es útil como gate adicional.*

4. **Fractal M1 espeja M5** — Lo que pasa en M5 se replica 5× más rápido en M1. Puedes anticipar M1 viendo M5. *RBF: HTF H1 ya da bias. Añadir validación de M5 como timeframe intermedio podría mejorar el contexto de entrada.*

5. **Velocidad de cierre de vela = convicción** — Vela que cierra rápido (precio moviéndose 10+ pips en <30 seg) = participantes convencidos. *RBF: El campo `bar_delta` captura esto parcialmente. Velas con delta alto en poco tiempo = breakout con convicción. Candidato a feature adicional.*

6. **Divergencia precio vs volumen** — Precio hace nuevo high/low pero el volumen del move cae = debilidad. Señal de reversión. *RBF: Si VR cae en confirmación de nuevo extremo + CVD diverge = short/long contra el movimiento. Exactamente lo que detecta el sistema. Confirmación de la lógica actual.*

7. **Rango apretado previo = mayor extensión del breakout** — Consolidación estrecha bajo tensión = breakout más violento. *RBF: `RANGE_MIN_PCT = 0.08%` ya filtra rangos demasiado pequeños. Pero la tesis es: rangos en el bucket 0.08–0.25% tienen mayor potencial de extensión (datos propios confirman WR=60% en <0.25%).*

8. **Scalp parcial + trailing del resto** — Cierra 50% en 1R, deja trailing el 50% restante. Permite capturar movimientos grandes sin psicología pesada. *RBF: El trailing lock floor hace exactamente esto automáticamente. La salida parcial es el comportamiento resultante de activar trailing a 1.90R.*

9. **No entrar en primer retest — esperar 2do o 3ro** — La primera prueba de un nivel tiene alta probabilidad de fallo. El 2do+ retest = compradores/vendedores confirmados. *RBF: El cooldown de 60 barras entre trades previene esto parcialmente. Para pre-breakout: el sistema ya exige VR≥1.5 pero no valida cuántas veces se testó el rango. Feature pendiente.*

10. **Gestión de pérdida máxima diaria** — 2% de cuenta como límite diario absoluto. Pasado eso, parar. Preserva psicología y capital. *RBF: Paper trader actualmente no tiene este límite. Añadir circuit breaker: si equity cae >3% en sesión = pausa automática de entradas.*

---

### 2. trading_en_vivo
**Fuente:** Trader en sesión documentada (London/NY Overlap)
**Enfoque:** Orderflow en tiempo real, lectura de book y tape

1. **Paredes de órdenes que desaparecen = movimiento inminente** — Una pared pasiva grande que se cancela antes de que precio llegue = retiro de liquidez = vacío = precio corre. *RBF: OBI puede capturar esto si la pared era lo suficientemente grande. Spikes de OBI que desaparecen en 1-2 barras = candidato a filtro (ignorar OBI inestable).*

2. **Órdenes iceberg (hidden)** — Cuando hay volumen ejecutado sin pared visible en el libro = órdenes ocultas de institucionales. *RBF: CVD + OBI en conjunto detectan esto. Si CVD sube pero OBI es neutro = comprador oculto agresivo. Combinación ya presente en el score.*

3. **Overlap = máxima probabilidad de ruptura** — La sesión London-NY (13:00–17:00 UTC) tiene la mayor liquidez y menor ruido. *RBF: Ya implementado como sesión activa. Datos propios: Overlap WR=43% con AvgR=+0.27R — menor que London y NY. Posible que los mejores Overlap trades sean subset VR 3-5x + score≥2.*

4. **Spread bid-ask como filtro de calidad** — Spread ancho = mercado ilíquido = slippage = no operar. Spread tight = ejecución limpia. *RBF: El veto `spread_wide` ya hace esto (spread_bps > 5). Confirmado correcto por esta fuente.*

5. **Consolidación intradiaria en primera hora = rango del día** — El rango de London primera hora (08:00–10:00 UTC) define high/low del día. Edges de ese rango = niveles para trades de NY. *RBF: Pendiente como feature. Los niveles London High/Low como candidatos a key levels de sesión (similar a PDH/PDL ya implementados).*

6. **Reacción post-datos económicos: esperar 5 minutos mínimo** — Primera reacción a noticias = ruido de institucionales ejecutando anticipado. La dirección real aparece 3–5 minutos después. *RBF: Sin filtro de noticias actualmente. Candidato: ignorar señales en los 5 minutos siguientes a horarios de noticias macro (CPI, FOMC, etc.).*

7. **POC como imán de precio** — El precio siempre gravita hacia el Point of Control (precio con más volumen) del día/semana. *RBF: Volume Profile ya renderizado en el chart. El POC podría usarse como filtro de target: si el target RBF está más cerca que el POC = buena señal. Si el POC está entre entry y target = obstáculo.*

---

### 3. How To Find The BEST Entry Zones
**Fuente:** Chart Fanatics — confluencia multi-timeframe
**Enfoque:** Zonas de entrada de alta probabilidad, demand/supply zones

1. **Zona de demanda ≠ línea, es un rango** — No es un precio exacto, es el rango completo donde grandes compradores entraron históricamente. El precio no toca "el punto" exacto sino la zona. *RBF: El rango del breakout ya define esto parcialmente. Pero en backtesting, el stop exacto podría estar 1-2% más generoso para no ser liquidado por micro-movements.*

2. **Rechazo repetido = probabilidad creciente, luego inversión** — 2–3 rechazos de mismo nivel = alta probabilidad de nuevo rechazo. Pero al 5to rechazo = ruptura probable. La curva se invierte. *RBF: El cooldown de 60 barras ya previene trades excesivos en el mismo nivel. Pero el concepto de "cuántas veces ha sido testado" podría añadir contexto al score.*

3. **Wick sin cierre = nivel NO roto** — El precio puede hacer wick por debajo/arriba de un nivel pero si no hay cierre de vela ahí = el nivel sigue vigente. *RBF: Importante para definición de breakout. RBF ya exige `close fuera del rango` para señal válida. Confirmado correcto.*

4. **Volatilidad modifica el contexto de la zona** — Una zona en mercado ATR bajo ≠ zona en mercado ATR alto. La zona sigue siendo válida pero el size del trade debe ajustar. *RBF: ATR ya está en las barras. Candidato: normalizar el stop loss por ATR dinámico en lugar de fijo por porcentaje del rango.*

5. **Confirmación 1-2 velas post-entrada** — Entra en la zona pero si las primeras 2 velas cierran contra ti = salida. La zona falló. *RBF: El trailing lock floor protege desde 1.90R pero antes de eso el único stop es el estructural. Un time stop corto (5-10 barras) podría proteger entradas que van inmediatamente en contra — diferente del time stop que había que eliminaba winners.*

6. **Volumen en la zona = fuerza de la zona** — Zona con alto volumen histórico (HVN) = fuerte. Zona con bajo volumen (LVN en zona) = débil. *RBF: El veto `hvn_target` protege cuando hay un HVN entre entry y target. El inverso (LVN entre entry y target) = camino libre = oportunidad.*

---

### 4. La estrategia EXACTA — Trader Yush ($2M+ payouts)
**Fuente:** Trader Yush — orderflow + LVN methodology
**Enfoque:** LVN como mecánica subyacente de todo movimiento, 4 pasos de confirmación

1. **FVG vs LVN: el FVG es el "qué", el LVN es el "por qué"** — Un Fair Value Gap visible en el gráfico es la manifestación visual de un LVN en el perfil de volumen. El LVN da la mecánica real: precio voló porque nadie estaba dispuesto a comprar/vender ahí. *RBF: Cuando el sistema detecta un breakout (FVG), el VR bajo en ese rango = LVN confirmado. Ambos son lo mismo. La integración ya existe.*

2. **4 pasos obligatorios: Nivel → Retest → LVN → Revisita** — Paso 1: identifica soporte/resistencia. Paso 2: espera retest confirmatorio. Paso 3: busca LVN en el move desde ese nivel. Paso 4: espera que precio visite el LVN con confirmación opuesta. *RBF: Este framework es exactamente el scoring en 4 capas. Cada paso suma al score. El sistema ya opera así — validación del diseño.*

3. **Timing del retest: 5–30 minutos post-impulso** — Un LVN visitado en los primeros 30 minutos post-impulso es el más válido. Después de horas, el contexto se resetea. *RBF: El cooldown de 60 barras (60 minutos en M1) podría ser calibrable. Si el LVN más fresco (dentro de 30 barras) es más válido, el cooldown de 60 podría ser conservador. Pendiente con n≥50.*

4. **Agresivos dicen la verdad, pasivos mienten** — Las órdenes de mercado (agresivas) revelan intención real. Las órdenes límite (pasivas) pueden cancelarse — son declaraciones de intención no vinculantes. *RBF: CVD mide la presión agresiva. OBI mide la pasiva. La primacía del CVD sobre el OBI como feature discriminador está confirmada en los datos propios.*

5. **Entrada precisa en el LVN exacto = stop muy apretado** — Si entras exactamente en el precio del LVN, el stop natural está apenas 2-3 pips más allá. Esto permite tamaños grandes con el mismo riesgo. *RBF: La precisión de entrada ya está determinada por el close de la vela de breakout. El stop es `range_high` (para shorts). El rango ya define este stop natural.*

6. **Mercado como subasta continua** — CVD = demanda vs oferta. OBI = fuerza de la puja. VR = volumen de la subasta. Combina los tres y tienes el estado completo del mercado. *RBF: Esta trinidad ya es el score de confluencia. Confirmación del diseño.*

---

### 5. 12 años de conocimiento — Umar Ashraf
**Fuente:** Umar Ashraf — ICT + Smart Money structures
**Enfoque:** Ciclos Smart Money, Break & Retest, Fair Value Gaps, Order Blocks

1. **Ciclo AMD: Acumulación → Manipulación → Distribución** — Smart Money acumula en silencio (OBI neutro, CVD plano), luego manipula el rango para cazar stops (spike falso), luego distribuye con fuerza (CVD colapsa/sube). *RBF: El detector AMD ya implementa esto. El RBF captura la distribución (breakout real). La manipulación previa al breakout es exactamente el rango de consolidación que el sistema detecta.*

2. **Daily bias como filtro universal** — Todo trade en M1 debe alinearse con el sesgo de D1. Si D1 bajista, ignorar señales largas en M1 aunque parezcan perfectas. *RBF: HTF H1 ya da bias. Reforzar: si H1 trend ≠ dirección del trade = score -1 o veto directo.*

3. **Break & Retest = 60%+ win rate garantizado si esperas** — El setup de mayor probabilidad: nivel rompe → precio retesta ese nivel desde el otro lado → entra con confluencia. *RBF: El post-breakout ya es esto. El precio rompe el rango y a veces retesta el techo del rango antes de caer. Feature pendiente: detectar retests post-breakout para mejorar el timing de entrada.*

4. **Order Blocks como zonas de alta probabilidad** — OB es el rango donde Smart Money ejecutó sus órdenes masivas. Cuando precio regresa → respeta con alta probabilidad. *RBF: Similar a la idea de rango anterior como soporte/resistencia. El detector podría marcar "zona de OB" cuando hay una consolidación + breakout + move de 2R+ como zona a vigilar para entradas futuras.*

5. **Manipulation = flush de stops antes del move real** — El movimiento real va precedido de un spike falso en dirección contraria que caza stops de retail. Reconocer este flush = entrada perfecta. *RBF: El veto `oi_covering` detecta cuando OI baja en short (longs cerrando, no shorts abriendo = señal mixta). El flush de stops es la misma idea desde el ángulo de OI.*

6. **Rompimiento falso = alta probabilidad si es contra-tendencia** — Breaks en contra de la tendencia D1 tienen 70%+ probabilidad de ser falsos. Breaks a favor = 70%+ de ser reales. *RBF: El sesgo HTF ya filtra esto. La dirección del trade (solo Shorts actualmente) es consistente con tendencia bajista dominante en el período observado.*

---

### 6. EN VIVO Sup TV — Bitcoin & Futuros
**Fuente:** Sup TV — orderflow en vivo, crypto
**Enfoque:** Footprint chart, delta, liquidaciones, correlaciones inter-mercado

1. **Candela de liquidación = extremo local** — Una vela que causa liquidaciones masivas (spike violento + alto volumen) define el extremo local. El rebote post-liquidación tiene 80% de probabilidad. *RBF: Cuando el precio toca el stop de un trade RBF con una vela de liquidación = probable falso. Feature pendiente: si la vela que activa el stop tiene VR extremo (>8x), registrar como "posible flush" para análisis posterior.*

2. **Correlación BTC → Alts con 30 segundos de lead** — ETH sube/baja antes que BTC. SOL antes que ETH. La cadena da señales anticipadas. *RBF: El sistema opera 5 símbolos simultáneamente. Si ETH CVD colapsa y BTC aún no = anticipar señal en BTC en próximas 1-2 barras. Feature multi-símbolo pendiente.*

3. **Delta P-shape = distribución en techo** — Si el volumen acumulado se concentra en el extremo superior de la vela = distribución. Señal bajista. *RBF: CVD que alcanza extremo y luego cae dentro de la misma barra = patrón de distribución. El campo `bar_delta` + `cvd_slope` pueden capturar esto.*

4. **Horario 8–10am UTC = máxima actividad y liquidez** — Coincide con apertura Europa. OBI y CVD se leen más limpios. Menor slippage. *RBF: London session (08:00–13:00 UTC). Los datos confirman WR=50% en London. El sub-window 08-10am podría ser el más eficiente dentro de London.*

5. **Trapped sellers covering = rally contra-intuitivo** — Si OBI fuertemente negativo pero precio sube = sellers forzados a cubrir → momentum comprador. Entrada long en este setup. *RBF: Esto es el opuesto del patrón RBF Shorts. Para futura expansión a Longs: OBI negativo + CVD recuperándose + precio resistiendo = Long potencial.*

6. **Scale-in en dirección del trade** — Entry 30% → confirma 2-3 velas → add 30% → trailing 40% restante. Compounding dentro del trade. *RBF: El paper trader actualmente es todo-o-nada (full position en entry). El scale-in reduciría riesgo en entries tempranas pero complicaría la lógica de gestión.*

---

### 7. TRADING EN VIVO — $40K con OrderFlow (Carmine Rosato)
**Fuente:** Carmine Rosato — Investor Trade, 7 cifras verificado
**Enfoque:** Scalping ES/crypto con footprint, market DNA, CLC framework

1. **Market DNA = Subasta, no indicadores** — El mercado es una subasta contínua. Los candlesticks son el resultado, no el predictor. CVD/OBI son el proceso. *RBF: Confirmación del diseño. El score se basa en CVD/OBI/VR (proceso), no en patrones de velas (resultado).*

2. **Absorción extendida** — Gran volumen agresivo sin movimiento de precio = el otro lado absorbió esas órdenes. El next move irá contra el aggressor. *RBF: El flag `absorption` en el score capta esto. Pero actualmente no discrimina bien (0% en wins y losses del backtest). Posible que el lookback period necesite calibración.*

3. **Stop hunts en niveles obvios** — Los stops de retail se concentran justo debajo/arriba de niveles redondos y resistencias/soportes visibles. Un flush rápido a esos niveles + rebound = patrón mecánico de 70%+ WR. *RBF: El pre-breakout intenta capturar exactamente esto: precio se acerca al borde del rango (donde están los stops) antes del breakout real. La lógica es correcta.*

4. **Tape speed como indicador de convicción** — Velas que cierran en <20 segundos con movimiento real = mercado "caliente". Velas que tardan 40+ segundos = indecisión. *RBF: Feature pendiente: duración de vela como proxy de convicción. En M1 todas las velas son 60 segundos pero el intrabar movement (cuánto se movió dentro de la vela) ya está capturado en high-low.*

5. **Framework CLC: Context + Location + Confirmation** — Los 3 obligatorios. Context = tendencia HTF. Location = en zona de alta probabilidad. Confirmation = volume/flow confirma. *RBF: Exactamente el score de confluencia en 3 dimensiones. Score ≥ 2 exige al menos 2 de 3 dimensiones activas.*

6. **Divergencia delta en extremos = alta probabilidad de reversión** — Si precio hace nuevo extremo pero delta (CVD) no lo acompaña = divergencia = reversión probable. *RBF: CVD divergence es el patrón clave. El sistema actual requiere `cvd_in_range < 0` para Shorts — que es presión vendedora durante el rango. La divergencia temporal (precio sube pero CVD baja) como señal de entrada es un refinamiento.*

---

### 8. La estrategia de Prop Firm mejor pagada — Okala ($5M payouts)
**Fuente:** Okala — scalping Nasdaq, 70% WR verificado
**Enfoque:** Niveles 80/20, mean reversion, patrones de velas específicos

1. **Niveles 80/20 como fractales de precio** — En cualquier rango de 100 puntos, los extremos del 80% y 20% son magnets. Mercado vuelve a esos fractales. *RBF: En crypto, los niveles psicológicos (52,500 / 53,000 / 50,000 en BTC) actúan igual. El porcentaje del rango donde está el precio al momento del breakout podría ser un feature.*

2. **Fork setup = higher low sin romper el previo** — Vela larga + cuerpo pequeño → siguiente vela hace higher high sin penetrar el low anterior = setup alcista confirmado. El inverso para shorts. *RBF: En M1, este patrón de 2 velas es una forma de detectar "test de rango exitoso". Feature candidato para pre-breakout.*

3. **H-pattern = continuación bajista** — Move bajista fuerte → rebote pequeño al 80% → rollover = entrada short confirmada. *RBF: Este es el patrón RBF post-breakout: colapso → pequeño rebote → continúa. El CVD que se recupera parcialmente y luego colapsa de nuevo es la firma de este H.*

4. **Repair candle = magnet vacío** — Vela sin wick superior en uptrend = órdenes límite sin ejecutar arriba. El mercado las buscará. *RBF: LVN por encima del rango de consolidación = destino probable del precio tras el breakout. El target de 2R a veces se explica así: el precio va a "llenar" la zona de vacuum sobre el rango.*

5. **Over-trading = destructor del WR** — Con más de 3 trades/día el WR cae drásticamente. 1-2 trades limpios > 10 trades mediocres. *RBF: El cooldown de 60 barras ya limita la frecuencia. Pero el sistema opera 5 símbolos simultáneamente, lo que puede generar 5-10 trades/día. El VR_MAX=5 gate y el score≥2 gate ayudarían a reducir cantidad manteniendo calidad.*

---

### 9. Domina el trading ALGORÍTMICO — Noel T (~$1M, 40% WR)
**Fuente:** Noel T — algo trading, Monte Carlo, ensemble systems
**Enfoque:** Risk-adjusted returns, robustez estadística, portfolio de estrategias

1. **Sharpe Ratio > Ganancia Bruta** — Estrategia A: 20% return 100% exposed vs B: 20% return 10% exposed. B es superior. *RBF: El sistema opera con 2% fijo de riesgo por trade. La métrica real de éxito debería ser `expectancy / (max_drawdown%)`, no solo total R.*

2. **Monte Carlo para validar robustez** — Reordena aleatoriamente tus 35 trades 1,000 veces. Si el peor percentil (5%) sigue siendo positivo = sistema robusto. Si la equity curve colapsa en el peor caso = sistema frágil. *RBF: Con bt_output.json ya disponible, este test es factible. Los 35 trades en shuffle 1,000 veces darán el intervalo de confianza real del sistema.*

3. **Ensemble voting** — 3 sistemas independientes (volumen, precio, momentum) confluyen en la misma señal = tamaño triple. Solo uno confluye = tamaño mínimo. *RBF: El score 0-6 ya implementa esto. Score 6 = todos los flags = posición máxima. Confirmación del diseño. El sizing_multiplier experimental sigue esta lógica.*

4. **Overfitting test obligatorio** — Entrena en 60% de datos, valida en 40% que no viste. Si el return cae >50% en out-of-sample = overfitted. *RBF: Los 35 trades actuales son todos in-sample (7 días). Cuando haya 60+ días, hacer este split: Jun 5–16 para calibración, Jun 17+ para validación.*

5. **Drawdown recovery asimétrico** — Perder 50% requiere ganar 100% para recuperar. El objetivo no es maximizar ganancias sino minimizar drawdowns. *RBF: El paper trader con $500 base y riesgo de $10/trade tiene drawdown máximo teórico de ~$190 (19 stops consecutivos). Con WR=45.7%, la probabilidad de 19 stops seguidos es <0.01%. Pero vale la pena calcular el max drawdown esperado por Monte Carlo.*

6. **Strategy portfolio: múltiples estrategias corriendo en paralelo** — 150 algos en incubación. 60% pasan a live. Diversificación de edge. *RBF: RBF + AMD + BE (Buyer Exhaustion) ya son 3 estrategias paralelas. La dirección correcta.*

---

### 10. LIVE Trading — $1M+ Order Flow Trader (Jay Oratani)
**Fuente:** Jay Oratani — stocks/options orderflow, $3K→$7M
**Enfoque:** Market DNA, trapped traders, anomalías de delta

1. **Anomalía de delta = presencia de big player** — Delta que salta 2-3× sobre el promedio histórico en un precio específico = participante grande ejecutando en ese nivel. El mercado lo reconocerá. *RBF: OBI outlier (>3 stddev) + VR alto en ese precio = big player confirmado. +2 al score de confluencia.*

2. **Trapped traders como edge** — Cuando un breakout falla y los que entraron quedan atrapados = el mercado irá a cazar sus stops. Ahí está el edge del sistema. *RBF: El pre-breakout RBF intenta entrar justo antes de que los shorts atrapados del rango sean forzados a cubrir. La mecánica es correcta.*

3. **Bull flag extended vs fresh** — Un bull flag en extensión (>150% ATR del día) tiene WR=10%. Un bull flag en inicio de tendencia = WR=90%. El contexto lo cambia todo. *RBF: Pendiente: añadir variable de "qué tan extendido está el precio" como factor. Si el precio ya se movió >150% del ATR diario antes del setup, reducir el score.*

4. **Pre-session bias: 30 min antes de apertura** — Analiza CVD/OBI de los 30 minutos previos a la apertura de cada sesión. El sesgo ya está formado cuando abre. *RBF: El session tracker ya marca las sesiones. Añadir una ventana de pre-session (30 barras antes de London/NY open) como contexto adicional.*

5. **P-shape = distribución** — Volumen concentrado en el extremo alto de la vela = vendedores distribuyendo en el techo. *RBF: CVD que llega a extremo y colapsa rápido dentro de la misma vela = patrón distributivo. Feature candidato.*

6. **Inconsistencia = problema de exit, no de entry** — Si WR=60% pero equity plana = los exits están mal calibrados. *RBF: Datos propios: WR=45.7% con AvgR wins = +2.03R. El R/loss es perfecto (-1.00R exacto). El problema no es la gestión de salidas sino la selección de entradas (45.7% WR podría ser 55%+ con mejor filtrado).*

---

### 11. Cómo negociar de $6K a $10M — Carmine Rosato (continuación)
**Fuente:** Carmine Rosato — LVN methodology deep dive
**Enfoque:** Volume by Price, absorción, CLC framework completo

1. **Volume by Price > Volume by Time** — Dónde se negoció el volumen importa más que cuándo. Un precio con 10× más volumen que sus vecinos = zona de aceptación (precio justo). Un precio con 0.1× del promedio = zona de rechazo (LVN). *RBF: VR bajo en rango = LVN. VR alto en precio = HVN. El sistema ya utiliza esta distinción. El ratio VR de la vela de breakout vs el promedio del rango es exactamente Volume by Price.*

2. **Delta outlier en LVN** — Si aparece delta extremo (compradores/vendedores agresivos) en una zona LVN = participante grande descubriendo ese precio. El LVN pierde su "vacío". *RBF: Un LVN con OBI extremo = zona que el mercado está re-evaluando activamente. Puede ser entry o invalidación según dirección.*

3. **Stop bajo estructura de mercado, no bajo número redondo** — El stop va bajo el último low de consolidación o bajo el LVN, no bajo el número redondo más cercano (49,000 vs 49,150). *RBF: El stop de RBF es `range_high` para Shorts (borde superior del rango). Esto es stop estructural puro. Confirmado correcto.*

4. **TP1 en primer LVN, TP2 en segundo, trail el resto** — Sistema de salida escalonado basado en estructura de mercado. *RBF: El trailing lock floor activa en 1.90R y deja correr. El "primer LVN" post-breakout es conceptualmente el nivel donde activa el trailing. Alineación conceptual.*

5. **Footprint + Heatmap + Volume Profile alineados** — Cuando 3 herramientas independientes muestran lo mismo = convicción máxima. *RBF: VR (perfil de volumen) + CVD (delta/footprint proxy) + OBI (heatmap/book) = las 3 herramientas. Ya implementado.*

---

### 12. ROBA este TRUCO de liquidez — Traveling Trader Z
**Fuente:** Traveling Trader Z — multi-mercado, 15+ años
**Enfoque:** Framework universal: sideways principle, trend line break, filter stack

1. **Sideways principle = base de todo** — Los mercados pasan 60-70% del tiempo en consolidación entre soporte y resistencia. El resto del tiempo están en tendencia. Operar rangos ≠ operar tendencias. *RBF: El rango 0.08–0.55% que detecta RBF ES la consolidación. El VR≥3× al salir de ella es el inicio de la tendencia. El sistema ya distingue correctamente.*

2. **Frequency + Proximity como validación de nivel** — Un nivel válido debe: (a) haber sido testeado 2+ veces (frequency) y (b) haber sido testeado recientemente (proximity). Sin ambos = nivel débil. *RBF: Candidato: añadir conteo de tests del rango como feature. Rango testado 3+ veces desde ambos lados antes del breakout = consolidación real. Rango con 1 solo test = puede ser ruido.*

3. **Trend line break = "pocket" de entrada** — El sweet spot de risk/reward está en el momento en que rompe la línea de tendencia del move previo. No en el inicio (muy riesgoso) ni al final (RR deteriorado). *RBF: El breakout del rango es exactamente el "pocket". El VR≥3 en ese momento confirma que es ruptura real, no ruido.*

4. **EMA21 como nivel de retest** — Post-break, precio retesta la EMA21 en 58% de casos antes de continuar. *RBF: Feature candidato: si en el momento de la señal el precio está cerca del EMA21 (dentro del rango) = entrada de mejor calidad. El VWAP ya cumple función similar.*

5. **Correlación en pares: cuidado con concentración** — Si operas múltiples pares correlacionados al mismo tiempo, el riesgo se concentra aunque parezca diversificado. *RBF: BTC/ETH están altamente correlacionados. Una señal simultánea en BTC y ETH es efectivamente una posición 2× en el mismo move. El cooldown por símbolo ya lo controla parcialmente.*

6. **Análisis en daily, entry en M1: no cambiar el timeframe de análisis** — Nunca cambies de TF para "encontrar" el setup que quieres. Decisión en TF alto, ejecución en bajo. *RBF: HTF H1 para contexto, M1 para entry. Regla clara y ya implementada.*

---

### 13. PRO Trader — Super Simple Strategy (Rajan D, DND Capital)
**Fuente:** Rajan D — auction theory, 25 años de experiencia
**Enfoque:** Market como subasta, price discovery, ejecución mecánica sin indicadores

1. **Break of Structure + Retest = única entry** — No hay otra forma válida de entrar. Esperar el retest post-break es no-negociable. *RBF: Post-breakout ya espera close fuera del rango (= break of structure). El retest automático ocurre cuando el precio regresa al borde del rango antes de continuar. Feature pendiente: detectar y explotar estos retests.*

2. **Stop = mitad del target** — Si target = 100 pts, stop = 50 pts. RR natural de 2:1 siempre. *RBF: RR_SHORT = 2.0 ya implementado. Stop = range size × 1 desde entry. Alineado.*

3. **Ventanas prime: 7-11am UK y 1-4pm UK** — Fuera de esas ventanas el mercado es chop. Disciplina de no operar fuera de ventanas. *RBF: London (07:00–13:00 UTC) y NY (13:00–20:00 UTC). Los datos confirman que todas las sesiones son positivas. Posible mejora: refinar a sub-ventanas específicas dentro de cada sesión.*

4. **Auction Area (Value Area)** — El precio pasa más tiempo donde hay más volumen = zona de valor. Cuando se aleja mucho = buscará regresar (mean reversion). *RBF: POC (Point of Control) del Volume Profile ya visible en chart. Candidato a feature: si target RBF está más cerca del POC que el entry = probabilidad de llegar al target más alta.*

5. **Trailing en estructura: stop al low de consolidación previa** — No trailing arbitrario. Cuando precio rompe consolidación anterior, mueve el stop al low de esa consolidación. *RBF: El trailing lock floor en 1.90R es un proxy de esto. Mejoría posible: en lugar de 1.90R fijo, usar el LVN más cercano como nivel de trailing dinámico.*

6. **WR 58% + RR 2:1 = matemáticamente óptimo** — No necesitas más. Optimizar el WR más allá es marginal; el RR tiene más impacto. *RBF: Datos propios: WR=45.7% + RR trailing avg=1.97:1. Matemáticamente positivo. La palanca más grande está en subir WR a ~55%, no en mejorar el RR (ya casi en 2:1).*

---

### 14. How To Trade Real Fair Value Gaps — Carmine Rosato
**Fuente:** Carmine Rosato — FVG como mecánica de orderflow
**Enfoque:** FVG no es patrón visual sino subasta incompleta explicada por orderflow

1. **FVG = subasta incompleta = LVN** — Un FVG (gap de 3 velas) se forma porque el precio voló sin procesar volumen. Es un LVN con forma geométrica visible. El mercado lo llena porque le "falta" precio justo ahí. *RBF: Cuando el breakout crea un FVG visual, el VR bajo en esa zona confirma el LVN. El target de 2R a menudo coincide con el llenado del FVG. Candidato: usar FVGs como targets dinámicos en lugar de RR fijo.*

2. **Triple wick = ultra-magnet** — Si el precio intenta romper un nivel 3 veces con wick pero sin cierre = los tres wicks forman un "triple rechazo". La 4ta visita tiene >80% de probabilidad de llenado completo. *RBF: Pre-breakout captura exactamente este patrón. El rango siendo testado 3+ veces = presión acumulada = breakout inminente con alto VR.*

3. **Spoof vs real order book** — Órdenes pasivas grandes que aparecen y desaparecen sin ejecutarse = spoof (manipulación). OBI inestable (spike en una barra, luego desaparece) = ignorar. OBI sostenido 2-3 barras = real. *RBF: Feature de filtrado: si OBI tiene varianza alta (oscila mucho entre barras) = mercado manipulado = bajar score. Si OBI es estable en dirección = confiable.*

4. **Contexto determina el resultado del mismo patrón** — El mismo FVG en zona de soporte = rebote. En zona de resistencia = continuación bajista. Sin contexto, el patrón solo tiene 50% WR. *RBF: El score ya incluye `vwap_bias` (lado del VWAP) como contexto. El HTF H1 alineado como contexto adicional. Con ambos el WR sube significativamente.*

5. **Absorción extendida** — Cuando hay gran volumen pero precio no se mueve = otro lado absorbió todo. El absorbedor ganó. El agresivo perdió. El move irá contra el agresivo. *RBF: CVD outlier + precio estático = absorción. Si es bajista (vendedores agresivos absorbidos por compradores) + en soporte = long setup. Inverso = short setup RBF.*

---

### 15. Trading simplificado — 3 pasos (Brando, $8M+ verified)
**Fuente:** Brando — options trader, prop firm focus
**Enfoque:** Mentality → Levels → Execution mecánico

1. **3 pilares antes de la estrategia** — Emociones controladas + comprensión RR + enfoque en aprender sobre ganar. Sin esto, cualquier estrategia falla. *RBF: El sistema paper trading automatizado elimina la emoción de la ecuación. El paper trader ejecuta mecánicamente. Ventaja estructural.*

2. **Extended vs fresh setup** — Si el precio ya se movió >150% del ATR diario antes del setup = mean reversion. Si está en inicio del move = momentum. Contexto crítico para el target. *RBF: Candidato: añadir "ATR consumido" como feature. Si el move pre-breakout ya consumió >150% del ATR M1 = bajar score (entrada tardía).*

3. **Nivel válido mínimo = 2 hits** — Un precio solo testeado 1 vez no es un "nivel", es un accidente. Mínimo 2 hits para considerar como zona. *RBF: Para el rango de consolidación: ¿cuántas veces rebotó en el techo/piso del rango antes del breakout? Rango con 3+ rebotes = consolidación real, no ruido. Feature candidato.*

4. **Niveles psicológicos como trampa** — Los números redondos (50,000 / 60,000 BTC) son conocidos por todos = stops de todos están ahí = precio va a buscarlos = flush = oportunidad post-flush. *RBF: No tratar un breakout de número redondo como señal confirmada sin VR + CVD. El flush puede ser el breakout. Pero el setup real viene después del flush (pre-breakout o post).*

5. **Pocket = sweet spot de riesgo** — La zona entre la señal contraria a tendencia (alta probabilidad de reversión) y la señal a favor de tendencia (bajo RR) = "pocket" donde RR es máximo. *RBF: El breakout del rango es exactamente el pocket: el rango ya venció la fuerza de reversión (precio no volvió al centro) pero el move no está extendido (VR<5×). El sistema opera en el pocket.*

6. **Reglas mecánicas > intuición** — "Si condiciones XYZ entonces ejecutar ABC". Decidido antes, no durante. La intuición durante el trade es ruido. *RBF: El paper trader en Rust es 100% mecánico. Zero intuición. Ventaja sistemática confirmada por esta fuente.*

7. **WR 50-58% + RR 2:1+ = sustentable** — No optimizar para 80% WR. Optimizar para RR. La matemática del RR compensa el WR bajo. *RBF: Datos propios: WR=45.7% + trailing avg=1.97:1. Positivo pero con margen de mejora. Subir WR a 55% con los filtros candidatos identificados (VR 3-5x, score≥2, range<0.50%) agregaría ~3-4R al total.*

---

## SÍNTESIS — Features candidatos para RBF

Ordenados por confianza/impacto:

| Feature candidato | Fuentes | Impacto estimado | Datos necesarios |
|---|---|---|---|
| VR_MAX = 5 para post-breakout | Análisis propio + 3 fuentes | ★★★ WR>5x=25% en datos | n≥25 en bucket >5x |
| Score≥2 en script Python (ya en live) | 5 fuentes | ★★★ Score1 AvgR=-0.18R | Implementar en backtest |
| RANGE_MAX_PCT bajar de 0.55% a 0.50% | 4 fuentes | ★★ AvgR=-0.03R en >0.50% | n≥25 en bucket >0.50% |
| Conteo de retests del rango | 7 fuentes | ★★ Mayor evidencia anecdótica | Feature nuevo en barras |
| Circuit breaker: max 3% drawdown/sesión | 4 fuentes | ★★ Gestión de riesgo | Implementar en paper trader |
| ATR consumido pre-setup como feature | 3 fuentes | ★ Candidato | Feature nuevo, n pequeño |
| London sub-window 08-10am UTC | 2 fuentes | ★ No confirmado en datos | Necesita análisis por hora |
| Multi-símbolo correlation gate | 3 fuentes | ★ BTC+ETH simultáneo = riesgo x2 | Lógica de gestión |

---

*Fuentes completas en `docs/orderflow/revisar/`. Siguiente revisión cuando n_total≥100 trades.*
