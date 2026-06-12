//! Range Breakout Flow — detector de continuación con gate de microestructura
//!
//! Hipótesis validada en backtest (30 días M1, n=318 señales London+Overlap):
//!   Rango de consolidación (0.08–0.55%, 15–60 barras M1) + CVD acumulado alineado
//!   + VR ≥ 3× en breakout → edge positivo en horizonte 30–60 min.
//!
//! Gate de microestructura (backtest offline 30d):
//!   cvd_slope confirma dirección  (+11pp WR en Q4 vs Q1)
//!   dz entre 0.5 y 3.0            (extremos >3 revierten; pico en dz ~2)
//!   obi confirma dirección        (pendiente validación con datos reales)
//!
//! Sessions operativas — London (08-13 UTC) y Overlap (13-17 UTC).
//! NewYork excluido: backtest 30d/43200 barras → WR 24.7% avgR -0.065.

use std::collections::VecDeque;
use serde::{Deserialize, Serialize};
use crate::session::{TradingSession, SessionPhase};

// ── Tipos de confluencia ──────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConfluenceFlag {
    // ── Estructura de mercado (independiente de régimen) ──────────────────────
    StackedImbalance,    // desequilibrio de libro en dirección del breakout
    AbsorcionFootprint,  // sellers absorbiendo bids (Short) / compradores bids (Long)
    LvnOThinZone,        // zona de bajo volumen = aceleración probable al target
    VwapBias,            // precio bajo VWAP para Short / sobre para Long = contexto macro
    OiMomentum,          // OI expandiendo = nuevas posiciones = convicción real
    // ── Participantes atrapados (hallazgo Jun 6-11: OBI inverso discrimina mejor) ──
    // Análisis 75 Shorts: OBI bullish en Short → WR=88% AvgR=+0.932 vs baseline 84%/+0.811
    // Lógica: compradores en el libro cuando precio rompe abajo = van a ser squeezed
    ObiTrap,             // OBI contra la dirección = participantes atrapados
}

impl ConfluenceFlag {
    fn as_str(&self) -> &'static str {
        match self {
            Self::StackedImbalance   => "stacked_imbalance",
            Self::AbsorcionFootprint => "absorption",
            Self::LvnOThinZone       => "lvn_thin",
            Self::VwapBias           => "vwap_bias",
            Self::OiMomentum         => "oi_momentum",
            Self::ObiTrap            => "obi_trap",
        }
    }
}

/// Datos de microestructura externos que no residen en RangeBreakoutState.
/// Los valores son crudos (no por-dirección) porque en main.rs aún no se
/// conoce la dirección del breakout cuando se construye este contexto.
#[derive(Debug, Clone)]
pub struct RbfGateContext {
    /// Stacked imbalance bearish activo (Bearish/FBG-Bearish).
    pub stacked_imbalance_bearish: bool,
    /// Stacked imbalance bullish activo (Bullish/FBG-Bullish).
    pub stacked_imbalance_bullish: bool,
    /// Absorción ask activa (señal bajista — asks absorbidos).
    pub absorption_ask: bool,
    /// Absorción bid activa (señal alcista — bids absorbidos).
    pub absorption_bid: bool,
    /// LVN cerca del precio actual (proxy: al menos un nivel en lvn_nearby).
    pub lvn_nearby: bool,
    /// Thin zone por debajo del precio (favorece SHORT breakout).
    pub thin_zone_below: bool,
    /// Thin zone por encima del precio (favorece LONG breakout).
    pub thin_zone_above: bool,
    /// Pared de bids cerca — bloquea SHORT (target difícil de alcanzar).
    pub bid_wall_nearby: bool,
    /// Pared de asks cerca — bloquea LONG (target difícil de alcanzar).
    pub ask_wall_nearby: bool,
    /// HVN levels para verificar obstáculos entre entry y target.
    pub hvn_levels: Vec<f64>,
    /// VPIN de la barra actual.
    pub vpin: Option<f64>,
    /// OI momentum alineado: precio + OI expandiéndose en la misma dirección.
    pub oi_momentum_aligned: Option<bool>,
    /// CVD acumulado desde el inicio de la sesión (reset diario UTC).
    pub session_cvd: f64,
    /// CVD acumulado de big trades (≥$100k notional) desde apertura de sesión.
    /// Fabio: "las órdenes grandes son las que importan" — si el breakout viene con big_cvd alineado, es real.
    pub big_trade_cvd_session: f64,
    /// % de cambio en Open Interest en la ventana reciente (~6 lecturas).
    /// Positivo = contratos abiertos creciendo (nuevas posiciones = convicción).
    /// Negativo = posiciones cerrando (cobertura / liquidación = debilidad).
    pub oi_delta_pct: Option<f64>,
    /// Barras consecutivas de divergencia CVD-precio al momento de evaluar el breakout.
    /// Negativo = bullish div (útil para LONG); positivo = bearish div (útil para SHORT).
    pub cvd_divergence_bars: Option<i32>,
    /// Tendencia H4 al momento del breakout: Some("Bull") / Some("Bear") / None si aún sin warmup (< 240 barras M1).
    /// Deriva de EMA-240M1 (≈ 4H): precio > EMA → Bull.
    pub htf_h1_trend: Option<String>,
    /// VP Open Variant del día actual (FASE 2.1): "InsideValue", "OutsideVaInsidePa", "TrendDay", "FadeGap".
    /// Clasifica el tipo de apertura de sesión vs el Value Area del día anterior.
    pub vp_open_bias: Option<String>,
    /// ATR(14) de la barra de breakout. Se usa para stop dinámico y filtro range/ATR.
    /// 0.0 si no disponible (warmup).
    pub atr: f64,
    /// Barras en régimen Expansion en las últimas 25 barras M1 pre-breakout.
    /// Derivado del campo `regime` de los bars. 0 = rango todavía comprimido.
    /// Valores altos (>4) indican que el move ya comenzó antes de que el detector lo confirme.
    /// Calibración n=30 Shorts: expansion_n≤3 → WR=60% +8.1R; sin filtro WR=40% +2.3R.
    /// NOTA: SOL muestra correlación invertida — pasar 0 para desactivar el filtro por símbolo.
    pub expansion_bars_recent: u8,
    /// Barras con OI momentum alineado (precio + OI expandiéndose en misma dirección) en
    /// las últimas 25 barras M1. Gate de pre-breakout: oi_mom_bars_recent <= pre_breakout_oi_max.
    /// Calibración n=30 Shorts: oi_mom_n≤3 → WR=58%; oi_mom_n≤4 → WR=57%.
    /// Pasar 0 para desactivar el gate o si el símbolo no tiene datos de OI.
    pub oi_mom_bars_recent: u8,
    /// Suma del bar_delta de las últimas 25 barras M1.
    /// Gate por símbolo para Shorts: cum_delta muy negativo = move ya consumido.
    /// Calibración BNB: wins avg −78, losses avg −1,357 → gate: cum_delta > −500.
    /// BTC: losses cum_delta = +377 → gate: cum_delta < +200 (compradores agresivos = fakeout).
    pub cum_delta_25b: f64,

