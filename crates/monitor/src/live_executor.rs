use crate::bybit_order::{BybitOrderClient, OrderError};
use crate::slack_alert;
use data::strategy::detectors::mtf_spot_detector::{MtfSpotDirection, MtfSpotSignal};
use std::time::{SystemTime, UNIX_EPOCH};

// ── Config ───────────────────────────────────────────────────────────────────

fn read_env(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}

fn read_env_f64(key: &str, default: f64) -> f64 {
    std::env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

pub struct LiveConfig {
    pub api_key: String,
    pub api_secret: String,
    pub testnet: bool,
    /// Fraction of capital risked per trade (e.g. 0.02 = 2%).
    pub risk_pct: f64,
    /// Initial/current capital in USDT (for sizing). Updated after each close.
    pub capital: f64,
    /// Max daily loss in R before kill switch activates (default 3R).
    pub max_daily_loss_r: f64,
    /// Max total drawdown fraction before kill switch (default 0.15 = 15%).
    pub max_drawdown_pct: f64,
}

impl LiveConfig {
    pub fn from_env() -> Option<Self> {
        let api_key = std::env::var("BYBIT_API_KEY").ok()?;
        let api_secret = std::env::var("BYBIT_SECRET_KEY").ok()?;
        if api_key.is_empty() || api_secret.is_empty() {
            return None;
        }
        Some(Self {
            api_key,
            api_secret,
            testnet: read_env("BYBIT_TESTNET", "true") != "false",
            risk_pct: read_env_f64("LIVE_RISK_PCT", 0.02),
            capital: read_env_f64("LIVE_INITIAL_CAPITAL", 500.0),
            max_daily_loss_r: read_env_f64("LIVE_MAX_DAILY_LOSS_R", 3.0),
            max_drawdown_pct: read_env_f64("LIVE_MAX_DRAWDOWN_PCT", 0.15),
        })
    }
}

// ── Live position state ───────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct LivePosition {
    pub signal: MtfSpotSignal,
    pub entry_order_id: String,
    /// Actual fill price from Bybit (may differ slightly from signal.entry).
    pub fill_price: Option<f64>,
    /// Qty filled in base currency (BTC).
    pub filled_qty: f64,
    /// TP limit order ID.
    pub tp_order_id: Option<String>,
    /// SL stop-market order ID.
    pub sl_order_id: Option<String>,
    pub opened_at_ms: i64,
    pub bars_in_trade: usize,
}

impl LivePosition {
    pub fn side_str(&self) -> &'static str {
        match self.signal.direction {
            MtfSpotDirection::Short => "Sell",
            MtfSpotDirection::Long => "Buy",
        }
    }

    /// Opposite side (for closing orders).
    pub fn close_side_str(&self) -> &'static str {
        match self.signal.direction {
            MtfSpotDirection::Short => "Buy",
            MtfSpotDirection::Long => "Sell",
        }
    }

    pub fn risk_usd(&self) -> f64 {
        let entry = self.fill_price.unwrap_or(self.signal.entry);
        let risk = match self.signal.direction {
            MtfSpotDirection::Short => self.signal.stop - entry,
            MtfSpotDirection::Long => entry - self.signal.stop,
        };
        risk * self.filled_qty
    }

    pub fn result_r(&self, exit_price: f64) -> f64 {
        let entry = self.fill_price.unwrap_or(self.signal.entry);
        let risk = match self.signal.direction {
            MtfSpotDirection::Short => self.signal.stop - entry,
            MtfSpotDirection::Long => entry - self.signal.stop,
        };
        if risk <= 0.0 {
            return 0.0;
        }
        let pnl = match self.signal.direction {
            MtfSpotDirection::Short => entry - exit_price,
            MtfSpotDirection::Long => exit_price - entry,
        };
        let fee_cost = 0.0007 * entry / risk; // 0.07% round-trip
        pnl / risk - fee_cost
    }
}

// ── Closed trade (for logging/Supabase) ──────────────────────────────────────

