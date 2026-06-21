/// ob_parser — parseo incremental de orderbook Bybit SPOT L2 a barras M1
///
/// Uso:
///   cargo run -p ob-parser                 # procesa pendientes
///   cargo run -p ob-parser -- --rebuild    # borra cache y reprocesa todo
///   cargo run -p ob-parser -- --date 2025-07-01  # solo ese dia
///
/// Rango: 2025-06-15 → 2026-06-15
/// Cache: data/bybit-spot/processed/ob_cache/YYYY-MM-DD.parquet
/// Salida: data/bybit-spot/processed/m1_obi.parquet
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
use parquet::arrow::ArrowWriter;
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use parquet::file::properties::WriterProperties;
use rayon::prelude::*;

const START: &str = "2025-06-15";
const END:   &str = "2026-06-15";
const NEAR:  usize = 5;

// ── Order Book ───────────────────────────────────────────────────────────────

struct OrderBook {
    bids: BTreeMap<u64, f64>, // key = price * 100 (u64 para orden exacto)
    asks: BTreeMap<u64, f64>,
}

impl OrderBook {
    fn new() -> Self { Self { bids: BTreeMap::new(), asks: BTreeMap::new() } }

    fn apply(&mut self, data: &serde_json::Value, snapshot: bool) {
        if snapshot { self.bids.clear(); self.asks.clear(); }

        if let Some(arr) = data["b"].as_array() {
            for e in arr {
                let k = price_key(e[0].as_str().unwrap_or("0"));
                let q: f64 = e[1].as_str().unwrap_or("0").parse().unwrap_or(0.0);
                if q == 0.0 { self.bids.remove(&k); } else { self.bids.insert(k, q); }
            }
        }
        if let Some(arr) = data["a"].as_array() {
            for e in arr {
                let k = price_key(e[0].as_str().unwrap_or("0"));
                let q: f64 = e[1].as_str().unwrap_or("0").parse().unwrap_or(0.0);
                if q == 0.0 { self.asks.remove(&k); } else { self.asks.insert(k, q); }
            }
        }
    }

    fn best_bid(&self) -> f64 { self.bids.keys().next_back().map_or(0.0, |k| *k as f64 / 100.0) }
    fn best_ask(&self) -> f64 { self.asks.keys().next().map_or(0.0, |k| *k as f64 / 100.0) }

    fn obi(&self, levels: usize) -> f64 {
        let b: f64 = self.bids.values().rev().take(levels).sum();
        let a: f64 = self.asks.values().take(levels).sum();
        let t = b + a;
        if t > 0.0 { (b - a) / t } else { 0.0 }
    }

    /// near_ask, near_bid, max_ask, max_bid — valores continuos.
    /// Los thresholds thin/wall se calculan con percentiles rolling en Python.
    fn near_liquidity(&self, near: usize) -> (f64, f64, f64, f64) {
        let top_b: Vec<f64> = self.bids.values().rev().take(near).cloned().collect();
        let top_a: Vec<f64> = self.asks.values().take(near).cloned().collect();

        if top_b.len() < near || top_a.len() < near {
            return (0.0, 0.0, 0.0, 0.0);
        }

        let near_bid: f64 = top_b.iter().sum();
        let near_ask: f64 = top_a.iter().sum();
        let max_bid = top_b.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
        let max_ask = top_a.iter().cloned().fold(f64::NEG_INFINITY, f64::max);

        (near_ask, near_bid, max_ask, max_bid)
    }
}

#[inline]
fn price_key(s: &str) -> u64 {
    (s.parse::<f64>().unwrap_or(0.0) * 100.0).round() as u64
}

// ── M1 Bar ───────────────────────────────────────────────────────────────────

#[derive(Clone)]
struct M1Bar {
    ts_ms:       i64,
    obi5_mean:   f64,
    obi10_mean:  f64,
    obi20_mean:  f64,
    obi10_min:   f64,
    obi10_max:   f64,
    spread_mean: f64,
    mid_open:    f64,
    mid_close:   f64,
    n_snapshots: i32,
    // Near-liquidity (near=5 niveles)
    near5_ask:   f64,
    near5_bid:   f64,
    max_ask5:    f64,
    max_bid5:    f64,
}

