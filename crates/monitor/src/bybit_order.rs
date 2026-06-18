use hmac::{Hmac, Mac};
use reqwest::Client;
use serde::Deserialize;
use sha2::Sha256;
use std::time::{SystemTime, UNIX_EPOCH};

type HmacSha256 = Hmac<Sha256>;

const LIVE_BASE: &str = "https://api.bybit.com";
const TESTNET_BASE: &str = "https://api-testnet.bybit.com";
const RECV_WINDOW: u64 = 5000;

// ── Error ────────────────────────────────────────────────────────────────────

#[derive(Debug)]
pub enum OrderError {
    Http(reqwest::Error),
    Api { code: i32, msg: String },
    Parse(String),
}

impl std::fmt::Display for OrderError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Http(e) => write!(f, "HTTP: {e}"),
            Self::Api { code, msg } => write!(f, "Bybit API {code}: {msg}"),
            Self::Parse(s) => write!(f, "parse: {s}"),
        }
    }
}

impl From<reqwest::Error> for OrderError {
    fn from(e: reqwest::Error) -> Self {
        Self::Http(e)
    }
}

// ── Response wrappers ────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct BybitResp<T> {
    #[serde(rename = "retCode")]
    ret_code: i32,
    #[serde(rename = "retMsg")]
    ret_msg: String,
    result: Option<T>,
}

#[derive(Deserialize)]
struct PlaceResult {
    #[serde(rename = "orderId")]
    order_id: String,
}

#[derive(Deserialize)]
struct OrderListResult {
    list: Vec<OrderDetail>,
}

#[derive(Deserialize)]
struct OrderDetail {
    #[serde(rename = "orderId")]
    order_id: String,
    #[serde(rename = "orderStatus")]
    order_status: String,
    #[serde(rename = "avgPrice", default)]
    avg_price: String,
    #[serde(rename = "cumExecQty", default)]
    cum_exec_qty: String,
}

#[derive(Deserialize)]
struct WalletResult {
    list: Vec<WalletAccount>,
}

#[derive(Deserialize)]
struct WalletAccount {
    coin: Vec<CoinBalance>,
}

#[derive(Deserialize)]
struct CoinBalance {
    coin: String,
    #[serde(rename = "walletBalance")]
    wallet_balance: String,
}

// ── Public types ─────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct FillResult {
    pub order_id: String,
    pub status: String,
    pub avg_price: Option<f64>,
    pub filled_qty: f64,
}

// ── Client ───────────────────────────────────────────────────────────────────

#[derive(Clone)]
pub struct BybitOrderClient {
    api_key: String,
    api_secret: String,
    base_url: &'static str,
    client: Client,
}

impl BybitOrderClient {
    pub fn new(api_key: String, api_secret: String, testnet: bool) -> Self {
        Self {
            api_key,
            api_secret,
            base_url: if testnet { TESTNET_BASE } else { LIVE_BASE },
            client: Client::new(),
        }
    }

