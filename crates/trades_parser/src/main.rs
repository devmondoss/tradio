/// trades_parser — parsea trades tick de Bybit perp a barras M1 + FOOTPRINT.
/// Paralelo (rayon). Extrae en una pasada: OHLCV + delta + footprint (delta@precio)
/// + tickDirection (agresor) + trade intensity.
///
/// Uso:
///   trades_parser --trades-dir DIR --cache-dir DIR --out FILE --start YYYY-MM-DD --end YYYY-MM-DD [--bucket 10]
///
/// Archivos de entrada: BTCUSDT{YYYY-MM-DD}.csv.gz (cols: timestamp,symbol,side,size,price,tickDirection,...)
use std::collections::BTreeMap;
use std::fs::{self, File};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

use arrow_array::{Float64Array, Int32Array, Int64Array, RecordBatch};
use arrow_schema::{DataType, Field, Schema};
use chrono::NaiveDate;
use flate2::read::GzDecoder;
use parquet::arrow::ArrowWriter;
use parquet::file::properties::WriterProperties;
use rayon::prelude::*;

// ── Acumulador de barra ────────────────────────────────────────────────────────
struct Bar {
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    buy_vol: f64,
    sell_vol: f64,
    n_trades: i64,
    max_trade: f64,
    plus_ticks: i64,
    minus_ticks: i64,
    levels: BTreeMap<i64, (f64, f64)>, // price_bucket -> (buy, sell)
}

impl Bar {
    fn new(p: f64) -> Self {
        Bar {
            open: p, high: p, low: p, close: p,
            buy_vol: 0.0, sell_vol: 0.0, n_trades: 0, max_trade: 0.0,
            plus_ticks: 0, minus_ticks: 0, levels: BTreeMap::new(),
        }
    }
}

// 27 campos por barra
type Row = (
    i64, f64, f64, f64, f64, f64, f64, f64, f64,  // ts, ohlc, vol, buy, sell, delta
    i64, f64, i64, i64,                           // n_trades, max_trade, plus, minus
    i64, i32, i32, i32, i32, i32,                 // fp_poc, n_levels, buy_imb, sell_imb, stack_buy, stack_sell
    i32, i32, f64, f64, f64,                      // unfin_hi, unfin_lo, delta_top, delta_bot, sell_dom
    i32, i32, i32,                                // absorb_sell, result_sell, absorb_buy
);

