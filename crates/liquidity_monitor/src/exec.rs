//! exec.rs — ejecución REAL de órdenes perps (Bybit linear), testnet o live.
//! Solo primitivas firmadas: PostOnly limit (entrada maker), market reduce-only (stop/exit),
//! cancel, get order, get position. category="linear". El loop de gestión vive en executor.rs.
//! NUNCA paniquea: todo devuelve Result; el caller decide.

use hmac::{Hmac, Mac};
use reqwest::Client;
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::Sha256;
use std::time::{SystemTime, UNIX_EPOCH};

type HmacSha256 = Hmac<Sha256>;
const LIVE_BASE: &str = "https://api.bybit.com";
const TESTNET_BASE: &str = "https://api-testnet.bybit.com";
const RECV_WINDOW: u64 = 5000;

#[derive(Debug)]
pub enum ExecError { Http(String), Api { code: i32, msg: String }, Parse(String) }
impl std::fmt::Display for ExecError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Http(e) => write!(f, "HTTP: {e}"),
            Self::Api { code, msg } => write!(f, "Bybit {code}: {msg}"),
            Self::Parse(s) => write!(f, "parse: {s}"),
        }
    }
}

#[derive(Deserialize)]
struct Resp<T> {
    #[serde(rename = "retCode")] ret_code: i32,
    #[serde(rename = "retMsg")]  ret_msg: String,
    result: Option<T>,
}

#[derive(Deserialize)]
struct PlaceResult { #[serde(rename = "orderId")] order_id: String }

#[derive(Deserialize)]
struct OrderListResult { list: Vec<OrderDetail> }
#[derive(Deserialize)]
struct OrderDetail {
    #[serde(rename = "orderStatus")] order_status: String,
    #[serde(rename = "avgPrice", default)] avg_price: String,
    #[serde(rename = "cumExecQty", default)] cum_exec_qty: String,
}

#[derive(Deserialize)]
struct PosListResult { list: Vec<PosDetail> }
#[derive(Deserialize)]
struct PosDetail {
    #[serde(default)] side: String,            // "Buy" | "Sell" | "" (flat)
    #[serde(default)] size: String,
    #[serde(rename = "avgPrice", default)] avg_price: String,
}

#[derive(Deserialize)]
struct ClosedPnlResult { list: Vec<ClosedPnlDetail> }
#[derive(Deserialize)]
struct ClosedPnlDetail {
    #[serde(rename = "closedPnl", default)] closed_pnl: String,
    #[serde(rename = "avgExitPrice", default)] avg_exit_price: String,
}

/// Estado de una orden consultada.
#[derive(Debug, Clone)]
pub struct OrderInfo { pub status: String, pub avg_price: f64, pub filled_qty: f64 }
/// Estado de la posición.
#[derive(Debug, Clone, Default)]
pub struct PosInfo { pub side: String, pub size: f64, pub avg_price: f64 }

#[derive(Clone)]
pub struct ExecClient {
    api_key: String,
    api_secret: String,
    base_url: &'static str,
    client: Client,
}

impl ExecClient {
    pub fn new(api_key: String, api_secret: String, testnet: bool) -> Self {
        Self { api_key, api_secret,
               base_url: if testnet { TESTNET_BASE } else { LIVE_BASE },
               client: Client::new() }
    }
    pub fn is_testnet(&self) -> bool { self.base_url == TESTNET_BASE }

    fn now_ms() -> u64 {
        SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_millis() as u64
    }
    fn sign(&self, payload: &str, ts: u64) -> String {
        let pre = format!("{}{}{}{}", ts, &self.api_key, RECV_WINDOW, payload);
        let mut mac = HmacSha256::new_from_slice(self.api_secret.as_bytes()).expect("HMAC init");
        mac.update(pre.as_bytes());
        hex::encode(mac.finalize().into_bytes())
    }

    async fn post<T: for<'de> Deserialize<'de>>(&self, path: &str, body: Value) -> Result<T, ExecError> {
        let ts = Self::now_ms();
        let body_str = body.to_string();
        let sig = self.sign(&body_str, ts);
        let resp = self.client.post(format!("{}{}", self.base_url, path))
            .header("X-BAPI-API-KEY", &self.api_key)
            .header("X-BAPI-SIGN", &sig)
            .header("X-BAPI-TIMESTAMP", ts.to_string())
            .header("X-BAPI-RECV-WINDOW", RECV_WINDOW.to_string())
            .header("Content-Type", "application/json")
            .body(body_str).send().await.map_err(|e| ExecError::Http(e.to_string()))?;
        let w: Resp<T> = resp.json().await.map_err(|e| ExecError::Http(e.to_string()))?;
        if w.ret_code != 0 { return Err(ExecError::Api { code: w.ret_code, msg: w.ret_msg }); }
        w.result.ok_or_else(|| ExecError::Parse("empty result".into()))
    }

    async fn get<T: for<'de> Deserialize<'de>>(&self, path: &str, params: Vec<(&str, String)>) -> Result<T, ExecError> {
        let ts = Self::now_ms();
        let query = params.iter().map(|(k, v)| format!("{}={}", k, v)).collect::<Vec<_>>().join("&");
        let sig = self.sign(&query, ts);
        let resp = self.client.get(format!("{}{}?{}", self.base_url, path, query))
            .header("X-BAPI-API-KEY", &self.api_key)
            .header("X-BAPI-SIGN", &sig)
            .header("X-BAPI-TIMESTAMP", ts.to_string())
            .header("X-BAPI-RECV-WINDOW", RECV_WINDOW.to_string())
            .send().await.map_err(|e| ExecError::Http(e.to_string()))?;
        let w: Resp<T> = resp.json().await.map_err(|e| ExecError::Http(e.to_string()))?;
        if w.ret_code != 0 { return Err(ExecError::Api { code: w.ret_code, msg: w.ret_msg }); }
        w.result.ok_or_else(|| ExecError::Parse("empty result".into()))
    }

