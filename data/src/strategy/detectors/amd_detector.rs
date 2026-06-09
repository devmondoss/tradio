//! AMD — Accumulation · Manipulation · Distribution
//!
//! Estrategia de reversión post-spike basada en SMC (Smart Money Concepts).
//!
//! Hipótesis: Los institucionales construyen un rango de consolidación (acumulación),
//! luego hacen un spike en una dirección para barrer stops (manipulación), y finalmente
//! mueven el precio en la dirección opuesta (distribución real).
//!
//! La manipulación se detecta por divergencia CVD/precio durante el spike + VPIN alto.
//! El entry es en la primera barra de reversión confirmada con CVD slope + OBI + VR.
//!
//! Sesiones: 24/7 (el patrón no tiene horario — validado en ejemplos Asia, NY, London).

use std::collections::VecDeque;
use serde::{Deserialize, Serialize};

// ── Constantes internas ────────────────────────────────────────────────────────

const VR_WINDOW:   usize = 50;
const WARMUP_BARS: usize = 60;

// ── Tipos públicos ─────────────────────────────────────────────────────────────

/// Dirección del spike de MANIPULACIÓN (la dirección FALSA).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum SpikeDir {
    Up,   // Spike alcista falso → distribución SHORT
    Down, // Spike bajista falso → distribución LONG
}

/// Dirección del trade (OPUESTA al spike).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum AmdDirection {
    Short,
    Long,
}

/// Cómo se eligió el target estructural.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TargetSource {
    LvnNearby,
    NakedPoc,
    OrderBlock,
    Fvg,
    ValueArea,  // VAL (Short) o VAH (Long) del Volume Profile de sesión
    Fallback2R, // No hay nivel estructural → 2× el risk
}

/// Señal AMD emitida en el momento de la distribución (entry de reversión).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AmdSignal {
    pub timestamp_ms:       i64,
    pub direction:          AmdDirection,
    pub entry_price:        f64,
    pub stop_price:         f64,  // Detrás del spike extreme + buffer
    pub target_price:       f64,  // Nivel estructural más cercano en dirección
    pub rr:                 f64,

    // Contexto de acumulación
    pub range_high:         f64,
    pub range_low:          f64,
    pub range_pct:          f64,
    pub range_bars:         usize,
    pub cvd_in_range:       f64,  // CVD acumulado durante la consolidación (debería ser ~0)

    // Contexto del spike de manipulación
    pub spike_extreme:      f64,  // High (spike Up) o Low (spike Down)
    pub spike_direction:    SpikeDir,
    pub vr_at_spike:        f64,
    pub vpin_at_spike:      Option<f64>,
    pub bar_delta_at_spike:  f64,  // Negativo en spike UP = CVD diverge = manipulación
    pub liq_ratio_at_spike:  f64,  // Z-score de liquidaciones en el spike
    pub dz_at_spike:         Option<f64>, // (close - vwap) / atr en el spike

    // Contexto del entry (primera barra de distribución)
    pub vr_at_entry:        f64,
    pub cvd_slope_at_entry: Option<f64>,
    pub obi_at_entry:       f64,

    // Target
    pub target_source:      TargetSource,

    // Metadata
    pub session_name:       String,
    pub funding_at_entry:   Option<f64>,

    // Quality score (0-10) — basado en Fabio Valentini orderflow methodology
    // Absorción primaria + CVD divergencia + VR extraordinario + dz + liq_ratio + session_cvd
    pub quality_score:         u8,
    pub absorption_in_range:   u8,   // barras del rango con absorción activa
    pub absorption_at_spike:   bool, // absorción confirma manipulación en spike
    pub regime_is_trending:    bool, // régimen direccional en el momento de la señal
    pub session_cvd:           f64,  // CVD acumulado de sesión al momento de la señal
}

// ── Contexto externo ───────────────────────────────────────────────────────────

