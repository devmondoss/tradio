use super::target_selector::TargetSelector;
use super::trade_manager::{ActiveTrade, TradeConfig};
use super::types::*;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::PathBuf;

// === VALORES DE ARRANQUE — se tunean en Fase D con datos reales ===
// Todas las constantes se pueden sobreescribir con variables de entorno.
const DEFAULT_INITIAL_CAPITAL: f64 = 300.0; // PAPER_INITIAL_CAPITAL (USD)
const DEFAULT_LEVERAGE: f64 = 10.0; // PAPER_LEVERAGE — simulates Binance USDM Futures
const DEFAULT_MAX_CONCURRENT: usize = 1; // PAPER_MAX_POSITIONS
const DEFAULT_RISK_PCT: f64 = 0.01; // PAPER_RISK_PCT — 1% del capital por trade
const DEFAULT_SLIPPAGE_BPS: f64 = 1.0; // PAPER_SLIPPAGE_BPS — 1 bp por lado
const DEFAULT_TAKER_FEE: f64 = 0.0004; // PAPER_TAKER_FEE — 0.04% Binance perps
const DEFAULT_FUNDING_RATE: f64 = 0.0001; // PAPER_FUNDING_RATE — 0.01% por período de 8h
const FUNDING_INTERVAL_MS: i64 = 8 * 3_600 * 1_000; // Binance: 00:00, 08:00, 16:00 UTC

// =================== Config ===================

fn read_env_f64(key: &str, default: f64) -> f64 {
    std::env::var(key)
        .ok()
        .and_then(|v| v.parse::<f64>().ok())
        .filter(|v| v.is_finite() && *v > 0.0)
        .unwrap_or(default)
}

fn read_env_usize(key: &str, default: usize) -> usize {
    std::env::var(key)
        .ok()
        .and_then(|v| v.parse::<usize>().ok())
        .filter(|&v| v > 0)
        .unwrap_or(default)
}

pub struct PaperConfig {
    pub initial_capital: f64,
    pub leverage: f64,
    pub max_concurrent_positions: usize,
    pub risk_pct: f64,
    pub slippage_bps: f64,
    pub taker_fee: f64,
    pub funding_rate: f64,
}

impl PaperConfig {
    pub fn from_env() -> Self {
        Self {
            initial_capital: read_env_f64("PAPER_INITIAL_CAPITAL", DEFAULT_INITIAL_CAPITAL),
            leverage: read_env_f64("PAPER_LEVERAGE", DEFAULT_LEVERAGE),
            max_concurrent_positions: read_env_usize("PAPER_MAX_POSITIONS", DEFAULT_MAX_CONCURRENT),
            risk_pct: read_env_f64("PAPER_RISK_PCT", DEFAULT_RISK_PCT),
            slippage_bps: read_env_f64("PAPER_SLIPPAGE_BPS", DEFAULT_SLIPPAGE_BPS),
            taker_fee: read_env_f64("PAPER_TAKER_FEE", DEFAULT_TAKER_FEE),
            funding_rate: read_env_f64("PAPER_FUNDING_RATE", DEFAULT_FUNDING_RATE),
        }
    }
}

// =================== Structs ===================

/// Una posición abierta en el motor de paper trading.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct PaperPosition {
    pub id: i64,
    pub symbol: String,
    pub strategy_id: Option<StrategyId>,
    pub side: Side,
    /// Precio de fill real (con slippage aplicado).
    pub entry_price: f64,
    /// Precio de entry que la señal pedía, sin slippage.
    pub intended_entry: f64,
    pub stop_price: Option<f64>,
    pub target_price: Option<f64>,
    pub size: f64,
    /// size × entry_price (con slippage).
    pub notional: f64,
    /// Capital inmovilizado para abrir la posición. Con leverage=1 coincide con notional.
    #[serde(default)]
    pub margin: f64,
    pub score: f64,
    pub opened_at_ms: i64,
    pub ttl_ms: i64,
    /// Fees acumuladas (entry ya incluida; se suma la de salida al cerrar).
    pub fees_paid: f64,
    /// Funding acumulado (positivo = pagado, negativo = recibido).
    pub funding_paid: f64,
    /// Balance antes de abrir esta posición (para calcular net_pnl_pct).
    pub balance_at_open: f64,
    // MFE/MAE tracking — no van al output serializado, solo a ClosedTrade.
    highest: f64,
    lowest: f64,
    // Niveles de contexto en el momento de apertura para invalidación semántica.
    #[serde(default)]
    pub entry_vwap: Option<f64>,
    #[serde(default)]
    pub entry_val: Option<f64>,
    #[serde(default)]
    pub entry_vah: Option<f64>,
    /// Dynamic stop management. None falls back to fixed stop/target from signal.
    #[serde(default)]
    pub active_trade: Option<ActiveTrade>,
}

impl PaperPosition {
    fn update_excursion(&mut self, high: f64, low: f64) {
        if high > self.highest {
            self.highest = high;
        }
        if low < self.lowest {
            self.lowest = low;
        }
    }

    fn mfe(&self) -> f64 {
        match self.side {
            Side::Long => (self.highest - self.entry_price).max(0.0),
            Side::Short => (self.entry_price - self.lowest).max(0.0),
        }
    }

    fn mae(&self) -> f64 {
        match self.side {
            Side::Long => (self.entry_price - self.lowest).max(0.0),
            Side::Short => (self.highest - self.entry_price).max(0.0),
        }
    }

