use serde::{Deserialize, Serialize};

use crate::detectors::{FvgContext, OrderBlockContext, SpoofContext};
use crate::session::SessionContext;
use crate::structure::MarketStructureContext;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Side {
    Long,
    Short,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum StrategyId {
    ValueAreaFailedAuction,
    VwapValuePullbackContinuation,
    LvnLiquidityVacuumBreakout,
    LiquidationHunt,
    FundingExhaustionReversal,
    SmartMoneyDivergence,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum StrategyAction {
    Wait,
    ShadowSignal,
    Blocked,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Regime {
    TrendUp,
    TrendDown,
    Chop,
    Compression,
    Expansion,
    Stress,
    Aftermath,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum DataQuality {
    Live,
    Fallback,
    Degraded,
    Stale,
    Missing,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ValueLocation {
    AboveVah,
    BelowVal,
    InValue,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum PriceRelation {
    Above,
    Below,
    At,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum AbsorptionSide {
    Bid,
    Ask,
    None,
    Unknown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum CvdDivergence {
    BearishAbsorption,
    BullishAbsorption,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ImbalanceSide {
    Bullish,
    Bearish,
    None,
    Unknown,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VolumeProfileContext {
    pub poc: Option<f64>,
    pub vah: Option<f64>,
    pub val: Option<f64>,
    pub hvn_nearby: Vec<f64>,
    pub lvn_nearby: Vec<f64>,
    pub value_location: ValueLocation,
    pub quality: DataQuality,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VwapContext {
    pub vwap_session: Option<f64>,
    pub avwap_bos: Option<f64>,
    pub avwap_event: Option<f64>,
    pub price_vs_vwap: PriceRelation,
    pub price_vs_avwap_bos: PriceRelation,
    pub price_vs_avwap_event: PriceRelation,
    pub quality: DataQuality,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderFlowContext {
    pub cvd: Option<f64>,
    pub cvd_slope: Option<f64>,
    pub delta: Option<f64>,
    pub taker_imbalance: Option<f64>,
    pub buy_volume: Option<f64>,
    pub sell_volume: Option<f64>,
    pub vpin: Option<f64>,
    pub cvd_divergence: Option<CvdDivergence>,
    pub footprint_absorption: AbsorptionSide,
    pub stacked_imbalance: ImbalanceSide,
    pub failed_acceptance: bool,
    pub sweep_confirmed: bool,
    pub mss_active: bool,
    pub quality: DataQuality,
    /// Funding rate del perp (decimal, 0.0001 = 1 bp). None si no disponible.
    pub funding_rate: Option<f64>,
    /// Basis perp-spot en % ((perp/spot - 1) * 100). None si spot no disponible.
    pub basis: Option<f64>,
    /// OI delta acumulado en el período (contratos). None si no hay suficiente historia.
    pub oi_delta: Option<f64>,
    /// Precio subió + OI subió (long) o precio bajó + OI subió (short). None si sin datos.
    pub oi_momentum_aligned: Option<bool>,
    /// Pared de bids dentro de 1×ATR por debajo del precio (soporte cercano).
    pub bid_wall_nearby: bool,
    /// Pared de asks dentro de 1×ATR por encima del precio (resistencia cercana).
    pub ask_wall_nearby: bool,
    /// Últimas 5 velas con ≤2 reversiones — movimiento limpio sin chopping.
    pub price_action_clean: bool,
    /// Pendiente rápida (últimas 5 barras) del precio normalizado por ATR.
    /// Negativo fuerte indica momentum bajista incluso si regime=TrendUp.
    pub fast_slope: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderBookContext {
    pub obi_l5: Option<f64>,
    pub obi_l10: Option<f64>,
    pub obi_l20: Option<f64>,
    pub microprice: Option<f64>,
    pub spread_bps: Option<f64>,
    pub walls_above: Vec<f64>,
    pub walls_below: Vec<f64>,
    pub thin_zone_above: bool,
    pub thin_zone_below: bool,
    pub quality: DataQuality,
    /// Detección de spoofing L2 en el tick actual. None hasta que SpoofDetector esté activo.
    #[serde(default)]
    pub spoof: Option<SpoofContext>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StrategyMarketContext {
    pub symbol: String,
    pub timestamp_ms: i64,
    pub price: f64,
    pub regime: Regime,
    pub atr: Option<f64>,
    pub volume_profile: VolumeProfileContext,
    pub vwap: VwapContext,
    pub flow: OrderFlowContext,
    pub orderbook: OrderBookContext,
    /// Institutional data (liquidations, L/S ratios, OI trend, funding).
    /// None until at least one REST fetch cycle completes.
    pub institutional: Option<crate::institutional::InstitutionalContext>,
    /// 20-bar swing high/low for structural target selection in find_structural_target.
    /// None during warmup (<20 bars available).
    #[serde(default)]
    pub swing_high_20: Option<f64>,
    #[serde(default)]
    pub swing_low_20: Option<f64>,

    // ── Nuevos contextos de Fase 1 ────────────────────────────────────────────

    /// Estructura de precio HTF (BOS/CHoCH, sesgo, zona premium/discount).
    /// None hasta que MarketStructureTracker haya procesado suficientes barras HTF.
    #[serde(default)]
    pub market_structure: Option<MarketStructureContext>,

    /// Sesión de trading activa (Asia/London/NY/Overlap) derivada del timestamp.
    /// None hasta que se inicialice el SessionTracker.
    #[serde(default)]
    pub session: Option<SessionContext>,

    /// Order Blocks activos detectados por OrderBlockDetector.
    /// None hasta que el detector tenga suficiente historia.
    #[serde(default)]
    pub order_blocks: Option<OrderBlockContext>,

    /// Fair Value Gaps activos detectados por FvgDetector.
    /// None hasta que el detector tenga suficiente historia.
    #[serde(default)]
    pub fvg: Option<FvgContext>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StrategySignal {
    pub action: StrategyAction,
    pub strategy_id: Option<StrategyId>,
    pub side: Option<Side>,
    pub regime: Regime,
    pub entry_price: Option<f64>,
    pub stop_price: Option<f64>,
    pub target_price: Option<f64>,
    pub score: f64,
    pub ttl_ms: i64,
    pub evidence: Vec<String>,
    pub missing: Vec<String>,
    pub invalidation: Vec<String>,
    pub created_at_ms: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StrategyConfig {
    pub enabled: bool,
    pub max_spread_bps: f64,
    pub max_vpin: f64,
    pub min_score: f64,
    pub default_ttl_ms: i64,
    /// Minimum R:R to emit a signal (default 1.5).
    pub min_rr: f64,
    /// Maximum R:R considered reachable on M5 BTC (default 8.0); filters fantasy targets.
    pub max_rr_m5: f64,

    // LiquidationHunt
    /// Minimum USD liquidated in 5 min to confirm hunt is in progress.
    pub liq_hunt_min_usd: f64,
    /// USD in 60s that constitutes a cascade (too late to enter).
    pub liq_cascade_threshold: f64,
    pub liq_ttl_ms: i64,

    // FundingExhaustionReversal
    /// Absolute funding rate that triggers extreme regime (0.0006 = 0.06%).
    pub funding_extreme_threshold: f64,
    pub funding_ttl_ms: i64,
    /// Top traders long pct minimum for FER Long (neutral-to-bullish positioning).
    pub fer_top_long_min: f64,
    /// Retail long pct maximum for FER Long (retail not overwhelmingly long).
    pub fer_retail_long_max: f64,

    // SmartMoneyDivergence
    /// Top traders long pct below this → smart money predominantly short.
    pub smart_short_threshold: f64,
    /// Retail long pct above this → retail predominantly long.
    pub retail_long_threshold: f64,
    /// Minimum divergence (retail_long - top_traders_long) to trigger.
    pub min_divergence: f64,
    pub smd_ttl_ms: i64,

    // ── Fase 1: nuevas opciones ───────────────────────────────────────────────

    /// Filtrar señales según la sesión de trading activa.
    /// Si false, se omite el filtro y todas las sesiones son válidas.
    pub session_filter_enabled: bool,

    /// min_score para estrategias institucionales (LiqHunt, FER, SMD).
    /// Separado porque sus señales son infrecuentes pero de alta convicción.
    pub min_score_institutional: f64,

    /// Activar multiplicador HTF basado en MarketStructureContext.
    /// Si false, el multiplicador vale 1.0 (neutro).
    pub htf_scoring_enabled: bool,

    /// Activar bloqueo de toxic_flow_gate cuando spoof_detected en dirección de señal.
    pub spoof_gate_enabled: bool,
}

impl Default for StrategyConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            max_spread_bps: 2.0,
            max_vpin: 0.75,
            // PLACEHOLDER — el umbral real se determina en Fase D analizando
            // la distribución de scores reales. No optimizar este número antes de eso.
            min_score: 0.60,
            default_ttl_ms: 250 * 60 * 1000,
            min_rr: 1.5,
            max_rr_m5: 8.0,
            liq_hunt_min_usd: 500_000.0,
            liq_cascade_threshold: 5_000_000.0,
            liq_ttl_ms: 10 * 60 * 1000,
            funding_extreme_threshold: 0.0006,
            funding_ttl_ms: 30 * 60 * 1000,
            fer_top_long_min: 0.46,
            fer_retail_long_max: 0.58,
            smart_short_threshold: 0.45,
            retail_long_threshold: 0.60,
            min_divergence: 0.18,
            smd_ttl_ms: 20 * 60 * 1000,
            session_filter_enabled: false,
            min_score_institutional: 0.55,
            htf_scoring_enabled: false,
            spoof_gate_enabled: false,
        }
    }
}