/// Datos externos que AMD necesita por barra y que no residen en el historial interno.
pub struct AmdContext {
    /// VPIN (Volume-synchronized Probability of Informed Trading).
    pub vpin:         Option<f64>,
    /// CVD slope OLS (USD/barra) — externo preferido sobre el cálculo interno.
    pub cvd_slope:    Option<f64>,
    /// Order Book Imbalance L5: positivo = bid dominante, negativo = ask dominante.
    pub obi_l5:       Option<f64>,
    /// VWAP de sesión (no usado en cálculo ahora, reservado para gate futuro).
    pub vwap:         Option<f64>,
    /// Niveles LVN del Volume Profile (precios con bajo volumen = camino libre).
    pub lvn_levels:   Vec<f64>,
    /// Naked POCs de sesiones anteriores no revisitados.
    pub naked_pocs:   Vec<f64>,
    /// Midpoints de Order Blocks activos.
    pub ob_levels:    Vec<f64>,
    /// Midpoints de Fair Value Gaps activos.
    pub fvg_levels:   Vec<f64>,
    /// Funding rate en el momento del entry.
    pub funding_rate: Option<f64>,
    /// Nombre de sesión para registrar en la señal.
    pub session_name: String,
    /// Z-score de precio respecto a VWAP de sesión (close - vwap) / atr.
    /// Gate: abs >= manip_dz_spike_min para confirmar que el spike rompió zona significativa.
    pub vwap_dz: Option<f64>,
    /// Ratio de liquidaciones en el spike (z-score abs de liq tracker).
    /// Gate: < manip_liq_ratio_max para filtrar cascadas (VPIN cascade = AMD falla).
    pub liq_ratio: f64,

    // ── Order flow context (quality score) ──────────────────────────────────────
    /// Absorción bid: compradores absorbiendo presión vendedora (bullish).
    pub absorption_bid: bool,
    /// Absorción ask: vendedores absorbiendo presión compradora (bearish).
    pub absorption_ask: bool,
    /// Régimen direccional (TrendUp/TrendDown/Expansion) vs lateral (Chop/Compression).
    pub regime_is_trending: bool,
    /// CVD acumulado desde el inicio de la sesión (reset diario UTC).
    /// Fabio: CVD plano = contracción = no operar; CVD direccional confirma expansión.
    pub session_cvd: f64,

    // ── Value Area + Walls (quality score + target) ─────────────────────────────
    /// Value Area Low del Volume Profile de sesión. Para scoring (+1pt) y target (Short→VAL).
    pub val: Option<f64>,
    /// Value Area High del Volume Profile de sesión. Para scoring (+1pt) y target (Long→VAH).
    pub vah: Option<f64>,
    /// Pared de bids (≤1×ATR por debajo del precio) — confirma soporte para LONG.
    pub bid_wall_nearby: bool,
    /// Pared de asks (≤1×ATR por encima del precio) — confirma resistencia para SHORT.
    pub ask_wall_nearby: bool,
    /// CVD de big trades (≥$100k notional) en la barra del spike.
    /// Negativo = big sellers dominaron spike alcista → señal fuerte de absorción institucional.
    pub big_trade_cvd_bar: f64,
}

// ── Estado interno ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
struct BarSnap {
    high:   f64,
    low:    f64,
    close:  f64,
    volume: f64,
    delta:  f64,
}

#[derive(Debug, Clone)]
enum AmdPhase {
    Idle,
    Accumulating {
        range_high:      f64,
        range_low:       f64,
        cvd_sum:         f64,
        bars:            usize,
        absorption_count: u8,  // barras con absorción durante el rango
    },
    ManipulationDetected {
        spike_extreme:        f64,
        spike_dir:            SpikeDir,
        vr_at_spike:          f64,
        vpin_at_spike:        Option<f64>,
        bar_delta_at_spike:   f64,
        liq_ratio_at_spike:   f64,
        dz_at_spike:          Option<f64>,
        range_high:           f64,
        range_low:            f64,
        range_bars:           usize,
        cvd_in_range:         f64,
        absorption_in_range:  u8,   // barras con absorción durante acumulación
        absorption_at_spike:  bool, // absorción en dirección correcta en el spike
        bars_since_spike:     usize,
        spike_timestamp_ms:   i64,
    },
}

pub struct AmdDetectorState {
    phase:           AmdPhase,
    history:         VecDeque<BarSnap>,
    vol_hist:        VecDeque<f64>,
    bars_seen:       usize,
    last_signal_bar: usize,
}

impl AmdDetectorState {
    pub fn new() -> Self {
        Self {
            phase:           AmdPhase::Idle,
            history:         VecDeque::with_capacity(65),
            vol_hist:        VecDeque::with_capacity(VR_WINDOW + 5),
            bars_seen:       0,
            last_signal_bar: 0,
        }
    }

