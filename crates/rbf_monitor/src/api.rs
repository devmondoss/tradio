use crate::data::{Bar, RbfSignal, Timeframe};

const BINANCE_FAPI: &str = "https://fapi.binance.com";

pub async fn fetch_bars(tf: Timeframe) -> Vec<Bar> {
    let symbol = std::env::var("SYMBOL").unwrap_or_else(|_| "BTCUSDT".into());
    let url = format!(
        "{BINANCE_FAPI}/fapi/v1/klines\
         ?symbol={symbol}&interval={}&limit=1500",
        tf.interval()
    );

    let client = reqwest::Client::new();
    match client.get(&url).send().await {
        Ok(r) if r.status().is_success() => {
            let raw: Vec<serde_json::Value> = match r.json().await {
                Ok(v) => v,
                Err(e) => {
                    eprintln!("[api] parse error: {e}");
                    return vec![];
                }
            };
            let bars: Vec<Bar> = raw
                .into_iter()
                .filter_map(|entry| {
                    let a = entry.as_array()?;
                    Some(Bar {
                        ts_ms: a[0].as_i64()?,
                        open: a[1].as_str()?.parse().ok()?,
                        high: a[2].as_str()?.parse().ok()?,
                        low: a[3].as_str()?.parse().ok()?,
                        close: a[4].as_str()?.parse().ok()?,
                        live: false,
                    })
                })
                .collect();
            eprintln!(
                "[api] {} barras {} desde Binance",
                bars.len(),
                tf.interval()
            );
            bars
        }
        Ok(r) => {
            eprintln!("[api] binance status {}", r.status());
            vec![]
        }
        Err(e) => {
            eprintln!("[api] binance error: {e}");
            vec![]
        }
    }
}

pub async fn fetch_signals() -> Vec<RbfSignal> {
    let base = std::env::var("SUPABASE_URL").unwrap_or_default();
    let key = std::env::var("SUPABASE_KEY").unwrap_or_default();
    if base.is_empty() {
        return vec![];
    }

    let mut headers = reqwest::header::HeaderMap::new();
    if let (Ok(k), Ok(auth)) = (key.parse(), format!("Bearer {key}").parse()) {
        headers.insert("apikey", k);
        headers.insert("Authorization", auth);
    }

    let url = format!(
        "{base}/rest/v1/rbf_signals\
         ?select=*&order=timestamp_ms.desc&limit=200"
    );
    let client = reqwest::Client::new();
    match client.get(&url).headers(headers).send().await {
        Ok(r) => r.json::<Vec<RbfSignal>>().await.unwrap_or_default(),
        Err(e) => {
            eprintln!("[api] signals: {e}");
            vec![]
        }
    }
}
