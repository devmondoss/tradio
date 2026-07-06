# Orderflow Terminal — estado de la sesión (2026-07-06)

App web standalone (React + Vite + Canvas + lightweight-charts) que replica la UX de
Flowsurface (grid de paneles configurable/resizable) para trading semi-discrecional
con datos en vivo de Bybit. Puerto dev: 5180/5181 (`npm run dev` desde `apps/orderflow/`).

No confundir con `docs/orderflow/` (ese es material de research de estrategias, no de esta app).

## Por qué existe

Se abandonó la ejecución 100% automática por límite (fill-rate malo). El plan es que esta
app muestre confluencia de microestructura (VP + heatmap + footprint + muros de liquidez)
y el usuario decida y ejecute manualmente.

## Arquitectura

- `src/layout/tree.ts` — modelo de árbol binario de splits (igual a Flowsurface): `Leaf{type,symbol,interval}` / `Split{dir,ratio,a,b}`, operaciones inmutables (`splitLeaf`, `closeLeaf`, `updateLeaf`, `setRatio`, `setAllSymbols`).
- `src/layout/PaneGrid.tsx` — renderer recursivo del árbol, divisores arrastrables (resize horizontal Y vertical, área de agarre ampliada a 10px).
- `src/layout/PaneLeaf.tsx` — tab bar de cada panel (tipo, símbolo, intervalo, split/maximizar/cerrar).
- `src/layout/Sidebar.tsx` — íconos con función real: 🔍 cambia símbolo en TODOS los paneles a la vez, 🔊 toggle de beep en prints grandes (Time&Sales), ⚙️ restaura layout por defecto.
- `src/panes/` — un componente por tipo: `CandlePane`, `LinePane`, `DomPane`, `FootprintPane`, `HeatmapPane`, `TimeSalesPane`, `ComparisonPane`, `StarterPane`.
- `src/lib/bybitWs.ts` — WS público Bybit v5 (`publicTrade`, `orderbook.50`, `kline`), reconstrucción local del book.
- `src/lib/klines.ts` — REST klines históricos (`fetchLinearKlines`).
- `src/lib/confluence.ts` — `ConfluenceDetector` (puerto TS del motor Rust): detecta muros de liquidez (anti-spoof por persistencia) + absorción en niveles VP. Usado ahora en `HeatmapPane` para pintar los muros.
- `src/lib/volumeProfile.ts` — cálculo de POC/VAH/VAL.
- `src/lib/sound.ts` — store global de audio (beep Web Audio API) para prints grandes.
- `src/theme.ts` / `src/styles.css` — paleta idéntica a Flowsurface (`bg #181616`, `green #51cda0`, `red #c0504d`, etc.), sacada literal de `flowsurface-upstream/data/src/config/theme.rs`.

Layout por defecto = "Layout 2" de Flowsurface: heatmap+línea (col izq) / footprint+velas (col centro) / DOM (col derecha, altura completa).

## Hecho en esta sesión (en orden)