#[derive(Debug, Clone)]
pub struct LiveClosedTrade {
    pub position: LivePosition,
    pub exit_price: f64,
    pub exit_order_id: Option<String>,
    pub reason: String,
    pub result_r: f64,
    pub exit_ts_ms: i64,
}

// ── Kill switch state ─────────────────────────────────────────────────────────

struct KillSwitch {
    daily_loss_r: f64,
    peak_capital: f64,
    day_epoch: u64,
    halted: bool,
}

impl KillSwitch {
    fn new(capital: f64) -> Self {
        Self {
            daily_loss_r: 0.0,
            peak_capital: capital,
            day_epoch: Self::today_epoch(),
            halted: false,
        }
    }

    fn today_epoch() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs()
            / 86400
    }

    fn reset_if_new_day(&mut self) {
        let today = Self::today_epoch();
        if today != self.day_epoch {
            self.day_epoch = today;
            self.daily_loss_r = 0.0;
        }
    }

    fn record_trade(&mut self, result_r: f64, capital: f64, max_daily_r: f64, max_dd_pct: f64) {
        self.reset_if_new_day();
        if result_r < 0.0 {
            self.daily_loss_r += result_r.abs();
        }
        if capital > self.peak_capital {
            self.peak_capital = capital;
        }
        let drawdown = (self.peak_capital - capital) / self.peak_capital;
        if self.daily_loss_r >= max_daily_r {
            let reason = format!("daily loss {:.2}R >= {:.2}R limit", self.daily_loss_r, max_daily_r);
            eprintln!("[live] KILL SWITCH — {reason}");
            tokio::spawn(async move { slack_alert::kill_switch_triggered(&reason).await });
            self.halted = true;
        }
        if drawdown >= max_dd_pct {
            let reason = format!("drawdown {:.1}% >= {:.1}% limit", drawdown * 100.0, max_dd_pct * 100.0);
            eprintln!("[live] KILL SWITCH — {reason}");
            tokio::spawn(async move { slack_alert::kill_switch_triggered(&reason).await });
            self.halted = true;
        }
    }

    fn is_halted(&self) -> bool {
        self.halted
    }
}

// ── LiveAccount ───────────────────────────────────────────────────────────────

pub struct LiveAccount {
    client: BybitOrderClient,
    pub config: LiveConfig,
    pub active: Option<LivePosition>,
    pub closed_trades: Vec<LiveClosedTrade>,
    kill: KillSwitch,
}

impl LiveAccount {
    pub fn new(config: LiveConfig) -> Self {
        let client = BybitOrderClient::new(
            config.api_key.clone(),
            config.api_secret.clone(),
            config.testnet,
        );
        let kill = KillSwitch::new(config.capital);
        Self {
            client,
            config,
            active: None,
            closed_trades: Vec::new(),
            kill,
        }
    }

    pub fn is_halted(&self) -> bool {
        self.kill.is_halted()
    }

    pub fn has_active(&self) -> bool {
        self.active.is_some()
    }

