# Flow Surface — Propuesta de integración: nuevos módulos, estrategias e indicadores

> Cada ítem nuevo se describe con su rol, cómo se integra al sistema existente, y con qué estrategias interactúa directamente.

---

## Leyenda de estado

| Badge | Significado |
|---|---|
| `NUEVO` | No existe en el sistema — crear desde cero |
| `EXTIENDE` | Amplía un módulo ya existente |
| `REFACTOR` | Reorganizar lógica ya existente en módulo propio |
| `EXISTE` | Ya implementado — referencia para contexto |

---

## Fase 1 — Impacto alto, esfuerzo bajo

### 1. `MarketStructureTracker` — BOS / CHoCH / Rangos HTF

| Campo | Detalle |
|---|---|
| **Estado** | `NUEVO` |
| **Tipo** | Módulo de contexto |
| **Capa** | L6 — Macroestructura de precio |
| **Ubicación propuesta** | `data/src/structure/market_structure.rs` |
| **Prioridad** | 1 |

**Qué hace:**
Evalúa la estructura de precio en timeframes altos (1h / 4h). Detecta:
- **BOS** (Break of Structure): el precio rompe un swing previo — confirma continuación
- **CHoCH** (Change of Character): el precio rompe en dirección opuesta — señal de cambio
- **Swing highs / lows** activos del día y la sesión
- **Premium zone** (>75% del rango), **Discount zone** (<25%), **Equilibrium** (50%)

**Cómo se integra:**
Se conecta a `scoring.rs` como multiplicador contextual. No bloquea señales, las pondera:

```
htf_bias = Bullish → score × 1.2 (señal larga) / score × 0.7 (señal corta)
htf_bias = Bearish → score × 0.7 (señal larga) / score × 1.2 (señal corta)
htf_bias = Neutral → score × 1.0
context_location = Discount → bonus +0.05 a señales largas
context_location = Premium → bonus +0.05 a señales cortas
```

**Rol en cada estrategia:**

| Estrategia | Rol del MarketStructureTracker |
|---|---|
| **VAFA** | Confirma si el failed auction ocurre en zona de discount (largo) o premium (corto) — aumenta convicción |
| **VWAP Pullback** | Valida que el pullback va a favor del HTF bias — filtra pullbacks contra-tendencia |
| **LVN** | Verifica que el breakout de vacío va en dirección de la estructura HTF |
| **LiqHunt** | Confirma que la liquidación apunta a sweep en zona premium/discount relevante |
| **FER** | El exhaustion de funding es más fiable cuando ocurre en extremo de rango HTF |
| **SMD** | La divergencia smart money es más fuerte cuando coincide con un CHoCH en HTF |

---

### 2. `SessionTracker` — Asia / London / NY

| Campo | Detalle |
|---|---|
| **Estado** | `NUEVO` |
| **Tipo** | Módulo de contexto |
| **Capa** | L6 — Estructura temporal |
| **Ubicación propuesta** | `src/session/session_tracker.rs` |
| **Prioridad** | 2 |

**Qué hace:**
Identifica la sesión activa en UTC y su fase (apertura, mid, cierre). Cada sesión tiene comportamiento estadístico diferente:
- **Asia** (00:00–08:00 UTC): acumulación, rangos, baja volatilidad
- **London** (07:00–12:00 UTC): breakouts, liquidity grabs, alta volatilidad
- **NY** (13:00–17:00 UTC): continuación o reversión del move de London
- **London/NY Overlap** (13:00–16:00 UTC): máxima liquidez, ideal para estrategias de flow

**Cómo se integra:**
Cada estrategia declara un campo `valid_sessions: Vec<Session>`. El `router.rs` descarta candidatos fuera de sesión antes de pasar al scoring.

**Rol en cada estrategia:**

| Estrategia | Sesiones válidas | Razón |
|---|---|---|
| **VAFA** | London, NY | Acceptance/rejection requiere liquidez real |
| **VWAP Pullback** | London open, NY open | Pullbacks más limpios en aperturas de sesión |
| **LVN** | London, NY Overlap | Vacíos se llenan con liquidez de sesión |
| **LiqHunt** | London open, NY open | Liquidaciones masivas ocurren en aperturas |
| **FER** | Asia, London pre-open | Funding extremo se acumula en rangos lentos |
| **SMD** | London, NY | Divergencia institucional necesita volumen de sesión |

---

### 3. `OrderBlockDetector` — OB alcista / bajista

| Campo | Detalle |
|---|---|
| **Estado** | `NUEVO` |
| **Tipo** | Detector / indicador de precio |
| **Capa** | L6 — Macroestructura de precio (ICT) |
| **Ubicación propuesta** | `src/detectors/order_block.rs` |
| **Prioridad** | 2 |

