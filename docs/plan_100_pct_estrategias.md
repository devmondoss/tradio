# Plan hacia 100% — Cada estrategia como trader profesional

> El sistema no existe para reemplazar al trader — existe para complementar
> la toma de decisiones con la precisión, velocidad y consistencia que
> un humano no puede mantener durante horas. Este documento define qué
> significa "100%" para cada estrategia y el camino para llegar ahí.

---

## Filosofía del 100%

Un trader profesional hace 4 cosas que el sistema debe replicar:

```
1. LEE el contexto antes de entrar
   → No entra en cualquier momento — espera su setup
   → El sistema lo hace con: ToxicFlowGate + Regime + HTF Bias + SessionTracker

2. CONFIRMA con múltiples confluencias
   → No entra solo porque el precio llegó a un nivel
   → El sistema lo hace con: scoring multi-capa + OB + FVG + SmartMoneyScore

3. GESTIONA el riesgo activamente
   → Mueve el stop cuando el trade está bien encaminado
   → El sistema lo hace con: TradeManager (BreakEven → Trailing estructural)

4. APRENDE de cada trade
   → Sabe qué setups funcionan mejor en qué condiciones
   → El sistema lo hace con: OutcomeTracker + análisis por estrategia/sesión/régimen
```

El "100%" no significa perfección — significa que el sistema toma la misma
decisión que tomaría un trader profesional con disciplina perfecta y sin fatiga.

---

## Estado actual y gap hacia 100% por estrategia

---

### 1. VAFA — Value Area Failed Auction

**Qué busca un profesional:**
El precio intenta salir del rango del Volume Profile (romper VAH o VAL),
no logra aceptación del otro lado, y regresa al interior. El profesional
entra cuando confirma que el rechazo es real — no un pullback temporal.

**Estado actual del sistema: 78%**

| Componente | Estado | Gap |
|---|---|---|
| Failed acceptance detection | ✅ Implementado | — |
| Footprint absorption | ✅ Implementado | — |
| Delta + CVD confirm | ✅ Implementado | — |
| HTF Bias filter | ✅ Implementado | — |
| OB en VAL/VAH como confirmación | ⚠️ Fase A (peso 0) | Esperar validación Fase B |
| FVG cerca del nivel | ⚠️ Fase A (peso 0) | Esperar validación |
| **Régimen chop detection** | ❌ No existe | **Gap crítico** |
| Cooldown post-señal | 🔧 Fix 01 pendiente | — |

**Gap crítico — régimen chop:**
VAFA genera más falsas señales cuando el mercado está en chop puro
(precio rebotando entre VAH y VAL sin intentar salir de verdad).
El Regime classifier tiene "Chop" como categoría — hay que agregar una
condición explícita en VAFA:

```rust
// En VAFA detector
if ctx.regime == Regime::Chop {
    // En chop, el failed acceptance tiene que ser MUCHO más claro
    // Exigir absorción más fuerte Y delta en contra más pronunciado
    min_absorption_ratio = 2.0;  // vs 1.3 normal
    min_delta_threshold  = -0.35; // vs -0.20 normal
}
```

**Plan al 100%:**
1. Fix 01 (cooldown) — +5%
2. OB Fase B en VAH/VAL — +7%
3. Chop regime stricter gates — +10%

**Timeline estimado: 6-8 semanas**

---

### 2. VWAP Pullback Continuation

**Qué busca un profesional:**
En una tendencia clara, el precio retrocede al VWAP (o AVWAP de un swing
relevante) y muestra signos de reanudación en la dirección original.
El profesional no entra en el primer toque — espera confirmación de flujo.

**Estado actual del sistema: 82%**

| Componente | Estado | Gap |
|---|---|---|
| VWAP / AVWAP anchor | ✅ Implementado | — |
| BOS confirm (tendencia válida) | ✅ HTF Bias | — |
| CVD slope reentry confirm | ✅ Implementado | — |
| fast_slope gate (Fix pusheado) | ✅ Fix aplicado | — |
| Intrabar override ±0.35 (Fix pusheado) | ✅ Fix aplicado | — |
| OB en zona de pullback | ⚠️ Fase A | Esperar validación |
| FVG en zona de pullback | ⚠️ Fase A | Esperar validación |
| **AVWAP de swing relevante** | ⚠️ Parcial | Solo usa AVWAP-BOS, falta AVWAP de high/low del día |
| Session gate | ✅ Implementado | Verificar asignación London/NY |

