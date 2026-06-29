//! liquidity_monitor — estrategia de provisión de liquidez en Rust.
//!
//! Reemplaza live/paper_liquidity.py con:
//!   - WS Bybit publicTrade + kline.15 (tokio-tungstenite, sin GIL)
//!   - FootprintAccumulator inline (HashMap<u32,(f64,f64)>)
//!   - compute_levels() en Rust (paridad con backtest)
//!   - PaperBook para fill ratio + PnL virtual
//!   - Supabase REST (mismas tablas que el Python)
//!
//! Env:
//!   SYMBOL, TF, SYSTEM (maker|flow|both), HIGH_VOL_ONLY (true|false)
//!   SUPABASE_URL, SUPABASE_KEY

mod book;
mod levels;
mod supa;
#[allow(dead_code)]
mod exec;
#[allow(dead_code)]
mod executor;

use book::{PaperBook, OpenPos};
#[allow(unused_imports)]
use levels::{ClosedBar, System, update_atr};
use supa::SupaClient;

use std::collections::{HashMap, VecDeque};
use std::sync::Arc;
use std::time::Duration;

use futures::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::time::interval;
use tokio_tungstenite::{connect_async, tungstenite::Message};

// ── Config ──────────────────────────────────────────────────────────────────

// Bybit geo-bloquea api.bybit.com (CloudFront) desde algunos países (p.ej. el IP de
// Railway). bytick.com es el dominio ESPEJO oficial con otra config → fallback.
const WS_HOSTS:   [&str; 2] = ["wss://stream.bybit.com/v5/public/linear",
                               "wss://stream.bytick.com/v5/public/linear"];
const REST_HOSTS: [&str; 2] = ["https://api.bybit.com", "https://api.bytick.com"];
const MAX_BARS:     usize = 700;   // 500 ATR median + 200 buffer

fn env(key: &str, default: &str) -> String {
    std::env::var(key).unwrap_or_else(|_| default.to_string())
}

// ── Footprint helpers ────────────────────────────────────────────────────────

fn fp_add(fp: &mut HashMap<u32, (f64, f64)>, price: f64, qty: f64, is_sell: bool) {
    let bin = (price / levels::bin()).round() as u32;
    let entry = fp.entry(bin).or_insert((0.0, 0.0));
    if is_sell { entry.1 += qty; } else { entry.0 += qty; }
}

// ── Bootstrap REST ───────────────────────────────────────────────────────────

/// Bootstrap RESILIENTE: reintenta con backoff y NUNCA paniquea. Un hipo de la REST
/// de Bybit (rate-limit, página de error de CloudFront, blip) ya NO mata el proceso
/// — antes el `.expect()` lo hacía crashear → Railway reiniciaba → re-machacaba la API
/// → rate-limit sostenido → crash-loop infinito.
async fn bootstrap(symbol: &str, tf: &str) -> Vec<ClosedBar> {
    let client = reqwest::Client::new();
    let mut attempt: u32 = 0;
    loop {
        attempt += 1;
        for host in REST_HOSTS {                       // prueba bybit.com, luego el espejo bytick.com
            match try_bootstrap(&client, host, symbol, tf).await {
                Ok(bars) if !bars.is_empty() => {
                    if host != REST_HOSTS[0] { eprintln!("[bootstrap] OK via fallback {host}"); }
                    return bars;
                }
                Ok(_)  => eprintln!("[bootstrap] {host} intento {attempt}: lista vacía"),
                Err(e) => eprintln!("[bootstrap] {host} intento {attempt} falló: {e}"),
            }
        }
        let wait = std::cmp::min(60, 2u64.pow(attempt.min(6)));   // 2,4,8,…,60s
        eprintln!("[bootstrap] esperando {wait}s antes de reintentar (no crash-loop)");
        tokio::time::sleep(Duration::from_secs(wait)).await;
    }
}

