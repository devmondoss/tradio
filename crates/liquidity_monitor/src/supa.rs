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

    pub async fn write_events(&self, events: &[crate::book::BookEvent]) {
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
            "price":      e.price,
        })).collect();
        self.insert("liquidity_paper_events", json!(rows)).await;
    }

    // ── Closed trades ───────────────────────────────────────────────────────

    pub async fn write_trades(&self, trades: &[crate::book::ClosedTrade]) {
        if trades.is_empty() { return; }
        let rows: Vec<Value> = trades.iter().map(|t| json!({
            "symbol":     self.symbol,
            "tf":         self.tf,
            "kind":       t.kind,
            "side":       t.side,
            "vol_regime": t.vol_regime,
            "regime":     t.regime,
            "gestion":    t.gestion,
            "entry":      t.entry,
            "stop":       t.stop,
            "target":     t.target,
            "exit_price": t.exit_price,
            "result_r":   t.result_r,
            "win":        t.win,
            "reason":     t.reason,
            "system":     t.system,
            "opened_at":  Self::iso(t.opened_at),
            "closed_at":  Self::iso(t.closed_at),
        })).collect();
        self.insert("liquidity_paper_trades", json!(rows)).await;
    }

    // ── Snapshot por barra ──────────────────────────────────────────────────

    pub async fn write_snapshot(&self, book: &crate::book::PaperBook, bar_close: i64, close_px: f64) {
        use crate::levels::VolRegime;
        let snap = json!({
            "symbol":          self.symbol,
            "tf":              self.tf,
            "system":          book.system,
            "at":              Self::iso(bar_close),
            "placed_high":     book.placed[0],
            "filled_high":     book.filled[0],
            "fill_ratio_high": book.fill_ratio(VolRegime::High),
            "placed_low":      book.placed[1],
            "filled_low":      book.filled[1],
            "fill_ratio_low":  book.fill_ratio(VolRegime::Low),
            "close_px":        close_px,
        });
        self.insert("liquidity_paper_snapshots", json!([snap])).await;
    }

    // ── Footprint bar (persistencia para sobrevivir reinicios) ──────────────

    pub async fn write_footprint(&self, bar: &crate::levels::ClosedBar) {
        if !bar.fp_real { return; }  // no persistir barras OHLCV approximadas
        let mut prices: Vec<f64> = Vec::new();
        let mut buy:    Vec<f64> = Vec::new();
        let mut sell:   Vec<f64> = Vec::new();

        let mut bins: Vec<(u32, (f64, f64))> = bar.fp.iter().map(|(&k, &v)| (k, v)).collect();
        bins.sort_by_key(|&(k, _)| k);
        for (bin, (b, s)) in bins {
            prices.push((bin as f64) * crate::levels::BIN);
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
                    let bin = (pv / crate::levels::BIN).round() as u32;
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

    // ── Fill ratio view (diagnóstico) ───────────────────────────────────────

    pub async fn fill_ratio_snapshot(&self) -> Option<String> {
        let resp = self.client
            .get(format!("{}/rest/v1/liquidity_paper_fill_ratio", self.url))
            .header("apikey", &self.key)
            .header("Authorization", format!("Bearer {}", self.key))
            .send().await.ok()?;
        let rows: Vec<Value> = resp.json().await.ok()?;
        Some(serde_json::to_string_pretty(&rows).unwrap_or_default())
    }

    // ── Helpers públicos ────────────────────────────────────────────────────

    pub fn symbol_ref(&self) -> &str { &self.symbol }
    pub fn tf_ref(&self)     -> &str { &self.tf }

    pub fn iso_ms(ms: i64) -> String { Self::iso(ms) }

    pub async fn raw_insert(&self, table: &str, rows: Value) {
        self.insert(table, rows).await;
    }
}