    fn unrealized_pnl(&self, bar_close: f64) -> f64 {
        match self.side {
            Side::Long => self.size * (bar_close - self.entry_price),
            Side::Short => self.size * (self.entry_price - bar_close),
        }
    }

    /// Returns true when key contextual levels crossed by price → semantic invalidation.
    fn check_invalidation(&self, ctx: &StrategyMarketContext) -> bool {
        let px = ctx.price;
        match self.side {
            Side::Long => {
                if self.entry_vwap.map(|v| px < v).unwrap_or(false) {
                    return true;
                }
                if self.entry_val.map(|v| px < v).unwrap_or(false) {
                    return true;
                }
            }
            Side::Short => {
                if self.entry_vwap.map(|v| px > v).unwrap_or(false) {
                    return true;
                }
                if self.entry_vah.map(|v| px > v).unwrap_or(false) {
                    return true;
                }
            }
        }
        false
    }

    /// Conservador: si stop y target se tocan en la misma vela, stop gana.
    fn check_close(&self, bar_high: f64, bar_low: f64, now_ms: i64) -> Option<&'static str> {
        let stop_hit = self
            .stop_price
            .map(|s| match self.side {
                Side::Long => bar_low <= s,
                Side::Short => bar_high >= s,
            })
            .unwrap_or(false);

        let target_hit = self
            .target_price
            .map(|t| match self.side {
                Side::Long => bar_high >= t,
                Side::Short => bar_low <= t,
            })
            .unwrap_or(false);

        let expires_at = self.opened_at_ms + self.ttl_ms;

        if stop_hit {
            Some("STOP_HIT")
        } else if target_hit {
            Some("TARGET_HIT")
        } else if now_ms >= expires_at {
            Some("TTL_EXPIRED")
        } else {
            None
        }
    }
}

/// Trade cerrado, incluyendo toda la contabilidad.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ClosedTrade {
    pub id: i64,
    pub symbol: String,
    pub strategy_id: Option<String>,
    pub side: String,
    pub entry_price: f64,
    pub intended_entry: f64,
    pub exit_price: f64,
    pub stop_price: Option<f64>,
    pub target_price: Option<f64>,
    pub size: f64,
    pub notional: f64,
    pub score: f64,
    pub opened_at_ms: i64,
    pub closed_at_ms: i64,
    pub ttl_ms: i64,
    pub close_reason: String,
    pub gross_pnl: f64,
    pub fees_paid: f64,
    pub funding_paid: f64,
    pub net_pnl: f64,
    pub net_pnl_pct: f64,
    pub mfe: f64,
    pub mae: f64,
    /// Initial stop at trade open (same as stop_price when no active trade).
    #[serde(default)]
    pub stop_initial: Option<f64>,
    /// Final stop price at close (reflects trailing if active).
    #[serde(default)]
    pub stop_final: Option<f64>,
    /// StopState name at close: "Original", "BreakEven", "TrailingStructural".
    #[serde(default)]
    pub stop_state_at_close: Option<String>,
    /// Structural target used by TradeManager (may differ from original signal target).
    #[serde(default)]
    pub target_structural: Option<f64>,
    /// Intermediate level between entry and target (break-even trigger).
    #[serde(default)]
    pub intermediate_level: Option<f64>,
    /// TradePhase name at close: "Open", "Level1Confirmed", "TargetExceeded".
    #[serde(default)]
    pub trade_phase_at_close: Option<String>,
    /// Number of bars the trade was open.
    #[serde(default)]
    pub bars_open: u32,
}

/// Registra contradicciones (señal opuesta a posición abierta del mismo symbol).
/// Propósito: datos para análisis en Fase D — NO simplificar.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ContradictionEvent {
    pub timestamp_ms: i64,
    pub symbol: String,
    pub existing_strategy: String,
    pub existing_side: String,
    pub incoming_strategy: String,
    pub incoming_side: String,
    pub resolution: String,
}

// =================== PaperAccount ===================

/// Snapshot serializable del PaperAccount. Config excluida intencionalmente:
/// se re-lee desde env vars en cada arranque para respetar cambios en prod.
#[derive(serde::Serialize, serde::Deserialize)]
struct PaperAccountState {
    balance: f64,
    equity: f64,
    open_positions: Vec<PaperPosition>,
    closed_trades: Vec<ClosedTrade>,
    contradiction_log: Vec<ContradictionEvent>,
    equity_curve: Vec<(i64, f64)>,
    last_bar_ms: i64,
    next_id: i64,
}

pub struct PaperAccount {
    pub balance: f64,
    pub equity: f64,
    pub config: PaperConfig,
    pub trade_config: TradeConfig,
    pub open_positions: Vec<PaperPosition>,
    pub closed_trades: Vec<ClosedTrade>,
    pub contradiction_log: Vec<ContradictionEvent>,
    /// Un punto por bar-close: (timestamp_ms, equity).
    pub equity_curve: Vec<(i64, f64)>,
    last_bar_ms: i64,
    next_id: i64,
}

impl Default for PaperAccount {
    fn default() -> Self {
        Self::new()
    }
}

impl PaperAccount {
    pub fn new() -> Self {
        let config = PaperConfig::from_env();
        let balance = config.initial_capital;
        let trade_config = TradeConfig { risk_pct: config.risk_pct, ..TradeConfig::default() };
        Self {
            equity: balance,
            balance,
            config,
            trade_config,
            open_positions: Vec::new(),
            closed_trades: Vec::new(),
            contradiction_log: Vec::new(),
            equity_curve: Vec::new(),
            last_bar_ms: -1,
            next_id: 1,
        }
    }