async fn try_bootstrap(client: &reqwest::Client, host: &str, symbol: &str, tf: &str) -> Result<Vec<ClosedBar>, String> {
    let resp = client
        .get(format!("{}/v5/market/kline", host))
        .query(&[("category", "linear"), ("symbol", symbol),
                 ("interval", tf), ("limit", "1000")])
        .send().await.map_err(|e| format!("REST: {e}"))?;
    let status = resp.status();
    let txt = resp.text().await.map_err(|e| format!("body: {e}"))?;
    // Parsear desde texto para poder LOGUEAR el cuerpo real si no es el JSON esperado.
    let v: Value = serde_json::from_str(&txt)
        .map_err(|e| format!("parse ({status}): {e} | body[..160]={:?}",
                             txt.chars().take(160).collect::<String>()))?;
    let list = v["result"]["list"].as_array()
        .ok_or_else(|| format!("sin result.list ({status}): {:?}",
                               txt.chars().take(160).collect::<String>()))?;
    let mut bars: Vec<ClosedBar> = list.iter().rev().map(|k| {
        let ts_ms = k[0].as_str().unwrap_or("0").parse::<i64>().unwrap_or(0);
        let open  = k[1].as_str().unwrap_or("0").parse::<f64>().unwrap_or(0.0);
        let high  = k[2].as_str().unwrap_or("0").parse::<f64>().unwrap_or(0.0);
        let low   = k[3].as_str().unwrap_or("0").parse::<f64>().unwrap_or(0.0);
        let close = k[4].as_str().unwrap_or("0").parse::<f64>().unwrap_or(0.0);
        let vol   = k[5].as_str().unwrap_or("0").parse::<f64>().unwrap_or(0.0);
        ClosedBar::from_bootstrap(ts_ms, open, high, low, close, vol)
    }).collect();
    // La última barra puede estar incompleta (kline actual) — descartarla
    if bars.last().map(|b| b.ts_ms).unwrap_or(0) > chrono::Utc::now().timestamp_millis() - 60_000 {
        bars.pop();
    }
    Ok(bars)
}

// ── ATR median ───────────────────────────────────────────────────────────────

fn compute_atr_series(bars: &[ClosedBar]) -> Vec<f64> {
    if bars.len() < levels::ATR_N + 1 { return vec![]; }
    let mut atrs = Vec::with_capacity(bars.len());
    let mut cur = 0.0_f64;
    for i in 1..bars.len() {
        cur = update_atr(cur, &bars[i], bars[i-1].close, levels::ATR_N);
        if i >= levels::ATR_N { atrs.push(cur); }
    }
    atrs
}

fn median_of(v: &[f64]) -> f64 {
    if v.is_empty() { return 0.0; }
    let mut s = v.to_vec();
    s.sort_by(|a, b| a.partial_cmp(b).unwrap());
    s[s.len() / 2]
}

// ── Estado principal ──────────────────────────────────────────────────────────

struct State {
    bars:          VecDeque<ClosedBar>,
    cur_fp:        HashMap<u32, (f64, f64)>,
    cur_delta:     f64,
    cur_atr:       f64,
    atr_history:   VecDeque<f64>,
    oi_history:    VecDeque<(i64, f64)>,   // (ts_ms, open_interest)
    books:         Vec<PaperBook>,
    symbol:        String,
    tf:            String,
    high_vol_only: bool,
    disable_h5:    bool,
    tp2_cap_r:     f64,
    supa:          Option<Arc<SupaClient>>,
    tick_count:    u64,   // ticks (publicTrade) recibidos en la barra en curso (diagnóstico)
    bar_count:     u64,   // barras procesadas desde inicio — para logs periódicos
    last_bar_ts:   i64,   // ts_ms de la última barra procesada — dedup contra reenvíos WS reconexión
    executor:      Option<executor::Executor>,  // ejecución real testnet/live (None = solo paper)
}

impl State {
    fn systems_for(sys: &str) -> Vec<System> {
        match sys {
            "maker" => vec![System::Maker],
            "flow"  => vec![System::Flow],
            _       => vec![System::Maker, System::Flow],
        }
    }

