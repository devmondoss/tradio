---
name: project-mtf-strategy
description: "MTF trading strategy system — shorts v7 + longs v2. Shorts $119K, Longs $78K."
metadata: 
  node_type: memory
  type: project
  originSessionId: 813627aa-1549-49c8-a92f-5398b5f7e51a
---

# MTF System — Estado y Arquitectura

> **ACTUAL (2026-06-19 sesión 3): template MTF-en-VAH MUERTO causal + pivota a ticks/derivados.**
> Ver `docs/mtf/MTF_WORKLOG_2026-06-19_microstructure_pivot.md`.
> - **2º lookahead encontrado (en el STOP):** el motor anclaba el stop al high del bucket H1 EN-CURSO
>   (`mtf_system.py:179`) → conocía el máximo futuro de la hora (~+0.2R IS inflado). Con stop causal el
>   "+0.16R residual" de la sesión 2 desaparece (OOS negativo). En LIVE no existe → backtest sobrestimaba.
> - **24 setups ICT+orderflow** (fade/sweep/continuación/orderflow/exhaustion), M1/M5/M15 y swing →
>   TODOS negativos causal, peores que baseline nulo OOS. No es el disparador ni el TF: es la geometría/datos.
> - **Micro agregada a M1 NO predice:** retorno fwd 1/5/15m condicionado a señal (15k+ instancias) =
>   ±1-2 bps vs 11 bps fee → ~0. Causa raíz: granularidad (el sweep dura ~15s, M1 lo licúa).
> - **PIVOTA:** los ticks crudos estaban borrados (`trades_raw/` vacío); se re-bajan. Pipeline nuevo:
>   `download_raw.py` (ticks full-res + `ob_1s`), `download_derivatives.py` (OI 5min + funding 8h, la
>   capa ortogonal que faltaba), `build_events.py` (eventos sub-minuto + outcome), `_event_predict.py`.
> - **Datos PORTÁTILES en `E:\Tonnio`** (`TRADIO_PERP=E:/tradio-data/bybit-perp`). ⚠️ `D:` "Anthony" es
>   disco FALLADO, no usar. Año completo de OI+funding YA bajado; tick+OB 1s del año en descarga (~4GB).
> - **Pregunta abierta:** ¿algún evento sub-minuto y/o contexto OI/funding predice > 11 bps robusto IS/OOS?
>   Si no → multi-instrumento o aceptar que BTC-solo no tiene edge intradía.

> **(2026-06-19 sesión 2): el "$74.9K" del modelo directions era LOOKAHEAD (features H1/H4).**
> Ver `docs/mtf/MTF_WORKLOG_2026-06-19_lookahead.md`. Infra correcta construida (sidecar, paridad).

## ⚠️ CORRECCIÓN CRÍTICA 2026-06-19 (sesión 2): lookahead H1/H4

`compute_spot_features.compute_h1/h4_features` mapeaban el bucket HTF EN-CURSO (close=fin de hora)
a cada barra M1 → **lookahead intra-hora** en `h1_bos_bear/bull`, `h4_bos_bear`, etc. El backtest
"veía" el cierre futuro de la hora al filtrar entradas. **Corregido a causal** (`idx-1` = último
bucket cerrado). Parquet regenerado causal (backup `.lookahead.bak`).

**Impacto (lookahead → causal):** Capital $74,916 → **$3,297**. Combinado OOS +0.412 → **+0.126R**.
Longs OOS +0.681 → **−0.259 (negativo)**. Shorts OOS +0.386 → +0.164.

**Autopsia:** el lookahead era un filtro casi-perfecto del futuro (mismo % de gate, pero cherry-pick:
ganadores +0.631 vs duds +0.021). Ver worklog §4.

**Re-research causal (shorts):** `vpin` murió (era artefacto). El "+0.44R recuperado" con
`fp_result_sell` era overfit a otro motor (mtf_v2 20h ≠ directions 4h). Configs causales oscilan
+0.25 a −0.06R OOS = **ruido**. Cuello: n≈40-58 OOS, muy chico para validar en BTC solo. **Sin edge
desplegable.** mtf_system revertido a baseline honesto (unión, sin gates fp, sin vpin, longs off).

**Infra construida y verificada (sí sirve):** sidecar `feature_server.py` (Python = una fuente de
verdad para features) + paridad de features PERFECTA + venue config (fee swappable) + parquet causal.
Costura: Python computa features, Rust ejecuta. Pendiente: cablear sidecar↔monitor con kill-switch.

**NO repetir:** validar refinamientos en el motor de despliegue (no otro harness); no tunear sobre
n≈50 OOS; no usar features HTF del bucket en-curso (lookahead).

