# Sesión 2026-06-29 — Ejecutor real (Bybit Demo Trading) de la estrategia de liquidez

Construcción y bring-up del **ejecutor de órdenes reales** sobre Bybit perps, corriendo en
**Demo Trading** (mercado REAL, plata virtual). Pasamos de paper (fills simulados) a ejecución
real con plata de mentira → mide fill ratio, timing, slippage y avgR **reales**.

**Conclusión de fondo: la plomería quedó validada de punta a punta (abre → gestiona → cierra →
registra), y el FILL RATIO real (~16% agregado) cae justo en el rango que el backtest/Nautilus
asumió (~14-27%). El P&L todavía no se puede juzgar (pocos trades). Próximo paso: acumular
~20-50 trades limpios.**

---

## 1. Arquitectura

- **Binario** `crates/liquidity_monitor` (Rust): en el MISMO proceso corre el **paper-book**
  (simulado, control) **+** el **ejecutor real** (`EXEC_MODE=demo`). Un símbolo por proceso.
- **Datos**: WS real de Bybit mainnet (`stream.bybit.com`) para señales/niveles/footprint.
- **Ejecución**: `api-demo.bybit.com` (Demo Trading) — mercado real, plata virtual.
- **Módulos nuevos**: `src/exec.rs` (cliente firmado HMAC, perps linear) + `src/executor.rs`
  (máquina de estados Idle→Resting→InPos→Idle).

### Por qué DEMO y no TESTNET (hallazgo clave)
Testnet tiene su **propio mercado falso/ilíquido** (BTC a $167k con saltos locos) → poner órdenes
a niveles del mercado real sobre ese libro NO valida nada. **Demo ejecuta contra el mercado REAL**
(precios/libro reales) con plata virtual → fills/timing realistas. `EXEC_MODE`: off|demo|testnet|live.

### Los tres tiempos (modelo mental)
| Pieza | Timeframe | Velocidad |
|---|---|---|
| Decisión (nivel + régimen) | **M15** (régimen = EMA corta `ema5`, no M1) | paciente — cierre de barra |
| Fill (precio toca el límite) | **tiempo real (tick)** | instantáneo |
| Salida (stop/tp/trail) | **tiempo real (tick)**, la maneja el exchange | instantáneo |

Lo único "lento" es DECIDIR dónde poner la orden (M15, por diseño). Fill y salida son tiempo real.

### Mapeo de gestión (el exchange gestiona la salida)
- **Fade** (chop): `set_trading_stop` con `stopLoss` + `takeProfit` (tp2 estructural).
- **Trail** (tendencia): `trailingStop` nativo = `TRAIL_ATR(6)·ATR`.
- **Entrada**: PostOnly limit (maker garantizado).

---

## 2. Bring-up — bugs hallados y arreglados (en orden)

1. **Error enmascarado** (`594fa29`): el parser tipado explotaba con el `result` vacío de los
   errores de Bybit → "error decoding body". Fix: parseo en 2 etapas que surfacea retCode/retMsg real.
2. **Rename** (`7a2fe10`): `liquidity_testnet_trades` → `liquidity_exec_trades` (confundía).
3. **`.env` autocargado** (dotenvy): el binario lee `.env` de la raíz; checker también. `.env` gitignored.
4. **Posición huérfana sin stop** tras redeploy (`e2e151f`): el ejecutor arrancaba sin estado y dejaba
   la posición SIN stop. Fix: flatten de huérfana al arrancar + self-heal del stop (reintenta arm).
5. **Órdenes pegadas en niveles viejos** (`fa98546`): no refrescaba. Fix: `on_bar` cancela las resting
   viejas y recoloca en los niveles actuales cada barra (paridad con el paper-book).
6. **Persist + restore de la posición** (`4725744`): tabla `liquidity_exec_open_pos`; el ejecutor
   guarda su contexto al abrir y lo restaura al arrancar → **la posición SOBREVIVE redeploys** (el
   stop ya vive en el exchange). Casos: posición+contexto→restaura; posición sin contexto→huérfana,
   flatten; contexto sin posición→cerró en downtime, registra; nada→idle.