**Gap principal — AVWAP multi-anchor:**
Un trader profesional no solo mira el VWAP del día — mira el AVWAP
anclado desde el último swing high/low relevante, desde el inicio de
la sesión, y desde el último BOS. Cada uno actúa como imán diferente.

```rust
pub struct AvwapSet {
    pub daily:         f64,  // VWAP del día — ya existe
    pub session:       f64,  // AVWAP desde apertura de sesión — nuevo
    pub last_swing:    f64,  // AVWAP desde último swing high/low — nuevo
    pub last_bos:      f64,  // AVWAP desde último BOS — ya existe
}
// Un pullback que toca 2+ de estos AVWAPs = zona de alta confluencia
```

**Plan al 100%:**
1. Fix 01 (cooldown) — +3%
2. AVWAP multi-anchor (session + last_swing) — +8%
3. OB Fase B — +5%
4. FVG Fase B — +2%

**Timeline estimado: 4-6 semanas**

---

### 3. LVN — Liquidity Vacuum Breakout

**Qué busca un profesional:**
Una zona de bajo volumen en el Volume Profile donde el precio se mueve
rápido porque no hay órdenes que lo frenen. El profesional lo usa como
"carretera" — una vez que el precio entra, apunta al HVN del otro extremo.

**Estado actual del sistema: 80%**

| Componente | Estado | Gap |
|---|---|---|
| LVN detection en VP | ✅ Implementado | — |
| Flow align (CVD + taker) | ✅ Implementado | — |
| Target HVN del otro lado | ✅ Implementado | — |
| HTF Bias filter | ✅ Implementado | — |
| Stacked imbalance confirm | ✅ Implementado | — |
| OB al inicio del LVN | ⚠️ Fase A | Confirma origen institucional |
| FVG dentro del LVN | ⚠️ Fase A | Confirma el vacío |
| **LOB regeneration rate** | ❌ No existe | Confirma vacío genuino vs falso |
| SpoofDetector en paredes | ⚠️ Nuevo | Validar falso-positivo rate |

**Gap principal — LOB regeneration:**
Un LVN genuino se reconoce porque cuando el precio entra, el libro
no se reconstituye rápido — no hay participantes dispuestos a defender
ese nivel. Sin medir la velocidad de regeneración del libro, el sistema
no puede distinguir un LVN genuino de uno que parece vacío pero tiene
participantes esperando.

```rust
// LOBRegenerationTracker — a agregar en Fase 3
pub struct LobRegenData {
    pub recon_time_ms: u64,      // ms que tardó el libro en recuperarse post-trade
    pub recon_depth_ratio: f64,  // % de la profundidad original recuperada en 1s
}
// En LVN: si recon_depth_ratio < 0.40 en 1s → vacío genuino confirmado
```

**Plan al 100%:**
1. Fix 01 (cooldown) — +3%
2. OB + FVG Fase B — +7%
3. SpoofDetector validado — +5%
4. LOBRegenerationTracker (Fase 3) — +5%

**Timeline estimado: 8-12 semanas**

---

### 4. LiqHunt — Liquidation Hunt

**Qué busca un profesional:**
Un sweep de liquidez donde el precio barre stops minoristas (toca un
swing high/low obvio) y luego revierte con fuerza. El profesional
identifica la acumulación de stops ANTES del sweep y entra en la reversión
cuando el flujo confirma que los institucionales ya tienen su liquidez.

**Estado actual del sistema: 85%**

| Componente | Estado | Gap |
|---|---|---|
| Liq cascade detection | ✅ Implementado | — |
| OI slope confirm | ✅ Implementado | — |
| Taker ratio | ✅ Implementado | — |
| CVD slope | ✅ Implementado | — |
| Session gate (London/NY open) | ✅ Implementado | — |
| **LiqMapTracker como target** | ⚠️ Fix 03 — Fase A | Validación en curso |
| **Sub-fase apertura +15min** | ❌ No existe | London/NY open = momento de mayor edge |
| FVG post-sweep como target | ⚠️ Fase A | FVG dejado por el sweep |
| Cooldown post-señal | 🔧 Fix 01 pendiente | — |

