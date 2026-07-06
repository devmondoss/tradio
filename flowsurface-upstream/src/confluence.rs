//! Confluence detector — read-only orderflow signal.
//!
//! Watches the live trade + depth streams and raises a *discretionary* alert
//! when three microstructure conditions converge at the same price (the trader
//! decides and executes manually — this never places orders):
//!
//!   1. WHERE  — price sits at a volume-profile level (POC / value-area edge).
//!   2. WALL   — a resting order-book wall defends that level and is *not* a
//!               spoof (it has persisted for at least `WALL_PERSIST_SECS`).
//!   3. WHEN   — footprint absorption: aggressive flow slams the wall
//!               (one-sided delta) yet price holds — the wall soaks it up.
//!
//! State is self-contained (built from raw `Trade`/`Depth`), so it does not
//! depend on the chart/pane aggregation. Timing uses wall-clock `Instant`,
//! which tracks event time closely on a live stream.

use std::collections::VecDeque;
use std::sync::{LazyLock, Mutex};
use std::time::Instant;

use exchange::Trade;
use exchange::adapter::StreamKind;
use exchange::depth::Depth;
use rustc_hash::FxHashMap;

/// A fired confluence, kept in a shared log so the chart layer can draw a marker
/// at the price/time where it triggered (the toast is transient; this persists).
#[derive(Clone)]
pub struct ConfluenceMark {
    pub symbol: String,
    pub price: f64,
    pub bearish: bool,
    pub at_ms: u64,
}

static MARKS: Mutex<Vec<ConfluenceMark>> = Mutex::new(Vec::new());

fn push_mark(mark: ConfluenceMark) {
    if let Ok(mut marks) = MARKS.lock() {
        marks.push(mark);
        // keep the log bounded (newest kept)
        let len = marks.len();
        if len > 500 {
            marks.drain(0..len - 500);
        }
    }
}

/// Marks for `symbol` triggered at or after `since_ms`. Read by the chart layer.
pub fn recent_marks(symbol: &str, since_ms: u64) -> Vec<ConfluenceMark> {
    MARKS
        .lock()
        .map(|marks| {
            marks
                .iter()
                .filter(|m| m.symbol == symbol && m.at_ms >= since_ms)
                .cloned()
                .collect()
        })
        .unwrap_or_default()
}

/// Current nearest resting walls for a symbol, with anti-spoof persistence flags.
/// Read by the chart layer to draw wall lines (solid = real, faint = maybe spoof).
#[derive(Clone, Default)]
pub struct WallSnapshot {
    pub bid_price: f64,
    pub bid_qty: f64,
    pub bid_persisted: bool,
    pub ask_price: f64,
    pub ask_qty: f64,
    pub ask_persisted: bool,
}

static WALLS: LazyLock<Mutex<FxHashMap<String, WallSnapshot>>> =
    LazyLock::new(|| Mutex::new(FxHashMap::default()));

fn set_walls(symbol: &str, snap: WallSnapshot) {
    if let Ok(mut w) = WALLS.lock() {
        w.insert(symbol.to_string(), snap);
    }
}

pub fn current_walls(symbol: &str) -> Option<WallSnapshot> {
    WALLS.lock().ok().and_then(|w| w.get(symbol).cloned())
}

// ── tuning (safe defaults; refine while watching it live) ───────────────────
const VP_WINDOW_SECS: f64 = 1800.0; // rolling volume-profile window (30 min)
const RECENT_DELTA_SECS: f64 = 12.0; // absorption look-back
const WALL_MULT: f64 = 5.0; // wall = ≥ 5× median nearby book size (selective)
const WALL_PERSIST_SECS: f64 = 5.0; // anti-spoof: wall must rest this long
const WALL_NEAR_FRAC: f64 = 0.0020; // only walls within 0.20% of price
const MEDIAN_NEAR_FRAC: f64 = 0.0050; // median book size over ±0.50% of mid
const ABSORB_RATIO: f64 = 2.5; // aggressive side ≥ 2.5× the passive side
const ABSORB_VS_WALL: f64 = 1.0; // aggressive flow ≥ the full wall it hit
const LEVEL_TOL_FRAC: f64 = 0.0008; // "near a VP level" tolerance (~0.08%)
const COOLDOWN_SECS: f64 = 45.0; // per-symbol alert cooldown
const WARMUP_TRADES: usize = 300; // min trades before the VP is trusted
const VALUE_AREA_PCT: f64 = 0.70; // value area = 70% of volume around POC
const BIN_FRAC: f64 = 0.0002; // volume-profile price bin (2 bps of price)

