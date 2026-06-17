use exchange::adapter::{Exchange, MarketKind, Venue};

/// Configures which exchange/market the monitor connects to.
/// Read from the `MONITOR_EXCHANGE` env var.
/// If it is not set, Railway service names containing `spot` default to
/// `bybit_spot`; all other services default to `binance_linear`.
///
/// | Value            | Exchange          | Market  |
/// |------------------|-------------------|---------|
/// | `binance_linear` | Binance USDT-M    | Futures |
/// | `binance_spot`   | Binance           | Spot    |
/// | `bybit_spot`     | Bybit             | Spot    |
/// | `bybit_linear`   | Bybit USDT-M      | Futures |
/// | `okx_linear`     | OKX USDT swap     | Futures |
/// | `okx_spot`       | OKX               | Spot    |
/// | `hyperliquid_linear` | Hyperliquid    | Futures |
/// | `hyperliquid_spot`   | Hyperliquid    | Spot    |
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExchangeTarget {
    BinanceLinear,
    BinanceSpot,
    BybitSpot,
    BybitLinear,
    OkxLinear,
    OkxSpot,
    HyperliquidLinear,
    HyperliquidSpot,
}

impl ExchangeTarget {
    pub fn from_env() -> Self {
        let raw = std::env::var("MONITOR_EXCHANGE")
            .ok()
            .filter(|v| !v.trim().is_empty())
            .or_else(|| {
                let service = std::env::var("RAILWAY_SERVICE_NAME")
                    .or_else(|_| std::env::var("RAILWAY_SERVICE"))
                    .unwrap_or_default()
                    .to_lowercase();
                if service.contains("spot") {
                    Some("bybit_spot".to_string())
                } else {
                    None
                }
            })
            .unwrap_or_else(|| "binance_linear".to_string());

        match raw.to_lowercase().replace('-', "_").as_str() {
            "binance_spot" | "binancespot" => Self::BinanceSpot,
            "bybit_spot" | "bybitspot" => Self::BybitSpot,
            "bybit_linear" | "bybitlinear" => Self::BybitLinear,
            "okx_linear" | "okxlinear" | "okex_linear" | "okexlinear" => Self::OkxLinear,
            "okx_spot" | "okxspot" | "okex_spot" | "okexspot" => Self::OkxSpot,
            "hyperliquid_linear" | "hyperliquidlinear" | "hl_linear" | "hllinear" => {
                Self::HyperliquidLinear
            }
            "hyperliquid_spot" | "hyperliquidspot" | "hl_spot" | "hlspot" => Self::HyperliquidSpot,
            _ => Self::BinanceLinear,
        }
    }

    pub fn exchange(self) -> Exchange {
        match self {
            Self::BinanceLinear => Exchange::BinanceLinear,
            Self::BinanceSpot => Exchange::BinanceSpot,
            Self::BybitSpot => Exchange::BybitSpot,
            Self::BybitLinear => Exchange::BybitLinear,
            Self::OkxLinear => Exchange::OkexLinear,
            Self::OkxSpot => Exchange::OkexSpot,
            Self::HyperliquidLinear => Exchange::HyperliquidLinear,
            Self::HyperliquidSpot => Exchange::HyperliquidSpot,
        }
    }

    pub fn venue(self) -> Venue {
        match self {
            Self::BinanceLinear | Self::BinanceSpot => Venue::Binance,
            Self::BybitSpot | Self::BybitLinear => Venue::Bybit,
            Self::OkxLinear | Self::OkxSpot => Venue::Okex,
            Self::HyperliquidLinear | Self::HyperliquidSpot => Venue::Hyperliquid,
        }
    }

    pub fn market_kind(self) -> MarketKind {
        match self {
            Self::BinanceLinear | Self::BybitLinear | Self::OkxLinear | Self::HyperliquidLinear => {
                MarketKind::LinearPerps
            }
            Self::BinanceSpot | Self::BybitSpot | Self::OkxSpot | Self::HyperliquidSpot => {
                MarketKind::Spot
            }
        }
    }

    /// Returns true for futures-style markets that have OI, funding rate,
    /// long/short ratios, liquidation streams, etc.
    pub fn is_futures(self) -> bool {
        matches!(
            self,
            Self::BinanceLinear | Self::BybitLinear | Self::OkxLinear | Self::HyperliquidLinear
        )
    }

    pub fn is_spot(self) -> bool {
        matches!(
            self,
            Self::BinanceSpot | Self::BybitSpot | Self::OkxSpot | Self::HyperliquidSpot
        )
    }

    // ── Klines ────────────────────────────────────────────────────────────────