    /// Intenta restaurar desde paper_account_state.json. Si no existe o falla el parse,
    /// arranca con una cuenta nueva. El app nunca debe negarse a abrir por estado corrupto.
    pub fn load_or_new() -> Self {
        let path = shadow_events_dir().join("paper_account_state.json");
        if path.exists() {
            match fs::read_to_string(&path) {
                Ok(contents) => match serde_json::from_str::<PaperAccountState>(&contents) {
                    Ok(state) => {
                        // Posiciones restauradas conservan su opened_at_ms original.
                        // En el primer bar-close post-restauración el motor las evalúa normalmente:
                        //   - TTL se evalúa contra now_ms real → una señal expirada mientras la app
                        //     estuvo cerrada cierra inmediatamente como TTL_EXPIRED (correcto).
                        //   - Stop/target se checan con bar H/L → gaps del downtime se detectan
                        //     con granularidad de 1 bar, consistente con el resto del modelo.
                        eprintln!(
                            "paper: estado restaurado — balance={:.2} open={} closed={}",
                            state.balance,
                            state.open_positions.len(),
                            state.closed_trades.len(),
                        );
                        let config = PaperConfig::from_env();
                        let trade_config =
                            TradeConfig { risk_pct: config.risk_pct, ..TradeConfig::default() };
                        return Self {
                            balance: state.balance,
                            equity: state.equity,
                            config,
                            trade_config,
                            open_positions: state.open_positions,
                            closed_trades: state.closed_trades,
                            contradiction_log: state.contradiction_log,
                            equity_curve: state.equity_curve,
                            last_bar_ms: state.last_bar_ms,
                            next_id: state.next_id,
                        };
                    }
                    Err(e) => {
                        eprintln!(
                            "paper: paper_account_state.json corrupto, arrancando de cero: {e}"
                        );
                    }
                },
                Err(e) => {
                    eprintln!(
                        "paper: no se pudo leer paper_account_state.json, arrancando de cero: {e}"
                    );
                }
            }
        }
        Self::new()
    }

    /// Persiste el estado completo de forma atómica: escribe a .tmp y renombra.
    /// El rename es atómico en el filesystem — un crash a mitad no corrompe el archivo anterior.
    /// Solo se llama en 3 eventos materiales: apertura, cierre, y contradicción.
    fn save_state(&self) {
        let dir = shadow_events_dir();
        let path = dir.join("paper_account_state.json");
        let tmp_path = dir.join("paper_account_state.json.tmp");

        let state = PaperAccountState {
            balance: self.balance,
            equity: self.equity,
            open_positions: self.open_positions.clone(),
            closed_trades: self.closed_trades.clone(),
            contradiction_log: self.contradiction_log.clone(),
            equity_curve: self.equity_curve.clone(),
            last_bar_ms: self.last_bar_ms,
            next_id: self.next_id,
        };

        match serde_json::to_string(&state) {
            Ok(json) => {
                if let Err(e) = fs::write(&tmp_path, json) {
                    eprintln!("paper: no se pudo escribir estado temporal: {e}");
                    return;
                }
                if let Err(e) = fs::rename(&tmp_path, &path) {
                    eprintln!("paper: no se pudo renombrar archivo de estado: {e}");
                }
            }
            Err(e) => eprintln!("paper: no se pudo serializar estado: {e}"),
        }
    }

    /// Punto de entrada en cada bar-close. Orden: funding → cierres → apertura → equity.
    pub fn on_bar_close(
        &mut self,
        symbol: &str,
        bar_close: f64,
        bar_high: f64,
        bar_low: f64,
        now_ms: i64,
        signal: Option<&StrategySignal>,
        ctx: Option<&StrategyMarketContext>,
    ) {
        // A — Funding y evaluación de posiciones abiertas
        self.apply_funding_if_crossed(now_ms);
        self.process_position_closes(bar_close, bar_high, bar_low, now_ms, ctx);

        // B — Abrir nueva posición si hay señal
        if let Some(sig) = signal
            && sig.action == StrategyAction::ShadowSignal
        {
            self.try_open_position(symbol, sig, bar_close, now_ms, ctx);
        }

        // C — Equity y curva
        self.update_equity(bar_close);
        self.equity_curve.push((now_ms, self.equity));
        self.last_bar_ms = now_ms;
    }

    /// Aplica funding si se cruzó un boundary de 8h entre el bar anterior y este.
    fn apply_funding_if_crossed(&mut self, now_ms: i64) {
        if self.last_bar_ms < 0 || self.open_positions.is_empty() {
            return;
        }
        let prev_period = self.last_bar_ms / FUNDING_INTERVAL_MS;
        let curr_period = now_ms / FUNDING_INTERVAL_MS;
        if curr_period <= prev_period {
            return;
        }
        let funding_rate = self.config.funding_rate;
        for pos in &mut self.open_positions {
            let amount = match pos.side {
                // Tasa positiva: Long paga, Short cobra (negative amount = ingreso).
                Side::Long => pos.notional * funding_rate,
                Side::Short => -(pos.notional * funding_rate),
            };
            pos.funding_paid += amount;
            // No se descuenta del balance aquí — se difiere al cierre vía net_pnl.
        }
    }