    fn atr_median(&self) -> f64 {
        let window: Vec<f64> = self.atr_history.iter()
            .rev().take(500).copied().collect();
        median_of(&window)
    }

    /// MA20 del ATR (para el detector de régimen: expansión = atr_actual / atr_ma20 > 1.30).
    fn atr_ma20(&self) -> f64 {
        let w: Vec<f64> = self.atr_history.iter().rev().take(20).copied().collect();
        if w.is_empty() { 0.0 } else { w.iter().sum::<f64>() / w.len() as f64 }
    }

    /// Multiplicador de sizing basado en OI direction (1h lookback).
    /// up >+0.1% → 2.0x | down <-0.1% → 0.5x | estable → 1.0x
    fn oi_size_mult(&self) -> f64 {
        if self.oi_history.len() < 2 { return 1.0; }
        let now_ts = self.oi_history.back().map(|(t,_)| *t).unwrap_or(0);
        let lookback_ms = 60 * 60_000i64;
        let past_oi = self.oi_history.iter()
            .rev()
            .find(|(t, _)| now_ts - t >= lookback_ms)
            .map(|(_, v)| *v);
        let now_oi = self.oi_history.back().map(|(_, v)| *v).unwrap_or(0.0);
        match past_oi {
            Some(p) if p > 0.0 => {
                let chg_pct = (now_oi - p) / p * 100.0;
                if chg_pct > 0.1 { 2.0 } else if chg_pct < -0.1 { 0.5 } else { 1.0 }
            }
            _ => 1.0,
        }
    }

    fn on_trade(&mut self, price: f64, qty: f64, is_sell: bool, ts: i64) {
        self.tick_count += 1;
        fp_add(&mut self.cur_fp, price, qty, is_sell);
        if is_sell { self.cur_delta -= qty; } else { self.cur_delta += qty; }
        let delta = self.cur_delta;
        for book in &mut self.books {
            book.on_trade(price, ts, delta);
        }
    }

