//! PaperBook — gestión de órdenes virtuales maker (fade + trail).
//! Port directo de paper_liquidity.py::PaperBook.

use crate::levels::{Gestion, Level, Side, VolRegime, TRAIL_ATR};

const FEE_MAKER: f64 = 0.0002;   // 2bps/lado
const FEE_TAKER: f64 = 0.00055;  // 5.5bps/lado

// ── Orden en reposo ─────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct RestingOrder {
    pub level: Level,
    pub placed_ts: i64,
}

// ── Posición abierta ────────────────────────────────────────────────────────

const SCALE2_ATR_FACTOR: f64 = 0.3;   // segundo nivel = price ± 0.3×ATR

#[derive(Debug, Clone)]
pub struct OpenPos {
    pub level:              Level,
    pub entry:              f64,
    pub fill_ts:            i64,
    pub bar_delta_at_fill:  f64,
    // gestión fade
    pub cur_stop:  f64,
    pub realized:  f64,
    pub rem:       f64,
    pub filled1:   bool,
    // gestión trail
    pub best_price: f64,
    pub trail_stop: f64,
    // entrada escalonada (50 % en level, 50 % en level ± 0.3 ATR)
    pub scale2_price:    f64,   // precio de la segunda orden (0.0 = no aplica)
    pub scale2_filled:   bool,
    pub effective_entry: f64,   // entry blended tras llenar scale2
}

impl OpenPos {
    pub fn new(level: Level, fill_ts: i64, bar_delta: f64) -> Self {
        let entry = level.price;
        let atr   = level.atr;
        // scale2: 0.3×ATR más profundo en la dirección del trade
        let scale2_price = if atr > 0.0 {
            match level.side {
                crate::levels::Side::Long  => entry - SCALE2_ATR_FACTOR * atr,
                crate::levels::Side::Short => entry + SCALE2_ATR_FACTOR * atr,
            }
        } else {
            0.0
        };
        let trail_stop = level.stop;
        Self {
            cur_stop:           level.stop,
            best_price:         entry,
            trail_stop,
            realized:           0.0,
            rem:                1.0,
            filled1:            false,
            fill_ts,
            bar_delta_at_fill:  bar_delta,
            entry,
            effective_entry:    entry,
            scale2_price,
            scale2_filled:      false,
            level,
        }
    }
}

// ── Trade cerrado ───────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct ClosedTrade {
    pub system:              String,
    pub kind:                String,
    pub side:                String,
    pub vol_regime:          String,
    pub regime:              String,
    pub gestion:             String,
    pub entry:               f64,
    pub stop:                f64,
    pub target:              f64,
    pub exit_price:          f64,
    pub result_r:            f64,
    pub win:                 bool,
    pub reason:              String,
    pub opened_at:           i64,
    pub closed_at:           i64,
    pub bar_delta_at_fill:   f64,
    pub scale2_filled:       bool,   // si la segunda orden también se ejecutó
    pub effective_entry:     f64,    // entry real blended (= entry si solo 1 orden)
}

// ── Evento de place/fill ────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct BookEvent {
    pub event_type:  String,   // "place" | "fill"
    pub kind:        String,
    pub side:        String,
    pub vol_regime:  String,
    pub regime:      String,
    pub gestion:     String,
    pub price:       f64,
    pub at:          i64,
    pub bar_delta:   f64,      // delta acumulado de la barra M15 al momento del fill (0.0 para place)
}

// ── PaperBook ───────────────────────────────────────────────────────────────

pub struct PaperBook {
    pub system:        String,
    pub fill_margin:   f64,   // bps — mercado debe penetrar N bps el nivel para fill
    pub timeout_ms:    i64,   // ms — cerrar posición si supera este tiempo (0=desactivado)
    resting:           Vec<RestingOrder>,
    open_pos:          Vec<OpenPos>,
    pub placed:        [u64; 2],  // [high, low]
    pub filled:        [u64; 2],
    pub trades:        Vec<ClosedTrade>,
    pub events:        Vec<BookEvent>,
}

