//! compute_levels — port directo de live/levels.py
//! Misma lógica, mismos parámetros, mismas salidas. Paridad con el backtest garantizada.

use std::collections::HashMap;
use std::sync::OnceLock;

// ── Parámetros (idénticos a levels.py) ─────────────────────────────────────

/// Ancho de bin del footprint (en precio). Se fija UNA vez al arrancar con
/// `set_bin()` (env FP_BIN o proporcional al precio). Default 5.0 si no se fija
/// — apropiado para BTC. CLAVE para ETH/SOL: con $5 fijo, un precio de $69 (SOL)
/// colapsa toda la barra en 1 bin → POC inútil. Debe escalar con el precio.
static BIN_WIDTH: OnceLock<f64> = OnceLock::new();
pub fn bin() -> f64 { *BIN_WIDTH.get().unwrap_or(&5.0) }
pub fn set_bin(w: f64) { let _ = BIN_WIDTH.set(w); }
pub const VA_BARS: usize = 96;   // ventana area de valor (96 × M15 = 1 día)
pub const SWING: usize   = 50;   // lookback swing H/L
pub const OB_WIN: usize  = 15;   // ventana order block
pub const MIN_RR: f64    = 1.2;
pub const MIN_TP1_RR: f64 = 2.3;
pub const STOP_FLOOR: f64 = 0.0015; // 0.15% mínimo: evita stops absurdamente ajustados (paridad con backtest)
pub const COOLDOWN_BARS: i64 = 6;   // anti-spam: barras entre entradas (paridad con backtest)
pub const MAX_TRADES_DAY: u32 = 2;  // anti-spam: máx entradas por día UTC
pub const TRAIL_ATR: f64  = 4.0;
pub const ATR_N: usize    = 14;
pub const TOUCH_TOL: f64  = 0.002; // 0.2% tolerancia para contar toques

// ── Tipos ───────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VolRegime { High, Low }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MarketRegime { Chop, Trend }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side { Long, Short }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Gestion { Fade, Trail }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum System { Maker, Flow }

// ── Conversiones str ⇄ enum (para persistir/restaurar posiciones abiertas) ──

impl Side {
    pub fn as_str(self) -> &'static str { if self == Side::Long { "long" } else { "short" } }
    pub fn from_str(s: &str) -> Side { if s == "short" { Side::Short } else { Side::Long } }
}
impl VolRegime {
    pub fn as_str(self) -> &'static str { if self == VolRegime::High { "high" } else { "low" } }
    pub fn from_str(s: &str) -> VolRegime { if s == "high" { VolRegime::High } else { VolRegime::Low } }
}
impl MarketRegime {
    pub fn as_str(self) -> &'static str { if self == MarketRegime::Trend { "trend" } else { "chop" } }
    pub fn from_str(s: &str) -> MarketRegime { if s == "trend" { MarketRegime::Trend } else { MarketRegime::Chop } }
}
impl Gestion {
    pub fn as_str(self) -> &'static str { if self == Gestion::Fade { "fade" } else { "trail" } }
    pub fn from_str(s: &str) -> Gestion { if s == "trail" { Gestion::Trail } else { Gestion::Fade } }
}

/// Mapea un kind de DB al &'static str canónico (los únicos generados por compute_levels).
pub fn kind_from_str(s: &str) -> &'static str {
    match s {
        "poc_def"       => "poc_def",
        "poc_def_short" => "poc_def_short",
        _               => "poc_ob",
    }
}
/// fp_source canónico.
pub fn fp_source_from_str(s: &str) -> &'static str {
    if s == "tick" { "tick" } else { "ohlcv" }
}

#[derive(Debug, Clone)]
pub struct Level {
    pub side: Side,
    pub kind: &'static str,
    pub price: f64,
    pub stop: f64,
    pub tp1: Option<f64>,
    pub tp: f64,
    pub vol_regime: VolRegime,
    pub regime: MarketRegime,
    pub gestion: Gestion,
    pub atr: f64,
    pub atr_median: f64,
    pub take_partial: bool,
    pub fp_source: &'static str,
}

/// Barra cerrada con footprint acumulado desde ticks
#[derive(Debug, Clone)]
pub struct ClosedBar {
    pub ts_ms: i64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
    pub day_id: i64,          // ts_ms / 86_400_000  (para PDH/PDL/semanal)
    /// bin_price_u32 → (buy_vol, sell_vol). Vacío para barras bootstrappeadas.
    pub fp: HashMap<u32, (f64, f64)>,
    /// POC real (ticks) si fp no está vacío, o (high+low)/2 si viene de bootstrap.
    pub poc: f64,
    pub fp_real: bool,
}

