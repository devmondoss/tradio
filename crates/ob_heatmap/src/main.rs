/// ob_heatmap — reconstruye el libro completo (ob500/ob200) y extrae features de
/// muros en el MOMENTO DEL FILL de cada trade (cuando el mid cruza el entry).
///
/// Uso:
///   cargo run -p ob-heatmap --release -- \
///     --ob-dir E:/bybit-data/bybit-perp-eth/orderbook \
///     --trades backtest/_trades_ETHUSDT.csv \
///     --out backtest/_heatmap_ETHUSDT.csv
use std::collections::{BTreeMap, HashMap};
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

use chrono::{TimeZone, Utc};
use rayon::prelude::*;

// ── Order Book (BTreeMap precio*100 → size) ──────────────────────────────────
struct OrderBook { bids: BTreeMap<u64, f64>, asks: BTreeMap<u64, f64> }
impl OrderBook {
    fn new() -> Self { Self { bids: BTreeMap::new(), asks: BTreeMap::new() } }
    fn apply(&mut self, data: &serde_json::Value, snap: bool) {
        if snap { self.bids.clear(); self.asks.clear(); }
        if let Some(arr) = data["b"].as_array() {
            for e in arr {
                let k = pk(e[0].as_str().unwrap_or("0"));
                let q: f64 = e[1].as_str().unwrap_or("0").parse().unwrap_or(0.0);
                if q == 0.0 { self.bids.remove(&k); } else { self.bids.insert(k, q); }
            }
        }
        if let Some(arr) = data["a"].as_array() {
            for e in arr {
                let k = pk(e[0].as_str().unwrap_or("0"));
                let q: f64 = e[1].as_str().unwrap_or("0").parse().unwrap_or(0.0);
                if q == 0.0 { self.asks.remove(&k); } else { self.asks.insert(k, q); }
            }
        }
    }
    fn best_bid(&self) -> f64 { self.bids.keys().next_back().map_or(0.0, |k| *k as f64 / 100.0) }
    fn best_ask(&self) -> f64 { self.asks.keys().next().map_or(0.0, |k| *k as f64 / 100.0) }
    /// suma de liquidez y muro más grande (precio, size) en el rango [lo, hi]
    fn range_liq(&self, bids: bool, lo: f64, hi: f64) -> (f64, f64, f64) {
        let book = if bids { &self.bids } else { &self.asks };
        let (klo, khi) = ((lo * 100.0).round() as u64, (hi * 100.0).round() as u64);
        let (mut sum, mut wp, mut ws) = (0.0, 0.0, 0.0);
        for (k, v) in book.range(klo..=khi) {
            sum += *v;
            if *v > ws { ws = *v; wp = *k as f64 / 100.0; }
        }
        (sum, wp, ws)
    }
}
#[inline]
fn pk(s: &str) -> u64 { (s.parse::<f64>().unwrap_or(0.0) * 100.0).round() as u64 }

struct Trade { tid: i64, ts: i64, entry: f64, long: bool, risk: f64 }

const TIMEOUT_MS: i64 = 24 * 3600 * 1000;