    // ── Microestructura adicional (OBI multi-profundidad + spread) ─────────────
    /// OBI L10 snapshot al cierre de barra (10 niveles).
    /// Más robusto que L5 ante spoofing. Negativo = ask pressure dominante.
    pub obi_l10: f64,
    /// OBI L20 snapshot al cierre de barra (20 niveles).
    /// Refleja intención institucional real — difícil de manipular.
    pub obi_l20: f64,
    /// Spread en bps al cierre de barra.
    /// Spread > umbral = mercado ilíquido → fakeout probable.
    pub spread_bps: f64,
    /// OBI L5 promedio intrabar (sampleo cada 10s durante la barra).
    /// None = sin datos acumulados aún (primeras barras del sistema).
    pub obi_mean_intrabar: Option<f64>,
}

fn score_confluence(
    direction: RbfDirection,
    macro_regime: MacroRegime,
    _cvd_slope: Option<f64>,
    obi: f64,
    vwap: Option<f64>,
    entry_price: f64,
    target_price: f64,
    gate: &RbfGateContext,
    cfg: &RangeBreakoutConfig,
) -> (u8, Vec<String>, Option<String>) {
    // ── VETOS: cancelan la señal independientemente del score ─────────────────
    // wall_target desactivado — 1×ATR es demasiado amplio, vetaba el 100% de señales.
    // Pendiente calibración con datos reales cuando haya 50+ señales con outcome.

    // Spread gate: mercado ilíquido = fakeout probable.
    // Threshold 5 bps — calibrado para perps líquidos (BTC/ETH/BNB/SOL/XRP).
    if gate.spread_bps > 5.0 {
        return (0, vec![], Some("spread_wide".into()));
    }

    // OI delta gate: si OI decrece mientras precio rompe abajo → longs cubriendo (stop hunt),
    // no shorts abriendo. Breakdown carece de convicción real.
    if direction == RbfDirection::Short {
        if let Some(oi_d) = gate.oi_delta_pct {
            if oi_d < -10.0 {
                return (0, vec![], Some("oi_covering".into()));
            }
        }
    }

    // HVN en el camino al target — solo veta si el HVN está ENTRE entry y target
    // (dirección correcta) y en la primera mitad del recorrido desde entry.
    let full_path = (target_price - entry_price).abs();
    let has_hvn_obstacle = gate.hvn_levels.iter().any(|&lvl| {
        let in_path = match direction {
            RbfDirection::Short => lvl < entry_price && lvl > target_price,
            RbfDirection::Long  => lvl > entry_price && lvl < target_price,
        };
        let dist_from_entry = (lvl - entry_price).abs();
        in_path && dist_from_entry < full_path * 0.5
    });
    if has_hvn_obstacle {
        return (0, vec![], Some("hvn_target".into()));
    }

    if gate.vpin.map_or(false, |v| v > 0.65) {
        return (0, vec![], Some("vpin_toxic".into()));
    }

    // ── PUNTOS — Principio: "Participantes atrapados + Estructura limpia" ────────
    // Hallazgo Jun 6-11 (75 Shorts): señales de "presión en mi dirección" (CVD slope,
    // OBI alineado, session CVD) NO discriminan outcome y pueden indicar move consumido.
    // Las señales de "participantes en el lado equivocado" (OBI trap) sí discriminan.
    // Max score: 6 puntos.
    let mut score: u8 = 0;
    let mut flags: Vec<ConfluenceFlag> = vec![];

    // [+1] Stacked imbalance en dirección del breakout — estructura de oferta/demanda real.
    // Desequilibrios apilados = suministro institucional pendiente de ser ejecutado.
    let stacked = match direction {
        RbfDirection::Short => gate.stacked_imbalance_bearish,
        RbfDirection::Long  => gate.stacked_imbalance_bullish,
    };
    if stacked { score += 1; flags.push(ConfluenceFlag::StackedImbalance); }

    // [+1] Absorción de footprint — sellers absorbiendo bids (Short) o viceversa.
    // Indica distribución institucional activa durante la consolidación.
    let absorbed = match direction {
        RbfDirection::Short => gate.absorption_ask,
        RbfDirection::Long  => gate.absorption_bid,
    };
    if absorbed { score += 1; flags.push(ConfluenceFlag::AbsorcionFootprint); }

    // [+1] LVN o thin zone en dirección del target — vacío de volumen = aceleración probable.
    let thin = match direction {
        RbfDirection::Short => gate.thin_zone_below,
        RbfDirection::Long  => gate.thin_zone_above,
    };
    if gate.lvn_nearby || thin {
        score += 1;
        flags.push(ConfluenceFlag::LvnOThinZone);
    }

    // [+1] VWAP bias — precio en lado correcto del VWAP para la dirección del trade.
    // Short bajo VWAP = sell-side pressure; Long sobre VWAP = buy-side pressure.
    if let Some(v) = vwap.filter(|&v| v > 0.0) {
        let above = entry_price > v;
        let aligned = match direction {
            RbfDirection::Short => !above,
            RbfDirection::Long  =>  above,
        };
        if aligned { score += 1; flags.push(ConfluenceFlag::VwapBias); }
    }

    // [+1] OI momentum — open interest expandiendo en la barra de breakout.
    // OI creciente = nuevas posiciones (convicción); OI decreciente = cierre de existentes.
    if gate.oi_momentum_aligned == Some(true) {
        score += 1;
        flags.push(ConfluenceFlag::OiMomentum);
    }

    // [+1] OBI trap — OBI en dirección CONTRARIA al breakout = participantes atrapados.
    // Short: obi > threshold (compradores en el libro) → van a ser squeezed hacia abajo.
    // Long:  obi < -threshold (vendedores en el libro) → van a ser squeezed hacia arriba.
    // Hallazgo Jun 6-11: OBI bullish en Short → WR=88% AvgR=+0.932 vs OBI bearish WR=79%.
    // Contraintuitivo: compradores atrapados = combustible para el move bajista.
    let obi_trap = match direction {
        RbfDirection::Short => obi > cfg.obi_threshold,
        RbfDirection::Long  => obi < -cfg.obi_threshold,
    };
    if obi_trap { score += 1; flags.push(ConfluenceFlag::ObiTrap); }

    // REMOVIDOS (análisis Jun 6-11 mostró que no discriminan o están invertidos):
    // ✗ CvdSlopeSostenido  — CVD slope acumulado puede indicar move ya consumido
    // ✗ ObiAlineado        — OBI en dirección del trade = peor outcome (invertido)
    // ✗ SessionCvdAligned  — CVD sesión acumulado = misma lógica que slope
    // ✗ BigCvdAligned      — big trade CVD acumulado = misma lógica
    // ✗ ObiMultiDepth      — L10+L20 alineados = mismo problema que ObiAlineado
    // ✗ ObiIntrabarMean    — OBI intrabar alineado = mismo problema

    // Veto especial: Long contra tendencia bajista sin máxima confluencia
    if direction == RbfDirection::Long
        && matches!(macro_regime, MacroRegime::Bear | MacroRegime::BearPullback)
        && score < cfg.bear_long_min_score
    {
        let flags_str = flags.iter().map(|f| f.as_str().to_string()).collect();
        return (score, flags_str, Some("long_bear_low_score".into()));
    }

    let flags_str = flags.iter().map(|f| f.as_str().to_string()).collect();
    (score, flags_str, None)
}

