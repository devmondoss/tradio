# sc3 "Absorción VP" — Cómo funciona, capa por capa

> Disección completa de la estrategia de scalping, paso por paso. Anclado en un trade
> REAL de SOL (2025-06-21 21:30 UTC). Gráfico anotado: `charts/sol_anatomia_trade.png`.

## La idea en una frase
**No predecimos dirección.** Detectamos dónde la liquidez grande está *defendiendo* un nivel
de valor (absorción del flujo) y nos sumamos como **liquidez pasiva maker**. Es provisión de
liquidez con confirmación de orderflow, no adivinanza.

---

## Las 8 capas

### CAPA 0 · Datos crudos
Tick-a-tick (precio, tamaño, lado agresor) + orderbook → velas M5 + **footprint** (volumen
comprador/vendedor por nivel de precio dentro de cada vela).

### CAPA 1 · El MAPA — Volume Profile (DÓNDE)
Solo operamos en **niveles de valor**: POC, VAH, **VAL**, PDH/PDL, weekly, swings — donde se
negoció mucho volumen = donde hay players grandes. Filtro espacial: el 99% del tiempo no hacemos
nada porque el precio no está en un nivel.
> *Ejemplo:* precio cae al **VAL = 130.92**, con **4 niveles en confluencia** (zona muy defendida).

### CAPA 2 · El FOOTPRINT — el flujo (QUÉ)
En la vela que toca el nivel: volumen agresor (`vr`) y **delta** (compra − venta).
> *Ejemplo:* `vr` **11.2×** la media · **delta −108.000** (−15% del volumen) = avalancha de venta.

### CAPA 3 · El DISPARADOR — absorción (CUÁNDO)
Venta agresiva brutal **pero el precio NO rompe**: la vela toca abajo y **cierra arriba**.
Alguien grande absorbe la venta con límites de compra → vendedores agotados → **fade long**.
> *Ejemplo:* low 130.71 tocó, cerró en 132.77 → absorción confirmada.

### CAPA 4 · FILTROS / BLOQUEADORES
- **ATR > mediana(500)** — solo con volatilidad (sin esto el edge desaparece, regla dura).
- **≤3 trades/día** por activo · cooldown entre trades.
- **Piso de stop 0.15%** + RR mínimo (no migajas).

### CAPA 5 · La ENTRADA — límite MAKER
Orden **límite post-only EN el nivel**. Somos liquidez pasiva: el precio viene a nosotros.
Pagamos maker (2bps), no taker (5.5bps). **Esto hace viable al scalp.**
> *Ejemplo:* límite en 130.92.

### CAPA 6 · La GESTIÓN — fade
- **Stop** justo abajo del nivel (−0.40%): si el nivel cede, salís rápido.
- **Parcial 50% en TP1** (medio camino) → stop a **breakeven** (trade "gratis").
- **El resto corre al target** estructural, capeado a **2.5R**.

### CAPA 7 · La SALIDA
- **Target** → límite maker. ✅ *(lo que pasó: +2.40R = $12 con $5 riesgo)*
- **Stop / timeout** → market taker.
- **Breakeven** → tocó TP1 y volvió.

### CAPA 8 · ECONOMÍA
- Riesgo **fijo 1%** ($5/$500) · fees maker 2bps / taker 5.5bps (descontados).
- **Sizing 2× cuando confluencia≥4** (los premium se sizean más).
- Fee se come ~40% del edge bruto → **maker obligatorio**; con rebate (volumen) el edge crece.

---

## El ciclo completo
> Precio cae a un nivel de valor → ola de venta lo golpea pero alguien la absorbe y el precio
> aguanta → límite maker en el nivel → parcial + breakeven → el resto corre al target.
> ~1-2 veces/día por activo, en los 3.

## Config canónica (`_scalp.SC3` / `run_sc3()`)
```
TF=M5 · maker fade · niveles ampliados · ATR>mediana(500) · stop floor 0.15% · ≤3/día
BTC: vr2.5 stop0.5×ATR tol0.6 → OOS +0.46 WR65% DD4%
ETH: vr1.5 stop0.5×ATR tol0.6 → OOS +0.52 WR58% DD9%
SOL: vr2.5 stop0.5×ATR tol0.6 → OOS +0.55 WR58% DD6%
Portfolio OOS +0.51 · maxDD 5% · $500→$4.724 (+845%) · 4.5 trades/día
```
