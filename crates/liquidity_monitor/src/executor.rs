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
use crate::exec::{ExecClient, ClosedPnl};
use crate::levels::{Level, Side, Gestion, TRAIL_ATR};
use crate::supa::SupaClient;

/// Métricas del cierre (reusado en cierre normal y en restore tras downtime).
/// Devuelve (exit_px, result_r, reason, qty, mfe_r, mae_r, fee_r).
fn close_metrics(lv: &Level, entry: f64, cp: &ClosedPnl, seen_hi: f64, seen_lo: f64)
    -> (f64, f64, String, f64, f64, f64, f64) {
    let exit = if cp.avg_exit > 0.0 { cp.avg_exit } else { entry };
    let aent = if cp.avg_entry > 0.0 { cp.avg_entry } else { entry };
    let qty  = cp.qty;
    let risk = (entry - lv.stop).abs();
    let dir  = if lv.side == Side::Long { 1.0 } else { -1.0 };
    let r = if risk > 0.0 { dir * (exit - entry) / risk } else { 0.0 };
    let (mfe_r, mae_r) = if risk > 0.0 {
        match lv.side {
            Side::Long  => ((seen_hi - entry) / risk, (entry - seen_lo) / risk),
            Side::Short => ((entry - seen_lo) / risk, (seen_hi - entry) / risk),
        }
    } else { (0.0, 0.0) };
    // fee real en R = (bruto - neto) / riesgo_usdt
    let gross = dir * (exit - aent) * qty;
    let fee_r = if risk * qty > 0.0 { (gross - cp.pnl) / (risk * qty) } else { 0.0 };
    let reason = if lv.gestion == Gestion::Trail { "trail".to_string() }
        else if (exit - lv.tp).abs() <= (exit - lv.stop).abs() { "target".to_string() }
        else { "stop".to_string() };
    (exit, r, reason, qty, mfe_r, mae_r, fee_r)
}

const POLL_MS: i64 = 8_000;        // reconciliar con el exchange cada ~8s
const COOLDOWN_BARS: i64 = 6;      // anti-spam (paridad backtest)
const MAX_TRADES_DAY: u32 = 2;
const BAR_MS: i64 = 15 * 60_000;

struct Resting { order_id: String, level: Level }
struct Active { level: Level, entry_px: f64, fill_ts: i64, ttf_s: i64, exits_armed: bool,
                seen_hi: f64, seen_lo: f64, bar_ts: i64 }

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
    placed_cum: u64,   // órdenes colocadas acumuladas (fill ratio real)
    filled_cum: u64,   // órdenes llenadas acumuladas
}

impl Executor {
    pub async fn new(cli: ExecClient, symbol: String, qty: String, px_dec: usize,
                     leverage: u32, supa: Option<Arc<SupaClient>>) -> Self {
        if let Err(e) = cli.set_leverage(&symbol, leverage).await {
            eprintln!("[exec] set_leverage warn: {e}");
        }
        let _ = cli.cancel_all(&symbol).await;   // cancela órdenes resting viejas (NO toca la posición)
        // ── RESTAURAR estado tras redeploy (la posición y su stop viven en el exchange) ──
        let live = cli.position(&symbol).await.ok();
        let open = live.as_ref().map(|p| p.size > 0.0).unwrap_or(false);
        let ctx  = if let Some(s) = &supa { s.load_exec_pos().await } else { None };
        let phase = match (open, ctx) {
            // (a) hay posición + contexto guardado → RESTAURAR y seguir gestionándola
            (true, Some((lv, entry, fill_ts, ttf, armed, seen_hi, seen_lo, bar_ts))) => {
                eprintln!("[exec] RESTAURADA posición {} @ {} ttf={ttf}s gestion={:?} (sobrevive redeploy)",
                          lv.side.as_str(), entry, lv.gestion);
                Phase::InPos(Active { level: lv, entry_px: entry, fill_ts, ttf_s: ttf,
                                      exits_armed: armed, seen_hi, seen_lo, bar_ts })
            }
            // (b) hay posición SIN contexto → huérfana real → cerrar a mercado
            (true, None) => {
                let p = live.as_ref().unwrap();
                let cs = if p.side == "Buy" { "Sell" } else { "Buy" };
                match cli.place_market(&symbol, cs, &format!("{}", p.size), true, "liq-reconcile").await {
                    Ok(_)  => eprintln!("[exec] huérfana SIN contexto cerrada: {} {}", p.side, p.size),
                    Err(e) => eprintln!("[exec] WARN no pude cerrar huérfana: {e}"),
                }
                Phase::Idle
            }
            // (c) había contexto pero la posición ya cerró (downtime) → registrar (rico) y limpiar
            (false, Some((lv, entry, fill_ts, ttf, _, seen_hi, seen_lo, bar_ts))) => {
                if let Some(s) = &supa {
                    let cp = cli.last_closed_pnl(&symbol).await.unwrap_or_default();
                    let (exit, r, reason, q, mfe, mae, fee) = close_metrics(&lv, entry, &cp, seen_hi, seen_lo);
                    eprintln!("[exec] cerró durante downtime → registrando {reason} exit={exit} R={r:.2}");
                    s.write_exec_trade(&lv, entry, exit, cp.pnl, r, &reason, q, mfe, mae, fee, ttf, bar_ts, fill_ts, fill_ts).await;
                    s.clear_exec_pos().await;
                }
                Phase::Idle
            }
            (false, None) => Phase::Idle,
        };
        eprintln!("[exec] ejecutor LISTO {symbol} qty={qty} base={} lev={leverage} phase={}",
                  cli.base(), if matches!(phase, Phase::InPos(_)) { "InPos(restaurada)" } else { "Idle" });
        Self { cli, symbol, qty, px_dec, supa, phase,
               last_poll: 0, last_open_bar: -1, day: 0, day_opens: 0, placed_ts: 0,
               placed_cum: 0, filled_cum: 0 }
    }

