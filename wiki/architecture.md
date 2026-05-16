# Arquitectura del sistema

## Visión general

FlowSurface es un sistema de detección de señales institucionales en crypto. Corre 24/7 en Railway, monitorea BTC perpetual en Binance, detecta setups de trading mediante 6 detectores, y se recalibra automáticamente cuando el mercado cambia.

```mermaid
flowchart TD
    B["🏦 BINANCE\nWebSocket: klines 5m · depth L2 · aggTrade\nREST: funding 60s · spot 30s · OI 5min"]

    M["⚙️ MONITOR RUST — Railway asia-southeast1\n─────────────────────────────\nBarState\n  bars[] → 300 klines rolling\n  cvd_history[] → 50 samples\n  oi_history[] → 7 samples\n─────────────────────────────\non_bar_close() cada 5 min:\n1. VWAP session reset UTC 00:00\n2. derive_regime OLS/ATR ventana 14\n3. volume_profile 150 bins 70% VA\n4. build contexts\n5. route_strategy → 6 detectores → señal\n6. paper account ejecuta trade\n7. write Supabase fire-and-forget\n─────────────────────────────\nconfig_loader cada 5 min:\nlee deployed_params → StrategyConfig dinámico"]

    S["🗄️ SUPABASE — fuente de verdad\n─────────────────────────────\nshadow_signals — señales detectadas\nsignal_outcomes — trades PnL/R/MFE/MAE\ninstitutional_snapshots — estado inst. 5min\nregime_history — cambios de régimen\ncalibration_log — historial calibraciones\ndeployed_params — params activos por régimen\n─────────────────────────────\nvista v_signals_with_outcomes"]

    P["🐍 PIPELINE PYTHON — local cada 30 min\n─────────────────────────────\n1. DegradationMonitor ¿recalibrar?\n   régimen cambió · gap>0.20R · +14 días\n2. walk_forward_optimize Optuna 200 trials\n   train 60d → test 20d → overfit_gap\n3. Deflated Sharpe gate DSR ≥ 0.95\n4. Si aprobado → deployed_params\n5. Rust recarga en <5 min\n6. Log MLflow + calibration_log"]

    B -->|"streams WS"| M
    M -->|"REST fire-and-forget"| S
    S -->|"DuckDB ATTACH postgres"| P
    P -->|"INSERT deployed_params"| S
    S -->|"GET deployed_params cada 5min"| M
```

---

## Capas del sistema

### Capa 1 — Datos en tiempo real (Binance)

**WebSocket streams** (via `exchange` crate, adaptador Binance):
| Stream | Frecuencia | Uso |
|--------|-----------|-----|
| Kline LinearPerps | Cada tick + cierre de barra | OHLCV, bar close trigger |
| Depth L2 | Cada actualización | OBI, spread, walls, microprice |
| AggTrade | Cada trade | CVD, delta, taker imbalance |

**REST fetches** (async tasks independientes):
| Endpoint | Intervalo | Uso |
|----------|----------|-----|
| `fapi/v1/premiumIndex` | 60s | Funding rate, mark price |
| `api/v3/ticker/price` | 30s | Spot price para basis |
| `fapi/v1/openInterest` | 5 min | OI para momentum y delta |

### Capa 2 — Procesamiento de señales (Rust)

Ver [reference/monitor.md](reference/monitor.md) para detalle del bar state y el pipeline de señales.

Ver [reference/strategy.md](reference/strategy.md) para router, scorer, detectores y paper account.

### Capa 3 — Persistencia (Supabase)

Ver [reference/supabase.md](reference/supabase.md) para el schema completo y queries útiles.

### Capa 4 — Calibración (Python)

Ver [reference/calibration.md](reference/calibration.md) para el pipeline completo.

---

## Flujo de una señal: de Binance a Supabase

```mermaid
flowchart TD
    A["Bar close Binance\nt = 0ms"]
    B["Kline is_closed=true llega al monitor\nt ≈ 10ms"]
    C["on_bar_close ejecuta\nVWAP 2ms · Régimen OLS 1ms\nVolume Profile ~5ms · Contexts 1ms\n6 detectores + scorer ~2ms\n──────────────\nTotal ≈ 11ms latencia"]
    D{"score ≥ min_score?"}
    E["ShadowSignal\nEmit JSON stdout\ntokio::spawn POST shadow_signals\nPaper account abre posición"]
    F["Wait / Blocked\nLog a rejected/blocked.jsonl"]
    G["Barras siguientes\nPaper account trackea MFE/MAE"]
    H{"stop / target / TTL\n/ invalidación?"}
    I["Cierra posición\nEmit JSON stdout\ntokio::spawn POST signal_outcomes"]
    J["Supabase recibe en <500ms\n(Railway → US East)"]

    A --> B --> C --> D
    D -->|sí| E --> G --> H -->|sí| I --> J
    D -->|no| F
    H -->|no| G
```

---

## Flujo de recalibración: de datos a nuevos parámetros