/// A fired confluence alert (title + human-readable body).
pub struct Signal {
    pub title: String,
    pub body: String,
}

struct TradeRec {
    at: Instant,
    qty: f64,
    is_sell: bool,
    bin: i64,
}

#[derive(Default)]
struct WallInfo {
    bid_price: f64,
    bid_qty: f64,
    bid_since: Option<Instant>,
    ask_price: f64,
    ask_qty: f64,
    ask_since: Option<Instant>,
}

struct SymbolState {
    trades: VecDeque<TradeRec>,
    vol_by_bin: FxHashMap<i64, f64>,
    bin_size: f64,
    total_trades: usize,
    last_price: f64,
    last_time_ms: u64,
    wall_seen: FxHashMap<i64, Instant>, // price-cents → first time seen as a wall
    wall: WallInfo,
    last_alert: Option<Instant>,
}

impl SymbolState {
    fn new() -> Self {
        Self {
            trades: VecDeque::new(),
            vol_by_bin: FxHashMap::default(),
            bin_size: 0.0,
            total_trades: 0,
            last_price: 0.0,
            last_time_ms: 0,
            wall_seen: FxHashMap::default(),
            wall: WallInfo::default(),
            last_alert: None,
        }
    }

    #[inline]
    fn bin_of(&self, price: f64) -> i64 {
        (price / self.bin_size).round() as i64
    }

    /// Refresh the nearest non-spoof walls from the latest depth snapshot.
    fn update_walls(&mut self, depth: &Depth, now: Instant) {
        let best_bid = depth.bids.keys().next_back().map(|p| p.to_f64());
        let best_ask = depth.asks.keys().next().map(|p| p.to_f64());
        let (Some(best_bid), Some(best_ask)) = (best_bid, best_ask) else {
            return;
        };
        let mid = (best_bid + best_ask) * 0.5;

        // median resting size over the book near the mid
        let mut near: Vec<f64> = Vec::new();
        for (p, q) in depth.bids.iter().chain(depth.asks.iter()) {
            let price = p.to_f64();
            if (price - mid).abs() <= mid * MEDIAN_NEAR_FRAC {
                near.push(q.to_f64());
            }
        }
        if near.len() < 5 {
            return;
        }
        near.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let median = near[near.len() / 2].max(f64::MIN_POSITIVE);
        let thresh = WALL_MULT * median;

        // collect the walls present right now and track when each first appeared
        let mut present: FxHashMap<i64, Instant> = FxHashMap::default();
        let mut nearest_bid: Option<(f64, f64)> = None; // (price, qty) closest below
        let mut nearest_ask: Option<(f64, f64)> = None; // (price, qty) closest above

        for (p, q) in depth.bids.iter() {
            let (price, qty) = (p.to_f64(), q.to_f64());
            if qty < thresh || (mid - price) > mid * WALL_NEAR_FRAC || price > mid {
                continue;
            }
            let cent = (price * 100.0).round() as i64;
            let since = *self.wall_seen.get(&cent).unwrap_or(&now);
            present.insert(cent, since);
            if nearest_bid.is_none_or(|(bp, _)| price > bp) {
                nearest_bid = Some((price, qty));
                self.wall.bid_since = Some(since);
            }
        }
        for (p, q) in depth.asks.iter() {
            let (price, qty) = (p.to_f64(), q.to_f64());
            if qty < thresh || (price - mid) > mid * WALL_NEAR_FRAC || price < mid {
                continue;
            }
            let cent = (price * 100.0).round() as i64;
            let since = *self.wall_seen.get(&cent).unwrap_or(&now);
            present.insert(cent, since);
            if nearest_ask.is_none_or(|(ap, _)| price < ap) {
                nearest_ask = Some((price, qty));
                self.wall.ask_since = Some(since);
            }
        }

        self.wall_seen = present; // walls no longer present are dropped (pulled/spoofed)
        match nearest_bid {
            Some((price, qty)) => {
                self.wall.bid_price = price;
                self.wall.bid_qty = qty;
            }
            None => {
                self.wall.bid_qty = 0.0;
                self.wall.bid_since = None;
            }
        }
        match nearest_ask {
            Some((price, qty)) => {
                self.wall.ask_price = price;
                self.wall.ask_qty = qty;
            }
            None => {
                self.wall.ask_qty = 0.0;
                self.wall.ask_since = None;
            }
        }
    }