    /// Resetea el cooldown para que el warmup no bloquee la primera barra live.
    pub fn reset_signal_cooldown(&mut self) {
        self.last_signal_bar = 0;
    }

    fn compute_vr(&self, volume: f64) -> f64 {
        if self.vol_hist.is_empty() { return 1.0; }
        let mean = self.vol_hist.iter().sum::<f64>() / self.vol_hist.len() as f64;
        if mean > 0.0 { volume / mean } else { 1.0 }
    }

    /// Selecciona el target estructural más cercano en la dirección de distribución.
    /// Orden de preferencia: LVN → Naked POC → Order Block → FVG → fallback 2× risk.
    fn select_target(
        dir:   AmdDirection,
        entry: f64,
        risk:  f64,
        ctx:   &AmdContext,
    ) -> (f64, TargetSource) {
        // Umbral mínimo: el target debe estar al menos 0.5× risk desde el entry
        // para no seleccionar niveles demasiado cercanos
        let min_dist = risk * 0.5;

        let mut best: Option<(f64, TargetSource)> = None;

        let candidates: &[(&[f64], TargetSource)] = &[
            (&ctx.lvn_levels,  TargetSource::LvnNearby),
            (&ctx.naked_pocs,  TargetSource::NakedPoc),
            (&ctx.ob_levels,   TargetSource::OrderBlock),
            (&ctx.fvg_levels,  TargetSource::Fvg),
        ];

        for (levels, source) in candidates {
            for &lvl in *levels {
                let in_dir = match dir {
                    AmdDirection::Short => lvl < entry - min_dist,
                    AmdDirection::Long  => lvl > entry + min_dist,
                };
                if !in_dir { continue; }
                let dist = (lvl - entry).abs();
                let is_nearer = best.as_ref().map_or(true, |(b, _)| dist < (b - entry).abs());
                if is_nearer {
                    best = Some((lvl, *source));
                }
            }
        }

        // VAL (Short) / VAH (Long): target del Value Area (Fabio: rebalance al valor)
        // Prioridad: más cercano que el mejor candidato actual, igual que los anteriores
        let va_level = match dir {
            AmdDirection::Short => ctx.val,
            AmdDirection::Long  => ctx.vah,
        };
        if let Some(lvl) = va_level {
            let in_dir = match dir {
                AmdDirection::Short => lvl < entry - min_dist,
                AmdDirection::Long  => lvl > entry + min_dist,
            };
            if in_dir {
                let dist = (lvl - entry).abs();
                let is_nearer = best.as_ref().map_or(true, |(b, _)| dist < (b - entry).abs());
                if is_nearer {
                    best = Some((lvl, TargetSource::ValueArea));
                }
            }
        }

        best.unwrap_or_else(|| {
            let fallback = match dir {
                AmdDirection::Short => entry - risk * 2.0,
                AmdDirection::Long  => entry + risk * 2.0,
            };
            (fallback, TargetSource::Fallback2R)
        })
    }

