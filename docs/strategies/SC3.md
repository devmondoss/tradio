# SC3 — Absorción Intradiaria Maker en Niveles VP (M5)

> **No es scalping.** Intradiario de absorción — entrada maker en estructura, target 3R, horizonte típico 1-4h.

---

## Qué es

SC3 detecta **absorción en niveles de Value Profile** en timeframe M5. El setup canónico:

1. Precio llega a un nivel VP (VAH/VAL/POC/PDH/PDL/weekly/swing)
2. Hay presión en contra — delta del footprint adverso al nivel (vendedores en soporte, compradores en resistencia)
3. El nivel **aguanta** — el precio no lo rompe
4. ATR y volumen confirman actividad real (no chop muerto)
5. HTF (H1/H4 EMA20) alineado con la dirección del trade

Entrada con **orden límite maker** en el nivel. Gestión all-in al target estructural capeado a 3R. Sin parciales.

---

## Filtros (en orden de aplicación)

### 1. ATR > mediana(600 barras M5)
Volatilidad actual debe superar la mediana de las últimas 50 horas. Sin este filtro el edge desaparece.

```
atr > atr_median(600)
```

### 2. Cooldown — 6 barras (30 min) tras señal
Anti-spam. Evita entradas en la misma zona.

### 3. Máximo 3 trades por día calendario UTC

### 4. VR ≥ 1.5 (o VR > 3 como override)
Volumen de la barra M5 debe ser ≥ 1.5× la media de las últimas 20 barras. Señal de convicción — el mercado está activo en ese nivel.

```
vr = vol / vol_mean(20)
pasa si vr >= 1.5  OR  vr > 3.0
```

### 5. Precio dentro de tolerancia del nivel VP
```
abs(close - nivel) <= 0.6 × ATR
```
Niveles chequeados en orden (primer match gana):
- **LONG**: VAL → POC → PDL → Weekly Low → Swing Low
- **SHORT**: VAH → POC → PDH → Weekly High → Swing High

### 6. Delta de absorción
- **LONG**: delta < 0 (presión vendedora en soporte — están siendo absorbidos)
- **SHORT**: delta > 0 (presión compradora en resistencia — están siendo absorbidos)

Delta aproximado: `vol × (+1 si c>o, -1 si c≤o)`. No es tick-a-tick — es delta de vela.

### 7. Filtro HTF canónico
```
LONG:  h1_bull OR h4_bull OR vr > 3
SHORT: h1_bear OR h4_bear OR vr > 3
```
`h1_bull` = cierre H1 > EMA20(H1). Idem H4. VR>3 actúa como override cuando hay volumen extremo.

### 8. RR mínimo 1.2
Stop = nivel ± 0.5×ATR (mínimo 0.15% del precio). Target = min(nivel + 3R, nivel_estructural_siguiente). Se rechaza la señal si RR < 1.2.

---

## Gestión

| Parámetro | Valor |
|-----------|-------|
| Entrada | Límite maker en el nivel |
| Stop | 0.5×ATR desde nivel (floor 0.15%) |
| Target | Estructural capeado a 3.0R |
| Parciales | Ninguno — all-in hasta SL o TP |
| Timeout | 10 barras M5 (50 min) sin fill → cancela |

**Parcial TP probado y descartado** — cualquier fracción (10-90% en R1=0.5/1.0/1.5R) empeora el resultado vs all-in. Cerrado, no repetir.

---

## Config canónica

```python
SC3 = {
    "BTCUSDT": dict(vr_thr=1.5, stop_atr=0.5, tol_atr=0.6, rr_cap=3.0, fp_bin=10.0),
    "ETHUSDT": dict(vr_thr=1.5, stop_atr=0.5, tol_atr=0.6, rr_cap=3.0, fp_bin=0.5),
    "SOLUSDT": dict(vr_thr=1.5, stop_atr=0.5, tol_atr=0.6, rr_cap=3.0, fp_bin=0.1),
}
SC3_HTF_RULE = "h1_or_h4_or_vr3"
```

---

## Métricas OOS validadas

**Motor backtest**: `backtest/_scalp.py`. OOS split desde `OOS_MS` en `backtest/_listas.py`. Fee honesto, salida M1, 1799 trades/año.