    async fn on_bar_close(&mut self, ts_ms: i64, open: f64, high: f64, low: f64, close: f64, vol: f64) {
        // Dedup: Bybit reenvía la última barra confirmada al reconectar el WS → ignorar duplicado
        if ts_ms == self.last_bar_ts { return; }
        self.last_bar_ts = ts_ms;
        self.bar_count += 1;

        // 1. Crear barra cerrada con footprint acumulado
        let ticks = self.tick_count; self.tick_count = 0;   // diagnóstico: ticks recibidos esta barra
        let fp = std::mem::take(&mut self.cur_fp);
        let fp_bins = fp.len();
        self.cur_delta = 0.0;
        let bar = ClosedBar::from_live(ts_ms, open, high, low, close, vol, fp);

        // 2. Actualizar ATR incremental
        let prev_close = self.bars.back().map(|b| b.close).unwrap_or(close);
        self.cur_atr = update_atr(self.cur_atr, &bar, prev_close, levels::ATR_N);
        self.atr_history.push_back(self.cur_atr);
        if self.atr_history.len() > 600 { self.atr_history.pop_front(); }

        // 3. Persistir footprint en Supabase
        if let Some(ref supa) = self.supa {
            let s = supa.clone();
            let b = bar.clone();
            tokio::spawn(async move { s.write_footprint(&b).await; });
        }

        // 4. Guardar barra
        self.bars.push_back(bar);
        if self.bars.len() > MAX_BARS { self.bars.pop_front(); }

        // 5. Calcular niveles
        let bars_slice: Vec<ClosedBar> = self.bars.iter().cloned().collect();
        let atr_med = self.atr_median();
        let atr_ma  = self.atr_ma20();
        let atr     = self.cur_atr;

        // Determinar sistema de cada book y calcular sus niveles
        let system_per_book: Vec<System> = self.books.iter().map(|b| {
            if b.system == "maker" { System::Maker } else { System::Flow }
        }).collect();

        let mut book_levels: Vec<Vec<levels::Level>> = system_per_book.iter().map(|&sys| {
            levels::compute_levels(&bars_slice, atr, atr_med, atr_ma, sys, self.high_vol_only, self.disable_h5, self.tp2_cap_r)
        }).collect();

        // 6. Diagnóstico cada hora (12 barras M15) cuando todos los libros quedan sin niveles
        if self.bar_count % 12 == 0 && book_levels.iter().all(|lv| lv.is_empty()) {
            let reason = levels::diag_block(&bars_slice, atr, atr_med, self.high_vol_only);
            eprintln!("[diag] NO_LEVELS  {reason}  atr={atr:.1} med={atr_med:.1} ({:.0}%)  \
                       h1_up={}  price={close:.1}",
                      if atr_med > 0.0 { 100.0 * atr / atr_med } else { 0.0 },
                      if bars_slice.len() > levels::H1_BARS { close > bars_slice[bars_slice.len()-1-levels::H1_BARS].close } else { false });
        }

        // 6. Refresh orders + flush a Supabase
        let supa = self.supa.clone();
        let oi_mult = self.oi_size_mult();
        for (i, book) in self.books.iter_mut().enumerate() {
            let lvls = std::mem::take(&mut book_levels[i]);
            book.refresh(lvls, ts_ms, oi_mult);

            let events = book.drain_events();
            let trades = book.drain_trades();

            println!("  [{}] {}", book.system.to_uppercase(), book.status_line());

            // Persistir posiciones abiertas para sobrevivir redeploys.
            if let Some(ref s) = supa {
                let sc = s.clone();
                let sys_name = book.system.clone();
                let open_snapshot: Vec<OpenPos> = book.open_positions().to_vec();
                tokio::spawn(async move {
                    sc.save_open_positions(&sys_name, &open_snapshot).await;
                });
            }

            if let Some(ref s) = supa {
                let sc = s.clone();
                let ev = events.clone();
                let tr = trades.clone();
                let cp = close;
                let placed = book.placed;
                let filled = book.filled;
                let sys_name = book.system.clone();
                tokio::spawn(async move {
                    sc.write_events(&ev, &sys_name).await;
                    sc.write_trades(&tr).await;
                    // snapshot simple
                    let snap = serde_json::json!([{
                        "symbol": sc.symbol_ref(),
                        "tf":     sc.tf_ref(),
                        "system": sys_name,
                        "at":     supa::SupaClient::iso_ms(ts_ms),
                        "placed_high":     placed[0],
                        "filled_high":     filled[0],
                        "fill_ratio_high": if placed[0] > 0 { filled[0] as f64 / placed[0] as f64 } else { 0.0 },
                        "placed_low":      placed[1],
                        "filled_low":      filled[1],
                        "fill_ratio_low":  if placed[1] > 0 { filled[1] as f64 / placed[1] as f64 } else { 0.0 },
                        "close_px": cp,
                    }]);
                    sc.raw_insert("liquidity_paper_snapshots", snap).await;
                });
            }
        }

        // Ejecutor real (testnet/live): sistema ruteado FLOW, independiente del paper.
        if self.executor.is_some() {
            let hvo = self.high_vol_only; let dh5 = self.disable_h5; let cap = self.tp2_cap_r;
            let flow_levels = levels::compute_levels(&bars_slice, atr, atr_med, atr_ma,
                System::Flow, hvo, dh5, cap);
            if let Some(ex) = self.executor.as_mut() {
                ex.on_bar(&flow_levels, ts_ms).await;
            }
        }

        let now = chrono::Utc::now().format("%m-%d %H:%M").to_string();
        println!("[{now}] M{} bar closed @ {close:.1}  ATR={atr:.1}  atr_med={atr_med:.1}  \
                  ticks={ticks} fp_bins={fp_bins}", self.tf);
    }

    async fn poll_executor(&mut self, ts: i64) {
        if let Some(ex) = self.executor.as_mut() { ex.poll(ts).await; }
    }