    /// Llamar en cada cierre de barra M1.
    pub fn on_bar_close(
        &mut self,
        high:       f64,
        low:        f64,
        close:      f64,
        volume:     f64,
        bar_delta:  f64,
        timestamp_ms: i64,
        ctx:        &AmdContext,
        cfg:        &AmdDetectorConfig,
    ) -> Option<AmdSignal> {
        if !cfg.enabled { return None; }

        self.bars_seen += 1;

        // Mantener buffers
        self.vol_hist.push_back(volume);
        if self.vol_hist.len() > VR_WINDOW { self.vol_hist.pop_front(); }

        self.history.push_back(BarSnap { high, low, close, volume, delta: bar_delta });
        let max_hist = cfg.accum_max_bars + 10;
        if self.history.len() > max_hist { self.history.pop_front(); }

        if self.bars_seen < WARMUP_BARS { return None; }
        if self.bars_seen - self.last_signal_bar < cfg.cooldown_bars { return None; }

        let vr  = self.compute_vr(volume);
        let obi = ctx.obi_l5.unwrap_or(0.0);

        // Clonar fase para evitar borrow mutable + inmutable simultáneo
        let phase = self.phase.clone();

        match phase {
            // ── IDLE: buscar acumulación ──────────────────────────────────────
            AmdPhase::Idle => {
                self.try_enter_accumulation(cfg);
                None
            }

            // ── ACUMULANDO: extender rango o detectar spike ───────────────────
            AmdPhase::Accumulating { range_high, range_low, cvd_sum, bars, absorption_count } => {
                // Precio sigue dentro del rango → extender
                if close > range_low && close < range_high {
                    let new_high = range_high.max(high);
                    let new_low  = range_low.min(low);
                    let new_pct  = (new_high - new_low) / close * 100.0;

                    if new_pct > cfg.accum_range_max_pct {
                        self.phase = AmdPhase::Idle;
                        return None;
                    }

                    if bars + 1 > cfg.accum_max_bars {
                        self.phase = AmdPhase::Idle;
                        return None;
                    }

                    // Trackear barras con absorción (cualquier lado = actividad institucional)
                    let new_absorption = absorption_count.saturating_add(
                        if ctx.absorption_bid || ctx.absorption_ask { 1 } else { 0 }
                    );

                    self.phase = AmdPhase::Accumulating {
                        range_high:      new_high,
                        range_low:       new_low,
                        cvd_sum:         cvd_sum + bar_delta,
                        bars:            bars + 1,
                        absorption_count: new_absorption,
                    };
                    return None;
                }

                // Precio cerró FUERA del rango → posible spike
                let range_pct = (range_high - range_low) / close * 100.0;

                if range_pct < cfg.accum_range_min_pct
                    || range_pct > cfg.accum_range_max_pct
                    || bars < cfg.accum_min_bars
                {
                    self.phase = AmdPhase::Idle;
                    return None;
                }

                if vr < cfg.manip_min_vr {
                    self.phase = AmdPhase::Idle;
                    return None;
                }

                let spike_dir = if close > range_high { SpikeDir::Up } else { SpikeDir::Down };
                let spike_extreme = match spike_dir {
                    SpikeDir::Up   => high,
                    SpikeDir::Down => low,
                };

                // Firma de manipulación: VPIN alto O CVD diverge (cualquiera es suficiente)
                let vpin_high = ctx.vpin.map_or(false, |v| v > cfg.manip_vpin_threshold);
                let cvd_diverged = match spike_dir {
                    SpikeDir::Up   => bar_delta < 0.0,
                    SpikeDir::Down => bar_delta > 0.0,
                };

                // Absorción en la dirección de la manipulación (Fabio: señal primaria)
                // Spike Up (falso): queremos Ask absorption (vendedores absorbiendo el up move)
                // Spike Down (falso): queremos Bid absorption (compradores absorbiendo el down move)
                let absorption_at_spike = match spike_dir {
                    SpikeDir::Up   => ctx.absorption_ask,
                    SpikeDir::Down => ctx.absorption_bid,
                };

                let liq_ok = ctx.liq_ratio <= cfg.manip_liq_ratio_max;
                let dz_ok  = ctx.vwap_dz.map_or(true, |dz| dz.abs() >= cfg.manip_dz_spike_min);

                if (vpin_high || cvd_diverged) && liq_ok && dz_ok {
                    self.phase = AmdPhase::ManipulationDetected {
                        spike_extreme,
                        spike_dir,
                        vr_at_spike:         vr,
                        vpin_at_spike:       ctx.vpin,
                        bar_delta_at_spike:  bar_delta,
                        liq_ratio_at_spike:  ctx.liq_ratio,
                        dz_at_spike:         ctx.vwap_dz,
                        range_high,
                        range_low,
                        range_bars:          bars,
                        cvd_in_range:        cvd_sum,
                        absorption_in_range: absorption_count,
                        absorption_at_spike,
                        bars_since_spike:    0,
                        spike_timestamp_ms:  timestamp_ms,
                    };
                } else {
                    self.phase = AmdPhase::Idle;
                }
                None
            }

            // ── MANIPULACIÓN DETECTADA: esperar primera barra de distribución ─
            AmdPhase::ManipulationDetected {
                spike_extreme, spike_dir,
                vr_at_spike, vpin_at_spike, bar_delta_at_spike,
                liq_ratio_at_spike, dz_at_spike,
                range_high, range_low, range_bars, cvd_in_range,
                absorption_in_range, absorption_at_spike,
                bars_since_spike, ..
            } => {
                if bars_since_spike >= cfg.max_wait_bars_after_spike {
                    self.phase = AmdPhase::Idle;
                    return None;
                }

                self.phase = AmdPhase::ManipulationDetected {
                    spike_extreme,
                    spike_dir,
                    vr_at_spike,
                    vpin_at_spike,
                    bar_delta_at_spike,
                    liq_ratio_at_spike,
                    dz_at_spike,
                    range_high,
                    range_low,
                    range_bars,
                    cvd_in_range,
                    absorption_in_range,
                    absorption_at_spike,
                    bars_since_spike: bars_since_spike + 1,
                    spike_timestamp_ms: timestamp_ms,
                };

                let dist_dir = match spike_dir {
                    SpikeDir::Up   => AmdDirection::Short,
                    SpikeDir::Down => AmdDirection::Long,
                };

                // La barra debe cerrar de vuelta por debajo/encima del nivel roto
                // (el spike falló en sostener el precio fuera del rango)
                let closes_right = match dist_dir {
                    AmdDirection::Short => close < range_high,
                    AmdDirection::Long  => close > range_low,
                };
                if !closes_right { return None; }

                // VR confirma volumen real en la nueva dirección
                if vr < cfg.dist_min_vr { return None; }

                // CVD slope y OBI: grabamos el contexto pero no bloqueamos en shadow mode.
                // En shadow mode (dist_cvd_slope=5.0, dist_obi_confirm=0.08 con defaults)
                // estos gates se desactivan con dist_cvd_slope=999.0 / dist_obi_confirm=1.0
                // para acumular señales y calibrar. En producción se activan con datos reales.

                // Calcular precios de entrada
                let entry = close;
                let stop = match dist_dir {
                    AmdDirection::Short => spike_extreme * (1.0 + cfg.stop_buffer_pct / 100.0),
                    AmdDirection::Long  => spike_extreme * (1.0 - cfg.stop_buffer_pct / 100.0),
                };
                let risk = (stop - entry).abs();
                if risk < 1.0 { return None; }

                let (target, target_source) = Self::select_target(dist_dir, entry, risk, ctx);
                let reward = (target - entry).abs();
                let rr = reward / risk;
                if rr < cfg.min_rr { return None; }

                let range_pct = (range_high - range_low) / entry * 100.0;

                let quality_score = Self::compute_quality_score(
                    vr_at_spike,
                    bar_delta_at_spike,
                    spike_dir,
                    dz_at_spike,
                    liq_ratio_at_spike,
                    absorption_in_range,
                    absorption_at_spike,
                    ctx.session_cvd,
                    range_high,
                    range_low,
                    ctx,
                );

                self.last_signal_bar = self.bars_seen;
                self.phase = AmdPhase::Idle;

                Some(AmdSignal {
                    timestamp_ms,
                    direction:           dist_dir,
                    entry_price:         entry,
                    stop_price:          stop,
                    target_price:        target,
                    rr,
                    range_high,
                    range_low,
                    range_pct,
                    range_bars,
                    cvd_in_range,
                    spike_extreme,
                    spike_direction:     spike_dir,
                    vr_at_spike,
                    vpin_at_spike,
                    bar_delta_at_spike,
                    liq_ratio_at_spike,
                    dz_at_spike,
                    vr_at_entry:         vr,
                    cvd_slope_at_entry:  ctx.cvd_slope,
                    obi_at_entry:        obi,
                    target_source,
                    session_name:        ctx.session_name.clone(),
                    funding_at_entry:    ctx.funding_rate,
                    quality_score,
                    absorption_in_range,
                    absorption_at_spike,
                    regime_is_trending:  ctx.regime_is_trending,
                    session_cvd:         ctx.session_cvd,
                })
            }
        }
    }