fn parse_day(path: &Path, bucket: f64) -> anyhow::Result<Vec<Row>> {
    let f = File::open(path)?;
    let reader = BufReader::with_capacity(1 << 20, GzDecoder::new(f));
    let mut bars: BTreeMap<i64, Bar> = BTreeMap::new();
    let bkt = bucket as i64;

    for (li, line) in reader.lines().enumerate() {
        let line = match line { Ok(l) => l, Err(_) => continue };
        if li == 0 || line.is_empty() { continue; } // header
        let mut it = line.split(',');
        let ts: f64 = match it.next().and_then(|s| s.parse().ok()) { Some(v) => v, None => continue };
        let _sym = it.next();
        let side = it.next().unwrap_or("");
        let size: f64 = it.next().and_then(|s| s.parse().ok()).unwrap_or(0.0);
        let price: f64 = it.next().and_then(|s| s.parse().ok()).unwrap_or(0.0);
        let tickdir = it.next().unwrap_or("");
        if price <= 0.0 { continue; }

        let ts_min = ((ts * 1000.0) as i64) / 60_000 * 60_000;
        let is_buy = side == "Buy";
        let b = bars.entry(ts_min).or_insert_with(|| Bar::new(price));
        b.high = b.high.max(price);
        b.low = b.low.min(price);
        b.close = price;
        b.n_trades += 1;
        b.max_trade = b.max_trade.max(size);
        if is_buy { b.buy_vol += size; } else { b.sell_vol += size; }
        if tickdir.starts_with("Plus") || tickdir == "ZeroPlusTick" { b.plus_ticks += 1; }
        else if tickdir.starts_with("Minus") || tickdir == "ZeroMinusTick" { b.minus_ticks += 1; }
        let pb = (price / bucket).floor() as i64 * bkt;
        let e = b.levels.entry(pb).or_insert((0.0, 0.0));
        if is_buy { e.0 += size; } else { e.1 += size; }
    }

    let mut rows = Vec::with_capacity(bars.len());
    for (ts, b) in bars {
        let prices: Vec<i64> = b.levels.keys().cloned().collect();
        let buys: Vec<f64> = b.levels.values().map(|v| v.0).collect();
        let sells: Vec<f64> = b.levels.values().map(|v| v.1).collect();
        let n = prices.len();
        let vols: Vec<f64> = (0..n).map(|i| buys[i] + sells[i]).collect();
        let deltas: Vec<f64> = (0..n).map(|i| buys[i] - sells[i]).collect();

        let poc = prices[vols.iter().enumerate().max_by(|a, b| a.1.partial_cmp(b.1).unwrap()).map(|(i, _)| i).unwrap_or(0)];
        let buy_imb = (1..n).filter(|&i| buys[i] >= 3.0 * (sells[i - 1] + 1e-9)).count() as i32;
        let sell_imb = (0..n.saturating_sub(1)).filter(|&i| sells[i] >= 3.0 * (buys[i + 1] + 1e-9)).count() as i32;
        let (mut mxb, mut mxs, mut cb, mut cs) = (0i32, 0i32, 0i32, 0i32);
        for &d in &deltas {
            if d > 0.0 { cb += 1; cs = 0; } else if d < 0.0 { cs += 1; cb = 0; } else { cb = 0; cs = 0; }
            mxb = mxb.max(cb); mxs = mxs.max(cs);
        }
        let tot_sell: f64 = sells.iter().sum();
        let tot_buy: f64 = buys.iter().sum();
        let tot = tot_sell + tot_buy + 1e-9;
        let sell_dom = tot_sell / tot;
        let buy_dom = tot_buy / tot;
        let absorb_sell = (sell_dom > 0.60 && b.close >= b.open) as i32;
        let result_sell = (sell_dom > 0.60 && b.close < b.open) as i32;
        let absorb_buy = (buy_dom > 0.60 && b.close <= b.open) as i32;
        let unfin_hi = (buys[n - 1] > 0.0 && sells[n - 1] > 0.0) as i32;
        let unfin_lo = (buys[0] > 0.0 && sells[0] > 0.0) as i32;

        rows.push((
            ts, b.open, b.high, b.low, b.close, b.buy_vol + b.sell_vol, b.buy_vol, b.sell_vol, b.buy_vol - b.sell_vol,
            b.n_trades, b.max_trade, b.plus_ticks, b.minus_ticks,
            poc, n as i32, buy_imb, sell_imb, mxb, mxs,
            unfin_hi, unfin_lo, deltas[n - 1], deltas[0], sell_dom,
            absorb_sell, result_sell, absorb_buy,
        ));
    }
    Ok(rows)
}

fn schema() -> Arc<Schema> {
    let f64f = |n: &str| Field::new(n, DataType::Float64, false);
    let i64f = |n: &str| Field::new(n, DataType::Int64, false);
    let i32f = |n: &str| Field::new(n, DataType::Int32, false);
    Arc::new(Schema::new(vec![
        i64f("ts_ms"), f64f("open"), f64f("high"), f64f("low"), f64f("close"),
        f64f("volume"), f64f("buy_vol"), f64f("sell_vol"), f64f("delta"),
        i64f("n_trades"), f64f("max_trade"), i64f("plus_ticks"), i64f("minus_ticks"),
        i64f("fp_poc"), i32f("fp_n_levels"), i32f("fp_buy_imb"), i32f("fp_sell_imb"),
        i32f("fp_stack_buy"), i32f("fp_stack_sell"), i32f("fp_unfinished_hi"), i32f("fp_unfinished_lo"),
        f64f("fp_delta_top"), f64f("fp_delta_bot"), f64f("fp_sell_dom"),
        i32f("fp_absorb_sell"), i32f("fp_result_sell"), i32f("fp_absorb_buy"),
    ]))
}