---

## HISTÓRICO (PRE-corrección lookahead): `mtf_system.py` directions — los números eran inflados

Sobre futuros bybit-perp (`data/bybit-perp/processed/btcusdt_perp_m1.parquet`, 767K barras).
**[INVÁLIDO por lookahead] IS n=309 WR 54.7% +0.502 / OOS n=82 WR 53.7% +0.412. $500→$74,916.**

Componentes (todos validados IS/OOS):
- **Unión disparadores ICT shorts**: rejection@VAH + FVG + OrderBlock + Displacement + LiquiditySweep
  (OTE y EqualHigh sweep EXCLUIDOS: flip OOS). La unión es NO-separable (slot-competition):
  quitar un trigger por su número aislado empeora el sistema.
- **Longs espejo** (rechazo VAL/AL/PDL/WL, régimen alcista, H1 bull, vetos). Muestra OOS chica (n=7).
- **Concurrencia por dirección** (1 short + 1 long): nunca se solapan (regímenes D1 opuestos) →
  volumen SIN riesgo correlacionado. Esto es lo que dio 2.4x volumen / 4.7x equity vs short-único.
- Confirmación orderflow: D1 EMA20 + H1 estructura + body_below_poc + minus/plus_ticks + veto fp_absorb.
- **Veto `vp_lvn_below==False`** (edge: OOS +0.635→+0.780) y **sizing vpin-aware** (`tape & vpin_hi`
  = firma institucional robusta OOS; vpin ⊥ tape corr=0.12). Ver [[project-orderflow-funnel]].
- Salida: target 2.8R, timeout 4h (240m), stop H1 ±0.40·ATR (0.30-0.75%).

**Techo de tpd**: 2-4 tpd en UN símbolo = leverage correlacionado (DD escala 28→70%, edge degrada).
El camino real a 2-4 tpd es MULTI-INSTRUMENTO (ETH/SOL paralelos = edge independiente). Pendiente.

**Pendiente**: portar mtf_system a `mtf_spot_detector.rs` + parity; multi-instrumento (re-bajar ETH).

---

# HISTÓRICO — MTF spot v7/v2 (2026-06-18, pre-futuros)

**Sistema live en Railway + Supabase. Rust monitor, Python analysis/backtest, React UI.**
**Activo actual: BTC SPOT (migrado de futuros multi-símbolo)**

---

## SHORTS — mtf_basics.py v7 (ACTUAL)

**Resultados walk-forward (366 días Jun 2025–May 2026):**
- IS: 681 trades, WR 53.2%, AvgR +0.308, 3.1 tpd
- OOS: 321 trades, WR 53.0%, AvgR +0.315, 3.8 tpd
- Capital: **$500 → $119,138** (rebalanceo mensual 2% base)

### Reglas de entrada
- Nivel: high dentro del 0.70% de VAH (requerido) + PDH/AH/WH
- Vela: wick superior 30-85%, cierre bajista
- Flujo: OBI < -0.05 OR delta < 0
- Sesiones: London + Overlap + NY
- Stop: H1_high + 0.40 × ATR_H1 (0.30%-0.75%)
- Target base: Chop=1.5R / Expansion=3R / else=2R
- CVD exit: 3 barras CVD+ consecutivas + OBI > 0.15 a ≥1R

### Bloqueos estructurales
- PDH+VAH (WR 37.9%)
- PDH+AH+VAH triple (WR 37.8%)
- WH hora 15h UTC (WR 30.8%)
- AH+VAH cuando OBI < -0.15 (WR 30%)

### Position sizing + targets — Score v3 BOOST a

Score = `sell_vol≥3.989` + `buy_vol≥2.712` + `cvd_slope>0` + `vr≥0.933`

| Score | Size | Target override | WR OOS | AvgR OOS |
|-------|------|----------------|--------|----------|
| 0/4 | 0.20× (0.4%) | regime | 55.6% | +0.339 |
| 1/4 | 0.50× (1.0%) | **1.5R fijo** | 48.0% | +0.153 |
| 2/4 | 1.00× (2.0%) | regime | 52.6% | +0.341 |
| 3/4 | 1.50× (3.0%) | **1.5R máx** | 53.8% | +0.335 |
| 4/4 | 2.00× (4.0%) | regime | 56.2% | +0.442 |

**Por qué score-specific targets:**
- Score 1: MFE>=2R = 0% (nunca llega) → 1.5R es el techo real
- Score 3: 11 stranded OOS (llegaron 1R MFE, revirtieron al stop) → 1.5R capta antes de la reversión