7. **Registro RICO + fill ratio** (`263a047`): `liquidity_exec_trades` ahora guarda reason, level
   (slippage), tp1, qty, **mfe_r/mae_r** (excursión, trackeada por tick vía `on_tick`), **fee_r real**
   (bruto−neto del closed-pnl), bar_ts (link con el paper). Nueva `liquidity_exec_snapshots`:
   **fill ratio real** (placed vs filled). Migración `liquidity_exec_enrich.sql` (autocontenida).

---

## 3. Tablas Supabase
- `liquidity_exec_trades` — trades reales cerrados (registro rico). Migración: `liquidity_exec_enrich.sql`.
- `liquidity_exec_open_pos` — posición abierta persistida (sobrevive redeploys).
- `liquidity_exec_snapshots` — fill ratio real por barra (placed/filled acumulados).

---

## 4. Primeros resultados (2026-06-29, muestra mínima)

### ✅ Validado
- **Plomería**: abre, rutea por régimen, arma el stop/trailing correcto, persiste, trackea MFE/MAE,
  cierra y registra. Confirmado en vivo (posición BTC restaurada tras redeploy, etc.).
- **FILL RATIO REAL ≈ 16% agregado** (BTC 2/6=33%, SOL 1/4=25%, ETH 1/15=7%) → **cae en el rango
  validado ~14-27%.** La pregunta abierta #1 respondida con fills reales.
- **fee_r real** confirmado (~0.07-0.19R, más pesado en stops chicos) — consistente con [[paper_r_truth]].

### ⚠️ Pendiente de muestra
- P&L: 2 trades limpios, ambos al stop (-1R). **No se puede juzgar** — la estrategia es WR bajo /
  payoff alto; perder los primeros es normal, faltan los targets/timeouts que cargan el avgR.
- Necesario: ~20-50 trades con algunos targets.

---

## 5. Límites V1 (refinar en V2)
1. **Sin parcial 50%@tp1** (usa stop+tp2 directo). El parcial está validado que protege DD/SOL
   ([[recon_tick_findings]]); un fade SOL fue +0.80R y volvió al stop — el parcial lo habría salvado.
2. **One-way mode**: coloca long+short y cancela el resto al primer fill (ventana ~8s).
3. qty/tick por símbolo hardcodeados (override por env).

---

## 6. Lecciones operativas
- **NO operar a mano en la cuenta del ejecutor.** Cerrar una posición de USDT a mano ensucia el
  registro (quedó un trade BTC con R=0). El trading manual del usuario fue en ETHUSDC (otro
  instrumento) — separado, pero idealmente en otra cuenta demo.
- **Railway redeploya en cada push** → con el persist/restore ya no se pierden posiciones, pero
  conviene minimizar churn mientras se valida.
- **Demo keys** se crean DENTRO de Demo Trading en bybit.com (distintas de mainnet/testnet).

---

## 7. Setup / cómo correr
- Env: `EXEC_MODE=demo`, `BYBIT_API_KEY/SECRET` (demo), `SYMBOL`, `EXEC_QTY`, `SYSTEM=flow`,
  `HIGH_VOL_ONLY=true`, `SUPABASE_*`. Template: `railway.liquidity-demo.env.example`.
- Migraciones: `liquidity_exec_enrich.sql` (autocontenida, crea las 3 tablas/columnas).
- Doc operativo: `docs/liquidity/TESTNET_EXECUTOR.md`. Checker: `backtest/_testnet_check.py`.
- Los 3 servicios Railway (tradio-btc/eth/sol) convertidos a demo.

---

## 8. Próximos pasos
1. **Acumular ~20-50 trades limpios** (sin tocar manual) → reporte: avgR real, reparto por reason,
   fill ratio por símbolo, ¿el no-parcial cuesta?
2. **V2 del ejecutor**: parcial 50%@tp1; (si el fill ratio lo pide) límite "un poco adentro" con regla dura.
3. **Vista comparativa paper vs demo** en trade-lab (mismas señales, simulado vs real → costo de ejecución).
4. **Camino a live**: si demo confirma avgR ≈ backtest → `EXEC_MODE=live` + keys reales + size chico BTC.
