//! executor.rs — ejecución REAL del sistema ruteado (flow) en perps testnet/live. V1.
//!
//! Filosofía: el EXCHANGE gestiona stop/tp/trailing (robusto ante caídas del proceso);
//! nosotros orquestamos (colocar entradas PostOnly, detectar fills, registrar). Un símbolo
//! por proceso (= un PaperBook). NUNCA paniquea. Mide lo que importa: fill ratio y timing real.
//!
//! Lifecycle: Idle --on_bar--> Resting(PostOnly en cada nivel) --fill--> InPos(stop+tp/trailing
//! exchange-side) --cierre--> Idle (+cooldown). V1 NO hace parcial 50%@tp1 (es V2): usa stop+tp2
//! para fade, trailing nativo para trend.

use std::sync::Arc;
use crate::exec::ExecClient;
use crate::levels::{Level, Side, Gestion, TRAIL_ATR};
use crate::supa::SupaClient;

const POLL_MS: i64 = 8_000;        // reconciliar con el exchange cada ~8s
const COOLDOWN_BARS: i64 = 6;      // anti-spam (paridad backtest)
const MAX_TRADES_DAY: u32 = 2;
const BAR_MS: i64 = 15 * 60_000;

struct Resting { order_id: String, level: Level }
struct Active { level: Level, entry_px: f64, fill_ts: i64, ttf_s: i64 }

enum Phase { Idle, Resting(Vec<Resting>), InPos(Active) }

pub struct Executor {
    cli: ExecClient,
    symbol: String,
    qty: String,
    px_dec: usize,
    supa: Option<Arc<SupaClient>>,
    phase: Phase,
    last_poll: i64,
    last_open_bar: i64,
    day: i64,
    day_opens: u32,
    placed_ts: i64,
}

impl Executor {
    pub async fn new(cli: ExecClient, symbol: String, qty: String, px_dec: usize,
                     leverage: u32, supa: Option<Arc<SupaClient>>) -> Self {
        if let Err(e) = cli.set_leverage(&symbol, leverage).await {
            eprintln!("[exec] set_leverage warn: {e}");
        }
        let _ = cli.cancel_all(&symbol).await;   // arranque limpio
        eprintln!("[exec] ejecutor LISTO {symbol} qty={qty} base={} lev={leverage}", cli.base());
        Self { cli, symbol, qty, px_dec, supa, phase: Phase::Idle,
               last_poll: 0, last_open_bar: -1, day: 0, day_opens: 0, placed_ts: 0 }
    }

    fn fmt(&self, p: f64) -> String { format!("{:.*}", self.px_dec, p) }

    fn entries_allowed(&self, ts: i64) -> bool {
        let bar = ts / BAR_MS;
        if self.last_open_bar >= 0 && bar - self.last_open_bar < COOLDOWN_BARS { return false; }
        let d = ts / 86_400_000;
        if d == self.day && self.day_opens >= MAX_TRADES_DAY { return false; }
        true
    }

    /// Llamado en cada cierre de barra con los niveles del sistema flow.
    pub async fn on_bar(&mut self, levels: &[Level], ts: i64) {
        if !matches!(self.phase, Phase::Idle) { return; }
        if !self.entries_allowed(ts) || levels.is_empty() { return; }
        let _ = self.cli.cancel_all(&self.symbol).await;
        let mut resting = Vec::new();
        for lv in levels {
            let side = if lv.side == Side::Long { "Buy" } else { "Sell" };
            let price = self.fmt(lv.price);
            let link = format!("liq-{}-{}", lv.kind, ts);
            match self.cli.place_limit(&self.symbol, side, &self.qty, &price, false, &link).await {
                Ok(oid) => resting.push(Resting { order_id: oid, level: lv.clone() }),
                Err(e)  => eprintln!("[exec] place {} {} fail: {e}", lv.kind, side),
            }
        }
        if !resting.is_empty() {
            self.placed_ts = ts;
            eprintln!("[exec] colocadas {} entradas PostOnly @ bar {}", resting.len(), ts / BAR_MS);
            self.phase = Phase::Resting(resting);
        }
    }