    /// Called when the MTF detector fires a new signal.
    /// Places market entry + TP limit + SL stop-market.
    pub async fn open_position(&mut self, signal: &MtfSpotSignal) -> Result<(), String> {
        if self.kill.is_halted() {
            return Err("kill switch active".into());
        }
        if self.active.is_some() {
            return Err("position already active".into());
        }

        let entry = signal.entry;
        let stop_pct = signal.stop_pct.abs();
        if stop_pct <= 0.0 {
            return Err("invalid stop_pct".into());
        }

        // Position sizing: risk_amount / stop_distance → size in USD → size in BTC
        let risk_amount = self.config.capital * self.config.risk_pct;
        let position_usd = risk_amount / stop_pct;
        let qty_btc = position_usd / entry;
        let qty_btc = (qty_btc * 100_000.0).floor() / 100_000.0; // floor to 5 decimal places
        if qty_btc <= 0.0 {
            return Err(format!("computed qty {qty_btc:.5} too small"));
        }

        let open_side = match signal.direction {
            MtfSpotDirection::Short => "Sell",
            MtfSpotDirection::Long => "Buy",
        };
        let close_side = match signal.direction {
            MtfSpotDirection::Short => "Buy",
            MtfSpotDirection::Long => "Sell",
        };

        // Entry: market order
        let entry_order_id = self
            .with_retry(3, || {
                let client = self.client.clone();
                let sym = signal.symbol.clone();
                let side = open_side.to_string();
                async move { client.place_market_order(&sym, &side, qty_btc).await }
            })
            .await
            .map_err(|e| format!("place entry: {e}"))?;

        println!(
            "[live] ENTRY {} {} qty={:.5} entry≈{:.2} stop={:.2} target={:.2} order={}",
            signal.symbol,
            signal.direction.as_str(),
            qty_btc,
            entry,
            signal.stop,
            signal.target,
            entry_order_id
        );

        // Brief wait for fill (market IOC fills near-instantly)
        tokio::time::sleep(tokio::time::Duration::from_millis(500)).await;

        // Query fill price
        let fill = self
            .client
            .get_fill(&signal.symbol, &entry_order_id)
            .await
            .ok();
        let fill_price = fill.as_ref().and_then(|f| f.avg_price);
        let filled_qty = fill
            .as_ref()
            .map(|f| f.filled_qty)
            .unwrap_or(qty_btc);

        let actual_entry = fill_price.unwrap_or(entry);
        let actual_qty = if filled_qty > 0.0 { filled_qty } else { qty_btc };

        // TP: limit order at target
        let tp_order_id = self
            .with_retry(2, || {
                let client = self.client.clone();
                let sym = signal.symbol.clone();
                let side = close_side.to_string();
                let target = signal.target;
                async move { client.place_limit_order(&sym, &side, actual_qty, target).await }
            })
            .await
            .ok();

        // SL: stop-market order at stop price
        let sl_order_id = self
            .with_retry(2, || {
                let client = self.client.clone();
                let sym = signal.symbol.clone();
                let side = close_side.to_string();
                let stop = signal.stop;
                async move {
                    client
                        .place_stop_market_order(&sym, &side, actual_qty, stop)
                        .await
                }
            })
            .await
            .ok();

        println!(
            "[live] ORDERS SET tp={:?} sl={:?} fill_price={:?}",
            tp_order_id, sl_order_id, fill_price
        );

        slack_alert::trade_opened(
            &signal.symbol,
            signal.direction.as_str(),
            fill_price.unwrap_or(entry),
            signal.stop,
            signal.target,
            &entry_order_id,
        )
        .await;

        self.active = Some(LivePosition {
            signal: signal.clone(),
            entry_order_id,
            fill_price,
            filled_qty: actual_qty,
            tp_order_id,
            sl_order_id,
            opened_at_ms: now_ms(),
            bars_in_trade: 0,
        });

        Ok(())
    }

    /// Called every bar tick while a position is open.
    pub fn tick_bar(&mut self) {
        if let Some(pos) = &mut self.active {
            pos.bars_in_trade += 1;
        }
    }

    /// Force close: cancel TP/SL orders then place market exit.
    /// Used for CVD exit or TTL expiry triggered by the detector.
    pub async fn close_position(&mut self, reason: &str, current_price: f64) {
        let pos = match self.active.take() {
            Some(p) => p,
            None => return,
        };

        // Cancel pending TP/SL
        if let Some(ref id) = pos.tp_order_id {
            let _ = self.client.cancel_order(&pos.signal.symbol, id).await;
        }
        if let Some(ref id) = pos.sl_order_id {
            let _ = self.client.cancel_order(&pos.signal.symbol, id).await;
        }

        // Market close
        let exit_order_id = self
            .with_retry(3, || {
                let client = self.client.clone();
                let sym = pos.signal.symbol.clone();
                let side = pos.close_side_str().to_string();
                let qty = pos.filled_qty;
                async move { client.place_market_order(&sym, &side, qty).await }
            })
            .await
            .ok();

        let result_r = pos.result_r(current_price);

        println!(
            "[live] CLOSED {} {} reason={} exit≈{:.2} R={:+.3}",
            pos.signal.symbol,
            pos.signal.direction.as_str(),
            reason,
            current_price,
            result_r
        );

        slack_alert::trade_closed(
            &pos.signal.symbol,
            pos.signal.direction.as_str(),
            result_r,
            reason,
        )
        .await;

        self.config.capital += result_r * pos.risk_usd();
        self.kill.record_trade(
            result_r,
            self.config.capital,
            self.config.max_daily_loss_r,
            self.config.max_drawdown_pct,
        );

        self.closed_trades.push(LiveClosedTrade {
            exit_price: current_price,
            exit_order_id,
            reason: reason.to_string(),
            result_r,
            exit_ts_ms: now_ms(),
            position: pos,
        });
    }

