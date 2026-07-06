# ESTRATEGIA SCALPING — sc3 (absorción maker MR)

> Espacio dedicado al scalping, **separado del liquidity A+B**. En análisis activo.
> Veredicto detallado: [VERDICT_2026-06-28.md](VERDICT_2026-06-28.md).

## Qué es

Scalping por **provisión de liquidez maker con confirmación de absorción**. Precio llega a un
nivel VP (POC/VAH/VAL), hay alto volumen agresor que **no rompe** el nivel y el delta del
footprint va en contra → la contraparte absorbe → **fade con límite maker**. Gestión: parcial
50% en TP1 → breakeven → target recortado a RR fijo (`rr_cap`).

Surgió de aterrizar el catálogo de 8 setups de scalping. **De los 8, sobrevive solo este.** Todo
lo taker/breakout/momentum (#2 OBI, #4 wall, #5 vwap, #7 stacked, #8 LVN) muere por fee + chop.

## Estado (2026-06-28)

| Test | Resultado |
|------|-----------|
| Backtest M5, fee honesto, 3 activos | OOS+ los 3 (BTC/ETH/SOL +0.45/+0.50/+0.57) |
| Perfil scalp (rr_cap) | WR 76-81%, DD 2-5% |
| Resolución | **M5** (M1 muere) |
| Fills reales (Nautilus tick+cola) | avgR real ≈ backtest (BTC +0.41 / ETH +0.59 / SOL +0.64), 44 fills |
| ¿Aditivo vs liquidity? | **SÍ** — 97% trades únicos; confluencia 2-3% rinde +1.0..+1.8R |
| ¿DOM refina absorción? | Sí, modesto (filtro near5 ΔOOS +0.24 ETH/SOL) |

## Config ganadora (canónica → `_scalp.SC3` / `run_sc3()`)

```
TF=M5 · entrada=maker límite en nivel · niveles AMPLIADOS (VP+PDH/PDL/weekly/swing)
filtro ATR>mediana(500) · stop floor 0.15% · gestión FADE en los 3 (parcial 50%→BE→rr2.5)
  BTC: vr2.5 stop0.5×ATR tol0.6 · fade   → IS+0.56 OOS+0.46 WR65% DD4%  (n538,1.0/d)
  ETH: vr1.5 stop0.5×ATR tol0.6 · fade   → IS+0.45 OOS+0.52 WR58% DD9%  (n697,1.9/d)
  SOL: vr2.5 stop0.5×ATR tol0.6 · fade   → IS+0.59 OOS+0.55 WR58% DD6%  (n392,1.1/d)
Portfolio OOS +0.51, maxDD 5%, equity $500→$4,724 (+845% fijo), 4.5/d. Curva suave, WR alto, robusta (IS≥OOS).
Sizing: 2× cuando confluencia≥4 (esos rinden OOS +1.07). NY 12-20 UTC = sweet spot (+0.80, no filtrar igual).
Fuente única: `_scalp.SC3` + `run_sc3()`.

VARIANTE BTC-trail (alto-riesgo/alto-retorno, NO default): mgmt='trail' trail_atr=6 stop0.6 →
OOS +0.88 PERO WR 26% / DD 33%, fragil (depende de tendencia BTC, OOS>>IS). Solo si aguantás esa varianza.
```

Hallazgos de la ronda de optimización (lo que SÍ y lo que NO):
- ✅ **Gestión per-asset** (BTC trail / ETH-SOL fade): +0.48→+0.59. Corrobora el trail-BTC del liquidity.
- ✅ Confluencia≥2 niveles: mejora avgR sin costar frecuencia.
- ❌ **Más frecuencia** (absorción event-based, 6-9/d): SIN edge — el edge necesita niveles VP mayores.
- ❌ **Bookmap/heatmap** (liquidez resting): no generaliza (SOL +0.11 / ETH −0.05), `wall_sz`=spoof.
- ❌ Target ATR-puro / RR-puro: no le ganan al estructural-clip 2.5. Régimen-router: empeora ETH/SOL.

## Código (`tradio/backtest/`)

| Script | Qué hace |
|--------|----------|
| `_scalp.py` | Motor + 7 generadores (sc1..sc8) + grid runner. `python backtest/_scalp.py <setup> --symbol BTCUSDT --tf 5` |
| `_scalp_fp.py` | Builder de features de footprint (imbalance diagonal, stacks, absorción) |
| `_scalp_sc3.py` | Calibración fina multiactivo del ganador. `--tf 5 [--fine]` |
| `_scalp_overlap.py` | Solape sc3 ↔ liquidity A+B (aditividad) |
| `_scalp_absorb.py` | ¿El DOM real (near5) refina el trigger de absorción? (ETH/SOL) |
| `_nautilus_scalp.py` | Validación de fills reales tick-a-tick + queue position. `[dias] [SYMBOL] [tf]` |

## Datos / reconstrucciones → **disco E** (no reconstruir)

Home: **`E:/bybit-data/_scalp/`** — ver [`MANIFEST.md`](file:///E:/bybit-data/_scalp/MANIFEST.md) ahí.
- `reconstructions/footprint/` — footprint por nivel (tick→footprint, LA reconstrucción cara)
- `reconstructions/scalp_fp/` — features derivadas
- `results/` — salidas de backtest (llenar al analizar)

El loader (`_scalp.py`) usa las copias vivas en `data/` y `E:/bybit-data/bybit-perp-*/processed/`;
si faltan, cae al backup de `E:/bybit-data/_scalp/reconstructions/`.

## Próximos pasos (avenidas abiertas)
1. **Paper como sleeve paralelo** al liquidity (juntar n real de fills).
2. **Confluencia sc3∩liquidity** como filtro de convicción/sizing (rinde +1.0..+1.8R).
3. **DOM event-level** (replay ob500 snapshot): absorción-real vs muro-falso/spoofing — el proxy
   M1 da Pearson 0.09; event-level debería afilar. Heavy (extender `crates/ob_parser`).
4. Per-asset tuning fino (vr_thr ya difiere; explorar rr_cap por activo).
