# Fix — Leverage y Position Sizing
## FlowSurface Paper Trading

**Fecha:** Mayo 2026  
**Archivo:** `data/src/strategy/paper.rs`  
**Problema:** El paper trader simula con leverage=1 cuando Binance Futures opera con leverage real

---

## Contexto — qué encontramos hoy

### El sistema generó 6 señales reales en producción

Todas LONG en VwapPullbackContinuation entre las 08:00 y 09:50 UTC.
Los datos en Supabase confirmaron el problema de sizing:

```
Señal real (08:00 UTC):
  entry:  $78,137
  stop:   $77,984   ← bug ya corregido hoy (era VAL fijo, ahora es 1×ATR)
  target: $78,168   ← bug ya corregido hoy (ahora es estructural)
  
  Con el fix de stop/target de hoy:
  stop:   $78,093   (entry - 1×ATR = $78,137 - $43.8)
  target: $78,268   (fallback 3×ATR)
  risk:   $44 por BTC
  reward: $131 por BTC
  R:R:    2.98:1  ✓
```

El R:R ya está corregido. Pero hay un segundo problema: **cuántos BTC se abren**.

---

## El problema de leverage=1

### Cómo funciona Binance USDM Futures

En Binance Perpetual Futures, tu cuenta tiene USDT como margen.
Con ese margen podés controlar una posición más grande usando apalancamiento.

```
Cuenta:          $3,000 USDT (margen)
Leverage 10×:    $3,000 × 10 = $30,000 de poder de compra
```

No necesitás tener los $12,480 en la cuenta — solo necesitás
el margen ($1,248 con leverage 10×) para controlar esa posición.

### El cálculo correcto del 1% de riesgo

El objetivo es arriesgar exactamente el 1% del balance en cada trade,
sin importar dónde esté el stop:

```
Balance:      $3,000
1% riesgo:    $30   ← esto es fijo, nunca cambia

Stop a $44 del entry (1×ATR con ATR=$44):
  ¿Cuántos BTC para perder exactamente $30 si toca el stop?
  size = $30 / $44 = 0.682 BTC
  notional = 0.682 × $78,137 = $53,290
  margen necesario con 10×: $53,290 / 10 = $5,329 → excede $3,000
  → cap activa: size = $3,000 × 10 / $78,137 = 0.384 BTC
  → riesgo real: 0.384 × $44 = $16.90 = 0.56% del balance

Stop a $250 del entry (ATR=$250, BTC a $104,000):
  size = $30 / $250 = 0.12 BTC
  notional = 0.12 × $104,000 = $12,480
  margen necesario con 10×: $12,480 / 10 = $1,248 → cabe en $3,000 ✓
  → riesgo real: 0.12 × $250 = $30 = 1% del balance ✓
```

El 1% se logra cuando el margen necesario no excede el balance.
Cuando excede, el cap activa y el riesgo real es menor al 1%.
Eso es correcto y esperado — el sistema nunca arriesga MÁS del 1%.

### Qué pasa con leverage=1 (configuración actual)

```
Balance:      $3,000
Leverage:     1×
Poder de compra: $3,000 × 1 = $3,000

Stop a $44 del entry:
  size ideal = $30 / $44 = 0.682 BTC → notional $53,290
  cap con leverage=1: $3,000 / $78,137 = 0.0384 BTC
  riesgo real: 0.0384 × $44 = $1.69 = 0.056% del balance

Stop a $250 del entry:
  size ideal = $30 / $250 = 0.12 BTC → notional $12,480
  cap con leverage=1: $3,000 / $104,000 = 0.02885 BTC
  riesgo real: 0.02885 × $250 = $7.21 = 0.24% del balance
```

Con leverage=1, el sistema nunca llega al 1% de riesgo real porque
$3,000 de capital no alcanza para abrir el tamaño necesario.
El 1% configurado es una ilusión — el riesgo real es 0.05% a 0.25%.

---

## Por qué esto importa para el shadow trading

El paper trading existe para simular lo que pasaría con capital real.
Si en producción real vas a usar Binance Futures con leverage,
el paper trader debe usar el mismo leverage para que los resultados
sean comparables.

Con leverage=1 en paper y leverage=10 en real:
```
Paper:   gana $7 en un trade ganador (0.24% del balance)
Real:    gana $90 en el mismo trade (3% del balance)

El sistema parece que funciona "bien" en paper porque las pérdidas
son pequeñas, pero en real las magnitudes son completamente distintas.
```

El objetivo del shadow trading es validar que la lógica funciona
antes de arriesgar capital real. Para eso los números tienen que
reflejar las condiciones reales.

---

## Límites reales de Binance (verificado en docs oficiales)

Para BTCUSDT Perpetual:
```
Mínimo de orden:     0.001 BTC
Mínimo notional:     50 USDT
Tick size:           $0.10
Max leverage:        125× (para posiciones pequeñas)
Leverage típico:     10× a 20× para traders conservadores
```

Con leverage=10 y balance=$3,000:
```
Poder de compra:     $30,000
Mínimo de orden:     0.001 BTC → $104 notional → cumple los $50 mínimos ✓
Máximo posición:     $30,000 / $104,000 = 0.288 BTC
```