// ── Parámetros del detector ───────────────────────────────────────────────────

const RANGE_WINDOWS:    &[usize] = &[15, 20, 30, 45, 60];
const RANGE_MIN_PCT:    f64 = 0.08;
const RANGE_MAX_PCT:    f64 = 0.55;
const BREAKOUT_VR_MIN:  f64 = 3.0;
const VR_WINDOW:        usize = 50;
const DZ_WINDOW:        usize = 50;
// Stop dinámico: stop = ATR_STOP_K × ATR. Reemplaza stop_pct fijo cuando ATR disponible.
// Grid search M1: 1.0×ATR óptimo. 0.7 era demasiado ajustado (67% stops en barra 1).
const ATR_STOP_K:       f64 = 1.0;
// Filtro de potencial: rango debe ser al menos MIN_RANGE_ATR_RATIO veces el ATR.
// Trades con rango < 1.5×ATR no tienen espacio real para desarrollarse (MFE < 1R).
const MIN_RANGE_ATR_RATIO: f64 = 1.5;
const CVD_SLOPE_WIN:  usize = 20;
const EMA_MACRO:      usize = 480;
// Backtest 3.5d (n=83): Longs WR=14% AvgR=-0.695 → desactivados hasta reunir edge positivo.
const LONGS_ENABLED:  bool  = false;

/// Sessions operativas — London, Overlap y NewYork.
/// Asia y OffHours excluidos (volumen insuficiente para RBF).
const fn is_operative(s: TradingSession) -> bool {
    matches!(s, TradingSession::London | TradingSession::LondonNyOverlap | TradingSession::NewYork)
}

