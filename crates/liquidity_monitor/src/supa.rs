//! Cliente Supabase REST (fire-and-forget async).
//! Escribe a las mismas tablas que paper_liquidity.py para mantener compatibilidad.

use reqwest::Client;
use serde_json::{json, Value};

pub struct SupaClient {
    client:  Client,
    url:     String,
    key:     String,
    symbol:  String,
    tf:      String,
}

impl SupaClient {
    pub fn new(url: &str, key: &str, symbol: &str, tf: &str) -> Option<Self> {
        if url.is_empty() || key.is_empty() { return None; }
        Some(Self {
            client: Client::new(),
            url: url.trim_end_matches('/').to_string(),
            key: key.to_string(),
            symbol: symbol.to_string(),
            tf: tf.to_string(),
        })
    }

    async fn upsert(&self, table: &str, rows: Value) {
        let _ = self.client
            .post(format!("{}/rest/v1/{}", self.url, table))
            .header("apikey", &self.key)
            .header("Authorization", format!("Bearer {}", self.key))
            .header("Content-Type", "application/json")
            .header("Prefer", "resolution=merge-duplicates,return=minimal")
            .json(&rows)
            .send()
            .await;
    }

    async fn insert(&self, table: &str, rows: Value) {
        let _ = self.client
            .post(format!("{}/rest/v1/{}", self.url, table))
            .header("apikey", &self.key)
            .header("Authorization", format!("Bearer {}", self.key))
            .header("Content-Type", "application/json")
            .header("Prefer", "return=minimal")
            .json(&rows)
            .send()
            .await;
    }

    /// Trade REAL del ejecutor (demo/live), registro RICO para análisis/backtest post-trade.
    #[allow(clippy::too_many_arguments)]
    pub async fn write_exec_trade(&self, lv: &crate::levels::Level, entry: f64, exit: f64,
                                  pnl: f64, r: f64, reason: &str, qty: f64, mfe_r: f64, mae_r: f64,
                                  fee_r: f64, ttf_s: i64, bar_ts: i64, fill_ts: i64, closed_ts: i64) {
        let rd = |x: f64| (x*10000.0).round()/10000.0;
        let row = json!({
            "symbol": self.symbol, "tf": self.tf,
            "kind": lv.kind, "side": lv.side.as_str(), "gestion": lv.gestion.as_str(),
            "vol_regime": lv.vol_regime.as_str(), "regime": lv.regime.as_str(),
            "level": lv.price, "entry": entry, "stop": lv.stop, "tp1": lv.tp1, "target": lv.tp,
            "exit_price": exit, "reason": reason, "qty": qty,
            "pnl_usdt": rd(pnl), "result_r": rd(r), "win": r > 0.0,
            "mfe_r": rd(mfe_r), "mae_r": rd(mae_r), "fee_r": rd(fee_r),
            "time_to_fill_s": ttf_s, "atr": lv.atr, "bar_ts": bar_ts,
            "opened_at": Self::iso(fill_ts), "closed_at": Self::iso(closed_ts),
        });
        self.insert("liquidity_exec_trades", json!([row])).await;
    }

    /// Snapshot de fill ratio real (placed vs filled acumulados).
    pub async fn write_exec_snapshot(&self, placed_cum: u64, filled_cum: u64, open_pos: u32) {
        let ratio = if placed_cum > 0 { filled_cum as f64 / placed_cum as f64 } else { 0.0 };
        let row = json!({ "symbol": self.symbol, "placed_cum": placed_cum,
            "filled_cum": filled_cum, "fill_ratio": ratio, "open_pos": open_pos });
        self.insert("liquidity_exec_snapshots", json!([row])).await;
    }

    // ── Persistencia de la posición abierta del ejecutor (sobrevive redeploys) ──

    /// Guarda el estado de la posición abierta (borra el anterior del símbolo + inserta).
    #[allow(clippy::too_many_arguments)]
    pub async fn save_exec_pos(&self, lv: &crate::levels::Level, entry: f64, fill_ts: i64,
                               ttf_s: i64, exits_armed: bool, seen_hi: f64, seen_lo: f64, bar_ts: i64) {
        let _ = self.client.delete(format!("{}/rest/v1/liquidity_exec_open_pos", self.url))
            .header("apikey", &self.key).header("Authorization", format!("Bearer {}", self.key))
            .query(&[("symbol", format!("eq.{}", self.symbol))]).send().await;
        let row = json!({
            "symbol": self.symbol, "tf": self.tf,
            "side": lv.side.as_str(), "kind": lv.kind, "gestion": lv.gestion.as_str(),
            "vol_regime": lv.vol_regime.as_str(), "regime": lv.regime.as_str(), "fp_source": lv.fp_source,
            "price": lv.price, "stop": lv.stop, "tp1": lv.tp1, "tp": lv.tp,
            "atr": lv.atr, "atr_median": lv.atr_median, "take_partial": lv.take_partial,
            "entry": entry, "fill_ts": fill_ts, "ttf_s": ttf_s, "exits_armed": exits_armed,
            "seen_hi": seen_hi, "seen_lo": seen_lo, "bar_ts": bar_ts,
        });
        self.insert("liquidity_exec_open_pos", json!([row])).await;
    }

