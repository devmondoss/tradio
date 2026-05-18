use std::collections::HashMap;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum SpoofSide {
    Bid,
    Ask,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SpoofEvent {
    pub side: SpoofSide,
    pub price: f64,
    pub size: f64,
    pub detected_at_ms: i64,
}

/// Contexto de spoof: resultado del último ciclo de detección.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SpoofContext {
    pub spoof_detected: bool,
    pub spoof_side: Option<SpoofSide>,
    pub last_event: Option<SpoofEvent>,
}

/// Órdenes grandes rastreadas: (price, size, first_seen_ms)
type TrackedOrder = (f64, f64, i64);

/// Detecta spoofing L2 observando órdenes grandes que aparecen cerca del precio
/// y desaparecen antes de ejecutarse (< 500ms).
///
/// Requiere snapshots del order book (bids/asks como slices de (price, size))
/// llamando a `update` en cada tick de L2.
pub struct SpoofDetector {
    /// Órdenes grandes siendo rastreadas: key = price.to_bits()
    tracked: HashMap<u64, TrackedOrder>,
    /// Tamaño promedio del lado bid (rolling)
    avg_bid_size: f64,
    /// Tamaño promedio del lado ask (rolling)
    avg_ask_size: f64,
    /// Cuántas veces avg_size debe superar una orden para considerarse "grande"
    large_multiplier: f64,
    /// Si la orden desaparece dentro de este tiempo → probable spoof
    spoof_ttl_ms: i64,
    /// Proximidad al precio actual para rastrear (fracción del precio)
    proximity_frac: f64,
    /// Tiempo de vida del flag spoof_detected antes de decaer
    decay_ms: i64,
    last_ctx: SpoofContext,
}

impl SpoofDetector {
    pub fn new() -> Self {
        Self {
            tracked: HashMap::new(),
            avg_bid_size: 0.0,
            avg_ask_size: 0.0,
            large_multiplier: 5.0,
            spoof_ttl_ms: 500,
            proximity_frac: 0.0005, // 0.05% del precio
            decay_ms: 2_000,
            last_ctx: SpoofContext::default(),
        }
    }

    /// Actualiza con el snapshot actual del order book.
    /// `book_bids` y `book_asks`: slices de (price, size), mejor nivel primero.
    pub fn update(
        &mut self,
        book_bids: &[(f64, f64)],
        book_asks: &[(f64, f64)],
        current_price: f64,
        now_ms: i64,
    ) {
        self.update_averages(book_bids, book_asks);

        let proximity = current_price * self.proximity_frac;
        let mut detected: Option<SpoofEvent> = None;

        // Evaluar órdenes ya rastreadas
        let mut to_remove: Vec<u64> = Vec::new();
        for (&key, &(price, size, first_seen)) in &self.tracked {
            let age = now_ms - first_seen;
            let near = (price - current_price).abs() <= proximity;

            let still_present = book_bids.iter().any(|&(p, _)| (p - price).abs() < 0.01)
                || book_asks.iter().any(|&(p, _)| (p - price).abs() < 0.01);

            if near && !still_present && age < self.spoof_ttl_ms {
                // Orden grande cercana desapareció rápido → spoof
                let side = if price < current_price { SpoofSide::Bid } else { SpoofSide::Ask };
                detected = Some(SpoofEvent { side, price, size, detected_at_ms: now_ms });
                to_remove.push(key);
            } else if age > self.spoof_ttl_ms * 6 {
                to_remove.push(key);
            }
        }
        for k in to_remove {
            self.tracked.remove(&k);
        }

        // Rastrear nuevas órdenes grandes cercanas
        let all_levels = book_bids.iter().chain(book_asks.iter());
        for &(price, size) in all_levels {
            if (price - current_price).abs() > proximity {
                continue;
            }
            let avg = if price < current_price { self.avg_bid_size } else { self.avg_ask_size };
            if avg > 0.0 && size > avg * self.large_multiplier {
                let key = price.to_bits();
                self.tracked.entry(key).or_insert((price, size, now_ms));
            }
        }

        // Decaer flag de detección anterior
        if self.last_ctx.spoof_detected {
            if let Some(ref last) = self.last_ctx.last_event {
                if now_ms - last.detected_at_ms > self.decay_ms {
                    self.last_ctx = SpoofContext::default();
                }
            }
        }

        if let Some(event) = detected {
            let side = event.side;
            self.last_ctx = SpoofContext {
                spoof_detected: true,
                spoof_side: Some(side),
                last_event: Some(event),
            };
        }
    }

    pub fn context(&self) -> &SpoofContext {
        &self.last_ctx
    }

    fn update_averages(&mut self, bids: &[(f64, f64)], asks: &[(f64, f64)]) {
        if !bids.is_empty() {
            self.avg_bid_size = bids.iter().map(|(_, s)| s).sum::<f64>() / bids.len() as f64;
        }
        if !asks.is_empty() {
            self.avg_ask_size = asks.iter().map(|(_, s)| s).sum::<f64>() / asks.len() as f64;
        }
    }
}

impl Default for SpoofDetector {
    fn default() -> Self {
        Self::new()
    }
}