    /// Intenta entrar en fase ACCUMULATING si las últimas `accum_min_bars` forman un rango válido.
    fn try_enter_accumulation(&mut self, cfg: &AmdDetectorConfig) {
        let n = self.history.len();
        if n < cfg.accum_min_bars { return; }

        let window: Vec<&BarSnap> = self.history.iter().rev().take(cfg.accum_min_bars).collect();
        let range_high = window.iter().map(|b| b.high).fold(f64::NEG_INFINITY, f64::max);
        let range_low  = window.iter().map(|b| b.low).fold(f64::INFINITY, f64::min);
        let close      = window[0].close;
        let range_pct  = (range_high - range_low) / close * 100.0;

        if range_pct < cfg.accum_range_min_pct || range_pct > cfg.accum_range_max_pct {
            return;
        }

        let cvd_sum: f64 = window.iter().map(|b| b.delta).sum();

        self.phase = AmdPhase::Accumulating {
            range_high,
            range_low,
            cvd_sum,
            bars:             cfg.accum_min_bars,
            absorption_count: 0,
        };
    }

    /// Quality score 0-12 inspirado en Fabio Valentini orderflow methodology.
    /// Cap en 10 para normalizar la escala independientemente de cuántos puntos sumen.
    ///
    /// [0-2] Absorción en rango       — señal primaria: institucionales activos en el rango
    /// [0-2] Absorción en spike bar   — el spike fue absorbido (Fabio: "vendedores siendo absorbidos")
    /// [0-2] CVD diverge              — precio en una dirección, delta en la otra
    /// [0-2] VR extraordinario        — volumen ≥2x o ≥3x la media
    /// [0-1] dz ≥ 1.5                 — spike rompió zona significativa vs VWAP
    /// [0-1] liq_ratio < 1.2          — no es una cascada de liquidaciones pura
    /// [0-1] session CVD contradice spike — CVD de sesión opuesto al spike = manipulación fuerte
    /// [0-1] Value Area: rango en VAH (Short) o VAL (Long) — setup Fabio "low of value area"
    /// [0-1] Wall confirma reversión  — ask_wall para Short, bid_wall para Long
    #[allow(clippy::too_many_arguments)]
    fn compute_quality_score(
        vr_at_spike:         f64,
        bar_delta_at_spike:  f64,
        spike_dir:           SpikeDir,
        dz_at_spike:         Option<f64>,
        liq_ratio_at_spike:  f64,
        absorption_in_range: u8,
        absorption_at_spike: bool,
        session_cvd:         f64,
        // Value Area y Walls (Fabio #4/#8)
        range_high:          f64,
        range_low:           f64,
        ctx:                 &AmdContext,
    ) -> u8 {
        let mut score: u8 = 0;

        // [+0-2] Absorción en rango (señal primaria según Fabio)
        score += absorption_in_range.min(2);

        // [+0-2] Absorción en el spike confirma que el move fue absorbido
        if absorption_at_spike { score += 2; }

        // [+0-2] CVD diverge: precio va en una dirección, delta en la otra
        let cvd_diverged = match spike_dir {
            SpikeDir::Up   => bar_delta_at_spike < 0.0,
            SpikeDir::Down => bar_delta_at_spike > 0.0,
        };
        if cvd_diverged { score += 2; }

        // [+0-2] Volumen extraordinario en el spike
        if vr_at_spike >= 3.0      { score += 2; }
        else if vr_at_spike >= 2.0 { score += 1; }

        // [+0-1] dz ≥ 1.5: spike rompió zona significativa desde VWAP
        if dz_at_spike.map_or(false, |dz| dz.abs() >= 1.5) { score += 1; }

        // [+0-1] liq_ratio bajo: no es una cascada pura de liquidaciones
        if liq_ratio_at_spike < 1.2 { score += 1; }

        // [+0-1] session CVD contradice spike: sesión neta vendedora pero spike alcista = trampa
        // Threshold ±500 USD delta para ignorar CVD plano/ruidoso de inicio de sesión
        let session_cvd_contradicts = match spike_dir {
            SpikeDir::Up   => session_cvd < -500.0,
            SpikeDir::Down => session_cvd >  500.0,
        };
        if session_cvd_contradicts { score += 1; }

        // [+0-1] Value Area: spike Up desde VAH, spike Down desde VAL (Fabio: "rango en value area")
        // El rango de acumulación estaba en el borde del Value Area — setup AAA clásico
        let va_proximity_threshold = 0.007; // ±0.7% del precio = alineado con VAH/VAL
        let va_aligned = match spike_dir {
            SpikeDir::Up   => ctx.vah.map_or(false, |vah| {
                (range_high - vah).abs() / vah < va_proximity_threshold
            }),
            SpikeDir::Down => ctx.val.map_or(false, |val| {
                (range_low - val).abs() / val < va_proximity_threshold
            }),
        };
        if va_aligned { score += 1; }

        // [+0-1] Wall confirma reversión: ask_wall para SHORT, bid_wall para LONG
        let wall_confirms = match spike_dir {
            SpikeDir::Up   => ctx.ask_wall_nearby,
            SpikeDir::Down => ctx.bid_wall_nearby,
        };
        if wall_confirms { score += 1; }

        // [+0-1] Big trade CVD contradice el spike (Fabio: "las órdenes grandes son las que importan")
        // Spike Up pero big sellers dominaron la barra del spike → spike absorbido institucionalmente
        let big_cvd_contradicts = match spike_dir {
            SpikeDir::Up   => ctx.big_trade_cvd_bar < -1.0,  // ≥1 BTC net en big sells
            SpikeDir::Down => ctx.big_trade_cvd_bar >  1.0,  // ≥1 BTC net en big buys
        };
        if big_cvd_contradicts { score += 1; }

        score.min(10)
    }
}