    fn exec_on_tick(&mut self, px: f64) {
        if let Some(ex) = self.executor.as_mut() { ex.on_tick(px); }
    }
}

// ── WS message handler ────────────────────────────────────────────────────────

async fn handle_message(state: &mut State, msg: &str) {
    let d: Value = match serde_json::from_str(msg) {
        Ok(v) => v,
        Err(_) => return,
    };
    let topic = d["topic"].as_str().unwrap_or("");

    if topic.starts_with("publicTrade") {
        if let Some(data) = d["data"].as_array() {
            let mut last_ts = 0;
            for t in data {
                let px  = t["p"].as_str().unwrap_or("0").parse::<f64>().unwrap_or(0.0);
                let qty = t["v"].as_str().unwrap_or("0").parse::<f64>().unwrap_or(0.0);
                let ts  = t["T"].as_i64().unwrap_or(0);
                let is_sell = t["S"].as_str() == Some("Sell");
                if px > 0.0 && qty > 0.0 {
                    state.on_trade(px, qty, is_sell, ts);
                    state.exec_on_tick(px);   // MFE/MAE de la posición del ejecutor
                    last_ts = ts;
                }
            }
            if last_ts > 0 { state.poll_executor(last_ts).await; }  // reconciliar exchange (rate-limited)
        }
    } else if topic.starts_with("tickers") {
        // OI llega en data como objeto (snapshot) o delta
        let data = if d["data"].is_object() { &d["data"] } else { &d["data"][0] };
        if let Some(oi_str) = data["openInterest"].as_str() {
            if let Ok(oi) = oi_str.parse::<f64>() {
                let ts = d["ts"].as_i64().unwrap_or(0);
                state.oi_history.push_back((ts, oi));
                // Mantener solo 2h de historial (120 entradas a 1/s es mucho — limitamos a 7200)
                while state.oi_history.len() > 7_200 { state.oi_history.pop_front(); }
            }
        }
    } else if topic.starts_with("kline") {
        if let Some(data) = d["data"].as_array() {
            for bar in data {
                if bar["confirm"].as_bool() != Some(true) { continue; }
                let ts_ms = bar["start"].as_i64().unwrap_or(0);
                let open  = bar["open"].as_str().unwrap_or("0").parse::<f64>().unwrap_or(0.0);
                let high  = bar["high"].as_str().unwrap_or("0").parse::<f64>().unwrap_or(0.0);
                let low   = bar["low"].as_str().unwrap_or("0").parse::<f64>().unwrap_or(0.0);
                let close = bar["close"].as_str().unwrap_or("0").parse::<f64>().unwrap_or(0.0);
                let vol   = bar["volume"].as_str().unwrap_or("0").parse::<f64>().unwrap_or(0.0);
                state.on_bar_close(ts_ms, open, high, low, close, vol).await;
            }
        }
    }
}

// ── main ─────────────────────────────────────────────────────────────────────