    fn now_ms() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_millis() as u64
    }

    fn sign(&self, payload: &str, ts: u64) -> String {
        let pre = format!("{}{}{}{}", ts, &self.api_key, RECV_WINDOW, payload);
        let mut mac =
            HmacSha256::new_from_slice(self.api_secret.as_bytes()).expect("HMAC init");
        mac.update(pre.as_bytes());
        hex::encode(mac.finalize().into_bytes())
    }

    async fn post<T: for<'de> Deserialize<'de>>(
        &self,
        path: &str,
        body: serde_json::Value,
    ) -> Result<T, OrderError> {
        let ts = Self::now_ms();
        let body_str = body.to_string();
        let sig = self.sign(&body_str, ts);
        let url = format!("{}{}", self.base_url, path);

        let resp = self
            .client
            .post(&url)
            .header("X-BAPI-API-KEY", &self.api_key)
            .header("X-BAPI-SIGN", &sig)
            .header("X-BAPI-SIGN-BY", "2")
            .header("X-BAPI-TIMESTAMP", ts.to_string())
            .header("X-BAPI-RECV-WINDOW", RECV_WINDOW.to_string())
            .header("Content-Type", "application/json")
            .body(body_str)
            .send()
            .await?;

        let wrapper: BybitResp<T> = resp.json().await?;
        if wrapper.ret_code != 0 {
            return Err(OrderError::Api {
                code: wrapper.ret_code,
                msg: wrapper.ret_msg,
            });
        }
        wrapper
            .result
            .ok_or_else(|| OrderError::Parse("empty result".into()))
    }

    async fn get<T: for<'de> Deserialize<'de>>(
        &self,
        path: &str,
        params: Vec<(&str, String)>,
    ) -> Result<T, OrderError> {
        let ts = Self::now_ms();
        let query = params
            .iter()
            .map(|(k, v)| format!("{}={}", k, v))
            .collect::<Vec<_>>()
            .join("&");
        let sig = self.sign(&query, ts);
        let url = format!("{}{}?{}", self.base_url, path, query);

        let resp = self
            .client
            .get(&url)
            .header("X-BAPI-API-KEY", &self.api_key)
            .header("X-BAPI-SIGN", &sig)
            .header("X-BAPI-SIGN-BY", "2")
            .header("X-BAPI-TIMESTAMP", ts.to_string())
            .header("X-BAPI-RECV-WINDOW", RECV_WINDOW.to_string())
            .send()
            .await?;

        let wrapper: BybitResp<T> = resp.json().await?;
        if wrapper.ret_code != 0 {
            return Err(OrderError::Api {
                code: wrapper.ret_code,
                msg: wrapper.ret_msg,
            });
        }
        wrapper
            .result
            .ok_or_else(|| OrderError::Parse("empty result".into()))
    }

    // ── Order operations ─────────────────────────────────────────────────────

    /// Place a market order. Returns the Bybit orderId.
    /// qty is in base currency (BTC for BTCUSDT).
    pub async fn place_market_order(
        &self,
        symbol: &str,
        side: &str, // "Buy" | "Sell"
        qty: f64,
    ) -> Result<String, OrderError> {
        let body = serde_json::json!({
            "category": "spot",
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": format!("{:.5}", qty),
            "timeInForce": "IOC"
        });
        let result: PlaceResult = self.post("/v5/order/create", body).await?;
        Ok(result.order_id)
    }

    /// Place a limit order (used for take-profit).
    /// Returns orderId.
    pub async fn place_limit_order(
        &self,
        symbol: &str,
        side: &str,
        qty: f64,
        price: f64,
    ) -> Result<String, OrderError> {
        let body = serde_json::json!({
            "category": "spot",
            "symbol": symbol,
            "side": side,
            "orderType": "Limit",
            "qty": format!("{:.5}", qty),
            "price": format!("{:.2}", price),
            "timeInForce": "GTC"
        });
        let result: PlaceResult = self.post("/v5/order/create", body).await?;
        Ok(result.order_id)
    }

    /// Place a stop-market order (trigger order for stop-loss).
    /// triggerPrice: price at which the market order fires.
    /// Returns orderId.
    pub async fn place_stop_market_order(
        &self,
        symbol: &str,
        side: &str,
        qty: f64,
        trigger_price: f64,
    ) -> Result<String, OrderError> {
        let body = serde_json::json!({
            "category": "spot",
            "symbol": symbol,
            "side": side,
            "orderType": "Market",
            "qty": format!("{:.5}", qty),
            "triggerPrice": format!("{:.2}", trigger_price),
            "triggerBy": "LastPrice",
            "timeInForce": "IOC",
            "isLeverage": 0
        });
        let result: PlaceResult = self.post("/v5/order/create", body).await?;
        Ok(result.order_id)
    }

    /// Cancel an open order.
    pub async fn cancel_order(&self, symbol: &str, order_id: &str) -> Result<(), OrderError> {
        let body = serde_json::json!({
            "category": "spot",
            "symbol": symbol,
            "orderId": order_id
        });
        // cancel returns orderId in result — we don't need it
        let _: serde_json::Value = self.post("/v5/order/cancel", body).await?;
        Ok(())
    }

    /// Get fill status of an order (open orders first, then history).
    pub async fn get_fill(&self, symbol: &str, order_id: &str) -> Result<FillResult, OrderError> {
        // Try open orders first
        if let Ok(r) = self
            .get_order_from_list("/v5/order/realtime", symbol, order_id)
            .await
        {
            return Ok(r);
        }
        // Fall back to history (order already closed)
        self.get_order_from_list("/v5/order/history", symbol, order_id)
            .await
    }

    async fn get_order_from_list(
        &self,
        path: &str,
        symbol: &str,
        order_id: &str,
    ) -> Result<FillResult, OrderError> {
        let result: OrderListResult = self
            .get(
                path,
                vec![
                    ("category", "spot".into()),
                    ("symbol", symbol.into()),
                    ("orderId", order_id.into()),
                ],
            )
            .await?;

        let d = result
            .list
            .into_iter()
            .next()
            .ok_or_else(|| OrderError::Parse("order not found".into()))?;

        Ok(FillResult {
            order_id: d.order_id,
            status: d.order_status,
            avg_price: d.avg_price.parse::<f64>().ok().filter(|&p| p > 0.0),
            filled_qty: d.cum_exec_qty.parse::<f64>().unwrap_or(0.0),
        })
    }

    /// Get USDT balance from Unified wallet.
    pub async fn get_usdt_balance(&self) -> Result<f64, OrderError> {
        let result: WalletResult = self
            .get(
                "/v5/account/wallet-balance",
                vec![
                    ("accountType", "UNIFIED".into()),
                    ("coin", "USDT".into()),
                ],
            )
            .await?;

        Ok(result
            .list
            .into_iter()
            .next()
            .and_then(|a| a.coin.into_iter().find(|c| c.coin == "USDT"))
            .and_then(|c| c.wallet_balance.parse::<f64>().ok())
            .unwrap_or(0.0))
    }
}
