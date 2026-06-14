use std::collections::VecDeque;

use serde::{Deserialize, Serialize};

/// Nivel de precio con densidad estimada de stops.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LiqDensityLevel {
    pub price: f64,
    /// Densidad normalizada 0.0–1.0 (1.0 = mayor concentración).
    pub density: f32,
}

/// Nivel de confianza en la predicción del LiqMapTracker.
/// Se deriva automáticamente de la densidad máxima del snapshot.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum LiqMapConfidence {
    /// Densidad >= 0.75 — nivel fuertemente respaldado por OI histórico.
    High,
    /// Densidad 0.50–0.74 — nivel relevante con respaldo moderado.
    Medium,
    /// Densidad < 0.50 — solo informativo, no usar para colocar targets.
    Low,
}

impl Default for LiqMapConfidence {
    fn default() -> Self {
        Self::Low
    }
}

/// Fuente de los datos de OI usados para construir el mapa.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum LiqMapSource {
    BinanceOI,
    BybitOI,
    /// Calculado a partir de swings + OI agregado — sin datos de OI por nivel.
    Estimated,
}

impl Default for LiqMapSource {
    fn default() -> Self {
        Self::Estimated
    }
}

/// Snapshot del mapa de liquidez estimado.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct LiqMapSnapshot {
    /// Niveles por encima del precio con concentración de stops (targets alcistas).
    pub density_above: Vec<LiqDensityLevel>,
    /// Niveles por debajo del precio con concentración de stops (targets bajistas).
    pub density_below: Vec<LiqDensityLevel>,
    /// Nivel de mayor densidad por encima (target principal alcista).
    pub primary_target_above: Option<f64>,
    /// Nivel de mayor densidad por debajo (target principal bajista).
    pub primary_target_below: Option<f64>,
    /// Confianza en los targets (derivada de la densidad máxima).
    #[serde(default)]
    pub confidence: LiqMapConfidence,
    /// Fuente de los datos de OI.
    #[serde(default)]
    pub data_source: LiqMapSource,
}

/// Metodología: los stops se concentran justo por encima de swing highs (stops de cortos)
/// y justo por debajo de swing lows (stops de largos). El OI en el momento del swing
/// sirve como proxy de la densidad — más OI = más posiciones = más stops acumulados.
///
/// El tracker acumula pares (swing_price, oi_at_time) y los pondera por antigüedad
/// para estimar qué niveles siguen siendo relevantes.
pub struct LiqMapTracker {
    /// Pares (precio del swing high, OI en ese momento, timestamp_ms)
    swing_highs: VecDeque<(f64, f64, i64)>,
    /// Pares (precio del swing low, OI en ese momento, timestamp_ms)
    swing_lows: VecDeque<(f64, f64, i64)>,
    /// OI actual de referencia para normalización
    oi_reference: f64,
    last_snapshot: LiqMapSnapshot,
}

impl LiqMapTracker {
    pub fn new() -> Self {
        Self {
            swing_highs: VecDeque::with_capacity(10),
            swing_lows: VecDeque::with_capacity(10),
            oi_reference: 1.0,
            last_snapshot: LiqMapSnapshot::default(),
        }
    }

    /// Actualiza el tracker con el estado de mercado actual.
    ///
    /// - `swing_high`/`swing_low`: swing vigente del período, si se detectó uno nuevo.
    /// - `oi_current`: OI actual en USD — actúa como referencia de normalización.
    /// - `current_price`: precio actual para clasificar arriba/abajo.
    pub fn update(
        &mut self,
        current_price: f64,
        swing_high: Option<f64>,
        swing_low: Option<f64>,
        oi_current: f64,
        timestamp_ms: i64,
    ) {
        if oi_current > 0.0 {
            self.oi_reference = oi_current;
        }

        // Registrar nuevo swing high si cambió
        if let Some(sh) = swing_high {
            let different = self
                .swing_highs
                .back()
                .map(|&(p, _, _)| (p - sh).abs() > sh * 0.0001)
                .unwrap_or(true);
            if different {
                if self.swing_highs.len() >= 10 {
                    self.swing_highs.pop_front();
                }
                self.swing_highs.push_back((sh, oi_current, timestamp_ms));
            }
        }

        if let Some(sl) = swing_low {
            let different = self
                .swing_lows
                .back()
                .map(|&(p, _, _)| (p - sl).abs() > sl * 0.0001)
                .unwrap_or(true);
            if different {
                if self.swing_lows.len() >= 10 {
                    self.swing_lows.pop_front();
                }
                self.swing_lows.push_back((sl, oi_current, timestamp_ms));
            }
        }

        self.last_snapshot = self.compute(current_price, timestamp_ms);
    }

    pub fn snapshot(&self) -> &LiqMapSnapshot {
        &self.last_snapshot
    }

    fn compute(&self, current_price: f64, now_ms: i64) -> LiqMapSnapshot {
        let ref_oi = self.oi_reference.max(1.0);
        // Factor de decaimiento temporal: niveles más viejos pesan menos
        let decay_half_life_ms: i64 = 4 * 60 * 60 * 1000; // 4 horas

        // When OI data is unavailable (oi_at_time == 0), fall back to pure time-decay
        // so swing levels are still visible (density ≈ 1.0 when fresh, decays to 0 over 4h).
        let oi_ratio = |oi_at_time: f64| -> f32 {
            if oi_at_time > 0.0 && ref_oi > 1.0 {
                (oi_at_time / ref_oi) as f32
            } else {
                1.0 // OI not available — use recency as sole weight
            }
        };

        let mut above: Vec<LiqDensityLevel> = self
            .swing_highs
            .iter()
            .filter(|&&(price, _, _)| price > current_price)
            .map(|&(price, oi_at_time, ts)| {
                let age_ms = (now_ms - ts).max(0);
                let time_factor = (-0.693 * age_ms as f64 / decay_half_life_ms as f64).exp() as f32;
                let density = (oi_ratio(oi_at_time) * time_factor).clamp(0.0, 1.0);
                LiqDensityLevel { price, density }
            })
            .collect();

        let mut below: Vec<LiqDensityLevel> = self
            .swing_lows
            .iter()
            .filter(|&&(price, _, _)| price < current_price)
            .map(|&(price, oi_at_time, ts)| {
                let age_ms = (now_ms - ts).max(0);
                let time_factor = (-0.693 * age_ms as f64 / decay_half_life_ms as f64).exp() as f32;
                let density = (oi_ratio(oi_at_time) * time_factor).clamp(0.0, 1.0);
                LiqDensityLevel { price, density }
            })
            .collect();

        above.sort_by(|a, b| {
            b.density
                .partial_cmp(&a.density)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        below.sort_by(|a, b| {
            b.density
                .partial_cmp(&a.density)
                .unwrap_or(std::cmp::Ordering::Equal)
        });

        let primary_target_above = above.first().map(|l| l.price);
        let primary_target_below = below.first().map(|l| l.price);

        let max_density = above
            .iter()
            .chain(below.iter())
            .map(|l| l.density)
            .fold(0.0_f32, f32::max);

        let confidence = if max_density >= 0.75 {
            LiqMapConfidence::High
        } else if max_density >= 0.50 {
            LiqMapConfidence::Medium
        } else {
            LiqMapConfidence::Low
        };

        LiqMapSnapshot {
            density_above: above,
            density_below: below,
            primary_target_above,
            primary_target_below,
            confidence,
            data_source: LiqMapSource::Estimated,
        }
    }
}

impl Default for LiqMapTracker {
    fn default() -> Self {
        Self::new()
    }
}
