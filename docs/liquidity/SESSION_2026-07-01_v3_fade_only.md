# Sesión 2026-07-01/02 — Autopsia del paper, retiro del trail y validación integral de v3 fade-only

**Resultado ejecutivo:** la estrategia Liquidity pasa de A+B enrutado a **fade-only (v3)**. El paper no mostraba pérdida de edge sino (1) el peor mes cripto desde jun-2022, (2) un detector de régimen sin paridad que ruteaba 52% a trail, y (3) artefactos de medición. v3 quedó validado como combo completo (OOS +1.45/+1.77/+1.46, DD 1-3%) y sobrevivió 4 desafíos de diseño. Pendiente: `git push` (deploya v3) y 2-3 semanas de paper.

---

## 1. Autopsia de los resultados del paper (BD Supabase)

### Eras identificadas (y limpieza de BD)

| Era | n | avgR | WR | Veredicto |
|-----|---|------|----|-----------|
| Pre-fixes (06-22→24) | 75 | -0.82 | 9% | Bugs de paridad conocidos → **99 filas BORRADAS de la BD** |
| Era limpia (06-25→26) | 65 | +0.80 | 25% | Única ventana comparable; positiva |
| Post 06-27 | 27 | -1.36 | 0% | Ver §2 — mercado + trail, no bugs |

### Falsa alarma corregida: "el parcial TP1 está roto" — NO

- tp1 estructural queda a **mediana 6.1R** (stops diminutos): 58/67 trades sin parcial nunca lo alcanzaron → `filled1=false` correcto.
- 8/9 que sí llegaron eran gestión trail (sin parcial by design, paridad OK con `_strategy_ab.py`). 1 solo caso anómalo.
- `book.rs` rama Fade replica exactamente el motor backtest.

### El 0/27 post-27-jun explicado

- **Macro**: junio 2026 = peor mes desde jun-2022 (BTC -20% mensual, -34% YTD, F&G 12 = mínimo del ciclo, conflicto US-Irán, -$5.96B ETF outflows). Capitulación 30-jun a $58k (OI BTC +9.3% = shorts presionando) → **short squeeze 01-jul** (OI -10.5% con precio +2.4%; SOL funding en cap).
- **Micro**: tras la explosión de vol del 25-26, el ATR M15 decayó más rápido que su mediana(500) → stops a la mitad (0.27% vs 0.51%) en mercado que seguía grindando 2-5%/día. MFE mediana de los 27: 0.68R.
- **Umbral ATR: NO tocar** — buckets no-monotónicos (1.1-1.3 es el ganador +1.46; subir a 1.3 bloquearía +16.1R de winners).

---

## 2. Decisión: retiro del trail → FORCE_FADE (commit `3a8eba9`)

- El detector de régimen del binario rutea **52% a trail vs 7-13% validado** (problema conocido: EMA5+racha2 sobre M15 = 81% acuerdo con parquet; a 7.5% de base rate eso sobre-dispara). Trail vivo: -0.57 avgR, 26/51 tocaron +1R antes de morir.
- Backtest A-sola vs A+B (`backtest_multiasset.py`): fade-only pasa regla dura (OOS +1.24/+1.46/+0.76), **gana a routed en ETH**, DD menor (2.6-4.2%) y WR 62-75%. El margen de routed (~+0.3R) depende de un detector bien calibrado que el binario no tiene.
- Implementación: `FORCE_FADE` env (default **true**) en `levels.rs`/`main.rs`; `regime` se sigue persistiendo para auditar el detector; `filter_version=v3_fade_only`.
- Incidente resuelto: los env examples de `deploy/` estaban untracked y el de SOL tenía la service_role key real → sanitizado con amend antes de salir del repo.

## 3. Portfolio combinado 3 símbolos (commit `19d324f`, `_portfolio_fade.py`)

Nunca se había modelado. Ventana común 359d, fade-only, $5/trade:

- **DD portfolio 2.70% < BTC solo 4.20%** — corr diaria de R entre símbolos ~0.01-0.10. $500→$8,245.
- Entradas multi-símbolo simultáneas: avgR **+2.08 vs +1.14** — cuando los 3 disparan juntos suele ser evento de vol de dos vías (el mejor régimen). El cluster perdedor del 06-29 fue la excepción (squeeze).
- **Cap de concurrencia global: NO** (K=3 ahorra 0.7pp de DD, regala 64R/año). Peor día: -5.7R; 22% días negativos.

## 4. Investigación con 2 agentes paralelos (commit `2920b49`)

### 4a. Modelo de fill condicional (`_fill_model.py`)

- **P(fill) real por nivel = 47.3%** (412 episodios dedup) — el "13%" era artefacto del re-posteo anti-spam. BTC 54 / ETH 46 / SOL 39%.
- **Selección adversa REFUTADA**: corr(P(fill), result_r)=+0.07; backtest reponderado por P(fill|contexto) mueve avgR ≤0.04R → **el edge sobrevive intacto al modelo de fills real**.
- Driver de fill: momentum hacia el nivel (72% vs 40%) — y ese contexto es rentable en los 3.
- ⚠️ **Niveles IFVG llenan 10% en vivo (3/30)** vs 53% POC — vigilar si su contribución teórica existe en la práctica.
- Recalibrar con 30+ días de eventos.

### 4b. Paridad v3 (`_v3_parity.py`) — lo deployado nunca se había backtesteado como combo