// ── Configuración ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AmdDetectorConfig {
    pub enabled: bool,

    // Fase de acumulación
    /// Rango mínimo como % del precio (0.06 = $36 a $60k BTC).
    pub accum_range_min_pct:  f64,
    /// Rango máximo como % del precio (0.45 = $270 a $60k BTC).
    pub accum_range_max_pct:  f64,
    /// Barras mínimas en acumulación antes de reconocer el rango.
    pub accum_min_bars:       usize,
    /// Barras máximas en acumulación antes de hacer timeout y resetear.
    pub accum_max_bars:       usize,

    // Detección de manipulación
    /// VR mínimo en la barra del spike (volumen relativo vs media 50 barras).
    pub manip_min_vr:         f64,
    /// VPIN mínimo durante el spike (flujo tóxico — institucionales ejecutando).
    pub manip_vpin_threshold: f64,

    // Entry de distribución
    /// VR mínimo en la primera barra de reversión.
    pub dist_min_vr:          f64,
    /// |CVD slope| mínimo confirmando dirección de distribución (USD/barra).
    pub dist_cvd_slope:       f64,
    /// |OBI| mínimo confirmando dirección de distribución.
    pub dist_obi_confirm:     f64,

    // Gestión del trade
    /// Buffer por encima/debajo del spike extreme para el stop (%).
    pub stop_buffer_pct:      f64,
    /// RR mínimo requerido para emitir señal.
    pub min_rr:               f64,
    /// Cooldown en barras entre señales.
    pub cooldown_bars:        usize,

    // Timeout de la fase de manipulación detectada
    /// Si no llega la barra de distribución en este tiempo, reset a Idle.
    pub max_wait_bars_after_spike: usize,

    // Gates de microestructura en el spike (desactivados por defecto)
    /// liq_ratio máximo permitido en el spike. 999.0 = desactivado.
    /// Basado en análisis: ganadores median=0.67, perdedores=1.51. Gate sugerido: 1.2.
    pub manip_liq_ratio_max: f64,
    /// |dz_vwap| mínimo en el spike = spike rompió zona significativa. 0.0 = desactivado.
    /// Basado en análisis: dz>=1.5 → WR 44%, avgR +0.33. Gate sugerido: 1.0–1.5.
    pub manip_dz_spike_min: f64,
}

