use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::session::session_tracker::{SessionPhase, TradingSession};
use crate::strategy::types::{ImbalanceSide, Regime, Side, StrategyAction, StrategyMarketContext};

// ── Ciclo de madurez ─────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum StrategyMaturity {
    /// Detecta y loggea, sin señal al router ni al paper.
    ObserveOnly,
    /// Genera LabSignal, no toca Core ni paper principal.
    ShadowLab,
    /// Simula con costos reales en paper separado.
    PaperCandidate,
    /// Puede entrar a un router secundario de validación.
    PaperPromoted,
    /// Compite en el router principal del Core.
    CoreActive,
}

// ── Estado en tiempo de ejecución ────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum StrategyRuntimeStatus {
    /// Faltan datos mínimos o sesión incorrecta.
    Asleep,
    /// Fenómeno parcial detectado, no alcanza para señal completa.
    Observed,
    /// Hipótesis generó señal completa con entry/stop/target.
    ShadowSignal,
    /// Un gate bloqueó el setup antes de emitir señal.
    Blocked { reason: BlockReason },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum BlockReason {
    SessionFilter,
    DataQuality { missing: Vec<String> },
    SpreadGate,
    RegimeStress,
    CooldownActive,
    RRTooLow { calculated: f64, minimum: f64 },
    LiqInstability,
}

// ── IDs de estrategias del Lab ────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum LabStrategyId {
    // Core — detectores existentes con wrapper Lab
    VwapContinuation,
    VwapRejection,
    LvnDisplacement,
    OfiContinuation,
    // Lab observe-only
    AbsorptionTrapReversal,
    SessionImbalanceBreakout,
    LiquidityMagnet,
    PositioningExpansion,
    OrderBlockFlowRetest,
}

impl LabStrategyId {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::VwapContinuation => "VwapContinuation",
            Self::VwapRejection => "VwapRejection",
            Self::LvnDisplacement => "LvnDisplacement",
            Self::OfiContinuation => "OfiContinuation",
            Self::AbsorptionTrapReversal => "AbsorptionTrapReversal",
            Self::SessionImbalanceBreakout => "SessionImbalanceBreakout",
            Self::LiquidityMagnet => "LiquidityMagnet",
            Self::PositioningExpansion => "PositioningExpansion",
            Self::OrderBlockFlowRetest => "OrderBlockFlowRetest",
        }
    }
}

// ── Nivel LVN con ancho de zona ───────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LvnLevel {
    pub price: f64,
    /// Ancho de la zona. None hasta que el VP exponga esta información.
    pub width: Option<f64>,
}

// ── Snapshot completo del mercado en el momento de la señal ──────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LabFeatureSnapshot {
    // Precio / Tiempo
    pub symbol: String,
    pub price: f64,
    pub timestamp_ms: i64,
    /// Sesión activa (Asia/London/etc.) — para SessionGate y análisis offline.
    pub session: Option<TradingSession>,
    /// Sub-fase (OpeningRush/Open/Mid/Close) — útil para LiqHunt.
    pub session_phase: Option<SessionPhase>,
    pub regime: Regime,

    // VWAP
    pub vwap_session: Option<f64>,
    pub avwap_bos: Option<f64>,
    /// abs(price - vwap_session) / atr. None si vwap o atr no disponibles.
    pub vwap_distance_atr: Option<f64>,

    // Volume Profile
    pub poc: Option<f64>,
    pub vah: Option<f64>,
    pub val: Option<f64>,
    pub hvn_above: Option<f64>,
    pub hvn_below: Option<f64>,
    pub lvn_nearby: Option<LvnLevel>,

    // Orderflow — Option<f64> para no guardar 0.0 falso cuando el dato falta.
    // Si llega None en el contexto, se agrega el nombre del campo a missing_data.
    pub cvd: Option<f64>,
    pub cvd_slope: Option<f64>,
    pub delta: Option<f64>,
    pub taker_imbalance: Option<f64>,
    pub fast_slope: Option<f64>,
    pub stacked_imbalance: ImbalanceSide,

    // Orderbook — Option<f64> por la misma razón
    pub obi_l5: Option<f64>,
    pub obi_l10: Option<f64>,
    pub obi_l20: Option<f64>,
    pub microprice: Option<f64>,
    pub spread_bps: Option<f64>,
    pub thin_zone_above: bool,
    pub thin_zone_below: bool,
    pub walls_above: Vec<f64>,
    pub walls_below: Vec<f64>,

    // Institucional
    pub oi_delta: Option<f64>,
    /// Z-score de OI delta. None hasta que OiTracker implemente la ventana.
    pub oi_delta_zscore: Option<f64>,
    pub funding_rate: Option<f64>,
    pub liq_total_usd_5m: Option<f64>,

    pub atr: Option<f64>,
}