impl ClosedBar {
    pub fn from_bootstrap(ts_ms: i64, open: f64, high: f64, low: f64, close: f64, vol: f64) -> Self {
        Self {
            ts_ms, open, high, low, close, volume: vol,
            day_id: ts_ms / 86_400_000,
            fp: HashMap::new(),
            poc: (high + low) / 2.0,
            fp_real: false,
        }
    }

    pub fn from_live(ts_ms: i64, open: f64, high: f64, low: f64, close: f64, vol: f64,
                     fp: HashMap<u32, (f64, f64)>) -> Self {
        let poc = if fp.is_empty() {
            (high + low) / 2.0
        } else {
            let best = fp.iter().max_by(|a, b| {
                let va = a.1.0 + a.1.1;
                let vb = b.1.0 + b.1.1;
                va.partial_cmp(&vb).unwrap()
            });
            best.map(|(p, _)| *p as f64 * bin()).unwrap_or((high + low) / 2.0)
        };
        let fp_real = !fp.is_empty();
        Self { ts_ms, open, high, low, close, volume: vol, day_id: ts_ms / 86_400_000,
               fp, poc, fp_real }
    }
}

// ── ATR incremental (Wilder) ────────────────────────────────────────────────

/// Actualiza el ATR de Wilder dado el ATR previo y una nueva barra.
/// `prev_close` es el close de la barra anterior.
pub fn update_atr(prev_atr: f64, bar: &ClosedBar, prev_close: f64, n: usize) -> f64 {
    let tr = (bar.high - bar.low)
        .max((bar.high - prev_close).abs())
        .max((bar.low  - prev_close).abs());
    if prev_atr == 0.0 {
        tr
    } else {
        let k = 1.0 / n as f64;
        prev_atr * (1.0 - k) + tr * k
    }
}

// ── Value area desde footprints ─────────────────────────────────────────────

struct VArea { poc: f64, vah: f64, val: f64 }

fn value_area(bars: &[ClosedBar]) -> Option<VArea> {
    let n = bars.len().min(VA_BARS);
    if n < 4 { return None; }
    let recent = &bars[bars.len() - n..];

    // Aggregate bins: si hay footprint real lo usa, si no usa close×volume
    let mut vol: HashMap<u32, f64> = HashMap::new();
    let has_fp = recent.iter().any(|b| b.fp_real);

    if has_fp {
        for bar in recent.iter().filter(|b| b.fp_real) {
            for (&bin, &(buy, sell)) in &bar.fp {
                *vol.entry(bin).or_insert(0.0) += buy + sell;
            }
        }
        // Para barras sin tick (bootstrap) fallback a close×volume
        for bar in recent.iter().filter(|b| !b.fp_real) {
            let bin = (bar.close / bin()).round() as u32;
            *vol.entry(bin).or_insert(0.0) += bar.volume;
        }
    } else {
        for bar in recent {
            let bin = (bar.close / bin()).round() as u32;
            *vol.entry(bin).or_insert(0.0) += bar.volume;
        }
    }

    if vol.is_empty() { return None; }

    let mut bins: Vec<(u32, f64)> = vol.into_iter().collect();
    bins.sort_by_key(|&(p, _)| p);

    let (poc_bin, _) = bins.iter().max_by(|a, b| a.1.partial_cmp(&b.1).unwrap())?;
    let poc = *poc_bin as f64 * bin();

    let total: f64 = bins.iter().map(|(_, v)| v).sum();
    let mut sorted = bins.clone();
    sorted.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
    let mut cum = 0.0;
    let mut sel: Vec<u32> = Vec::new();
    for (bin, v) in &sorted {
        sel.push(*bin);
        cum += v;
        if cum >= 0.70 * total { break; }
    }
    let vah = *sel.iter().max()? as f64 * bin();
    let val = *sel.iter().min()? as f64 * bin();

    Some(VArea { poc, vah, val })
}

// ── Helpers estructurales ───────────────────────────────────────────────────

fn swing_hl(bars: &[ClosedBar]) -> (f64, f64) {
    let n = bars.len().saturating_sub(1).min(SWING);
    let w = &bars[bars.len().saturating_sub(n + 1)..bars.len().saturating_sub(1)];
    let sh = w.iter().map(|b| b.high).fold(f64::NEG_INFINITY, f64::max);
    let sl = w.iter().map(|b| b.low ).fold(f64::INFINITY,     f64::min);
    (sh, sl)
}