    pub async fn clear_exec_pos(&self) {
        let _ = self.client.delete(format!("{}/rest/v1/liquidity_exec_open_pos", self.url))
            .header("apikey", &self.key).header("Authorization", format!("Bearer {}", self.key))
            .query(&[("symbol", format!("eq.{}", self.symbol))]).send().await;
    }

    /// Restaura el contexto: (Level, entry, fill_ts, ttf_s, exits_armed, seen_hi, seen_lo, bar_ts) o None.
    pub async fn load_exec_pos(&self) -> Option<(crate::levels::Level, f64, i64, i64, bool, f64, f64, i64)> {
        use crate::levels::{Level, Side, VolRegime, MarketRegime, Gestion, kind_from_str, fp_source_from_str};
        let resp = self.client.get(format!("{}/rest/v1/liquidity_exec_open_pos", self.url))
            .header("apikey", &self.key).header("Authorization", format!("Bearer {}", self.key))
            .query(&[("symbol", format!("eq.{}", self.symbol)), ("select", "*".into())])
            .send().await.ok()?;
        let rows: Vec<Value> = resp.json().await.ok()?;
        let r = rows.into_iter().next()?;
        let lv = Level {
            side:         Side::from_str(r["side"].as_str()?),
            kind:         kind_from_str(r["kind"].as_str()?),
            price:        r["price"].as_f64()?,
            stop:         r["stop"].as_f64()?,
            tp1:          r["tp1"].as_f64(),
            tp:           r["tp"].as_f64()?,
            vol_regime:   VolRegime::from_str(r["vol_regime"].as_str()?),
            regime:       MarketRegime::from_str(r["regime"].as_str()?),
            gestion:      Gestion::from_str(r["gestion"].as_str()?),
            atr:          r["atr"].as_f64()?,
            atr_median:   r["atr_median"].as_f64().unwrap_or(0.0),
            take_partial: r["take_partial"].as_bool().unwrap_or(false),
            fp_source:    fp_source_from_str(r["fp_source"].as_str()?),
        };
        let entry = r["entry"].as_f64()?;
        Some((lv, entry, r["fill_ts"].as_i64()?,
              r["ttf_s"].as_i64().unwrap_or(0), r["exits_armed"].as_bool().unwrap_or(false),
              r["seen_hi"].as_f64().unwrap_or(entry), r["seen_lo"].as_f64().unwrap_or(entry),
              r["bar_ts"].as_i64().unwrap_or(0)))
    }

    fn iso(ms: i64) -> String {
        let secs = ms / 1000;
        let millis = ms % 1000;
        // Formato ISO8601 simplificado (Supabase lo acepta)
        use std::time::{UNIX_EPOCH, Duration};
        let d = UNIX_EPOCH + Duration::from_secs(secs as u64);
        let dt: chrono::DateTime<chrono::Utc> =
            chrono::DateTime::from(d) + chrono::Duration::milliseconds(millis);
        dt.to_rfc3339()
    }

    // ── Events (place/fill) ─────────────────────────────────────────────────

    pub async fn write_events(&self, events: &[crate::book::BookEvent], system: &str) {
        if events.is_empty() { return; }
        let rows: Vec<Value> = events.iter().map(|e| json!({
            "at":         Self::iso(e.at),
            "symbol":     self.symbol,
            "tf":         self.tf,
            "event_type": e.event_type,
            "kind":       e.kind,
            "side":       e.side,
            "vol_regime": e.vol_regime,
            "regime":     e.regime,
            "gestion":    e.gestion,
            "system":     system,
            "price":      e.price,
            "bar_delta":  (e.bar_delta * 10000.0).round() / 10000.0,
        })).collect();
        self.insert("liquidity_paper_events", json!(rows)).await;
    }

    // ── Closed trades ───────────────────────────────────────────────────────