impl LabFeatureSnapshot {
    pub fn from_ctx(ctx: &StrategyMarketContext) -> Self {
        let sess = ctx.session.as_ref();
        let vp = &ctx.volume_profile;
        let flow = &ctx.flow;
        let ob = &ctx.orderbook;
        let inst = ctx.institutional.as_ref();
        let price = ctx.price;

        let vwap_distance_atr = ctx.vwap.vwap_session.zip(ctx.atr).map(|(vw, atr)| {
            if atr > 0.0 {
                (price - vw).abs() / atr
            } else {
                0.0
            }
        });

        let hvn_above = vp
            .hvn_nearby
            .iter()
            .copied()
            .filter(|&h| h > price)
            .reduce(f64::min);
        let hvn_below = vp
            .hvn_nearby
            .iter()
            .copied()
            .filter(|&h| h < price)
            .reduce(f64::max);
        let lvn_nearby = vp
            .lvn_nearby
            .first()
            .map(|&p| LvnLevel { price: p, width: None });

        Self {
            symbol: ctx.symbol.clone(),
            price,
            timestamp_ms: ctx.timestamp_ms,
            session: sess.map(|s| s.session),
            session_phase: sess.map(|s| s.phase),
            regime: ctx.regime,

            vwap_session: ctx.vwap.vwap_session,
            avwap_bos: ctx.vwap.avwap_bos,
            vwap_distance_atr,

            poc: vp.poc,
            vah: vp.vah,
            val: vp.val,
            hvn_above,
            hvn_below,
            lvn_nearby,

            cvd: flow.cvd,
            cvd_slope: flow.cvd_slope,
            delta: flow.delta,
            taker_imbalance: flow.taker_imbalance,
            fast_slope: flow.fast_slope,
            stacked_imbalance: flow.stacked_imbalance,

            obi_l5: ob.obi_l5,
            obi_l10: ob.obi_l10,
            obi_l20: ob.obi_l20,
            microprice: ob.microprice,
            spread_bps: ob.spread_bps,
            thin_zone_above: ob.thin_zone_above,
            thin_zone_below: ob.thin_zone_below,
            walls_above: ob.walls_above.clone(),
            walls_below: ob.walls_below.clone(),

            oi_delta: flow.oi_delta,
            oi_delta_zscore: flow.oi_delta_zscore,
            funding_rate: flow.funding_rate,
            liq_total_usd_5m: inst.map(|i| i.liquidations.total_usd_5m),

            atr: ctx.atr,
        }
    }
}

// ── Señal del Lab ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LabSignal {
    pub signal_id: Uuid,
    pub strategy_id: LabStrategyId,
    pub status: StrategyRuntimeStatus,
    pub maturity: StrategyMaturity,
    pub timestamp_ms: i64,
    /// Some solo cuando status == ShadowSignal
    pub action: Option<StrategyAction>,
    pub side: Option<Side>,
    pub entry_price: Option<f64>,
    pub target: Option<f64>,
    pub stop: Option<f64>,
    pub rr: Option<f64>,
    /// 0.0–1.0 según condiciones cumplidas
    pub confidence: f64,
    pub snapshot: LabFeatureSnapshot,
    /// Nombres de campos que llegaron None en el contexto
    pub missing_data: Vec<String>,
}