    fn process_position_closes(
        &mut self,
        bar_close: f64,
        bar_high: f64,
        bar_low: f64,
        now_ms: i64,
        ctx: Option<&StrategyMarketContext>,
    ) {
        let atr = ctx.and_then(|c| c.atr).unwrap_or(0.0);
        let tc = self.trade_config;
        let mut i = 0;
        while i < self.open_positions.len() {
            self.open_positions[i].update_excursion(bar_high, bar_low);
            let invalidated = ctx
                .map(|c| self.open_positions[i].check_invalidation(c))
                .unwrap_or(false);
            let reason: Option<&'static str> =
                if let Some(ref mut at) = self.open_positions[i].active_trade {
                    at.on_bar_close(bar_high, bar_low, bar_close, atr, tc, invalidated)
                        .map(|r| r.as_str())
                } else {
                    self.open_positions[i]
                        .check_close(bar_high, bar_low, now_ms)
                        .or(if invalidated { Some("INVALIDATED") } else { None })
                };
            if let Some(reason) = reason {
                let pos = self.open_positions.remove(i);
                let trade = self.build_closed_trade(pos, reason, bar_close, now_ms);
                log_paper_trade(&trade);
                self.closed_trades.push(trade);
                // update equity with actual bar_close before saving so the persisted
                // equity reflects the post-close balance (not the stale pre-close value)
                self.update_equity(bar_close);
                self.save_state();
            } else {
                i += 1;
            }
        }
    }

    fn build_closed_trade(
        &mut self,
        pos: PaperPosition,
        reason: &str,
        bar_close: f64,
        now_ms: i64,
    ) -> ClosedTrade {
        let exit_level = if let Some(ref at) = pos.active_trade {
            match reason {
                "STOP_HIT" | "TRAILING_HIT" => at.stop_price,
                "TARGET_HIT" => at.levels.target,
                _ => bar_close,
            }
        } else {
            match reason {
                "STOP_HIT" => pos.stop_price.unwrap_or(bar_close),
                "TARGET_HIT" => pos.target_price.unwrap_or(bar_close),
                _ => bar_close,
            }
        };

        let exit_price = apply_slippage_exit(exit_level, pos.side, self.config.slippage_bps);
        let exit_fee = pos.size * exit_price * self.config.taker_fee;
        let total_fees = pos.fees_paid + exit_fee;

        let gross_pnl = match pos.side {
            Side::Long => pos.size * (exit_price - pos.entry_price),
            Side::Short => pos.size * (pos.entry_price - exit_price),
        };

        let net_pnl = gross_pnl - total_fees - pos.funding_paid;
        let net_pnl_pct = if pos.balance_at_open > 0.0 {
            net_pnl / pos.balance_at_open
        } else {
            0.0
        };

        // Margen devuelto + resultado neto del trade.
        // entry_fee y funding_paid no se descontaron del balance en su momento,
        // por eso net_pnl ya los cubre y la fórmula no tiene términos implícitos.
        self.balance += self.position_margin(&pos) + net_pnl;

        let (stop_initial, stop_final, stop_state_at_close, target_structural, intermediate_level, trade_phase_at_close, bars_open) =
            if let Some(ref at) = pos.active_trade {
                (
                    Some(at.stop_initial),
                    Some(at.stop_price),
                    Some(at.stop_state.name().to_string()),
                    Some(at.levels.target),
                    at.levels.intermediate,
                    Some(at.phase.name().to_string()),
                    at.bars_open,
                )
            } else {
                (None, pos.stop_price, None, pos.target_price, None, None, 0)
            };

        ClosedTrade {
            id: pos.id,
            symbol: pos.symbol.clone(),
            strategy_id: pos.strategy_id.map(|id| format!("{id:?}")),
            side: format!("{:?}", pos.side),
            entry_price: pos.entry_price,
            intended_entry: pos.intended_entry,
            exit_price,
            stop_price: pos.stop_price,
            target_price: pos.target_price,
            size: pos.size,
            notional: pos.notional,
            score: pos.score,
            opened_at_ms: pos.opened_at_ms,
            closed_at_ms: now_ms,
            ttl_ms: pos.ttl_ms,
            close_reason: reason.to_string(),
            gross_pnl,
            fees_paid: total_fees,
            funding_paid: pos.funding_paid,
            net_pnl,
            net_pnl_pct,
            mfe: pos.mfe(),
            mae: pos.mae(),
            stop_initial,
            stop_final,
            stop_state_at_close,
            target_structural,
            intermediate_level,
            trade_phase_at_close,
            bars_open,
        }
    }