/// Reconstruye el día y captura features de libro cuando el mid cruza el entry.
fn process_day(zip_path: &Path, trades: &[Trade]) -> Vec<(i64, f64, f64, f64, f64)> {
    let file = match File::open(zip_path) { Ok(f) => f, Err(_) => return vec![] };
    let mut ar = match zip::ZipArchive::new(file) { Ok(a) => a, Err(_) => return vec![] };
    if ar.is_empty() { return vec![]; }
    let inner = match ar.by_index(0) { Ok(i) => i, Err(_) => return vec![] };
    let reader = BufReader::with_capacity(1 << 20, inner);

    let mut ob = OrderBook::new();
    let mut pend: Vec<&Trade> = trades.iter().collect();
    pend.sort_by_key(|t| t.ts);
    let (mut pi, mut active, mut out) = (0usize, Vec::<&Trade>::new(), Vec::new());

    for line in reader.lines() {
        let line = match line { Ok(l) => l, Err(_) => continue };
        if line.is_empty() { continue; }
        let msg: serde_json::Value = match serde_json::from_str(&line) { Ok(v) => v, Err(_) => continue };
        let typ = msg["type"].as_str().unwrap_or("");
        if typ != "snapshot" && typ != "delta" { continue; }
        let ts = msg["ts"].as_i64().unwrap_or(0);
        ob.apply(&msg["data"], typ == "snapshot");
        while pi < pend.len() && ts >= pend[pi].ts { active.push(pend[pi]); pi += 1; }
        if active.is_empty() { continue; }
        let (bb, ba) = (ob.best_bid(), ob.best_ask());
        if bb <= 0.0 || ba <= 0.0 || ba <= bb { continue; }
        let mid = (bb + ba) / 2.0;
        let mut still = Vec::new();
        for t in active.drain(..) {
            let hit = if t.long { mid <= t.entry } else { mid >= t.entry };
            if hit {
                let (e, w) = (t.entry, 3.0 * t.risk);
                let (liq_up, wup_p, wup_s) = ob.range_liq(false, e, e + w);
                let (liq_dn, wdn_p, wdn_s) = ob.range_liq(true, e - w, e);
                if liq_up > 0.0 && liq_dn > 0.0 {
                    let (liq_t, liq_s, wp, ws) = if t.long {
                        (liq_up, liq_dn, wup_p, wup_s)
                    } else {
                        (liq_dn, liq_up, wdn_p, wdn_s)
                    };
                    let ratio = liq_t / (liq_s + 1e-9);
                    let dist = (wp - e).abs() / t.risk;
                    let imb = (liq_t - liq_s) / (liq_t + liq_s);
                    out.push((t.tid, ratio, dist, ws, imb));
                }
            } else if ts - t.ts < TIMEOUT_MS {
                still.push(t);
            }
        }
        active = still;
        if pi >= pend.len() && active.is_empty() { break; }
    }
    out
}

fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let av = |k: &str| args.windows(2).find(|w| w[0] == k).map(|w| w[1].clone());
    let ob_dir = PathBuf::from(av("--ob-dir").expect("--ob-dir requerido"));
    let trades_csv = PathBuf::from(av("--trades").expect("--trades requerido"));
    let out_csv = PathBuf::from(av("--out").expect("--out requerido"));

    // leer trades, agrupar por día
    let txt = fs::read_to_string(&trades_csv)?;
    let mut by_day: HashMap<String, Vec<Trade>> = HashMap::new();
    for (li, line) in txt.lines().enumerate() {
        if li == 0 { continue; }
        let f: Vec<&str> = line.split(',').collect();
        if f.len() < 5 { continue; }
        let ts: i64 = f[1].parse().unwrap_or(0);
        let risk: f64 = f[4].parse().unwrap_or(0.0);
        if risk <= 0.0 { continue; }
        let day = Utc.timestamp_millis_opt(ts).single().unwrap().format("%Y-%m-%d").to_string();
        by_day.entry(day).or_default().push(Trade {
            tid: f[0].parse().unwrap_or(-1), ts, entry: f[2].parse().unwrap_or(0.0),
            long: f[3] == "long", risk,
        });
    }

    // mapear día → zip
    let zips: HashMap<String, PathBuf> = fs::read_dir(&ob_dir)?
        .filter_map(|e| e.ok()).map(|e| e.path())
        .filter_map(|p| {
            let n = p.file_name()?.to_string_lossy().to_string();
            if n.len() >= 10 { Some((n[..10].to_string(), p)) } else { None }
        }).collect();
    let days: Vec<(String, Vec<Trade>)> = by_day.into_iter().filter(|(d, _)| zips.contains_key(d)).collect();
    println!("días con libro: {} | trades totales: {}", days.len(),
             days.iter().map(|(_, t)| t.len()).sum::<usize>());

    let results: Vec<(i64, f64, f64, f64, f64)> = days.par_iter().flat_map(|(d, trs)| {
        let r = process_day(&zips[d], trs);
        println!("  {} -> {}/{} fills", d, r.len(), trs.len());
        r
    }).collect();

    let mut out = File::create(&out_csv)?;
    writeln!(out, "tid,liq_ratio,wall_dist,wall_sz,book_imb")?;
    for (tid, ratio, dist, sz, imb) in &results {
        writeln!(out, "{},{:.4},{:.4},{:.4},{:.4}", tid, ratio, dist, sz, imb)?;
    }
    println!("OK: {} trades con features de libro -> {}", results.len(), out_csv.display());
    Ok(())
}
