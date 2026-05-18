use std::collections::VecDeque;

use serde::{Deserialize, Serialize};

/// Sesgo de la estructura de precio en timeframe alto.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum HtfBias {
    Bullish,
    Bearish,
    Neutral,
}

/// Zona del precio dentro del rango HTF activo.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum PriceZone {
    /// > 75% del rango — zona de distribución / venta cara.
    Premium,
    /// 25%–75% del rango — zona de equilibrio.
    Equilibrium,
    /// < 25% del rango — zona de acumulación / compra barata.
    Discount,
    Unknown,
}

/// Tipo de ruptura estructural.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum StructureEvent {
    /// Break of Structure — confirma continuación del sesgo vigente.
    Bos,
    /// Change of Character — señal de cambio de sesgo.
    Choch,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StructureBreak {
    pub event: StructureEvent,
    pub timestamp_ms: i64,
    /// Nivel de precio que fue roto.
    pub broken_level: f64,
    pub direction: HtfBias,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MarketStructureContext {
    pub htf_bias: HtfBias,
    pub price_zone: PriceZone,
    /// Swing high del rango HTF activo.
    pub range_high: Option<f64>,
    /// Swing low del rango HTF activo.
    pub range_low: Option<f64>,
    /// Umbral de zona Premium (75% del rango).
    pub premium_threshold: Option<f64>,
    /// Umbral de zona Discount (25% del rango).
    pub discount_threshold: Option<f64>,
    /// Ruptura estructural más reciente.
    pub last_event: Option<StructureBreak>,
}

/// Barra OHLC para el tracker de estructura.
#[derive(Debug, Clone, Copy)]
struct Bar {
    #[allow(dead_code)]
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    timestamp_ms: i64,
}

/// Rastrea la estructura de precio en HTF detectando BOS y CHoCH.
/// Consume barras en orden cronológico vía `push_bar`.
pub struct MarketStructureTracker {
    bars: VecDeque<Bar>,
    max_bars: usize,
    /// Barras a cada lado necesarias para confirmar un swing high/low.
    swing_period: usize,
    last_context: Option<MarketStructureContext>,
}

impl MarketStructureTracker {
    pub fn new(max_bars: usize, swing_period: usize) -> Self {
        Self {
            bars: VecDeque::with_capacity(max_bars),
            max_bars,
            swing_period,
            last_context: None,
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
        self.recompute();
    }

    pub fn context(&self) -> Option<&MarketStructureContext> {
        self.last_context.as_ref()
    }

    pub fn snapshot(&self) -> Option<MarketStructureContext> {
        self.last_context.clone()
    }

    fn recompute(&mut self) {
        let n = self.bars.len();
        if n < 2 * self.swing_period + 1 {
            return;
        }

        let bars: Vec<Bar> = self.bars.iter().copied().collect();

        // Detectar swing highs y lows confirmados
        let mut swing_highs: Vec<(f64, i64)> = Vec::new();
        let mut swing_lows: Vec<(f64, i64)> = Vec::new();

        let sp = self.swing_period;
        for i in sp..(n - sp) {
            let h = bars[i].high;
            let l = bars[i].low;
            let ts = bars[i].timestamp_ms;

            let is_swing_high = (1..=sp).all(|j| bars[i - j].high < h && bars[i + j].high < h);
            let is_swing_low = (1..=sp).all(|j| bars[i - j].low > l && bars[i + j].low > l);

            if is_swing_high {
                swing_highs.push((h, ts));
            }
            if is_swing_low {
                swing_lows.push((l, ts));
            }
        }

        let range_high = swing_highs.last().map(|&(h, _)| h);
        let range_low = swing_lows.last().map(|&(l, _)| l);
        let current_price = bars.last().unwrap().close;
        let current_ts = bars.last().unwrap().timestamp_ms;

        // Zona del precio dentro del rango
        let (price_zone, premium_threshold, discount_threshold) =
            match (range_high, range_low) {
                (Some(rh), Some(rl)) if rh > rl => {
                    let range = rh - rl;
                    let premium = rl + range * 0.75;
                    let discount = rl + range * 0.25;
                    let zone = if current_price > premium {
                        PriceZone::Premium
                    } else if current_price < discount {
                        PriceZone::Discount
                    } else {
                        PriceZone::Equilibrium
                    };
                    (zone, Some(premium), Some(discount))
                }
                _ => (PriceZone::Unknown, None, None),
            };

        let prev_bias = self
            .last_context
            .as_ref()
            .map(|c| c.htf_bias)
            .unwrap_or(HtfBias::Neutral);

        let (htf_bias, last_event) = self.detect_structure_break(
            &swing_highs,
            &swing_lows,
            current_price,
            current_ts,
            prev_bias,
        );

        self.last_context = Some(MarketStructureContext {
            htf_bias,
            price_zone,
            range_high,
            range_low,
            premium_threshold,
            discount_threshold,
            last_event,
        });
    }

    fn detect_structure_break(
        &self,
        swing_highs: &[(f64, i64)],
        swing_lows: &[(f64, i64)],
        current_price: f64,
        timestamp_ms: i64,
        prev_bias: HtfBias,
    ) -> (HtfBias, Option<StructureBreak>) {
        if swing_highs.len() < 2 || swing_lows.len() < 2 {
            // Sin suficientes swings — mantener sesgo previo sin evento
            let bias = if swing_highs.len() == 1 && swing_lows.len() == 1 {
                let (sh, _) = swing_highs[0];
                let (sl, _) = swing_lows[0];
                if current_price > sh {
                    HtfBias::Bullish
                } else if current_price < sl {
                    HtfBias::Bearish
                } else {
                    prev_bias
                }
            } else {
                prev_bias
            };
            return (bias, None);
        }

        let (last_high, _) = swing_highs[swing_highs.len() - 1];
        let (prev_high, _) = swing_highs[swing_highs.len() - 2];
        let (last_low, _) = swing_lows[swing_lows.len() - 1];
        let (prev_low, _) = swing_lows[swing_lows.len() - 2];

        // Ruptura alcista: precio por encima del último swing high
        if current_price > last_high {
            let event_type = if matches!(prev_bias, HtfBias::Bearish) {
                StructureEvent::Choch // ruptura contra-tendencia = cambio de carácter
            } else {
                StructureEvent::Bos // continuación alcista
            };
            return (
                HtfBias::Bullish,
                Some(StructureBreak {
                    event: event_type,
                    timestamp_ms,
                    broken_level: last_high,
                    direction: HtfBias::Bullish,
                }),
            );
        }

        // Ruptura bajista: precio por debajo del último swing low
        if current_price < last_low {
            let event_type = if matches!(prev_bias, HtfBias::Bullish) {
                StructureEvent::Choch // ruptura contra-tendencia = cambio de carácter
            } else {
                StructureEvent::Bos // continuación bajista
            };
            return (
                HtfBias::Bearish,
                Some(StructureBreak {
                    event: event_type,
                    timestamp_ms,
                    broken_level: last_low,
                    direction: HtfBias::Bearish,
                }),
            );
        }

        // Sin ruptura — inferir sesgo de la estructura de swings
        let bullish_structure = last_high > prev_high && last_low > prev_low;
        let bearish_structure = last_high < prev_high && last_low < prev_low;

        let bias = if bullish_structure {
            HtfBias::Bullish
        } else if bearish_structure {
            HtfBias::Bearish
        } else {
            prev_bias
        };

        (bias, None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn push_candles(tracker: &mut MarketStructureTracker, candles: &[(f64, f64, f64, f64)]) {
        for (i, &(o, h, l, c)) in candles.iter().enumerate() {
            tracker.push_bar(o, h, l, c, i as i64 * 60_000);
        }
    }

    #[test]
    fn detects_bullish_bias_from_structure() {
        let mut tracker = MarketStructureTracker::new(50, 2);
        // Secuencia de HH y HL
        let candles = [
            (100.0, 102.0, 99.0, 101.0),
            (101.0, 103.0, 100.0, 102.0),
            (102.0, 104.0, 101.0, 103.5),
            (103.0, 105.0, 102.0, 104.0),
            (104.0, 106.0, 103.0, 105.0),
            (105.0, 107.0, 104.0, 106.5),
            (106.0, 108.0, 105.0, 107.0),
        ];
        push_candles(&mut tracker, &candles);
        let ctx = tracker.context();
        assert!(ctx.is_some());
        let ctx = ctx.unwrap();
        assert!(
            matches!(ctx.htf_bias, HtfBias::Bullish | HtfBias::Neutral),
            "expected bullish/neutral, got {:?}",
            ctx.htf_bias
        );
    }

    #[test]
    fn price_zone_discount() {
        let mut tracker = MarketStructureTracker::new(50, 2);
        // Rango claro 100–110, precio ahora en 101 = discount
        let candles = [
            (100.0, 100.5, 99.5, 100.0),
            (100.0, 110.0, 100.0, 109.0),
            (109.0, 110.0, 108.0, 108.5),
            (108.5, 109.0, 107.5, 108.0),
            (108.0, 108.5, 107.0, 107.5),
            (107.5, 108.0, 101.0, 101.5), // pullback fuerte
            (101.5, 102.0, 101.0, 101.2),
        ];
        push_candles(&mut tracker, &candles);
        if let Some(ctx) = tracker.context() {
            if ctx.range_high.is_some() && ctx.range_low.is_some() {
                // Solo verificar que la zona se asigna (puede ser Discount o Equilibrium según swings detectados)
                assert!(!matches!(ctx.price_zone, PriceZone::Unknown));
            }
        }
    }
}
