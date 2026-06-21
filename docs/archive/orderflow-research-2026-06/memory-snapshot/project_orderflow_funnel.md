---
name: project_orderflow_funnel
description: Qué features de orderflow sirven vs no (validado IS/OOS); sistema refinado régimen+agresión+veto absorb_buy; sizing multi-tier CVD+OBI+tape validado 2026-06-19
metadata: 
  node_type: memory
  type: project
  originSessionId: 4cf20f96-9548-48f0-a467-7b7ab5e0949c
---

# Orderflow Funnel — Validación completa

## Sistema refinado original (del barrido inicial)
Régimen D1 EMA + agresión (minus_ticks>plus_ticks) + veto absorb_buy → IS +0.198 / OOS +0.277.

## Sistema actual v2 con sizing multi-tier (validado 2026-06-19)

### Gates duros (binario)
- `body_below_poc` + `minus_ticks > plus_ticks` + `NOT fp_absorb_buy`
- D1 EMA20 ≤ 0.980 para shorts, 1.000-1.030 para longs
- H1 BOS/ChoCH bear (OR), NOT H4 BOS bear (longs), H1 BOS bull (longs)

### Sizing multi-tier corregido

| Tier | Condición | Mult | IS WR | IS AvgR |
|------|-----------|------|-------|---------|
| 1 | CVD>0 + tape + obi10>=0 | 2.5x | 75.0% | +1.648 PF=6.59 |
| 2 | CVD>0 + tape, obi10<0 | 2.0x | 47.4% | +0.532 |
| 3a | CVD>0 solo | 1.5x | 64.3% | +1.033 |
| 3b | tape solo | 1.5x | 55.6% | +0.729 |
| 3c | obi10>=0 solo | 1.5x | 50.0% | +0.728 |
| 5 | ninguno | 1.0x | 7.7% | -0.941 |

**Why:** CVD>0 = absorción institucional. obi10>=0 = bids pesadas = bull trap en VAH (no obi<0 que sería asks ya visibles = setup menos sorprendente).
Tier 5 NO vetar (OOS=44.4%, 3 wins a 2.5R en May-Jun 2026). Ya está controlado con 1.0x mínimo.

**Resultado global (shorts+longs, $500 inicial):**
```
Capital final : $15,810  (+3,062%)   [actualizado 2026-06-19]
Shorts: IS n=127 WR=51.2% AvgR=+0.658 PF=2.12 | OOS n=42 WR=50.0% AvgR=+0.635
Longs:  IS n=42  WR=45.2% AvgR=+0.346 PF=1.52 | OOS n=7  WR=42.9% AvgR=+0.365
```

### Longs sizing (validado 2026-06-19) — diferente de shorts
Para longs, CVD>0 y OBI>0 (MISMO signo que shorts) son las señales positivas:
- CVD>0 en VAL = compradores DEFENDIENDO nivel (no atrapados)  
- OBI>0 = bids pesadas en VAL = compradores activos
- **Tape solo** = 1.0x (NO 1.5x). IS WR=16.7%, AvgR=-0.612 — tape sin dirección = volumen alto pero bajista

| Tier | Condición | Mult |
|------|-----------|------|
| 1 | CVD>0 + tape + OBI>0 | 2.5x |
| 2 | CVD>0 + tape | 2.0x |
| 3 | CVD>0 o OBI>0 | 1.5x |
| 5 | ninguno / tape solo | 1.0x |

`asian_low` habilitado como nivel de longs (espejo de AH para shorts). 1 trade IS = WR=100%, AvgR=+2.632.

### Absorption Score (AS spec v3) — NO aplica a nuestro sistema
AS = max(DZ,0) × (1-max(-desplaz,0)). Testado 2026-06-19:
- En barras de ENTRY: AS=0 siempre (nuestros entries tienen delta negativo = DZ<0)
- En barras PREVIAS (max ventana 5 barras): n=6 AS>=1.5 → WR=33%, AvgR=-0.532 = NEGATIVO
- Razón: AS está diseñado para entrar EN la barra de absorción, no después del rechazo.
  Nuestro sistema ya captura absorción con CVD>0 + body_below_poc + fp_absorb_buy veto.

### Lo que NO sirve (testado y rechazado)
- `h1_ob_bear`: IS WR=22.5%, AvgR=-0.440 → destruye valor
- `fp_stack_sell` como boost de sizing: discriminante NEGATIVO (más apilado = peor)
- `obi10 < 0` SOLA en tier 3: WR=7.7% catastrófico; dirección correcta es >=0
- CVD>0 como gate duro: reduce volumen, OOS PF mejora pero menos trades
- Absorption Score (AS): no aplica, siempre 0 en nuestro entry pattern

### Disponible sin explotar todavía
- `fp_result_sell` (28.9%): TRUE+CVD>0 → n=16, WR=62.5%, AvgR=+1.071 (pequeño n)
- `fp_unfinished_hi`: FALSE=señal débil negativa, solo 14/127 IS trades
- OI/Funding: requiere API Bybit (pendiente)

## Paridad Python ↔ Rust (2026-06-19)
`mtf_spot_detector.rs` actualizado:
- `n_trades: f64` en `MtfSpotBarContext` (serde default 0.0)
- `sizing_mult: f64` en `MtfSpotSignal`
- Tiers en `on_bar_close`, obi10>=0 shorts / obi10>0 longs (corregido: longs CVD+obi en misma dirección)
- `n_trades_q50` rolling median (warmup + live update cada 500 bars)
- Compilado exitoso 2026-06-19
- **PENDIENTE**: actualizar Rust con longs sizing correcto (CVD>0 y OBI>0, tape solo=1.0x)

Detalle completo de catálogo en `docs/ORDERFLOW_CATALOGO.md`.