### Por nivel OOS
- WH+VAH: 65.4% WR, AvgR +0.641 (premium)
- AH+WH+VAH: 61.0% WR, AvgR +0.482
- AH+VAH: 52.1% WR, AvgR +0.286
- VAH: 51.9% WR, AvgR +0.281 (base)

### Por sesion OOS
- Overlap (12-16h): 60.2% WR, AvgR +0.529 (mejor)
- NY (16-20h): 52.3% WR, AvgR +0.313
- London (07-12h): 47.2% WR, AvgR +0.136 (débil — pendiente de analizar)

### Historial versiones SHORTS

| v | Cambio | Capital |
|---|--------|---------|
| v5 | flat 2% + bloqueos estructurales | $44,794 |
| v6 | + score v3 BOOST a (sin hora blocks) | $114,606 |
| **v7** | **+ targets calibrados por score (1/3 → 1.5R)** | **$119,138** |

---

## LONGS — mtf_longs.py v2 (nuevo — score sizing)

**Resultados OOS (Mar-May 2026):**
- IS: 804 trades, WR 49.5%, AvgR +0.200, 3.1 tpd
- OOS: 333 trades, WR 52.6%, AvgR +0.257, 3.7 tpd
- Capital: **$500 → $77,847** (MaxDD 19.7%)

### Reglas de entrada
- Nivel: low dentro del 0.70% de VAL (requerido) + PDL/AL/WL
- Vela: wick inferior 30-85%, cierre alcista
- Flujo: OBI > +0.05 OR delta > 0
- Stop: H1_low - 0.40 × ATR_H1 (0.30%-0.75%)
- Target: Chop=1.5R, Expansion=3R, else=2R
- CVD exit: 3 barras CVD negativas + OBI < -0.15 a ≥1R

### Filtros estructurales (NO bloques de hora)
- PDL+AL+VAL: bloqueado (WR 41.9%)
- bid_wall=True: bloqueado (soporte falso)

### Position sizing por score (v2 — CLAVE)
Score = suma de: sell_vol≥Q50, buy_vol≥Q50, delta>Q50, vr>Q50
- Score 0/4: 20% del monthly_risk (→ 0.4%)
- Score 1/4: 50% (→ 1.0%)
- Score 2/4: 100% (→ 2.0%) [base]
- Score 3/4: 150% (→ 3.0%)
- Score 4/4: 200% (→ 4.0%)

Thresholds IS (Jun2025-Feb2026): sell_vol Q50=2.992, buy_vol Q50=4.055, delta Q50=0.806, vr Q50=0.940

### Score OOS performance
- Score 0: 46.6% WR, AvgR +0.109
- Score 1: 43.8% WR, AvgR +0.032 (weakest)
- Score 2: 58.2% WR, AvgR +0.372
- Score 3: 55.7% WR, AvgR +0.357
- Score 4: 59.2% WR, AvgR +0.425 (premium)

### Por nivel OOS
- WL+VAL: 76.9% WR, AvgR +0.770 (premium — n=13)
- AL+VAL: 53.9% WR, AvgR +0.292 (volumen)
- VAL:    51.5% WR, AvgR +0.241 (base)
- PDL+VAL: 40.0% WR, AvgR -0.034 (débil en OOS)

---

## DISCOVERY: Features > Horas

**Análisis de 81 columnas del M1 parquet.**

El usuario tenía razón: las horas son un PROXY de condiciones reales.

Las condiciones reales que determinan calidad del long:
- `sell_vol` alto (Q50+): absorción real de vendedores = +14pp WR vs Q1
- `buy_vol` alto (Q50+): compradores activos sobre la media
- `delta` alto (Q50+): presión neta compradora
- `vr` > 1.0: volumen total sobre promedio

Otros hallazgos:
- `bid_wall=True` → 42.9% WR (soporte falso, alguien muestra para atrapar)
- `big_trade_bearish=True` → 0% WR conceptualmente (en práctica no activa con vela alcista)
- `stacked_imb=Bearish` → 42.2% WR (no bloquear: mata volumen)
- `Expansion` regime: 38.6% WR para longs (pero target 3R lo maneja)

**El position sizing por score reemplaza los bloques de hora:**
- Hora v2 + flat 2%: $26,357
- Sin hora + score BOOST a: $78,268

---

## Historial versiones LONGS

| v | Cambio | Capital OOS | WR OOS |
|---|--------|-------------|--------|
| v1 | bloqueos hora + flat 2% | $25,745 | 52.8% |
| **v2** | **score sizing por features (no hora)** | **$77,847** | **52.6%** |

---

## Decisiones descartadas (con evidencia)