// ── Snapshot tuple fields ─────────────────────────────────────────────────────
// (ts_ms, obi5, obi10, obi20, spread_bps, mid, near5_ask, near5_bid, max_ask5, max_bid5)
type Snap = (i64, f64, f64, f64, f64, f64, f64, f64, f64, f64);

// ── Parse one ZIP ────────────────────────────────────────────────────────────

fn parse_day(zip_path: &Path) -> anyhow::Result<Vec<M1Bar>> {
    let file = File::open(zip_path)?;
    let mut archive = zip::ZipArchive::new(file)?;
    if archive.len() == 0 { return Ok(vec![]); }

    let inner = archive.by_index(0)?;
    let reader = BufReader::with_capacity(512 * 1024, inner);

    let mut ob = OrderBook::new();
    let mut snaps: Vec<Snap> = Vec::with_capacity(8_000);

    for line in reader.lines() {
        let line = match line { Ok(l) => l, Err(_) => continue };
        if line.is_empty() { continue; }

        let msg: serde_json::Value = match serde_json::from_str(&line) {
            Ok(v) => v, Err(_) => continue,
        };

        let typ = msg["type"].as_str().unwrap_or("");
        if typ != "snapshot" && typ != "delta" { continue; }

        let ts_ms = msg["ts"].as_i64().unwrap_or(0);
        ob.apply(&msg["data"], typ == "snapshot");

        let bb = ob.best_bid();
        let ba = ob.best_ask();
        if bb <= 0.0 || ba <= 0.0 || ba <= bb { continue; }

        let mid = (bb + ba) / 2.0;
        let (na, nb, ma, mb) = ob.near_liquidity(NEAR);

        snaps.push((
            ts_ms,
            ob.obi(5),
            ob.obi(10),
            ob.obi(20),
            (ba - bb) / mid * 10_000.0,
            mid,
            na, nb, ma, mb,
        ));
    }

    Ok(aggregate_m1(snaps))
}

fn aggregate_m1(mut snaps: Vec<Snap>) -> Vec<M1Bar> {
    if snaps.is_empty() { return vec![]; }
    snaps.sort_unstable_by_key(|s| s.0);

    let mut bars: Vec<M1Bar> = Vec::new();
    let mut i = 0;

    while i < snaps.len() {
        let min_ts = (snaps[i].0 / 60_000) * 60_000;
        let j = i + snaps[i..].partition_point(|s| (s.0 / 60_000) * 60_000 == min_ts);
        let g = &snaps[i..j];
        let n = g.len() as f64;

        bars.push(M1Bar {
            ts_ms:       min_ts,
            obi5_mean:   g.iter().map(|s| s.1).sum::<f64>() / n,
            obi10_mean:  g.iter().map(|s| s.2).sum::<f64>() / n,
            obi20_mean:  g.iter().map(|s| s.3).sum::<f64>() / n,
            obi10_min:   g.iter().map(|s| s.2).fold(f64::MAX,          f64::min),
            obi10_max:   g.iter().map(|s| s.2).fold(f64::NEG_INFINITY, f64::max),
            spread_mean: g.iter().map(|s| s.4).sum::<f64>() / n,
            mid_open:    g[0].5,
            mid_close:   g[j - i - 1].5,
            n_snapshots: g.len() as i32,
            near5_ask:   g.iter().map(|s| s.6).sum::<f64>() / n,
            near5_bid:   g.iter().map(|s| s.7).sum::<f64>() / n,
            max_ask5:    g.iter().map(|s| s.8).sum::<f64>() / n,
            max_bid5:    g.iter().map(|s| s.9).sum::<f64>() / n,
        });
        i = j;
    }
    bars
}

// ── Parquet I/O ──────────────────────────────────────────────────────────────