**Qué hace:**
Detecta Order Blocks — la última vela bajista antes de un impulso alcista fuerte (OB alcista), o la última vela alcista antes de un impulso bajista fuerte (OB bajista). Son zonas donde los institucionales colocaron órdenes pendientes y el precio tiende a retornar a ellas.

Criterios de validez:
- El impulso posterior rompe al menos 2 swings previos (confirma institucional)
- El OB no ha sido "mitigado" (el precio aún no volvió a esa zona)
- OB dentro del rango premium/discount relevante tiene mayor peso

**Cómo se integra:**
Complementa el Volume Profile existente. VP dice dónde hay volumen acumulado, OB dice dónde están las órdenes institucionales pendientes. Juntos forman una zona de confluencia fuerte.

**Rol en cada estrategia:**

| Estrategia | Rol del OrderBlockDetector |
|---|---|
| **VAFA** | Un OB no mitigado en VAL/VAH eleva significativamente la convicción del failed auction |
| **VWAP Pullback** | Reentrada en VWAP que coincide con un OB = confluencia de alta calidad |
| **LVN** | OB al inicio de un LVN confirma que el vacío tiene origen institucional |
| **SMD** | Divergencia smart money en zona de OB = setup de máxima calidad |

---

### 4. `FVGDetector` — Fair Value Gaps

| Campo | Detalle |
|---|---|
| **Estado** | `NUEVO` |
| **Tipo** | Detector / indicador de precio |
| **Capa** | L6 — Macroestructura de precio (ICT) |
| **Ubicación propuesta** | `src/detectors/fvg.rs` |
| **Prioridad** | 3 |

**Qué hace:**
Detecta Fair Value Gaps — desequilibrios de 3 velas donde la vela del medio tiene un gap entre el high de la vela anterior y el low de la vela posterior (o viceversa). El precio actúa como imán hacia estos gaps. Tipos:
- **Bullish FVG**: gap entre high[i-1] y low[i+1] donde vela[i] es alcista fuerte
- **Bearish FVG**: gap entre low[i-1] y high[i+1] donde vela[i] es bajista fuerte
- Estado: `unfilled` / `partially_filled` / `filled` (deja de ser relevante cuando se llena)

**Cómo se integra:**
Funciona como imán de precio y zona de retorno. Se cruza con VP y OB para formar zonas de confluencia. Extiende la lógica de `thin_zone` en LVN.

**Rol en cada estrategia:**

| Estrategia | Rol del FVGDetector |
|---|---|
| **VAFA** | FVG no llenado cerca de VAL/VAH refuerza la zona de failed acceptance |
| **VWAP Pullback** | Pullback que retorna a un FVG + VWAP = zona de retorno de altísima confluencia |
| **LVN** | Un LVN que coincide con un FVG bullish/bearish confirma la dirección del breakout |
| **LiqHunt** | FVG por encima del precio = target natural después de la liquidación |

---

### 5. Scoring diferenciado por familia de estrategia

| Campo | Detalle |
|---|---|
| **Estado** | `REFACTOR` |
| **Tipo** | Lógica de scoring |
| **Capa** | Core — scoring.rs |
| **Ubicación propuesta** | `src/strategy/scoring.rs` — agregar `StrategyProfile` |
| **Prioridad** | 3 |

**Qué hace:**
Actualmente todas las estrategias compiten con los mismos pesos en `scoring.rs`. Las estrategias institucionales (LiqHunt, FER, SMD) tienen señales infrecuentes pero de altísima convicción — el scoring único las subvalora cuando sus inputs están en extremos.

Propuesta de pesos diferenciados:

| Componente | Mercado puro (VAFA/VWAP/LVN) | Institucional (LiqHunt/FER/SMD) |
|---|---|---|
| CVD slope | 0.25 | 0.15 |
| Taker imbalance | 0.20 | 0.10 |
| Delta | 0.10 | 0.05 |
| Target dist | 0.25 | 0.20 |
| R:R | 0.20 | 0.15 |
| Funding/OI/LS | — | 0.35 |
| **min_score** | 0.60 | 0.55 |

**Rol en cada estrategia:**

| Estrategia | Impacto del cambio |
|---|---|
| **VAFA / VWAP / LVN** | Sin cambio funcional — pesos ya calibrados para flow puro |
| **LiqHunt** | OI slope + cascade + funding ahora pesan 35% — señales fuertes ya no son subvaloradas |
| **FER** | Funding extremo + retail divergencia pesa más — mejora detección de reversiones reales |
| **SMD** | LS ratio + OI maturity como señal dominante cuando están en extremos |