**Gap principal — sub-fase de apertura:**
Las liquidaciones masivas ocurren con mayor frecuencia en los primeros
15 minutos de London open y NY open. Este sub-periodo merece un
multiplicador adicional en scoring:

```rust
pub enum SessionPhase {
    PreOpen,
    OpeningRush,    // primeros 15min → LiqHunt score ×1.15
    MidSession,
    Closing,
}
// SessionTracker debe emitir SessionPhase además de Session
```

**Plan al 100%:**
1. Fix 01 (cooldown) — +3%
2. Fix 03 (LiqMap Fase A → B) — +5%
3. Sub-fase apertura ×1.15 — +4%
4. FVG post-sweep como target Fase B — +3%

**Timeline estimado: 6-10 semanas**

---

### 5. FER — Funding Exhaustion Reversal

**Qué busca un profesional:**
Funding rate extremo (todos están en el mismo lado apalancados) + señales
de que las posiciones están empezando a cerrarse. El profesional entra
cuando la divergencia entre el precio y el flujo real confirma el agotamiento.

**Estado actual del sistema: 83%**

| Componente | Estado | Gap |
|---|---|---|
| Funding extreme detection | ✅ Implementado | — |
| OI weakening | ✅ Implementado | — |
| CVD divergence | ✅ Implementado | — |
| Retail/top long% divergence | ✅ Implementado | — |
| SmartMoneyScore | ✅ Implementado | — |
| Session gate (Asia/pre-London) | ✅ Implementado | — |
| **Order book thin side** | ❌ No implementado | En exhaustion, el book del lado contrario es delgado |
| **Funding rate velocity** | ❌ No implementado | No solo el nivel — la velocidad de cambio |
| HTF Bias align | ✅ Implementado | — |

**Gap principal — funding velocity:**
El nivel de funding es una foto. Lo que predice la reversión no es solo
"funding está en extremo" sino "funding llegó al extremo y está
empezando a bajar" — eso indica que las posiciones se están cerrando activamente.

```rust
pub struct FundingData {
    pub rate: f64,           // ya existe
    pub regime: FundingRegime, // ya existe
    pub velocity: f64,       // NUEVO — cambio del funding en las últimas 3 mediciones
    pub peak_confirmed: bool, // NUEVO — true si el funding tocó extremo y bajó >= 10%
}
// FER entra cuando peak_confirmed = true, no cuando rate está en extremo
```

**Gap secundario — book thin side:**
En un funding extremo long (todos comprados), el lado ask del libro
debería estar delgado — no hay vendedores porque todos están long.
Esa delgadez en el libro confirma el desequilibrio.

```rust
// En FER: verificar que el lado del libro en dirección de la reversión es delgado
let ask_thin = ctx.ob.ask_depth_l10 < ctx.ob.bid_depth_l10 * 0.60;
// Si todos están long y el ask está thin → confirma exhaustion
```

**Plan al 100%:**
1. Fix 01 (cooldown) — +3%
2. Funding velocity + peak_confirmed — +8%
3. Book thin side detection — +4%
4. SmartMoneyScore refinement — +2%

**Timeline estimado: 4-6 semanas**

---

### 6. SMD — Smart Money Divergence

**Qué busca un profesional:**
Los institucionales se posicionan en dirección contraria al retail + el
precio está en una zona clave (resistencia/soporte estructural) + el CVD
muestra debilidad. El profesional lo usa para anticipar moves de mediano
plazo donde los institucionales lideran y el retail queda atrapado.

**Estado actual del sistema: 80%**

| Componente | Estado | Gap |
|---|---|---|
| CVD divergence vs precio | ✅ Implementado | — |
| OI maturity | ✅ Implementado | — |
| LS ratio top vs retail | ✅ Implementado | — |
| Funding confirm | ✅ Implementado | — |
| SmartMoneyScore unificado | ✅ Implementado | — |
| @resistance/support confirm | ✅ Implementado | — |
| OB en zona de divergencia | ⚠️ Fase A | Máxima confluencia cuando divergencia ocurre en OB |
| **Divergencia multi-TF** | ❌ No implementado | CVD del TF de ejecución vs CVD del HTF |
| **OI by delta** | ❌ No implementado | OI subiendo con delta negativo = institucionales comprando en caídas |
| LiqMap confirm | ⚠️ Fix 03 — Fase A | La divergencia apuntando a zona de liq = mayor convicción |