fn make_schema() -> Arc<Schema> {
    Arc::new(Schema::new(vec![
        Field::new("ts_ms",       DataType::Int64,   false),
        Field::new("obi5_mean",   DataType::Float64, false),
        Field::new("obi10_mean",  DataType::Float64, false),
        Field::new("obi20_mean",  DataType::Float64, false),
        Field::new("obi10_min",   DataType::Float64, false),
        Field::new("obi10_max",   DataType::Float64, false),
        Field::new("spread_mean", DataType::Float64, false),
        Field::new("mid_open",    DataType::Float64, false),
        Field::new("mid_close",   DataType::Float64, false),
        Field::new("n_snapshots", DataType::Int32,   false),
        Field::new("near5_ask",   DataType::Float64, false),
        Field::new("near5_bid",   DataType::Float64, false),
        Field::new("max_ask5",    DataType::Float64, false),
        Field::new("max_bid5",    DataType::Float64, false),
    ]))
}

fn write_parquet(path: &Path, bars: &[M1Bar]) -> anyhow::Result<()> {
    if bars.is_empty() { return Ok(()); }
    let schema = make_schema();
    let batch = RecordBatch::try_new(schema.clone(), vec![
        Arc::new(Int64Array::from_iter_values(bars.iter().map(|b| b.ts_ms))),
        Arc::new(Float64Array::from_iter_values(bars.iter().map(|b| b.obi5_mean))),
        Arc::new(Float64Array::from_iter_values(bars.iter().map(|b| b.obi10_mean))),
        Arc::new(Float64Array::from_iter_values(bars.iter().map(|b| b.obi20_mean))),
        Arc::new(Float64Array::from_iter_values(bars.iter().map(|b| b.obi10_min))),
        Arc::new(Float64Array::from_iter_values(bars.iter().map(|b| b.obi10_max))),
        Arc::new(Float64Array::from_iter_values(bars.iter().map(|b| b.spread_mean))),
        Arc::new(Float64Array::from_iter_values(bars.iter().map(|b| b.mid_open))),
        Arc::new(Float64Array::from_iter_values(bars.iter().map(|b| b.mid_close))),
        Arc::new(Int32Array::from_iter_values(bars.iter().map(|b| b.n_snapshots))),
        Arc::new(Float64Array::from_iter_values(bars.iter().map(|b| b.near5_ask))),
        Arc::new(Float64Array::from_iter_values(bars.iter().map(|b| b.near5_bid))),
        Arc::new(Float64Array::from_iter_values(bars.iter().map(|b| b.max_ask5))),
        Arc::new(Float64Array::from_iter_values(bars.iter().map(|b| b.max_bid5))),
    ])?;

    let file = File::create(path)?;
    let props = WriterProperties::builder().build();
    let mut writer = ArrowWriter::try_new(file, schema, Some(props))?;
    writer.write(&batch)?;
    writer.close()?;
    Ok(())
}

