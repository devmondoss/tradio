use data::strategy::detectors::mtf_spot_detector::{MtfSpotBarContext, MtfSpotState};
use serde::Serialize;
use std::{
    env,
    fs::File,
    io::{BufRead, BufReader},
};

#[derive(Debug, Serialize)]
struct ParityTrade {
    ts_ms: i64,
    direction: String,
    strategy: String,
    session: String,
    level: String,
    entry: f64,
    stop: f64,
    target: f64,
    exit_price: f64,
    reason: String,
    gross_r: f64,
    result_r: f64,
    fee_r: f64,
    duration_bars: usize,
}

fn round3(v: f64) -> f64 {
    (v * 1_000.0).round() / 1_000.0
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = env::args().skip(1);
    let mode = args.next().unwrap_or_else(|| "both".to_string());
    let path = args
        .next()
        .ok_or("usage: mtf_spot_parity <shorts|longs|both> <contexts.ndjson>")?;

    let allow_shorts = matches!(mode.as_str(), "shorts" | "both");
    let allow_longs = matches!(mode.as_str(), "longs" | "both");
    if !allow_shorts && !allow_longs {
        return Err(format!("unknown mode: {mode}").into());
    }

    let file = File::open(path)?;
    let reader = BufReader::new(file);
    let mut state = MtfSpotState::new("BTCUSDT");
    let mut trades = Vec::new();

    for line in reader.lines() {
        let line = line?;
        if line.trim().is_empty() {
            continue;
        }
        let ctx: MtfSpotBarContext = serde_json::from_str(&line)?;
        for event in state.on_bar_close(&ctx, allow_shorts, allow_longs) {
            if !event.is_open {
                trades.push(ParityTrade {
                    ts_ms: event.signal.ts_ms,
                    direction: event.signal.direction.as_str().to_string(),
                    strategy: event.signal.strategy,
                    session: event.signal.session,
                    level: event.signal.level,
                    entry: round3(event.signal.entry),
                    stop: round3(event.signal.stop),
                    target: round3(event.signal.target),
                    exit_price: round3(event.exit_price.unwrap_or(0.0)),
                    reason: event.reason.unwrap_or_default(),
                    gross_r: round3(event.gross_r.unwrap_or(0.0)),
                    result_r: round3(event.result_r.unwrap_or(0.0)),
                    fee_r: round3(event.fee_r.unwrap_or(0.0)),
                    duration_bars: event.duration_bars.unwrap_or(0),
                });
            }
        }
    }

    println!("{}", serde_json::to_string(&trades)?);
    Ok(())
}
