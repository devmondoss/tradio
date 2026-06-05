use crate::data::{Bar, RbfSignal};

fn supabase_url() -> String {
    std::env::var("SUPABASE_URL").unwrap_or_default()
}
fn supabase_key() -> String {
    std::env::var("SUPABASE_KEY").unwrap_or_default()
}

fn headers() -> reqwest::header::HeaderMap {
    let key = supabase_key();
    let mut h = reqwest::header::HeaderMap::new();
    if let (Ok(apikey), Ok(auth)) = (key.parse(), format!("Bearer {key}").parse()) {
        h.insert("apikey", apikey);
        h.insert("Authorization", auth);
    }
    h
}

pub async fn fetch_bars() -> Vec<Bar> {
    let base = supabase_url();
    if base.is_empty() { return vec![]; }

    let client = reqwest::Client::new();
    let url = format!(
        "{base}/rest/v1/scalping_bars\
         ?select=ts_ms,open,high,low,close\
         &order=ts_ms.desc&limit=1500"
    );
    match client.get(&url).headers(headers()).send().await {
        Ok(r) => {
            #[derive(serde::Deserialize)]
            struct Row { ts_ms: i64, open: Option<f64>, high: Option<f64>, low: Option<f64>, close: f64 }
            if let Ok(rows) = r.json::<Vec<Row>>().await {
                let mut bars: Vec<Bar> = rows.into_iter()
                    .filter_map(|r| Some(Bar {
                        ts_ms: r.ts_ms,
                        open:  r.open?,
                        high:  r.high?,
                        low:   r.low?,
                        close: r.close,
                        live:  false,
                    }))
                    .collect();
                bars.sort_by_key(|b| b.ts_ms);
                bars
            } else { vec![] }
        }
        Err(e) => { eprintln!("[api] fetch_bars: {e}"); vec![] }
    }
}

pub async fn fetch_signals() -> Vec<RbfSignal> {
    let base = supabase_url();
    if base.is_empty() { return vec![]; }

    let client = reqwest::Client::new();
    let url = format!(
        "{base}/rest/v1/rbf_signals\
         ?select=*&order=timestamp_ms.desc&limit=200"
    );
    match client.get(&url).headers(headers()).send().await {
        Ok(r) => r.json::<Vec<RbfSignal>>().await.unwrap_or_default(),
        Err(e) => { eprintln!("[api] fetch_signals: {e}"); vec![] }
    }
}