fn read_parquet(path: &Path) -> anyhow::Result<Vec<M1Bar>> {
    let file = File::open(path)?;
    let reader = ParquetRecordBatchReaderBuilder::try_new(file)?.build()?;
    let mut bars = Vec::new();

    for batch in reader {
        let batch = batch?;
        macro_rules! col_f64 {
            ($i:expr) => { batch.column($i).as_any().downcast_ref::<Float64Array>().unwrap() }
        }
        macro_rules! col_i64 {
            ($i:expr) => { batch.column($i).as_any().downcast_ref::<Int64Array>().unwrap() }
        }
        macro_rules! col_i32 {
            ($i:expr) => { batch.column($i).as_any().downcast_ref::<Int32Array>().unwrap() }
        }

        let ts   = col_i64!(0);
        let o5   = col_f64!(1);
        let o10  = col_f64!(2);
        let o20  = col_f64!(3);
        let omin = col_f64!(4);
        let omax = col_f64!(5);
        let sp   = col_f64!(6);
        let mo   = col_f64!(7);
        let mc   = col_f64!(8);
        let ns   = col_i32!(9);

        // near-liquidity cols: presentes solo si el cache fue generado con esta version
        let has_near = batch.num_columns() >= 14;
        let na = if has_near { Some(col_f64!(10)) } else { None };
        let nb = if has_near { Some(col_f64!(11)) } else { None };
        let ma = if has_near { Some(col_f64!(12)) } else { None };
        let mb = if has_near { Some(col_f64!(13)) } else { None };

        for i in 0..batch.num_rows() {
            bars.push(M1Bar {
                ts_ms:       ts.value(i),
                obi5_mean:   o5.value(i),
                obi10_mean:  o10.value(i),
                obi20_mean:  o20.value(i),
                obi10_min:   omin.value(i),
                obi10_max:   omax.value(i),
                spread_mean: sp.value(i),
                mid_open:    mo.value(i),
                mid_close:   mc.value(i),
                n_snapshots: ns.value(i),
                near5_ask:   na.map_or(0.0, |a| a.value(i)),
                near5_bid:   nb.map_or(0.0, |a| a.value(i)),
                max_ask5:    ma.map_or(0.0, |a| a.value(i)),
                max_bid5:    mb.map_or(0.0, |a| a.value(i)),
            });
        }
    }
    Ok(bars)
}

// ── Main ─────────────────────────────────────────────────────────────────────

fn arg_val(args: &[String], key: &str) -> Option<String> {
    args.windows(2).find(|w| w[0] == key).map(|w| w[1].clone())
}

