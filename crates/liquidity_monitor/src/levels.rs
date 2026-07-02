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

/// Fade-only (env FORCE_FADE, default true): toda gestión = Fade, ignorando el detector
/// de régimen. Motivo: el detector vivo rutea ~52% a trail (validado: 7-13%) y fade-only
/// pasa la regla dura OOS en los 3 activos (BTC +1.24 / ETH +1.46 / SOL +0.76) con menos DD.
/// El campo `regime` se sigue persistiendo con lo que dice el detector, para poder auditarlo.
static FORCE_FADE: OnceLock<bool> = OnceLock::new();
pub fn force_fade() -> bool { *FORCE_FADE.get().unwrap_or(&true) }
pub fn set_force_fade(v: bool) { let _ = FORCE_FADE.set(v); }
pub const VA_BARS: usize = 96;   // ventana area de valor (96 × M15 = 1 día)
pub const SWING: usize   = 50;   // lookback swing H/L
pub const OB_WIN: usize  = 15;   // ventana order block
pub const MIN_RR: f64    = 1.2;
pub const MIN_RANGE: f64 = 0.5;     // % mínimo entry→tp1 para fadear (no fadear migajas, paridad backtest)
pub const STOP_FLOOR: f64 = 0.0015; // 0.15% mínimo: evita stops absurdamente ajustados (paridad con backtest)
pub const COOLDOWN_BARS: i64 = 6;   // anti-spam: barras entre entradas (paridad con backtest)
pub const MAX_TRADES_DAY: u32 = 2;  // anti-spam: máx entradas por día UTC
pub const TRAIL_ATR: f64  = 6.0;    // trail trend: 6×ATR (optim validada 365d/3 activos, slippage-robusta)
pub const STOP_SCALE: f64 = 0.8;    // stops 0.8× (optim validada: stop más chico → mayor R en target estructural)
pub const ATR_N: usize    = 14;
pub const TOUCH_TOL: f64  = 0.002; // 0.2% tolerancia para contar toques
// Detector de régimen: EMA5 (75min) + racha 2 sobre M15.
// El parquet usa EMA20 de datos M1 (que el Rust no tiene). Con EMA20 sobre M15 se sobre-detecta
// trend (39% vs 7.5% parquet). EMA5+2 da 81% acuerdo con el parquet. Fix real para tendencias
// graduales = filtro H1 slope (ya deployado): bloquea longs/shorts cuando H1 no está alineado.
pub const REGIME_EMA: usize   = 5;
pub const REGIME_STREAK: i32  = 2;
pub const REGIME_EXP: f64     = 1.30;   // ATR actual > 1.3× su MA20 → expansión (tendencia)
// Filtros validados 2026-06-29 (OOS +1.97/+1.91/+1.87 vs base +1.0, regla dura 3 activos)
pub const H1_BARS: usize   = 4;     // 4×M15 = 1H — contexto horario
pub const DIST_MIN: f64    = 0.5;   // dist(close, level) >= 0.5×ATR — llegada limpia
// IFVG (Inverse Fair Value Gap) — validado OOS +0.30/+0.33/+0.50 standalone, regla dura 3 activos
pub const IFVG_K: usize    = 60;    // barras M15 de lookback para buscar IFVGs activos
pub const IFVG_TOL: f64    = 0.0015; // 0.15% tolerancia de toque al nivel
pub const IFVG_MIN_GAP: f64 = 0.15; // mínimo tamaño del gap en fracción de ATR
// S/R estructurales como entradas (weekly H/L + round numbers) — validados OOS 3 activos
pub const SR_STOP_FRAC: f64   = 0.5;    // stop = nivel ± 0.5×ATR
pub const SR_ENTRY_TOL: f64   = 0.001;  // entrada 0.1% por encima/debajo del nivel
pub const SR_TOUCH_TOL: f64   = 0.002;  // tolerancia de toque para contar virgin
pub const SR_LOOKBACK: usize  = 20;     // barras M15 (5h) para virgin check

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
        "ifvg_bull"     => "ifvg_bull",
        "ifvg_bear"     => "ifvg_bear",
        "weekly_l"      => "weekly_l",
        "weekly_h"      => "weekly_h",
        "round_l"       => "round_l",
        "round_h"       => "round_h",
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

fn nearest_round(price: f64, mult: f64) -> f64 {
    (price / mult).round() * mult
}