    // ── Órdenes (category=linear) ─────────────────────────────────────────────

    /// Limit PostOnly (maker garantizado: si cruzaría, se rechaza). reduce_only para salidas.
    /// `link_id` = orderLinkId propio (idempotencia / tracking). qty y price ya formateados a step.
    pub async fn place_limit(&self, symbol: &str, side: &str, qty: &str, price: &str,
                             reduce_only: bool, link_id: &str) -> Result<String, ExecError> {
        let r: PlaceResult = self.post("/v5/order/create", json!({
            "category": "linear", "symbol": symbol, "side": side,
            "orderType": "Limit", "qty": qty, "price": price,
            "timeInForce": "PostOnly", "reduceOnly": reduce_only,
            "positionIdx": 0, "orderLinkId": link_id,
        })).await?;
        Ok(r.order_id)
    }

    /// Market IOC (para stop/exit). reduce_only=true cierra sin abrir nuevo.
    pub async fn place_market(&self, symbol: &str, side: &str, qty: &str,
                              reduce_only: bool, link_id: &str) -> Result<String, ExecError> {
        let r: PlaceResult = self.post("/v5/order/create", json!({
            "category": "linear", "symbol": symbol, "side": side,
            "orderType": "Market", "qty": qty, "timeInForce": "IOC",
            "reduceOnly": reduce_only, "positionIdx": 0, "orderLinkId": link_id,
        })).await?;
        Ok(r.order_id)
    }

    pub async fn cancel(&self, symbol: &str, order_id: &str) -> Result<(), ExecError> {
        let _: Value = self.post("/v5/order/cancel", json!({
            "category": "linear", "symbol": symbol, "orderId": order_id,
        })).await?;
        Ok(())
    }

    pub async fn cancel_all(&self, symbol: &str) -> Result<(), ExecError> {
        let _: Value = self.post("/v5/order/cancel-all", json!({
            "category": "linear", "symbol": symbol,
        })).await?;
        Ok(())
    }

    /// Fija stop-loss / take-profit / trailing-stop sobre la POSICIÓN (lado exchange).
    /// stop_loss y take_profit = precios absolutos; trailing = distancia en precio.
    pub async fn set_trading_stop(&self, symbol: &str, stop_loss: Option<&str>,
                                  take_profit: Option<&str>, trailing: Option<&str>) -> Result<(), ExecError> {
        let mut body = json!({ "category": "linear", "symbol": symbol, "positionIdx": 0 });
        if let Some(sl) = stop_loss   { body["stopLoss"]   = sl.into(); }
        if let Some(tp) = take_profit { body["takeProfit"] = tp.into(); }
        if let Some(t)  = trailing    { body["trailingStop"] = t.into(); }
        let _: Value = self.post("/v5/position/trading-stop", body).await?;
        Ok(())
    }

    /// Fija el apalancamiento (idempotente; ignora "leverage not modified").
    pub async fn set_leverage(&self, symbol: &str, lev: u32) -> Result<(), ExecError> {
        let r: Result<Value, ExecError> = self.post("/v5/position/set-leverage", json!({
            "category": "linear", "symbol": symbol,
            "buyLeverage": lev.to_string(), "sellLeverage": lev.to_string(),
        })).await;
        match r { Ok(_) => Ok(()), Err(ExecError::Api { code: 110043, .. }) => Ok(()), Err(e) => Err(e) }
    }

    /// Estado de una orden (abierta o reciente). status: New/PartiallyFilled/Filled/Cancelled/...
    pub async fn get_order(&self, symbol: &str, order_id: &str) -> Result<OrderInfo, ExecError> {
        let r: OrderListResult = self.get("/v5/order/realtime",
            vec![("category", "linear".into()), ("symbol", symbol.into()), ("orderId", order_id.into())]).await?;
        let d = r.list.into_iter().next().ok_or_else(|| ExecError::Parse("orden no encontrada".into()))?;
        Ok(OrderInfo {
            status: d.order_status,
            avg_price: d.avg_price.parse().unwrap_or(0.0),
            filled_qty: d.cum_exec_qty.parse().unwrap_or(0.0),
        })
    }

    /// P&L realizado del último trade cerrado (USDT) + precio de salida promedio.
    pub async fn last_closed_pnl(&self, symbol: &str) -> Result<(f64, f64), ExecError> {
        let r: ClosedPnlResult = self.get("/v5/position/closed-pnl",
            vec![("category", "linear".into()), ("symbol", symbol.into()), ("limit", "1".into())]).await?;
        match r.list.into_iter().next() {
            Some(c) => Ok((c.closed_pnl.parse().unwrap_or(0.0), c.avg_exit_price.parse().unwrap_or(0.0))),
            None => Ok((0.0, 0.0)),
        }
    }

    /// Posición actual (size 0 = flat).
    pub async fn position(&self, symbol: &str) -> Result<PosInfo, ExecError> {
        let r: PosListResult = self.get("/v5/position/list",
            vec![("category", "linear".into()), ("symbol", symbol.into())]).await?;
        match r.list.into_iter().next() {
            Some(p) => Ok(PosInfo {
                side: p.side,
                size: p.size.parse().unwrap_or(0.0),
                avg_price: p.avg_price.parse().unwrap_or(0.0),
            }),
            None => Ok(PosInfo::default()),
        }
    }
}