impl PaperBook {
    pub fn new(system: impl Into<String>, fill_margin_bps: f64, timeout_hours: f64) -> Self {
        Self {
            system:      system.into(),
            fill_margin: fill_margin_bps,
            timeout_ms:  (timeout_hours * 3600.0 * 1000.0) as i64,
            resting:     Vec::new(),
            open_pos:    Vec::new(),
            placed:      [0, 0],
            filled:      [0, 0],
            trades:      Vec::new(),
            events:      Vec::new(),
        }
    }

    fn reg_idx(r: VolRegime) -> usize {
        if r == VolRegime::High { 0 } else { 1 }
    }

    fn side_str(s: Side) -> &'static str {
        if s == Side::Long { "long" } else { "short" }
    }

    fn vol_str(r: VolRegime) -> &'static str {
        if r == VolRegime::High { "high" } else { "low" }
    }

    fn gestion_str(g: Gestion) -> &'static str {
        if g == Gestion::Fade { "fade" } else { "trail" }
    }

    fn make_event(&self, etype: &str, lv: &Level, at: i64, bar_delta: f64) -> BookEvent {
        BookEvent {
            event_type:  etype.into(),
            kind:        lv.kind.into(),
            side:        Self::side_str(lv.side).into(),
            vol_regime:  Self::vol_str(lv.vol_regime).into(),
            regime:      if lv.regime == crate::levels::MarketRegime::Trend { "trend" } else { "chop" }.into(),
            gestion:     Self::gestion_str(lv.gestion).into(),
            price:       lv.price,
            at,
            bar_delta,
        }
    }

    /// Reemplaza las órdenes en reposo con los niveles recién calculados.
    pub fn refresh(&mut self, levels: Vec<Level>, ts: i64) {
        self.resting.clear();
        for lv in levels {
            // No colocar si ya hay una posición abierta en el mismo lado y precio
            // (evita acumular múltiples entradas en el mismo nivel barra a barra)
            let already_open = self.open_pos.iter().any(|p| {
                p.level.side == lv.side && (p.entry - lv.price).abs() < 2.0
            });
            if already_open { continue; }

            let idx = Self::reg_idx(lv.vol_regime);
            self.placed[idx] += 1;
            let evt = self.make_event("place", &lv, ts, 0.0);
            self.events.push(evt);
            self.resting.push(RestingOrder { level: lv, placed_ts: ts });
        }
    }

    /// Procesar un tick: simular fills y actualizar posiciones abiertas.
    /// `bar_delta`: delta acumulado de la barra M15 en curso (buy_vol - sell_vol)
    pub fn on_trade(&mut self, px: f64, ts: i64, bar_delta: f64) {
        // ── fills ──
        // mem::take libera el borrow de self.resting para que make_event pueda tomar &self
        let orders = std::mem::take(&mut self.resting);
        let mut filled: Vec<RestingOrder> = Vec::new();
        let mut still:  Vec<RestingOrder> = Vec::new();
        let margin_frac = self.fill_margin / 10_000.0;
        for o in orders {
            let hit = match o.level.side {
                Side::Long  => px <= o.level.price * (1.0 - margin_frac),
                Side::Short => px >= o.level.price * (1.0 + margin_frac),
            };
            if hit {
                self.filled[Self::reg_idx(o.level.vol_regime)] += 1;
                let evt = self.make_event("fill", &o.level, ts, bar_delta);
                self.events.push(evt);
                filled.push(o);
            } else {
                still.push(o);
            }
        }
        self.resting = still;
        for o in filled {
            self.open_pos.push(OpenPos::new(o.level, ts, bar_delta));
        }

        // ── gestión de posiciones abiertas ──
        let mut rem_pos: Vec<OpenPos> = Vec::new();
        let closed = std::mem::take(&mut self.trades);
        self.trades = closed;

        for mut p in self.open_pos.drain(..) {
            // ── scale2: llenar la segunda orden si precio alcanza scale2_price ──
            if p.scale2_price > 0.0 && !p.scale2_filled {
                let hit2 = match p.level.side {
                    Side::Long  => px <= p.scale2_price,
                    Side::Short => px >= p.scale2_price,
                };
                if hit2 {
                    p.scale2_filled   = true;
                    // entry blended = promedio de los dos fills (50/50)
                    p.effective_entry = (p.entry + p.scale2_price) / 2.0;
                }
            }

            // Usar effective_entry y riesgo desde effective_entry al stop
            let risk = (p.effective_entry - p.level.stop).abs();
            if risk <= 0.0 { continue; }

            // ── Timeout ──────────────────────────────────────────────────────
            if self.timeout_ms > 0 && ts - p.fill_ts > self.timeout_ms {
                let r_gross = match p.level.side {
                    Side::Long  => (px - p.effective_entry) / risk,
                    Side::Short => (p.effective_entry - px) / risk,
                };
                let fee_r = (FEE_MAKER + FEE_TAKER) * p.effective_entry / risk;
                let r_final = r_gross - fee_r;
                self.trades.push(ClosedTrade {
                    system:            self.system.clone(),
                    kind:              p.level.kind.into(),
                    side:              Self::side_str(p.level.side).into(),
                    vol_regime:        Self::vol_str(p.level.vol_regime).into(),
                    regime:            if p.level.regime == crate::levels::MarketRegime::Trend { "trend" } else { "chop" }.into(),
                    gestion:           Self::gestion_str(p.level.gestion).into(),
                    entry:             p.entry,
                    stop:              p.level.stop,
                    target:            p.level.tp,
                    exit_price:        (px * 100.0).round() / 100.0,
                    result_r:          (r_final * 10000.0).round() / 10000.0,
                    win:               r_final > 0.0,
                    reason:            "timeout".into(),
                    opened_at:         p.fill_ts,
                    closed_at:         ts,
                    bar_delta_at_fill: p.bar_delta_at_fill,
                    scale2_filled:     p.scale2_filled,
                    effective_entry:   p.effective_entry,
                });
                continue;
            }

            let mut done   = false;
            let mut reason = String::new();
            let mut exit_px = 0.0_f64;
            let mut r_final = 0.0_f64;

            match p.level.gestion {
                Gestion::Trail => {
                    let atr0 = if p.level.atr > 0.0 { p.level.atr } else { risk / TRAIL_ATR };
                    match p.level.side {
                        Side::Long => {
                            p.best_price = p.best_price.max(px);
                            p.trail_stop = p.trail_stop.max(p.best_price - TRAIL_ATR * atr0);
                            if px <= p.trail_stop {
                                exit_px = p.trail_stop; reason = "trail".into(); done = true;
                            }
                        }
                        Side::Short => {
                            p.best_price = p.best_price.min(px);
                            p.trail_stop = p.trail_stop.min(p.best_price + TRAIL_ATR * atr0);
                            if px >= p.trail_stop {
                                exit_px = p.trail_stop; reason = "trail".into(); done = true;
                            }
                        }
                    }
                    if done {
                        let r_gross = match p.level.side {
                            Side::Long  => (exit_px - p.effective_entry) / risk,
                            Side::Short => (p.effective_entry - exit_px) / risk,
                        };
                        let fee_r = (FEE_MAKER + FEE_TAKER) * p.effective_entry / risk;
                        r_final = r_gross - fee_r;
                    }
                }

                Gestion::Fade => {
                    let ee = p.effective_entry;
                    match p.level.side {
                        Side::Long => {
                            if px <= p.cur_stop {
                                p.realized += p.rem * ((p.cur_stop - ee) / risk);
                                reason  = if p.filled1 { "breakeven" } else { "stop" }.into();
                                exit_px = p.cur_stop; done = true;
                            } else if !p.filled1 && p.level.take_partial {
                                if let Some(tp1) = p.level.tp1 {
                                    if px >= tp1 {
                                        p.realized += 0.5 * ((tp1 - ee) / risk);
                                        p.rem -= 0.5; p.filled1 = true; p.cur_stop = ee;
                                    }
                                }
                            }
                            if !done && px >= p.level.tp {
                                p.realized += p.rem * ((p.level.tp - ee) / risk);
                                reason = "target".into(); exit_px = p.level.tp; done = true;
                            }
                        }
                        Side::Short => {
                            if px >= p.cur_stop {
                                p.realized += p.rem * ((ee - p.cur_stop) / risk);
                                reason  = if p.filled1 { "breakeven" } else { "stop" }.into();
                                exit_px = p.cur_stop; done = true;
                            } else if !p.filled1 && p.level.take_partial {
                                if let Some(tp1) = p.level.tp1 {
                                    if px <= tp1 {
                                        p.realized += 0.5 * ((ee - tp1) / risk);
                                        p.rem -= 0.5; p.filled1 = true; p.cur_stop = ee;
                                    }
                                }
                            }
                            if !done && px <= p.level.tp {
                                p.realized += p.rem * ((ee - p.level.tp) / risk);
                                reason = "target".into(); exit_px = p.level.tp; done = true;
                            }
                        }
                    }
                    if done {
                        let exit_fee = if reason == "target" { FEE_MAKER } else { FEE_TAKER };
                        let partial_fee = if p.filled1 { FEE_MAKER * 0.5 } else { 0.0 };
                        let fee_r = (FEE_MAKER + partial_fee + exit_fee * p.rem) * p.effective_entry / risk;
                        r_final = p.realized - fee_r;
                    }
                }
            }

            if done {
                self.trades.push(ClosedTrade {
                    system:            self.system.clone(),
                    kind:              p.level.kind.into(),
                    side:              Self::side_str(p.level.side).into(),
                    vol_regime:        Self::vol_str(p.level.vol_regime).into(),
                    regime:            if p.level.regime == crate::levels::MarketRegime::Trend { "trend" } else { "chop" }.into(),
                    gestion:           Self::gestion_str(p.level.gestion).into(),
                    entry:             p.entry,
                    stop:              p.level.stop,
                    target:            p.level.tp,
                    exit_price:        (exit_px * 100.0).round() / 100.0,
                    result_r:          (r_final * 10000.0).round() / 10000.0,
                    win:               r_final > 0.0,
                    reason,
                    opened_at:         p.fill_ts,
                    closed_at:         ts,
                    bar_delta_at_fill: p.bar_delta_at_fill,
                    scale2_filled:     p.scale2_filled,
                    effective_entry:   p.effective_entry,
                });
            } else {
                rem_pos.push(p);
            }
        }
        self.open_pos = rem_pos;
    }

    pub fn drain_trades(&mut self) -> Vec<ClosedTrade> {
        std::mem::take(&mut self.trades)
    }

    pub fn drain_events(&mut self) -> Vec<BookEvent> {
        std::mem::take(&mut self.events)
    }

    pub fn fill_ratio(&self, regime: VolRegime) -> f64 {
        let idx = Self::reg_idx(regime);
        if self.placed[idx] == 0 { 0.0 } else { self.filled[idx] as f64 / self.placed[idx] as f64 }
    }

    pub fn status_line(&self) -> String {
        format!(
            "resting={} open={} HIGH fill={}/{} ({:.0}%) LOW fill={}/{} ({:.0}%)",
            self.resting.len(), self.open_pos.len(),
            self.filled[0], self.placed[0], self.fill_ratio(VolRegime::High) * 100.0,
            self.filled[1], self.placed[1], self.fill_ratio(VolRegime::Low)  * 100.0,
        )
    }
}