    /// Drop trades older than the VP window, keeping `vol_by_bin` in sync.
    fn prune(&mut self, now: Instant) {
        while let Some(front) = self.trades.front() {
            if now.duration_since(front.at).as_secs_f64() <= VP_WINDOW_SECS {
                break;
            }
            let rec = self.trades.pop_front().unwrap();
            if let Some(v) = self.vol_by_bin.get_mut(&rec.bin) {
                *v -= rec.qty;
                if *v <= f64::MIN_POSITIVE {
                    self.vol_by_bin.remove(&rec.bin);
                }
            }
        }
    }

    /// POC + value-area edges (prices) from the rolling volume profile.
    fn volume_profile(&self) -> Option<(f64, f64, f64)> {
        if self.vol_by_bin.is_empty() {
            return None;
        }
        let total: f64 = self.vol_by_bin.values().sum();
        let mut bins: Vec<(i64, f64)> =
            self.vol_by_bin.iter().map(|(b, v)| (*b, *v)).collect();
        bins.sort_by_key(|(b, _)| *b);
        let poc_idx = bins
            .iter()
            .enumerate()
            .max_by(|a, b| a.1.1.partial_cmp(&b.1.1).unwrap())
            .map(|(i, _)| i)?;

        let (mut lo, mut hi) = (poc_idx, poc_idx);
        let mut acc = bins[poc_idx].1;
        while acc < VALUE_AREA_PCT * total && (lo > 0 || hi < bins.len() - 1) {
            let up = if hi + 1 < bins.len() { bins[hi + 1].1 } else { -1.0 };
            let dn = if lo > 0 { bins[lo - 1].1 } else { -1.0 };
            if up >= dn {
                hi += 1;
                acc += up.max(0.0);
            } else {
                lo -= 1;
                acc += dn.max(0.0);
            }
        }
        let poc = bins[poc_idx].0 as f64 * self.bin_size;
        let vah = bins[hi].0 as f64 * self.bin_size;
        let val = bins[lo].0 as f64 * self.bin_size;
        Some((poc, vah, val))
    }

    /// Aggressive buy / sell notional over the recent absorption window.
    fn recent_flow(&self, now: Instant) -> (f64, f64) {
        let (mut buy, mut sell) = (0.0, 0.0);
        for rec in self.trades.iter().rev() {
            if now.duration_since(rec.at).as_secs_f64() > RECENT_DELTA_SECS {
                break;
            }
            if rec.is_sell {
                sell += rec.qty;
            } else {
                buy += rec.qty;
            }
        }
        (buy, sell)
    }

