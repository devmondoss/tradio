use std::collections::VecDeque;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum OBType {
    /// Última vela bajista antes de un impulso alcista fuerte.
    Bullish,
    /// Última vela alcista antes de un impulso bajista fuerte.
    Bearish,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum OBStatus {
    Active,
    PartiallyMitigated,
    /// El precio retornó y cubrió completamente la zona — ya no relevante.
    Mitigated,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderBlock {
    pub ob_type: OBType,
    pub high: f64,
    pub low: f64,
    pub timestamp_ms: i64,
    pub status: OBStatus,
}

/// Snapshot de Order Blocks activos relativo al precio actual.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct OrderBlockContext {
    /// OBs alcistas activos (potenciales soportes debajo del precio actual).
    pub bullish_obs: Vec<OrderBlock>,
    /// OBs bajistas activos (potenciales resistencias sobre el precio actual).
    pub bearish_obs: Vec<OrderBlock>,
    /// OB alcista más cercano por debajo del precio.
    pub nearest_bullish: Option<OrderBlock>,
    /// OB bajista más cercano por encima del precio.
    pub nearest_bearish: Option<OrderBlock>,
}

#[derive(Clone, Copy)]
struct Bar {
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    timestamp_ms: i64,
}

/// Detecta Order Blocks a partir de una ventana deslizante de velas OHLC.
/// Se actualiza con `push_bar`; llama a `snapshot(price)` para obtener el contexto actual.
pub struct OrderBlockDetector {
    bars: VecDeque<Bar>,
    max_bars: usize,
    /// Barras consecutivas de continuación para confirmar el impulso.
    impulse_bars: usize,
    /// Máximo de OBs activos por lado a retener.
    max_obs: usize,
}

impl OrderBlockDetector {
    pub fn new(max_bars: usize) -> Self {
        Self {
            bars: VecDeque::with_capacity(max_bars),
            max_bars,
            impulse_bars: 2,
            max_obs: 5,
        }
    }

    pub fn push_bar(
        &mut self,
        open: f64,
        high: f64,
        low: f64,
        close: f64,
        timestamp_ms: i64,
    ) {
        if self.bars.len() >= self.max_bars {
            self.bars.pop_front();
        }
        self.bars.push_back(Bar { open, high, low, close, timestamp_ms });
    }

    pub fn snapshot(&self, current_price: f64) -> OrderBlockContext {
        let bars: Vec<Bar> = self.bars.iter().copied().collect();
        let n = bars.len();

        if n < self.impulse_bars + 2 {
            return OrderBlockContext::default();
        }

        let mut bullish_obs: Vec<OrderBlock> = Vec::new();
        let mut bearish_obs: Vec<OrderBlock> = Vec::new();

        for i in 0..(n - self.impulse_bars - 1) {
            let bar = bars[i];
            let is_bearish = bar.close < bar.open;
            let is_bullish = bar.close > bar.open;

            // Bullish OB: última vela bajista antes de impulso alcista
            if is_bearish {
                let impulse_up = (1..=self.impulse_bars)
                    .all(|j| bars[i + j].close > bars[i + j].open);
                let breaks_high = bars[i + self.impulse_bars].high > bar.high;

                if impulse_up && breaks_high {
                    let status = ob_status_bullish(bar.high, bar.low, current_price);
                    if !matches!(status, OBStatus::Mitigated) {
                        bullish_obs.push(OrderBlock {
                            ob_type: OBType::Bullish,
                            high: bar.high,
                            low: bar.low,
                            timestamp_ms: bar.timestamp_ms,
                            status,
                        });
                    }
                }
            }

            // Bearish OB: última vela alcista antes de impulso bajista
            if is_bullish {
                let impulse_down = (1..=self.impulse_bars)
                    .all(|j| bars[i + j].close < bars[i + j].open);
                let breaks_low = bars[i + self.impulse_bars].low < bar.low;

                if impulse_down && breaks_low {
                    let status = ob_status_bearish(bar.high, bar.low, current_price);
                    if !matches!(status, OBStatus::Mitigated) {
                        bearish_obs.push(OrderBlock {
                            ob_type: OBType::Bearish,
                            high: bar.high,
                            low: bar.low,
                            timestamp_ms: bar.timestamp_ms,
                            status,
                        });
                    }
                }
            }
        }

        // Más recientes primero, limitado a max_obs
        let bullish_obs: Vec<_> = bullish_obs.into_iter().rev().take(self.max_obs).collect();
        let bearish_obs: Vec<_> = bearish_obs.into_iter().rev().take(self.max_obs).collect();

        let nearest_bullish = bullish_obs
            .iter()
            .filter(|ob| ob.high < current_price)
            .max_by(|a, b| a.high.partial_cmp(&b.high).unwrap_or(std::cmp::Ordering::Equal))
            .cloned();

        let nearest_bearish = bearish_obs
            .iter()
            .filter(|ob| ob.low > current_price)
            .min_by(|a, b| a.low.partial_cmp(&b.low).unwrap_or(std::cmp::Ordering::Equal))
            .cloned();

        OrderBlockContext {
            bullish_obs,
            bearish_obs,
            nearest_bullish,
            nearest_bearish,
        }
    }
}

fn ob_status_bullish(high: f64, low: f64, price: f64) -> OBStatus {
    if price < low {
        OBStatus::Mitigated
    } else if price <= high {
        OBStatus::PartiallyMitigated
    } else {
        OBStatus::Active
    }
}

fn ob_status_bearish(high: f64, low: f64, price: f64) -> OBStatus {
    if price > high {
        OBStatus::Mitigated
    } else if price >= low {
        OBStatus::PartiallyMitigated
    } else {
        OBStatus::Active
    }
}