    pub async fn write_trades(&self, trades: &[crate::book::ClosedTrade]) {
        if trades.is_empty() { return; }
        let rows: Vec<Value> = trades.iter().map(|t| json!({
            "symbol":              self.symbol,
            "tf":                  self.tf,
            "kind":                t.kind,
            "side":                t.side,
            "vol_regime":          t.vol_regime,
            "regime":              t.regime,
            "gestion":             t.gestion,
            "entry":               t.entry,
            "stop":                t.stop,
            "target":              t.target,
            "exit_price":          t.exit_price,
            "result_r":            t.result_r,
            "win":                 t.win,
            "reason":              t.reason,
            "system":              t.system,
            "opened_at":           Self::iso(t.opened_at),
            "closed_at":           Self::iso(t.closed_at),
            "bar_delta_at_fill":   (t.bar_delta_at_fill * 10000.0).round() / 10000.0,
            "scale2_filled":       t.scale2_filled,
            "effective_entry":     (t.effective_entry * 100.0).round() / 100.0,
            "size_mult":           t.size_mult,
            // contexto completo (auto-explica y permite backtestear el trade)
            "fp_source":           t.fp_source,
            "tp1":                 t.tp1,
            "atr":                 (t.atr * 100.0).round() / 100.0,
            "atr_median":          (t.atr_median * 100.0).round() / 100.0,
            "take_partial":        t.take_partial,
            "placed_ts":           t.placed_ts,
            "time_to_fill_s":      ((t.opened_at - t.placed_ts) as f64 / 1000.0).round(),
            "filled1":             t.filled1,
            "realized_r":          t.realized_r,
            "fee_r":               t.fee_r,
            "mfe_r":               t.mfe_r,
            "mae_r":               t.mae_r,
            "bar_delta_at_exit":   (t.bar_delta_at_exit * 10000.0).round() / 10000.0,
            "filter_version":      "v2_h1_ifvg",
        })).collect();
        // Prefer: resolution=ignore-duplicates evita doble-write si el kline WS llega 2 veces
        let _ = self.client
            .post(format!("{}/rest/v1/liquidity_paper_trades", self.url))
            .header("apikey", &self.key)
            .header("Authorization", format!("Bearer {}", self.key))
            .header("Content-Type", "application/json")
            .header("Prefer", "resolution=ignore-duplicates,return=minimal")
            .json(&json!(rows))
            .send()
            .await;
    }

    // El snapshot por barra se escribe inline en main.rs (raw_insert) con los
    // contadores ya copiados del book, evitando mover el &book a la task async.

    // ── Footprint bar (persistencia para sobrevivir reinicios) ──────────────

    pub async fn write_footprint(&self, bar: &crate::levels::ClosedBar) {
        if !bar.fp_real { return; }  // no persistir barras OHLCV approximadas
        let mut prices: Vec<f64> = Vec::new();
        let mut buy:    Vec<f64> = Vec::new();
        let mut sell:   Vec<f64> = Vec::new();

        let mut bins: Vec<(u32, (f64, f64))> = bar.fp.iter().map(|(&k, &v)| (k, v)).collect();
        bins.sort_by_key(|&(k, _)| k);
        for (bin, (b, s)) in bins {
            prices.push((bin as f64) * crate::levels::bin());
            buy.push((b * 10000.0).round() / 10000.0);
            sell.push((s * 10000.0).round() / 10000.0);
        }

        let row = json!({
            "ts_ms":  bar.ts_ms,
            "symbol": self.symbol,
            "tf":     self.tf,
            "poc":    (bar.poc * 100.0).round() / 100.0,
            "delta":  ((buy.iter().sum::<f64>() - sell.iter().sum::<f64>()) * 10000.0).round() / 10000.0,
            "vol":    ((buy.iter().sum::<f64>() + sell.iter().sum::<f64>()) * 10000.0).round() / 10000.0,
            "prices": prices,
            "buy":    buy,
            "sell":   sell,
        });
        self.upsert("liquidity_paper_footprint", json!([row])).await;
    }

    // ── Restore footprint bars al arrancar ──────────────────────────────────