---

## Fase 2 — Impacto alto, esfuerzo medio

### 6. `LiqMapTracker` — Mapa de stops estimados

| Campo | Detalle |
|---|---|
| **Estado** | `NUEVO` |
| **Tipo** | Tracker institucional |
| **Capa** | L4 — Liquidaciones |
| **Ubicación propuesta** | `data/src/institutional/liq_map_tracker.rs` |
| **Prioridad** | 4 |

**Qué hace:**
Diferente al `LiquidationTracker` existente (que registra liquidaciones ya ocurridas), este módulo **estima dónde están los stops concentrados que aún no fueron activados**. El precio es jalado hacia donde hay mayor densidad de liquidez pendiente.

Metodología:
- OI por nivel de precio (de datos de exchange)
- Leverage promedio estimado por nivel
- Distancia al precio actual → calcula "presión de liquidación"
- Output: `liq_density_above` y `liq_density_below` como vectores de densidad

**Rol en cada estrategia:**

| Estrategia | Rol del LiqMapTracker |
|---|---|
| **LiqHunt** | Confirma hacia dónde apunta la caza — liq_density_above confirma target long, abajo confirma target short |
| **SMD** | La divergencia smart money es más fuerte cuando apunta hacia una zona de alta densidad de stops |
| **LVN** | Un breakout de LVN que apunta a una zona de alta liq_density tiene mayor probabilidad de continuación |
| **VAFA** | El target post-auction se calibra con la densidad de liquidez más cercana |

---

### 7. `SpoofDetector` — Detección L2 de manipulación

| Campo | Detalle |
|---|---|
| **Estado** | `NUEVO` |
| **Tipo** | Detector de microestructura |
| **Capa** | L2 / L8 — Order book + manipulación |
| **Ubicación propuesta** | `src/detectors/spoof_detector.rs` |
| **Prioridad** | 5 |

**Qué hace:**
Detecta órdenes grandes que aparecen en el book y se cancelan antes de ejecutarse cuando el precio se acerca. Señal de manipulación del order book para empujar/frenar el precio artificialmente.

Criterios de detección (versión L2, sin necesitar L3):
- Orden ≥ N × avg_size aparece en el book
- El precio se acerca a ±0.05% de esa orden
- La orden desaparece en < 500ms sin ejecutarse
- Señal: `spoof_detected: bool` + `spoof_side: Bid/Ask`

**Cómo se integra:**
Extiende `toxic_flow_gate()` con una condición adicional. Si `spoof_detected = true`, el gate puede bloquear la señal o reducir el score con un multiplicador.

**Rol en cada estrategia:**

| Estrategia | Rol del SpoofDetector |
|---|---|
| **LVN** | Una pared falsa en la zona de vacío puede simular resistencia que no existe — spoof detection evita falsas señales |
| **VAFA** | Spoof en VAH/VAL puede simular acceptance/rejection artificialmente |
| **LiqHunt** | Detecta si la "pared" que genera el squeeze es real o fabricada |
| **Todas** | Bloqueo global en toxic_flow_gate cuando spoof_detected en dirección de la señal |

---

### 8. `SmartMoneyScore` — Índice institucional unificado

| Campo | Detalle |
|---|---|
| **Estado** | `REFACTOR` |
| **Tipo** | Tracker institucional |
| **Capa** | L5 — Institucional |
| **Ubicación propuesta** | `data/src/institutional/smart_money_score.rs` |
| **Prioridad** | 6 |

**Qué hace:**
FER y SMD calculan divergencia institucional de forma redundante con lógica casi idéntica. Extraerlo como módulo compartido elimina duplicación, estandariza el cálculo y lo hace disponible para todas las estrategias.

Output: `smart_money_score: f32` en rango −1.0 a +1.0
- +1.0: institucionales fuertemente largos, retail fuertemente corto
- −1.0: institucionales fuertemente cortos, retail fuertemente largo
- 0.0: sin divergencia clara

Inputs combinados:
- `top_long%` vs `retail_long%` (diferencial ponderado)
- `oi_trend` (creciendo vs cayendo)
- `funding_regime` (extremo positivo/negativo)
- `cascade` (señal de agotamiento)

**Rol en cada estrategia:**

| Estrategia | Rol del SmartMoneyScore |
|---|---|
| **FER** | Consume `smart_money_score` directamente en vez de recalcularlo — simplifica el detector |
| **SMD** | Ídem — SMD usa el score como condición de entrada principal |
| **LiqHunt** | Score > 0.6 en dirección del hunt confirma que los institucionales están del mismo lado |
| **VWAP** | Score disponible como confirmación opcional en reentradas de alta convicción |