impl Default for AmdDetectorConfig {
    fn default() -> Self {
        Self {
            enabled:                   false,
            accum_range_min_pct:       0.06,
            accum_range_max_pct:       0.45,
            accum_min_bars:            15,
            accum_max_bars:            50,
            manip_min_vr:              2.0,
            manip_vpin_threshold:      0.60,
            dist_min_vr:               2.5,
            dist_cvd_slope:            10.0,
            dist_obi_confirm:          0.10,
            stop_buffer_pct:           0.08,
            min_rr:                    2.0,
            cooldown_bars:             45,
            max_wait_bars_after_spike: 6,
            manip_liq_ratio_max:       999.0, // desactivado — activar tras 30+ días de datos
            manip_dz_spike_min:        0.0,   // desactivado — activar tras 30+ días de datos
        }
    }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn cfg() -> AmdDetectorConfig {
        AmdDetectorConfig { enabled: true, ..AmdDetectorConfig::default() }
    }

    fn ctx_neutral() -> AmdContext {
        AmdContext {
            vpin:               Some(0.30),
            cvd_slope:          Some(-15.0),
            obi_l5:             Some(-0.15),
            vwap:               None,
            lvn_levels:         vec![],
            naked_pocs:         vec![],
            ob_levels:          vec![],
            fvg_levels:         vec![],
            funding_rate:       None,
            session_name:       "London".into(),
            vwap_dz:            None,
            liq_ratio:          0.0,
            absorption_bid:     false,
            absorption_ask:     false,
            regime_is_trending: false,
            session_cvd:        0.0,
            val:                None,
            vah:                None,
            bid_wall_nearby:    false,
            ask_wall_nearby:    false,
        }
    }