fn write_parquet(path: &Path, rows: &[Row]) -> anyhow::Result<()> {
    if rows.is_empty() { return Ok(()); }
    let s = schema();
    macro_rules! f64c { ($i:tt) => { Arc::new(Float64Array::from_iter_values(rows.iter().map(|r| r.$i))) } }
    macro_rules! i64c { ($i:tt) => { Arc::new(Int64Array::from_iter_values(rows.iter().map(|r| r.$i))) } }
    macro_rules! i32c { ($i:tt) => { Arc::new(Int32Array::from_iter_values(rows.iter().map(|r| r.$i))) } }
    let batch = RecordBatch::try_new(s.clone(), vec![
        i64c!(0), f64c!(1), f64c!(2), f64c!(3), f64c!(4), f64c!(5), f64c!(6), f64c!(7), f64c!(8),
        i64c!(9), f64c!(10), i64c!(11), i64c!(12),
        i64c!(13), i32c!(14), i32c!(15), i32c!(16), i32c!(17), i32c!(18), i32c!(19), i32c!(20),
        f64c!(21), f64c!(22), f64c!(23), i32c!(24), i32c!(25), i32c!(26),
    ])?;
    let file = File::create(path)?;
    let mut w = ArrowWriter::try_new(file, s, Some(WriterProperties::builder().build()))?;
    w.write(&batch)?;
    w.close()?;
    Ok(())
}

fn arg(args: &[String], k: &str) -> Option<String> {
    args.windows(2).find(|w| w[0] == k).map(|w| w[1].clone())
}

fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let dir = PathBuf::from(arg(&args, "--trades-dir").expect("--trades-dir requerido"));
    let cache = PathBuf::from(arg(&args, "--cache-dir").expect("--cache-dir requerido"));
    let out = PathBuf::from(arg(&args, "--out").unwrap_or_else(|| "m1_footprint.parquet".into()));
    let start = arg(&args, "--start").expect("--start requerido");
    let end = arg(&args, "--end").expect("--end requerido");
    let bucket: f64 = arg(&args, "--bucket").and_then(|s| s.parse().ok()).unwrap_or(10.0);
    let symbol = arg(&args, "--symbol").unwrap_or_else(|| "BTCUSDT".into());
    let slen = symbol.len();
    let start_d = NaiveDate::parse_from_str(&start, "%Y-%m-%d")?;
    let end_d = NaiveDate::parse_from_str(&end, "%Y-%m-%d")?;
    fs::create_dir_all(&cache)?;

    // archivos {symbol}{date}.csv.gz en rango, no cacheados
    let mut zips: Vec<PathBuf> = fs::read_dir(&dir)?
        .filter_map(|e| e.ok()).map(|e| e.path())
        .filter(|p| {
            let n = p.file_name().unwrap_or_default().to_string_lossy();
            if !n.starts_with(symbol.as_str()) || !n.ends_with(".csv.gz") { return false; }
            if n.len() < slen + 10 { return false; }
            match NaiveDate::parse_from_str(&n[slen..slen + 10], "%Y-%m-%d") {
                Ok(d) => d >= start_d && d <= end_d, Err(_) => false,
            }
        }).collect();
    zips.sort();
    let pending: Vec<PathBuf> = zips.into_iter()
        .filter(|p| {
            let d = &p.file_name().unwrap().to_string_lossy()[slen..slen + 10].to_string();
            !cache.join(format!("{}.parquet", d)).exists()
        }).collect();

    println!("Footprint Rust: {} dias pendientes ({} - {})", pending.len(), start, end);
    let t0 = Instant::now();
    let done = Arc::new(AtomicUsize::new(0));
    let total = pending.len();
    pending.par_iter().for_each(|zip| {
        let d = zip.file_name().unwrap().to_string_lossy()[slen..slen + 10].to_string();
        match parse_day(zip, bucket) {
            Ok(rows) => {
                let _ = write_parquet(&cache.join(format!("{}.parquet", d)), &rows);
                let n = done.fetch_add(1, Ordering::Relaxed) + 1;
                if n % 20 == 0 || n <= 3 {
                    let el = t0.elapsed().as_secs_f64();
                    println!("  [{}/{}] {} -> {} barras | {:.1} dias/s ETA {:.0}s", n, total, d, rows.len(), n as f64 / el, (total - n) as f64 / (n as f64 / el));
                }
            }
            Err(e) => eprintln!("  ERROR {}: {}", d, e),
        }
    });
    println!("Parseo: {} dias en {:.1}s", done.load(Ordering::Relaxed), t0.elapsed().as_secs_f64());

    // combinar caches -> out (lo lee Python para mergear)
    let mut files: Vec<PathBuf> = fs::read_dir(&cache)?.filter_map(|e| e.ok()).map(|e| e.path())
        .filter(|p| p.extension().map(|e| e == "parquet").unwrap_or(false)).collect();
    files.sort();
    println!("Caches totales: {} (combinar en Python desde {})", files.len(), cache.display());
    let _ = out; // el merge final lo hace Python leyendo el cache-dir
    Ok(())
}