```mermaid
flowchart TD
    A["monitor.py\ncada 30 min"]
    B["DegradationMonitor.check()\n¿régimen cambió? regime_history\n¿gap > 0.20R? últimos 50 trades\n¿+14 días sin calibrar? calibration_log"]
    C{"should_recalibrate?"}
    D["query_signals(regime)\n≥ 50 trades requeridos"]
    E["walk_forward_optimize\ntrain 60d → Optuna 200 trials\ntest 20d → overfit_gap\nget_best_robust_params gap < 0.20R"]
    F["edge_is_real DSR\nDeflated Sharpe Ratio\nn_trials = 200"]
    G{"DSR ≥ 0.95?"}
    H["Rechaza\nmantiene params actuales"]
    I["Supabase\ncalibration_log INSERT\ndeployed_params UPDATE is_active=False\ndeployed_params INSERT nuevo activo"]
    J["MLflow\nlog params · métricas · CSV walk-forward"]
    K["Rust ConfigLoader\n≤ 5 min detecta stale\nGET deployed_params → nuevo StrategyConfig"]

    A --> B --> C
    C -->|no| A
    C -->|sí| D --> E --> F --> G
    G -->|no| H
    G -->|sí| I --> J --> K
```

---

## Componentes y archivos

```mermaid
graph LR
    subgraph monitor["crates/monitor/src/"]
        main["main.rs\nBarState · async tasks"]
        cfg_loader["config_loader.rs\nStrategyConfig desde Supabase"]
        sb_writer["supabase_writer.rs\nfire-and-forget writes"]
    end

    subgraph strategy["data/src/strategy/"]
        types["types.rs\nenums · contextos · señal · config"]
        router["router.rs\n6 detectores → señal ganadora"]
        scoring["scoring.rs\nscore 0-1 pesos + modulación"]
        paper["paper.rs\nposiciones · trades · PnL"]
        tracker["tracker.rs\noutcome tracker MFE/MAE"]
        logger["logger.rs\nJSONL signals/rejected/blocked"]
        subgraph detectors["detectors/"]
            gate["toxic_flow_gate.rs"]
            va["value_area_failed_auction.rs"]
            lvn["lvn_liquidity_vacuum_breakout.rs"]
            vwap["vwap_value_pullback_continuation.rs"]
            liq["liquidation_hunt.rs"]
            smd["smart_money_divergence.rs"]
            fe["funding_exhaustion_reversal.rs"]
        end
    end

    subgraph inst["data/src/institutional/"]
        itypes["types.rs\nLiqSnap · LsRatio · OiTrend · Funding"]
        liq_t["liquidation_tracker.rs"]
        ls_t["ls_ratio_tracker.rs"]
        oi_t["oi_tracker.rs"]
        fund_t["funding_tracker.rs"]
    end

    subgraph cal["calibration/"]
        monpy["monitor.py\n--once / --force / --status"]
        db["core/db.py\nDuckDB + Supabase attach"]
        deg["core/degradation_monitor.py"]
        wf["calibration/walk_forward.py\nOptuna walk-forward"]
        dsr["calibration/deflated_sharpe.py"]
        dep["calibration/param_deployer.py"]
        mlf["tracking/mlflow_tracker.py"]
    end

    main --> router
    router --> gate & va & lvn & vwap & liq & smd & fe
    router --> scoring
    main --> paper & tracker & logger
    main --> cfg_loader & sb_writer
    liq & smd & fe --> inst
    monpy --> db & deg & wf & dsr & dep & mlf
```

---

## Régimen de mercado

El sistema no opera igual en todos los regímenes. El régimen se calcula en cada bar close:

```rust
fn derive_regime(closes: &[f64], atr: f64) -> Regime {
    let slope = ols_slope(closes) / atr;  // pendiente normalizada por ATR
    
    match slope {
        s if s >  0.3  => Regime::TrendUp
        s if s < -0.3  => Regime::TrendDown
        s if s.abs() < 0.1 => Regime::Chop
        // + Compression, Expansion, Stress, Aftermath via volatility
    }
}
```

**Impacto en detección:**
| Régimen | Detectores activos preferentemente | Score modifier |
|---------|----------------------------------|----------------|
| TrendUp | VwapPullback (long), LvnBreakout (long) | ×1.15 si alineado |
| TrendDown | VwapPullback (short), ValueArea (short) | ×1.15 si alineado |
| Chop | FundingExhaustion, SmartMoney, ValueArea | ×0.70 para trend-following |
| Expansion | LiquidationHunt, LvnBreakout | ×1.15 si alineado |
| Stress/Aftermath | Todos bloqueados o reducidos | múltiples gates activos |

**Impacto en calibración:**  
Los parámetros en `deployed_params` se guardan **por régimen** — el sistema tiene parámetros distintos para TrendUp vs. Chop vs. Expansion. Cuando el régimen cambia, carga los params calibrados para ese contexto.