    pub async fn load_footprint(&self, limit: usize) -> Vec<crate::levels::ClosedBar> {
        use std::collections::HashMap;
        let resp = self.client
            .get(format!("{}/rest/v1/liquidity_paper_footprint", self.url))
            .header("apikey", &self.key)
            .header("Authorization", format!("Bearer {}", self.key))
            .query(&[
                ("symbol", format!("eq.{}", self.symbol)),
                ("tf",     format!("eq.{}", self.tf)),
                ("select", "*".into()),
                ("order",  "ts_ms.desc".into()),
                ("limit",  limit.to_string()),
            ])
            .send().await;

        let rows: Vec<Value> = match resp {
            Ok(r) => r.json().await.unwrap_or_default(),
            Err(_) => return vec![],
        };

        let mut bars: Vec<crate::levels::ClosedBar> = rows.iter().rev().filter_map(|row| {
            let ts_ms = row["ts_ms"].as_i64()?;
            let poc   = row["poc"].as_f64()?;
            let prices = row["prices"].as_array()?;
            let buy    = row["buy"].as_array()?;
            let sell   = row["sell"].as_array()?;

            let mut fp: HashMap<u32, (f64, f64)> = HashMap::new();
            for ((p, b), s) in prices.iter().zip(buy.iter()).zip(sell.iter()) {
                if let (Some(pv), Some(bv), Some(sv)) = (p.as_f64(), b.as_f64(), s.as_f64()) {
                    let bin = (pv / crate::levels::bin()).round() as u32;
                    fp.insert(bin, (bv, sv));
                }
            }

            Some(crate::levels::ClosedBar {
                ts_ms,
                open:    0.0, high: poc, low: poc, close: poc, volume: 0.0,
                day_id:  ts_ms / 86_400_000,
                fp,
                poc,
                fp_real: true,
            })
        }).collect();

        bars
    }

    // ── Open positions (persistencia para sobrevivir redeploys) ─────────────

    /// Reemplaza el set completo de posiciones abiertas de (symbol, tf, system):
    /// borra las viejas e inserta las vivas. open_pos es chico, así que es barato.
    pub async fn save_open_positions(&self, system: &str, positions: &[crate::book::OpenPos]) {
        // 1. Borrar las existentes de este book
        let _ = self.client
            .delete(format!("{}/rest/v1/liquidity_paper_open_pos", self.url))
            .header("apikey", &self.key)
            .header("Authorization", format!("Bearer {}", self.key))
            .query(&[
                ("symbol", format!("eq.{}", self.symbol)),
                ("tf",     format!("eq.{}", self.tf)),
                ("system", format!("eq.{}", system)),
            ])
            .send().await;

        if positions.is_empty() { return; }

        // 2. Insertar las vivas
        let rows: Vec<Value> = positions.iter().map(|p| {
            let lv = &p.level;
            json!({
                "symbol":            self.symbol,
                "tf":                self.tf,
                "system":            system,
                "side":              lv.side.as_str(),
                "kind":              lv.kind,
                "price":             lv.price,
                "lvl_stop":          lv.stop,
                "tp1":               lv.tp1,
                "tp":                lv.tp,
                "vol_regime":        lv.vol_regime.as_str(),
                "regime":            lv.regime.as_str(),
                "gestion":           lv.gestion.as_str(),
                "atr":               lv.atr,
                "atr_median":        lv.atr_median,
                "take_partial":      lv.take_partial,
                "fp_source":         lv.fp_source,
                "entry":             p.entry,
                "fill_ts":           p.fill_ts,
                "bar_delta_at_fill": p.bar_delta_at_fill,
                "cur_stop":          p.cur_stop,
                "realized":          p.realized,
                "rem":               p.rem,
                "filled1":           p.filled1,
                "best_price":        p.best_price,
                "trail_stop":        p.trail_stop,
                "scale2_price":      p.scale2_price,
                "scale2_filled":     p.scale2_filled,
                "effective_entry":   p.effective_entry,
            })
        }).collect();
        self.insert("liquidity_paper_open_pos", json!(rows)).await;
    }

