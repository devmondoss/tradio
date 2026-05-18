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
    /// OB válido, precio no ha vuelto a la zona.
    Active,
    /// Precio tocó la zona pero no cerró vela dentro — sigue activo.
    Tested,
    /// Precio cerró vela dentro del OB (cruzó el 50% del rango) — considerado mitigado.
    Mitigated,
    /// Precio cerró completamente al otro lado del OB — invalidado.
    Invalidated,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderBlock {
    pub ob_type: OBType,
    pub high: f64,
    pub low: f64,
    /// Punto medio del OB — nivel de mitigación (50% del rango).
    pub mid: f64,
    pub timestamp_ms: i64,
    pub status: OBStatus,
    /// Ratio volumen_ob / volumen_promedio_20_velas. 1.0 si sin datos de volumen.
    pub volume_ratio: f64,
    /// Número de barras cuyo high (bullish OB) o low (bearish OB) fueron superadas por el impulso.
    pub swings_broken: u32,
}

impl OrderBlock {
    /// Actualiza el estado del OB basándose en el cierre de la vela (no intrabar).
    /// El estado se determina por cierre, no por precio intrabar, para reducir ruido.
    pub fn update_status(&mut self, close: f64, low: f64, high: f64) {
        match self.ob_type {
            OBType::Bullish => {
                if close < self.low {
                    self.status = OBStatus::Invalidated;
                } else if close < self.mid {
                    self.status = OBStatus::Mitigated;
                } else if low <= self.high && low >= self.low {
                    self.status = OBStatus::Tested;
                }
            }
            OBType::Bearish => {
                if close > self.high {
                    self.status = OBStatus::Invalidated;
                } else if close > self.mid {
                    self.status = OBStatus::Mitigated;
                } else if high >= self.low && high <= self.high {
                    self.status = OBStatus::Tested;
                }
            }
        }
    }

    /// Distancia porcentual del precio actual al centro del OB.
    pub fn distance_pct(&self, price: f64) -> f64 {
        if self.mid > 0.0 {
            ((price - self.mid) / self.mid).abs()
        } else {
            f64::INFINITY
        }
    }