    /// Reconciliación periódica (rate-limited). last_px = último precio (para R aprox si hace falta).
    pub async fn poll(&mut self, ts: i64) {
        if ts - self.last_poll < POLL_MS { return; }
        self.last_poll = ts;
        match self.phase {
            Phase::Idle => {}
            Phase::Resting(_) => self.poll_resting(ts).await,
            Phase::InPos(_)   => self.poll_inpos(ts).await,
        }
    }

    async fn poll_resting(&mut self, ts: i64) {
        let pos = match self.cli.position(&self.symbol).await { Ok(p) => p, Err(_) => return };
        if pos.size <= 0.0 { return; }                       // nada filleado aún
        let want = if pos.side == "Buy" { Side::Long } else { Side::Short };
        // sacar el nivel filleado del estado resting
        let lv = match std::mem::replace(&mut self.phase, Phase::Idle) {
            Phase::Resting(r) => r.into_iter().find(|x| x.level.side == want).map(|x| x.level),
            other => { self.phase = other; return; }
        };
        let lv = match lv { Some(l) => l, None => {
            eprintln!("[exec] posición {} inesperada, cancelo y reseteo", pos.side);
            let _ = self.cli.cancel_all(&self.symbol).await; return;
        }};
        let _ = self.cli.cancel_all(&self.symbol).await;     // matar el resto de entradas
        self.arm_exits(&lv, pos.avg_price).await;
        let ttf = (ts - self.placed_ts) / 1000;
        eprintln!("[exec] FILL {} {:?} @ {} ttf={}s gestion={:?}", self.symbol, lv.side, pos.avg_price, ttf, lv.gestion);
        let d = ts / 86_400_000;
        if d != self.day { self.day = d; self.day_opens = 0; }
        self.day_opens += 1; self.last_open_bar = ts / BAR_MS;
        self.phase = Phase::InPos(Active { level: lv, entry_px: pos.avg_price, fill_ts: ts, ttf_s: ttf });
    }

    async fn arm_exits(&self, lv: &Level, entry: f64) {
        match lv.gestion {
            Gestion::Trail => {
                let dist = self.fmt(TRAIL_ATR * lv.atr);
                if let Err(e) = self.cli.set_trading_stop(&self.symbol, None, None, Some(&dist)).await {
                    eprintln!("[exec] trailing fail: {e}");
                }
            }
            Gestion::Fade => {
                // V1: stop + tp2 (estructural) exchange-side. (parcial 50%@tp1 = V2)
                let sl = self.fmt(lv.stop); let tp = self.fmt(lv.tp);
                if let Err(e) = self.cli.set_trading_stop(&self.symbol, Some(&sl), Some(&tp), None).await {
                    eprintln!("[exec] stop/tp fail: {e}");
                }
            }
        }
        let _ = entry;
    }

    async fn poll_inpos(&mut self, ts: i64) {
        let pos = match self.cli.position(&self.symbol).await { Ok(p) => p, Err(_) => return };
        if pos.size > 0.0 { return; }                        // sigue abierta (exchange gestiona stop/tp/trail)
        // cerrada → registrar
        let (pnl, exit_px) = self.cli.last_closed_pnl(&self.symbol).await.unwrap_or((0.0, 0.0));
        if let Phase::InPos(a) = std::mem::replace(&mut self.phase, Phase::Idle) {
            let risk = (a.entry_px - a.level.stop).abs();
            let dir = if a.level.side == Side::Long { 1.0 } else { -1.0 };
            let r = if risk > 0.0 && exit_px > 0.0 { dir * (exit_px - a.entry_px) / risk } else { 0.0 };
            eprintln!("[exec] CLOSED {} pnl={:.4} exit={} R={:.2} (entry {} stop {})",
                      self.symbol, pnl, exit_px, r, a.entry_px, a.level.stop);
            if let Some(s) = &self.supa {
                s.write_testnet_trade(&a.level, a.entry_px, exit_px, pnl, r, a.ttf_s, a.fill_ts, ts).await;
            }
        }
        let _ = self.cli.cancel_all(&self.symbol).await;
    }
}