    fn try_open_position(
        &mut self,
        symbol: &str,
        signal: &StrategySignal,
        bar_close: f64,
        now_ms: i64,
        ctx: Option<&StrategyMarketContext>,
    ) {
        let Some(side) = signal.side else { return };

        // Symbol-level conflict check FIRST — contradictions must always be recorded
        // even when we are already at max capacity.
        let conflict_side = self
            .open_positions
            .iter()
            .find(|p| p.symbol == symbol)
            .map(|p| (p.strategy_id, p.side));

        if let Some((existing_strategy, existing_side)) = conflict_side {
            if existing_side == side {
                // Misma dirección — no piramidar.
                return;
            }
            // Dirección opuesta — contradicción. Registrar siempre, incluso si no
            // hubiera cupo para abrir (la primera posición en el tiempo gana).
            let event = ContradictionEvent {
                timestamp_ms: now_ms,
                symbol: symbol.to_string(),
                existing_strategy: existing_strategy
                    .map(|id| format!("{id:?}"))
                    .unwrap_or_default(),
                existing_side: format!("{existing_side:?}"),
                incoming_strategy: signal
                    .strategy_id
                    .map(|id| format!("{id:?}"))
                    .unwrap_or_default(),
                incoming_side: format!("{side:?}"),
                resolution: "kept_existing_ignored_incoming".to_string(),
            };
            log_contradiction(&event);
            self.contradiction_log.push(event);
            self.save_state();
            return;
        }

        // Max concurrent check — solo para señales sin conflicto de symbol.
        if self.open_positions.len() >= self.config.max_concurrent_positions {
            return;
        }

        // Sizing: risk fijo porcentual.
        let intended_entry = signal.entry_price.unwrap_or(bar_close);
        let Some(stop) = signal.stop_price else {
            eprintln!("paper: no stop_price on signal — skipping (cannot size without stop)");
            return;
        };

        let raw_risk_per_unit = (intended_entry - stop).abs();
        if !raw_risk_per_unit.is_finite() || raw_risk_per_unit < 1e-10 {
            eprintln!("paper: degenerate risk_per_unit={raw_risk_per_unit:.6} — skipping");
            return;
        }

        // Floor risk_per_unit so size never exceeds max_notional, eliminating spurious
        // cap warnings on tight-stop signals. Formula: entry × risk_pct / leverage ensures
        // the resulting notional is exactly balance×leverage when the floor binds.
        // The actual stop_price in the position is unchanged — only sizing is affected.
        let min_risk_per_unit =
            intended_entry * self.config.risk_pct / self.config.leverage.max(1.0);
        let risk_per_unit = raw_risk_per_unit.max(min_risk_per_unit);

        let risk_amount = self.balance * self.config.risk_pct;
        let mut size = risk_amount / risk_per_unit;

        let entry_price = apply_slippage_entry(intended_entry, side, self.config.slippage_bps);
        let mut notional = size * entry_price;

        // Notional cap: guards against leverage > 1 or floating-point edge cases.
        let max_notional = self.balance * self.config.leverage;
        if notional > max_notional {
            size = max_notional / entry_price;
            notional = size * entry_price;
        }

        let entry_fee = notional * self.config.taker_fee;
        let balance_at_open = self.balance;
        let margin = notional / self.config.leverage.max(1e-10);
        self.balance -= margin;

        let id = self.next_id;
        self.next_id += 1;

        let (entry_vwap, entry_val, entry_vah) = ctx
            .map(|c| (c.vwap.vwap_session, c.volume_profile.val, c.volume_profile.vah))
            .unwrap_or((None, None, None));

        let active_trade = TargetSelector::from_signal(signal, ctx, side).and_then(|levels| {
            ActiveTrade::open(
                entry_price,
                stop.into(),
                levels,
                side,
                self.equity,
                self.trade_config,
            )
        });

        self.open_positions.push(PaperPosition {
            id,
            symbol: symbol.to_string(),
            strategy_id: signal.strategy_id,
            side,
            entry_price,
            intended_entry,
            stop_price: signal.stop_price,
            target_price: signal.target_price,
            size,
            notional,
            margin,
            score: signal.score,
            opened_at_ms: now_ms,
            ttl_ms: signal.ttl_ms,
            fees_paid: entry_fee,
            funding_paid: 0.0,
            balance_at_open,
            highest: entry_price,
            lowest: entry_price,
            entry_vwap,
            entry_val,
            entry_vah,
            active_trade,
        });
        self.save_state();
    }

    fn update_equity(&mut self, bar_close: f64) {
        // Equity = free cash + locked margin + unrealized PnL - deferred costs.
        let open_value: f64 = self
            .open_positions
            .iter()
            .map(|p| {
                self.position_margin(p) + p.unrealized_pnl(bar_close) - p.fees_paid - p.funding_paid
            })
            .sum();
        self.equity = self.balance + open_value;
    }

    fn position_margin(&self, pos: &PaperPosition) -> f64 {
        if pos.margin.is_finite() && pos.margin > 0.0 {
            pos.margin
        } else {
            // Backward-compatible fallback for persisted states written before `margin` existed.
            (pos.notional / self.config.leverage.max(1e-10)).min(pos.balance_at_open)
        }
    }
}

// =================== Slippage helpers ===================

fn apply_slippage_entry(price: f64, side: Side, slippage_bps: f64) -> f64 {
    let factor = slippage_bps / 10_000.0;
    match side {
        Side::Long => price * (1.0 + factor),
        Side::Short => price * (1.0 - factor),
    }
}

fn apply_slippage_exit(price: f64, side: Side, slippage_bps: f64) -> f64 {
    let factor = slippage_bps / 10_000.0;
    match side {
        Side::Long => price * (1.0 - factor),
        Side::Short => price * (1.0 + factor),
    }
}

// =================== I/O ===================

fn shadow_events_dir() -> PathBuf {
    let base = std::env::current_dir()
        .unwrap_or_else(|_| PathBuf::from("."))
        .join("logs");
    let _ = fs::create_dir_all(&base);
    base
}

fn log_paper_trade(trade: &ClosedTrade) {
    let path = shadow_events_dir().join("paper_trades.jsonl");
    if let Ok(json) = serde_json::to_string(trade)
        && let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&path)
    {
        let _ = writeln!(file, "{json}");
    }
}

fn log_contradiction(event: &ContradictionEvent) {
    let path = shadow_events_dir().join("contradictions.jsonl");
    if let Ok(json) = serde_json::to_string(event)
        && let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&path)
    {
        let _ = writeln!(file, "{json}");
    }
}

// =================== Tests ===================

#[cfg(test)]
mod tests {
    use super::*;