| Config | OOS BTC/ETH/SOL | Regla dura |
|--------|-----------------|------------|
| fade sin filtros | +0.71/+0.95/+0.68 | pasa |
| **v3 (fade+H1+dist+IFVG, ss=0.8)** | **+1.45/+1.77/+1.46**, DD ~2%, WR 86-90% | **pasa con margen** |

- **dist>0.5ATR es EL filtro** (solo, casi duplica avgR en los 3).
- **H1 slope: mismo avgR sin él, +34% netR OOS** (recorta 26% de trades rentables) → candidato A/B en paper (env-flag futuro).
- `stop_scale` 0.7 pasa regla dura estricta pero alto riesgo modelo (fill exacto en stop, stops ya 0.37%) — anotado, NO perseguir 0.6 (gradiente mecánico).
- Sobrevive slip 5bps (~-0.1R). `p1_frac`/`timeout` planos.

## 5. Cuatro desafíos de diseño a v3 — sobrevive todos

### 5a. Auditoría del WR 90% (escepticismo del usuario — sano)

Anatomía OOS (3 símbolos): **68.8% de trades salen breakeven** (parcial cobrado + BE, avgR +0.67) / 15% target (+5.37) / 5.8% timeout (+7.18) / 10.5% stop (-1.37). El WR alto es **mecánico del diseño parcial+BE**. Robustez: top-10 trades = solo 13% del netR (sin ellos avgR +1.35); mensual OOS +1.10→+2.00 sin mes flojo; IS≈OOS; PF 11-12. **La pata débil: asume que el parcial límite en tp1 llena al toque — métrica reina del paper v3 (`filled1`).**

### 5b. ¿Sin parcial, "de corrido"? (`_nopartial_sweep.py`, commit `2610870`)

- Sin cap: muerto (DD 8-17%, confirma recon de junio).
- **Con cap: plateau robusto 3.8-4.4R**, punto operable 4.0 (OOS +1.71/+1.68/+1.66, netR +10%) — pero falla regla dura (ETH OOS, SOL IS) y DD 2-6× (6.2/2.3/6.6%). **Riesgo-ajustado gana el parcial** (mismo presupuesto de DD permite ~2× sizing).
- **Queda como PLAN B validado** si el fill de tp1 falla en vivo (el binario ya tiene `TP2_CAP_R`; faltaría `NO_PARTIAL`). No usar cap 4.2 (salto de DD BTC 6.2→9.3%).

### 5c. ¿tp1 a RR fijo? (`_tp1_rr_sweep.py`, commit `a28cab6`)

Curva **monotónica**: 0.5R +0.86/+1.28/+0.82 → 3R +1.39/+1.82/+1.41 → **estructural +1.45/+1.77/+1.46**. Ningún RR fijo pasa regla dura. Bonus didáctico: tp1=1R da **WR 99%** perdiendo 0.4R de avgR — el WR es un dial de diseño, no medida de edge. La estructura del mercado es el target correcto.

### 5d. ¿Otro porcentaje de parcial? (sweep 0.3→0.9)

Gradientes **opuestos por símbolo** (BTC/SOL quieren 90%, ETH quiere 30%) que se cancelan → 50% es el equilibrio y ninguna fracción pasa regla dura. Efecto minúsculo (±0.05R BTC/SOL) = parámetro plano = robustez, no hay que afinarlo. Per-symbol p1 = optimizar ruido (SOL IS contradice SOL OOS).

---

## Config final de producción (v3)

| Parámetro | Valor |
|-----------|-------|
| Gestión | fade-only, parcial **50%** en **tp1 estructural** → BE → target estructural |
| `FORCE_FADE` | true (default) |
| Filtros | H1 slope + dist>0.5ATR + ATR>med(500); niveles VP + IFVG |
| `stop_scale` / `tp2_cap_r` | 0.8 / 0 (sin cap) |
| Símbolos / riesgo | BTC+ETH+SOL, $5/trade, sin cap de concurrencia |
| Esperado OOS | avgR +1.45/+1.77/+1.46 · WR 86-90% · DD 1-3% · ~6 trades/día |

## Pendientes (en orden)

1. **`git push`** → deploya v3 (verificar banner `FORCE_FADE=true` y primer trade `v3_fade_only`).
2. Acumular 2-3 semanas de paper. **Métrica reina: fill del parcial en tp1** (`filled1` cuando MFE ≥ tp1). >80% → parcial confirmado; si no → plan B all-in cap 4.0.
3. Vigilar: fill de niveles IFVG (¿siguen en 10%?), ETH fade (-0.35 vivo vs +1.77 backtest, n chico).
4. A/B candidato: quitar H1 slope (mismo avgR, +34% netR).
5. Infra: testnet executor (migración SQL + keys), cron semanal de paridad paper-vs-backtest, recalibrar fill model a los 30 días.

## Cerrado en esta sesión (no reabrir)

Trail/routing con el detector actual · all-in sin cap · tp1 RR fijo · p1_frac ≠ 0.5 · umbral ATR (no-monotónico) · cap de concurrencia · hipótesis de selección adversa en fills · "parcial TP1 roto" (falsa alarma).

## Commits

| Commit | Qué |
|--------|-----|
| `3a8eba9` | FORCE_FADE en binario Rust + CLAUDE.md + env examples (key sanitizada) |
| `19d324f` | `_portfolio_fade.py` — portfolio combinado |
| `2920b49` | `_fill_model.py` + `_v3_parity.py` — agentes fill model y paridad v3 |
| `2610870` | `_nopartial_sweep.py` — all-in capeado (plan B 4.0R) |
| `a28cab6` | `_tp1_rr_sweep.py` — tp1 estructural > RR fijo |