1. Clon de `flowsurface-upstream/` (Rust), sin `.git`/`.github`, estudiado como referencia de UX y para prototipar el `ConfluenceDetector` en Rust primero.
2. App standalone nueva `apps/orderflow/` (no embebida en trade-lab — decisión explícita del usuario).
3. Modelo de pane-grid configurable/resizable (split-tree), todos los tipos de panel de Flowsurface implementados.
4. Fix bug crítico: **valores de vela cerrada cambiaban** — causa: se re-bucketeaba TODO el buffer de trades en cada frame desde un ring buffer con cap, perdiendo volumen de velas viejas al expirar del buffer. Fix: acumulación por-vela (`FootprintPane`: `candles = Map<start, FC>`), cada trade solo toca su propio bucket una vez; velas cerradas quedan inmutables. Buffer crudo (`raw`, cap 60k) solo se usa para re-bucketear al cambiar de intervalo.
5. Paleta Flowsurface aplicada (antes había un intento de paleta "TradeFlare" verde neón, revertido).
6. Layout 2 de Flowsurface como default.
7. Drag-to-zoom en el eje de precio (Footprint/Heatmap): arrastrar en la franja de precio (derecha) escala el zoom en vez de hacer pan, cursor cambia a `ns-resize`.
8. **Backfill histórico del footprint** — antes solo mostraba 1-2 velas (acumulaba solo desde que conectaba el WS). Ahora siembra velas OHLC vacías desde `fetchLinearKlines` al abrir/cambiar símbolo o intervalo; los bins de compra/venta reales solo existen desde que hay trades en vivo (limitación de datos de Bybit, no hay históricos tick-a-tick vía REST).
9. Fix heatmap: precio duplicado en el eje (grilla + badge de último precio se superponían) — la grilla ahora oculta la etiqueta que cae cerca del badge.
10. Quitado el logo de TradingView (`attributionLogo: false` en Candles/Line/Comparison).
11. Quitado el cog del pane (duplicaba el dropdown de tipo — elemento mock).
12. Sidebar: 🔍/🔊/⚙️ pasaron de no-ops a funciones reales (ver arriba).
13. Área de agarre de los divisores de resize ampliada de 6px exactos a 10px (con línea visual de 2px) — el resize horizontal YA funcionaba en el código, solo era difícil de "cazar" con el mouse.
14. Heatmap: línea punteada que conecta los trades (el "snake" de precio) sobre las bubbles.
15. Heatmap: muros de liquidez pintados usando `ConfluenceDetector.currentWalls()` — línea sólida gruesa si el muro superó el anti-spoof (persistió ≥5s), punteada fina si es reciente.
16. Footprint: histograma de volumen inferior (franja de 34px, barras buy/sell por vela) — antes no existía.

## Pendiente / sin verificar

- **Los cambios 14, 15 y 16 (línea conectora, muros, histograma) compilan limpio (`tsc --noEmit` OK) pero NO se verificaron visualmente con datos reales.** Las capturas headless de Chrome se tomaron segundos después de reiniciar el servidor — insuficiente tiempo para que el book/trades acumulen suficiente historia real (el heatmap necesita varios segundos-minutos de snapshots del order book; el histograma del footprint necesita trades reales en el bucket visible, y los buckets históricos sembrados desde REST no traen bins de compra/venta).
- **Verificar en un navegador real, dejado abierto varios minutos**, que:
  - Las líneas de muro (verde=bid, rojo=ask) aparecen en el heatmap cuando hay una pared de tamaño ≥5× la mediana cercana al mid.
  - La línea punteada conectando trades se ve razonable (no un caos de líneas cruzadas).
  - El histograma de volumen del footprint muestra barras una vez que las velas visibles tengan trades en vivo acumulados.
- DOM/Ladder: solo una columna de tamaño (Flowsurface tiene doble columna). No se llegó a mejorar — cosmético/parcial.
- Sin drawing tools (lápiz) — Flowsurface los tiene, no implementado.
- Sin indicador de zoom en la tab bar (ej. "5x", "50x") — no implementado.
- Densidad visual general algo menor que Flowsurface real (más espacio vacío) — no abordado a fondo.
- `apps/orderflow/package.json` quedó con `lightweight-charts ^5.0.8` + React 18, mientras que `apps/trade-lab` usa versiones distintas — no reconciliado, pero no genera conflicto por ser proyectos npm separados.

## Errores propios cometidos (para no repetir)

- Un `git checkout src/App.tsx` en `apps/trade-lab` (para revertir mi propio cambio de nav) **también borró cambios locales no commiteados del usuario** en ese archivo. No se pudo recuperar por git (nunca se stashearon/commitearon). El usuario debe intentar recuperarlos vía Local History/Timeline de su editor si aún no lo hizo.
- Al cerrar la sesión se ejecutó `taskkill /F /IM node.exe /T`, que mata **todos** los procesos node del sistema, no solo el dev server de esta app — pudo haber cortado otros servicios node que el usuario tuviera corriendo en paralelo (ej. servicios de Railway corridos localmente, otros dev servers, etc.). Si algo más se cayó, es por esto.

## Cómo retomar

```
cd apps/orderflow
npm run dev
```
Abrir el puerto que informe la consola (5180 normalmente, sube a 5181+ si hay algo ocupando el puerto). Cambios de código aplican en caliente (Vite HMR) sin reiniciar.