#[tokio::main]
async fn main() {
    let _ = dotenvy::dotenv();   // carga .env (raíz del repo o padres) si existe; en Railway usa el dashboard
    let symbol      = env("SYMBOL", "BTCUSDT");
    let tf          = env("TF", "15");
    let system      = env("SYSTEM", "both");
    let hvo         = env("HIGH_VOL_ONLY", "false").to_lowercase() == "true";
    let disable_h5  = env("DISABLE_H5", "false").to_lowercase() == "true";
    let tp2_cap_r   = env("TP2_CAP_R", "0").parse::<f64>().unwrap_or(0.0);
    let fill_margin = env("FILL_MARGIN_BPS", "2").parse::<f64>().unwrap_or(2.0);
    let timeout_h   = env("TIMEOUT_HOURS", "24").parse::<f64>().unwrap_or(24.0);
    let supa_url    = env("SUPABASE_URL", "");
    let supa_key    = env("SUPABASE_KEY", "");

    println!(
        ">>> liquidity_monitor  SYMBOL={symbol}  TF={tf}  SYSTEM={system}  \
         HIGH_VOL_ONLY={hvo}  DISABLE_H5={disable_h5}  TP2_CAP_R={tp2_cap_r}  \
         FILL_MARGIN={fill_margin}bps  TIMEOUT={timeout_h}h"
    );

    let supa = SupaClient::new(&supa_url, &supa_key, &symbol, &tf).map(Arc::new);

    // Bootstrap REST: historial de barras
    println!("[bootstrap] descargando klines M{tf}...");
    let mut boot_bars: Vec<ClosedBar> = bootstrap(&symbol, &tf).await;

    // Ancho de bin del footprint. CLAVE para ETH/SOL: con el $5 fijo de BTC, un
    // precio de $69 (SOL) colapsa toda la barra en 1 bin → POC inútil. Default
    // proporcional al precio (1 bps), override por env FP_BIN.
    //   BTC ~$61k → ~$6  · ETH ~$1650 → ~$0.16  · SOL ~$69 → ~$0.007
    let ref_px = boot_bars.last().map(|b| b.close).unwrap_or(0.0);
    let fp_bin = match std::env::var("FP_BIN").ok().and_then(|s| s.parse::<f64>().ok()) {
        Some(b) if b > 0.0 => b,
        _ => (ref_px * 0.0001).max(1e-9),
    };
    levels::set_bin(fp_bin);
    println!("[FP] bin={fp_bin} (ref_px={ref_px})");

    // Restaurar footprint bars desde Supabase
    let mut restored = 0usize;
    if let Some(ref s) = supa {
        let fp_bars = s.load_footprint(200).await;
        restored = fp_bars.len();
        // Merge: overlay fp/poc del footprint sobre la barra bootstrap (OHLCV correcto)
        for fp_bar in fp_bars {
            if let Some(b) = boot_bars.iter_mut().find(|b| b.ts_ms == fp_bar.ts_ms) {
                b.fp      = fp_bar.fp;
                b.poc     = fp_bar.poc;
                b.fp_real = true;
            }
        }
        println!("[supa] restauradas {restored} barras de footprint");
    }
    if restored == 0 {
        println!("[FP] sin historial — warmup ~5h hasta VP tick activo");
    }

    // Calcular ATR histórico desde el bootstrap
    let atr_series = compute_atr_series(&boot_bars);
    let init_atr   = atr_series.last().copied().unwrap_or(0.0);
    let mut atr_history: VecDeque<f64> = atr_series.into_iter().collect();

    let tf_min     = tf.parse::<f64>().unwrap_or(15.0);
    let cooldown_ms = (levels::COOLDOWN_BARS as f64 * tf_min * 60_000.0) as i64;
    let mut books: Vec<PaperBook> = State::systems_for(&system)
        .into_iter()
        .map(|s| PaperBook::new(
            if s == System::Maker { "maker" } else { "flow" },
            fill_margin,
            timeout_h,
            cooldown_ms,
            levels::MAX_TRADES_DAY,
        ))
        .collect();

    // Restaurar posiciones abiertas desde Supabase (sobreviven redeploys).
    if let Some(ref s) = supa {
        for book in books.iter_mut() {
            let restored_pos = s.load_open_positions(&book.system).await;
            if !restored_pos.is_empty() {
                println!("[supa] restauradas {} posiciones abiertas [{}]",
                         restored_pos.len(), book.system);
                book.restore_positions(restored_pos);
            }
        }
    }

    // ── Ejecutor real (testnet/live), env-gated. EXEC_MODE=off (default) → solo paper.
    let exec_mode = env("EXEC_MODE", "off").to_lowercase();
    let executor = if exec_mode == "testnet" || exec_mode == "demo" || exec_mode == "live" {
        let k = env("BYBIT_API_KEY", ""); let sec = env("BYBIT_API_SECRET", "");
        if k.is_empty() || sec.is_empty() {
            eprintln!("[exec] EXEC_MODE={exec_mode} pero faltan BYBIT_API_KEY/SECRET → ejecutor OFF");
            None
        } else {
            let base = match exec_mode.as_str() {
                "live" => exec::LIVE_BASE,
                "demo" => exec::DEMO_BASE,
                _      => exec::TESTNET_BASE,
            };
            let (qty_step, qty_prec, def_dec): (f64, usize, usize) = match symbol.as_str() {
                "BTCUSDT" => (0.001, 3, 1),
                "ETHUSDT" => (0.01,  2, 2),
                "SOLUSDT" => (0.1,   1, 3),
                _         => (0.01,  2, 2),
            };
            let risk_usdt: f64 = env("RISK_USDT", "5").parse().unwrap_or(5.0);
            let px_dec: usize = env("EXEC_PX_DEC", &def_dec.to_string()).parse().unwrap_or(def_dec);
            let lev: u32 = env("EXEC_LEVERAGE", "1").parse().unwrap_or(1);
            let cli = exec::ExecClient::new(k, sec, base);
            Some(executor::Executor::new(cli, symbol.clone(), risk_usdt, qty_step, qty_prec, px_dec, lev, supa.clone()).await)
        }
    } else { None };

    let mut state = State {
        bars:          boot_bars.into_iter().collect(),
        cur_fp:        HashMap::new(),
        cur_delta:     0.0,
        cur_atr:       init_atr,
        atr_history,
        books,
        symbol:        symbol.clone(),
        tf:            tf.clone(),
        high_vol_only: hvo,
        disable_h5,
        tp2_cap_r,
        oi_history:    VecDeque::new(),
        supa:          supa.clone(),
        tick_count:    0,
        bar_count:     0,
        last_bar_ts:   0,
        executor,
    };

    println!("[bootstrap] {}/700 barras cargadas  ATR={:.1}", state.bars.len(), state.cur_atr);

    // Suscribir y reconectar loop
    let subscribe_msg = json!({
        "op":   "subscribe",
        "args": [
            format!("publicTrade.{symbol}"),
            format!("kline.{tf}.{symbol}"),
            format!("tickers.{symbol}"),
        ]
    }).to_string();

    let mut ws_try: usize = 0;
    loop {
        let ws_url = WS_HOSTS[ws_try % WS_HOSTS.len()];   // alterna bybit/bytick si uno se bloquea
        ws_try += 1;
        println!("[WS] conectando a {ws_url}...");
        match connect_async(ws_url).await {
            Err(e) => {
                eprintln!("[WS] error: {e} — reintentando en 5s");
                tokio::time::sleep(Duration::from_secs(5)).await;
                continue;
            }
            Ok((ws, _)) => {
                let (mut write, mut read) = ws.split();
                if let Err(e) = write.send(Message::Text(subscribe_msg.clone().into())).await {
                    eprintln!("[WS] subscribe error: {e}");
                    continue;
                }
                println!("[WS] suscrito publicTrade + kline.{tf}");

                let mut ping_tick = interval(Duration::from_secs(20));

                loop {
                    tokio::select! {
                        msg = read.next() => {
                            match msg {
                                Some(Ok(Message::Text(txt))) => {
                                    handle_message(&mut state, &txt).await;
                                }
                                Some(Ok(Message::Ping(d))) => {
                                    let _ = write.send(Message::Pong(d)).await;
                                }
                                Some(Err(e)) => {
                                    eprintln!("[WS] error: {e} — reconectando");
                                    break;
                                }
                                None => {
                                    eprintln!("[WS] stream cerrado — reconectando");
                                    break;
                                }
                                _ => {}
                            }
                        }
                        _ = ping_tick.tick() => {
                            let _ = write.send(Message::Text(
                                json!({"op":"ping"}).to_string().into()
                            )).await;
                        }
                    }
                }
            }
        }
        tokio::time::sleep(Duration::from_secs(3)).await;
    }
}