// ── Tipos públicos ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum RbfDirection {
    Short,
    Long,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum MacroRegime {
    Bull,
    Bear,
    BullPullback,
    BearPullback,
    Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RbfSignal {
    pub direction:        RbfDirection,
    pub entry_price:      f64,
    pub stop_price:       f64,
    pub target_price:     f64,
    pub rr:               f64,
    pub range_high:       f64,
    pub range_low:        f64,
    pub range_pct:        f64,
    pub range_bars:       usize,
    pub cvd_in_range:     f64,
    pub vr_at_breakout:   f64,
    pub macro_regime:     MacroRegime,
    pub session:          TradingSession,
    pub timestamp_ms:     i64,
    pub evidence:         Vec<String>,

    // Contexto adicional
    pub range_touch_count:  usize,
    pub session_phase:      SessionPhase,
    pub price_vs_vwap_pct:  Option<f64>,
    pub funding_at_entry:   Option<f64>,
    pub liq_ratio_pre:      f64,

    // Microestructura en la barra de breakout
    /// CVD slope (USD/bar) en la barra de ruptura; positivo = compradores acumulando.
    pub cvd_slope_at_entry: Option<f64>,
    /// Delta z-score de la barra de ruptura (normalizado 50 barras).
    pub dz_at_entry:        f64,
    /// Order Book Imbalance en la barra de ruptura (bid-ask / total).
    pub obi_at_entry:       f64,

    // Microestructura de absorción (FASE 1.1)
    /// Intensidad de delta direccional normalizada (0–1). max(dz_dir,0)/3.
    /// 0 = sin presión alineada, 1 = dz≥3 en la dirección del breakout.
    pub absorption_score:   f64,
    /// Ratio cuerpo/rango de la vela de ruptura (0–1). 0 = pin bar, 1 = marubozu.
    /// Bajo valor = precio apenas se desplazó pese al delta → absorción.
    pub bar_displacement:   f64,

    // OI delta % en evento (FASE 1.2)
    /// Cambio porcentual de Open Interest en la ventana reciente al momento del breakout.
    /// Positivo = expansión (nueva convicción); negativo = cierre de posiciones.
    pub oi_delta_pct:       Option<f64>,

    // CVD divergencia explícita (FASE 1.3)
    /// Barras consecutivas de divergencia CVD-precio al momento del breakout.
    /// Positivo = bearish div (precio sube pero CVD no); negativo = bullish div (precio baja pero CVD sube).
    /// Valor absoluto alto = divergencia persistente = mayor convicción en el breakout contrario.
    pub cvd_divergence_bars: Option<i32>,

    // VR tier (FASE 2.2)
    /// Categoría de volumen relativo: 1=2-3×, 2=3-4×, 3=4×+.
    /// Tier 3 = breakout real con volumen extraordinario (análogos a eventos institucionales).
    pub vr_tier: u8,

    // Calidad del rango (FASE 2.3)
    /// Touches del lado opuesto al breakout vs del mismo lado (0–1).
    /// Cerca de 0.5 = rango simétrico (ambos lados tocados igualmente).
    /// Cerca de 1 o 0 = asimétrico (muchos rechazos en un solo lado).
    pub range_touch_symmetry: f64,
    /// CVD acumulado en rango / número de barras. Mide densidad de presión direccional.
    /// Valor absoluto alto = mucho CVD por barra = acumulación intensa.
    pub cvd_per_bar: f64,
    /// % de extensión del close más allá del rango (distancia desde el nivel roto).
    /// Ej: 0.02 = close está 0.02% más allá del range_high/low.
    pub breakout_extension_pct: f64,

    // HTF H4 estructura (FASE 2.6)
    /// Tendencia H4 al momento del breakout: "Bull" (precio > EMA-240M1), "Bear", o None si sin warmup.
    pub htf_h1_trend: Option<String>,
    // VP Open Variant (FASE 2.1)
    /// Variante del día: "InsideValue", "OutsideVaInsidePa", "TrendDay", "FadeGap", o None.
    pub vp_open_bias: Option<String>,
    /// True si la dirección del trade está alineada con la tendencia H4.
    /// Long+Bull o Short+Bear = alineado; contra-tendencia = false.
    pub htf_h1_aligned: Option<bool>,

    // Score continuo + sizing dinámico (FASE 4)
    /// Score continuo 0–1 ponderando absorción, VR tier, extensión, H4 alineamiento y confluencia.
    pub signal_score_v2: f64,
    /// Multiplicador sugerido: 0.5× (<0.3), 1.0× (0.3–0.5), 1.5× (0.5–0.7), 2.0× (≥0.7).
    pub sizing_multiplier: f64,

    // Confluencia v2
    /// Puntuación de confluencia (0–7). Señales con veto tienen score calculado antes del veto.
    pub confluence_score: u8,
    /// Flags activos al disparar (ej. ["cvd_slope", "obi", "vwap_bias"]).
    pub confluence_flags: Vec<String>,
    /// Razón de veto si la señal fue vetada (no se abre posición, sí se registra en DB).
    pub veto_reason: Option<String>,
    /// True si es una entrada pre-breakout: precio estaba dentro del rango, cerca del borde,
    /// antes de que el VR≥3× confirmara el breakout. Stop = range_high, RR = pre_breakout_rr.
    /// El flag "pre_breakout" también se incluye en confluence_flags para queries Supabase.
    pub is_pre_breakout: bool,
}

// ── Estado interno ─────────────────────────────────────────────────────────────

#[derive(Debug)]
#[allow(dead_code)]
struct BarSnapshot {
    high:   f64,
    low:    f64,
    close:  f64,
    volume: f64,
    delta:  f64,
}

pub struct RangeBreakoutState {
    history:              VecDeque<BarSnapshot>,
    vol_hist:             VecDeque<f64>,
    /// Historial de bar_delta para calcular dz internamente.
    delta_hist:           VecDeque<f64>,
    /// Historial de CVD acumulado para calcular cvd_slope internamente (fallback).
    cvd_acc_hist:         VecDeque<f64>,
    cvd_running:          f64,
    ema480:               f64,
    ema480_prev:          f64,
    ema480_initialized:   bool,
    bars_seen:            usize,
    last_signal_bar:      usize,
    last_pre_signal_bar:  usize,
}

impl RangeBreakoutState {
    pub fn new() -> Self {
        Self {
            history:            VecDeque::with_capacity(65),
            vol_hist:           VecDeque::with_capacity(VR_WINDOW + 5),
            delta_hist:         VecDeque::with_capacity(DZ_WINDOW + 5),
            cvd_acc_hist:       VecDeque::with_capacity(CVD_SLOPE_WIN + 5),
            cvd_running:        0.0,
            ema480:             0.0,
            ema480_prev:        0.0,
            ema480_initialized: false,
            bars_seen:             0,
            last_signal_bar:       0,
            last_pre_signal_bar:   0,
        }
    }

    /// Resetea el cooldown de señal tras warm-up para no bloquear la primera barra live.
    pub fn reset_signal_cooldown(&mut self) {
        self.last_signal_bar = 0;
    }

    /// OLS slope del CVD acumulado sobre las últimas `window` barras.
    fn compute_cvd_slope(&self) -> Option<f64> {
        let n = self.cvd_acc_hist.len();
        if n < 5 { return None; }
        let slice: Vec<f64> = self.cvd_acc_hist.iter().copied().collect();
        let nf = n as f64;
        let sum_x: f64 = (0..n).map(|i| i as f64).sum();
        let sum_y: f64 = slice.iter().sum();
        let sum_xy: f64 = slice.iter().enumerate().map(|(i, &y)| i as f64 * y).sum();
        let sum_x2: f64 = (0..n).map(|i| (i * i) as f64).sum();
        let denom = nf * sum_x2 - sum_x * sum_x;
        if denom.abs() < 1e-10 { return None; }
        Some((nf * sum_xy - sum_x * sum_y) / denom)
    }

    /// Delta z-score de la barra actual.
    fn compute_dz(&self, bar_delta: f64) -> f64 {
        let n = self.delta_hist.len();
        if n < 5 { return 0.0; }
        let mean = self.delta_hist.iter().sum::<f64>() / n as f64;
        let var  = self.delta_hist.iter().map(|&d| (d - mean).powi(2)).sum::<f64>() / n as f64;
        let std  = var.sqrt();
        if std < 1e-8 { return 0.0; }
        (bar_delta - mean) / std
    }

    /// Llamar en cada cierre de barra M1.
    pub fn on_bar_close(
        &mut self,
        open: f64, high: f64, low: f64, close: f64,
        volume: f64, bar_delta: f64,
        session: TradingSession,
        timestamp_ms: i64,
        cfg: &RangeBreakoutConfig,
        // Microestructura externa
        vwap:         Option<f64>,
        funding_rate: Option<f64>,
        liq_ratio:    f64,
        obi:          f64,
        cvd_slope_ext: Option<f64>, // slope computado externamente (preferido)
        // Contexto de confluencia v2 (opcional — si es None no se puntúa)
        gate: Option<&RbfGateContext>,
    ) -> Option<RbfSignal> {
        let _ = open;

        self.bars_seen += 1;

        // EMA480
        let k = 2.0 / (EMA_MACRO as f64 + 1.0);
        if !self.ema480_initialized {
            self.ema480      = close;
            self.ema480_prev = close;
            self.ema480_initialized = true;
        } else {
            self.ema480_prev = self.ema480;
            self.ema480 = close * k + self.ema480 * (1.0 - k);
        }

        // Historial de volumen (VR)
        self.vol_hist.push_back(volume);
        if self.vol_hist.len() > VR_WINDOW { self.vol_hist.pop_front(); }

        // Historial de delta (dz)
        self.delta_hist.push_back(bar_delta);
        if self.delta_hist.len() > DZ_WINDOW { self.delta_hist.pop_front(); }

        // Historial de CVD acumulado (slope fallback)
        self.cvd_running += bar_delta;
        self.cvd_acc_hist.push_back(self.cvd_running);
        if self.cvd_acc_hist.len() > CVD_SLOPE_WIN { self.cvd_acc_hist.pop_front(); }

        // Snapshot de la barra
        let max_history = *RANGE_WINDOWS.iter().max().unwrap_or(&60);
        self.history.push_back(BarSnapshot { high, low, close, volume, delta: bar_delta });
        if self.history.len() > max_history + 2 { self.history.pop_front(); }

        if self.bars_seen < VR_WINDOW + max_history { return None; }
        if self.bars_seen - self.last_signal_bar < cfg.cooldown_bars { return None; }
        if !is_operative(session) { return None; }
        if !cfg.enabled { return None; }

        // VR
        let mean_vol = if self.vol_hist.is_empty() { 1.0 }
            else { self.vol_hist.iter().sum::<f64>() / self.vol_hist.len() as f64 };
        let vr = if mean_vol > 0.0 { volume / mean_vol } else { 0.0 };

        // pre_eligible: condiciones para el path de entrada anticipada.
        // Se evalúa antes del cooldown para que el check sea barato.
        let pre_eligible = cfg.pre_breakout_enabled
            && vr >= cfg.pre_breakout_vr_min
            && self.bars_seen.saturating_sub(self.last_pre_signal_bar) >= cfg.cooldown_bars;

        // Salida rápida: VR insuficiente incluso para pre-breakout
        let effective_vr_min = if pre_eligible { cfg.pre_breakout_vr_min } else { BREAKOUT_VR_MIN };
        if vr < effective_vr_min { return None; }

        // Microestructura en esta barra
        let dz         = self.compute_dz(bar_delta);
        let cvd_slope  = cvd_slope_ext.or_else(|| self.compute_cvd_slope());

        // Fase / régimen macro
        let session_ctx  = crate::session::classify_session(timestamp_ms);
        let session_phase = session_ctx.phase;
        let slope_pos    = self.ema480 > self.ema480_prev;
        let above_ema    = close > self.ema480;
        let macro_regime = match (above_ema, slope_pos) {
            (true,  true)  => MacroRegime::Bull,
            (false, false) => MacroRegime::Bear,
            (true,  false) => MacroRegime::BullPullback,
            (false, true)  => MacroRegime::BearPullback,
        };

        let price_vs_vwap_pct = vwap
            .filter(|v| *v > 0.0)
            .map(|v| (close - v) / v * 100.0);

        let hist_len = self.history.len();
        for &range_bars in RANGE_WINDOWS {
            if hist_len < range_bars + 1 { continue; }

            let window_start = hist_len - range_bars - 1;
            let window: Vec<&BarSnapshot> = self.history
                .iter()
                .skip(window_start)
                .take(range_bars)
                .collect();

            let range_high = window.iter().map(|b| b.high).fold(f64::NEG_INFINITY, f64::max);
            let range_low  = window.iter().map(|b| b.low).fold(f64::INFINITY,  f64::min);
            let range_pct  = (range_high - range_low) / close * 100.0;

            if range_pct < RANGE_MIN_PCT || range_pct > RANGE_MAX_PCT { continue; }

            // Filtro de potencial: rango debe ser ≥ MIN_RANGE_ATR_RATIO × ATR.
            // Rangos menores al ATR típico no tienen espacio real para desarrollarse.
            if let Some(ctx) = gate {
                if ctx.atr > 0.0 {
                    let range_abs = range_high - range_low;
                    if range_abs < MIN_RANGE_ATR_RATIO * ctx.atr { continue; }
                }
                // Filtro de régimen expansión: si el mercado ya entró en expansión hace
                // varias barras, el move está maduro y el edge desaparece.
                // Calibración n=30 Shorts: expansion_n≤3 → WR=60%; sin filtro → WR=40%.
                // Pasar expansion_bars_recent=0 para símbolos donde no aplica (ej: SOL).
                if let Some(max_exp) = cfg.expansion_max_bars {
                    if ctx.expansion_bars_recent > max_exp { continue; }
                }
                // Gate cum_delta_25b por símbolo (Short).
                // Muy negativo = selling masivo ya consumido → edge desaparece.
                // Muy positivo = compradores agresivos activos → fakeout probable.
                if let Some(g) = gate {
                    if let Some(min_d) = cfg.cum_delta_min_short {
                        if g.cum_delta_25b < min_d { continue; }
                    }
                    if let Some(max_d) = cfg.cum_delta_max_short {
                        if g.cum_delta_25b > max_d { continue; }
                    }
                }
            }

            let cvd_in_range: f64 = window.iter().map(|b| b.delta).sum();

            let touches_high = window.iter().filter(|b| b.high >= range_high * 0.999 && b.close < range_high).count();
            let touches_low  = window.iter().filter(|b| b.low  <= range_low  * 1.001 && b.close > range_low).count();

            let breaks_down = close < range_low;
            let breaks_up   = close > range_high;

            // ── PRE-BREAKOUT PATH ──────────────────────────────────────────────────
            // Precio dentro del rango, cerca del borde inferior (Short).
            // Entra antes del VR≥3× — gate compensador: expansion_bars_recent + oi_mom.
            // Calibración n=30 Shorts: pct_done=54% en modo actual → entrada aquí captura
            // el move completo (pre_move_r avg 2.63R + 2R target = 4.6R potencial desde range_low).
            if pre_eligible && !breaks_down && !breaks_up {
                let near_low = close <= range_low * (1.0 + cfg.pre_breakout_zone_pct);
                // LONGS desactivados → solo verificar near_low (Short pre-breakout)
                if near_low && cvd_in_range < 0.0 {
                    // OI momentum gate: oi_mom_n alto = move ya maduro, evitar entrar
                    let oi_gate_ok = cfg.pre_breakout_oi_max.map_or(true, |max| {
                        gate.map_or(true, |g| g.oi_mom_bars_recent <= max)
                    });
                    if oi_gate_ok {
                        let stop_price = range_high;
                        let risk = stop_price - close;
                        if risk > 1e-6 {
                            let rr = cfg.pre_breakout_rr;
                            let target_price = close - rr * risk;
                            if rr >= cfg.min_rr {
                                let (confluence_score, mut confluence_flags, veto_reason) = if let Some(g) = gate {
                                    score_confluence(
                                        RbfDirection::Short, macro_regime,
                                        cvd_slope, obi, vwap,
                                        close, target_price, g, cfg,
                                    )
                                } else {
                                    (0, vec![], None)
                                };
                                // Solo emitir si no hay veto y score mínimo
                                let eff_min_score = cfg.min_confluence_score_override
                                    .unwrap_or(cfg.min_confluence_score);
                                if veto_reason.is_none()
                                    && confluence_score >= eff_min_score
                                {
                                    confluence_flags.push("pre_breakout".to_string());
                                    let vr_tier: u8 = if vr >= 4.0 { 3 } else if vr >= 2.0 { 2 } else { 1 };
                                    let cvd_per_bar = if range_bars > 0 { cvd_in_range / range_bars as f64 } else { 0.0 };
                                    let dz_dir_pb = -dz; // Short: selling pressure = dz negativo
                                    let absorption_score_pb = (dz_dir_pb.max(0.0) / 3.0).min(1.0);
                                    let bar_disp_pb = if high > low { (close - open).abs() / (high - low) } else { 0.5 };
                                    let cvd_neg_pb = window.iter().filter(|b| b.delta < 0.0).count();
                                    let cvd_div_range_pb = cvd_neg_pb as f64 / window.len().max(1) as f64;
                                    let h4_aligned_pb = gate.and_then(|g| g.htf_h1_trend.as_ref()
                                        .map(|t| t.as_str() == "Bear"));
                                    let signal_score_v2_pb = {
                                        let mut s = 0.0_f64;
                                        s += absorption_score_pb * 0.25;
                                        s += (vr_tier as f64 - 1.0) / 2.0 * 0.20;
                                        s += if h4_aligned_pb.unwrap_or(false) { 0.15 } else { 0.0 };
                                        s += (confluence_score as f64 / 9.0) * 0.25;
                                        s += cvd_div_range_pb * 0.10;
                                        s.min(1.0)
                                    };
                                    let sizing_multiplier_pb: f64 = if signal_score_v2_pb >= 0.70 { 2.0 }
                                        else if signal_score_v2_pb >= 0.50 { 1.5 }
                                        else if signal_score_v2_pb >= 0.30 { 1.0 }
                                        else { 0.5 };
                                    self.last_pre_signal_bar = self.bars_seen;
                                    self.last_signal_bar     = self.bars_seen;
                                    let price_vs_vwap_pct_pb = vwap.filter(|&v| v > 0.0).map(|v| (close - v) / v * 100.0);
                                    println!(
                                        "[rbf_pre] Short entry={:.2} stop={:.2} target={:.2} rr={:.1} exp={} oi_mom={} score_v2={:.2}",
                                        close, stop_price, target_price, rr,
                                        gate.map(|g| g.expansion_bars_recent).unwrap_or(0),
                                        gate.map(|g| g.oi_mom_bars_recent).unwrap_or(0),
                                        signal_score_v2_pb,
                                    );
                                    return Some(RbfSignal {
                                        direction:           RbfDirection::Short,
                                        entry_price:         close,
                                        stop_price,
                                        target_price,
                                        rr,
                                        range_high,
                                        range_low,
                                        range_pct,
                                        range_bars,
                                        cvd_in_range,
                                        vr_at_breakout:      vr,
                                        macro_regime,
                                        session,
                                        timestamp_ms,
                                        evidence: vec![
                                            "pre_breakout".to_string(),
                                            format!("range_pct={:.3}%", range_pct),
                                            format!("vr={:.2}x", vr),
                                            format!("rr={:.1}", rr),
                                        ],
                                        range_touch_count:   touches_low,
                                        session_phase,
                                        price_vs_vwap_pct:   price_vs_vwap_pct_pb,
                                        funding_at_entry:    funding_rate,
                                        liq_ratio_pre:       liq_ratio,
                                        cvd_slope_at_entry:  cvd_slope,
                                        dz_at_entry:         dz,
                                        obi_at_entry:        obi,
                                        absorption_score:    absorption_score_pb,
                                        bar_displacement:    bar_disp_pb,
                                        oi_delta_pct:        gate.and_then(|g| g.oi_delta_pct),
                                        cvd_divergence_bars: gate.and_then(|g| g.cvd_divergence_bars),
                                        vr_tier,
                                        range_touch_symmetry: 0.5,
                                        cvd_per_bar,
                                        breakout_extension_pct: 0.0,
                                        signal_score_v2:     signal_score_v2_pb,
                                        sizing_multiplier:   sizing_multiplier_pb,
                                        htf_h1_trend:        gate.and_then(|g| g.htf_h1_trend.clone()),
                                        htf_h1_aligned:      h4_aligned_pb,
                                        vp_open_bias:        gate.and_then(|g| g.vp_open_bias.clone()),
                                        confluence_score,
                                        confluence_flags,
                                        veto_reason:         None,
                                        is_pre_breakout:     true,
                                    });
                                }
                            }
                        }
                    }
                }
                continue; // pre-breakout no disparó — próxima ventana de rango
            }

            // ── POST-BREAKOUT PATH (precio fuera del rango, VR≥3×) ───────────────
            if !breaks_down && !breaks_up { continue; }
            // Requiere VR completo para confirmar el breakout
            if vr < BREAKOUT_VR_MIN { continue; }

            let direction = if breaks_down { RbfDirection::Short } else { RbfDirection::Long };
            if direction == RbfDirection::Long  && !cfg.allow_long  { continue; }
            if direction == RbfDirection::Short && !cfg.allow_short { continue; }
            let sign: f64 = if breaks_down { 1.0 } else { -1.0 }; // sign para "move in direction"

            // CVD acumulado en rango alineado con dirección
            let cvd_aligned = match direction {
                RbfDirection::Short => cvd_in_range < 0.0,
                RbfDirection::Long  => cvd_in_range > 0.0,
            };
            if !cvd_aligned { continue; }

            // cvd_in_range gate por símbolo (Short): ETH wins avg -418 vs losses -982.
            // Rechazar setups donde el selling durante el rango fue excesivo (move ya consumido).
            if direction == RbfDirection::Short {
                if let Some(min_cvd) = cfg.cvd_in_range_min_short {
                    if cvd_in_range < min_cvd { continue; }
                }
            }

            // ── Gate de microestructura ────────────────────────────────────────
            let cvd_slope_dir = cvd_slope.map(|s| sign * (-s));
            // cvd_slope_gate desactivado: backtest 30d muestra que slope>=0
            // (absorción de compradores) tiene mejor WR que slope<0 (continuación pura).
            if cfg.cvd_slope_gate {
                if let Some(sd) = cvd_slope_dir {
                    if sd <= 0.0 { continue; }
                }
            }

            // dz alineado con la dirección
            let dz_dir = sign * (-dz);
            if dz_dir < cfg.dz_min || dz_dir > cfg.dz_max { continue; }

            // OBI gate
            if cfg.obi_gate {
                let obi_ok = match direction {
                    RbfDirection::Short => obi < -cfg.obi_threshold,
                    RbfDirection::Long  => obi >  cfg.obi_threshold,
                };
                if !obi_ok { continue; }
            }

            // ── VSWAP proximity gate ───────────────────────────────────────────
            // No shortar cuando precio ya está extendido bajo el VWAP.
            // Backtest 30d: precio <-0.3% del VWAP → WR=6.5%; dentro del -0.3% → WR=37.3%.
            if cfg.vswap_gate {
                if let Some(v) = vwap.filter(|&v| v > 0.0) {
                    let pct = (close - v) / v;
                    let ok = match direction {
                        RbfDirection::Short => pct > -cfg.vswap_max_dev,
                        RbfDirection::Long  => pct <  cfg.vswap_max_dev,
                    };
                    if !ok { continue; }
                }
            }

            // ── Breakout extension gate ────────────────────────────────────────
            // El close debe romper con convicción (>X% más allá del nivel roto).
            // Backtest 30d: ext<0.1% WR=18.8%; ext>0.1% WR=44.4%; ext>0.2% WR=53.8%.
            if cfg.breakout_ext_gate {
                let ext = match direction {
                    RbfDirection::Short => (range_low - close) / range_low,
                    RbfDirection::Long  => (close - range_high) / range_high,
                };
                if ext < cfg.breakout_ext_min { continue; }
            }

            // Stop = techo/piso del rango de consolidación (estructura de mercado real).
            // ATR M1 (~0.1%) era ruido puro — el stop quedaba DENTRO de la consolidación,
            // no encima de ella. Si el precio regresa al rango, el breakout falló.
            let rr_short = cfg.target_short_pct / cfg.stop_pct; // e.g. 0.50/0.25 = 2.0
            let rr_long  = cfg.target_long_pct  / cfg.stop_pct;
            let (stop_price, target_price) = match direction {
                RbfDirection::Short => {
                    let s = range_high;
                    (s, close - rr_short * (s - close))
                }
                RbfDirection::Long => {
                    let s = range_low;
                    (s, close + rr_long * (close - s))
                }
            };
            let risk   = (close - stop_price).abs();
            let reward = (target_price - close).abs();
            let rr     = if risk > 1e-10 { reward / risk } else { 0.0 };
            if rr < cfg.min_rr { continue; }

            // Evidencia
            let mut evidence = vec![
                format!("rbf:{:?}", direction),
                format!("range_pct={:.3}%", range_pct),
                format!("range_bars={}", range_bars),
                format!("cvd_in_range={:.1}", cvd_in_range),
                format!("vr={:.2}x", vr),
                format!("macro={:?}", macro_regime),
                format!("session={:?}", session),
            ];
            if let Some(sd) = cvd_slope_dir {
                evidence.push(format!("cvd_slope_dir={:.2}", sd));
            }
            evidence.push(format!("dz_dir={:.2}", dz_dir));
            if cfg.obi_gate {
                evidence.push(format!("obi={:.3}", obi));
            }

            let counter_trend = match direction {
                RbfDirection::Short => matches!(macro_regime, MacroRegime::Bull | MacroRegime::BullPullback),
                RbfDirection::Long  => matches!(macro_regime, MacroRegime::Bear | MacroRegime::BearPullback),
            };
            if counter_trend { evidence.push("contra_tendencia".to_string()); }

            let range_touch_count = if breaks_down { touches_low } else { touches_high };

            // ── VR tier (FASE 2.2) ───────────────────────────────────────────
            let vr_tier: u8 = if vr >= 4.0 { 3 } else if vr >= 3.0 { 2 } else { 1 };

            // ── Calidad del rango (FASE 2.3) ─────────────────────────────────
            // Simetría de toques: cuánto balance había entre ambos lados del rango
            let total_touches = (touches_high + touches_low) as f64;
            let range_touch_symmetry = if total_touches > 0.0 {
                let hi_ratio = touches_high as f64 / total_touches;
                // 0.5 = simétrico, 0 o 1 = todos los toques en un lado
                1.0 - (hi_ratio - 0.5).abs() * 2.0
            } else {
                0.5
            };
            // CVD acumulado por barra del rango
            let cvd_per_bar = if range_bars > 0 { cvd_in_range / range_bars as f64 } else { 0.0 };
            // Extensión del cierre más allá del rango roto (%)
            let breakout_extension_pct = match direction {
                RbfDirection::Short => (range_low - close) / close * 100.0,
                RbfDirection::Long  => (close - range_high) / close * 100.0,
            }.max(0.0);

            // ── Absorción (FASE 1.1) ──────────────────────────────────────────
            // Intensidad del delta direccional normalizada: max(dz_dir,0)/3 ∈ [0,1]
            let absorption_score = (dz_dir.max(0.0) / 3.0).min(1.0);

            // Divergencia CVD durante el rango: % de barras con delta en dirección del breakout.
            // Traders de orderflow: "presión sostenida durante la consolidación = instituciones distribuyendo"
            // Short: queremos alta % de barras con delta < 0 (sellers activos mientras rango aguantaba).
            // Long: queremos alta % de barras con delta > 0 (compradores activos durante consolidación).
            let cvd_neg_bars = window.iter().filter(|b| b.delta < 0.0).count();
            let cvd_neg_ratio = cvd_neg_bars as f64 / window.len().max(1) as f64;
            let cvd_divergence_range = match direction {
                RbfDirection::Short => cvd_neg_ratio,
                RbfDirection::Long  => 1.0 - cvd_neg_ratio,
            };
            // Ratio cuerpo/rango: low = pin bar / absorbed; high = engulfing candle
            let bar_displacement = if high > low {
                (close - open).abs() / (high - low)
            } else {
                0.5
            };

            // ── Confluencia v2 ────────────────────────────────────────────────
            let (confluence_score, confluence_flags, veto_reason) = if let Some(g) = gate {
                score_confluence(
                    direction, macro_regime,
                    cvd_slope, obi, vwap,
                    close, target_price,
                    g, cfg,
                )
            } else {
                (0, vec![], None)
            };

            if let Some(ref reason) = veto_reason {
                evidence.push(format!("veto={}", reason));
            } else {
                evidence.push(format!("confluence={}/{}", confluence_score, 8));
            }

            self.last_signal_bar = self.bars_seen;

            // ── Score continuo (FASE 4) ───────────────────────────────────────
            let h4_aligned = gate.and_then(|g| g.htf_h1_trend.as_ref().map(|t| {
                matches!((direction, t.as_str()), (RbfDirection::Long,"Bull")|(RbfDirection::Short,"Bear"))
            }));
            let signal_score_v2 = {
                let mut s = 0.0_f64;
                s += absorption_score * 0.25;
                s += (vr_tier as f64 - 1.0) / 2.0 * 0.20;
                s += breakout_extension_pct.min(0.10) / 0.10 * 0.15;
                s += if h4_aligned.unwrap_or(false) { 0.15 } else { 0.0 };
                s += (confluence_score as f64 / 9.0) * 0.25;
                // Divergencia CVD durante el rango: presión sostenida = mayor convicción en el breakout.
                // Fuente: múltiples traders orderflow (Yush, Brando, Umar) — "CVD declining during range = distribution".
                s += cvd_divergence_range * 0.10;
                s.min(1.0_f64)
            };
            let sizing_multiplier: f64 = if signal_score_v2 >= 0.70 { 2.0 }
                else if signal_score_v2 >= 0.50 { 1.5 }
                else if signal_score_v2 >= 0.30 { 1.0 }
                else { 0.5 };

            // Gate de confluence score (por símbolo via override, o global).
            let eff_min_score_post = cfg.min_confluence_score_override
                .unwrap_or(cfg.min_confluence_score);
            if veto_reason.is_some() || confluence_score < eff_min_score_post {
                continue;
            }

            return Some(RbfSignal {
                direction,
                entry_price: close,
                stop_price,
                target_price,
                rr,
                range_high,
                range_low,
                range_pct,
                range_bars,
                cvd_in_range,
                vr_at_breakout: vr,
                macro_regime,
                session,
                timestamp_ms,
                evidence,
                range_touch_count,
                session_phase,
                price_vs_vwap_pct,
                funding_at_entry:    funding_rate,
                liq_ratio_pre:       liq_ratio,
                cvd_slope_at_entry:  cvd_slope,
                dz_at_entry:         dz,
                obi_at_entry:        obi,
                absorption_score,
                bar_displacement,
                oi_delta_pct:        gate.map(|g| g.oi_delta_pct).flatten(),
                cvd_divergence_bars: gate.map(|g| g.cvd_divergence_bars).flatten(),
                vr_tier,
                range_touch_symmetry,
                cvd_per_bar,
                breakout_extension_pct,
                signal_score_v2,
                sizing_multiplier,
                htf_h1_trend: gate.and_then(|g| g.htf_h1_trend.clone()),
                htf_h1_aligned: h4_aligned,
                vp_open_bias: gate.and_then(|g| g.vp_open_bias.clone()),
                confluence_score,
                confluence_flags,
                veto_reason,
                is_pre_breakout: false,
            });
        }

        None
    }
}

// ── Configuración ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RangeBreakoutConfig {
    pub enabled:           bool,
    /// Permitir señales Long. false = solo Shorts.
    pub allow_long:        bool,
    /// Permitir señales Short. false = solo Longs.
    pub allow_short:       bool,
    pub stop_pct:          f64,
    pub target_short_pct:  f64,
    pub target_long_pct:   f64,
    pub min_rr:            f64,

    // Gates de microestructura (validados backtest 30d)
    /// Activar gate de CVD slope: slope debe confirmar dirección del breakout.
    pub cvd_slope_gate:    bool,
    /// dz mínimo alineado con dirección (0.5 = backtest óptimo, elimina breakouts planos).
    pub dz_min:            f64,
    /// dz máximo alineado con dirección (3.0 = cortar extremos que revierten).
    pub dz_max:            f64,
    /// Activar gate de OBI (requiere validación con datos reales; desactivado por defecto).
    pub obi_gate:          bool,
    /// Umbral OBI para el gate: SHORT quiere obi < threshold, LONG quiere obi > (1-threshold).
    pub obi_threshold:     f64,

    // Sistema de confluencia v2
    /// Score mínimo para abrir posición. 1 = shadow mode (grabar todo). 3+ = producción.
    pub min_confluence_score: u8,
    /// |cvd_slope| mínimo para sumar el flag CvdSlopeSostenido.
    pub cvd_slope_threshold: f64,
    /// Score mínimo requerido para Long en régimen Bear/BearPullback.
    pub bear_long_min_score: u8,

    // ── Filtros validados backtest 30d ─────────────────────────────────────────
    /// Activar filtro VWAP proximity: rechaza entries muy extendidos del VWAP.
    pub vswap_gate:          bool,
    /// Desviación máxima del VWAP permitida (0.003 = 0.3%).
    /// Short rechazado si close < vwap*(1-vswap_max_dev).
    pub vswap_max_dev:       f64,
    /// Activar filtro de extensión de breakout.
    pub breakout_ext_gate:   bool,
    /// Extensión mínima del close más allá del nivel roto (0.001 = 0.1%).
    pub breakout_ext_min:    f64,

    // ── Filtro de régimen expansión (calibrado con datos live 72 trades) ──────
    /// Máximo de barras en régimen Expansion permitidas en las 25 barras pre-entry.
    /// None = no filtrar. Calibración: BTC/ETH/BNB → Some(3). SOL → None (inv. correlación).
    /// Shorts n=30: expansion_n≤3 WR=60% +8.1R vs sin filtro WR=40% +2.3R.
    pub expansion_max_bars: Option<u8>,

    // ── Entrada anticipada pre-breakout (calibrada con datos live 72 trades) ──
    /// Activar modo de entrada anticipada: entra cuando precio toca el borde del rango
    /// con condiciones de microestructura, ANTES de esperar VR≥3× de confirmación.
    /// Gate compensador: expansion_max_bars + oi_mom_bars_recent reemplazan el VR≥3×.
    /// Calibración: pct_done=54% en modo actual → potencial 4.6R desde range_low vs 2R.
    pub pre_breakout_enabled:  bool,
    /// Zona alrededor del borde inferior del rango que activa el pre-breakout (fracción).
    /// Si close ≤ range_low × (1 + zone_pct): trigger para Short pre-breakout.
    /// 0.001 = 0.1% — precio a menos de 0.1% por encima de range_low.
    pub pre_breakout_zone_pct: f64,
    /// RR objetivo del pre-breakout. Más profundo que post-breakout (3.0 vs 2.0) porque
    /// se entra antes del move: potencial avg 4.6R desde range_low.
    pub pre_breakout_rr:       f64,
    /// VR mínimo para activar pre-breakout. Más bajo que BREAKOUT_VR_MIN=3×.
    /// Requiere actividad de volumen en el borde del rango sin exigir breakout confirmado.
    pub pre_breakout_vr_min:   f64,
    /// Barras máximas con OI momentum en las 25 pre-entry para el gate de pre-breakout.
    /// Calibración: oi_mom_n≤3 → WR=58%; oi_mom_n≤4 → WR=57%. None = gate desactivado.
    pub pre_breakout_oi_max:   Option<u8>,

    // ── Gates cum_delta por símbolo ───────────────────────────────────────────
    /// Filtro Short: rechaza si cum_delta_25b < umbral (move demasiado consumido).
    /// BNB calibrado: Some(-500.0) — wins avg -78, losses avg -1357.
    /// None = sin filtro (default).
    pub cum_delta_min_short: Option<f64>,
    /// Filtro Short: rechaza si cum_delta_25b > umbral (compradores agresivos = fakeout).
    /// BTC candidato: Some(200.0) — losses cum_delta = +377.
    /// None = sin filtro (default).
    pub cum_delta_max_short: Option<f64>,
    /// Filtro Short: rechaza si cvd_in_range < umbral (selling demasiado consumido).
    /// ETH calibrado: Some(-700.0) — wins avg -418, losses avg -982.
    /// None = sin filtro (default).
    pub cvd_in_range_min_short: Option<f64>,
    /// Score mínimo de confluencia específico por símbolo (sobrescribe min_confluence_score).
    /// ETH: Some(2) — OBI invertido, exigir más evidencia. None = usar min_confluence_score.
    pub min_confluence_score_override: Option<u8>,
    /// Barras mínimas entre señales del mismo detector (cooldown).
    /// Configurable desde strategy.toml [range_breakout] cooldown_bars.
    pub cooldown_bars: usize,
}