    fn evaluate(&mut self, now: Instant, sym: &str) -> Option<Signal> {
        if let Some(t) = self.last_alert
            && now.duration_since(t).as_secs_f64() < COOLDOWN_SECS
        {
            return None;
        }
        if self.total_trades < WARMUP_TRADES || self.bin_size <= 0.0 {
            return None;
        }
        let (poc, vah, val) = self.volume_profile()?;
        let price = self.last_price;
        if price <= 0.0 {
            return None;
        }
        let (buy, sell) = self.recent_flow(now);
        let tol = price * LEVEL_TOL_FRAC;

        let persisted = |since: Option<Instant>| {
            since.is_some_and(|t| now.duration_since(t).as_secs_f64() >= WALL_PERSIST_SECS)
        };

        // ── LONG: absorption at support (bid wall soaks aggressive selling) ──
        let near_support = (price - poc).abs() <= tol || (price - val).abs() <= tol;
        if near_support
            && self.wall.bid_qty > 0.0
            && persisted(self.wall.bid_since)
            && price >= self.wall.bid_price
            && sell >= ABSORB_RATIO * buy.max(f64::MIN_POSITIVE)
            && sell >= ABSORB_VS_WALL * self.wall.bid_qty
        {
            let lvl = if (price - val).abs() <= tol { "VAL" } else { "POC" };
            self.last_alert = Some(now);
            push_mark(ConfluenceMark {
                symbol: sym.to_string(),
                price,
                bearish: false,
                at_ms: self.last_time_ms,
            });
            return Some(Signal {
                title: format!("🟢 LONG absorción · {sym}"),
                body: format!(
                    "{sym} en {lvl} {:.2} · pared bid {:.0} defiende ({:.0}s) · \
                     venta agresiva {:.0} absorbida, precio aguanta",
                    self.wall.bid_price,
                    self.wall.bid_qty,
                    wall_age(self.wall.bid_since, now),
                    sell,
                ),
            });
        }

        // ── SHORT: absorption at resistance (ask wall soaks aggressive buying) ──
        let near_resist = (price - poc).abs() <= tol || (price - vah).abs() <= tol;
        if near_resist
            && self.wall.ask_qty > 0.0
            && persisted(self.wall.ask_since)
            && price <= self.wall.ask_price
            && buy >= ABSORB_RATIO * sell.max(f64::MIN_POSITIVE)
            && buy >= ABSORB_VS_WALL * self.wall.ask_qty
        {
            let lvl = if (price - vah).abs() <= tol { "VAH" } else { "POC" };
            self.last_alert = Some(now);
            push_mark(ConfluenceMark {
                symbol: sym.to_string(),
                price,
                bearish: true,
                at_ms: self.last_time_ms,
            });
            return Some(Signal {
                title: format!("🔴 SHORT absorción · {sym}"),
                body: format!(
                    "{sym} en {lvl} {:.2} · pared ask {:.0} defiende ({:.0}s) · \
                     compra agresiva {:.0} absorbida, precio no pasa",
                    self.wall.ask_price,
                    self.wall.ask_qty,
                    wall_age(self.wall.ask_since, now),
                    buy,
                ),
            });
        }

        let _ = vah; // (referenced above; silence if branches change)
        None
    }
}

fn wall_age(since: Option<Instant>, now: Instant) -> f64 {
    since.map_or(0.0, |t| now.duration_since(t).as_secs_f64())
}

/// Live orderflow confluence detector, one lightweight state per symbol.
#[derive(Default)]
pub struct ConfluenceDetector {
    per: FxHashMap<String, SymbolState>,
}

impl ConfluenceDetector {
    pub fn new() -> Self {
        Self::default()
    }

    /// Feed the latest order-book snapshot (updates the wall / anti-spoof state).
    pub fn on_depth(&mut self, stream: &StreamKind, depth: &Depth) {
        let sym = stream.ticker_info().ticker.to_string();
        let st = self.per.entry(sym.clone()).or_insert_with(SymbolState::new);
        let now = Instant::now();
        st.update_walls(depth, now);

        let persisted = |since: Option<Instant>| {
            since.is_some_and(|t| now.duration_since(t).as_secs_f64() >= WALL_PERSIST_SECS)
        };
        set_walls(
            &sym,
            WallSnapshot {
                bid_price: st.wall.bid_price,
                bid_qty: st.wall.bid_qty,
                bid_persisted: persisted(st.wall.bid_since),
                ask_price: st.wall.ask_price,
                ask_qty: st.wall.ask_qty,
                ask_persisted: persisted(st.wall.ask_since),
            },
        );
    }

    /// Feed the latest trades; returns a signal when confluence fires.
    pub fn on_trades(&mut self, stream: &StreamKind, trades: &[Trade]) -> Option<Signal> {
        let sym = stream.ticker_info().ticker.to_string();
        let st = self.per.entry(sym.clone()).or_insert_with(SymbolState::new);
        let now = Instant::now();
        for t in trades {
            let price = t.price.to_f64();
            let qty = t.qty.to_f64();
            if price <= 0.0 || qty <= 0.0 {
                continue;
            }
            if st.bin_size <= 0.0 {
                st.bin_size = (price * BIN_FRAC).max(f64::MIN_POSITIVE);
            }
            let bin = st.bin_of(price);
            *st.vol_by_bin.entry(bin).or_insert(0.0) += qty;
            st.trades.push_back(TradeRec {
                at: now,
                qty,
                is_sell: t.is_sell,
                bin,
            });
            st.total_trades += 1;
            st.last_price = price;
            st.last_time_ms = t.time.as_u64();
        }
        st.prune(now);
        st.evaluate(now, &sym)
    }
}