### Shorts
- Asia excluida: WR 43.6%, horas 03h/06h tóxicas
- H4 filter: WR 61% pero capital $14K (mata frecuencia)
- D1 EMA20: sin beneficio, corta 60% volumen
- M5/M15 TF: stops desbalanceados para ruido real

### Longs
- swing_low_50 como nivel: WR 47-49% (no añade valor)
- Filtros numéricos binarios (obi10_min, sell_vol threshold): matan volumen → matan capital
- WL_TOL + bloqueo PDL+WL: PDL+WL también bloquea WL+VAL limpios que detectan PDL cercano
- Hora blocks: reemplazados por score sizing (más preciso, menos restrictivo)
- Target boost por score: no mejora capital (trades largos → timeouts)
- CVD=2 en alta calidad: no mejora

---

## Data pipeline

```
data/bybit-spot/
  processed/
    btcusdt_m1.parquet   → 489,600 barras, 81 columnas (COMPLETO)
```

## Archivos clave
- `backtest/mtf_basics.py` — SHORTS v7 (score sizing + score-specific targets)
- `backtest/mtf_longs.py` — LONGS v2 (score sizing)
- `backtest/_mfe_analysis.py` — análisis MFE/stranding que llevó a v7
- `backtest/_exit_deep_analysis.py` — simulación target x score
- `backtest/_feature_discriminant.py` — análisis de 81 features
- `backtest/_feature_oos_validate.py` — validación OOS del score sizing

## Próximos pasos
1. Aplicar análisis MFE a LONGS — mismo problema potencial en score 1/3
2. London para shorts — 47.2% WR, analizar si score 0/1 lo arrastra o es todo London
3. WH+VAH bonus — 65.4% WR, explorar bonus de nivel sobre score (riesgo overfitting: n=15)
- `docs/MTF_SPOT_SHORTS_SPEC.md` — spec shorts

## AUDITORÍA v7/v2 (2026-06-18) — score sizing es leverage, no edge

Auditado con `backtest/_audit_v7.py` (toggles sizing/override + sweep de ladders).

**Hallazgos clave:**
1. **El score NO es monótono OOS.** Shorts AvgR por score — IS: sc0+0.14 sc1+0.41 sc2+0.20 sc3+0.22 sc4+0.56 / OOS: sc0+0.34 sc1+0.15 sc2+0.34 sc3+0.34 sc4+0.44. Solo **sc4 es robusto** (mejor en ambos). sc1 se invierte (mejor IS → peor OOS). La escala [.2,.5,1,1.5,2] asume un orden que los datos no sostienen. Longs igual: sc1 peor OOS (+0.032), sc4 mejor (+0.425).
2. **Sizing no cambia el edge.** WR/AvgR idénticos (+0.304 / 52.2% OOS) en TODAS las ladders — el sizing solo cambia cuánto $ monta cada trade. El "$119K" es artefacto de apalancamiento, no edge.
3. **La ladder v7 de 5 peldaños está DOMINADA.** 2-tier (solo sc4=2x, resto 1x) = **$184,814 @ MaxDD 18.6%** vs v7 5-rung $115K @ 19.0%. Más capital, menos drawdown, más simple. v7 desperdicia compounding al down-size de sc0 (que es +0.34 OOS, perfectamente bueno).
4. **Target override (sc1→1.5R, sc3→cap 1.5R): huella de overfit a OOS.** Baja TotalR IS (214.5→213.1) y sube OOS (96.2→101.3) — firma clásica de tuning a OOS. Magnitud ~5R/~$3K, inmaterial. **Descartar, no portar a Rust.**
5. Thresholds Q50 hardcoded ≈ pero ≠ Q50 de entries IS (vr 0.933 vs 0.896 real). Provenencia difusa, riesgo bajo.

**El edge REAL y robusto:** 3 ingredientes + bloqueos + stop H1 + target regime + CVD exit → OOS WR ~53%, AvgR ~+0.31. ESO es lo que debe portarse fiel a Rust.

**Decisión de implementación:** portar el core fiel + reemplazar ladder 5-rung y override por 2-tier (sc4=2x, resto 1x) o flat. NO cementar el overfit en Rust.

## Próximos pasos
1. Aplicar la simplificación 2-tier a mtf_basics.py y mtf_longs.py, actualizar specs SPOT (siguen en v5/v1, código en v7/v2)
2. Portar a mtf_spot_detector.rs (Shorts v4/Longs v1 → core robusto) + re-correr parity
3. Paper live Bybit SPOT
4. Posible: London para shorts (47.2% WR vs 60.2% Overlap)