**Gap principal — divergencia multi-TF:**
Una divergencia SMD en 5m que va en contra del CVD de 1h es una señal
débil. Una divergencia en 5m que está alineada con divergencia en 1h
(institucionales acumulando en HTF también) = señal de máxima calidad.

```rust
pub struct CvdContext {
    pub tf_execution: CvdData,   // ya existe — TF de ejecución
    pub tf_1h: Option<CvdData>,  // NUEVO — CVD en 1h
}
// SMD solo emite en máxima convicción cuando:
// cvd_execution diverge Y cvd_1h también diverge en la misma dirección
```

**Gap secundario — OI by delta:**
```rust
// OI subiendo + delta negativo (precio baja pero OI sube)
// = alguien está abriendo posiciones largas en la caída
// = acumulación institucional discreta
let oi_accumulation =
    ctx.oi.trend == OiTrend::Rising &&
    ctx.cvd.delta < -0.20 &&
    ctx.price_action == PriceAction::Declining;
```

**Plan al 100%:**
1. Fix 01 (cooldown) — +3%
2. CVD multi-TF (execution + 1h) — +9%
3. OI by delta detection — +5%
4. OB Fase B en zona de divergencia — +3%

**Timeline estimado: 6-10 semanas**

---

## Resumen del roadmap hacia 100%

### Fixes inmediatos (este sprint)
| Fix | Impacto | Estrategias |
|---|---|---|
| Fix 01 — Router cooldown | +3-5% cada una | Todas (×6) |
| Fix 02 — OB Fase A (solo logging) | +0% ahora, base para futuro | VAFA, VWAP, LVN, SMD |
| Fix 03 — LiqMap validación pasiva | +0% ahora, base para futuro | LiqHunt, SMD |

### Sprint 2 (semanas 2-4)
| Mejora | Estrategia | Impacto estimado |
|---|---|---|
| Chop regime gates más estrictos | VAFA | +10% |
| Funding velocity + peak_confirmed | FER | +8% |
| AVWAP session + last_swing | VWAP | +8% |
| CVD multi-TF (1h) | SMD | +9% |
| SessionPhase (OpeningRush) | LiqHunt | +4% |

### Sprint 3 (semanas 4-8)
| Mejora | Estrategia | Impacto estimado |
|---|---|---|
| OB Fase B (peso 0.08) si validación OK | VAFA, VWAP, LVN, SMD | +5-7% cada una |
| FVG Fase B | VAFA, VWAP, LVN | +2-5% |
| LiqMap Fase B (target alternativo) | LiqHunt, SMD | +5% |
| OI by delta | SMD | +5% |
| Book thin side | FER | +4% |

### Sprint 4 (semanas 8-12)
| Mejora | Estrategia | Impacto estimado |
|---|---|---|
| LOBRegenerationTracker | LVN | +5% |
| LiqMap Fase C — target principal LiqHunt | LiqHunt | +5% |
| OB Fase C (peso 0.12) si métricas lo avalan | Todas | +3% |
| Regime confidence score | Todas | +2% |

---

## Proyección de cobertura por estrategia

| Estrategia | Hoy | Sprint 2 | Sprint 3 | Sprint 4 |
|---|---|---|---|---|
| VAFA | 78% | 88% | 93% | 97% |
| VWAP Pullback | 82% | 90% | 95% | 97% |
| LVN | 80% | 85% | 92% | 97% |
| LiqHunt | 85% | 89% | 94% | 99% |
| FER | 83% | 91% | 94% | 96% |
| SMD | 80% | 89% | 94% | 97% |
| **Sistema** | **81%** | **89%** | **94%** | **97%** |

> El 3% restante hacia 100% es calibración empírica continua —
> ajuste de pesos según los datos reales del OutcomeTracker.
> El 100% absoluto no existe: el mercado cambia, los pesos deben adaptarse.

---

## Principio rector

> El sistema es el trader profesional que nunca se cansa, nunca tiene miedo,
> y nunca rompe las reglas. El trader humano aporta el juicio contextual
> que el sistema no puede computar: noticias no anticipadas, cambios de régimen
> macroeconómico, intuición de muchos años. Juntos son mejores que separados.
