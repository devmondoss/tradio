/// Fire-and-forget Slack webhook alerts for live trading events.
/// Set SLACK_WEBHOOK_URL in Railway to enable. No-op if not set.

use reqwest::Client;

static WEBHOOK: std::sync::OnceLock<Option<String>> = std::sync::OnceLock::new();

fn webhook_url() -> Option<&'static str> {
    WEBHOOK
        .get_or_init(|| std::env::var("SLACK_WEBHOOK_URL").ok().filter(|v| !v.is_empty()))
        .as_deref()
}

async fn send(text: &str) {
    let url = match webhook_url() {
        Some(u) => u,
        None => return,
    };
    let body = serde_json::json!({ "text": text });
    let _ = Client::new().post(url).json(&body).send().await;
}

// ── Public alert functions ────────────────────────────────────────────────────

pub async fn trade_opened(symbol: &str, direction: &str, entry: f64, stop: f64, target: f64, order_id: &str) {
    let msg = format!(
        "🟢 *LIVE OPEN* `{symbol}` {direction}\nEntry: `{entry:.2}` | Stop: `{stop:.2}` | Target: `{target:.2}`\nOrder: `{order_id}`"
    );
    send(&msg).await;
}

pub async fn trade_closed(symbol: &str, direction: &str, result_r: f64, reason: &str) {
    let emoji = if result_r >= 0.0 { "✅" } else { "❌" };
    let msg = format!(
        "{emoji} *LIVE CLOSED* `{symbol}` {direction}\nResult: `{result_r:+.3}R` | Reason: `{reason}`"
    );
    send(&msg).await;
}

pub async fn kill_switch_triggered(reason: &str) {
    let msg = format!("🚨 *KILL SWITCH* activated: {reason}\nNo new trades will open until manual reset.");
    send(&msg).await;
}

pub async fn order_error(symbol: &str, action: &str, error: &str) {
    let msg = format!("⚠️ *ORDER ERROR* `{symbol}` {action}\n`{error}`");
    send(&msg).await;
}