fn main() -> anyhow::Result<()> {
    let args: Vec<String> = std::env::args().collect();
    let rebuild   = args.iter().any(|a| a == "--rebuild");
    let date_only = arg_val(&args, "--date");

    // Rutas y rango configurables (default = spot, retrocompatible).
    let base      = std::env::current_dir()?;
    let ob_dir    = arg_val(&args, "--ob-dir")
        .map(PathBuf::from)
        .unwrap_or_else(|| base.join("data/bybit-spot/orderbook"));
    let cache_dir = arg_val(&args, "--cache-dir")
        .map(PathBuf::from)
        .unwrap_or_else(|| base.join("data/bybit-spot/processed/ob_cache"));
    let out_file  = arg_val(&args, "--out")
        .map(PathBuf::from)
        .unwrap_or_else(|| base.join("data/bybit-spot/processed/m1_obi.parquet"));
    let start_s   = arg_val(&args, "--start").unwrap_or_else(|| START.to_string());
    let end_s     = arg_val(&args, "--end").unwrap_or_else(|| END.to_string());

    if rebuild && cache_dir.exists() {
        fs::remove_dir_all(&cache_dir)?;
        println!("Cache borrada.");
    }
    fs::create_dir_all(&cache_dir)?;

    if !ob_dir.exists() {
        anyhow::bail!("No se encontro el directorio de orderbook: {}", ob_dir.display());
    }

    let start_d = NaiveDate::parse_from_str(&start_s, "%Y-%m-%d")?;
    let end_d   = NaiveDate::parse_from_str(&end_s,   "%Y-%m-%d")?;

    let mut all_zips: Vec<PathBuf> = fs::read_dir(&ob_dir)?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| {
            let name = p.file_name().unwrap_or_default().to_string_lossy();
            if name.len() < 10 { return false; }
            if let Ok(d) = NaiveDate::parse_from_str(&name[..10], "%Y-%m-%d") {
                d >= start_d && d <= end_d
            } else { false }
        })
        .collect();
    all_zips.sort();

    if let Some(ref d) = date_only {
        all_zips.retain(|p| p.file_name().unwrap().to_string_lossy().starts_with(d.as_str()));
    }

    // Filtrar: si el cache existe pero NO tiene near-liquidity cols, lo descartamos
    let pending: Vec<PathBuf> = all_zips.iter()
        .filter(|p| {
            let date  = &p.file_name().unwrap().to_string_lossy()[..10];
            let cache = cache_dir.join(format!("{}.parquet", date));
            if !cache.exists() { return true; }
            // Verificar que el parquet tiene 14 columnas (con near-liquidity)
            if let Ok(f) = File::open(&cache) {
                if let Ok(r) = ParquetRecordBatchReaderBuilder::try_new(f) {
                    return r.schema().fields().len() < 14;
                }
            }
            false
        })
        .cloned()
        .collect();

    let cached = all_zips.len() - pending.len();
    println!("Dias en rango : {}  |  cacheados: {}  |  pendientes: {}",
             all_zips.len(), cached, pending.len());

    if !pending.is_empty() {
        let t0     = Instant::now();
        let done   = Arc::new(AtomicUsize::new(0));
        let errors = Arc::new(AtomicUsize::new(0));
        let total  = pending.len();

        pending.par_iter().for_each(|zip_path| {
            let name  = zip_path.file_name().unwrap().to_string_lossy();
            let date  = name[..10].to_string();
            let cache = cache_dir.join(format!("{}.parquet", date));

            match parse_day(zip_path) {
                Ok(bars) => {
                    let n_bars = bars.len();
                    if let Err(e) = write_parquet(&cache, &bars) {
                        eprintln!("  ERROR escribiendo {}: {}", date, e);
                        errors.fetch_add(1, Ordering::Relaxed);
                    } else {
                        let n = done.fetch_add(1, Ordering::Relaxed) + 1;
                        if n % 10 == 0 || n <= 5 {
                            let elapsed = t0.elapsed().as_secs_f64();
                            let rate = n as f64 / elapsed;
                            let eta  = (total - n) as f64 / rate;
                            println!("  [{:3}/{}] {} -> {} barras  |  {:.1} dias/s  ETA: {:.0}s",
                                     n, total, date, n_bars, rate, eta);
                        }
                    }
                }
                Err(e) => {
                    eprintln!("  ERROR {} : {}", date, e);
                    errors.fetch_add(1, Ordering::Relaxed);
                }
            }
        });

        let elapsed = t0.elapsed().as_secs_f64();
        let n_ok  = done.load(Ordering::Relaxed);
        let n_err = errors.load(Ordering::Relaxed);
        println!("\nParseo: {} ok, {} errores en {:.1}s ({:.1} dias/s)",
                 n_ok, n_err, elapsed, n_ok as f64 / elapsed);
    }

    // Combinar todos los archivos de cache
    let mut cache_files: Vec<PathBuf> = fs::read_dir(&cache_dir)?
        .filter_map(|e| e.ok())
        .map(|e| e.path())
        .filter(|p| p.extension().map(|e| e == "parquet").unwrap_or(false))
        .collect();
    cache_files.sort();

    if cache_files.is_empty() {
        anyhow::bail!("No hay datos en cache.");
    }

    print!("\nCombinando {} archivos de cache... ", cache_files.len());
    let mut all_bars: Vec<M1Bar> = Vec::new();
    for f in &cache_files {
        all_bars.extend(read_parquet(f)?);
    }
    all_bars.sort_unstable_by_key(|b| b.ts_ms);
    all_bars.dedup_by_key(|b| b.ts_ms);

    write_parquet(&out_file, &all_bars)?;

    let first = fmt_ts(all_bars[0].ts_ms);
    let last  = fmt_ts(all_bars[all_bars.len() - 1].ts_ms);
    println!("{} barras M1", all_bars.len());
    println!("Rango   : {}  ->  {}", first, last);
    println!("Guardado: {}  ({:.1} MB)",
             out_file.display(), out_file.metadata()?.len() as f64 / 1e6);

    Ok(())
}

fn fmt_ts(ms: i64) -> String {
    use chrono::{TimeZone, Utc};
    Utc.timestamp_millis_opt(ms)
        .single()
        .map(|dt| dt.format("%Y-%m-%d %H:%M:%S UTC").to_string())
        .unwrap_or_else(|| ms.to_string())
}