    /// Builds a REST URL for historical klines.
    /// `interval`: Binance format ("1m", "5m", "1h", "4h", "1d") — converted automatically.
    pub fn klines_url(self, symbol: &str, interval: &str, limit: usize) -> Option<String> {
        match self {
            Self::BinanceLinear => Some(format!(
                "https://fapi.binance.com/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}"
            )),
            Self::BinanceSpot => Some(format!(
                "https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
            )),
            Self::BybitSpot => {
                let iv = to_bybit_interval(interval);
                Some(format!(
                    "https://api.bybit.com/v5/market/kline?category=spot&symbol={symbol}&interval={iv}&limit={limit}"
                ))
            }
            Self::BybitLinear => {
                let iv = to_bybit_interval(interval);
                Some(format!(
                    "https://api.bybit.com/v5/market/kline?category=linear&symbol={symbol}&interval={iv}&limit={limit}"
                ))
            }
            Self::OkxSpot | Self::OkxLinear => {
                let inst_id = self.okx_inst_id(symbol);
                let bar = to_okx_interval(interval);
                Some(format!(
                    "https://www.okx.com/api/v5/market/candles?instId={inst_id}&bar={bar}&limit={limit}"
                ))
            }
            Self::HyperliquidLinear | Self::HyperliquidSpot => None,
        }
    }

    /// Extracts kline rows from a REST JSON response, normalizing exchange differences.
    ///
    /// - Binance: top-level array, oldest-first
    /// - Bybit: wrapped in `result.list`, newest-first → reversed here
    ///
    /// Row index mapping (identical for both after normalization):
    /// - `[0]` open time ms  `[1]` open  `[2]` high  `[3]` low  `[4]` close  `[5]` volume
    pub fn extract_kline_rows(self, json: &serde_json::Value) -> Vec<Vec<serde_json::Value>> {
        let raw = match self {
            Self::BinanceLinear | Self::BinanceSpot => json.as_array(),
            Self::BybitSpot | Self::BybitLinear => json
                .get("result")
                .and_then(|r| r.get("list"))
                .and_then(|l| l.as_array()),
            Self::OkxSpot | Self::OkxLinear => json.get("data").and_then(|l| l.as_array()),
            Self::HyperliquidLinear | Self::HyperliquidSpot => None,
        };

        let Some(rows) = raw else { return vec![] };

        let mut result: Vec<Vec<serde_json::Value>> = rows
            .iter()
            .filter_map(|row| row.as_array().cloned())
            .collect();

        if matches!(
            self,
            Self::BybitSpot | Self::BybitLinear | Self::OkxSpot | Self::OkxLinear
        ) {
            result.reverse();
        }

        result
    }

    /// Minimum expected number of fields per kline row.
    pub fn kline_min_fields(self) -> usize {
        match self {
            Self::BinanceLinear | Self::BinanceSpot => 10, // needs index 9 (taker_buy)
            Self::BybitSpot
            | Self::BybitLinear
            | Self::OkxSpot
            | Self::OkxLinear
            | Self::HyperliquidLinear
            | Self::HyperliquidSpot => 6,
        }
    }

    /// Returns the open timestamp (ms) from a kline row.
    /// Handles both Binance (integer) and Bybit (string) formats.
    pub fn open_ms_from_row(row: &[serde_json::Value]) -> i64 {
        row.get(0)
            .and_then(|v| {
                v.as_i64()
                    .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
            })
            .unwrap_or(0)
    }

    /// Returns the taker_buy_vol from a kline row.
    /// Bybit SPOT klines don't expose taker_buy, so we assume 50/50 split.
    pub fn taker_buy_from_row(self, row: &[serde_json::Value]) -> f64 {
        match self {
            Self::BinanceLinear | Self::BinanceSpot => row
                .get(9)
                .and_then(|v| v.as_str())
                .and_then(|s| s.parse().ok())
                .unwrap_or(0.0),
            Self::BybitSpot
            | Self::BybitLinear
            | Self::OkxSpot
            | Self::OkxLinear
            | Self::HyperliquidLinear
            | Self::HyperliquidSpot => {
                let volume: f64 = row
                    .get(5)
                    .and_then(|v| v.as_str())
                    .and_then(|s| s.parse().ok())
                    .unwrap_or(0.0);
                volume / 2.0
            }
        }
    }

    // ── Spot price ───────────────────────────────────────────────────────────

    pub fn spot_price_url(self, symbol: &str) -> Option<String> {
        match self {
            Self::BinanceLinear | Self::BinanceSpot => Some(format!(
                "https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
            )),
            Self::BybitSpot => Some(format!(
                "https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}"
            )),
            Self::BybitLinear => Some(format!(
                "https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}"
            )),
            Self::OkxSpot | Self::OkxLinear => Some(format!(
                "https://www.okx.com/api/v5/market/ticker?instId={}",
                self.okx_inst_id(symbol)
            )),
            Self::HyperliquidLinear | Self::HyperliquidSpot => None,
        }
    }