    fn make_signal(
        strategy: StrategyId,
        side: Side,
        entry: f64,
        stop: f64,
        target: f64,
        ttl_ms: i64,
        score: f64,
    ) -> StrategySignal {
        StrategySignal {
            action: StrategyAction::ShadowSignal,
            strategy_id: Some(strategy),
            side: Some(side),
            regime: Regime::Unknown,
            entry_price: Some(entry),
            stop_price: Some(stop),
            target_price: Some(target),
            score,
            ttl_ms,
            evidence: vec![],
            missing: vec![],
            invalidation: vec![],
            created_at_ms: 1_000_000,
        }
    }

    fn default_account() -> PaperAccount {
        let config = PaperConfig {
            initial_capital: 3_000.0,
            leverage: 1.0,
            max_concurrent_positions: 1,
            risk_pct: 0.01,
            slippage_bps: 1.0,
            taker_fee: 0.0004,
            funding_rate: 0.0001,
        };
        let balance = config.initial_capital;
        let trade_config = TradeConfig { risk_pct: config.risk_pct, ..TradeConfig::default() };
        PaperAccount {
            equity: balance,
            balance,
            config,
            trade_config,
            open_positions: Vec::new(),
            closed_trades: Vec::new(),
            contradiction_log: Vec::new(),
            equity_curve: Vec::new(),
            last_bar_ms: -1,
            next_id: 1,
        }
    }

    // Ejemplo completo del prompt: Long LvnBreakout con 3000 USD, target tocado 3 bars después.
    #[test]
    fn full_accounting_long_target_hit() {
        // Entry=100000, Stop=99750 (risk=250), Target=100750 (R:R=3.0)
        // entry=3000, stop=2750 (risk_per_unit=250 = 8.3% of price, above the 1% floor)
        // risk_amount = 3000 * 0.01 = 30, size = 30 / 250 = 0.12 BTC

        let sig = make_signal(
            StrategyId::LvnLiquidityVacuumBreakout,
            Side::Long,
            3_000.0, // entry
            2_750.0, // stop, risk_per_unit=250
            3_750.0, // target
            3_600_000,
            0.80,
        );

        let mut acc = default_account();
        // Bar N close — signal arrives
        acc.on_bar_close("BTCUSDT", 3_000.0, 3_010.0, 2_990.0, 1_000_000, Some(&sig), None);

        // risk_amount = 3000 * 0.01 = 30
        // size = 30 / 250 = 0.12
        // entry_fill = 3000 * 1.0001 = 3000.30 (slippage)
        // notional = 0.12 * 3000.30 = 360.036
        // entry_fee = 360.036 * 0.0004 = 0.1440144
        // balance_after_open = 3000 - 360.036 - 0.1440144 ≈ 2639.82
        assert_eq!(acc.open_positions.len(), 1);
        let pos = &acc.open_positions[0];
        let expected_size = 30.0 / 250.0; // 0.12
        assert!((pos.size - expected_size).abs() < 1e-6, "size={}", pos.size);
        let expected_entry = 3_000.0 * (1.0 + 1.0 / 10_000.0); // 3000.30
        assert!(
            (pos.entry_price - expected_entry).abs() < 1e-6,
            "entry={}",
            pos.entry_price
        );
        assert!((pos.intended_entry - 3_000.0).abs() < 1e-9);
        let expected_notional = expected_size * expected_entry;
        assert!(
            (pos.notional - expected_notional).abs() < 1e-6,
            "notional={}",
            pos.notional
        );
        let expected_entry_fee = expected_notional * 0.0004;
        assert!(
            (pos.fees_paid - expected_entry_fee).abs() < 1e-6,
            "fee={}",
            pos.fees_paid
        );
        // entry_fee is deferred to close — only notional is deducted from balance at open.
        let expected_balance = 3_000.0 - expected_notional;
        assert!(
            (acc.balance - expected_balance).abs() < 1e-4,
            "balance={}",
            acc.balance
        );

        // Bars N+1, N+2 — no signal, price moves up
        acc.on_bar_close("BTCUSDT", 3_200.0, 3_250.0, 3_180.0, 1_100_000, None, None);
        acc.on_bar_close("BTCUSDT", 3_400.0, 3_450.0, 3_380.0, 1_200_000, None, None);
        assert_eq!(acc.open_positions.len(), 1, "still open after 2 bars");

        // Bar N+3 — target hit (high=3760 >= 3750)
        acc.on_bar_close("BTCUSDT", 3_700.0, 3_760.0, 3_680.0, 1_300_000, None, None);

        assert_eq!(acc.open_positions.len(), 0, "position should be closed");
        assert_eq!(acc.closed_trades.len(), 1);
        let trade = &acc.closed_trades[0];
        assert_eq!(trade.close_reason, "TARGET_HIT");

        // exit_fill = 3750 * (1 - 0.0001) = 3749.625 (slippage exit long)
        let expected_exit = 3_750.0 * (1.0 - 1.0 / 10_000.0);
        assert!(
            (trade.exit_price - expected_exit).abs() < 1e-4,
            "exit={}",
            trade.exit_price
        );

        // gross_pnl = size * (exit - entry_fill)
        let expected_gross = expected_size * (expected_exit - expected_entry);
        assert!(
            (trade.gross_pnl - expected_gross).abs() < 1e-4,
            "gross={}",
            trade.gross_pnl
        );

        let exit_fee = expected_size * expected_exit * 0.0004;
        let expected_total_fees = expected_entry_fee + exit_fee;
        assert!(
            (trade.fees_paid - expected_total_fees).abs() < 1e-4,
            "fees={}",
            trade.fees_paid
        );

        let expected_net = expected_gross - expected_total_fees;
        assert!(
            (trade.net_pnl - expected_net).abs() < 1e-4,
            "net={}",
            trade.net_pnl
        );

        // Balance should be roughly 3000 + net_pnl
        assert!(
            (acc.balance - (3_000.0 + expected_net)).abs() < 1e-2,
            "final_balance={}",
            acc.balance
        );
    }