fn prev_day_hl(bars: &[ClosedBar]) -> (f64, f64) {
    if bars.is_empty() { return (f64::NAN, f64::NAN); }
    let today = bars.last().unwrap().day_id;
    let prev: Vec<&ClosedBar> = bars.iter().filter(|b| b.day_id == today - 1).collect();
    if prev.is_empty() { return (f64::NAN, f64::NAN); }
    let h = prev.iter().map(|b| b.high).fold(f64::NEG_INFINITY, f64::max);
    let l = prev.iter().map(|b| b.low ).fold(f64::INFINITY,     f64::min);
    (h, l)
}

fn weekly_hl(bars: &[ClosedBar]) -> (f64, f64) {
    if bars.is_empty() { return (f64::NAN, f64::NAN); }
    let today = bars.last().unwrap().day_id;
    let wk: Vec<&ClosedBar> = bars.iter()
        .filter(|b| b.day_id >= today - 8 && b.day_id < today).collect();
    if wk.is_empty() { return (f64::NAN, f64::NAN); }
    let h = wk.iter().map(|b| b.high).fold(f64::NEG_INFINITY, f64::max);
    let l = wk.iter().map(|b| b.low ).fold(f64::INFINITY,     f64::min);
    (h, l)
}

/// Target estructural: (tp1, tp2) = (nivel cercano, nivel lejano).
fn struct_target(side: Side, entry: f64, va: &VArea,
                 sh: f64, sl: f64, pdh: f64, pdl: f64, wh: f64, wl: f64)
    -> Option<(Option<f64>, f64)>
{
    let is_finite = |v: f64| v.is_finite();
    let (tp1, tp2) = match side {
        Side::Long => {
            let cands: Vec<f64> = [va.vah, sh, pdh, wh].iter()
                .copied().filter(|&v| is_finite(v) && v > entry * 1.001).collect();
            if cands.is_empty() { return None; }
            let tp2 = cands.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            let tp1 = cands.iter().copied().fold(f64::INFINITY, f64::min);
            (tp1, tp2)
        }
        Side::Short => {
            let cands: Vec<f64> = [va.val, sl, pdl, wl].iter()
                .copied().filter(|&v| is_finite(v) && v < entry * 0.999).collect();
            if cands.is_empty() { return None; }
            let tp2 = cands.iter().copied().fold(f64::INFINITY, f64::min);
            let tp1 = cands.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            (tp1, tp2)
        }
    };
    // tp1 == tp2 when solo hay un nivel → devolver None para tp1
    let tp1_out = if (tp1 - tp2).abs() < 1.0 { None } else { Some(tp1) };
    Some((tp1_out, tp2))
}

// ── Regime de mercado (chop vs tendencia) ───────────────────────────────────

fn is_trend(bars: &[ClosedBar], atr: f64) -> bool {
    let n = 50;
    if bars.len() < n { return false; }
    let sma: f64 = bars.iter().rev().take(n).map(|b| b.close).sum::<f64>() / n as f64;
    let price = bars.last().unwrap().close;
    (price - sma).abs() > 0.6 * atr
}

// ── compute_levels ──────────────────────────────────────────────────────────