fn sr_touches(bars: &[ClosedBar], lvl: f64, lookback: usize) -> usize {
    let n = bars.len();
    let start = n.saturating_sub(lookback + 1);
    bars[start..n.saturating_sub(1)].iter().filter(|b| {
        (b.low  - lvl).abs() / lvl <= SR_TOUCH_TOL ||
        (b.high - lvl).abs() / lvl <= SR_TOUCH_TOL
    }).count()
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

/// Régimen de mercado — port de compute_regime validado en M15 (EMA corta + racha 2).
/// trend (→ TRAIL) si: ATR expande >1.3× su MA20, o el precio lleva 2+ barras consecutivas
/// del mismo lado de la EMA5. Si no, chop (→ FADE). Reemplaza el viejo |precio-sma50|>0.6·atr.
// ── Señales IFVG ────────────────────────────────────────────────────────────
// Detecta FVGs llenados en el buffer y genera niveles de retest.
// Bull FVG en barra j: bars[j].low > bars[j-2].high  → gap alcista
//   Fill: algún bar k>j con close < bars[j-2].high
//   IFVG_bear: retest desde abajo → SHORT en bars[j-2].high
// Bear FVG en barra j: bars[j].high < bars[j-2].low  → gap bajista
//   Fill: algún bar k>j con close > bars[j-2].low
//   IFVG_bull: retest desde arriba → LONG en bars[j-2].low
fn ifvg_levels(
    bars: &[ClosedBar], atr: f64, price: f64,
    va: &VArea, sh: f64, sl: f64, pdh: f64, pdl: f64, wh: f64, wl: f64,
    vol_regime: VolRegime, regime: MarketRegime, atr_median: f64,
) -> Vec<Level> {
    let n = bars.len();
    if n < IFVG_K + 4 { return vec![]; }
    let cur = &bars[n - 1];
    let mut out = Vec::new();
    let start = n.saturating_sub(IFVG_K + 2);

    // ── Buscar bearish IFVG (ex-bull FVG llenado → resistencia) ─────────────
    'bull_loop: for j in (start + 2)..(n - 2) {
        let fvg_bot = bars[j - 2].high;
        let fvg_top = bars[j].low;
        let gap = fvg_top - fvg_bot;
        if gap < IFVG_MIN_GAP * atr { continue; }
        // ¿Se llenó después de j y antes del bar actual?
        let filled = bars[(j + 1)..(n - 1)].iter().any(|b| b.close < fvg_bot);
        if !filled { continue; }
        // Retest: high[cur] toca el nivel y close queda debajo
        if cur.high >= fvg_bot * (1.0 - IFVG_TOL) && cur.close < fvg_bot {
            let stop = fvg_top + 0.25 * atr;
            if let Some((tp1, tp)) = struct_target(Side::Short, fvg_bot, va, sh, sl, pdh, pdl, wh, wl) {
                out.push(Level { side: Side::Short, kind: "ifvg_bear", price: fvg_bot,
                                  stop, tp1, tp, vol_regime, regime,
                                  gestion: Gestion::Fade, atr, atr_median,
                                  take_partial: false, fp_source: "ohlcv" });
                break 'bull_loop; // solo el IFVG más reciente
            }
        }
    }

    // ── Buscar bullish IFVG (ex-bear FVG llenado → soporte) ─────────────────
    'bear_loop: for j in (start + 2)..(n - 2) {
        let fvg_top = bars[j - 2].low;
        let fvg_bot = bars[j].high;
        let gap = fvg_top - fvg_bot;
        if gap < IFVG_MIN_GAP * atr { continue; }
        let filled = bars[(j + 1)..(n - 1)].iter().any(|b| b.close > fvg_top);
        if !filled { continue; }
        if cur.low <= fvg_top * (1.0 + IFVG_TOL) && cur.close > fvg_top {
            let stop = fvg_bot - 0.25 * atr;
            if let Some((tp1, tp)) = struct_target(Side::Long, fvg_top, va, sh, sl, pdh, pdl, wh, wl) {
                out.push(Level { side: Side::Long, kind: "ifvg_bull", price: fvg_top,
                                  stop, tp1, tp, vol_regime, regime,
                                  gestion: Gestion::Fade, atr, atr_median,
                                  take_partial: false, fp_source: "ohlcv" });
                break 'bear_loop;
            }
        }
    }

    // Aplicar H1 slope + dist al igual que las señales base
    let h1_close = if n > H1_BARS { bars[n - 1 - H1_BARS].close } else { price };
    out.into_iter().filter(|lv| {
        let slope_ok = match lv.side {
            Side::Long  => price > h1_close,
            Side::Short => price < h1_close,
        };
        let dist_ok = (price - lv.price).abs() >= DIST_MIN * atr;
        slope_ok && dist_ok
    }).collect()
}