    /// Restaurar posiciones abiertas de (symbol, tf, system) al arrancar.
    pub async fn load_open_positions(&self, system: &str) -> Vec<crate::book::OpenPos> {
        use crate::levels::{Level, Side, VolRegime, MarketRegime, Gestion,
                            kind_from_str, fp_source_from_str};
        let resp = self.client
            .get(format!("{}/rest/v1/liquidity_paper_open_pos", self.url))
            .header("apikey", &self.key)
            .header("Authorization", format!("Bearer {}", self.key))
            .query(&[
                ("symbol", format!("eq.{}", self.symbol)),
                ("tf",     format!("eq.{}", self.tf)),
                ("system", format!("eq.{}", system)),
                ("select", "*".into()),
            ])
            .send().await;

        let rows: Vec<Value> = match resp {
            Ok(r) => r.json().await.unwrap_or_default(),
            Err(_) => return vec![],
        };

        rows.iter().filter_map(|r| {
            let level = Level {
                side:         Side::from_str(r["side"].as_str()?),
                kind:         kind_from_str(r["kind"].as_str()?),
                price:        r["price"].as_f64()?,
                stop:         r["lvl_stop"].as_f64()?,
                tp1:          r["tp1"].as_f64(),               // None si null
                tp:           r["tp"].as_f64()?,
                vol_regime:   VolRegime::from_str(r["vol_regime"].as_str()?),
                regime:       MarketRegime::from_str(r["regime"].as_str()?),
                gestion:      Gestion::from_str(r["gestion"].as_str()?),
                atr:          r["atr"].as_f64()?,
                atr_median:   r["atr_median"].as_f64().unwrap_or(0.0),
                // Derivar de tp1 en vez de leer el flag persistido: take_partial ≡ tp1.is_some()
                // por diseño (levels.rs), y así una posición restaurada tras un restart no pierde
                // el parcial aunque el flag guardado quedara desincronizado.
                take_partial: r["tp1"].as_f64().is_some(),
                fp_source:    fp_source_from_str(r["fp_source"].as_str()?),
            };
            let entry_px = r["entry"].as_f64()?;
            Some(crate::book::OpenPos {
                level,
                entry:             entry_px,
                fill_ts:           r["fill_ts"].as_i64()?,
                placed_ts:         r["fill_ts"].as_i64().unwrap_or(0),
                bar_delta_at_fill: r["bar_delta_at_fill"].as_f64().unwrap_or(0.0),
                seen_hi:           r["best_price"].as_f64().unwrap_or(entry_px),
                seen_lo:           r["best_price"].as_f64().unwrap_or(entry_px),
                cur_stop:          r["cur_stop"].as_f64()?,
                realized:          r["realized"].as_f64().unwrap_or(0.0),
                rem:               r["rem"].as_f64().unwrap_or(1.0),
                filled1:           r["filled1"].as_bool().unwrap_or(false),
                best_price:        r["best_price"].as_f64()?,
                trail_stop:        r["trail_stop"].as_f64()?,
                scale2_price:      r["scale2_price"].as_f64().unwrap_or(0.0),
                scale2_filled:     r["scale2_filled"].as_bool().unwrap_or(false),
                effective_entry:   r["effective_entry"].as_f64()?,
            })
        }).collect()
    }

    // ── Fill ratio view (diagnóstico) ───────────────────────────────────────
    // Lee el fill ratio REAL acumulado desde la vista de eventos (inmune a
    // restarts, a diferencia de los contadores en-memoria del status_line).
    #[allow(dead_code)]
    pub async fn fill_ratio_snapshot(&self) -> Option<String> {
        let resp = self.client
            .get(format!("{}/rest/v1/liquidity_paper_fill_ratio", self.url))
            .header("apikey", &self.key)
            .header("Authorization", format!("Bearer {}", self.key))
            .send().await.ok()?;
        let rows: Vec<Value> = resp.json().await.ok()?;
        Some(serde_json::to_string_pretty(&rows).unwrap_or_default())
    }

    // ── Exec order events (place / fill / cancel) ──────────────────────────

    /// Ciclo de vida de órdenes reales: place → fill → cancel.
    /// event_type: "place" | "fill" | "cancel_fill" | "cancel_redeploy"
    pub async fn write_exec_order_event(
        &self,
        event_type: &str,
        order_id: Option<&str>,
        lv: Option<&crate::levels::Level>,
        bar_ts: i64,
        reason: Option<&str>,
        n_cancelled: Option<u32>,
        fill_price: Option<f64>,
    ) {
        let row = json!({
            "symbol":      self.symbol,
            "tf":          self.tf,
            "event_type":  event_type,
            "order_id":    order_id,
            "side":        lv.map(|l| l.side.as_str()),
            "kind":        lv.map(|l| l.kind),
            "price":       lv.map(|l| l.price),
            "stop":        lv.map(|l| l.stop),
            "tp":          lv.map(|l| l.tp),
            "gestion":     lv.map(|l| l.gestion.as_str()),
            "regime":      lv.map(|l| l.regime.as_str()),
            "vol_regime":  lv.map(|l| l.vol_regime.as_str()),
            "bar_ts":      bar_ts,
            "reason":      reason,
            "n_cancelled": n_cancelled,
            "fill_price":  fill_price,
        });
        self.insert("liquidity_exec_order_events", json!([row])).await;
    }

    // ── Helpers públicos ────────────────────────────────────────────────────

    pub fn symbol_ref(&self) -> &str { &self.symbol }
    pub fn tf_ref(&self)     -> &str { &self.tf }

    pub fn iso_ms(ms: i64) -> String { Self::iso(ms) }

    pub async fn raw_insert(&self, table: &str, rows: Value) {
        self.insert(table, rows).await;
    }
}