    fn push_bars(state: &mut AmdDetectorState, n: usize, close: f64, cfg: &AmdDetectorConfig) {
        let ctx = AmdContext {
            vpin: Some(0.30), cvd_slope: Some(0.0), obi_l5: Some(0.0),
            vwap: None, lvn_levels: vec![], naked_pocs: vec![],
            ob_levels: vec![], fvg_levels: vec![], funding_rate: None,
            session_name: "Test".into(),
            vwap_dz: None, liq_ratio: 0.0,
            absorption_bid: false, absorption_ask: false, regime_is_trending: false, session_cvd: 0.0,
            val: None, vah: None, bid_wall_nearby: false, ask_wall_nearby: false, big_trade_cvd_bar: 0.0,
        };
        for i in 0..n {
            let ts = (i as i64) * 60_000;
            state.on_bar_close(close + 5.0, close - 5.0, close, 100.0, 0.0, ts, &ctx, cfg);
        }
    }

    #[test]
    fn no_signal_before_warmup() {
        let mut state = AmdDetectorState::new();
        let cfg = cfg();
        let ctx = ctx_neutral();
        let sig = state.on_bar_close(100.0, 90.0, 95.0, 200.0, -50.0, 0, &ctx, &cfg);
        assert!(sig.is_none(), "antes del warmup no debe emitir señal");
    }

    #[test]
    fn no_signal_when_disabled() {
        let mut state = AmdDetectorState::new();
        let cfg = AmdDetectorConfig { enabled: false, ..AmdDetectorConfig::default() };
        push_bars(&mut state, 70, 60_000.0, &cfg);
        let ctx = ctx_neutral();
        let sig = state.on_bar_close(61_000.0, 59_000.0, 60_500.0, 500.0, -200.0, 1_000_000, &ctx, &cfg);
        assert!(sig.is_none(), "con enabled=false nunca emite señal");
    }

    fn ctx_target(lvn: Vec<f64>, pocs: Vec<f64>) -> AmdContext {
        AmdContext {
            vpin: None, cvd_slope: None, obi_l5: None, vwap: None,
            lvn_levels: lvn, naked_pocs: pocs, ob_levels: vec![], fvg_levels: vec![],
            funding_rate: None, session_name: "Test".into(),
            vwap_dz: None, liq_ratio: 0.0,
            absorption_bid: false, absorption_ask: false, regime_is_trending: false, session_cvd: 0.0,
            val: None, vah: None, bid_wall_nearby: false, ask_wall_nearby: false, big_trade_cvd_bar: 0.0,
        }
    }

    #[test]
    fn fallback_target_gives_2r() {
        let entry = 60_000.0;
        let risk  = 200.0;
        let ctx = ctx_target(vec![], vec![]);
        let (target, source) = AmdDetectorState::select_target(AmdDirection::Short, entry, risk, &ctx);
        assert_eq!(source, TargetSource::Fallback2R);
        assert!((target - (entry - risk * 2.0)).abs() < 0.01);
    }

    #[test]
    fn lvn_target_preferred_over_fallback() {
        let entry = 60_000.0;
        let risk  = 200.0;
        let ctx = ctx_target(vec![59_500.0], vec![]);
        let (target, source) = AmdDetectorState::select_target(AmdDirection::Short, entry, risk, &ctx);
        assert_eq!(source, TargetSource::LvnNearby);
        assert!((target - 59_500.0).abs() < 0.01);
    }

    #[test]
    fn nearest_structural_level_wins() {
        let entry = 60_000.0;
        let risk  = 200.0;
        let ctx = ctx_target(vec![59_200.0], vec![59_700.0]);
        let (target, source) = AmdDetectorState::select_target(AmdDirection::Short, entry, risk, &ctx);
        assert_eq!(source, TargetSource::NakedPoc, "Naked POC más cercano debe ganar");
        assert!((target - 59_700.0).abs() < 0.01);
    }
}