    /// True si el precio está dentro del rango del OB.
    pub fn price_inside(&self, price: f64) -> bool {
        price >= self.low && price <= self.high
    }
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
    /// Volumen total de la vela. 0.0 si no disponible.
    volume: f64,
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
        volume: f64,
        timestamp_ms: i64,
    ) {
        if self.bars.len() >= self.max_bars {
            self.bars.pop_front();
        }
        self.bars.push_back(Bar { open, high, low, close, volume, timestamp_ms });
    }

    pub fn snapshot(&self, current_price: f64) -> OrderBlockContext {
        let bars: Vec<Bar> = self.bars.iter().copied().collect();
        let n = bars.len();

        if n < self.impulse_bars + 2 {
            return OrderBlockContext::default();
        }

        // Volumen promedio de las últimas 20 velas para volume_ratio
        let avg_vol = {
            let window = bars.iter().rev().take(20);
            let (sum, count) = window.fold((0.0_f64, 0_usize), |(s, c), b| (s + b.volume, c + 1));
            if count > 0 && sum > 0.0 { sum / count as f64 } else { 0.0 }
        };

        let mut bullish_obs: Vec<OrderBlock> = Vec::new();
        let mut bearish_obs: Vec<OrderBlock> = Vec::new();

        // i + impulse_bars must be < n → i < n - impulse_bars
        for i in 0..(n - self.impulse_bars) {
            let bar = bars[i];
            let is_bearish = bar.close < bar.open;
            let is_bullish = bar.close > bar.open;

            // Bullish OB: última vela bajista antes de impulso alcista
            if is_bearish {
                let impulse_up = (1..=self.impulse_bars)
                    .all(|j| bars[i + j].close > bars[i + j].open);
                let breaks_high = bars[i + self.impulse_bars].high > bar.high;

                if impulse_up && breaks_high {
                    // swings_broken: barras antes del OB cuyo high fue superado por el impulso top
                    let impulse_top = bars[i + self.impulse_bars].high;
                    let swings_broken = bars[..i]
                        .iter()
                        .rev()
                        .take(5)
                        .filter(|b| b.high < impulse_top)
                        .count() as u32;

                    let volume_ratio = if avg_vol > 0.0 { bar.volume / avg_vol } else { 1.0 };
                    let mid = (bar.high + bar.low) / 2.0;
                    let status = ob_status_bullish(bar.high, bar.low, mid, current_price);

                    if !matches!(status, OBStatus::Mitigated | OBStatus::Invalidated) {
                        bullish_obs.push(OrderBlock {
                            ob_type: OBType::Bullish,
                            high: bar.high,
                            low: bar.low,
                            mid,
                            timestamp_ms: bar.timestamp_ms,
                            status,
                            volume_ratio,
                            swings_broken,
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
                    let impulse_bottom = bars[i + self.impulse_bars].low;
                    let swings_broken = bars[..i]
                        .iter()
                        .rev()
                        .take(5)
                        .filter(|b| b.low > impulse_bottom)
                        .count() as u32;

                    let volume_ratio = if avg_vol > 0.0 { bar.volume / avg_vol } else { 1.0 };
                    let mid = (bar.high + bar.low) / 2.0;
                    let status = ob_status_bearish(bar.high, bar.low, mid, current_price);

                    if !matches!(status, OBStatus::Mitigated | OBStatus::Invalidated) {
                        bearish_obs.push(OrderBlock {
                            ob_type: OBType::Bearish,
                            high: bar.high,
                            low: bar.low,
                            mid,
                            timestamp_ms: bar.timestamp_ms,
                            status,
                            volume_ratio,
                            swings_broken,
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

fn ob_status_bullish(high: f64, low: f64, mid: f64, price: f64) -> OBStatus {
    if price < low {
        OBStatus::Invalidated
    } else if price < mid {
        OBStatus::Mitigated
    } else if price <= high {
        OBStatus::Tested
    } else {
        OBStatus::Active
    }
}

fn ob_status_bearish(high: f64, low: f64, mid: f64, price: f64) -> OBStatus {
    if price > high {
        OBStatus::Invalidated
    } else if price > mid {
        OBStatus::Mitigated
    } else if price >= low {
        OBStatus::Tested
    } else {
        OBStatus::Active
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_detector() -> OrderBlockDetector {
        OrderBlockDetector::new(50)
    }

    fn bar(o: f64, h: f64, l: f64, c: f64, vol: f64, ts: i64) -> (f64, f64, f64, f64, f64, i64) {
        (o, h, l, c, vol, ts)
    }

    #[test]
    fn update_status_mitigated_by_close_past_mid() {
        let mut ob = OrderBlock {
            ob_type: OBType::Bullish,
            high: 100.0,
            low: 90.0,
            mid: 95.0,
            timestamp_ms: 0,
            status: OBStatus::Active,
            volume_ratio: 1.0,
            swings_broken: 0,
        };
        // Close below mid → Mitigated
        ob.update_status(94.0, 93.0, 94.0);
        assert_eq!(ob.status, OBStatus::Mitigated);
    }

    #[test]
    fn update_status_tested_when_low_touches_ob() {
        let mut ob = OrderBlock {
            ob_type: OBType::Bullish,
            high: 100.0,
            low: 90.0,
            mid: 95.0,
            timestamp_ms: 0,
            status: OBStatus::Active,
            volume_ratio: 1.0,
            swings_broken: 0,
        };
        // Close above mid but low touched the OB range → Tested
        ob.update_status(98.0, 91.0, 98.0);
        assert_eq!(ob.status, OBStatus::Tested);
    }

    #[test]
    fn update_status_invalidated_below_low() {
        let mut ob = OrderBlock {
            ob_type: OBType::Bullish,
            high: 100.0,
            low: 90.0,
            mid: 95.0,
            timestamp_ms: 0,
            status: OBStatus::Active,
            volume_ratio: 1.0,
            swings_broken: 0,
        };
        ob.update_status(85.0, 84.0, 85.0);
        assert_eq!(ob.status, OBStatus::Invalidated);
    }

    #[test]
    fn distance_pct_and_price_inside() {
        let ob = OrderBlock {
            ob_type: OBType::Bullish,
            high: 100.0,
            low: 90.0,
            mid: 95.0,
            timestamp_ms: 0,
            status: OBStatus::Active,
            volume_ratio: 1.5,
            swings_broken: 2,
        };
        assert!(ob.price_inside(95.0));
        assert!(!ob.price_inside(85.0));
        assert!((ob.distance_pct(95.0)).abs() < 1e-9);
        assert!((ob.distance_pct(100.0) - 5.0 / 95.0).abs() < 1e-6);
    }

    #[test]
    fn detects_bullish_ob_with_volume_ratio() {
        let mut det = make_detector();
        let bars = [
            bar(102.0, 104.0, 101.0, 103.0, 1000.0, 1), // bullish
            bar(103.0, 105.0, 102.0, 104.5, 1100.0, 2), // bullish
            bar(104.5, 105.0, 101.0, 101.5, 1800.0, 3), // bearish OB (high volume)
            bar(101.5, 108.0, 101.0, 107.0, 900.0, 4),  // bullish impulse 1
            bar(107.0, 112.0, 106.5, 111.0, 850.0, 5),  // bullish impulse 2 — breaks OB high
        ];
        for (o, h, l, c, v, ts) in bars {
            det.push_bar(o, h, l, c, v, ts);
        }
        let ctx = det.snapshot(115.0);
        assert!(ctx.nearest_bullish.is_some(), "should detect a bullish OB");
        let ob = ctx.nearest_bullish.unwrap();
        assert!(ob.volume_ratio > 1.0, "OB bar should have above-average volume");
        assert!((ob.mid - (ob.high + ob.low) / 2.0).abs() < 1e-9);
    }
}