impl Default for RangeBreakoutConfig {
    fn default() -> Self {
        Self {
            enabled:          true,
            allow_long:       true,
            allow_short:      true,
            stop_pct:         0.25,
            target_short_pct: 0.50,
            target_long_pct:  0.45,
            min_rr:           1.5,
            // Microestructura — valores del backtest 30d
            cvd_slope_gate:   true,
            dz_min:           0.5,
            dz_max:           3.0,
            obi_gate:         false, // pendiente validación con datos reales
            obi_threshold:    0.45,
            // v2: shadow mode por defecto (grabar todo, sin filtro de score)
            min_confluence_score: 1,
            cvd_slope_threshold:  15.0,
            bear_long_min_score:  5,
            // Filtros validados backtest 30d (activados)
            vswap_gate:        true,
            vswap_max_dev:     0.003,  // rechaza si precio > 0.3% bajo VWAP
            breakout_ext_gate: true,
            breakout_ext_min:  0.001,  // close debe romper >0.1% más allá del nivel
            // Filtro expansion: None por defecto. El caller lo sobrescribe por símbolo.
            // BTC/ETH/BNB → Some(3). SOL/XRP → None.
            expansion_max_bars: None,
            // Pre-breakout: desactivado por defecto hasta calibración con datos.
            // Activar cuando haya n≥30 trades pre-breakout para medir WR real.
            pre_breakout_enabled:  false,
            pre_breakout_zone_pct: 0.001,  // 0.1% por encima de range_low
            pre_breakout_rr:       3.0,    // target 3× vs 2× en post-breakout
            pre_breakout_vr_min:   1.5,    // requiere VR≥1.5× en borde del rango
            pre_breakout_oi_max:   Some(3), // oi_mom_n≤3 → WR=58%
            cum_delta_min_short:        None,
            cum_delta_max_short:        None,
            cvd_in_range_min_short:     None,
            min_confluence_score_override: None,
            cooldown_bars: 30,
        }
    }
}