fn is_trend(bars: &[ClosedBar], cur_atr: f64, atr_ma: f64) -> bool {
    let n = bars.len();
    if n < 25 { return false; }
    if atr_ma > 0.0 && cur_atr / atr_ma > REGIME_EXP { return true; }   // expansión (prioridad)
    let start = n.saturating_sub(60);                                    // warmup EMA
    let k = 2.0 / (REGIME_EMA as f64 + 1.0);
    let mut ema = bars[start].close;
    let mut ema_at = vec![0.0_f64; n - start];
    for i in start..n {
        ema = bars[i].close * k + ema * (1.0 - k);
        ema_at[i - start] = ema;
    }
    let bull = |i: usize| bars[i].close > ema_at[i - start] * 1.002;
    let bear = |i: usize| bars[i].close < ema_at[i - start] * 0.998;
    let mut bs = 0i32; let mut rs = 0i32;
    for i in (start..n).rev() { if bull(i) { bs += 1; } else { break; } }
    for i in (start..n).rev() { if bear(i) { rs += 1; } else { break; } }
    bs >= REGIME_STREAK || rs >= REGIME_STREAK
}

// ── compute_levels ──────────────────────────────────────────────────────────

pub fn compute_levels(
    bars: &[ClosedBar],
    current_atr: f64,
    atr_median: f64,
    atr_ma: f64,
    _system: System,
    high_vol_only: bool,
    disable_h5: bool,
    tp2_cap_r: f64,
    round_mults: &[f64],
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

    // Régimen de mercado — aplica a AMBOS sistemas (paridad backtest mode='routed')
    let trend = is_trend(bars, atr, atr_ma);
    let regime = if trend { MarketRegime::Trend } else { MarketRegime::Chop };
    let fp_source = if bars.iter().rev().take(VA_BARS).any(|b| b.fp_real) { "tick" } else { "ohlcv" };

    // Niveles estructurales
    let va = match value_area(bars) { Some(v) => v, None => return vec![] };
    let (sh, sl) = swing_hl(bars);
    let (pdh, pdl) = prev_day_hl(bars);
    let (wh, wl)   = weekly_hl(bars);

    let mut out: Vec<Level> = Vec::new();

    // ── Filtros contextuales (validados 2026-06-29, regla dura 3 activos) ──
    let n = bars.len();
    // H1 slope: close actual vs close de hace 4 barras M15 (= 1H real)
    let h1_close = if n > H1_BARS { bars[n - 1 - H1_BARS].close } else { price };
    let h1_up    = price > h1_close;   // tendencia H1 alcista
    let h1_dn    = price < h1_close;   // tendencia H1 bajista
    // dist: precio debe estar a >= DIST_MIN×ATR del nivel (llegada limpia)
    let dist_ok  = |lvl: f64| (price - lvl).abs() >= DIST_MIN * atr;

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

            for (side, stop, slope_ok) in [
                (Side::Long,  obl - 0.25 * STOP_SCALE * atr, h1_up),
                (Side::Short, obh + 0.25 * STOP_SCALE * atr, h1_dn),
            ] {
                if !slope_ok || !dist_ok(obpoc) { continue; }
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
    if touches_low >= 2 && h1_up && dist_ok(defended) {
        if let Some((tp1, tp)) = struct_target(Side::Long, defended, &va, sh, sl, pdh, pdl, wh, wl) {
            out.push(Level { side: Side::Long, kind: "poc_def", price: defended,
                              stop: defended - 0.6 * STOP_SCALE * atr, tp1, tp,
                              vol_regime, regime, gestion: Gestion::Fade,
                              atr, atr_median, take_partial: false, fp_source });
        }
    }

    // ── Mirror short (resistencia tocada >= 2 veces por máximos) → short ─
    let touches_hi = recent_ob.iter()
        .filter(|b| (b.high - defended).abs() / defended <= TOUCH_TOL)
        .count();
    if touches_hi >= 2 && h1_dn && dist_ok(defended) {
        if let Some((tp1, tp)) = struct_target(Side::Short, defended, &va, sh, sl, pdh, pdl, wh, wl) {
            out.push(Level { side: Side::Short, kind: "poc_def_short", price: defended,
                              stop: defended + 0.6 * STOP_SCALE * atr, tp1, tp,
                              vol_regime, regime, gestion: Gestion::Fade,
                              atr, atr_median, take_partial: false, fp_source });
        }
    }

    // ── IFVG (Inverse Fair Value Gap) ────────────────────────────────────
    out.extend(ifvg_levels(bars, atr, price, &va, sh, sl, pdh, pdl, wh, wl,
                            vol_regime, regime, atr_median));

    // ── Weekly H/L como entrada directa (validado OOS +1.95R, 3 activos) ─
    let cur = bars.last().unwrap();
    // Long en weekly_low: precio toca desde arriba, cierra por encima
    if wl.is_finite() && wl > 0.0 {
        let entry = wl * (1.0 + SR_ENTRY_TOL);
        if cur.low <= entry && cur.close > wl {
            let stop = entry - SR_STOP_FRAC * atr;
            let risk = entry - stop;
            if risk > 0.0 {
                if let Some((tp1, tp)) = struct_target(Side::Long, entry, &va, sh, sl, pdh, pdl, wh, wl) {
                    if (tp - entry) / risk >= MIN_RR {
                        out.push(Level { side: Side::Long, kind: "weekly_l", price: entry,
                                          stop, tp1, tp, vol_regime, regime,
                                          gestion: Gestion::Fade, atr, atr_median,
                                          take_partial: false, fp_source });
                    }
                }
            }
        }
    }
    // Short en weekly_high: precio toca desde abajo, cierra por debajo
    if wh.is_finite() && wh > 0.0 {
        let entry = wh * (1.0 - SR_ENTRY_TOL);
        if cur.high >= entry && cur.close < wh {
            let stop = entry + SR_STOP_FRAC * atr;
            let risk = stop - entry;
            if risk > 0.0 {
                if let Some((tp1, tp)) = struct_target(Side::Short, entry, &va, sh, sl, pdh, pdl, wh, wl) {
                    if (entry - tp) / risk >= MIN_RR {
                        out.push(Level { side: Side::Short, kind: "weekly_h", price: entry,
                                          stop, tp1, tp, vol_regime, regime,
                                          gestion: Gestion::Fade, atr, atr_median,
                                          take_partial: false, fp_source });
                    }
                }
            }
        }
    }

    // ── Round numbers virgin (validado OOS +2.45R, 3 activos) ────────────
    for &mult in round_mults {
        let nr = nearest_round(price, mult);
        if nr <= 0.0 { continue; }
        // Virgin: 0 toques en las últimas SR_LOOKBACK barras
        if sr_touches(bars, nr, SR_LOOKBACK) > 0 { continue; }
        if price > nr {
            // Long: precio sobre el round, low toca el nivel
            if cur.low <= nr * (1.0 + SR_TOUCH_TOL) && cur.close > nr {
                let stop = nr - SR_STOP_FRAC * atr;
                let risk = nr - stop;
                if risk > 0.0 {
                    if let Some((tp1, tp)) = struct_target(Side::Long, nr, &va, sh, sl, pdh, pdl, wh, wl) {
                        if (tp - nr) / risk >= MIN_RR {
                            out.push(Level { side: Side::Long, kind: "round_l", price: nr,
                                              stop, tp1, tp, vol_regime, regime,
                                              gestion: Gestion::Fade, atr, atr_median,
                                              take_partial: false, fp_source });
                        }
                    }
                }
            }
        } else {
            // Short: precio bajo el round, high toca el nivel
            if cur.high >= nr * (1.0 - SR_TOUCH_TOL) && cur.close < nr {
                let stop = nr + SR_STOP_FRAC * atr;
                let risk = stop - nr;
                if risk > 0.0 {
                    if let Some((tp1, tp)) = struct_target(Side::Short, nr, &va, sh, sl, pdh, pdl, wh, wl) {
                        if (nr - tp) / risk >= MIN_RR {
                            out.push(Level { side: Side::Short, kind: "round_h", price: nr,
                                              stop, tp1, tp, vol_regime, regime,
                                              gestion: Gestion::Fade, atr, atr_median,
                                              take_partial: false, fp_source });
                        }
                    }
                }
            }
        }
    }

    // ── Filtros finales ──────────────────────────────────────────────────
    let gestion = if trend && !force_fade() { Gestion::Trail } else { Gestion::Fade };

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
        // min_range: para fades, descartar si tp1 está demasiado cerca (<MIN_RANGE%) — no fadear migajas.
        if gestion == Gestion::Fade {
            if let Some(t1) = lv.tp1 {
                if (t1 - lv.price).abs() / lv.price * 100.0 < MIN_RANGE { return None; }
            }
        }
        // Parcial 50% siempre que exista un tp1 cercano válido (paridad con backtest, sin umbral de R).
        lv.take_partial = lv.tp1.is_some();
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

/// Diagnóstico: razón por la que compute_levels devolvería vacío.
/// Llamar SOLO para logging (no en el path caliente).
pub fn diag_block(bars: &[ClosedBar], atr: f64, atr_median: f64, high_vol_only: bool) -> &'static str {
    if bars.len() < 300        { return "WARMUP"; }
    if atr <= 0.0              { return "ATR_ZERO"; }
    let vol_ok = atr_median > 0.0 && atr > atr_median;
    if high_vol_only && !vol_ok { return "ATR_BLOCK"; }
    if value_area(bars).is_none() { return "NO_VA"; }
    "NO_LEVEL"
}