---

## El fix

### Cambio único en `paper.rs`

```rust
// Línea actual:
const DEFAULT_LEVERAGE: f64 = 1.0;

// Cambiar a:
const DEFAULT_LEVERAGE: f64 = 10.0;
```

### Cómo queda el sizing con leverage=10

Con los datos reales de hoy (ATR=$44, precio=$78,137):

```
risk_amount    = $3,000 × 1%         = $30.00
risk_per_unit  = $44.00              (1×ATR, stop correcto del fix anterior)
size_ideal     = $30 / $44           = 0.682 BTC
notional_ideal = 0.682 × $78,137     = $53,290

max_notional   = $3,000 × 10         = $30,000  ← cap de leverage
size_real      = $30,000 / $78,137   = 0.384 BTC  ← cap activa
notional_real  = $30,000

riesgo real    = 0.384 × $44         = $16.90 = 0.56% del balance
```

El cap activa porque ATR=$44 es pequeño (mercado en chop).
Cuando ATR sea mayor ($250+ en movimientos fuertes), el 1% real se alcanza:

```
ATR = $250, precio = $104,000:
  size_ideal   = $30 / $250          = 0.12 BTC
  notional     = 0.12 × $104,000     = $12,480
  max_notional = $3,000 × 10         = $30,000
  → no activa cap ✓
  riesgo real  = 0.12 × $250         = $30 = 1% exacto ✓
```

### Escenarios WIN/LOSS con leverage=10 y ATR=$250

```
Setup del trade:
  Balance:       $3,000
  Leverage:      10×
  Risk:          1% = $30
  Entry:         $104,000
  Stop:          $103,750  (1×ATR = $250)
  Target:        $104,800  (nivel estructural, R:R 3.2:1)
  Size:          0.12 BTC
  Notional:      $12,480
  Margen usado:  $1,248  (41% del balance, 59% libre)

WIN — target hit a $104,800:
  gross PnL  = 0.12 × ($104,800 - $104,000) = +$96.00
  fees       = $12,480 × 0.04% × 2          = +$9.98  (entry + exit)
  net PnL    = $96.00 - $9.98               = +$86.02
  net %      = $86.02 / $3,000              = +2.87%
  balance    = $3,086.02

LOSS — stop hit a $103,750:
  gross PnL  = 0.12 × ($103,750 - $104,000) = -$30.00
  fees       = $9.98
  net PnL    = -$30.00 - $9.98              = -$39.98
  net %      = -$39.98 / $3,000             = -1.33%
  balance    = $2,960.02
```

Nota: el riesgo real es -1.33% en vez de -1% porque las fees se suman
a la pérdida. Con R:R de 3.2:1, el net R:R real post-fees es:

```
Net R:R = $86.02 / $39.98 = 2.15:1
Break-even win rate = $39.98 / ($86.02 + $39.98) = 31.7%
```

El sistema es rentable si gana más de 1 de cada 3 trades.

### Simulación 10 trades con distintos win rates

```
Win rate   Wins  Losses   PnL neto            Balance final
  25%        2.5    7.5   2.5×$86 - 7.5×$40  = -$85.00  → $2,915
  32%        3.2    6.8   break-even          → $3,000
  40%        4      6     4×$86 - 6×$40      = +$104.00 → $3,104
  50%        5      5     5×$86 - 5×$40      = +$230.00 → $3,230
  60%        6      4     6×$86 - 4×$40      = +$356.00 → $3,356
```

---

## Tests a verificar después del fix

```bash
cargo test -p flowsurface-data --lib 2>&1 | grep "paper"
```

Los tests existentes de paper trading deben seguir pasando porque
solo cambia la constante de leverage — la lógica de sizing es la misma.

Verificar específicamente:
```
test strategy::paper::tests::full_accounting_long_target_hit     → ok
test strategy::paper::tests::stop_hit_before_target_same_bar     → ok
test strategy::paper::tests::short_position_pnl_correct          → ok
test strategy::paper::tests::funding_accumulated_and_reflected... → ok
```

Si alguno falla, es porque el test tiene hardcodeado el notional
calculado con leverage=1. Actualizar esos valores al nuevo sizing.

---

## Resumen de todos los fixes aplicados hoy

| Fix | Qué estaba mal | Qué se corrigió | Impacto |
|-----|---------------|-----------------|---------|
| Stop con `max()` | `min()` elegía el stop más lejano | `max()` elige el más cercano | Riesgo de $153 → $44 |
| Target estructural | VAH fijo sin verificar R:R | HVN/VAH/walls/fallback con R:R mínimo | R:R de 0.20 → 2.98 |
| TTL | 5 minutos (1 barra) | 250 minutos (50 barras) | Trade tiene tiempo de resolver |
| Leverage | 1× (shadow ≠ real) | 10× (simula Binance Futures real) | Sizing refleja operación real |

Con todos estos fixes, el sistema está listo para recolectar señales
que representen condiciones reales de trading en Binance Futures.