    #[test]
    fn max_concurrent_one_blocks_second_position() {
        let sig = make_signal(
            StrategyId::ValueAreaFailedAuction,
            Side::Long,
            1_000.0,
            900.0,
            1_200.0,
            3_600_000,
            0.75,
        );
        let sig2 = make_signal(
            StrategyId::LvnLiquidityVacuumBreakout,
            Side::Long,
            1_010.0,
            910.0,
            1_210.0,
            3_600_000,
            0.80,
        );

        let mut acc = default_account();
        acc.on_bar_close("BTCUSDT", 1_000.0, 1_010.0, 990.0, 1_000_000, Some(&sig), None);
        assert_eq!(acc.open_positions.len(), 1);

        // Second signal — same symbol, same side, different strategy. Should be blocked.
        acc.on_bar_close("BTCUSDT", 1_010.0, 1_020.0, 1_000.0, 1_100_000, Some(&sig2), None);
        assert_eq!(
            acc.open_positions.len(),
            1,
            "second position must be blocked (max=1)"
        );
    }

    #[test]
    fn contradiction_recorded_even_when_ignored() {
        let sig_long = make_signal(
            StrategyId::ValueAreaFailedAuction,
            Side::Long,
            1_000.0,
            900.0,
            1_200.0,
            3_600_000,
            0.75,
        );
        let sig_short = make_signal(
            StrategyId::VwapValuePullbackContinuation,
            Side::Short,
            1_010.0,
            1_110.0,
            810.0,
            3_600_000,
            0.70,
        );

        let mut acc = default_account();
        acc.on_bar_close(
            "BTCUSDT",
            1_000.0,
            1_010.0,
            990.0,
            1_000_000,
            Some(&sig_long),
            None,
        );
        assert_eq!(acc.open_positions.len(), 1);

        // Opposite side signal arrives — should be ignored but contradiction recorded.
        acc.on_bar_close(
            "BTCUSDT",
            1_010.0,
            1_020.0,
            1_000.0,
            1_100_000,
            Some(&sig_short),
            None,
        );
        assert_eq!(acc.open_positions.len(), 1, "existing long must survive");
        assert_eq!(
            acc.contradiction_log.len(),
            1,
            "contradiction must be recorded"
        );
        let event = &acc.contradiction_log[0];
        assert_eq!(event.resolution, "kept_existing_ignored_incoming");
        assert_eq!(event.existing_side, "Long");
        assert_eq!(event.incoming_side, "Short");
    }

    #[test]
    fn stop_hit_before_target_when_both_same_bar() {
        // Long: stop=900, target=1100. Bar: high=1150, low=850 — both hit.
        let sig = make_signal(
            StrategyId::ValueAreaFailedAuction,
            Side::Long,
            1_000.0,
            900.0,
            1_100.0,
            3_600_000,
            0.75,
        );

        let mut acc = default_account();
        acc.on_bar_close("BTCUSDT", 1_000.0, 1_010.0, 990.0, 1_000_000, Some(&sig), None);
        // Both stop and target hit in same bar
        acc.on_bar_close("BTCUSDT", 1_050.0, 1_150.0, 850.0, 1_100_000, None, None);

        assert_eq!(acc.closed_trades.len(), 1);
        assert_eq!(acc.closed_trades[0].close_reason, "STOP_HIT");
    }

    #[test]
    fn ttl_expiry_closes_position() {
        // ttl_ms = 60_000, opened_at_ms = 1_000_000, expires = 1_060_000
        let sig = make_signal(
            StrategyId::LvnLiquidityVacuumBreakout,
            Side::Long,
            1_000.0,
            900.0,
            1_200.0,
            60_000,
            0.80,
        );

        let mut acc = default_account();
        acc.on_bar_close("BTCUSDT", 1_000.0, 1_010.0, 990.0, 1_000_000, Some(&sig), None);
        // Price stays safe but TTL expires
        acc.on_bar_close("BTCUSDT", 1_010.0, 1_020.0, 1_005.0, 1_060_000, None, None);

        assert_eq!(acc.closed_trades.len(), 1);
        assert_eq!(acc.closed_trades[0].close_reason, "TTL_EXPIRED");
        // TTL_EXPIRED exits at bar_close (1010)
        let expected_exit = 1_010.0 * (1.0 - 1.0 / 10_000.0); // slippage long exit
        assert!((acc.closed_trades[0].exit_price - expected_exit).abs() < 1e-4);
    }