---

## Fase 3 — Refinamiento cuando el sistema esté estable

### 9. `LOBRegenerationTracker` — Velocidad de recovery del book

| Campo | Detalle |
|---|---|
| **Estado** | `NUEVO` |
| **Tipo** | Detector avanzado de microestructura |
| **Capa** | L2 — Order book avanzado |
| **Ubicación propuesta** | `src/detectors/lob_regen.rs` |
| **Prioridad** | 8 |

**Qué hace:**
Mide cuánto tarda el book en recuperar profundidad después de que una orden grande lo consume. Book que se regenera en < 200ms = soporte/resistencia real con participantes activos. Book que tarda > 1s en regenerarse = zona débil, vacío real.

**Rol en cada estrategia:**

| Estrategia | Rol |
|---|---|
| **LVN** | Confirma que el thin_zone es genuino — libro que no se regenera = vacío estructural real |
| **VAFA** | Regeneración rápida en VAL/VAH = defensa institucional real de esa zona |

---

### 10. L3 Order Flow Feed

| Campo | Detalle |
|---|---|
| **Estado** | `NUEVO` |
| **Tipo** | Fuente de datos |
| **Capa** | L3 — Datos tick a tick |
| **Ubicación propuesta** | `data/src/feeds/l3_feed.rs` |
| **Prioridad** | 10 |

**Qué hace:**
Datos raw de cada orden individual: timestamp de microsegundo, ciclo de vida completo (placed → modified → cancelled / filled), cancel-to-fill ratio por nivel de precio. Permite spoof detection con certeza (vs la versión L2 probabilística del ítem 7).

**Nota:** Requiere feed especial (Binance/Bybit tienen L3 parcial). Alto costo de infraestructura y storage. Implementar solo después de que el sistema esté validado en paper y se identifique un gap específico que L2 no pueda resolver.

**Rol en cada estrategia:**

| Estrategia | Rol |
|---|---|
| **Todas** | Reemplaza la versión probabilística del SpoofDetector con detección determinística |
| **LiqHunt** | Identifica órdenes institucionales reales vs órdenes de retail en tiempo real |

---

## Resumen consolidado — todos los módulos

| # | Módulo | Tipo | Estado | Fase | Estrategias que impacta |
|---|---|---|---|---|---|
| 1 | `MarketStructureTracker` (BOS/CHoCH) | Contexto HTF | `NUEVO` | 1 | Todas (×6) |
| 2 | `SessionTracker` | Contexto temporal | `NUEVO` | 1 | Todas (×6) |
| 3 | `OrderBlockDetector` | Detector ICT | `NUEVO` | 1 | VAFA, VWAP, LVN, SMD |
| 4 | `FVGDetector` | Detector ICT | `NUEVO` | 1 | VAFA, VWAP, LVN, LiqHunt |
| 5 | Scoring diferenciado | Refactor core | `REFACTOR` | 1 | LiqHunt, FER, SMD |
| 6 | `LiqMapTracker` | Tracker institucional | `NUEVO` | 2 | LiqHunt, SMD, LVN, VAFA |
| 7 | `SpoofDetector` | Detector L2 | `NUEVO` | 2 | Todas (via toxic_flow_gate) |
| 8 | `SmartMoneyScore` | Refactor institucional | `REFACTOR` | 2 | FER, SMD, LiqHunt, VWAP |
| 9 | `LOBRegenerationTracker` | Microestructura avanzada | `NUEVO` | 3 | LVN, VAFA |
| 10 | L3 Feed | Fuente de datos | `NUEVO` | 3 | Todas |

---

## Arquitectura propuesta — dónde vive cada nuevo módulo

```
src/
├── detectors/
│   ├── order_block.rs          ← NUEVO (3)
│   ├── fvg.rs                  ← NUEVO (4)
│   ├── spoof_detector.rs       ← NUEVO (7)
│   └── lob_regen.rs            ← NUEVO (9)
├── session/
│   └── session_tracker.rs      ← NUEVO (2)
└── strategy/
    └── scoring.rs              ← REFACTOR (5) — agregar StrategyProfile

data/src/
├── structure/
│   └── market_structure.rs     ← NUEVO (1)
├── institutional/
│   ├── smart_money_score.rs    ← REFACTOR (8)
│   └── liq_map_tracker.rs      ← NUEVO (6)
└── feeds/
    └── l3_feed.rs              ← NUEVO (10) — fase 3
```
