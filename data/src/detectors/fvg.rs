use std::collections::VecDeque;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum FvgType {
    /// Gap alcista: low[i+1] > high[i-1] — zona de soporte potencial debajo del precio.
    Bullish,
    /// Gap bajista: high[i+1] < low[i-1] — zona de resistencia potencial sobre el precio.
    Bearish,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum FvgStatus {
    Unfilled,
    PartiallyFilled,
    Filled,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FairValueGap {
    pub fvg_type: FvgType,
    /// Límite superior del gap.
    pub high: f64,
    /// Límite inferior del gap.
    pub low: f64,
    pub timestamp_ms: i64,
    pub status: FvgStatus,
}

/// Snapshot de FVGs activos relativo al precio actual.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct FvgContext {
    /// FVGs alcistas activos (debajo del precio — soportes potenciales).
    pub bullish_fvgs: Vec<FairValueGap>,
    /// FVGs bajistas activos (sobre el precio — resistencias potenciales).
    pub bearish_fvgs: Vec<FairValueGap>,
    /// FVG alcista no llenado más cercano por debajo del precio.
    pub nearest_bullish: Option<FairValueGap>,
    /// FVG bajista no llenado más cercano por encima del precio.
    pub nearest_bearish: Option<FairValueGap>,
}

#[derive(Clone, Copy)]
struct Bar {
    high: f64,
    low: f64,
    timestamp_ms: i64,
}

/// Detecta Fair Value Gaps en una ventana deslizante de barras.
/// Patrón de 3 velas: la vela central pertenece al gap si existe separación
/// entre high[i-1] y low[i+1] (bullish) o entre low[i-1] y high[i+1] (bearish).
pub struct FvgDetector {
    bars: VecDeque<Bar>,
    max_bars: usize,
    max_fvgs: usize,
}

impl FvgDetector {
    pub fn new(max_bars: usize) -> Self {
        Self {
            bars: VecDeque::with_capacity(max_bars),
            max_bars,
            max_fvgs: 5,
        }
    }

    pub fn push_bar(&mut self, high: f64, low: f64, timestamp_ms: i64) {
        if self.bars.len() >= self.max_bars {
            self.bars.pop_front();
        }
        self.bars.push_back(Bar {
            high,
            low,
            timestamp_ms,
        });
    }

    pub fn snapshot(&self, current_price: f64) -> FvgContext {
        let bars: Vec<Bar> = self.bars.iter().copied().collect();
        let n = bars.len();

        if n < 3 {
            return FvgContext::default();
        }

        let mut bullish_fvgs: Vec<FairValueGap> = Vec::new();
        let mut bearish_fvgs: Vec<FairValueGap> = Vec::new();

        for i in 1..(n - 1) {
            let prev = bars[i - 1];
            let curr = bars[i];
            let next = bars[i + 1];

            // Bullish FVG: low[i+1] > high[i-1] → precio saltó hacia arriba
            if next.low > prev.high {
                let gap_low = prev.high;
                let gap_high = next.low;
                let status = fvg_status_bullish(gap_low, gap_high, current_price);
                if !matches!(status, FvgStatus::Filled) {
                    bullish_fvgs.push(FairValueGap {
                        fvg_type: FvgType::Bullish,
                        high: gap_high,
                        low: gap_low,
                        timestamp_ms: curr.timestamp_ms,
                        status,
                    });
                }
            }

            // Bearish FVG: high[i+1] < low[i-1] → precio cayó bruscamente
            if next.high < prev.low {
                let gap_low = next.high;
                let gap_high = prev.low;
                let status = fvg_status_bearish(gap_low, gap_high, current_price);
                if !matches!(status, FvgStatus::Filled) {
                    bearish_fvgs.push(FairValueGap {
                        fvg_type: FvgType::Bearish,
                        high: gap_high,
                        low: gap_low,
                        timestamp_ms: curr.timestamp_ms,
                        status,
                    });
                }
            }
        }

        // Más recientes primero, limitado a max_fvgs
        let bullish_fvgs: Vec<_> = bullish_fvgs.into_iter().rev().take(self.max_fvgs).collect();
        let bearish_fvgs: Vec<_> = bearish_fvgs.into_iter().rev().take(self.max_fvgs).collect();

        // Nearest: bullish más cerca por debajo, bearish más cerca por encima
        let nearest_bullish = bullish_fvgs
            .iter()
            .filter(|g| g.high < current_price)
            .max_by(|a, b| {
                a.high
                    .partial_cmp(&b.high)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .cloned();

        let nearest_bearish = bearish_fvgs
            .iter()
            .filter(|g| g.low > current_price)
            .min_by(|a, b| {
                a.low
                    .partial_cmp(&b.low)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .cloned();

        FvgContext {
            bullish_fvgs,
            bearish_fvgs,
            nearest_bullish,
            nearest_bearish,
        }
    }
}

fn fvg_status_bullish(gap_low: f64, gap_high: f64, price: f64) -> FvgStatus {
    if price < gap_low {
        // Precio volvió por debajo del gap → llenado
        FvgStatus::Filled
    } else if price <= gap_high {
        FvgStatus::PartiallyFilled
    } else {
        FvgStatus::Unfilled
    }
}

fn fvg_status_bearish(gap_low: f64, gap_high: f64, price: f64) -> FvgStatus {
    if price > gap_high {
        // Precio subió por encima del gap bajista → llenado
        FvgStatus::Filled
    } else if price >= gap_low {
        FvgStatus::PartiallyFilled
    } else {
        FvgStatus::Unfilled
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn detects_bullish_fvg() {
        let mut det = FvgDetector::new(20);
        // vela[0]: high=100, low=99
        // vela[1]: high=105, low=100 (vela central del gap)
        // vela[2]: high=106, low=101  → low[2]=101 > high[0]=100 → bullish FVG [100, 101]
        det.push_bar(100.0, 99.0, 0);
        det.push_bar(105.0, 100.0, 1000);
        det.push_bar(106.0, 101.0, 2000);

        let ctx = det.snapshot(106.0);
        assert!(!ctx.bullish_fvgs.is_empty(), "debe detectar FVG alcista");
        let fvg = &ctx.bullish_fvgs[0];
        assert_eq!(fvg.fvg_type, FvgType::Bullish);
        assert!((fvg.low - 100.0).abs() < 0.001);
        assert!((fvg.high - 101.0).abs() < 0.001);
    }

    #[test]
    fn detects_bearish_fvg() {
        let mut det = FvgDetector::new(20);
        // vela[0]: high=106, low=105
        // vela[1]: high=104, low=100
        // vela[2]: high=103, low=99 → high[2]=103 < low[0]=105 → bearish FVG [103, 105]
        det.push_bar(106.0, 105.0, 0);
        det.push_bar(104.0, 100.0, 1000);
        det.push_bar(103.0, 99.0, 2000);

        let ctx = det.snapshot(99.0);
        assert!(!ctx.bearish_fvgs.is_empty(), "debe detectar FVG bajista");
        let fvg = &ctx.bearish_fvgs[0];
        assert_eq!(fvg.fvg_type, FvgType::Bearish);
    }
}