impl LabSignal {
    pub fn asleep(
        strategy_id: LabStrategyId,
        maturity: StrategyMaturity,
        ctx: &StrategyMarketContext,
        missing: Vec<String>,
    ) -> Self {
        Self {
            signal_id: Uuid::new_v4(),
            strategy_id,
            status: StrategyRuntimeStatus::Asleep,
            maturity,
            timestamp_ms: ctx.timestamp_ms,
            action: None,
            side: None,
            entry_price: None,
            target: None,
            stop: None,
            rr: None,
            confidence: 0.0,
            snapshot: LabFeatureSnapshot::from_ctx(ctx),
            missing_data: missing,
        }
    }

    pub fn blocked(
        strategy_id: LabStrategyId,
        maturity: StrategyMaturity,
        ctx: &StrategyMarketContext,
        reason: BlockReason,
    ) -> Self {
        Self {
            signal_id: Uuid::new_v4(),
            strategy_id,
            status: StrategyRuntimeStatus::Blocked { reason },
            maturity,
            timestamp_ms: ctx.timestamp_ms,
            action: None,
            side: None,
            entry_price: None,
            target: None,
            stop: None,
            rr: None,
            confidence: 0.0,
            snapshot: LabFeatureSnapshot::from_ctx(ctx),
            missing_data: vec![],
        }
    }

    pub fn observed(
        strategy_id: LabStrategyId,
        maturity: StrategyMaturity,
        ctx: &StrategyMarketContext,
        confidence: f64,
    ) -> Self {
        Self {
            signal_id: Uuid::new_v4(),
            strategy_id,
            status: StrategyRuntimeStatus::Observed,
            maturity,
            timestamp_ms: ctx.timestamp_ms,
            action: None,
            side: None,
            entry_price: None,
            target: None,
            stop: None,
            rr: None,
            confidence,
            snapshot: LabFeatureSnapshot::from_ctx(ctx),
            missing_data: vec![],
        }
    }
}

// ── Outcomes multi-horizonte ──────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HorizonOutcome {
    pub price_at_horizon: f64,
    /// Positivo = a favor de la señal, negativo = en contra
    pub r_achieved: f64,
    pub direction_correct: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum OutcomeStatus {
    TargetHit,
    StopHit,
    TtlExpired { exit_price: f64, r_achieved: f64 },
    StillOpen,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LabOutcome {
    pub signal_id: Uuid,
    pub strategy_id: LabStrategyId,
    pub entry_price: f64,
    pub target: f64,
    pub stop: f64,
    pub side: Side,

    // Horizontes sub-barra (requieren intrabar module — None en Fase 0/1)
    pub outcome_30s: Option<HorizonOutcome>,
    pub outcome_1m: Option<HorizonOutcome>,
    pub outcome_3m: Option<HorizonOutcome>,
    // Horizontes por barra M5
    pub outcome_5m: Option<HorizonOutcome>,
    pub outcome_15m: Option<HorizonOutcome>,
    /// Al vencer TTL (N barras configuradas)
    pub outcome_ttl: Option<HorizonOutcome>,

    /// Max favorable excursion en R desde entry
    pub mfe: Option<f64>,
    /// Max adverse excursion en R desde entry
    pub mae: Option<f64>,
    pub final_status: Option<OutcomeStatus>,
}

// ── Configuración del Lab ─────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct LabConfig {
    pub enabled: bool,
    pub session_gate_enabled: bool,
    pub spread_gate_max_bps: f64,
    /// Barras M5 máximas antes de cerrar un outcome como TtlExpired
    pub ttl_bars: u32,
}

impl Default for LabConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            session_gate_enabled: true,
            spread_gate_max_bps: 3.0,
            ttl_bars: 50, // ~250 min en M5
        }
    }
}

impl LabConfig {
    pub fn from_env() -> Self {
        Self {
            enabled: std::env::var("LAB_ENABLED")
                .map(|v| v.eq_ignore_ascii_case("true") || v == "1")
                .unwrap_or(false),
            ..Self::default()
        }
    }
}