    #[test]
    fn short_position_pnl_correct() {
        // Short: entry=1000, stop=1100, target=800.
        // risk_per_unit = 100, risk_amount = 30, size = 0.30
        // entry_fill = 1000 * (1 - 0.0001) = 999.90 (slippage short entry)
        let sig = make_signal(
            StrategyId::VwapValuePullbackContinuation,
            Side::Short,
            1_000.0,
            1_100.0,
            800.0,
            3_600_000,
            0.75,
        );

        let mut acc = default_account();
        acc.on_bar_close("BTCUSDT", 1_000.0, 1_010.0, 990.0, 1_000_000, Some(&sig), None);
        let expected_entry = 1_000.0 * (1.0 - 1.0 / 10_000.0);
        let pos_size = acc.open_positions[0].size;
        assert!((acc.open_positions[0].entry_price - expected_entry).abs() < 1e-6);

        // Target hit (low=795 <= 800)
        acc.on_bar_close("BTCUSDT", 810.0, 820.0, 795.0, 1_100_000, None, None);

        let trade = &acc.closed_trades[0];
        assert_eq!(trade.close_reason, "TARGET_HIT");
        // exit_fill = 800 * (1 + 0.0001) = 800.08 (slippage short exit, always adverse)
        let expected_exit = 800.0 * (1.0 + 1.0 / 10_000.0);
        assert!((trade.exit_price - expected_exit).abs() < 1e-4);
        // gross_pnl = size * (entry - exit) for Short
        let expected_gross = pos_size * (expected_entry - expected_exit);
        assert!((trade.gross_pnl - expected_gross).abs() < 1e-4);
        assert!(trade.net_pnl < trade.gross_pnl, "fees must reduce net_pnl");
    }

    #[test]
    fn funding_accumulated_and_reflected_in_net_pnl() {
        // Entry=1000, stop=900 (risk=100), target=1300, ttl long enough to survive funding.
        // risk_amount=30, size=0.30, entry_fill=1000.10, notional≈300.03
        // funding_amount = notional * 0.0001 ≈ 0.030003 per 8h crossing
        let sig = make_signal(
            StrategyId::LvnLiquidityVacuumBreakout,
            Side::Long,
            1_000.0,
            900.0,
            1_300.0,
            86_400_000_000,
            0.80,
        );

        let mut acc = default_account();
        // Bar 0: open position
        acc.on_bar_close("BTCUSDT", 1_000.0, 1_010.0, 990.0, 0, Some(&sig), None);
        assert_eq!(acc.open_positions.len(), 1);

        // Bar 1: crosses first 8h boundary — funding accumulates in pos.funding_paid
        // Balance does NOT change (funding is deferred to close).
        let balance_before_funding = acc.balance;
        acc.on_bar_close(
            "BTCUSDT",
            1_010.0,
            1_020.0,
            1_005.0,
            FUNDING_INTERVAL_MS + 1,
            None,
            None,
        );

        assert!(
            acc.open_positions[0].funding_paid > 0.0,
            "Long should have positive funding_paid"
        );
        assert_eq!(
            acc.balance, balance_before_funding,
            "balance must not change when funding is crossed (deferred to close)"
        );

        let funding_paid = acc.open_positions[0].funding_paid;

        // Bar 2: target hit (high=1310 >= 1300)
        let pre_close_balance = acc.balance;
        acc.on_bar_close(
            "BTCUSDT",
            1_250.0,
            1_310.0,
            1_240.0,
            FUNDING_INTERVAL_MS + 2,
            None,
            None,
        );

        assert_eq!(acc.closed_trades.len(), 1);
        let trade = &acc.closed_trades[0];

        // funding_paid must be in the closed trade and must reduce net_pnl
        assert!(
            (trade.funding_paid - funding_paid).abs() < 1e-9,
            "trade.funding_paid={} expected={}",
            trade.funding_paid,
            funding_paid
        );
        assert!(
            trade.net_pnl < trade.gross_pnl - trade.fees_paid,
            "funding must reduce net_pnl: gross={} fees={} net={} funding={}",
            trade.gross_pnl,
            trade.fees_paid,
            trade.net_pnl,
            trade.funding_paid
        );
        assert!(
            (trade.net_pnl - (trade.gross_pnl - trade.fees_paid - trade.funding_paid)).abs() < 1e-9,
            "net_pnl must equal gross_pnl - fees_paid - funding_paid"
        );

        // Balance after close must equal pre_close + notional + net_pnl
        let expected_balance = pre_close_balance + trade.notional + trade.net_pnl;
        assert!(
            (acc.balance - expected_balance).abs() < 1e-6,
            "balance={} expected={}",
            acc.balance,
            expected_balance
        );
    }

    #[test]
    fn equity_curve_populated_each_bar() {
        let mut acc = default_account();
        acc.on_bar_close("BTCUSDT", 1_000.0, 1_010.0, 990.0, 1_000_000, None, None);
        acc.on_bar_close("BTCUSDT", 1_010.0, 1_020.0, 1_000.0, 1_100_000, None, None);
        assert_eq!(acc.equity_curve.len(), 2);
        assert_eq!(acc.equity_curve[0].0, 1_000_000);
        assert_eq!(acc.equity_curve[1].0, 1_100_000);
    }

    #[test]
    fn slippage_always_adverse() {
        // Entry long: fill must be ABOVE intended
        let fill_long_entry = apply_slippage_entry(100.0, Side::Long, 1.0);
        assert!(fill_long_entry > 100.0);

        // Entry short: fill must be BELOW intended
        let fill_short_entry = apply_slippage_entry(100.0, Side::Short, 1.0);
        assert!(fill_short_entry < 100.0);

        // Exit long: fill must be BELOW intended
        let fill_long_exit = apply_slippage_exit(100.0, Side::Long, 1.0);
        assert!(fill_long_exit < 100.0);

        // Exit short: fill must be ABOVE intended
        let fill_short_exit = apply_slippage_exit(100.0, Side::Short, 1.0);
        assert!(fill_short_exit > 100.0);
    }
}