pub fn compute_levels(
    bars: &[ClosedBar],
    current_atr: f64,
    atr_median: f64,
    system: System,
    high_vol_only: bool,
    disable_h5: bool,
    tp2_cap_r: f64,
) -> Vec<Level> {
    if bars.len() < 300 { return vec![]; }

    let price = bars.last().unwrap().close;
    let atr = current_atr;
    if atr <= 0.0 { return vec![]; }

    // Régimen de volatilidad
    let vol_regime = if atr_median > 0.0 && atr > atr_median {
        VolRegime::High
    } else {
        VolRegime::Low
    };
    if high_vol_only && vol_regime != VolRegime::High { return vec![]; }

    // Régimen de mercado (para FLOW)
    let trend = system == System::Flow && is_trend(bars, atr);
    let regime = if trend { MarketRegime::Trend } else { MarketRegime::Chop };
    let fp_source = if bars.iter().rev().take(VA_BARS).any(|b| b.fp_real) { "tick" } else { "ohlcv" };

    // Niveles estructurales
    let va = match value_area(bars) { Some(v) => v, None => return vec![] };
    let (sh, sl) = swing_hl(bars);
    let (pdh, pdl) = prev_day_hl(bars);
    let (wh, wl)   = weekly_hl(bars);

    let mut out: Vec<Level> = Vec::new();

    // ── POC del Order Block (vela de mayor rango en OB_WIN) ──────────────
    if !disable_h5 {
        let ob_slice = &bars[bars.len().saturating_sub(OB_WIN)..];
        if let Some(obi) = ob_slice.iter()
            .enumerate()
            .max_by(|a, b| (a.1.high - a.1.low).partial_cmp(&(b.1.high - b.1.low)).unwrap())
            .map(|(i, _)| i)
        {
            let ob = &ob_slice[obi];
            let (obh, obl) = (ob.high, ob.low);
            let obpoc = ob.poc;  // real si fp_real, midpoint si bootstrap

            for (side, stop) in [
                (Side::Long,  obl - 0.25 * atr),
                (Side::Short, obh + 0.25 * atr),
            ] {
                if let Some((tp1, tp)) = struct_target(side, obpoc, &va, sh, sl, pdh, pdl, wh, wl) {
                    out.push(Level { side, kind: "poc_ob", price: obpoc, stop, tp1, tp,
                                      vol_regime, regime, gestion: Gestion::Fade,
                                      atr, atr_median, take_partial: false, fp_source });
                }
            }
        }
    }

    // ── POC defendido (soporte tocado >= 2 veces por mínimos) → long ─────
    let defended = va.poc;
    let recent_ob = &bars[bars.len().saturating_sub(OB_WIN)..];
    let touches_low = recent_ob.iter()
        .filter(|b| (b.low - defended).abs() / defended <= TOUCH_TOL)
        .count();
    if touches_low >= 2 {
        if let Some((tp1, tp)) = struct_target(Side::Long, defended, &va, sh, sl, pdh, pdl, wh, wl) {
            out.push(Level { side: Side::Long, kind: "poc_def", price: defended,
                              stop: defended - 0.6 * atr, tp1, tp,
                              vol_regime, regime, gestion: Gestion::Fade,
                              atr, atr_median, take_partial: false, fp_source });
        }
    }

    // ── Mirror short (resistencia tocada >= 2 veces por máximos) → short ─
    let touches_hi = recent_ob.iter()
        .filter(|b| (b.high - defended).abs() / defended <= TOUCH_TOL)
        .count();
    if touches_hi >= 2 {
        if let Some((tp1, tp)) = struct_target(Side::Short, defended, &va, sh, sl, pdh, pdl, wh, wl) {
            out.push(Level { side: Side::Short, kind: "poc_def_short", price: defended,
                              stop: defended + 0.6 * atr, tp1, tp,
                              vol_regime, regime, gestion: Gestion::Fade,
                              atr, atr_median, take_partial: false, fp_source });
        }
    }

    // ── Filtros finales ──────────────────────────────────────────────────
    let gestion = if trend { Gestion::Trail } else { Gestion::Fade };

    let filtered: Vec<Level> = out.into_iter().filter_map(|mut lv| {
        // Stop floor: empuja el stop a >= STOP_FLOOR del precio si quedó demasiado ajustado.
        let min_dist = lv.price * STOP_FLOOR;
        if (lv.price - lv.stop).abs() < min_dist {
            lv.stop = match lv.side {
                Side::Long  => lv.price - min_dist,
                Side::Short => lv.price + min_dist,
            };
        }
        let risk = (lv.price - lv.stop).abs();
        if risk <= 0.0 { return None; }
        // Cap tp2 a N×risk si tp2_cap_r > 0
        if tp2_cap_r > 0.0 {
            let cap = match lv.side {
                Side::Long  => lv.price + tp2_cap_r * risk,
                Side::Short => lv.price - tp2_cap_r * risk,
            };
            let beyond = match lv.side {
                Side::Long  => lv.tp > cap,
                Side::Short => lv.tp < cap,
            };
            if beyond {
                lv.tp = cap;
                // anular tp1 si queda fuera del rango [price, nuevo tp]
                if let Some(t1) = lv.tp1 {
                    let in_range = match lv.side {
                        Side::Long  => t1 > lv.price && t1 < lv.tp,
                        Side::Short => t1 < lv.price && t1 > lv.tp,
                    };
                    if !in_range { lv.tp1 = None; }
                }
            }
        }
        if (lv.tp - lv.price).abs() / risk < MIN_RR { return None; }
        match lv.side {
            Side::Long  if !(lv.price < price && lv.stop < lv.price && lv.price < lv.tp) => return None,
            Side::Short if !(lv.price > price && lv.tp < lv.price && lv.price < lv.stop) => return None,
            _ => {}
        }
        let tp1_rr = lv.tp1.map(|t| (t - lv.price).abs() / risk).unwrap_or(0.0);
        lv.take_partial = tp1_rr >= MIN_TP1_RR;
        lv.gestion = gestion;
        Some(lv)
    }).collect();

    // Dedup: si dos niveles tienen el mismo lado y precio dentro de $2, conservar solo el primero
    let mut seen: Vec<(Side, u32)> = Vec::new();
    filtered.into_iter().filter(|lv| {
        let key = (lv.side, (lv.price / 2.0).round() as u32);
        if seen.contains(&key) { false } else { seen.push(key); true }
    }).collect()
}