    /// Actualiza extremos MFE/MAE en cada tick (barato, sin I/O). Llamado por cada publicTrade.
    pub fn on_tick(&mut self, px: f64) {
        if let Phase::InPos(a) = &mut self.phase {
            if px > a.seen_hi { a.seen_hi = px; }
            if px < a.seen_lo { a.seen_lo = px; }
        }
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
        // 1) reconciliar: ¿llenó alguna resting entre polls? (evita refrescar sobre una posición)
        if matches!(self.phase, Phase::Resting(_)) { self.poll_resting(ts).await; }
        // snapshot de fill ratio real (cada barra)
        if let Some(s) = &self.supa {
            let open = if matches!(self.phase, Phase::InPos(_)) { 1 } else { 0 };
            s.write_exec_snapshot(self.placed_cum, self.filled_cum, open).await;
        }
        if let Phase::InPos(a) = &self.phase {
            // re-persistir MFE/MAE actual para que sobreviva un redeploy con datos frescos
            if let Some(s) = &self.supa {
                s.save_exec_pos(&a.level, a.entry_px, a.fill_ts, a.ttf_s, a.exits_armed, a.seen_hi, a.seen_lo, a.bar_ts).await;
            }
            return;   // gestionando posición → no tocar entradas
        }
        // 2) REFRESH cada barra: cancelar las resting viejas y recolocar en los niveles ACTUALES
        //    (igual que la estrategia, que re-evalúa por barra; evita órdenes pegadas en niveles viejos)
        let _ = self.cli.cancel_all(&self.symbol).await;
        self.phase = Phase::Idle;
        if !self.entries_allowed(ts) || levels.is_empty() { return; }
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
        self.placed_cum += resting.len() as u64;
        if !resting.is_empty() {
            self.placed_ts = ts;
            eprintln!("[exec] {} entradas PostOnly (refresh) @ bar {} | fill ratio acum {}/{}",
                      resting.len(), ts / BAR_MS, self.filled_cum, self.placed_cum);
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
        let armed = self.arm_exits(&lv).await;
        let ttf = (ts - self.placed_ts) / 1000;
        eprintln!("[exec] FILL {} {:?} @ {} ttf={}s gestion={:?} exits_armed={armed}", self.symbol, lv.side, pos.avg_price, ttf, lv.gestion);
        let d = ts / 86_400_000;
        if d != self.day { self.day = d; self.day_opens = 0; }
        self.day_opens += 1; self.last_open_bar = ts / BAR_MS; self.filled_cum += 1;
        let entry = pos.avg_price; let bar_ts = self.placed_ts;
        if let Some(s) = &self.supa {
            s.save_exec_pos(&lv, entry, ts, ttf, armed, entry, entry, bar_ts).await;   // persistir (sobrevive redeploy)
        }
        self.phase = Phase::InPos(Active { level: lv, entry_px: entry, fill_ts: ts, ttf_s: ttf,
                                           exits_armed: armed, seen_hi: entry, seen_lo: entry, bar_ts });
    }

    /// Pone stop/tp (fade) o trailing (trail) exchange-side. Devuelve true si lo logró.
    async fn arm_exits(&self, lv: &Level) -> bool {
        let r = match lv.gestion {
            Gestion::Trail => self.cli.set_trading_stop(&self.symbol, None, None, Some(&self.fmt(TRAIL_ATR * lv.atr))).await,
            // V1: stop + tp2 (estructural) exchange-side. (parcial 50%@tp1 = V2)
            Gestion::Fade  => self.cli.set_trading_stop(&self.symbol, Some(&self.fmt(lv.stop)), Some(&self.fmt(lv.tp)), None).await,
        };
        match r { Ok(_) => true, Err(e) => { eprintln!("[exec] arm exits fail: {e}"); false } }
    }

    async fn poll_inpos(&mut self, ts: i64) {
        let pos = match self.cli.position(&self.symbol).await { Ok(p) => p, Err(_) => return };
        if pos.size > 0.0 {
            // self-heal: si el stop no se armó (falló en el fill), reintentar hasta lograrlo.
            let need = matches!(&self.phase, Phase::InPos(a) if !a.exits_armed);
            if need {
                let lv = if let Phase::InPos(a) = &self.phase { a.level.clone() } else { return };
                let armed = self.arm_exits(&lv).await;
                if armed { eprintln!("[exec] stop re-armado OK {}", self.symbol); }
                if let Phase::InPos(a) = &mut self.phase { a.exits_armed = armed; }
            }
            return;                                          // sigue abierta (exchange gestiona stop/tp/trail)
        }
        // cerrada → registrar (rico: reason, MFE/MAE, fee real, qty, link al paper)
        let cp = self.cli.last_closed_pnl(&self.symbol).await.unwrap_or_default();
        if let Phase::InPos(a) = std::mem::replace(&mut self.phase, Phase::Idle) {
            let (exit, r, reason, q, mfe, mae, fee) = close_metrics(&a.level, a.entry_px, &cp, a.seen_hi, a.seen_lo);
            eprintln!("[exec] CLOSED {} {reason} exit={exit} R={r:.2} mfe={mfe:.2} mae={mae:.2} fee={fee:.2}", self.symbol);
            if let Some(s) = &self.supa {
                s.write_exec_trade(&a.level, a.entry_px, exit, cp.pnl, r, &reason, q, mfe, mae, fee,
                                   a.ttf_s, a.bar_ts, a.fill_ts, ts).await;
                s.clear_exec_pos().await;   // posición cerrada → limpiar contexto persistido
            }
        }
        let _ = self.cli.cancel_all(&self.symbol).await;
    }
}
