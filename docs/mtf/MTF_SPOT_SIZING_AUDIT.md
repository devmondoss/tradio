# MTF Spot — Auditoría de Sizing: Edge vs Apalancamiento

> Auditoría walk-forward del "score sizing" introducido en Shorts v7 / Longs v2.
> Fecha: 2026-06-18 | Script reproducible: `backtest/_audit_v7.py`
> Conclusión: el score sizing **no mejora el edge — es solo apalancamiento**. Se simplificó a una versión conservadora y robusta.

---

## 1. Las dos perillas — no confundirlas

El sistema tiene dos cosas distintas que ambas se expresan con números tipo "1.5" / "2". **No son lo mismo.**

### A) Target — ES edge
- Dónde se toma ganancia, medido en R (múltiplos del riesgo).
- En código: variable `tgt`, depende del regime → Chop=1.5R, Expansion=3R, resto=2R.
- Cambiar el target cambia **qué trades ganan/pierden** → mueve WR y AvgR.

### B) Multiplicador de tamaño — es APALANCAMIENTO puro
- Cuánto dinero se arriesga en el trade (% del capital).
- En código: `SCORE_MULT` (shorts) / `BOOST_MULT` (longs). Ej. `sc4=1.5×` = arriesga 3% en vez de 2% en trades de score 4.
- **No cambia si el trade gana o pierde, ni cuántos R hace.** Solo escala dólares y drawdown, en la misma proporción.

**Regla mental:** el edge lo dan las entradas, los stops y los targets. El multiplicador de sizing es una perilla de riesgo aparte: sube retorno y drawdown juntos, no mejora ninguna métrica de calidad.

---

## 2. Qué se auditó y qué se encontró

Toggles y sweeps en `backtest/_audit_v7.py` sobre el walk-forward (IS: Jun 2025–Feb 2026 / OOS: Mar–May 2026).

### Hallazgo 1 — El score NO es monótono fuera de muestra
El sizing viejo `[0.2, 0.5, 1.0, 1.5, 2.0]` asume que más score = mejor trade. Los datos lo desmienten:

**Shorts — AvgR por score:**
| | sc0 | sc1 | sc2 | sc3 | sc4 |
|---|---|---|---|---|---|
| IS | +0.14 | **+0.41** | +0.20 | +0.22 | +0.56 |
| OOS | +0.34 | **+0.15** | +0.34 | +0.34 | +0.44 |

`sc1` se invierte por completo (mejor en IS → peor en OOS). **Solo `sc4` es robusto** (mejor en ambos). Longs muestra el mismo patrón: sc1 peor OOS (+0.032), sc4 mejor (+0.425).

### Hallazgo 2 — El sizing no toca el edge
WR y AvgR OOS son **idénticos** (+0.304 / 52.2% shorts) en TODAS las escalas probadas. Lo único que cambia es el capital final. El "$119K" del headline era un artefacto de apalancamiento, no de calidad.

### Hallazgo 3 — La escala de 5 peldaños estaba mal repartida
Achicar `sc0` a 0.2× desperdiciaba compounding en trades que en OOS son positivos (+0.34). Una escala que solo levera el bucket robusto rinde más con menos riesgo.

### Hallazgo 4 — El target override era overfit a OOS (eliminado)
Override `sc1→1.5R`, `sc3→cap 1.5R`: bajaba TotalR IS (214.5→213.1) y subía OOS (96.2→101.3) — firma clásica de tuning a OOS. Magnitud inmaterial (~5R / ~$3K). **Eliminado.**

---

## 3. Comparación de perillas (edge constante)

**SHORTS** (sin target override):
| Perilla | Capital | MaxDD | OOS WR | OOS AvgR |
|---|---|---|---|---|
| plano 1× | $61K | 14.1% | 52.2% | +0.304 |
| **sc4=1.5× (activo)** | **$104K** | **~16%** | 52.2% | +0.304 |
| sc4=2× | $185K | 18.6% | 52.2% | +0.304 |

**LONGS:**
| Perilla | Capital | OOS WR | OOS AvgR |
|---|---|---|---|
| plano 1× | $24K | 52.6% | +0.257 |
| **sc4=1.5× (activo)** | **$39K** | 52.6% | +0.257 |
| sc4=2× | $61K | 52.6% | +0.257 |

WR y AvgR no se mueven — confirma que es solo la perilla de riesgo.

---

## 4. Decisión activa

`SCORE_MULT` / `BOOST_MULT` = **`[1.0, 1.0, 1.0, 1.0, 1.5]`** en ambos sistemas.
- Score 0–3: riesgo plano (2% con rebalanceo mensual).
- Score 4: boost a 1.5× (3%) — único bucket robusto IS+OOS.
- Target override: eliminado (target queda solo por regime).

**Edge real y robusto a portar a Rust:** 3 ingredientes + bloqueos + stop H1 + target por regime + CVD exit → OOS WR ~52%, AvgR ~+0.30. Eso es lo que importa, no el capital final.

### Cómo cambiar la perilla
Una sola línea en `backtest/mtf_basics.py` y `backtest/mtf_longs.py`:
- Flat puro (más conservador): `[1, 1, 1, 1, 1]`
- **Activo:** `[1, 1, 1, 1, 1.5]`
- Agresivo (subir solo tras validar fills reales en paper): `[1, 1, 1, 1, 2.0]`

---

## 5. Por qué no perseguir más apalancamiento ahora

- El drawdown del backtest (14–19%) es un **piso optimista**. En real, con slippage, fills perdidos y cambios de régimen, esperar 1.5–2× eso.
- Buena parte del capital OOS descansa en 3 meses excepcionales (mar 2026 = 61.5% WR). No es el régimen permanente.
- Apalancar fuerte una estrategia con fills no validados es la forma más común de quemar una cuenta rentable. Primero paper live, comprobar que los fills se parecen al backtest, recién después considerar subir la perilla.