    pub fn parse_spot_price(self, json: &serde_json::Value) -> Option<f64> {
        match self {
            Self::BinanceLinear | Self::BinanceSpot => json.get("price")?.as_str()?.parse().ok(),
            Self::BybitSpot | Self::BybitLinear => json
                .get("result")?
                .get("list")?
                .as_array()?
                .first()?
                .get("lastPrice")?
                .as_str()?
                .parse()
                .ok(),
            Self::OkxSpot | Self::OkxLinear => json
                .get("data")?
                .as_array()?
                .first()?
                .get("last")?
                .as_str()?
                .parse()
                .ok(),
            Self::HyperliquidLinear | Self::HyperliquidSpot => None,
        }
    }

    // ── Futures-only endpoints ───────────────────────────────────────────────
    // All return None/empty for spot targets.

    pub fn premium_index_url(self, symbol: &str) -> Option<String> {
        match self {
            Self::BinanceLinear => Some(format!(
                "https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}"
            )),
            _ => None,
        }
    }

    pub fn open_interest_url(self, symbol: &str) -> Option<String> {
        match self {
            Self::BinanceLinear => Some(format!(
                "https://fapi.binance.com/fapi/v1/openInterest?symbol={symbol}"
            )),
            _ => None,
        }
    }

    pub fn top_trader_ls_url(self, symbol: &str) -> Option<String> {
        match self {
            Self::BinanceLinear => Some(format!(
                "https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={symbol}&period=5m&limit=1"
            )),
            _ => None,
        }
    }

    pub fn global_ls_url(self, symbol: &str) -> Option<String> {
        match self {
            Self::BinanceLinear => Some(format!(
                "https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=5m&limit=1"
            )),
            _ => None,
        }
    }

    pub fn taker_ratio_url(self, symbol: &str) -> Option<String> {
        match self {
            Self::BinanceLinear => Some(format!(
                "https://fapi.binance.com/futures/data/takerlongshortRatio?symbol={symbol}&period=5m&limit=1"
            )),
            _ => None,
        }
    }

    pub fn funding_history_url(self, symbol: &str) -> Option<String> {
        match self {
            Self::BinanceLinear => Some(format!(
                "https://fapi.binance.com/fapi/v1/fundingRate?symbol={symbol}&limit=21"
            )),
            _ => None,
        }
    }

    pub fn oi_history_url(self, symbol: &str) -> Option<String> {
        match self {
            Self::BinanceLinear => Some(format!(
                "https://fapi.binance.com/futures/data/openInterestHist?symbol={symbol}&period=5m&limit=30"
            )),
            _ => None,
        }
    }

    /// Returns the Binance FAPI ls-history endpoint names; empty for non-Binance-futures.
    /// Each tuple: (full URL, endpoint_name_for_source_mapping)
    pub fn ls_history_endpoint_urls(self, symbol: &str) -> Vec<(String, &'static str)> {
        match self {
            Self::BinanceLinear => vec![
                (
                    format!(
                        "https://fapi.binance.com/futures/data/topLongShortPositionRatio?symbol={symbol}&period=5m&limit=6"
                    ),
                    "topLongShortPositionRatio",
                ),
                (
                    format!(
                        "https://fapi.binance.com/futures/data/globalLongShortAccountRatio?symbol={symbol}&period=5m&limit=6"
                    ),
                    "globalLongShortAccountRatio",
                ),
            ],
            _ => vec![],
        }
    }

    fn okx_inst_id(self, symbol: &str) -> String {
        let base_quote = symbol
            .strip_suffix("USDT")
            .map(|base| format!("{base}-USDT"))
            .or_else(|| {
                symbol
                    .strip_suffix("USDC")
                    .map(|base| format!("{base}-USDC"))
            })
            .unwrap_or_else(|| symbol.to_string());
        if matches!(self, Self::OkxLinear) {
            format!("{base_quote}-SWAP")
        } else {
            base_quote
        }
    }
}

fn to_bybit_interval(binance_iv: &str) -> &'static str {
    match binance_iv {
        "1m" => "1",
        "3m" => "3",
        "5m" => "5",
        "15m" => "15",
        "30m" => "30",
        "1h" => "60",
        "4h" => "240",
        "1d" => "D",
        "1w" => "W",
        _ => "1",
    }
}

fn to_okx_interval(binance_iv: &str) -> &'static str {
    match binance_iv {
        "1m" => "1m",
        "3m" => "3m",
        "5m" => "5m",
        "15m" => "15m",
        "30m" => "30m",
        "1h" => "1H",
        "4h" => "4H",
        "1d" => "1D",
        "1w" => "1W",
        _ => "1m",
    }
}