    /// Periodic reconciliation: check Bybit to see if TP or SL already triggered.
    /// Call this every ~30s from the monitor loop.
    pub async fn reconcile(&mut self) {
        let pos = match &self.active {
            Some(p) => p.clone(),
            None => return,
        };

        // Check TP order
        if let Some(ref tp_id) = pos.tp_order_id.clone() {
            if let Ok(fill) = self.client.get_fill(&pos.signal.symbol, tp_id).await {
                if fill.status == "Filled" {
                    let exit_price = fill.avg_price.unwrap_or(pos.signal.target);
                    self.record_exit_from_bybit(exit_price, "TARGET_HIT", Some(tp_id.clone()))
                        .await;
                    return;
                }
            }
        }

        // Check SL order
        if let Some(ref sl_id) = pos.sl_order_id.clone() {
            if let Ok(fill) = self.client.get_fill(&pos.signal.symbol, sl_id).await {
                if fill.status == "Filled" {
                    let exit_price = fill.avg_price.unwrap_or(pos.signal.stop);
                    self.record_exit_from_bybit(exit_price, "STOP_HIT", Some(sl_id.clone()))
                        .await;
                }
            }
        }
    }

    async fn record_exit_from_bybit(
        &mut self,
        exit_price: f64,
        reason: &str,
        exit_order_id: Option<String>,
    ) {
        let pos = match self.active.take() {
            Some(p) => p,
            None => return,
        };

        // Cancel the other leg
        let other_id = if reason == "TARGET_HIT" {
            pos.sl_order_id.clone()
        } else {
            pos.tp_order_id.clone()
        };
        if let Some(ref id) = other_id {
            let _ = self.client.cancel_order(&pos.signal.symbol, id).await;
        }

        let result_r = pos.result_r(exit_price);
        println!(
            "[live] RECONCILE CLOSED {} {} reason={} exit={:.2} R={:+.3}",
            pos.signal.symbol,
            pos.signal.direction.as_str(),
            reason,
            exit_price,
            result_r
        );

        self.config.capital += result_r * pos.risk_usd();
        self.kill.record_trade(
            result_r,
            self.config.capital,
            self.config.max_daily_loss_r,
            self.config.max_drawdown_pct,
        );

        self.closed_trades.push(LiveClosedTrade {
            exit_price,
            exit_order_id,
            reason: reason.to_string(),
            result_r,
            exit_ts_ms: now_ms(),
            position: pos,
        });
    }

    // ── Retry helper ─────────────────────────────────────────────────────────

    async fn with_retry<F, Fut, T>(&self, attempts: usize, mut f: F) -> Result<T, OrderError>
    where
        F: FnMut() -> Fut,
        Fut: std::future::Future<Output = Result<T, OrderError>>,
    {
        let mut last_err = None;
        for attempt in 0..attempts {
            match f().await {
                Ok(v) => return Ok(v),
                Err(e) => {
                    eprintln!("[live] order attempt {}/{attempts} failed: {e}", attempt + 1);
                    last_err = Some(e);
                    if attempt + 1 < attempts {
                        tokio::time::sleep(tokio::time::Duration::from_secs(1)).await;
                    }
                }
            }
        }
        Err(last_err.unwrap())
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as i64
}