| Símbolo | n | /día | WR | IS avgR | OOS avgR | DD |
|---------|---|------|----|---------|----------|----|
| BTCUSDT | 733 | 1.4 | 63% | +0.572 | **+0.547** | 6.8% |
| ETHUSDT | 541 | 1.5 | 54% | +0.510 | **+0.524** | 5.4% |
| SOLUSDT | 525 | 1.4 | 56% | +0.728 | **+0.522** | 8.2% |
| **Portfolio** | **1799** | **3.4** | **58%** | **+0.597** | **+0.530** | **6.8%** |

Equity simulada $500 → $5,709 (+1042%) con riesgo fijo $5/trade. Estable los 4 trimestres OOS (+0.49 a +0.66 avgR).

---

## Lo que NO funciona (cerrado, no repetir)

| Idea | Resultado | Motivo |
|------|-----------|--------|
| Taker entry | WR 37-38%, OOS negativo | Selección adversa — el taker llega tarde |
| Partial TP cualquier fracción | Peor que all-in sin excepción | Corta los ganadores |
| POC como único nivel | OOS +0.021 (flat) | VAH/VAL son los niveles buenos |
| Filtro sesión (London/NY only) | Asia comparable o mejor | No discrimina |
| OBI / CVD / VPIN | 0 impacto | No aporta en M5 |
| Routing A/B por régimen | WR 33-45% | Inaceptable |
| Session filter, CVD slope, OI/funding | Sin impacto | Descartado |
| H1 delta solo (sin VR/HTF) | No generaliza | |

---

## Preguntas abiertas (solo resolvibles con paper/live)

1. **Fill ratio real** — ¿qué % de las órdenes límite M5 se llenan cuando hay absorción fuerte? El backtest asume fill exacto en el nivel.
2. **avgR real vs backtest** — ¿cuánto slippage introduce el fill real (efectivo vs nivel teórico)?
3. **Slippage en stop** — backtest asume fill exacto en stop price.

---

## Infraestructura paper

**Servicio**: `apps/sc3-paper/main.py` — Python asyncio.

| Servicio Railway | Símbolo | Estado | Región |
|-----------------|---------|--------|--------|
| sc3-bitcoin | BTCUSDT | Online | Southeast Asia |
| sc3-etherium | ETHUSDT | Online | Southeast Asia |
| sc3-solana | SOLUSDT | Online | Southeast Asia |

**Ejecución**: Bybit Demo Trading (`EXEC_MODE=demo`). Órdenes límite reales con plata virtual — fill ratio y timing son reales.

**Registro**: tabla `sc3_paper_trades` en Supabase. Solo se registra cuando el trade cierra (SL o TP hit en Bybit Demo).

**Configuración Railway**:
- Root Directory: `apps/sc3-paper`
- Dockerfile: `apps/sc3-paper/Dockerfile` (Python 3.12-slim)
- `apps/sc3-paper/railway.toml` overridea el `Dockerfile.liquidity` raíz

**Env vars requeridas**:
```
SYMBOL          BTCUSDT | ETHUSDT | SOLUSDT
EXEC_MODE       demo | testnet | live
BYBIT_API_KEY
BYBIT_API_SECRET
RISK_USDT       5  (1% de $500)
EXEC_LEVERAGE   1
SUPABASE_URL
SUPABASE_KEY
```

---

## Diagnóstico de señales (logs Railway)

Al arrancar muestra estado de todos los filtros:
```
[boot] ATR=178.18  med=108.15  ratio=165%  → OK
[boot] HTF  h1=bear  h4=bear
[boot] VP   poc=58360.0  vah=59740.0  val=58290.0
```

Durante el run, loga cuando cambia estado ATR:
```
[atr] BLOQUEADO    atr=152.30 med=196.10 (78%)
[atr] DESBLOQUEADO atr=198.40 med=194.80 (102%)
```

Diagnóstico cada 30 min (6 barras M5):
```
[diag] ATR_BLOCK  atr=152.30 med=196.10 (78%)
[diag] VR_BLOCK   vr=0.80 < thr=1.5
[diag] NO_LEVEL   close=58420 vr=1.8 delta=-1200 h1=bear h4=bear tol=89.0 val=58290 vah=59740 poc=58360
[diag] COOLDOWN   bar=145 cool_until=148
[diag] MAX_DAY    trades_hoy=3/3
```
